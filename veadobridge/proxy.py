"""The proxy server: a WebSocket relay between bridge clients.

Whatever one client sends is forwarded verbatim to every other client; the
clients themselves decide what to do with it.  The listening socket is bound
before the event loop starts, so a busy port is reported immediately and can be
diagnosed (and, if it is one of our own leftovers, reclaimed) before anything
else happens.
"""

import asyncio
import time

from websockets.asyncio.server import serve

from .netutil import PortInUse, bind_listen_socket
from .service import RUNNING, AsyncService


class ProxyService(AsyncService):
    name = "proxy"

    def __init__(self, config, on_state=None, on_port_conflict=None):
        super().__init__(config, on_state=on_state)
        # Called with a PortInUse; returns True if the caller freed the port and
        # we should try binding again.
        self.on_port_conflict = on_port_conflict
        self._socket = None
        self._host = None
        self._port = None
        self._connections = set()
        self.messages_relayed = 0

    @property
    def client_count(self):
        return len(self._connections)

    # ------------------------------------------------------------------ setup
    def _prepare(self):
        cfg = self.config.get()
        self._host = cfg["proxy_host"]
        self._port = cfg["proxy_port"]
        self._connections = set()
        self.messages_relayed = 0

        for attempt in range(3):
            try:
                self._socket = bind_listen_socket(self._host, self._port)
                return
            except PortInUse as exc:
                self.log.warning("%s", exc.describe())
                if self.on_port_conflict is None or not self.on_port_conflict(exc):
                    raise
                self.log.info("Retrying the bind on port %d (attempt %d)", self._port, attempt + 2)
                # Give Windows a moment to release the socket of the process we
                # just stopped.
                time.sleep(0.5)
        self._socket = bind_listen_socket(self._host, self._port)

    def _cleanup(self):
        sock, self._socket = self._socket, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        self._connections = set()

    # ------------------------------------------------------------------- body
    async def _run(self, stop_event):
        sock = self._socket
        if sock is None:
            raise RuntimeError("the listening socket was not prepared")
        # asyncio takes ownership of the socket from here on.
        self._socket = None

        self.log.info("Proxy listening on ws://%s:%d", self._host, self._port)
        try:
            async with serve(
                self._handler,
                sock=sock,
                ping_interval=20,
                ping_timeout=20,
                close_timeout=5,
            ) as server:
                self.set_state(RUNNING, "listening on %s:%d" % (self._host, self._port), clients=0)
                reporter = asyncio.ensure_future(self._report_loop())
                try:
                    await stop_event.wait()
                finally:
                    reporter.cancel()
                    self.log.info("Closing the proxy and disconnecting %d client(s)", len(self._connections))
                    server.close()
                    await asyncio.wait_for(server.wait_closed(), timeout=5)
        except asyncio.TimeoutError:
            self.log.warning("Some connections did not close in time; forcing the socket shut")
        finally:
            try:
                sock.close()
            except OSError:
                pass
            self.log.info("Proxy stopped, port %d released", self._port)

    async def _report_loop(self):
        """Keep the dashboard counters fresh without spamming the log."""
        last = None
        while True:
            snapshot = (len(self._connections), self.messages_relayed)
            if snapshot != last:
                last = snapshot
                self.set_state(
                    RUNNING,
                    "listening on %s:%d" % (self._host, self._port),
                    clients=snapshot[0],
                    relayed=snapshot[1],
                )
            await asyncio.sleep(0.5)

    async def _handler(self, connection):
        peer = _peer_name(connection)
        self._connections.add(connection)
        self.log.info("Client connected: %s (%d online)", peer, len(self._connections))
        try:
            async for message in connection:
                await self._relay(connection, message)
        except Exception as exc:
            self.log.debug("Connection %s ended: %s", peer, exc)
        finally:
            self._connections.discard(connection)
            self.log.info("Client disconnected: %s (%d online)", peer, len(self._connections))

    async def _relay(self, sender, message):
        # Snapshot first: the set can change while we are awaiting the sends.
        targets = [conn for conn in self._connections if conn is not sender]
        if not targets:
            self.log.debug("Nothing to relay to (no other clients): %s", _clip(message))
            return
        self.log.debug("Relaying to %d client(s): %s", len(targets), _clip(message))
        results = await asyncio.gather(
            *(conn.send(message) for conn in targets), return_exceptions=True
        )
        for conn, result in zip(targets, results):
            if isinstance(result, Exception):
                self.log.debug("Could not deliver to %s: %s", _peer_name(conn), result)
                self._connections.discard(conn)
        self.messages_relayed += 1


def _peer_name(connection):
    try:
        address = connection.remote_address
        if address:
            return "%s:%s" % (address[0], address[1])
    except Exception:
        pass
    return "unknown"


def _clip(message, limit=200):
    text = message if isinstance(message, str) else repr(message)
    return text if len(text) <= limit else text[:limit] + "..."
