# proxy.py
"""
WebSocket Proxy Server for Veadotube
==================================

This module implements a WebSocket proxy server that forwards messages between
connected clients. It provides both a GUI interface and command-line execution
options.

Features:
- WebSocket proxy server with client management
- GUI interface for monitoring the server
- Real-time message logging
- Cross-platform compatibility

Classes:
    ProxyServerGUI: GUI interface for managing the proxy server

Functions:
    main: Entry point for the application
"""

import asyncio
import websockets
import tkinter as tk
from tkinter import ttk, scrolledtext
import threading
import time
import sys
import os
from config_manager import ConfigManager

# Global variables for server control
clients = set()
server_instance = None

class ProxyServerGUI:
    """GUI interface for managing the WebSocket proxy server.
    
    This class provides a graphical user interface for monitoring the WebSocket proxy server.
    
    Attributes:
        root: The main Tkinter window
        status_var: Tkinter StringVar for displaying server status
        log_text: ScrolledText widget for displaying server logs
    """
    
    def __init__(self, root):
        self.root = root
        self.root.title("Veadotube Proxy Server")
        self.root.geometry("600x500")
        
        # Server status
        self.status_var = tk.StringVar()
        self.status_var.set("Server Running")
        
        # Create UI elements
        self.create_widgets()
        
        # Start server automatically when GUI is initialized
        self.start_server()
        
    def create_widgets(self):
        # Status frame
        status_frame = tk.Frame(self.root)
        status_frame.pack(fill=tk.X, padx=10, pady=5)
        
        tk.Label(status_frame, text="Server Status:").pack(side=tk.LEFT)
        tk.Label(status_frame, textvariable=self.status_var, fg="red").pack(side=tk.LEFT, padx=5)
        
        # Log display
        log_frame = tk.LabelFrame(self.root, text="Server Logs")
        log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        self.log_text = scrolledtext.ScrolledText(log_frame, height=20)
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Add some initial log messages
        self.log_message("Veadotube Proxy Server UI")
        self.log_message("Server running automatically...")
        
    def log_message(self, message):
        """Add a message to the log display"""
        timestamp = time.strftime("%H:%M:%S")
        self.log_text.insert(tk.END, f"[{timestamp}] {message}\n")
        self.log_text.see(tk.END)
        self.log_text.update_idletasks()
        
    def start_server(self):
        """Start the proxy server in a separate thread"""
        # Start server in a separate thread
        self.server_thread = threading.Thread(target=self.run_server, daemon=True)
        self.server_thread.start()
            
    def stop_server(self):
        """Stop the proxy server"""
        global server_running, server_instance
        if server_running:
            self.log_message("Stopping server. ..")
            server_running = False
            
            # Close all connected clients
            for client in clients.copy():
                try:
                    client.close()
                except:
                    pass
            clients.clear()
            
            # Close the server instance if it exists
            if server_instance:
                try:
                    # Properly close the server
                    server_instance.close()
                    # Wait for server to actually close using a temporary loop
                    temp_loop = asyncio.new_event_loop()
                    try:
                        temp_loop.run_until_complete(server_instance.wait_closed())
                    finally:
                        temp_loop.close()
                except Exception as e:
                    self.log_message(f"Error closing server: {e}")
            
            self.status_var.set("Server Stopped")
            
    def run_server(self):
        """Run the server in a separate thread"""
        global server_running, server_instance
        
        # Read port from config file using ConfigManager
        try:
            config_manager = ConfigManager("config.json")
            config = config_manager.get()
            port = config.get("proxy_port", 8765)
            # Validate port number
            if not isinstance(port, int) or port < 1 or port > 65535:
                raise ValueError("Invalid port number")
        except Exception as e:
            print(f"Error reading config: {e}")
            port = 8765  # fallback to default

        async def handler(ws):
            clients.add(ws)
            try:
                async for msg in ws:
                    self.log_message(f"Received: {msg}")
                    for c in clients:
                        if c != ws:
                            self.log_message(f"Forwarding: {msg}")
                            await c.send(msg)
            finally:
                clients.remove(ws)

        async def main():
            global server_running, server_instance
            server_running = True
            try:
                server = await websockets.serve(handler, "0.0.0.0", port)
                server_instance = server
                self.log_message(f"Server listening on ws://0.0.0.0:{port}")
                await server.wait_closed()  # Wait for the server to close
            except Exception as e:
                self.log_message(f"Server error: {e}")
                server_running = False

        # Run the server in a new event loop
        try:
            # Create a new event loop for this server instance
            server_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(server_loop)
            try:
                server_loop.run_until_complete(main())
            finally:
                server_loop.close()
        except Exception as e:
            self.log_message(f"Server error: {e}")
            server_running = False

def main():
    root = tk.Tk()
    app = ProxyServerGUI(root)
    def on_closing():
        app.stop_server()
        root.destroy()
    root.protocol("WM_DELETE_WINDOW", on_closing)
    root.mainloop()

if __name__ == "__main__":
    main()