"""The bridge client: Veadotube on one side, the proxy server on the other.

Outgoing: nodes listed in `listen_map` are subscribed on Veadotube; whatever
they report is translated through `send_map` and pushed to the proxy.
Incoming: anything the proxy relays from *another* client is applied to the
local Veadotube.

Updates are coalesced per target node and flushed at `send_rate` Hz, so a node
that changes faster than the network can carry only ever costs one message per
tick.
"""

import asyncio
import json
import threading

import websockets
from websockets.asyncio.client import connect

from .nodes import parse_node_list
from .service import RUNNING, AsyncService
from .veado import CONNECTED, VeadoLink


class ClientService(AsyncService):
    name = "client"

    def __init__(self, config, on_state=None, on_nodes=None):
        super().__init__(config, on_state=on_state)
        # Called with [(key, name), ...] whenever Veadotube reports its node
        # list - on connect and again each time the list changes.
        self.on_nodes = on_nodes
        self.veado = None
        self._proxy_ws = None
        self._pending = {}
        self._pending_lock = threading.Lock()
        self._veado_status = "disconnected"
        self._proxy_status = "disconnected"
        self.sent_to_proxy = 0
        self.received_from_proxy = 0

    # ------------------------------------------------------------------ setup
    def _prepare(self):
        cfg = self.config.get()
        if not cfg["listen_map"] and not cfg["send_map"]:
            self.log.warning(
                "No listen or send mappings are configured - the client will connect but "
                "will not forward anything. Add mappings on the Configuration tab."
            )
        with self._pending_lock:
            self._pending.clear()
        self.sent_to_proxy = 0
        self.received_from_proxy = 0

    def _cleanup(self):
        link, self.veado = self.veado, None
        if link is not None:
            link.stop()
        self._proxy_ws = None
        self._veado_status = "disconnected"
        self._proxy_status = "disconnected"

    # ------------------------------------------------------------------- body
    async def _run(self, stop_event):
        self.veado = VeadoLink(
            self.config, on_event=self._on_veado_event, on_state=self._on_veado_state
        )
        self.veado.start()
        self._publish_state()

        try:
            await asyncio.gather(
                self._proxy_loop(stop_event),
                self._sender_loop(stop_event),
                self._closer(stop_event),
            )
        finally:
            link, self.veado = self.veado, None
            if link is not None:
                link.stop()
            self.log.info("Bridge client stopped")

    async def _closer(self, stop_event):
        """Closes the live proxy socket so the reader stops waiting on it."""
        await stop_event.wait()
        websocket = self._proxy_ws
        if websocket is not None:
            try:
                await websocket.close()
            except Exception:
                pass

    # ------------------------------------------------------------ proxy side
    async def _proxy_loop(self, stop_event):
        announced_failure = False
        while not stop_event.is_set():
            cfg = self.config.get()
            url = cfg["proxy_url"]
            try:
                async with connect(url, open_timeout=10, ping_interval=20, ping_timeout=20) as websocket:
                    self._proxy_ws = websocket
                    self._proxy_status = "connected"
                    announced_failure = False
                    self.log.info("Connected to the proxy at %s", url)
                    self._publish_state()
                    async for message in websocket:
                        self._on_proxy_message(message)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if not announced_failure and not stop_event.is_set():
                    self.log.warning(
                        "Cannot reach the proxy at %s (%s). Is the proxy server running and "
                        "the port open? Retrying every %.0fs.",
                        url, _reason(exc), cfg["reconnect_delay"],
                    )
                    announced_failure = True
                else:
                    self.log.debug("Proxy connection problem: %s", _reason(exc))
            finally:
                self._proxy_ws = None
                if self._proxy_status == "connected" and not stop_event.is_set():
                    self.log.info("Proxy connection lost - reconnecting")
                self._proxy_status = "disconnected"
                self._publish_state()

            if stop_event.is_set():
                break
            await _sleep_or_stop(stop_event, cfg["reconnect_delay"])

    def _on_proxy_message(self, message):
        try:
            data = json.loads(message)
        except ValueError:
            self.log.debug("Ignoring a non-JSON message from the proxy: %s", str(message)[:200])
            return
        if not isinstance(data, dict):
            self.log.debug("Ignoring an unexpected message from the proxy: %s", str(message)[:200])
            return
        if data.get("source") == self.config.read("client_id"):
            return  # our own echo
        for field in ("type", "id", "value"):
            if field not in data:
                self.log.debug("Proxy message is missing %r, ignoring: %s", field, str(message)[:200])
                return

        self.received_from_proxy += 1
        self.log.debug(
            "Proxy -> Veadotube: %s:%s = %s", data["type"], data["id"], _clip(data["value"])
        )
        link = self.veado
        if link is None or not link.connected:
            self.log.debug("Veadotube is not connected - update dropped")
            return
        link.set_node(data["type"], data["id"], data["value"])

    # ----------------------------------------------------------- veado side
    def _on_veado_state(self, status, detail):
        self._veado_status = status
        if status == CONNECTED:
            self._subscribe_listen_nodes()
        self._publish_state()

    def _subscribe_listen_nodes(self):
        link = self.veado
        if link is None:
            return
        # Subscribe to the node list itself, so the Configuration tab always
        # offers what this Veadotube actually has rather than a remembered set.
        link.listen_nodes()
        nodes = self.config.read("listen_map", [])
        for node in nodes:
            link.listen_node(node["type"], node["id"])
        if nodes:
            self.log.info("Subscribed to %d Veadotube node(s)", len(nodes))

    def _on_veado_event(self, data):
        if not isinstance(data, dict):
            return

        entries = parse_node_list(data)
        if entries is not None:
            self._on_node_list(entries)
            return

        if data.get("event") != "payload":
            return
        node_type, node_id = data.get("type"), data.get("id")
        if node_type is None or node_id is None or "payload" not in data:
            return

        value = data["payload"]
        client_id = self.config.read("client_id")
        matched = 0
        for mapping in self.config.read("send_map", []):
            source = mapping["from"]
            if source["type"] != node_type or source["id"] != node_id:
                continue
            target = mapping["to"]
            message = {
                "source": client_id,
                "type": target["type"],
                "id": target["id"],
                "value": value,
            }
            with self._pending_lock:
                # Keyed by target node: only the newest value per node is sent.
                self._pending[(target["type"], target["id"])] = message
            matched += 1

        if matched:
            self.log.debug(
                "Veadotube -> proxy: %s:%s = %s (%d mapping(s))",
                node_type, node_id, _clip(value), matched,
            )

    def _on_node_list(self, entries):
        self.log.debug(
            "Veadotube listed %d node(s): %s",
            len(entries), ", ".join(key for key, _name in entries),
        )
        self._warn_about_missing_nodes(entries)
        if self.on_nodes:
            try:
                self.on_nodes(entries)
            except Exception:
                self.log.exception("Handling the Veadotube node list failed")

    def _warn_about_missing_nodes(self, entries):
        """A listen_map entry Veadotube does not have will never fire. Say so."""
        available = {key for key, _name in entries}
        if not available:
            return
        missing = [
            "%s:%s" % (node["type"], node["id"])
            for node in self.config.read("listen_map", [])
            if "%s:%s" % (node["type"], node["id"]) not in available
        ]
        if missing:
            self.log.warning(
                "Veadotube does not have these nodes, so they will never report: %s. "
                "Check the listen map on the Configuration tab.", ", ".join(missing),
            )

    # ---------------------------------------------------------------- sending
    async def _sender_loop(self, stop_event):
        while not stop_event.is_set():
            interval = 1.0 / max(1, self.config.read("send_rate", 60))
            websocket = self._proxy_ws
            if websocket is not None:
                with self._pending_lock:
                    batch = list(self._pending.values())
                    self._pending.clear()
                for message in batch:
                    try:
                        await websocket.send(json.dumps(message))
                        self.sent_to_proxy += 1
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        self.log.debug("Could not send to the proxy: %s", _reason(exc))
                        break
            await _sleep_or_stop(stop_event, interval)

    # ------------------------------------------------------------------ state
    def apply_config(self):
        """Re-subscribe after a config change that does not need a restart."""
        if self.running:
            self._subscribe_listen_nodes()

    def _publish_state(self):
        detail = "Veadotube: %s | Proxy: %s" % (self._veado_status, self._proxy_status)
        link = self.veado
        self.set_state(
            RUNNING,
            detail,
            veado=self._veado_status,
            proxy=self._proxy_status,
            sent=self.sent_to_proxy,
            received=self.received_from_proxy,
            dropped=link.dropped if link else 0,
        )


async def _sleep_or_stop(stop_event, delay):
    """Sleep, but wake up immediately if the service is asked to stop."""
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=delay)
    except asyncio.TimeoutError:
        pass


def _reason(exc):
    text = str(exc).strip()
    if not text:
        text = exc.__class__.__name__
    if isinstance(exc, websockets.exceptions.InvalidURI):
        return "%s - check proxy_url" % text
    return text


def _clip(value, limit=120):
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= limit else text[:limit] + "..."
