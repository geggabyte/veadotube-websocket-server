"""Port binding and process inspection.

Everything here exists to answer one question well: "the port is busy - who has
it, and can we get it back?".  On Windows the listening socket is released the
moment the owning process dies, so a busy port always means a live process, and
naming it is what turns a cryptic WinError 10048 into something actionable.
"""

import socket
import subprocess
import sys

from .logbus import get_logger

log = get_logger("net")

IS_WINDOWS = sys.platform == "win32"
_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0  # CREATE_NO_WINDOW


class PortInUse(Exception):
    """Raised when a listening socket cannot be bound because the port is taken."""

    def __init__(self, host, port, pid=None, process=None, original=None):
        self.host = host
        self.port = port
        self.pid = pid
        self.process = process
        self.original = original
        super().__init__(self.describe())

    def describe(self):
        who = ""
        if self.pid:
            who = " It is held by %s (PID %d)." % (self.process or "an unknown program", self.pid)
        return "Port %d on %s is already in use.%s" % (self.port, self.host, who)


# --------------------------------------------------------------------- binding
def bind_listen_socket(host, port, backlog=128):
    """Create, bind and listen on a TCP socket, or raise PortInUse/OSError.

    Binding here rather than inside the event loop means a busy port is reported
    on the caller's thread, before any async machinery is spun up, and the
    socket is closed deterministically if anything goes wrong.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if IS_WINDOWS:
            # SO_EXCLUSIVEADDRUSE stops any other process from stealing the port
            # from under us. Deliberately no SO_REUSEADDR: on Windows it allows
            # two processes to bind the same port and silently split traffic.
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            except (AttributeError, OSError):
                pass
        else:
            # On POSIX SO_REUSEADDR only skips the TIME_WAIT wait, which is
            # exactly what we want after a restart.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
        sock.listen(backlog)
        sock.setblocking(False)
        return sock
    except OSError as exc:
        sock.close()
        if exc.errno in (98, 10048, 48) or getattr(exc, "winerror", None) == 10048:
            pid = find_listener_pid(port)
            raise PortInUse(host, port, pid, process_name(pid) if pid else None, exc) from exc
        raise


def port_is_free(host, port):
    """Cheap check without keeping the socket. True when the port can be bound."""
    try:
        sock = bind_listen_socket(host, port, backlog=1)
    except (PortInUse, OSError):
        return False
    sock.close()
    return True


# ------------------------------------------------------------------- processes
def find_listener_pid(port):
    """PID of the process listening on `port`, or None if it cannot be found."""
    try:
        if IS_WINDOWS:
            return _find_listener_pid_windows(port)
        return _find_listener_pid_posix(port)
    except Exception as exc:
        log.debug("Could not identify the owner of port %d: %s", port, exc)
        return None


def _run(args):
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=8,
        creationflags=_NO_WINDOW,
    )


def _find_listener_pid_windows(port):
    result = _run(["netstat", "-a", "-n", "-o", "-p", "TCP"])
    if result.returncode != 0:
        return None
    suffix = ":%d" % port
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() != "TCP":
            continue
        local, state, pid = parts[1], parts[3], parts[4]
        if state.upper() != "LISTENING" or not local.endswith(suffix):
            continue
        try:
            return int(pid)
        except ValueError:
            continue
    return None


def _find_listener_pid_posix(port):
    for args in (["lsof", "-t", "-i", "tcp:%d" % port, "-sTCP:LISTEN"], ["ss", "-lptnH"]):
        try:
            result = _run(args)
        except (OSError, subprocess.SubprocessError):
            continue
        if result.returncode != 0:
            continue
        if args[0] == "lsof":
            for line in result.stdout.split():
                if line.strip().isdigit():
                    return int(line.strip())
        else:
            for line in result.stdout.splitlines():
                if ":%d " % port not in line + " ":
                    continue
                marker = "pid="
                if marker in line:
                    tail = line.split(marker, 1)[1]
                    digits = ""
                    for char in tail:
                        if char.isdigit():
                            digits += char
                        else:
                            break
                    if digits:
                        return int(digits)
    return None


def process_alive(pid):
    """True when a process with this PID currently exists."""
    if not pid or pid <= 0:
        return False
    if IS_WINDOWS:
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    else:
        import errno
        import os

        try:
            os.kill(pid, 0)
        except OSError as exc:
            return exc.errno == errno.EPERM
        return True


def process_name(pid):
    """Executable name for a PID, or None."""
    if not pid or pid <= 0:
        return None
    try:
        if IS_WINDOWS:
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return None
            try:
                size = ctypes.c_ulong(1024)
                buffer = ctypes.create_unicode_buffer(size.value)
                if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                    import os

                    return os.path.basename(buffer.value)
            finally:
                kernel32.CloseHandle(handle)
            return None
        with open("/proc/%d/comm" % pid, "r", encoding="utf-8") as handle:
            return handle.read().strip()
    except Exception as exc:
        log.debug("Could not read the name of PID %s: %s", pid, exc)
        return None


def terminate_process(pid, timeout=5.0):
    """Force-stop a process. Returns True once it is gone."""
    import time

    if not process_alive(pid):
        return True
    try:
        if IS_WINDOWS:
            import ctypes

            PROCESS_TERMINATE = 0x0001
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
            if not handle:
                log.warning("No permission to stop PID %d", pid)
                return False
            try:
                kernel32.TerminateProcess(handle, 1)
            finally:
                kernel32.CloseHandle(handle)
        else:
            import os
            import signal

            os.kill(pid, signal.SIGTERM)
    except Exception as exc:
        log.warning("Could not stop PID %s: %s", pid, exc)
        return False

    deadline = time.time() + timeout
    while time.time() < deadline:
        if not process_alive(pid):
            return True
        time.sleep(0.15)

    if not IS_WINDOWS:
        try:
            import os
            import signal

            os.kill(pid, signal.SIGKILL)
            time.sleep(0.3)
        except Exception:
            pass
    return not process_alive(pid)
