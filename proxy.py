# proxy.py
import asyncio
import websockets
import tkinter as tk
from tkinter import ttk, scrolledtext
import threading
import time
import sys
import os

# Global variables for server control
server_running = False
clients = set()

class ProxyServerGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Veadotube Proxy Server")
        self.root.geometry("600x500")
        
        # Server status
        self.status_var = tk.StringVar()
        self.status_var.set("Server Stopped")
        
        # Create UI elements
        self.create_widgets()
        
        # Server thread
        self.server_thread = None
        
    def create_widgets(self):
        # Status frame
        status_frame = tk.Frame(self.root)
        status_frame.pack(fill=tk.X, padx=10, pady=5)
        
        tk.Label(status_frame, text="Server Status:").pack(side=tk.LEFT)
        tk.Label(status_frame, textvariable=self.status_var, fg="red").pack(side=tk.LEFT, padx=5)
        
        # Control buttons
        control_frame = tk.Frame(self.root)
        control_frame.pack(fill=tk.X, padx=10, pady=5)
        
        self.start_button = tk.Button(control_frame, text="Start Server", command=self.start_server)
        self.start_button.pack(side=tk.LEFT, padx=5)
        
        self.stop_button = tk.Button(control_frame, text="Stop Server", command=self.stop_server, state=tk.DISABLED)
        self.stop_button.pack(side=tk.LEFT, padx=5)
        
        # Log display
        log_frame = tk.LabelFrame(self.root, text="Server Logs")
        log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        self.log_text = scrolledtext.ScrolledText(log_frame, height=20)
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Add some initial log messages
        self.log_message("Veadotube Proxy Server UI")
        self.log_message("Ready to start server...")
        
    def log_message(self, message):
        """Add a message to the log display"""
        timestamp = time.strftime("%H:%M:%S")
        self.log_text.insert(tk.END, f"[{timestamp}] {message}\n")
        self.log_text.see(tk.END)
        self.log_text.update_idletasks()
        
    def start_server(self):
        """Start the proxy server in a separate thread"""
        if not server_running:
            self.log_message("Starting server...")
            self.start_button.config(state=tk.DISABLED)
            self.stop_button.config(state=tk.NORMAL)
            self.status_var.set("Server Running")
            
            # Start server in a separate thread
            self.server_thread = threading.Thread(target=self.run_server, daemon=True)
            self.server_thread.start()
            
    def stop_server(self):
        """Stop the proxy server"""
        global server_running
        if server_running:
            self.log_message("Stopping server...")
            server_running = False
            self.start_button.config(state=tk.NORMAL)
            self.stop_button.config(state=tk.DISABLED)
            self.status_var.set("Server Stopped")
            
    def run_server(self):
        """Run the server in a separate thread"""
        global server_running
        
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
            global server_running
            server_running = True
            try:
                async with websockets.serve(handler, "0.0.0.0", 8765):
                    self.log_message("Server listening on ws://0.0.0.0:8765")
                    await asyncio.Future()  # Run forever
            except Exception as e:
                self.log_message(f"Server error: {e}")
                server_running = False

        # Run the server
        try:
            asyncio.run(main())
        except Exception as e:
            self.log_message(f"Server error: {e}")
            server_running = False

def main():
    root = tk.Tk()
    app = ProxyServerGUI(root)
    root.protocol("WM_DELETE_WINDOW", lambda: app.stop_server())
    root.mainloop()

if __name__ == "__main__":
    main()