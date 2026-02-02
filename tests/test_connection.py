"""Tests for the TCP connection module (no Fusion 360 required)."""

import json
import socket
import threading

import pytest

from fusion360_mcp.connection import (
    Fusion360Connection,
    get_connection,
    reset_connection,
)


def _start_echo_server(host="127.0.0.1", port=0):
    """Start a minimal TCP server that echoes back JSON commands as responses."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(1)
    actual_port = srv.getsockname()[1]

    def handler():
        conn, _ = srv.accept()
        buf = b""
        try:
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    cmd = json.loads(line)
                    resp = {
                        "status": "success",
                        "result": {"echoed_type": cmd["type"],
                                   "echoed_params": cmd.get("params", {})},
                    }
                    conn.sendall((json.dumps(resp) + "\n").encode())
        except Exception:
            pass
        finally:
            conn.close()
            srv.close()

    t = threading.Thread(target=handler, daemon=True)
    t.start()
    return actual_port, srv, t


def _start_multi_accept_server(host="127.0.0.1", port=0, max_accepts=3):
    """Server that accepts multiple sequential connections (for reconnect tests)."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(5)
    actual_port = srv.getsockname()[1]

    def handler():
        for _ in range(max_accepts):
            try:
                conn, _ = srv.accept()
            except OSError:
                break
            buf = b""
            try:
                while True:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        cmd = json.loads(line)
                        resp = {
                            "status": "success",
                            "result": {"echoed_type": cmd["type"],
                                       "echoed_params": cmd.get("params", {})},
                        }
                        conn.sendall((json.dumps(resp) + "\n").encode())
            except Exception:
                pass
            finally:
                conn.close()
        srv.close()

    t = threading.Thread(target=handler, daemon=True)
    t.start()
    return actual_port, srv, t


class TestFusion360Connection:
    def test_connect_to_echo_server(self):
        port, srv, _ = _start_echo_server()
        conn = Fusion360Connection(host="127.0.0.1", port=port)
        assert conn.connect() is True
        assert conn.connected is True
        conn.disconnect()
        assert conn.connected is False

    def test_send_command_round_trip(self):
        port, srv, _ = _start_echo_server()
        conn = Fusion360Connection(host="127.0.0.1", port=port)
        conn.connect()

        result = conn.send_command("ping", {"foo": "bar"})
        assert result["echoed_type"] == "ping"
        assert result["echoed_params"] == {"foo": "bar"}
        conn.disconnect()

    def test_connect_failure(self):
        conn = Fusion360Connection(host="127.0.0.1", port=1)  # nothing listens
        assert conn.connect() is False
        assert conn.connected is False

    def test_send_without_connection_raises(self):
        conn = Fusion360Connection(host="127.0.0.1", port=1)
        with pytest.raises(ConnectionError):
            conn.send_command("ping", retries=0)

    def test_error_response_raises_runtime_error(self):
        """If the server returns status=error, send_command raises RuntimeError."""
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]

        def handler():
            c, _ = srv.accept()
            c.recv(4096)
            resp = {"status": "error", "message": "boom"}
            c.sendall((json.dumps(resp) + "\n").encode())
            c.close()
            srv.close()

        t = threading.Thread(target=handler, daemon=True)
        t.start()

        conn = Fusion360Connection(host="127.0.0.1", port=port)
        conn.connect()
        with pytest.raises(RuntimeError, match="boom"):
            conn.send_command("explode")


class TestPing:
    def test_ping_returns_true_when_connected(self):
        port, srv, _ = _start_echo_server()
        conn = Fusion360Connection(host="127.0.0.1", port=port)
        conn.connect()
        assert conn.ping() is True
        conn.disconnect()

    def test_ping_returns_false_when_disconnected(self):
        conn = Fusion360Connection(host="127.0.0.1", port=1)
        assert conn.ping() is False


class TestReconnect:
    def test_reconnect_after_disconnect(self):
        port, srv, _ = _start_multi_accept_server()
        conn = Fusion360Connection(host="127.0.0.1", port=port)
        conn.connect()
        assert conn.connected is True

        conn.disconnect()
        assert conn.connected is False

        assert conn.reconnect() is True
        assert conn.connected is True
        conn.disconnect()

    def test_ensure_connected_recovers(self):
        port, srv, _ = _start_multi_accept_server()
        conn = Fusion360Connection(host="127.0.0.1", port=port)
        conn.connect()

        # Simulate a dropped connection by closing the socket directly
        conn._sock.close()
        conn._sock = None

        assert conn.ensure_connected() is True
        conn.disconnect()

    def test_ensure_connected_on_healthy_connection(self):
        port, srv, _ = _start_echo_server()
        conn = Fusion360Connection(host="127.0.0.1", port=port)
        conn.connect()

        # Should succeed without reconnecting
        assert conn.ensure_connected() is True
        conn.disconnect()


