"""Configuration loading, validation, atomic saving and file watching."""

import copy
import json
import os
import tempfile
import threading
import time

from .logbus import get_logger

log = get_logger("config")

DEFAULTS = {
    "client_id": "veadobridge",
    "proxy_url": "ws://localhost:8765",
    "proxy_host": "0.0.0.0",
    "proxy_port": 8765,
    "veado_host": "127.0.0.1",
    "veado_port": 2424,
    # Off by default: a fresh install should sit still until it has been
    # configured, rather than binding a port and dialling out on first launch.
    "run_proxy": False,
    "run_client": False,
    "reconnect_delay": 3.0,
    "send_rate": 60,
    "log_level": "INFO",
    "listen_map": [],
    "send_map": [],
}

# Changing any of these means the running service has to be rebuilt.
PROXY_KEYS = ("proxy_host", "proxy_port")
CLIENT_KEYS = ("client_id", "proxy_url", "veado_host", "veado_port", "send_rate", "reconnect_delay")


class ConfigError(Exception):
    """Raised when a config file cannot be read or is not usable."""


def _as_int(value, default, low, high, name, problems):
    try:
        number = int(value)
    except (TypeError, ValueError):
        problems.append("%s: %r is not a whole number, using %r" % (name, value, default))
        return default
    if not low <= number <= high:
        problems.append("%s: %d is out of range %d-%d, using %r" % (name, number, low, high, default))
        return default
    return number


def _as_float(value, default, low, high, name, problems):
    try:
        number = float(value)
    except (TypeError, ValueError):
        problems.append("%s: %r is not a number, using %r" % (name, value, default))
        return default
    if not low <= number <= high:
        problems.append("%s: %s is out of range %s-%s, using %r" % (name, number, low, high, default))
        return default
    return number


def _as_bool(value, default, name, problems):
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false", "1", "0", "yes", "no"):
        return value.strip().lower() in ("true", "1", "yes")
    problems.append("%s: %r is not true/false, using %r" % (name, value, default))
    return default


def _as_node(value, name, problems):
    """Normalise a node reference to {'type': str, 'id': str}."""
    if isinstance(value, str) and ":" in value:
        node_type, node_id = value.split(":", 1)
        value = {"type": node_type, "id": node_id}
    if not isinstance(value, dict):
        problems.append("%s: %r is not a node mapping, entry dropped" % (name, value))
        return None
    node_type = str(value.get("type", "")).strip()
    node_id = str(value.get("id", "")).strip()
    if not node_type or not node_id:
        problems.append("%s: node needs both a type and an id, entry dropped" % name)
        return None
    return {"type": node_type, "id": node_id}


def validate(raw):
    """Return (config, problems). Never raises: bad values fall back to defaults."""
    problems = []
    if not isinstance(raw, dict):
        problems.append("config root is not an object, using defaults")
        raw = {}

    cfg = copy.deepcopy(DEFAULTS)

    cfg["client_id"] = str(raw.get("client_id", DEFAULTS["client_id"])).strip() or DEFAULTS["client_id"]
    cfg["proxy_url"] = str(raw.get("proxy_url", DEFAULTS["proxy_url"])).strip() or DEFAULTS["proxy_url"]
    if not cfg["proxy_url"].startswith(("ws://", "wss://")):
        problems.append("proxy_url: %r has no ws:// or wss:// scheme, assuming ws://" % cfg["proxy_url"])
        cfg["proxy_url"] = "ws://" + cfg["proxy_url"].lstrip("/")

    cfg["proxy_host"] = str(raw.get("proxy_host", DEFAULTS["proxy_host"])).strip() or DEFAULTS["proxy_host"]
    cfg["proxy_port"] = _as_int(raw.get("proxy_port", DEFAULTS["proxy_port"]), DEFAULTS["proxy_port"], 1, 65535, "proxy_port", problems)
    cfg["veado_host"] = str(raw.get("veado_host", DEFAULTS["veado_host"])).strip() or DEFAULTS["veado_host"]
    cfg["veado_port"] = _as_int(raw.get("veado_port", DEFAULTS["veado_port"]), DEFAULTS["veado_port"], 1, 65535, "veado_port", problems)
    cfg["run_proxy"] = _as_bool(raw.get("run_proxy", DEFAULTS["run_proxy"]), DEFAULTS["run_proxy"], "run_proxy", problems)
    cfg["run_client"] = _as_bool(raw.get("run_client", DEFAULTS["run_client"]), DEFAULTS["run_client"], "run_client", problems)
    cfg["reconnect_delay"] = _as_float(raw.get("reconnect_delay", DEFAULTS["reconnect_delay"]), DEFAULTS["reconnect_delay"], 0.5, 60.0, "reconnect_delay", problems)
    cfg["send_rate"] = _as_int(raw.get("send_rate", DEFAULTS["send_rate"]), DEFAULTS["send_rate"], 1, 240, "send_rate", problems)

    level = str(raw.get("log_level", DEFAULTS["log_level"])).strip().upper()
    if level not in ("DEBUG", "INFO", "WARNING", "ERROR"):
        problems.append("log_level: %r is not DEBUG/INFO/WARNING/ERROR, using INFO" % level)
        level = "INFO"
    cfg["log_level"] = level

    listen_map = []
    raw_listen = raw.get("listen_map", raw.get("listen_mappings", []))
    if not isinstance(raw_listen, list):
        problems.append("listen_map is not a list, using an empty one")
        raw_listen = []
    for item in raw_listen:
        node = _as_node(item, "listen_map", problems)
        if node and node not in listen_map:
            listen_map.append(node)
    cfg["listen_map"] = listen_map

    send_map = []
    raw_send = raw.get("send_map", raw.get("send_mappings", []))
    if not isinstance(raw_send, list):
        problems.append("send_map is not a list, using an empty one")
        raw_send = []
    for item in raw_send:
        if not isinstance(item, dict):
            problems.append("send_map: %r is not a mapping, entry dropped" % (item,))
            continue
        source = _as_node(item.get("from"), "send_map.from", problems)
        target = _as_node(item.get("to"), "send_map.to", problems)
        if source and target:
            send_map.append({"from": source, "to": target})
    cfg["send_map"] = send_map

    return cfg, problems


