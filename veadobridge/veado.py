"""Connection to a local Veadotube instance.

Veadotube speaks a line-ish protocol over WebSocket: every message is
`<channel>: <json>`.  The synchronous `websocket-client` library is used here
(rather than the async one used for the proxy) because that is what Veadotube
has been verified to accept.

The link owns two threads while connected: a reader that blocks in `recv()` and
a writer that drains an outbound queue.  Sends are queued rather than written
inline so that no caller - least of all an asyncio loop - ever blocks on a
socket write.
"""

import json
import queue
import threading
import time

import websocket

from .logbus import get_logger

log = get_logger("veado")

DISCONNECTED = "disconnected"
CONNECTING = "connecting"
CONNECTED = "connected"

_SEND_QUEUE_MAX = 1000


class VeadoLink:
    """Keeps a Veadotube connection alive and pumps messages both ways."""

    def __init__(self, config, on_event=None, on_state=None):
        self.config = config
        self.on_event = on_event  # called with the decoded payload dict
        self.on_state = on_state  # called with (status, detail)
        self._stop = threading.Event()
        self._reader = None
        self._writer = None
        self._ws = None
        self._ws_lock = threading.Lock()
        self._outbox = queue.Queue(maxsize=_SEND_QUEUE_MAX)
        self._connected = threading.Event()
        self.status = DISCONNECTED
        self.messages_in = 0
        self.messages_out = 0
        self.dropped = 0

    # ---------------------------------------------------------------- control
    @property
    def connected(self):
        return self._connected.is_set()

    def start(self):
        if self._reader and self._reader.is_alive():
            return
        self._stop.clear()
        self._reader = threading.Thread(target=self._reader_main, name="veado-reader", daemon=True)
        self._reader.start()

    def stop(self, timeout=5.0):
        self._stop.set()
        self._connected.clear()
        self._drop_socket()
        # Unblock the writer.
        try:
            self._outbox.put_nowait(None)
        except queue.Full:
            pass
        for thread in (self._reader, self._writer):
            if thread and thread.is_alive():
                thread.join(timeout=timeout)
        self._reader = None
        self._writer = None
        self._set_status(DISCONNECTED, "stopped")

    # ------------------------------------------------------------------- send
    def send(self, payload):
        """Queue a payload for Veadotube. Never blocks, never raises."""
        if self._stop.is_set():
            return False
        try:
            self._outbox.put_nowait(payload)
            return True
        except queue.Full:
            self.dropped += 1
            if self.dropped % 100 == 1:
                log.warning(
                    "Veadotube is not keeping up - %d message(s) dropped so far", self.dropped
                )
            return False

    def list_nodes(self):
        """Ask the nodes channel for the current node list, once."""
        return self.send({"event": "list"})

    def listen_nodes(self, token="sync-nodes"):
        """Ask for the node list now and again whenever it changes.

        This is the nodes channel itself, not a single node: the event sits at
        the top level rather than inside a `payload`.
        """
        return self.send({"event": "listen", "token": token})

    def listen_node(self, node_type, node_id):
        """Subscribe to a node so Veadotube pushes its changes to us."""
        return self.send(
            {
                "event": "payload",
                "type": node_type,
                "id": node_id,
                "payload": {"event": "listen", "token": "sync"},
            }
        )

    def set_node(self, node_type, node_id, value):
        return self.send(
            {
                "event": "payload",
                "type": node_type,
                "id": node_id,
                "payload": {"event": "set", "value": value},
            }
        )

    # ---------------------------------------------------------------- threads
    def _reader_main(self):
        backoff_notice_shown = False
        while not self._stop.is_set():
            cfg = self.config.get()
            url = "ws://%s:%s?n=%s" % (cfg["veado_host"], cfg["veado_port"], cfg["client_id"])
            self._set_status(CONNECTING, "connecting to %s:%s" % (cfg["veado_host"], cfg["veado_port"]))

            try:
                sock = websocket.WebSocket()
                sock.connect(url, timeout=5)
                sock.settimeout(None)
            except Exception as exc:
                if not backoff_notice_shown:
                    log.warning(
                        "Cannot reach Veadotube at %s:%s (%s). Is Veadotube running with its "
                        "WebSocket server enabled? Retrying every %.0fs.",
                        cfg["veado_host"], cfg["veado_port"], exc, cfg["reconnect_delay"],
                    )
                    backoff_notice_shown = True
                else:
                    log.debug("Veadotube still unreachable: %s", exc)
                self._set_status(DISCONNECTED, "not reachable")
                if self._stop.wait(cfg["reconnect_delay"]):
                    break
                continue

            backoff_notice_shown = False
            with self._ws_lock:
                self._ws = sock
            self._connected.set()
            self._set_status(CONNECTED, "%s:%s" % (cfg["veado_host"], cfg["veado_port"]))
            log.info("Connected to Veadotube at %s:%s", cfg["veado_host"], cfg["veado_port"])

            self._start_writer()
            self._read_until_closed(sock)

            self._connected.clear()
            self._drop_socket()
            if self._stop.is_set():
                break
            self._set_status(DISCONNECTED, "reconnecting")
            log.info("Veadotube connection lost - reconnecting in %.0fs", cfg["reconnect_delay"])
            if self._stop.wait(cfg["reconnect_delay"]):
                break

        self._set_status(DISCONNECTED, "stopped")

    def _read_until_closed(self, sock):
        while not self._stop.is_set():
            try:
                raw = sock.recv()
            except (websocket.WebSocketConnectionClosedException, OSError) as exc:
                if not self._stop.is_set():
                    log.debug("Veadotube read ended: %s", exc)
                return
            except Exception as exc:
                if not self._stop.is_set():
                    log.warning("Error reading from Veadotube: %s", exc)
                return

            if raw is None or raw == "":
                return
            self.messages_in += 1
            self._dispatch(raw)

    def _dispatch(self, raw):
        if isinstance(raw, bytes):
            try:
                raw = raw.decode("utf-8")
            except UnicodeDecodeError:
                log.debug("Ignoring a binary message from Veadotube (%d bytes)", len(raw))
                return
        if ":" not in raw:
            log.debug("Ignoring an unrecognised Veadotube message: %s", raw[:200])
            return
        channel, body = raw.split(":", 1)
        try:
            data = json.loads(body.strip())
        except ValueError as exc:
            log.debug("Veadotube sent something that is not JSON (%s): %s", exc, body[:200])
            return
        log.debug("Veadotube -> %s: %s", channel.strip(), body.strip()[:300])
        if self.on_event:
            try:
                self.on_event(data)
            except Exception:
                log.exception("Handler for a Veadotube message failed")

    def _start_writer(self):
        if self._writer and self._writer.is_alive():
            return
        self._writer = threading.Thread(target=self._writer_main, name="veado-writer", daemon=True)
        self._writer.start()

    def _writer_main(self):
        while not self._stop.is_set():
            try:
                payload = self._outbox.get(timeout=0.25)
            except queue.Empty:
                if not self.connected:
                    return
                continue
            if payload is None:
                return
            if not self.connected:
                # Dropped rather than buffered: stale avatar state is worse than
                # no state, and the next config apply re-subscribes anyway.
                continue
            message = "nodes: " + json.dumps(payload)
            with self._ws_lock:
                sock = self._ws
            if sock is None:
                continue
            try:
                sock.send(message)
                self.messages_out += 1
                log.debug("Veadotube <- %s", message[:300])
            except Exception as exc:
                if not self._stop.is_set():
                    log.debug("Could not send to Veadotube: %s", exc)
                self._connected.clear()
                return

    # --------------------------------------------------------------- internals
    def _drop_socket(self):
        with self._ws_lock:
            sock, self._ws = self._ws, None
        if sock is None:
            return
        # abort() shuts the underlying socket down so a blocked recv() returns
        # right away; close() alone would wait for a close handshake that a
        # gone-away Veadotube will never send.
        for method in ("abort", "close"):
            try:
                getattr(sock, method)()
            except Exception:
                pass

    def _set_status(self, status, detail=""):
        self.status = status
        if self.on_state:
            try:
                self.on_state(status, detail)
            except Exception:
                log.exception("Veadotube state callback failed")


