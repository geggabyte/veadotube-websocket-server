#!/usr/bin/env python3
"""Headless self-test: no GUI, no real Veadotube, no fixed ports.

Runs the same code the app runs - config validation, the instance lock, the
port helpers, the proxy service and two bridge clients - against a stand-in
Veadotube, and checks that a node change on one side comes out the other side.

    python smoketest.py

Exit code 0 means everything passed.
"""

import asyncio
import json
import os
import socket
import sys
import tempfile
import threading
import time

from websockets.asyncio.server import serve

from veadobridge.client import ClientService
from veadobridge.config import ConfigManager, validate
from veadobridge.logbus import setup_logging
from veadobridge.netutil import PortInUse, bind_listen_socket, find_listener_pid, port_is_free
from veadobridge.nodes import parse_node_list
from veadobridge.proxy import ProxyService
from veadobridge.singleton import AlreadyRunning, InstanceLock

failures = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print("[%s] %s%s" % (status, name, (" - " + detail) if detail and not condition else ""))
    if not condition:
        failures.append(name)


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# --------------------------------------------------------------------- config
def test_config(tmp):
    path = os.path.join(tmp, "cfg.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(
            {
                "client_id": "a",
                "proxy_port": "not-a-port",
                "veado_port": 999999,
                "proxy_url": "localhost:8765",
                "listen_map": ["boolean:X", {"type": "number", "id": "N"}, "garbage"],
                "send_map": [{"from": "boolean:X", "to": "boolean:Y"}, {"from": "bad"}],
            },
            handle,
        )
    manager = ConfigManager(path, watch=False)
    cfg = manager.get()
    check("config: bad port falls back to the default", cfg["proxy_port"] == 8765)
    check("config: out-of-range port falls back", cfg["veado_port"] == 2424)
    check("config: missing scheme is repaired", cfg["proxy_url"] == "ws://localhost:8765")
    check("config: listen_map accepts strings and dicts", len(cfg["listen_map"]) == 2, str(cfg["listen_map"]))
    check("config: broken send_map entries are dropped", len(cfg["send_map"]) == 1)

    manager.save(dict(cfg, client_id="renamed"))
    with open(path, "r", encoding="utf-8") as handle:
        check("config: save round-trips", json.load(handle)["client_id"] == "renamed")

    _, problems = validate({"proxy_port": -1})
    check("config: problems are reported", any("proxy_port" in p for p in problems))


# -------------------------------------------------------------------- netutil
def test_netutil():
    port = free_port()
    sock = bind_listen_socket("127.0.0.1", port)
    try:
        check("net: a bound port is not free", not port_is_free("127.0.0.1", port))
        check("net: the listening PID is found", find_listener_pid(port) == os.getpid(),
              "got %s, expected %s" % (find_listener_pid(port), os.getpid()))
        try:
            bind_listen_socket("127.0.0.1", port)
            check("net: rebinding raises PortInUse", False)
        except PortInUse as exc:
            check("net: rebinding raises PortInUse", True)
            check("net: the conflict names the owner", exc.pid == os.getpid(), str(exc))
    finally:
        sock.close()
    check("net: the port is free once released", port_is_free("127.0.0.1", port))


# ------------------------------------------------------------------ singleton
def test_singleton(tmp):
    path = os.path.join(tmp, "lock-target.json")
    first = InstanceLock(path)
    first.acquire()
    second = InstanceLock(path)
    try:
        second.acquire()
        check("lock: a second instance is refused", False)
    except AlreadyRunning:
        check("lock: a second instance is refused", True)
    first.release()
    third = InstanceLock(path)
    try:
        third.acquire()
        check("lock: the lock is reusable once released", True)
        third.release()
    except AlreadyRunning:
        check("lock: the lock is reusable once released", False)

    other = InstanceLock(os.path.join(tmp, "different.json"))
    keeper = InstanceLock(path)
    keeper.acquire()
    try:
        other.acquire()
        check("lock: a different config may run alongside", True)
        other.release()
    except AlreadyRunning:
        check("lock: a different config may run alongside", False)
    keeper.release()


