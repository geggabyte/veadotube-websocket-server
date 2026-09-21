"""Reading the node list from a Veadotube instance.

Veadotube answers a `list` event on the nodes channel with an `entries` array:

    nodes: {"event": "list", "entries": [
        {"type": "boolean",     "id": "mini", "name": "push-to-talk"},
        {"type": "stateEvents", "id": "mini", "name": "avatar state"}
    ]}

Two things about that shape matter:

* `id` and `name` are different fields.  The id is what a mapping addresses;
  the name is only what the user sees in Veadotube.  Guessing one from the
  other produces a node reference that does not exist.
* ids are unique only *per type* - veadotube mini exposes both `boolean:mini`
  and `stateEvents:mini` - so a node is keyed by "type:id" and carries its
  display name alongside rather than in the key.

`fetch_nodes` opens its own short-lived connection, for the case where the
bridge client is not running.  When it is, the live link subscribes with a
`listen` event instead and Veadotube pushes the list whenever it changes.
"""

import json

import websocket

from .logbus import get_logger

log = get_logger("nodes")

LIST_REQUEST = {"event": "list"}


def node_key(node_type, node_id):
    return "%s:%s" % (node_type, node_id)


def parse_node_list(data):
    """Return [(key, name), ...] from a decoded nodes-channel message.

    Returns None - not an empty list - when the message is not a node list at
    all, so callers can tell "Veadotube has no nodes" from "this was some other
    message".
    """
    if not isinstance(data, dict) or data.get("event") != "list":
        return None
    # `entries` is what the API documents; `nodes` is tolerated in case a build
    # uses the channel name for the array.
    entries = data.get("entries")
    if not isinstance(entries, list):
        entries = data.get("nodes")
    if not isinstance(entries, list):
        return None

    found = []
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        node_type = str(entry.get("type", "")).strip()
        node_id = str(entry.get("id", "")).strip()
        if not node_type or not node_id:
            continue  # unaddressable without both halves
        key = node_key(node_type, node_id)
        if key in seen:
            continue
        seen.add(key)
        found.append((key, str(entry.get("name", "")).strip()))
    return found


def fetch_nodes(host, port, client_id="veadobridge-config", timeout=4.0):
    """Return [(key, name), ...] from a one-off connection. Raises if unreachable."""
    url = "ws://%s:%s?n=%s" % (host, port, client_id)
    sock = websocket.WebSocket()
    sock.connect(url, timeout=timeout)
    found = []
    try:
        sock.settimeout(timeout)
        sock.send("nodes: " + json.dumps(LIST_REQUEST))
        # Veadotube sends its instance banner first, so read a few messages and
        # keep the one that is a node list.
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
            entries = _parse_message(raw)
            if entries is not None:
                found = entries
                break
    finally:
        try:
            sock.close()
        except Exception:
            pass

    log.info("Veadotube reported %d node(s)", len(found))
    return found


def _parse_message(raw):
    """Decode one `<channel>: <json>` line and parse it as a node list."""
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if ":" not in raw:
        return None
    _, body = raw.split(":", 1)
    try:
        data = json.loads(body.strip())
    except ValueError:
        return None
    log.debug("Veadotube said: %s", body.strip()[:500])
    return parse_node_list(data)
