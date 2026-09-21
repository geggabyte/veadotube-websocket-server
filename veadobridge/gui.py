"""The single window: dashboard on top, one shared log at the bottom."""

import collections
import logging
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk
import time

from . import APP_NAME, VERSION
from .configview import ConfigView
from .logbus import get_logger, log_path
from .paths import log_dir, open_in_file_manager
from .service import FAILED, RUNNING, STARTING, STOPPING
from .widgets import StatusDot

log = get_logger("gui")

MAX_LOG_LINES = 4000
LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR"]

LEVEL_STYLE = {
    logging.DEBUG: ("debug", "#7a7a7a"),
    logging.INFO: ("info", "#1c1c1c"),
    logging.WARNING: ("warning", "#b26a00"),
    logging.ERROR: ("error", "#c33c3c"),
    logging.CRITICAL: ("critical", "#8b0000"),
}

PHASE_DOT = {
    RUNNING: "ok",
    STARTING: "busy",
    STOPPING: "busy",
    FAILED: "bad",
}


class MainWindow:
    def __init__(self, root, controller):
        self.root = root
        self.controller = controller
        self.history = collections.deque(maxlen=MAX_LOG_LINES)
        self._pending_states = collections.deque()
        self._veado_status = None
        self._closing = False

        root.title("%s %s" % (APP_NAME, VERSION))
        root.geometry("940x680")
        root.minsize(760, 520)

        self._build()
        self._tick()

    # ------------------------------------------------------------------ build
    def _build(self):
        panes = ttk.PanedWindow(self.root, orient=tk.VERTICAL)
        panes.pack(fill=tk.BOTH, expand=True)

        notebook = ttk.Notebook(panes)
        self.dashboard = self._build_dashboard(notebook)
        self.config_view = ConfigView(
            notebook, self.controller.config, on_saved=self.controller.on_config_saved
        )
        notebook.add(self.dashboard, text="Dashboard")
        notebook.add(self.config_view, text="Configuration")
        notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)
        panes.add(notebook, weight=3)

        panes.add(self._build_log(panes), weight=4)

    def _build_dashboard(self, master):
        frame = ttk.Frame(master, padding=10)

        proxy_box = ttk.LabelFrame(frame, text="Proxy server", padding=10)
        proxy_box.pack(fill=tk.X)
        self.proxy_status = StatusDot(proxy_box, "stopped")
        self.proxy_status.pack(anchor="w")
        self.proxy_stats = ttk.Label(proxy_box, text="", foreground="#666666")
        self.proxy_stats.pack(anchor="w", pady=(2, 6))
        proxy_buttons = ttk.Frame(proxy_box)
        proxy_buttons.pack(anchor="w")
        self.proxy_start_btn = ttk.Button(proxy_buttons, text="Start", command=self.controller.start_proxy)
        self.proxy_start_btn.pack(side=tk.LEFT)
        self.proxy_stop_btn = ttk.Button(proxy_buttons, text="Stop", command=self.controller.stop_proxy)
        self.proxy_stop_btn.pack(side=tk.LEFT, padx=4)
        ttk.Button(proxy_buttons, text="Restart", command=self.controller.restart_proxy).pack(side=tk.LEFT)
        ttk.Button(proxy_buttons, text="Check port", command=self.controller.check_port).pack(side=tk.LEFT, padx=4)

        client_box = ttk.LabelFrame(frame, text="Bridge client", padding=10)
        client_box.pack(fill=tk.X, pady=(10, 0))
        self.client_status = StatusDot(client_box, "stopped")
        self.client_status.pack(anchor="w")
        self.client_stats = ttk.Label(client_box, text="", foreground="#666666")
        self.client_stats.pack(anchor="w", pady=(2, 6))
        client_buttons = ttk.Frame(client_box)
        client_buttons.pack(anchor="w")
        self.client_start_btn = ttk.Button(client_buttons, text="Start", command=self.controller.start_client)
        self.client_start_btn.pack(side=tk.LEFT)
        self.client_stop_btn = ttk.Button(client_buttons, text="Stop", command=self.controller.stop_client)
        self.client_stop_btn.pack(side=tk.LEFT, padx=4)
        ttk.Button(client_buttons, text="Restart", command=self.controller.restart_client).pack(side=tk.LEFT)

        info = ttk.LabelFrame(frame, text="This instance", padding=10)
        info.pack(fill=tk.X, pady=(10, 0))
        ttk.Label(info, text="Config: %s" % self.controller.config.path, foreground="#666666").pack(anchor="w")
        ttk.Label(info, text="Log file: %s" % (log_path() or "not available"), foreground="#666666").pack(anchor="w")
        return frame

    def _build_log(self, master):
        frame = ttk.LabelFrame(master, text="Log", padding=6)

        toolbar = ttk.Frame(frame)
        toolbar.pack(fill=tk.X)
        ttk.Label(toolbar, text="Show").pack(side=tk.LEFT)
        self.level_var = tk.StringVar(value="INFO")
        level_box = ttk.Combobox(
            toolbar, textvariable=self.level_var, values=LEVELS, width=9, state="readonly"
        )
        level_box.pack(side=tk.LEFT, padx=6)
        level_box.bind("<<ComboboxSelected>>", lambda _e: self._rerender())

        self.autoscroll_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(toolbar, text="Auto-scroll", variable=self.autoscroll_var).pack(side=tk.LEFT)
        ttk.Button(toolbar, text="Clear", command=self.clear_log).pack(side=tk.RIGHT)
        ttk.Button(toolbar, text="Open log folder", command=self._open_logs).pack(side=tk.RIGHT, padx=4)

        self.log_text = scrolledtext.ScrolledText(
            frame, height=16, wrap=tk.WORD, state=tk.DISABLED, font=("Consolas", 9)
        )
        self.log_text.pack(fill=tk.BOTH, expand=True, pady=(6, 0))
        for _level, (tag, colour) in LEVEL_STYLE.items():
            self.log_text.tag_configure(tag, foreground=colour)
        return frame

    # ------------------------------------------------------------------- ticks
    def _tick(self):
        """Runs on the Tk thread: drains logs and service state, keeps the UI live.

        It also gives the interpreter a chance to run signal handlers, which Tk's
        C-level main loop would otherwise starve.
        """
        if self._closing:
            return
        try:
            self._drain_logs()
            self._drain_states()
            self._refresh_buttons()
        except Exception:
            log.exception("Refreshing the window failed")
        finally:
            self.root.after(120, self._tick)

    def _drain_logs(self):
        handler = self.controller.gui_handler
        if handler is None:
            return
        entries = handler.drain()
        if not entries:
            return
        threshold = logging.getLevelName(self.level_var.get())
        if not isinstance(threshold, int):
            threshold = logging.INFO
        self.log_text.configure(state=tk.NORMAL)
        for entry in entries:
            self.history.append(entry)
            if entry[2] >= threshold:
                self._insert(entry)
        self._trim()
        self.log_text.configure(state=tk.DISABLED)
        if self.autoscroll_var.get():
            self.log_text.see(tk.END)

    def _insert(self, entry):
        created, source, levelno, message = entry
        tag = LEVEL_STYLE.get(levelno, LEVEL_STYLE[logging.INFO])[0]
        stamp = time.strftime("%H:%M:%S", time.localtime(created))
        self.log_text.insert(tk.END, "%s [%-6s] %s\n" % (stamp, source, message), tag)

    def _trim(self):
        lines = int(self.log_text.index("end-1c").split(".")[0])
        if lines > MAX_LOG_LINES:
            self.log_text.delete("1.0", "%d.0" % (lines - MAX_LOG_LINES))

    def _rerender(self):
        threshold = logging.getLevelName(self.level_var.get())
        if not isinstance(threshold, int):
            threshold = logging.INFO
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        for entry in self.history:
            if entry[2] >= threshold:
                self._insert(entry)
        self.log_text.configure(state=tk.DISABLED)
        if self.autoscroll_var.get():
            self.log_text.see(tk.END)

    def clear_log(self):
        self.history.clear()
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _open_logs(self):
        folder = log_dir()
        if not open_in_file_manager(folder):
            messagebox.showinfo("Log folder", folder, parent=self.root)

    # ------------------------------------------------------------------ state
    def queue_state(self, name, state):
        """Called from service threads; the Tk thread picks it up on the next tick."""
        self._pending_states.append((name, state))

    def _drain_states(self):
        seen = {}
        while self._pending_states:
            name, state = self._pending_states.popleft()
            seen[name] = state
        for name, state in seen.items():
            if name == "proxy":
                self._render_proxy(state)
            elif name == "client":
                self._render_client(state)

    def _render_proxy(self, state):
        self.proxy_status.set(PHASE_DOT.get(state.phase, "off"), state.detail or state.phase)
        stats = state.stats
        if state.phase == RUNNING:
            self.proxy_stats.configure(
                text="%d client(s) connected  ·  %d message(s) relayed"
                % (stats.get("clients", 0), stats.get("relayed", 0))
            )
        else:
            self.proxy_stats.configure(text="")

    def _on_tab_changed(self, event):
        try:
            current = event.widget.nametowidget(event.widget.select())
        except (tk.TclError, KeyError):
            return
        if current is self.config_view:
            self.config_view.on_shown()

    def _render_client(self, state):
        stats = state.stats
        # A fresh Veadotube connection is the moment its node list is worth
        # re-reading: the avatar (and with it the nodes) may have changed.
        veado_status = stats.get("veado") if state.phase == RUNNING else None
        if veado_status != self._veado_status:
            self._veado_status = veado_status
            if veado_status == "connected":
                self.config_view.on_veado_connected()
        if state.phase == RUNNING:
            veado_ok = stats.get("veado") == "connected"
            proxy_ok = stats.get("proxy") == "connected"
            dot = "ok" if (veado_ok and proxy_ok) else "busy"
            self.client_status.set(dot, state.detail)
            text = "sent %d  ·  received %d" % (stats.get("sent", 0), stats.get("received", 0))
            if stats.get("dropped"):
                text += "  ·  dropped %d" % stats["dropped"]
            self.client_stats.configure(text=text)
        else:
            self.client_status.set(PHASE_DOT.get(state.phase, "off"), state.detail or state.phase)
            self.client_stats.configure(text="")

    def _refresh_buttons(self):
        proxy_running = self.controller.proxy.running
        self.proxy_start_btn.state(["disabled"] if proxy_running else ["!disabled"])
        self.proxy_stop_btn.state(["!disabled"] if proxy_running else ["disabled"])
        client_running = self.controller.client.running
        self.client_start_btn.state(["disabled"] if client_running else ["!disabled"])
        self.client_stop_btn.state(["!disabled"] if client_running else ["disabled"])

    # --------------------------------------------------------------- shutdown
    def confirm_close(self):
        if self.config_view.dirty:
            answer = messagebox.askyesnocancel(
                "Unsaved changes",
                "The Configuration tab has unsaved changes.\n\nSave them before closing?",
                parent=self.root,
            )
            if answer is None:
                return False
            if answer:
                self.config_view.save()
        return True

    def show_busy(self, text):
        """Replace the window contents while shutting down, so it never looks hung."""
        self._closing = True
        for child in self.root.winfo_children():
            child.destroy()
        ttk.Label(self.root, text=text, padding=30).pack(expand=True)
        self.root.update_idletasks()
