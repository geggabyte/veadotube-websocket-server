# client.py
"""
WebSocket Bridge Client for Veadotube
====================================

This module implements a WebSocket bridge client that connects to both
the Veadotube system and the proxy server, forwarding messages between them.

The client handles:
- Connecting to Veadotube WebSocket server
- Listening to specific nodes in Veadotube
- Forwarding messages to the proxy server
- Receiving messages from the proxy server and applying them to Veadotube

Classes:
    Veado: Client for connecting to Veadotube WebSocket server
    BridgeClient: Main client that bridges Veadotube and proxy

Functions:
    match_map: Check if a mapping matches a given type and ID
    apply_map: Apply a mapping to transform data
    main: Entry point for the client application
"""

import asyncio
import json
import websockets
import websocket  # sync lib for veadotube
import threading
from config_manager import ConfigManager


# ---------------------------
# VEADOTUBE CLIENT (threaded)
# ---------------------------
class Veado:
    """Client for connecting to Veadotube WebSocket server.
    
    This class handles the connection to the Veadotube WebSocket server,
    listening for events, and sending messages.
    
    Attributes:
        config: ConfigManager instance for reading configuration
        ws: WebSocket connection to Veadotube
        callbacks: List of callback functions to be notified of events
    """
    
    def __init__(self):
        self.config = ConfigManager()
        self.ws = websocket.WebSocket()
        self.ws.connect(f"ws://{self.config.read('veado_host')}:{self.config.read('veado_port')}?n={self.config.read('client_id')}")
        self.callbacks = []

        threading.Thread(target=self._listen, daemon=True).start()

    def disconnect(self):
        """Close the WebSocket connection to Veadotube."""
        self.ws.close()

    def _listen(self):
        """Internal method to listen for messages from Veadotube."""
        while True:
            msg = self.ws.recv()
            if ":" not in msg:
                continue
            channel, data = msg.split(":", 1)
            data = json.loads(data.strip())

            for cb in self.callbacks:
                cb(data)

    def send(self, payload):
        """Send a payload to Veadotube.
        
        Args:
            payload: The data to send to Veadotube
        """
        msg = f"nodes: {json.dumps(payload)}"
        self.ws.send(msg)
        print(msg)

    def listen_node(self, t, i):
        """Subscribe to a specific node in Veadotube.
        
        Args:
            t: Type of the node
            i: ID of the node
        """
        self.send({
            "event": "payload",
            "type": t,
            "id": i,
            "payload": {
                "event": "listen",
                "token": "sync"
            }
        })


# ---------------------------
# MAPPING LOGIC
# ---------------------------
def match_map(mapping, t, i):
    """Check if a mapping matches a given type and ID.
    
    Args:
        mapping: The mapping configuration
        t: Type to match
        i: ID to match
        
    Returns:
        bool: True if the mapping matches, False otherwise
    """
    return mapping["from"]["type"] == t and mapping["from"]["id"] == i


def apply_map(mapping, value):
    """Apply a mapping to transform data.
    
    Args:
        mapping: The mapping configuration
        value: The value to transform
        
    Returns:
        dict: Transformed data
    """
    return {
        "type": mapping["to"]["type"],
        "id": mapping["to"]["id"],
        "value": value
    }


# ---------------------------
# MAIN CLIENT
# ---------------------------
class BridgeClient:
    """Main bridge client that connects to both Veadotube and the proxy server.
    
    This class manages the connection to both the Veadotube system and the
    proxy server, forwarding messages between them based on the configuration.
    
    Attributes:
        loop: Asyncio event loop
        veado: Veado instance for connecting to Veadotube
        config: ConfigManager instance for reading configuration
        pending_messages: Dictionary of messages waiting to be sent
        send_interval: Interval between sending messages (in seconds)
    """
    
    def __init__(self):
        self.loop = None
        self.veado = Veado()
        self.config = ConfigManager()

        self.pending_messages = {}
        self.send_interval = 1 / 60  # 60Hz

    async def start(self):
        """Start the bridge client.
        
        This method establishes connections to both Veadotube and the proxy server,
        and starts the message forwarding loops.
        """
        self.loop = asyncio.get_running_loop()
        self.proxy = await websockets.connect(self.config.read('proxy_url'))
        self.refresh_listeners()
        # subscribe to nodes
        for item in self.config.read('listen_map'):
            self.veado.listen_node(item["type"], item["id"])

        # hook veado events
        self.veado.callbacks.append(self.handle_local_event)

        await asyncio.gather(
            self.listen_proxy(),
            self.sender_loop()
        )

    # -----------------------
    # LOCAL → PROXY
    # -----------------------
    def handle_local_event(self, data):
        cfg = self.config.get()

        if data.get("event") != "payload":
            return

        t = data["type"]
        i = data["id"]
        val = data["payload"]

        for m in cfg["send_map"]:
            if m["from"]["type"] == t and m["from"]["id"] == i:
                msg = {
                    "source": cfg["client_id"],
                    "type": m["to"]["type"],
                    "id": m["to"]["id"],
                    "value": val
                }

                key = (msg["type"], msg["id"])
                self.pending_messages[key] = msg

    # -----------------------
    # PROXY → LOCAL
    # -----------------------
    async def listen_proxy(self):
        async for msg in self.proxy:
            data = json.loads(msg)
            if data["source"] == self.config.read('client_id'):
                continue
            self.apply_remote(data)

    def apply_remote(self, data):
        self.veado.send({
            "event": "payload",
            "type": data["type"],
            "id": data["id"],
            "payload": {"event": "set", "value": data["value"]}
        })

    def refresh_listeners(self):
        cfg = self.config.get()

        for item in cfg["listen_map"]:
            self.veado.listen_node(item["type"], item["id"])

    def on_config_reload(self, cfg):
        print("[CLIENT] Applying new config")

        self.listen_map = cfg.get("listen_map", [])
        self.send_map = cfg.get("send_map", [])

        self.refresh_listeners()
        self.active_listeners = set()

    # ---------------------------
    # SENDER COURUTINE
    # ---------------------------
    async def sender_loop(self):
        while True:
            try:
                if self.pending_messages:

                    messages = list(self.pending_messages.values())
                    self.pending_messages.clear()

                    for msg in messages:

                        if self.proxy:
                            await self.proxy.send(json.dumps(msg))

                await asyncio.sleep(self.send_interval)

            except Exception as e:
                print("[SENDER ERROR]", e)

                await asyncio.sleep(1)

# ---------------------------
# RUN
# ---------------------------
if __name__ == "__main__":
    #veado_test()
    client = BridgeClient()
    asyncio.run(client.start())