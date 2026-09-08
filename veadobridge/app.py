"""Application wiring: one process, one window, one log, two services."""

import argparse
import atexit
import logging
import os
import signal
import sys
import threading
import tkinter as tk
from tkinter import messagebox

from . import APP_NAME, VERSION
from .client import ClientService
from .config import CLIENT_KEYS, PROXY_KEYS, ConfigManager
from .gui import MainWindow
from .logbus import get_logger, setup_logging
from .netutil import PortInUse, bind_listen_socket, port_is_free, process_alive, terminate_process
from .paths import default_config_path
from .proxy import ProxyService
from .singleton import AlreadyRunning, InstanceLock, stale_lock_sweep

log = get_logger("app")

# Executable names that mean "an older or leftover copy of this project".
OWN_PROCESS_NAMES = {
    "veadotubebridge.exe",
    "veadotubeproxy.exe",
    "veadotubeproxyclient.exe",
    "veadotubeproxyclientconfig.exe",
}


def _looks_like_our_app(process_name):
    """True when a port holder is plausibly another copy of this app."""
    if not process_name:
        return False
    name = process_name.lower()
    # A source checkout runs as python.exe / python3.10.exe / pythonw.exe.
    return (
        name in OWN_PROCESS_NAMES
        or name.startswith("python")
        or name == os.path.basename(sys.executable).lower()
    )

SHUTDOWN_WATCHDOG_SECONDS = 15


