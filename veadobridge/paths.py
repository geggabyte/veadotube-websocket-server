"""Filesystem locations used by the app.

The app is portable first: if the folder holding the executable (or the source
tree) is writable, the config and logs live right next to it.  If it is not
writable (Program Files, a read-only share, ...) everything falls back to the
per-user application data folder, so the app never fails to start just because
it was dropped somewhere awkward.
"""

import os
import sys
import tempfile

from . import APP_SLUG

_CONFIG_NAME = "config.json"
_LOG_DIR_NAME = "logs"
_RUNTIME_DIR_NAME = "runtime"


def is_frozen():
    """True when running from a PyInstaller-built executable."""
    return getattr(sys, "frozen", False)


def app_dir():
    """Folder that holds the executable (frozen) or the project root (source)."""
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def user_data_dir():
    """Per-user writable folder, used when the app folder is read-only."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.join(
            os.path.expanduser("~"), ".local", "share"
        )
    return os.path.join(base, APP_SLUG)


def _is_writable(directory):
    try:
        os.makedirs(directory, exist_ok=True)
        with tempfile.TemporaryFile(dir=directory):
            pass
        return True
    except OSError:
        return False


_data_dir_cache = None


def data_dir():
    """Writable base folder for config, logs and runtime state."""
    global _data_dir_cache
    if _data_dir_cache is None:
        candidate = app_dir()
        if not _is_writable(candidate):
            candidate = user_data_dir()
            os.makedirs(candidate, exist_ok=True)
        _data_dir_cache = candidate
    return _data_dir_cache


def default_config_path():
    """Config file next to the app, or in the user data folder as a fallback."""
    portable = os.path.join(app_dir(), _CONFIG_NAME)
    if os.path.exists(portable):
        return portable
    return os.path.join(data_dir(), _CONFIG_NAME)


def log_dir():
    path = os.path.join(data_dir(), _LOG_DIR_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def runtime_dir():
    """Folder for instance lock files. Cleared of stale entries on startup."""
    path = os.path.join(data_dir(), _RUNTIME_DIR_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def open_in_file_manager(path):
    """Reveal a folder in the OS file manager. Best effort, never raises."""
    try:
        if sys.platform == "win32":
            os.startfile(path)  # noqa: S606 - intentional shell-less open
        elif sys.platform == "darwin":
            import subprocess

            subprocess.Popen(["open", path])
        else:
            import subprocess

            subprocess.Popen(["xdg-open", path])
        return True
    except Exception:
        return False