def discover_instances():
    """Look for Veadotube instance files and return [(name, host, port), ...].

    Veadotube writes a small JSON file per running instance describing the
    address of its WebSocket server.  Scanning those saves the user from
    hunting for a port that changes every launch.  Best effort: an empty list
    just means the user types the port by hand.
    """
    import os

    candidates = [
        os.path.join(os.path.expanduser("~"), ".veadotube", "instances"),
        os.path.join(os.environ.get("APPDATA", ""), "veadotube", "instances"),
        os.path.join(os.path.expanduser("~"), ".config", "veadotube", "instances"),
    ]
    found = []
    for directory in candidates:
        if not directory or not os.path.isdir(directory):
            continue
        try:
            entries = os.listdir(directory)
        except OSError:
            continue
        for entry in entries:
            full = os.path.join(directory, entry)
            try:
                if time.time() - os.path.getmtime(full) > 24 * 3600:
                    continue  # almost certainly a leftover from an old session
                with open(full, "r", encoding="utf-8") as handle:
                    data = json.loads(handle.read() or "{}")
            except (OSError, ValueError):
                continue
            server = str(data.get("server", ""))
            if ":" not in server:
                continue
            host, _, port = server.rpartition(":")
            if not port.isdigit():
                continue
            found.append((str(data.get("name", entry)), host or "127.0.0.1", int(port)))
    return found
