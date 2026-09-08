"""One log for the whole app.

Every component logs through the standard `logging` module.  Records are fanned
out to a rotating file and to an in-memory queue that the GUI drains on the Tk
thread, so a single window shows the proxy, the client, the config manager and
any library warning in one stream.

Unhandled exceptions (main thread, worker threads, asyncio loops) are routed
here too - nothing is allowed to die silently into a closed console.
"""

import logging
import logging.handlers
import os
import queue
import sys
import threading
import traceback
import warnings

from .paths import log_dir

LOG_FILE_NAME = "veadobridge.log"
MAX_QUEUE = 5000

# Short tags shown in the GUI, keyed by the tail of the logger name.
_SOURCE_TAGS = {
    "app": "APP",
    "gui": "GUI",
    "proxy": "PROXY",
    "client": "CLIENT",
    "veado": "VEADO",
    "config": "CONFIG",
    "net": "NET",
    "nodes": "NODES",
    "singleton": "LOCK",
}

_gui_handler = None
_log_path = None


class GuiLogHandler(logging.Handler):
    """Buffers formatted records for the GUI to pick up on its own thread."""

    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records = queue.Queue(maxsize=MAX_QUEUE)
        self.dropped = 0
        self._lock = threading.Lock()

    def emit(self, record):
        try:
            entry = (
                record.created,
                source_tag(record.name),
                record.levelno,
                self.format(record),
            )
        except Exception:  # formatting must never take the app down
            return
        try:
            self.records.put_nowait(entry)
        except queue.Full:
            with self._lock:
                self.dropped += 1
            # Drop the oldest entry so the newest problem is always visible.
            try:
                self.records.get_nowait()
                self.records.put_nowait(entry)
            except (queue.Empty, queue.Full):
                pass

    def drain(self, limit=500):
        """Return up to `limit` buffered entries, oldest first."""
        out = []
        while len(out) < limit:
            try:
                out.append(self.records.get_nowait())
            except queue.Empty:
                break
        return out


def source_tag(logger_name):
    """Map a logger name onto the short tag shown in the log window."""
    if logger_name.startswith("veadobridge."):
        return _SOURCE_TAGS.get(logger_name.rsplit(".", 1)[-1], "APP")
    if logger_name in ("veadobridge", "root", "__main__"):
        return "APP"
    if logger_name.startswith("websockets"):
        return "WS"
    if logger_name.startswith("websocket"):
        return "WS"
    if logger_name.startswith("asyncio"):
        return "LOOP"
    return logger_name.split(".", 1)[0].upper()[:6]


def get_logger(name):
    """Logger for a component, e.g. get_logger('proxy')."""
    return logging.getLogger("veadobridge." + name)


def log_path():
    return _log_path


def gui_handler():
    return _gui_handler


def setup_logging(debug=False):
    """Install the file handler, the GUI handler and the exception hooks."""
    global _gui_handler, _log_path

    if _gui_handler is not None:
        return _gui_handler

    _log_path = os.path.join(log_dir(), LOG_FILE_NAME)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    file_fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s [%(name)s] %(message)s", "%Y-%m-%d %H:%M:%S"
    )
    try:
        file_handler = logging.handlers.RotatingFileHandler(
            _log_path, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(file_fmt)
        root.addHandler(file_handler)
    except OSError as exc:
        # A missing log file is not a reason to refuse to run.
        print("Could not open log file %s: %s" % (_log_path, exc), file=sys.stderr)
        _log_path = None

    _gui_handler = GuiLogHandler()
    _gui_handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(_gui_handler)

    # Library chatter is useful when debugging and noise otherwise.
    library_level = logging.DEBUG if debug else logging.WARNING
    for name in ("websockets", "websocket", "asyncio"):
        logging.getLogger(name).setLevel(library_level)

    logging.captureWarnings(True)
    warnings.simplefilter("default")

    _install_exception_hooks()
    return _gui_handler


def _install_exception_hooks():
    log = get_logger("app")

    def excepthook(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            return
        log.critical(
            "Unhandled exception:\n%s",
            "".join(traceback.format_exception(exc_type, exc, tb)).rstrip(),
        )

    sys.excepthook = excepthook

    def thread_excepthook(args):
        if issubclass(args.exc_type, SystemExit):
            return
        log.error(
            "Unhandled exception in thread %s:\n%s",
            args.thread.name if args.thread else "?",
            "".join(
                traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)
            ).rstrip(),
        )

    threading.excepthook = thread_excepthook


def asyncio_exception_handler(loop, context):
    """Loop-level handler so background task failures reach the same log."""
    log = get_logger("app")
    exc = context.get("exception")
    message = context.get("message", "unhandled asyncio error")
    if exc is not None:
        log.error(
            "asyncio: %s\n%s",
            message,
            "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            ).rstrip(),
        )
    else:
        log.error("asyncio: %s", message)
