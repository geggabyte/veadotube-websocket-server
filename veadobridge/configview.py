"""The Configuration tab: everything the old separate config editor did, plus
node discovery, validation feedback and unsaved-change tracking."""

import threading
import tkinter as tk
from tkinter import messagebox, ttk

from .config import validate
from .logbus import get_logger
from .nodes import fetch_nodes
from .paths import open_in_file_manager
from .veado import discover_instances
from .widgets import ScrollableFrame

log = get_logger("gui")

FALLBACK_NODES = ["boolean:example", "number:example", "state:mini"]


class ConfigView(ttk.Frame):
    def __init__(self, master, config, on_saved=None):
        super().__init__(master, padding=8)
        self.config_manager = config
        self.on_saved = on_saved
        self.node_options = list(FALLBACK_NODES)
        self._dirty = False
        self._loading = False
        self.listen_rows = []
        self.send_rows = []

        self._build()
        self.refresh_from_config(self.config_manager.get(), force=True)

    # ------------------------------------------------------------------ build
    def _build(self):
        toolbar = ttk.Frame(self)
        toolbar.pack(fill=tk.X)
        ttk.Button(toolbar, text="Save & apply", command=self.save).pack(side=tk.LEFT)
        ttk.Button(toolbar, text="Reload from file", command=self.reload).pack(side=tk.LEFT, padx=4)
        ttk.Button(toolbar, text="Fetch nodes", command=self.fetch_nodes).pack(side=tk.LEFT)
        ttk.Button(toolbar, text="Find Veadotube", command=self.find_veadotube).pack(side=tk.LEFT, padx=4)
        ttk.Button(toolbar, text="Open config folder", command=self.open_folder).pack(side=tk.LEFT)
        self.dirty_label = ttk.Label(toolbar, text="", foreground="#b26a00")
        self.dirty_label.pack(side=tk.RIGHT)

        scroller = ScrollableFrame(self)
        scroller.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        body = scroller.body

        general = ttk.LabelFrame(body, text="General", padding=8)
        general.pack(fill=tk.X, padx=2, pady=(0, 8))
        general.columnconfigure(1, weight=1)

        self.vars = {}
        rows = [
            ("client_id", "Client ID", "Name for this machine. Must differ on each side of the bridge."),
            ("proxy_url", "Proxy URL", "Where the bridge client connects, e.g. ws://192.168.1.10:8765"),
            ("veado_host", "Veadotube host", "Usually 127.0.0.1"),
            ("veado_port", "Veadotube port", "The port shown in Veadotube's WebSocket server settings"),
            ("proxy_host", "Proxy bind address", "0.0.0.0 accepts connections from other machines"),
            ("proxy_port", "Proxy port", "Port this machine's proxy server listens on"),
            ("send_rate", "Send rate (Hz)", "How often queued updates are flushed to the proxy"),
            ("reconnect_delay", "Reconnect delay (s)", "Pause between reconnection attempts"),
        ]
        for index, (key, label, hint) in enumerate(rows):
            ttk.Label(general, text=label).grid(row=index, column=0, sticky="w", pady=2)
            var = tk.StringVar()
            var.trace_add("write", lambda *_a: self._mark_dirty())
            self.vars[key] = var
            ttk.Entry(general, textvariable=var, width=42).grid(row=index, column=1, sticky="ew", padx=6)
            ttk.Label(general, text=hint, foreground="#666666").grid(row=index, column=2, sticky="w")

        options = ttk.LabelFrame(body, text="Startup and logging", padding=8)
        options.pack(fill=tk.X, padx=2, pady=(0, 8))

        self.run_proxy_var = tk.BooleanVar()
        self.run_client_var = tk.BooleanVar()
        for var in (self.run_proxy_var, self.run_client_var):
            var.trace_add("write", lambda *_a: self._mark_dirty())
        ttk.Checkbutton(options, text="Start the proxy server on launch", variable=self.run_proxy_var).pack(anchor="w")
        ttk.Checkbutton(options, text="Start the bridge client on launch", variable=self.run_client_var).pack(anchor="w")

        level_row = ttk.Frame(options)
        level_row.pack(anchor="w", pady=(6, 0))
        ttk.Label(level_row, text="Log level").pack(side=tk.LEFT)
        self.log_level_var = tk.StringVar()
        self.log_level_var.trace_add("write", lambda *_a: self._mark_dirty())
        ttk.Combobox(
            level_row,
            textvariable=self.log_level_var,
            values=["DEBUG", "INFO", "WARNING", "ERROR"],
            width=10,
            state="readonly",
        ).pack(side=tk.LEFT, padx=6)

        listen = ttk.LabelFrame(
            body, text="Listen map - Veadotube nodes this machine watches", padding=8
        )
        listen.pack(fill=tk.X, padx=2, pady=(0, 8))
        self.listen_frame = ttk.Frame(listen)
        self.listen_frame.pack(fill=tk.X)
        ttk.Button(listen, text="+ Add node", command=lambda: self.add_listen_row()).pack(anchor="w", pady=(6, 0))

        send = ttk.LabelFrame(
            body, text="Send map - how watched nodes map onto the other machine", padding=8
        )
        send.pack(fill=tk.X, padx=2, pady=(0, 8))
        self.send_frame = ttk.Frame(send)
        self.send_frame.pack(fill=tk.X)
        ttk.Button(send, text="+ Add mapping", command=lambda: self.add_send_row()).pack(anchor="w", pady=(6, 0))

    # ------------------------------------------------------------------- rows
    def add_listen_row(self, value=""):
        row = ttk.Frame(self.listen_frame)
        row.pack(fill=tk.X, pady=1)
        var = tk.StringVar(value=value)
        var.trace_add("write", lambda *_a: self._mark_dirty())
        combo = ttk.Combobox(row, textvariable=var, values=self.node_options, width=38)
        combo.pack(side=tk.LEFT)
        ttk.Button(row, text="Remove", width=8, command=lambda: self._remove(row, self.listen_rows)).pack(side=tk.LEFT, padx=4)
        self.listen_rows.append((row, var, combo))
        self._mark_dirty()

    def add_send_row(self, from_value="", to_value=""):
        row = ttk.Frame(self.send_frame)
        row.pack(fill=tk.X, pady=1)
        from_var = tk.StringVar(value=from_value)
        to_var = tk.StringVar(value=to_value)
        for var in (from_var, to_var):
            var.trace_add("write", lambda *_a: self._mark_dirty())
        from_combo = ttk.Combobox(row, textvariable=from_var, values=self.node_options, width=30)
        from_combo.pack(side=tk.LEFT)
        ttk.Label(row, text="  ->  ").pack(side=tk.LEFT)
        to_combo = ttk.Combobox(row, textvariable=to_var, values=self.node_options, width=30)
        to_combo.pack(side=tk.LEFT)
        ttk.Button(row, text="Remove", width=8, command=lambda: self._remove(row, self.send_rows)).pack(side=tk.LEFT, padx=4)
        self.send_rows.append((row, from_var, to_var, from_combo, to_combo))
        self._mark_dirty()

    def _remove(self, row, collection):
        row.destroy()
        collection[:] = [entry for entry in collection if entry[0] is not row]
        self._mark_dirty()

    def _clear_rows(self):
        for entry in self.listen_rows + self.send_rows:
            entry[0].destroy()
        self.listen_rows.clear()
        self.send_rows.clear()

    # ------------------------------------------------------------------- load
    def refresh_from_config(self, cfg, force=False):
        """Fill the form from a config dict. Unsaved edits are never overwritten."""
        if self._dirty and not force:
            log.warning(
                "config.json changed on disk but the Configuration tab has unsaved edits - "
                "the form was left alone. Use 'Reload from file' to discard them."
            )
            return

        self._loading = True
        try:
            for key, var in self.vars.items():
                var.set(str(cfg.get(key, "")))
            self.run_proxy_var.set(bool(cfg.get("run_proxy", False)))
            self.run_client_var.set(bool(cfg.get("run_client", False)))
            self.log_level_var.set(cfg.get("log_level", "INFO"))

            self._clear_rows()
            for node in cfg.get("listen_map", []):
                self.add_listen_row("%s:%s" % (node["type"], node["id"]))
            for mapping in cfg.get("send_map", []):
                self.add_send_row(
                    "%s:%s" % (mapping["from"]["type"], mapping["from"]["id"]),
                    "%s:%s" % (mapping["to"]["type"], mapping["to"]["id"]),
                )
            self._remember_known_nodes()
        finally:
            self._loading = False
            self._set_dirty(False)

    def reload(self):
        self.refresh_from_config(self.config_manager.load(), force=True)
        log.info("Configuration reloaded from disk")

    # ------------------------------------------------------------------- save
    def collect(self):
        cfg = self.config_manager.get()
        for key, var in self.vars.items():
            cfg[key] = var.get().strip()
        cfg["run_proxy"] = bool(self.run_proxy_var.get())
        cfg["run_client"] = bool(self.run_client_var.get())
        cfg["log_level"] = self.log_level_var.get()
        cfg["listen_map"] = [entry[1].get().strip() for entry in self.listen_rows if entry[1].get().strip()]
        cfg["send_map"] = [
            {"from": entry[1].get().strip(), "to": entry[2].get().strip()}
            for entry in self.send_rows
            if entry[1].get().strip() and entry[2].get().strip()
        ]
        return cfg

    def save(self):
        draft = self.collect()
        clean, problems = validate(draft)
        if problems:
            messagebox.showwarning(
                "Configuration adjusted",
                "Some values were not usable and were replaced:\n\n- " + "\n- ".join(problems),
                parent=self,
            )
        try:
            stored = self.config_manager.save(clean)
        except OSError as exc:
            messagebox.showerror(
                "Could not save",
                "The configuration could not be written to\n%s\n\n%s"
                % (self.config_manager.path, exc),
                parent=self,
            )
            log.error("Saving the configuration failed: %s", exc)
            return None
        self.refresh_from_config(stored, force=True)
        if self.on_saved:
            self.on_saved(stored)
        return stored

    # -------------------------------------------------------------- discovery
    def fetch_nodes(self):
        host = self.vars["veado_host"].get().strip() or "127.0.0.1"
        port = self.vars["veado_port"].get().strip() or "2424"
        log.info("Asking Veadotube at %s:%s for its node list...", host, port)

        def worker():
            try:
                nodes = fetch_nodes(host, port)
            except Exception as exc:
                # Bind the exception now: `exc` is gone once this block ends.
                self.after(0, lambda error=exc: self._nodes_failed(host, port, error))
                return
            self.after(0, lambda: self._nodes_ready(nodes))

        threading.Thread(target=worker, name="fetch-nodes", daemon=True).start()

    def _nodes_ready(self, nodes):
        if not nodes:
            # Veadotube 0.6 (the full app) does not answer node list requests;
            # veadotube mini does. Say so instead of leaving an empty drop-down.
            log.warning("Veadotube did not send a node list.")
            messagebox.showinfo(
                "No node list",
                "Veadotube connected but did not answer the node list request - not every "
                "version supports it.\n\nType node names by hand instead, as type:id, for "
                "example  boolean:MyToggle.\n\nThe names are the ones you gave the nodes in "
                "Veadotube.",
                parent=self,
            )
            return
        self.node_options = sorted(set(self.node_options) | set(nodes))
        self._apply_node_options()
        log.info("Node list updated: %s", ", ".join(nodes))

    def _nodes_failed(self, host, port, exc):
        log.error("Could not fetch nodes from %s:%s - %s", host, port, exc)
        messagebox.showerror(
            "Could not reach Veadotube",
            "No answer from %s:%s.\n\n%s\n\nCheck that Veadotube is running and that its "
            "WebSocket server is enabled on that port." % (host, port, exc),
            parent=self,
        )

    def _remember_known_nodes(self):
        """Values already in the config belong in the drop-downs too."""
        values = set(self.node_options)
        for entry in self.listen_rows:
            if entry[1].get().strip():
                values.add(entry[1].get().strip())
        for entry in self.send_rows:
            for var in (entry[1], entry[2]):
                if var.get().strip():
                    values.add(var.get().strip())
        self.node_options = sorted(values)
        self._apply_node_options()

    def _apply_node_options(self):
        for entry in self.listen_rows:
            entry[2].configure(values=self.node_options)
        for entry in self.send_rows:
            entry[3].configure(values=self.node_options)
            entry[4].configure(values=self.node_options)

    def find_veadotube(self):
        instances = discover_instances()
        if not instances:
            messagebox.showinfo(
                "No instances found",
                "No running Veadotube instance was found on this machine.\n\n"
                "Enter the host and port from Veadotube's WebSocket server settings by hand.",
                parent=self,
            )
            return
        name, host, port = instances[0]
        self.vars["veado_host"].set(host)
        self.vars["veado_port"].set(str(port))
        log.info("Found Veadotube instance '%s' at %s:%d", name, host, port)
        if len(instances) > 1:
            log.info(
                "Other instances found: %s",
                ", ".join("%s (%s:%d)" % item for item in instances[1:]),
            )

    def open_folder(self):
        import os

        folder = os.path.dirname(self.config_manager.path)
        if not open_in_file_manager(folder):
            messagebox.showinfo("Configuration folder", folder, parent=self)

    # ------------------------------------------------------------------ dirty
    def _mark_dirty(self):
        if not self._loading:
            self._set_dirty(True)

    def _set_dirty(self, value):
        self._dirty = value
        self.dirty_label.configure(text="Unsaved changes" if value else "")

    @property
    def dirty(self):
        return self._dirty
