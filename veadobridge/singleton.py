"""Single-instance guard, keyed to the config file in use.

Two copies of the app pointed at the same config would fight over the same
proxy port, so only one is allowed.  Two copies with *different* config files
(for example two local Veadotube instances) are perfectly fine and stay
allowed.

The lock itself is an OS-level file lock, not a PID file: if the app is killed
or crashes, the kernel drops the lock immediately, so the next launch is never
blocked by a leftover file.  Owner details are written to a separate, unlocked
sidecar file so the running instance can still be named to the user.
"""

import hashlib
import json
import os
import sys
import time

from .logbus import get_logger
from .netutil import process_alive, process_name
from .paths import runtime_dir

log = get_logger("singleton")

IS_WINDOWS = sys.platform == "win32"


class AlreadyRunning(Exception):
    def __init__(self, pid=None, process=None, config_path=None):
        self.pid = pid
        self.process = process
        self.config_path = config_path
        who = ""
        if pid:
            who = " (%s, PID %d)" % (process or "unknown program", pid)
        super().__init__(
            "Another copy of the app%s is already using %s."
            % (who, config_path or "this configuration")
        )


class InstanceLock:
    """Hold with `acquire()`, release with `release()`. Safe to release twice."""

    def __init__(self, config_path):
        self.config_path = os.path.abspath(config_path)
        digest = hashlib.sha1(self.config_path.lower().encode("utf-8")).hexdigest()[:12]
        base = os.path.join(runtime_dir(), "instance-%s" % digest)
        self.path = base + ".lock"
        self.info_path = base + ".json"
        self._handle = None

    # ------------------------------------------------------------------ public
    def acquire(self):
        """Take the lock, or raise AlreadyRunning."""
        handle = open(self.path, "a+", encoding="utf-8")
        try:
            self._lock_file(handle)
        except OSError as exc:
            handle.close()
            info = self._read_info()
            raise AlreadyRunning(
                info.get("pid"), info.get("process"), self.config_path
            ) from exc

        self._handle = handle
        self._write_info()
        log.debug("Instance lock held at %s", self.path)
        return self

    def release(self):
        if self._handle is None:
            return
        handle, self._handle = self._handle, None
        try:
            self._unlock_file(handle)
        except OSError:
            pass
        try:
            handle.close()
        except OSError:
            pass
        for path in (self.info_path, self.path):
            try:
                os.unlink(path)
            except OSError:
                # Another instance may already have taken the file over.
                pass
        log.debug("Instance lock released")

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *exc_info):
        self.release()
        return False

    # ---------------------------------------------------------------- internal
    def _write_info(self):
        try:
            with open(self.info_path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "pid": os.getpid(),
                        "process": os.path.basename(sys.executable),
                        "config": self.config_path,
                        "started": time.time(),
                    },
                    handle,
                )
        except OSError as exc:
            log.debug("Could not write lock details: %s", exc)

    def _read_info(self):
        try:
            with open(self.info_path, "r", encoding="utf-8") as handle:
                data = json.loads(handle.read() or "{}")
        except (OSError, ValueError):
            return {}
        pid = data.get("pid")
        if isinstance(pid, int) and process_alive(pid):
            data["process"] = process_name(pid) or data.get("process")
            return data
        return {}

    @staticmethod
    def _lock_file(handle):
        if IS_WINDOWS:
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _unlock_file(handle):
        if IS_WINDOWS:
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def stale_lock_sweep():
    """Delete lock files whose owner is gone. Housekeeping, not correctness."""
    directory = runtime_dir()
    removed = 0
    try:
        entries = os.listdir(directory)
    except OSError:
        return 0
    for name in entries:
        if not name.startswith("instance-") or not name.endswith(".json"):
            continue
        info_path = os.path.join(directory, name)
        try:
            with open(info_path, "r", encoding="utf-8") as handle:
                data = json.loads(handle.read() or "{}")
        except (OSError, ValueError):
            data = {}
        pid = data.get("pid")
        if isinstance(pid, int) and process_alive(pid):
            continue
        for path in (info_path, info_path[: -len(".json")] + ".lock"):
            try:
                os.unlink(path)
                removed += 1
            except OSError:
                pass
    if removed:
        log.debug("Cleaned up %d stale lock file(s)", removed)
    return removed