class Application:
    def __init__(self, config_path=None, debug=False):
        self.gui_handler = setup_logging(debug=debug)
        self.debug = debug
        self.config_path = os.path.abspath(config_path or default_config_path())
        self.lock = InstanceLock(self.config_path)
        self.config = None
        self.proxy = None
        self.client = None
        self.root = None
        self.window = None
        self._shutting_down = False
        self._previous_config = {}

    # -------------------------------------------------------------- lifecycle
    def run(self):
        log.info("%s %s starting", APP_NAME, VERSION)
        log.info("Configuration file: %s", self.config_path)

        stale_lock_sweep()
        try:
            self.lock.acquire()
        except AlreadyRunning as exc:
            self._report_already_running(exc)
            return 1
        atexit.register(self.lock.release)

        self.config = ConfigManager(self.config_path)
        self._previous_config = self.config.get()
        self._apply_log_level(self._previous_config.get("log_level", "INFO"))

        self.proxy = ProxyService(
            self.config, on_state=self._on_service_state, on_port_conflict=self._on_port_conflict
        )
        self.client = ClientService(self.config, on_state=self._on_service_state)
        self.config.register_callback(self._on_config_changed)

        self.root = tk.Tk()
        self.root.report_callback_exception = self._on_tk_exception
        self.window = MainWindow(self.root, self)
        self.root.protocol("WM_DELETE_WINDOW", self.request_shutdown)
        self.window.level_var.set(self._previous_config.get("log_level", "INFO"))
        self._install_signal_handlers()

        self._autostart()

        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            self.request_shutdown()
        return 0

    def _autostart(self):
        cfg = self.config.get()
        if cfg["run_proxy"]:
            self.start_proxy()
        else:
            log.info("Proxy server is set not to start automatically")
        if cfg["run_client"]:
            self.start_client()
        else:
            log.info("Bridge client is set not to start automatically")
        if not cfg["run_proxy"] and not cfg["run_client"]:
            log.info("Nothing is set to start - use the Dashboard buttons when you are ready")

    def _report_already_running(self, exc):
        message = (
            "%s is already running with this configuration.\n\n%s\n\n"
            "Close the other window first, or start this copy with a different config file "
            "using:  VeadotubeBridge.exe --config other.json"
        ) % (APP_NAME, exc)
        log.error("%s", exc)
        try:
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("Already running", message)
            root.destroy()
        except Exception:
            print(message, file=sys.stderr)

    # ----------------------------------------------------------- service control
    def start_proxy(self):
        self.proxy.start()

    def stop_proxy(self):
        self.proxy.stop()

    def restart_proxy(self):
        self.proxy.stop()
        self.proxy.start()

    def start_client(self):
        self.client.start()

    def stop_client(self):
        self.client.stop()

    def restart_client(self):
        self.client.stop()
        self.client.start()

    def _on_service_state(self, name, state):
        if self.window is not None:
            self.window.queue_state(name, state)

    # ------------------------------------------------------------------ config
    def on_config_saved(self, cfg):
        """Called by the Configuration tab right after a successful save."""
        self._on_config_changed(cfg)

    def _on_config_changed(self, cfg):
        """Apply a new config, restarting only the services that actually need it."""
        if self._shutting_down:
            return
        old, self._previous_config = self._previous_config, cfg

        self._apply_log_level(cfg.get("log_level", "INFO"))

        if self.window is not None and self.root is not None:
            self.root.after(0, lambda: self.window.config_view.refresh_from_config(cfg))

        if any(old.get(key) != cfg.get(key) for key in PROXY_KEYS) and self.proxy.running:
            log.info("Proxy settings changed - restarting the proxy server")
            self._on_ui_thread(self.restart_proxy)

        if any(old.get(key) != cfg.get(key) for key in CLIENT_KEYS) and self.client.running:
            log.info("Connection settings changed - restarting the bridge client")
            self._on_ui_thread(self.restart_client)
        elif (old.get("listen_map") != cfg.get("listen_map")
              or old.get("send_map") != cfg.get("send_map")):
            log.info("Node mappings changed - re-subscribing")
            self.client.apply_config()

    def _apply_log_level(self, level_name):
        level = getattr(logging, str(level_name).upper(), logging.INFO)
        library_level = logging.DEBUG if (self.debug or level <= logging.DEBUG) else logging.WARNING
        for name in ("websockets", "websocket", "asyncio"):
            logging.getLogger(name).setLevel(library_level)

    def _on_ui_thread(self, callback):
        """Service start/stop always happens on the Tk thread, so the port-conflict
        dialog has a window to attach to."""
        if self.root is not None:
            self.root.after(0, callback)
        else:
            callback()

    # ------------------------------------------------------------------- ports
    def check_port(self):
        """Dashboard button: report who holds the configured proxy port."""
        cfg = self.config.get()
        host, port = cfg["proxy_host"], cfg["proxy_port"]
        if self.proxy.running:
            messagebox.showinfo(
                "Port in use by this app",
                "The proxy server in this window is listening on %s:%d." % (host, port),
                parent=self.root,
            )
            return
        if port_is_free(host, port):
            log.info("Port %d on %s is free", port, host)
            messagebox.showinfo(
                "Port is free", "Nothing is using %s:%d." % (host, port), parent=self.root
            )
            return

        try:
            bind_listen_socket(host, port).close()
        except PortInUse as exc:
            if self._on_port_conflict(exc):
                messagebox.showinfo(
                    "Port released",
                    "Port %d is free again. You can start the proxy server now." % port,
                    parent=self.root,
                )
        except OSError as exc:
            messagebox.showerror(
                "Cannot use this port",
                "%s:%d cannot be used: %s" % (host, port, exc),
                parent=self.root,
            )

    def _on_port_conflict(self, exc):
        """Ask whether to reclaim a busy port. Returns True if it was freed."""
        if exc.pid is None or not process_alive(exc.pid):
            self._port_advice(exc)
            return False

        looks_like_us = _looks_like_our_app(exc.process)
        if looks_like_us:
            question = (
                "Port %d is held by %s (PID %d).\n\n"
                "That looks like an earlier copy of this app that did not shut down "
                "properly.\n\nStop it and take the port back?"
            ) % (exc.port, exc.process, exc.pid)
        else:
            question = (
                "Port %d is held by %s (PID %d).\n\n"
                "This is not a program this app started. Stopping it will close whatever "
                "it was doing.\n\nStop it anyway?\n\n"
                "If you are not sure, choose No and set a different proxy port on the "
                "Configuration tab."
            ) % (exc.port, exc.process or "an unknown program", exc.pid)

        if not messagebox.askyesno("Port %d is busy" % exc.port, question, parent=self.root, default="no" if not looks_like_us else "yes"):
            log.warning("Leaving PID %d alone - port %d stays busy", exc.pid, exc.port)
            return False

        log.info("Stopping PID %d to free port %d", exc.pid, exc.port)
        if terminate_process(exc.pid):
            log.info("PID %d stopped, port %d should be free now", exc.pid, exc.port)
            return True
        log.error("Could not stop PID %d", exc.pid)
        messagebox.showerror(
            "Could not free the port",
            "PID %d would not stop. Try closing it from Task Manager, or pick a different "
            "proxy port on the Configuration tab." % exc.pid,
            parent=self.root,
        )
        return False

    def _port_advice(self, exc):
        log.error("%s", exc.describe())
        messagebox.showerror(
            "Port %d is busy" % exc.port,
            "%s\n\nThe program holding it could not be identified. Either close it, or set a "
            "different proxy port on the Configuration tab." % exc.describe(),
            parent=self.root,
        )

    # ---------------------------------------------------------------- shutdown
    def request_shutdown(self):
        if self._shutting_down:
            return
        if self.window is not None and not self.window.confirm_close():
            return
        self.shutdown()

    def shutdown(self):
        if self._shutting_down:
            return
        self._shutting_down = True
        log.info("Shutting down")

        # If anything wedges, the process still goes away - and with it the port.
        watchdog = threading.Timer(SHUTDOWN_WATCHDOG_SECONDS, self._force_exit)
        watchdog.daemon = True
        watchdog.start()

        try:
            if self.window is not None:
                self.window.show_busy("Closing connections...")
        except Exception:
            pass

        for service in (self.client, self.proxy):
            if service is None:
                continue
            try:
                service.stop()
            except Exception:
                log.exception("Stopping the %s failed", service.name)

        if self.config is not None:
            self.config.close()

        try:
            self.lock.release()
        except Exception:
            pass

        watchdog.cancel()
        log.info("Goodbye")
        logging.shutdown()

        try:
            if self.root is not None:
                self.root.destroy()
        except Exception:
            pass

    def _force_exit(self):
        # Last resort: never leave a half-dead process holding the proxy port.
        print("Shutdown timed out - forcing exit", file=sys.stderr)
        os._exit(1)

    def _install_signal_handlers(self):
        def handler(signum, _frame):
            log.info("Received signal %s - shutting down", signum)
            if self.root is not None:
                self.root.after(0, self.shutdown)
            else:
                self.shutdown()

        for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
            sig = getattr(signal, name, None)
            if sig is None:
                continue
            try:
                signal.signal(sig, handler)
            except (ValueError, OSError):
                pass  # not available on this platform / not the main thread

    def _on_tk_exception(self, exc_type, exc, traceback_obj):
        import traceback

        log.error(
            "Error while handling a window event:\n%s",
            "".join(traceback.format_exception(exc_type, exc, traceback_obj)).rstrip(),
        )


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="VeadotubeBridge",
        description="Proxy server, bridge client and configurator for syncing Veadotube avatars.",
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        help="config file to use (default: config.json next to the app)",
    )
    parser.add_argument("--debug", action="store_true", help="log everything, including library detail")
    parser.add_argument("--version", action="version", version="%s %s" % (APP_NAME, VERSION))
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    app = Application(config_path=args.config, debug=args.debug)
    try:
        return app.run()
    except Exception:
        logging.getLogger("veadobridge.app").exception("The app stopped because of an error")
        try:
            messagebox.showerror(
                "Unexpected error",
                "%s hit an error it could not recover from.\n\nThe details are in the log file."
                % APP_NAME,
            )
        except Exception:
            pass
        return 1
    finally:
        app.shutdown()
