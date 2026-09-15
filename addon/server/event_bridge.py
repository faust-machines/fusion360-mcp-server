"""
EventBridge — dispatches work from socket threads to Fusion's main thread.

Uses adsk.core.CustomEvent + a thread-safe queue so all Fusion API calls
happen on the main thread.  A 200 ms backup timer fires the custom event
periodically in case fireCustomEvent from a daemon thread is unreliable.
"""

import queue
import threading
import time
import traceback

import adsk.core

from . import get_logger
from .hints import classify

log = get_logger("bridge")

CUSTOM_EVENT_ID = "Fusion360MCP_BridgeEvent"
TIMER_INTERVAL_MS = 200  # backup polling interval


class WorkItem:
    """One unit of work submitted from a socket thread."""

    __slots__ = ("command", "result", "error", "done", "cancelled")

    def __init__(self, command: dict):
        self.command = command
        self.result = None
        self.error = None
        self.done = threading.Event()
        # Set when the waiting socket thread gave up (timeout).  A
        # cancelled item is skipped by drain_queue so a timed-out command
        # never executes late — the client has already reported failure.
        self.cancelled = False


class _MainThreadHandler(adsk.core.CustomEventHandler):
    """Attached to the CustomEvent; runs on Fusion's main thread."""

    def __init__(self, bridge: "EventBridge"):
        super().__init__()
        self._bridge = bridge

    def notify(self, args):  # called on main thread
        self._bridge.drain_queue()


class EventBridge:
    """
    Bridge between daemon socket threads and Fusion's main thread.

    Socket thread calls ``submit(command)`` which blocks (up to *timeout*
    seconds) until the main thread has executed the command and stored the
    result.
    """

    def __init__(self, app: adsk.core.Application, command_handler):
        self._app = app
        self._handler = command_handler
        self._queue: queue.Queue = queue.Queue()

        # Register a custom event on the main thread
        self._event = app.registerCustomEvent(CUSTOM_EVENT_ID)
        self._event_handler = _MainThreadHandler(self)
        self._event.add(self._event_handler)

        # Backup timer thread — fires the custom event every 200 ms
        self._timer_running = True
        self._timer_thread = threading.Thread(target=self._timer_loop, daemon=True)
        self._timer_thread.start()

        log.info("EventBridge initialised (custom event: %s)", CUSTOM_EVENT_ID)

    # ------------------------------------------------------------------
    # Called from socket (daemon) threads
    # ------------------------------------------------------------------

    def submit(self, command: dict, timeout: float = 30.0) -> dict:
        """Queue *command* for main-thread execution; block until done."""
        cmd_type = command.get("type", "?")

        # Fast-path: ping never touches Fusion API — answer immediately
        if cmd_type == "ping":
            log.debug("ping (fast path)")
            return {"status": "success", "result": {"ok": True, "status": "pong"}}

        log.debug("submit cmd=%s", cmd_type)
        item = WorkItem(command)
        self._queue.put(item)

        # Poke the main thread
        try:
            self._app.fireCustomEvent(CUSTOM_EVENT_ID)
        except Exception:
            pass  # timer will pick it up

        if not item.done.wait(timeout=timeout):
            # The client has given up.  Cancel the item so drain_queue
            # skips it instead of executing it late — a late execution
            # plus a client retry would apply mutations twice.
            item.cancelled = True
            log.warning("Command %s timed out after %ss (cancelled)", cmd_type, timeout)
            return {
                "status": "error",
                "error_kind": "TIMEOUT",
                "message": f"Command timed out after {timeout}s",
            }

        if item.error is not None:
            log.error("Command %s failed: %s", cmd_type, item.error)
            kind, hints = classify(item.error)
            return {
                "status": "error",
                "error_kind": kind,
                "hints": hints,
                "message": item.error,
            }

        log.debug("Command %s completed", cmd_type)
        return item.result

    # ------------------------------------------------------------------
    # Called on Fusion's main thread (from CustomEventHandler.notify)
    # ------------------------------------------------------------------

    def drain_queue(self):
        """Execute every queued work item (main thread only)."""
        while True:
            try:
                item: WorkItem = self._queue.get_nowait()
            except queue.Empty:
                break

            cmd_type = item.command.get("type", "?")
            if item.cancelled:
                log.info("Skipping cancelled command %s", cmd_type)
                continue
            try:
                if cmd_type == "reload_handler":
                    # Must run on the main thread — CommandHandler's
                    # constructor touches the Fusion API.
                    self.reload_handler()
                    item.result = {
                        "status": "success",
                        "result": {"ok": True, "reloaded": True},
                    }
                else:
                    item.result = self._handler.execute_command(item.command)
            except Exception as exc:
                item.error = f"{exc}\n{traceback.format_exc()}"
                log.error("Main-thread exec of %s raised: %s", cmd_type, exc)
            finally:
                item.done.set()

    # ------------------------------------------------------------------
    # Backup timer
    # ------------------------------------------------------------------

    def _timer_loop(self):
        interval = TIMER_INTERVAL_MS / 1000.0
        while self._timer_running:
            time.sleep(interval)
            if not self._queue.empty():
                try:
                    self._app.fireCustomEvent(CUSTOM_EVENT_ID)
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Hot reload
    # ------------------------------------------------------------------

    def reload_handler(self):
        """Reimport handler modules and replace the active handler instance.

        Called on the main thread (from drain_queue).  Sibling modules
        are reloaded first, in dependency order, so command_handler picks
        up their changes too.
        """
        import importlib

        from . import command_handler as ch_mod
        from . import hints as hints_mod
        from . import hole_geometry as hole_mod
        from . import parameter_units as units_mod

        for mod in (hints_mod, hole_mod, units_mod, ch_mod):
            importlib.reload(mod)
        self._handler = ch_mod.CommandHandler()
        # Reset lazy dispatch table so it picks up new commands
        self._handler.__class__._COMMANDS = None
        log.info("CommandHandler reloaded")

    # ------------------------------------------------------------------
    # Teardown
    # ------------------------------------------------------------------

    def stop(self):
        self._timer_running = False
        # Release any queued items so blocked socket threads wake up
        # immediately instead of waiting out their full timeout against
        # a dead bridge.
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            item.cancelled = True
            item.error = "Add-in stopped before the command executed"
            item.done.set()
        try:
            self._event.remove(self._event_handler)
        except Exception:
            pass
        try:
            self._app.unregisterCustomEvent(CUSTOM_EVENT_ID)
        except Exception:
            pass
        log.info("EventBridge stopped")