class ConfigManager:
    """Thread-safe config store with change notification and file watching.

    Callbacks receive the new config dict and are invoked from the watcher
    thread (or from the thread that called `save`), never while holding the
    internal lock.
    """

    def __init__(self, path, watch=True):
        self.path = os.path.abspath(path)
        self._lock = threading.RLock()
        self._data = copy.deepcopy(DEFAULTS)
        self._callbacks = []
        self._last_signature = None
        self._stop = threading.Event()
        self._watcher = None
        self._suppress_watch_until = 0.0

        self.load(notify=False)

        if watch:
            self._watcher = threading.Thread(target=self._watch, name="config-watch", daemon=True)
            self._watcher.start()

    # ------------------------------------------------------------------ read
    def get(self):
        with self._lock:
            return copy.deepcopy(self._data)

    def read(self, name, default=None):
        with self._lock:
            return copy.deepcopy(self._data.get(name, default))

    def register_callback(self, callback):
        with self._lock:
            self._callbacks.append(callback)

    # ----------------------------------------------------------------- write
    def load(self, notify=True):
        """(Re)read the config file. Missing or broken files fall back to defaults."""
        raw = {}
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as handle:
                    raw = json.load(handle)
            except (OSError, ValueError) as exc:
                log.error("Cannot read %s (%s). Keeping the values already loaded.", self.path, exc)
                return self.get()
        else:
            log.info("No config at %s yet - creating one with default values.", self.path)

        cfg, problems = validate(raw)
        for problem in problems:
            log.warning("config: %s", problem)

        if not os.path.exists(self.path):
            try:
                self._write_file(cfg)
            except OSError as exc:
                log.error("Could not create the default config: %s", exc)

        return self._apply(cfg, notify=notify)

    def save(self, cfg, notify=True):
        """Validate and atomically write a new config. Returns the stored config."""
        clean, problems = validate(cfg)
        for problem in problems:
            log.warning("config: %s", problem)
        self._write_file(clean)
        log.info("Configuration saved to %s", self.path)
        return self._apply(clean, notify=notify)

    def update(self, **changes):
        """Merge a handful of keys into the config and save."""
        cfg = self.get()
        cfg.update(changes)
        return self.save(cfg)

    def close(self):
        self._stop.set()

    # --------------------------------------------------------------- internal
    def _write_file(self, cfg):
        directory = os.path.dirname(self.path) or "."
        os.makedirs(directory, exist_ok=True)
        # Write to a temp file in the same folder, then replace: a crash during
        # the write can never leave a half-written config behind.
        handle = tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=directory, prefix=".config-", suffix=".tmp", delete=False
        )
        temp_name = handle.name
        try:
            with handle:
                json.dump(cfg, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
        except BaseException:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise
        # Our own write must not bounce back through the watcher.
        self._suppress_watch_until = time.time() + 1.5

    def _apply(self, cfg, notify=True):
        signature = json.dumps(cfg, sort_keys=True)
        with self._lock:
            changed = signature != self._last_signature
            self._data = cfg
            self._last_signature = signature
            callbacks = list(self._callbacks)

        if changed and notify:
            for callback in callbacks:
                try:
                    callback(copy.deepcopy(cfg))
                except Exception:
                    log.exception("A config change handler failed")
        return copy.deepcopy(cfg)

    def _watch(self):
        last_mtime = None
        while not self._stop.wait(1.0):
            try:
                if time.time() < self._suppress_watch_until:
                    last_mtime = os.path.getmtime(self.path)
                    continue
                mtime = os.path.getmtime(self.path)
                if last_mtime is None:
                    last_mtime = mtime
                    continue
                if mtime != last_mtime:
                    last_mtime = mtime
                    log.info("config.json changed on disk - reloading")
                    self.load()
            except FileNotFoundError:
                if last_mtime is not None:
                    log.warning("config.json disappeared - keeping the values in memory")
                    last_mtime = None
            except OSError as exc:
                log.warning("Could not check the config file: %s", exc)
