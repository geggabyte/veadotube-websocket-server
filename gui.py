import tkinter as tk
from tkinter import ttk
import json
import websocket
import threading

CONFIG_PATH = "config.json"


# ---------------------------
# VEADOTUBE NODE FETCHER
# ---------------------------
def fetch_nodes():
    nodes = []

    try:
        ws = websocket.WebSocket()
        ws.connect("ws://127.0.0.1:2424?n=ConfigEditor")
        ws.recv()
        ws.send('nodes: {"event":"list"}')

        msg = ws.recv()
        print(msg)
        _, data = msg.split(":", 1)
        data = json.loads(data.strip())

        for entry in data.get("entries", []):
            nodes.append(f"{entry['type']}:{entry['id']}")

        ws.close()
    except Exception as e:
        print("[FETCH ERROR]", e)

    return nodes


# ---------------------------
# GUI
# ---------------------------
class ConfigGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Veadotube Config Editor")

        self.node_options = ["boolean:mini"]  # fallback

        # buttons
        top = tk.Frame(self.root)
        top.pack()

        tk.Button(top, text="Fetch Nodes", command=self.update_nodes).pack(side="left")
        tk.Button(top, text="Load", command=self.load).pack(side="left")
        tk.Button(top, text="Save", command=self.save).pack(side="left")

        # LISTEN MAP
        tk.Label(self.root, text="Listen Map").pack()
        self.listen_frame = tk.Frame(self.root)
        self.listen_frame.pack()

        self.listen_rows = []

        tk.Button(self.root, text="+ Add Listen", command=self.add_listen_row).pack()

        # SEND MAP
        tk.Label(self.root, text="Send Map").pack()
        self.send_frame = tk.Frame(self.root)
        self.send_frame.pack()

        self.send_rows = []

        tk.Button(self.root, text="+ Add Send", command=self.add_send_row).pack()

        self.load()

    # -----------------------
    # NODE FETCH
    # -----------------------
    def update_nodes(self):
        self.node_options = fetch_nodes()
        print("[NODES]", self.node_options)

    # -----------------------
    # LISTEN ROW
    # -----------------------
    def add_listen_row(self, value=None):
        frame = tk.Frame(self.listen_frame)
        frame.pack()

        var = tk.StringVar()

        cb = ttk.Combobox(frame, textvariable=var, values=self.node_options, width=30)
        cb.pack(side="left")

        if value:
            var.set(value)

        tk.Button(frame, text="X", command=lambda: self.remove_row(frame, self.listen_rows)).pack(side="left")

        self.listen_rows.append((frame, var))

    # -----------------------
    # SEND ROW
    # -----------------------
    def add_send_row(self, from_val=None, to_val=None):
        frame = tk.Frame(self.send_frame)
        frame.pack()

        from_var = tk.StringVar()
        to_var = tk.StringVar()

        ttk.Combobox(frame, textvariable=from_var, values=self.node_options, width=25).pack(side="left")
        tk.Label(frame, text="→").pack(side="left")
        ttk.Combobox(frame, textvariable=to_var, values=self.node_options, width=25).pack(side="left")

        if from_val:
            from_var.set(from_val)
        if to_val:
            to_var.set(to_val)

        tk.Button(frame, text="X", command=lambda: self.remove_row(frame, self.send_rows)).pack(side="left")

        self.send_rows.append((frame, from_var, to_var))

    # -----------------------
    def remove_row(self, frame, collection):
        frame.destroy()
        collection[:] = [r for r in collection if r[0] != frame]

    # -----------------------
    # LOAD CONFIG
    # -----------------------
    def load(self):
        try:
            with open(CONFIG_PATH, "r") as f:
                cfg = json.load(f)

            # clear
            for r in self.listen_rows:
                r[0].destroy()
            for r in self.send_rows:
                r[0].destroy()

            self.listen_rows.clear()
            self.send_rows.clear()

            # load listen
            for item in cfg.get("listen_map", []):
                val = f"{item['type']}:{item['id']}"
                self.add_listen_row(val)

            # load send
            for item in cfg.get("send_map", []):
                fval = f"{item['from']['type']}:{item['from']['id']}"
                tval = f"{item['to']['type']}:{item['to']['id']}"
                self.add_send_row(fval, tval)

        except Exception as e:
            print("[LOAD ERROR]", e)

    # -----------------------
    # SAVE CONFIG
    # -----------------------
    def save(self):
        cfg = {
            "client_id": "clientGUI",
            "proxy_url": "ws://localhost:8765",
            "veado_host": "127.0.0.1",
            "veado_port": 2424,
            "listen_map": [],
            "send_map": []
        }

        # listen
        for _, var in self.listen_rows:
            if ":" in var.get():
                t, i = var.get().split(":")
                cfg["listen_map"].append({"type": t, "id": i})

        # send
        for _, fvar, tvar in self.send_rows:
            if ":" in fvar.get() and ":" in tvar.get():
                ft, fi = fvar.get().split(":")
                tt, ti = tvar.get().split(":")

                cfg["send_map"].append({
                    "from": {"type": ft, "id": fi},
                    "to": {"type": tt, "id": ti}
                })

        with open(CONFIG_PATH, "w") as f:
            json.dump(cfg, f, indent=2)

        print("[GUI] Saved config")

    # -----------------------
    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    ConfigGUI().run()