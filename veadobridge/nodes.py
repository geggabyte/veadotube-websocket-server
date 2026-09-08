"""Ask a running Veadotube instance which nodes it exposes.

Used by the Configuration tab to fill the drop-downs, so mappings can be picked
from a list instead of typed from memory.  This opens its own short-lived
connection: the bridge client may not be running, and if it is, we do not want
to disturb its subscriptions.
"""

import json

import websocket

from .logbus import get_logger

log = get_logger("nodes")


def fetch_nodes(host, port, client_id="veadobridge-config", timeout=4.0):
    """Return a sorted list of "type:id" strings. Raises on connection failure."""
    url = "ws://%s:%s?n=%s" % (host, port, client_id)
    sock = websocket.WebSocket()
    sock.connect(url, timeout=timeout)
    found = []
    try:
        sock.settimeout(timeout)
        sock.send("nodes: " + json.dumps({"event": "list"}))
        # Veadotube may send unrelated traffic first; read a few messages and
        # keep whatever looks like a node list.
        for _ in range(10):
            try:
                raw = sock.recv()
            except websocket.WebSocketTimeoutException:
                break
            except Exception as exc:
                log.debug("Stopped reading the node list: %s", exc)
                break
            if not raw:
                break
            entries = _parse_node_list(raw)
            if entries:
                found.extend(entries)
                break
    finally:
        try:
            sock.close()
        except Exception:
            pass

    unique = sorted(set(found))
    log.info("Veadotube reported %d node(s)", len(unique))
    return unique


def _parse_node_list(raw):
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            return []
    if ":" not in raw:
        return []
    _, body = raw.split(":", 1)
    try:
        data = json.loads(body.strip())
    except ValueError:
        return []
    log.debug("Node list response: %s", body.strip()[:500])

    nodes = data.get("nodes") if isinstance(data, dict) else None
    if not isinstance(nodes, list):
        return []

    entries = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_type = str(node.get("type", "")).strip()
        node_id = str(node.get("id", node.get("name", ""))).strip()
        if node_type and node_id:
            entries.append("%s:%s" % (node_type, node_id))
    return entries