class TestRetry:
    def test_send_command_retries_on_broken_connection(self, monkeypatch):
        """When the socket dies mid-command, send_command reconnects and retries."""
        port, srv, _ = _start_multi_accept_server()
        conn = Fusion360Connection(host="127.0.0.1", port=port)
        conn.connect()

        # Reduce retry delay so the test runs fast
        monkeypatch.setattr("fusion360_mcp.connection._RETRY_DELAY", 0.05)

        # Kill the underlying socket to simulate a dropped connection
        conn._sock.close()

        # send_command should reconnect and succeed
        result = conn.send_command("ping", retries=1)
        assert result["echoed_type"] == "ping"
        conn.disconnect()

    def test_send_command_exhausts_retries(self, monkeypatch):
        """After all retries fail, ConnectionError is raised."""
        monkeypatch.setattr("fusion360_mcp.connection._RETRY_DELAY", 0.01)

        conn = Fusion360Connection(host="127.0.0.1", port=1)
        # Force a socket so the first send attempt actually tries the wire
        conn._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        with pytest.raises(ConnectionError):
            conn.send_command("ping", retries=1)


class TestRecvJson:
    """Edge cases in _recv_json parsing."""

    def _make_server_sending(self, data: bytes):
        """Create a server that sends raw bytes then closes."""
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]

        def handler():
            c, _ = srv.accept()
            c.recv(4096)  # consume the command
            c.sendall(data)
            c.close()
            srv.close()

        t = threading.Thread(target=handler, daemon=True)
        t.start()
        return port

    def test_json_without_trailing_newline(self):
        """Server sends valid JSON but no newline — fallback parse."""
        resp = {"status": "success", "result": {"ok": True}}
        port = self._make_server_sending(json.dumps(resp).encode())

        conn = Fusion360Connection(host="127.0.0.1", port=port)
        conn.connect()
        result = conn.send_command("ping", retries=0)
        assert result == {"ok": True}

    def test_json_with_trailing_newline(self):
        """Standard newline-delimited JSON."""
        resp = {"status": "success", "result": {"ok": True}}
        data = (json.dumps(resp) + "\n").encode()
        port = self._make_server_sending(data)

        conn = Fusion360Connection(host="127.0.0.1", port=port)
        conn.connect()
        result = conn.send_command("ping", retries=0)
        assert result == {"ok": True}

    def test_connection_closed_raises(self):
        """Server closes immediately after accepting."""
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]

        def handler():
            c, _ = srv.accept()
            c.recv(4096)
            c.close()  # close without sending
            srv.close()

        t = threading.Thread(target=handler, daemon=True)
        t.start()

        conn = Fusion360Connection(host="127.0.0.1", port=port)
        conn.connect()
        with pytest.raises(ConnectionError):
            conn.send_command("ping", retries=0)


class TestGetConnectionSingleton:
    """Verify the module-level singleton behavior."""

    def test_reset_clears_singleton(self):
        """reset_connection should clear the cached connection."""
        import fusion360_mcp.connection as mod
        saved = mod._connection
        try:
            mod._connection = Fusion360Connection("127.0.0.1", 1)
            reset_connection()
            assert mod._connection is None
        finally:
            mod._connection = saved

    def test_reset_when_no_connection(self):
        """reset_connection should not error when nothing cached."""
        import fusion360_mcp.connection as mod
        saved = mod._connection
        try:
            mod._connection = None
            reset_connection()  # should not raise
            assert mod._connection is None
        finally:
            mod._connection = saved

    def test_get_connection_returns_same_instance(self):
        """Repeated calls return the same connection object."""
        port, srv, _ = _start_echo_server()
        import fusion360_mcp.connection as mod
        saved = mod._connection
        mod._connection = None
        try:
            c1 = get_connection(port=port)
            c2 = get_connection(port=port)
            assert c1 is c2
        finally:
            if mod._connection:
                mod._connection.disconnect()
            mod._connection = saved


class TestMultipleCommands:
    """Verify sending multiple commands on one connection."""

    def test_sequential_commands(self):
        port, srv, _ = _start_echo_server()
        conn = Fusion360Connection(host="127.0.0.1", port=port)
        conn.connect()

        for cmd in ["ping", "get_scene_info", "extrude"]:
            result = conn.send_command(cmd, {"test": cmd})
            assert result["echoed_type"] == cmd
            assert result["echoed_params"]["test"] == cmd

        conn.disconnect()

    def test_command_with_empty_params(self):
        port, srv, _ = _start_echo_server()
        conn = Fusion360Connection(host="127.0.0.1", port=port)
        conn.connect()
        result = conn.send_command("ping")
        assert result["echoed_type"] == "ping"
        assert result["echoed_params"] == {}
        conn.disconnect()

    def test_command_with_nested_params(self):
        port, srv, _ = _start_echo_server()
        conn = Fusion360Connection(host="127.0.0.1", port=port)
        conn.connect()
        params = {
            "points": [[0, 0], [1, 2], [3, 1]],
            "nested": {"a": 1, "b": [True, False]},
        }
        result = conn.send_command("draw_spline", params)
        assert result["echoed_params"] == params
        conn.disconnect()
