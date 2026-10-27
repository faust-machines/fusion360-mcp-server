"""Exercise bridge timeout races with a simulated Fusion event loop."""

import importlib.util
import json
import logging
import queue
import socket
import sys
import threading
import time
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


@pytest.fixture
def bridge(monkeypatch):
    package = ModuleType("_bridge_test")
    package.__path__ = [str(Path(__file__).parents[1] / "addon/server")]
    package.get_logger = logging.getLogger
    adsk = ModuleType("adsk")
    adsk.core = SimpleNamespace(CustomEventHandler=object, Application=object)
    monkeypatch.setitem(sys.modules, "adsk", adsk)
    monkeypatch.setitem(sys.modules, "adsk.core", adsk.core)
    monkeypatch.setitem(sys.modules, "_bridge_test", package)
    spec = importlib.util.spec_from_file_location(
        "_bridge_test.event_bridge", Path(package.__path__[0]) / "event_bridge.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    instance = module.EventBridge.__new__(module.EventBridge)
    instance._lock = threading.Lock()
    instance._operations = {}
    instance._queue = queue.Queue()
    instance._draining = False
    instance._app = SimpleNamespace(fireCustomEvent=lambda _: None)
    instance._handler = SimpleNamespace(
        execute_command=lambda _: {
            "status": "success",
            "result": {"ok": True, "value": 42},
        }
    )
    return instance


def test_queued_timeout_is_cancelled_and_never_runs(bridge):
    response = bridge.submit({"type": "execute_code"}, timeout=0)
    assert response["operation_state"] == "cancelled"
    bridge._handler.execute_command = lambda _: pytest.fail("cancelled work executed")
    bridge.drain_queue()
    status = bridge.submit(
        {
            "type": "get_operation_status",
            "params": {
                "operation_id": response["operation_id"],
            },
        }
    )
    assert status["result"]["operation_state"] == "cancelled"


@pytest.mark.parametrize("fail", [False, True])
def test_running_timeout_recovers_original_result(bridge, fail):
    started, release = threading.Event(), threading.Event()
    calls = []

    def execute(command):
        calls.append(command)
        started.set()
        assert release.wait(2)
        if fail:
            raise RuntimeError("cloud unavailable")
        return {"status": "success", "result": {"ok": True, "files": ["GA_HS_R3"]}}

    bridge._handler.execute_command = execute
    responses = []
    waiter = threading.Thread(
        target=lambda: responses.append(
            bridge.submit({"type": "execute_code"}, timeout=0.1)
        )
    )
    waiter.start()
    deadline = time.monotonic() + 2
    while bridge._queue.empty() and time.monotonic() < deadline:
        time.sleep(0.001)
    worker = threading.Thread(target=bridge.drain_queue)
    worker.start()
    try:
        assert started.wait(2)
        waiter.join(2)
        response = responses[0]
        assert response["operation_state"] == "running"
        assert bridge.submit({"type": "ping"})["result"]["main_thread_checked"] is False
        assert (
            bridge.get_operation_status(response["operation_id"])["result"][
                "operation_state"
            ]
            == "running"
        )
        # A nested custom event must not run queued commands reentrantly.
        work_item = bridge.submit.__func__.__globals__["WorkItem"]
        queued_ping = work_item({"type": "ping", "params": {"main_thread": True}})
        bridge._queue.put(queued_ping)
        bridge.drain_queue()
        assert queued_ping.state == "queued"
    finally:
        release.set()
        worker.join(2)
        waiter.join(2)
    status = bridge.get_operation_status(response["operation_id"])["result"]
    assert status["operation_state"] == ("failed" if fail else "completed")
    if fail:
        assert "cloud unavailable" in status["response"]["message"]
    else:
        assert status["response"]["result"]["files"] == ["GA_HS_R3"]
    assert len(calls) == 1


def test_main_thread_ping_uses_queue(bridge):
    bridge._app.fireCustomEvent = lambda _: bridge.drain_queue()
    result = bridge.submit({"type": "ping", "params": {"main_thread": True}})
    assert result["result"]["main_thread_checked"] is True


def test_expired_and_unknown_operations(bridge):
    assert bridge.get_operation_status("missing")["error_kind"] == "INVALID_INPUT"
    response = bridge.submit({"type": "execute_code"}, timeout=0)
    bridge._operations[response["operation_id"]].finished_at -= 601
    assert bridge.get_operation_status(response["operation_id"])["status"] == "error"


def test_main_thread_ping_cannot_report_health_when_queue_blocked(bridge):
    result = bridge.submit({"type": "ping", "params": {"main_thread": True}}, timeout=0)
    assert result["error_kind"] == "TIMEOUT"
    assert result["operation_state"] == "cancelled"


def test_retention_is_bounded_and_preserves_running_work(bridge):
    item_class = bridge.submit.__func__.__globals__["WorkItem"]
    running = item_class({"type": "execute_code"})
    running.state = "running"
    bridge._operations[running.operation_id] = running
    for _ in range(140):
        bridge.submit({"type": "execute_code"}, timeout=0)
    with bridge._lock:
        bridge._prune_operations()
    assert len(bridge._operations) == 129
    assert running.operation_id in bridge._operations


@pytest.mark.parametrize("operation_id", ["", 123, [], "x" * 129])
def test_invalid_client_operation_id_is_not_queued(bridge, operation_id):
    response = bridge.submit({"type": "execute_code", "operation_id": operation_id})
    assert response["error_kind"] == "INVALID_INPUT"
    assert bridge._queue.empty()


def test_duplicate_id_preserves_original_operation(bridge):
    command = {"type": "execute_code", "operation_id": "client-id"}
    bridge.submit(command, timeout=0)
    original = bridge._operations["client-id"]
    assert bridge.submit(command, timeout=0)["error_kind"] == "INVALID_INPUT"
    assert bridge._operations["client-id"] is original
    assert bridge._queue.qsize() == 1


@pytest.mark.parametrize("fail", [False, True])
def test_transport_timeout_recovers_execute_code_without_resubmitting(bridge, fail):
    """Lose the socket response before bridge timeout, then recover on a new socket."""
    from fusion360_mcp.connection import Fusion360Connection, FusionError
    from fusion360_mcp.server import _fusion_error_result

    started, release = threading.Event(), threading.Event()
    calls = []
    threads = []

    def execute(command):
        calls.append(command)
        started.set()
        assert release.wait(3)
        if fail:
            raise RuntimeError("export failed")
        return {"status": "success", "result": {"ok": True, "file": "assembly.step"}}

    bridge._handler.execute_command = execute
    conn = Fusion360Connection()

    def open_socket():
        client, server = socket.socketpair()
        conn._sock = client

        def serve():
            try:
                with server.makefile("rb") as stream:
                    command = json.loads(stream.readline())
                response = bridge.submit(command, timeout=0.2)
                try:
                    server.sendall((json.dumps(response) + "\n").encode())
                except OSError:
                    pass  # caller already timed out and disconnected
            finally:
                server.close()

        thread = threading.Thread(target=serve)
        threads.append(thread)
        thread.start()

    # Start execution as soon as the socket thread queues the request.
    def fire(_):
        worker = threading.Thread(target=bridge.drain_queue)
        threads.append(worker)
        worker.start()

    bridge._app.fireCustomEvent = fire
    try:
        open_socket()
        with pytest.raises(FusionError) as caught:
            conn.send_command("execute_code", {"code": "export()"}, timeout=0.05)
        assert started.wait(2)
        error = caught.value
        operation_id = error.details["operation_id"]
        assert error.details["operation_state"] == "unknown"
        assert calls[0]["operation_id"] == operation_id
        rendered = _fusion_error_result("execute_code", error)
        assert operation_id in rendered.content[0].text
        open_socket()
        status = conn.send_command(
            "get_operation_status", {"operation_id": operation_id}
        )
        assert status["operation_state"] == "running"
        conn.disconnect()
        release.set()
        for thread in threads:
            thread.join(3)
            assert not thread.is_alive()
        open_socket()
        status = conn.send_command(
            "get_operation_status", {"operation_id": operation_id}
        )
        assert status["operation_state"] == ("failed" if fail else "completed")
        if fail:
            assert "export failed" in status["response"]["message"]
        else:
            assert status["response"]["result"]["file"] == "assembly.step"
        assert len(calls) == 1
    finally:
        release.set()
        conn.disconnect()
        for thread in threads:
            thread.join(3)
