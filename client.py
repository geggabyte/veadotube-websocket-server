# client.py
"""
WebSocket Bridge Client for Veadotube
"""

import asyncio
import json
import websockets
import websocket  # sync lib for veadotube
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext
import time
from config_manager import ConfigManager


class Veado:
    """Client for connecting to Veadotube WebSocket server."""
    """
    
    def __init__(self, log_callback=None):
        self.config = ConfigManager()
        self.ws = websocket.WebSocket()
        self.log_callback = log_callback
        try:
            self.ws.connect(f"ws://{self.config.read('veado_host')}:{self.config.read('veado_port')}?n={self.config.read('client_id')}")
            if self.log_callback:
                self.log_callback(f"Connected to Veadotube at {self.config.read('veado_host')}:{self.config.read('veado_port')}")
        except Exception as e:
            if self.log_callback:
                self.log_callback(f"Failed to connect to Veadotube: {e}")
        self.callbacks = []

        threading.Thread(target=self._listen, daemon=True).start()

    def disconnect(self):
        """Close the WebSocket connection to Veadotube."""
        self.ws.close()

    def _listen(self):
        """Internal method to listen for messages from Veadotube."""
        while True:
            try:
                msg = self.ws.recv()
                if ":" not in msg:
                    continue
                channel, data = msg.split(":", 1)
                data = json.loads(data.strip())

                for cb in self.callbacks:
                    cb(data)
            except Exception as e:
                if self.log_callback:
                    self.log_callback(f"Error in Veadotube listener: {e}")
                break

    def send(self, payload):
        """Send a payload to Veadotube.
        
        Args:
            payload: The data to send to Veadotube
        """
        try:
            msg = f"nodes: {json.dumps(payload)}"
            self.ws.send(msg)
            if self.log_callback:
                self.log_callback(msg)
        except Exception as e:
            if self.log_callback:
                self.log_callback(f"Error sending to Veadotube: {e}")

    def listen_node(self, t, i):
        """Subscribe to a specific node in Veadotube.
        
        Args:
            t: Type of the node
            i: ID of the node
        """
        try:
            self.send({
                "event": "payload",
                "type": t,
                "id": i,
                "payload": {
                    "event": "listen",
                    "token": "sync"
                }
            })
        except Exception as e:
            if self.log_callback:
                self.log_callback(f"Error listening to node {t}:{i}: {e}")


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
    
    def __init__(self, log_callback=None):
        self.loop = None
        self.log_callback = log_callback
        self.veado = Veado(log_callback)
        self.config = ConfigManager()

        self.pending_messages = {}
        self.send_interval = 1 / 60  # 60Hz

    async def start(self):
        """Start the bridge client.
        
        This method establishes connections to both Veadotube and the proxy server,
        and starts the message forwarding loops.
        """
        try:
            self.loop = asyncio.get_running_loop()
            self.proxy = await websockets.connect(self.config.read('proxy_url'))
            if self.log_callback:
                self.log_callback(f"Connected to proxy at {self.config.read('proxy_url')}")
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
        except Exception as e:
            if self.log_callback:
                self.log_callback(f"Error starting client: {e}")
            raise

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
        try:
            async for msg in self.proxy:
                data = json.loads(msg)
                if data["source"] == self.config.read('client_id'):
                    continue
                self.apply_remote(data)
        except Exception as e:
            if self.log_callback:
                self.log_callback(f"Error in proxy listener: {e}")

    def apply_remote(self, data):
        try:
            self.veado.send({
                "event": "payload",
                "type": data["type"],
                "id": data["id"],
                "payload": {"event": "set", "value": data["value"]}
            })
        except Exception as e:
            if self.log_callback:
                self.log_callback(f"Error applying remote data: {e}")

    def refresh_listeners(self):
        cfg = self.config.get()

        for item in cfg["listen_map"]:
            self.veado.listen_node(item["type"], item["id"])

    def on_config_reload(self, cfg):
        if self.log_callback:
            self.log_callback("[CLIENT] Applying new config")

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
                if self.log_callback:
                    self.log_callback(f"[SENDER ERROR] {e}")

                await asyncio.sleep(1)

# ---------------------------
# CLIENT GUI
# ---------------------------
class ClientGUI:
    """GUI interface for managing the Veadotube client.
    
    This class provides a graphical user interface for monitoring the Veadotube client.
    
    Attributes:
        root: The main Tkinter window
        log_text: ScrolledText widget for displaying client logs
        client: BridgeClient instance for running the client
    """
    
    def __init__(self, root):
        self.root = root
        self.root.title("Veadotube Client")
        self.root.geometry("600x500")
        
        # Create UI elements
        self.create_widgets()
        
        # Initialize client
        self.client = BridgeClient(self.log_message)
        
        # Start client in a separate thread
        self.start_client()
        
    def create_widgets(self):
        # Log display
        log_frame = tk.LabelFrame(self.root, text="Client Logs")
        log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        self.log_text = scrolledtext.ScrolledText(log_frame, height=20)
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Add some initial log messages
        self.log_message("Veadotube Client UI")
        self.log_message("Client running automatically...")
        
    def log_message(self, message):
        """Add a message to the log display"""
        try:
            timestamp = time.strftime("%H:%M:%S")
            self.log_text.insert(tk.END, f"[{timestamp}] {message}\n")
            self.log_text.see(tk.END)
            self.log_text.update_idletasks()
        except Exception as e:
            # If GUI is not available, just print to console
            print(f"GUI logging error: {e}")
            print(message)
            
    def start_client(self):
        """Start the client in a separate thread"""
        def run_client():
            try:
                asyncio.run(self.client.start())
            except Exception as e:
                self.log_message(f"Client error: {e}")
                # Don't close the window, let user read the error
                self.log_message("Client stopped due to error. Please check logs above.")
        
        # Start client in a separate thread
        client_thread = threading.Thread(target=run_client, daemon=True)
        client_thread.start()
        
    def stop_client(self):
        """Stop the client"""
        self.log_message("Stopping client...")
        # Here we could add logic to properly shut down the client
        # For now, we'll just close the window
        self.root.destroy()


# ---------------------------
# RUN
# ---------------------------
def main():
    root = tk.Tk()
    app = ClientGUI(root)
    
    def on_closing():
        app.stop_client()
        root.destroy()
        
    root.protocol("WM_DELETE_WINDOW", on_closing)
    root.mainloop()

if __name__ == "__main__":
    main()