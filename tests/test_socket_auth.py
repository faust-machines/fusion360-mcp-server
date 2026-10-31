"""Tests for token auth and message framing in the add-in socket server.

No Fusion 360 required: the add-in package is stubbed and socket_server.py
is loaded by path, with a fake bridge that records what would be dispatched.
"""

import importlib
import json
import logging
import os
import socket
import sys
import time
import types

import pytest

_SERVER_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "addon", "server")
)
_PKG = "fusion_addon_under_test"
TOKEN = "test-token-123"


@pytest.fixture(scope="module")
def socket_server_mod():
    pkg = types.ModuleType(_PKG)
    pkg.__path__ = [_SERVER_DIR]
    pkg.get_logger = lambda name: logging.getLogger(f"fusion360mcp.{name}")
    sys.modules[_PKG] = pkg
    try:
        yield importlib.import_module(f"{_PKG}.socket_server")
    finally:
        for name in list(sys.modules):
            if name == _PKG or name.startswith(_PKG + "."):
                del sys.modules[name]


class FakeBridge:
    """Stands in for EventBridge: records every dispatched command."""

    def __init__(self):
        self.commands = []

    def submit(self, command, timeout=30.0):
        self.commands.append(command)
        return {"status": "success", "result": {"ok": True}}


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_listening(port):
    deadline = time.time() + 5
    while True:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=1).close()
            return
        except OSError:
            if time.time() > deadline:
                raise
            time.sleep(0.05)


@pytest.fixture
def server(socket_server_mod):
    bridge = FakeBridge()
    port = _free_port()
    srv = socket_server_mod.Fusion360MCPServer(
        bridge, host="127.0.0.1", port=port, token=TOKEN
    )
    srv.start()
    _wait_listening(port)
    yield srv, bridge, port
    srv.stop()


def _connect(port):
    return socket.create_connection(("127.0.0.1", port), timeout=5)


def _read_line(sock):
    buf = b""
    while b"\n" not in buf:
        chunk = sock.recv(65536)
        if not chunk:
            break
        buf += chunk
    return buf.split(b"\n", 1)[0]


def _read_until_closed(sock):
    """Everything the server sent before closing; a timeout means it stayed open."""
    data = b""
    try:
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                return data
            data += chunk
    except ConnectionResetError:
        return data


def _assert_rejected(port, bridge, payload, error_kind):
    with _connect(port) as c:
        c.sendall(payload)
        data = _read_until_closed(c)
    reply = json.loads(data.split(b"\n", 1)[0])
    assert reply["status"] == "error"
    assert reply["error_kind"] == error_kind
    assert bridge.commands == []


def test_valid_token_dispatches(server):
    _, bridge, port = server
    with _connect(port) as c:
        msg = {"type": "get_scene_info", "token": TOKEN}
        c.sendall(json.dumps(msg).encode() + b"\n")
        reply = json.loads(_read_line(c))
    assert reply["status"] == "success"
    # The token is removed before the command reaches the bridge.
    assert bridge.commands == [{"type": "get_scene_info"}]


def test_valid_token_raw_json_without_newline_dispatches(server):
    _, bridge, port = server
    with _connect(port) as c:
        c.sendall(json.dumps({"type": "get_scene_info", "token": TOKEN}).encode())
        reply = json.loads(_read_line(c))
    assert reply["status"] == "success"
    assert bridge.commands == [{"type": "get_scene_info"}]


def test_wrong_token_is_rejected_and_closed(server):
    _, bridge, port = server
    payload = json.dumps({"type": "get_scene_info", "token": "wrong"}).encode() + b"\n"
    _assert_rejected(port, bridge, payload, "AUTH")


def test_missing_token_is_rejected_and_closed(server):
    _, bridge, port = server
    payload = json.dumps({"type": "get_scene_info"}).encode() + b"\n"
    _assert_rejected(port, bridge, payload, "AUTH")


def test_non_string_token_is_rejected_and_closed(server):
    _, bridge, port = server
    payload = json.dumps({"type": "get_scene_info", "token": ["x"]}).encode() + b"\n"
    _assert_rejected(port, bridge, payload, "AUTH")


def test_http_post_with_json_body_is_rejected_and_closed(server):
    _, bridge, port = server
    body = json.dumps({"type": "get_scene_info", "token": TOKEN}).encode()
    payload = (
        b"POST / HTTP/1.1\r\n"
        b"Host: localhost\r\n"
        b"Content-Type: application/json\r\n"
        + f"Content-Length: {len(body)}\r\n\r\n".encode()
        + body
        + b"\n"
    )
    _assert_rejected(port, bridge, payload, "BAD_REQUEST")
