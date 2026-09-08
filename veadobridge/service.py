"""Shared plumbing for the background services (proxy and client).

Each service owns one thread running one asyncio loop.  Starting and stopping
is synchronous from the caller's point of view, and stopping always ends with
the loop closed and `_cleanup()` run - that is what keeps sockets, ports and
threads from outliving the thing that created them.
"""

import asyncio
import threading

from .logbus import asyncio_exception_handler, get_logger

STOPPED = "stopped"
STARTING = "starting"
RUNNING = "running"
STOPPING = "stopping"
FAILED = "failed"


class ServiceState:
    """Immutable snapshot handed to the GUI."""

    __slots__ = ("phase", "detail", "stats")

    def __init__(self, phase=STOPPED, detail="", stats=None):
        self.phase = phase
        self.detail = detail
        self.stats = stats or {}

    def __repr__(self):
        return "ServiceState(%s, %r)" % (self.phase, self.detail)


class AsyncService:
    """Base class: one dedicated thread, one event loop, deterministic teardown."""

    name = "service"

    def __init__(self, config, on_state=None):
        self.config = config
        self.log = get_logger(self.name)
        self._on_state = on_state
        self._thread = None
        self._loop = None
        self._loop_ready = threading.Event()
        self._stop_requested = threading.Event()
        self._stop_event = None
        self._state = ServiceState()
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ state
    @property
    def state(self):
        return self._state

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def set_state(self, phase, detail="", **stats):
        self._state = ServiceState(phase, detail, stats)
        if self._on_state:
            try:
                self._on_state(self.name, self._state)
            except Exception:
                self.log.exception("State callback failed")

    # ---------------------------------------------------------------- control
    def start(self):
        """Start the service. Returns True on success, False if it could not start.

        Startup problems are logged and reported through the state callback
        rather than raised, so one broken service never takes the app down.
        """
        with self._lock:
            if self.running:
                self.log.debug("Already running - start ignored")
                return True

            self._stop_requested.clear()
            self._loop_ready.clear()
            self.set_state(STARTING, "starting...")

            try:
                self._prepare()
            except Exception as exc:
                self._cleanup_safely()
                self._report_start_failure(exc)
                return False

            self._thread = threading.Thread(
                target=self._thread_main, name="%s-loop" % self.name, daemon=True
            )
            self._thread.start()
            # Wait briefly so the caller sees a real state, not "starting".
            self._loop_ready.wait(timeout=5.0)
            return self.running

    def stop(self, timeout=8.0):
        """Ask the service to stop and wait for the thread to finish."""
        with self._lock:
            thread = self._thread
            if thread is None or not thread.is_alive():
                self._cleanup_safely()
                self.set_state(STOPPED, "stopped")
                self._thread = None
                return True

            self.set_state(STOPPING, "stopping...")
            self._stop_requested.set()
            loop, event = self._loop, self._stop_event
            if loop is not None and event is not None and not loop.is_closed():
                try:
                    loop.call_soon_threadsafe(event.set)
                except RuntimeError:
                    pass

        thread.join(timeout=timeout)
        stopped = not thread.is_alive()
        with self._lock:
            if stopped:
                self._thread = None
                self.set_state(STOPPED, "stopped")
            else:
                # Daemon thread: it cannot keep the process alive, but say so.
                self.log.error(
                    "%s did not shut down within %.1fs - it will be abandoned", self.name, timeout
                )
                self.set_state(FAILED, "did not shut down cleanly")
            self._cleanup_safely()
        return stopped

    def restart(self):
        self.stop()
        return self.start()

    # ------------------------------------------------------------- subclassing
    def _prepare(self):
        """Synchronous pre-flight on the caller's thread. May raise."""

    async def _run(self, stop_event):
        """The service body. Must return when `stop_event` is set."""
        raise NotImplementedError

    def _cleanup(self):
        """Synchronous teardown. Always called, must not raise."""

    def _report_start_failure(self, exc):
        self.log.error("Could not start the %s: %s", self.name, exc)
        self.set_state(FAILED, str(exc))

    # ---------------------------------------------------------------- internal
    def _cleanup_safely(self):
        try:
            self._cleanup()
        except Exception:
            self.log.exception("Cleanup failed")

    def _thread_main(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.set_exception_handler(asyncio_exception_handler)
        self._loop = loop
        self._stop_event = asyncio.Event()
        if self._stop_requested.is_set():
            self._stop_event.set()
        self._loop_ready.set()

        try:
            loop.run_until_complete(self._run(self._stop_event))
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self.log.exception("%s stopped because of an error", self.name)
            self.set_state(FAILED, str(exc))
        finally:
            try:
                _drain_loop(loop)
            finally:
                asyncio.set_event_loop(None)
                loop.close()
                self._loop = None
                self._stop_event = None
                self._cleanup_safely()
                if self._state.phase not in (FAILED, STOPPING):
                    self.set_state(STOPPED, "stopped")
                self.log.debug("%s loop closed", self.name)


def _drain_loop(loop):
    """Cancel whatever is left and let the loop settle before closing it."""
    pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
    for task in pending:
        task.cancel()
    if pending:
        try:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        except Exception:
            pass
    try:
        loop.run_until_complete(loop.shutdown_asyncgens())
    except Exception:
        pass
