"""Translating node payloads to and from plain values.

Every node type reports and accepts a different payload shape, so the bridge
cannot pass payloads through untouched - what a node reports is not what
another node accepts, even when both are the same type:

    type          reports                              accepts as "set"
    ------------  -----------------------------------  --------------------------
    boolean       false                                {"event":"set","value":false}
    number        {"value":0.4,"min":-1,"max":1}       {"event":"set","value":0.4}
    stateEvents   {"event":"peek","state":"talking"}   {"event":"set","state":"talking"}

Note that stateEvents has no "value" field anywhere: a state is addressed by
its id.  That is why reading and writing go through here rather than being
assumed to be the same thing.

Every node therefore reduces to one plain value - a bool, a number, or a state
id - which is what travels over the bridge and what a mapping can rewrite.
"""

BOOLEAN = "boolean"
NUMBER = "number"
STATE_EVENTS = "stateEvents"

#: Payload events that carry something other than the node's current value.
_NOT_A_VALUE = ("list", "thumb")


def read_value(node_type, payload):
    """Return the plain value a reported payload carries, or None if it has none.

    None means "nothing to forward": an unset node, a state stack with nothing
    on it, or a message that answers some other question (a state list, a
    thumbnail).
    """
    if node_type == STATE_EVENTS:
        if not isinstance(payload, dict) or payload.get("event") in _NOT_A_VALUE:
            return None
        state = payload.get("state")
        # An empty state id means the stack is empty; there is no state to set
        # on the other side, so treat it as nothing rather than forwarding "".
        return state if isinstance(state, str) and state else None

    raw = payload.get("value") if isinstance(payload, dict) else payload

    if node_type == BOOLEAN:
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, (int, float)):
            return bool(raw)  # the docs allow 1/0 as well as true/false
        return None  # {} - no value has been set yet

    if node_type == NUMBER:
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            return None
        return raw

    # A node type this app does not model: hand the payload through untouched
    # rather than guessing at its shape.
    return payload


def read_range(node_type, payload):
    """Return {"min": .., "max": ..} when a number reported one, else None."""
    if node_type != NUMBER or not isinstance(payload, dict):
        return None
    low, high = payload.get("min"), payload.get("max")
    if isinstance(low, (int, float)) and isinstance(high, (int, float)):
        return {"min": low, "max": high}
    return None


def set_payload(node_type, value, value_range=None):
    """Build the payload that sets `value` on this node type, or None if it cannot.

    Returning None means the value does not fit the target - a state name sent
    to a number node, say - and the update should be dropped rather than
    guessed at.
    """
    if value is None:
        return None

    if node_type == STATE_EVENTS:
        # A state is addressed by id. Numbers and booleans have no meaning here
        # unless a mapping has already turned them into a state id.
        if not isinstance(value, str) or not value:
            return None
        return {"event": "set", "state": value}

    if node_type == BOOLEAN:
        if isinstance(value, str):
            return None
        return {"event": "set", "value": bool(value)}

    if node_type == NUMBER:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        if value_range:
            # Veadotube discards the target's range when a bare number is set,
            # so pass the source's range along to keep it.
            return {"event": "set", "value": dict(value_range, value=value)}
        return {"event": "set", "value": value}

    return {"event": "set", "value": value}
