"""The Configuration tab: everything the old separate config editor did, plus
node discovery, validation feedback and unsaved-change tracking.

The node drop-downs are filled from the running Veadotube instance, never from
a hardcoded list: node names are whatever the user typed in Veadotube and they
change whenever the avatar does.  Nodes still named in config.json are kept in
the list so an existing mapping is never silently rewritten, but they are
marked so it is obvious Veadotube no longer reports them.
"""

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

UNKNOWN_MARK = "not reported by Veadotube"
UNKNOWN_COLOUR = "#b26a00"
NAME_COLOUR = "#666666"


class _NodePicker:
    """A combobox holding one "type:id" node reference.

    The value stays the bare "type:id" that gets saved; the node's display name
    from Veadotube is shown beside it rather than folded into the value, so
    nothing has to be parsed back out on save.
    """

    def __init__(self, parent, value, on_change, width):
        self.var = tk.StringVar(value=value)
        self.var.trace_add("write", on_change)
        self.combo = ttk.Combobox(parent, textvariable=self.var, width=width)

    @property
    def value(self):
        return self.var.get().strip()

    def set_options(self, options):
        self.combo.configure(values=options)

    def is_stale(self, live_nodes):
        """True when this picker names a node the live Veadotube did not list."""
        return bool(self.value) and bool(live_nodes) and self.value not in live_nodes

    def note(self, live_nodes):
        """(text, colour) describing this node, as far as Veadotube is concerned."""
        if not self.value or not live_nodes:
            return "", NAME_COLOUR  # nothing typed, or no list read yet
        if self.value not in live_nodes:
            return UNKNOWN_MARK, UNKNOWN_COLOUR
        name = live_nodes[self.value]
        return (name, NAME_COLOUR) if name else ("", NAME_COLOUR)


class _ListenRow:
    def __init__(self, parent, value, on_change, on_remove):
        self.frame = ttk.Frame(parent)
        self.frame.pack(fill=tk.X, pady=1)
        self.node = _NodePicker(self.frame, value, on_change, width=38)
        self.node.combo.pack(side=tk.LEFT)
        ttk.Button(self.frame, text="Remove", width=8, command=lambda: on_remove(self)).pack(
            side=tk.LEFT, padx=4
        )
        self.marker = ttk.Label(self.frame, text="", foreground=NAME_COLOUR)
        self.marker.pack(side=tk.LEFT)

    @property
    def pickers(self):
        return (self.node,)

    def refresh_marker(self, live_nodes):
        text, colour = self.node.note(live_nodes)
        self.marker.configure(text=text, foreground=colour)


class _SendRow:
    def __init__(self, parent, from_value, to_value, on_change, on_remove):
        self.frame = ttk.Frame(parent)
        self.frame.pack(fill=tk.X, pady=1)
        self.source = _NodePicker(self.frame, from_value, on_change, width=30)
        self.source.combo.pack(side=tk.LEFT)
        ttk.Label(self.frame, text="  ->  ").pack(side=tk.LEFT)
        self.target = _NodePicker(self.frame, to_value, on_change, width=30)
        self.target.combo.pack(side=tk.LEFT)
        ttk.Button(self.frame, text="Remove", width=8, command=lambda: on_remove(self)).pack(
            side=tk.LEFT, padx=4
        )
        self.marker = ttk.Label(self.frame, text="", foreground=NAME_COLOUR)
        self.marker.pack(side=tk.LEFT)

    @property
    def pickers(self):
        return (self.source, self.target)

    def refresh_marker(self, live_nodes):
        # The target lives on the *other* machine, so only the source can be
        # checked against the Veadotube this app is talking to.
        text, colour = self.source.note(live_nodes)
        self.marker.configure(text=text, foreground=colour)