# ---------------------------------------------------------------------- nodes
def test_nodes():
    """The list reply shape, exactly as the nodes channel documents it."""
    entries = parse_node_list({
        "event": "list",
        "entries": [
            {"type": "boolean", "id": "mini", "name": "push-to-talk"},
            {"type": "stateEvents", "id": "mini", "name": "avatar state"},
        ],
    })
    check("nodes: a documented list reply parses",
          entries == [("boolean:mini", "push-to-talk"), ("stateEvents:mini", "avatar state")],
          str(entries))
    check("nodes: ids are unique per type, not on their own",
          entries is not None and len({key for key, _name in entries}) == 2, str(entries))
    check("nodes: a payload event is not a node list",
          parse_node_list({"event": "payload", "type": "boolean", "id": "mini"}) is None)
    check("nodes: an entry with no id is unaddressable and dropped",
          parse_node_list({"event": "list", "entries": [{"type": "boolean", "name": "x"}]}) == [],
          "a display name is not an id")
    check("nodes: an empty list differs from 'not a list'",
          parse_node_list({"event": "list", "entries": []}) == [])


# ------------------------------------------------------------- fake veadotube
class FakeVeadotube:
    """Just enough of Veadotube to exercise the client: `nodes: {json}` framing."""

    #: What this instance answers a nodes-channel `list` event with.
    NODES = [
        {"type": "boolean", "id": "mini", "name": "push-to-talk"},
        {"type": "stateEvents", "id": "mini", "name": "avatar state"},
        {"type": "boolean", "id": "SourceNode", "name": "the source"},
    ]

    def __init__(self):
        self.port = free_port()
        self.received = []
        self.subscriptions = []
        self.list_requests = []
        self._connections = set()
        self._loop = None
        self._ready = threading.Event()
        self._stop = None
        self._thread = threading.Thread(target=self._main, daemon=True)

    def start(self):
        self._thread.start()
        self._ready.wait(5)

    def stop(self):
        if self._loop and self._stop:
            self._loop.call_soon_threadsafe(self._stop.set)
        self._thread.join(5)

    def push(self, node_type, node_id, value):
        """Pretend a node changed, the way a listened-to node reports."""
        message = "nodes: " + json.dumps(
            {"event": "payload", "type": node_type, "id": node_id, "payload": value}
        )
        for connection in list(self._connections):
            asyncio.run_coroutine_threadsafe(connection.send(message), self._loop)

    def _main(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._stop = asyncio.Event()
        loop.run_until_complete(self._serve())
        loop.close()

    async def _serve(self):
        async with serve(self._handler, "127.0.0.1", self.port):
            self._ready.set()
            await self._stop.wait()

    async def _handler(self, connection):
        self._connections.add(connection)
        try:
            async for raw in connection:
                if ":" not in raw:
                    continue
                body = json.loads(raw.split(":", 1)[1])
                event = body.get("event")
                payload = body.get("payload", {})
                if event in ("list", "listen", "unlisten") and "payload" not in body:
                    # The nodes channel itself, not one node: no payload object.
                    self.list_requests.append(event)
                    if event != "unlisten":
                        await connection.send(
                            "nodes: " + json.dumps({"event": "list", "entries": self.NODES})
                        )
                elif isinstance(payload, dict) and payload.get("event") == "listen":
                    self.subscriptions.append((body.get("type"), body.get("id")))
                else:
                    self.received.append(body)
        except Exception:
            pass
        finally:
            self._connections.discard(connection)


def write_config(tmp, name, **values):
    path = os.path.join(tmp, name)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(values, handle)
    return ConfigManager(path, watch=False)


def wait_for(predicate, timeout=10.0, interval=0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


# ------------------------------------------------------------------ end to end
def test_bridge(tmp):
    proxy_port = free_port()
    veado_a, veado_b = FakeVeadotube(), FakeVeadotube()
    veado_a.start()
    veado_b.start()

    proxy_cfg = write_config(tmp, "proxy.json", proxy_host="127.0.0.1", proxy_port=proxy_port)
    proxy = ProxyService(proxy_cfg)
    check("proxy: starts", proxy.start())
    check("proxy: refuses to double-bind", not port_is_free("127.0.0.1", proxy_port))

    url = "ws://127.0.0.1:%d" % proxy_port
    sender_cfg = write_config(
        tmp, "sender.json",
        client_id="sender", proxy_url=url, veado_host="127.0.0.1", veado_port=veado_a.port,
        listen_map=[{"type": "boolean", "id": "SourceNode"}],
        send_map=[{"from": {"type": "boolean", "id": "SourceNode"},
                   "to": {"type": "boolean", "id": "TargetNode"}}],
    )
    receiver_cfg = write_config(
        tmp, "receiver.json",
        client_id="receiver", proxy_url=url, veado_host="127.0.0.1", veado_port=veado_b.port,
        listen_map=[], send_map=[],
    )
    listed = []
    sender = ClientService(sender_cfg, on_nodes=listed.append)
    receiver = ClientService(receiver_cfg)
    check("client: sender starts", sender.start())
    check("client: receiver starts", receiver.start())

    try:
        check("client: subscribes to its listen_map",
              wait_for(lambda: ("boolean", "SourceNode") in veado_a.subscriptions),
              str(veado_a.subscriptions))
        check("client: subscribes to the node list itself",
              wait_for(lambda: "listen" in veado_a.list_requests), str(veado_a.list_requests))
        check("client: the reported node list reaches the app",
              wait_for(lambda: listed and ("stateEvents:mini", "avatar state") in listed[-1]),
              str(listed))
        check("client: a node list is not mistaken for a node change",
              not any(m.get("event") == "list" for m in veado_a.received), str(veado_a.received))
        check("client: both clients reach the proxy",
              wait_for(lambda: proxy.client_count == 2), "connected: %d" % proxy.client_count)

        veado_a.push("boolean", "SourceNode", True)
        check("bridge: the value arrives on the far side",
              wait_for(lambda: any(m.get("id") == "TargetNode" for m in veado_b.received)),
              str(veado_b.received))

        if veado_b.received:
            applied = [m for m in veado_b.received if m.get("id") == "TargetNode"][-1]
            check("bridge: it is mapped onto the target node", applied["type"] == "boolean")
            check("bridge: it is sent as a set event",
                  applied["payload"]["event"] == "set" and applied["payload"]["value"] is True,
                  json.dumps(applied))
        check("bridge: the sender does not echo to itself", not veado_a.received, str(veado_a.received))

        # Coalescing: a burst of updates must not become a burst of messages.
        before = sender.sent_to_proxy
        for index in range(50):
            veado_a.push("boolean", "SourceNode", index % 2 == 0)
        time.sleep(0.5)
        check("bridge: rapid updates are coalesced", sender.sent_to_proxy - before < 40,
              "sent %d for 50 updates" % (sender.sent_to_proxy - before))
    finally:
        check("client: sender stops cleanly", sender.stop())
        check("client: receiver stops cleanly", receiver.stop())
        check("proxy: stops cleanly", proxy.stop())
        veado_a.stop()
        veado_b.stop()

    check("proxy: the port is free again after stopping",
          wait_for(lambda: port_is_free("127.0.0.1", proxy_port), timeout=5))
    check("proxy: it can be started again on the same port", proxy.start())
    proxy.stop()
    check("proxy: and released again", wait_for(lambda: port_is_free("127.0.0.1", proxy_port), timeout=5))


def main():
    setup_logging(debug="-v" in sys.argv)
    if "-v" not in sys.argv:
        import logging

        logging.getLogger().setLevel(logging.WARNING)

    with tempfile.TemporaryDirectory(prefix="veadobridge-selftest-") as tmp:
        test_config(tmp)
        test_nodes()
        test_netutil()
        test_singleton(tmp)
        test_bridge(tmp)

    print()
    if failures:
        print("%d check(s) failed: %s" % (len(failures), ", ".join(failures)))
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
