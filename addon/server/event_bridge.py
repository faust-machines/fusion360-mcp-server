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
import uuid

import adsk.core

from . import get_logger
from .hints import classify

log = get_logger("bridge")

CUSTOM_EVENT_ID = "Fusion360MCP_BridgeEvent"
TIMER_INTERVAL_MS = 200  # backup polling interval


class WorkItem:
    """One unit of work submitted from a socket thread."""

    __slots__ = (
        "command",
        "result",
        "error",
        "done",
        "cancelled",
        "operation_id",
        "state",
        "finished_at",
    )

    def __init__(self, command: dict):
        self.command = command
        self.result = None
        self.error = None
        self.done = threading.Event()
        # Only work that has not started can be cancelled on timeout.
        # Running work stays tracked so the caller can recover its result.
        self.cancelled = False
        self.operation_id = command.get("operation_id") or uuid.uuid4().hex
        self.state = "queued"
        self.finished_at = None


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
        self._lock = threading.Lock()
        self._operations = {}
        self._draining = False

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
        if cmd_type == "get_operation_status":
            return self.get_operation_status(
                command.get("params", {}).get("operation_id")
            )

        if cmd_type == "ping" and not command.get("params", {}).get(
            "main_thread", False
        ):
            log.debug("ping (fast path)")
            return {
                "status": "success",
                "result": {
                    "ok": True,
                    "status": "pong",
                    "scope": "transport",
                    "main_thread_checked": False,
                },
            }

        log.debug("submit cmd=%s", cmd_type)
        operation_id = command.get("operation_id")
        if operation_id is not None and (
            not isinstance(operation_id, str)
            or not operation_id
            or len(operation_id) > 128
        ):
            return {
                "status": "error",
                "error_kind": "INVALID_INPUT",
                "message": (
                    "operation_id must be a nonempty string of at most 128 characters"
                ),
            }
        item = WorkItem(command)
        with self._lock:
            self._prune_operations()
            if item.operation_id in self._operations:
                return {
                    "status": "error",
                    "error_kind": "INVALID_INPUT",
                    "message": "operation_id already exists; poll get_operation_status",
                }
            self._operations[item.operation_id] = item
        self._queue.put(item)

        # Poke the main thread
        try:
            self._app.fireCustomEvent(CUSTOM_EVENT_ID)
        except Exception:
            pass  # timer will pick it up

        if not item.done.wait(timeout=timeout):
            with self._lock:
                # Starting and cancellation share a lock: a cancelled queued
                # mutation can never start after we report it as cancelled.
                if item.state == "queued":
                    item.cancelled = True
                    item.state = "cancelled"
                    item.finished_at = time.monotonic()
                    item.done.set()
                state = item.state
            if state not in ("completed", "failed"):
                running = state == "running"
                message = (
                    f"Command timed out after {timeout}s; operation "
                    f"{item.operation_id} is {state}. "
                    + (
                        "Fusion may still be executing; interruption is unsafe. "
                        if running
                        else "Cancelled before execution. "
                    )
                    + "Poll get_operation_status using this ID; do not resubmit."
                )
                log.warning(message)
                return {
                    "status": "error",
                    "error_kind": "TIMEOUT",
                    "message": message,
                    "operation_id": item.operation_id,
                    "operation_state": state,
                    "hints": [
                        "A transport ping does not check Fusion's main thread.",
                        "Running native exports and other Fusion API calls cannot "
                        "be safely interrupted; poll this ID instead of resubmitting.",
                    ],
                }
        return self._response(item)

    @staticmethod
    def _response(item):
        if item.error is not None:
            kind, hints = classify(item.error)
            return {
                "status": "error",
                "error_kind": kind,
                "hints": hints,
                "message": item.error,
            }
        return item.result

    def _prune_operations(self):
        """Retain at most 128 finished operations, for up to ten minutes.

        Caller holds _lock. Running/queued work is never evicted.
        """
        finished = [
            key
            for key, item in self._operations.items()
            if item.finished_at is not None
        ]
        now = time.monotonic()
        for index, key in enumerate(finished):
            if (
                len(finished) - index > 128
                or now - self._operations[key].finished_at > 600
            ):
                del self._operations[key]

    def get_operation_status(self, operation_id):
        """Socket-thread-only lookup; works even when Fusion is blocked."""
        with self._lock:
            self._prune_operations()
            item = self._operations.get(operation_id)
            if item is None:
                return {
                    "status": "error",
                    "error_kind": "INVALID_INPUT",
                    "message": "Unknown or expired operation_id (or add-in restarted)",
                }
            result = {
                "ok": True,
                "operation_id": operation_id,
                "operation_state": item.state,
            }
            if item.state in ("completed", "failed"):
                result["response"] = self._response(item)
            return {"status": "success", "result": result}

    # ------------------------------------------------------------------
    # Called on Fusion's main thread (from CustomEventHandler.notify)
    # ------------------------------------------------------------------

    def drain_queue(self):
        """Execute every queued work item (main thread only)."""
        with self._lock:
            if self._draining:
                return
            self._draining = True
        try:
            self._drain_queue()
        finally:
            with self._lock:
                self._draining = False

    def _drain_queue(self):
        while True:
            try:
                item: WorkItem = self._queue.get_nowait()
            except queue.Empty:
                break

            cmd_type = item.command.get("type", "?")
            with self._lock:
                if item.cancelled:
                    continue
                item.state = "running"
            try:
                if cmd_type == "ping":
                    item.result = {
                        "status": "success",
                        "result": {
                            "ok": True,
                            "status": "pong",
                            "scope": "main_thread",
                            "main_thread_checked": True,
                        },
                    }
                elif cmd_type == "reload_handler":
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
                with self._lock:
                    response = self._response(item)
                    failed = (
                        response.get("status") == "error"
                        or response.get("result", {}).get("ok") is False
                    )
                    item.state = "failed" if failed else "completed"
                    item.finished_at = time.monotonic()
                    item.done.set()
                    self._prune_operations()

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
            with self._lock:
                item.cancelled = True
                item.state = "cancelled"
                item.finished_at = time.monotonic()
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