class ConfigView(ttk.Frame):
    def __init__(self, master, config, on_saved=None):
        super().__init__(master, padding=8)
        self.config_manager = config
        self.on_saved = on_saved
        # What Veadotube last told us it has, as {"type:id": display name}.
        # Empty means "we do not know yet", which is deliberately different
        # from "it has no nodes".
        self.live_nodes = {}
        self.node_options = []
        self._fetch_in_flight = False
        self._fetched_from = None  # (host, port) the live list came from
        self._dirty = False
        self._loading = False
        self.listen_rows = []
        self.send_rows = []

        self._build()
        self.refresh_from_config(self.config_manager.get(), force=True)
        # Ask Veadotube as soon as the window is up, so the drop-downs are
        # populated before the user ever opens this tab.
        self.after(400, self.auto_fetch_nodes)

    # ------------------------------------------------------------------ build
    def _build(self):
        toolbar = ttk.Frame(self)
        toolbar.pack(fill=tk.X)
        ttk.Button(toolbar, text="Save & apply", command=self.save).pack(side=tk.LEFT)
        ttk.Button(toolbar, text="Reload from file", command=self.reload).pack(side=tk.LEFT, padx=4)
        ttk.Button(toolbar, text="Refresh nodes", command=self.fetch_nodes).pack(side=tk.LEFT)
        ttk.Button(toolbar, text="Find Veadotube", command=self.find_veadotube).pack(side=tk.LEFT, padx=4)
        ttk.Button(toolbar, text="Open config folder", command=self.open_folder).pack(side=tk.LEFT)
        self.dirty_label = ttk.Label(toolbar, text="", foreground="#b26a00")
        self.dirty_label.pack(side=tk.RIGHT)
        self.nodes_label = ttk.Label(toolbar, text="Nodes: not read yet", foreground="#666666")
        self.nodes_label.pack(side=tk.RIGHT, padx=8)

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

        # A new address means the node list we hold belongs to a different
        # instance, so drop it rather than offering nodes from the old one.
        for key in ("veado_host", "veado_port"):
            self.vars[key].trace_add("write", lambda *_a: self._veado_address_changed())

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
        self.listen_rows.append(
            _ListenRow(self.listen_frame, value, lambda *_a: self._node_edited(), self._remove_listen)
        )
        self._refresh_node_options()
        self._mark_dirty()

    def add_send_row(self, from_value="", to_value=""):
        self.send_rows.append(
            _SendRow(
                self.send_frame, from_value, to_value,
                lambda *_a: self._node_edited(), self._remove_send,
            )
        )
        self._refresh_node_options()
        self._mark_dirty()

    def _remove_listen(self, row):
        row.frame.destroy()
        self.listen_rows.remove(row)
        self._refresh_node_options()
        self._mark_dirty()

    def _remove_send(self, row):
        row.frame.destroy()
        self.send_rows.remove(row)
        self._refresh_node_options()
        self._mark_dirty()

    def _clear_rows(self):
        for row in self.listen_rows + self.send_rows:
            row.frame.destroy()
        self.listen_rows.clear()
        self.send_rows.clear()

    def _node_edited(self):
        self._mark_dirty()
        self._refresh_markers()

    @property
    def _all_rows(self):
        return self.listen_rows + self.send_rows

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
            self._refresh_node_options()
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
        cfg["listen_map"] = [row.node.value for row in self.listen_rows if row.node.value]
        cfg["send_map"] = [
            {"from": row.source.value, "to": row.target.value}
            for row in self.send_rows
            if row.source.value and row.target.value
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
        self.auto_fetch_nodes()
        return stored

    # -------------------------------------------------------------- discovery
    def _veado_address(self):
        host = self.vars["veado_host"].get().strip() or "127.0.0.1"
        port = self.vars["veado_port"].get().strip() or "2424"
        return host, port

    def _veado_address_changed(self):
        """The node list describes one instance; a new address invalidates it."""
        if self._loading or self._fetched_from is None:
            return
        if self._veado_address() != self._fetched_from:
            self._fetched_from = None
            self.live_nodes = {}
            self.nodes_label.configure(text="Nodes: address changed, refresh to re-read")
            self._refresh_node_options()

    def on_shown(self):
        """The Configuration tab was brought to the front."""
        self.auto_fetch_nodes()

    def on_veado_connected(self):
        """The bridge client just reached Veadotube - its node list is worth re-reading."""
        self.auto_fetch_nodes(force=True)

    def auto_fetch_nodes(self, force=False):
        """Read the node list in the background without nagging on failure."""
        if not force and self._fetched_from == self._veado_address():
            return  # already have a live list for this instance
        self.fetch_nodes(quiet=True)

    def fetch_nodes(self, quiet=False):
        if self._fetch_in_flight:
            return
        host, port = self._veado_address()
        if not quiet:
            log.info("Asking Veadotube at %s:%s for its node list...", host, port)
        self._fetch_in_flight = True
        self.nodes_label.configure(text="Nodes: reading from Veadotube...")

        def worker():
            try:
                nodes = fetch_nodes(host, port)
            except Exception as exc:
                # Bind the exception now: `exc` is gone once this block ends.
                self._on_tk_thread(lambda error=exc: self._nodes_failed(host, port, error, quiet))
                return
            self._on_tk_thread(lambda: self._nodes_ready(host, port, nodes, quiet))

        threading.Thread(target=worker, name="fetch-nodes", daemon=True).start()

    def _on_tk_thread(self, callback):
        """Hand a callback to the Tk thread; the window may already be gone."""
        try:
            self.after(0, callback)
        except tk.TclError:
            self._fetch_in_flight = False

    def set_live_nodes(self, entries, source):
        """Take a node list straight from Veadotube. `entries` is [(key, name)].

        Called both by the one-off fetch and by the running bridge client,
        which is subscribed to the nodes channel and gets a fresh list pushed
        whenever it changes.
        """
        if not entries:
            return
        # Replace, never merge: a node Veadotube no longer lists is gone, and
        # keeping it in the drop-down is how stale entries used to survive.
        self.live_nodes = dict(entries)
        self._fetched_from = tuple(source)
        self.nodes_label.configure(
            text="Nodes: %d read from %s:%s" % (len(self.live_nodes), source[0], source[1])
        )
        self._refresh_node_options()
        self._warn_about_stale_mappings()

    def _nodes_ready(self, host, port, nodes, quiet):
        self._fetch_in_flight = False
        if not nodes:
            # Veadotube 0.6 (the full app) does not answer node list requests;
            # veadotube mini does. Say so instead of leaving an empty drop-down.
            self.nodes_label.configure(text="Nodes: Veadotube sent no list")
            log.warning("Veadotube did not send a node list.")
            if not quiet:
                messagebox.showinfo(
                    "No node list",
                    "Veadotube connected but did not answer the node list request - not every "
                    "version supports it.\n\nType node names by hand instead, as type:id, for "
                    "example  stateEvents:mini.\n\nThe names are the ones you gave the nodes in "
                    "Veadotube.",
                    parent=self,
                )
            return

        self.set_live_nodes(nodes, (host, port))
        log.info(
            "Node list read from Veadotube: %s",
            ", ".join("%s (%s)" % (key, name) if name else key for key, name in nodes),
        )

    def _nodes_failed(self, host, port, exc, quiet):
        self._fetch_in_flight = False
        self.nodes_label.configure(text="Nodes: %s:%s did not answer" % (host, port))
        if quiet:
            log.debug("Could not read nodes from %s:%s - %s", host, port, exc)
            return
        log.error("Could not fetch nodes from %s:%s - %s", host, port, exc)
        messagebox.showerror(
            "Could not reach Veadotube",
            "No answer from %s:%s.\n\n%s\n\nCheck that Veadotube is running and that its "
            "WebSocket server is enabled on that port." % (host, port, exc),
            parent=self,
        )

    def _warn_about_stale_mappings(self):
        # Send targets live on the other machine, so only local nodes are checked.
        local = [row.node for row in self.listen_rows] + [row.source for row in self.send_rows]
        stale = sorted({p.value for p in local if p.is_stale(self.live_nodes)})
        if stale:
            log.warning(
                "These configured nodes are not on this Veadotube any more and will never "
                "fire: %s", ", ".join(stale)
            )

    # ---------------------------------------------------------- node drop-downs
    def _refresh_node_options(self):
        """Offer the live nodes, plus anything the config still points at."""
        values = set(self.live_nodes)
        for row in self._all_rows:
            for picker in row.pickers:
                if picker.value:
                    values.add(picker.value)
        self.node_options = sorted(values)
        for row in self._all_rows:
            for picker in row.pickers:
                picker.set_options(self.node_options)
        self._refresh_markers()

    def _refresh_markers(self):
        for row in self._all_rows:
            row.refresh_marker(self.live_nodes)

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
        self.auto_fetch_nodes(force=True)

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
