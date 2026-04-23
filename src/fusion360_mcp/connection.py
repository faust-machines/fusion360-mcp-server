"""
TCP connection to the Fusion360MCP add-in running inside Fusion 360.

The add-in listens on localhost:9876 by default and speaks
newline-delimited JSON. Override via env vars FUSION_MCP_HOST /
FUSION_MCP_PORT for cross-machine setups (e.g. MCP server on a
Mac Mini connecting to Fusion running on a Windows PC).
"""

import json
import logging
import os
import socket
import time
from typing import Any

log = logging.getLogger("fusion360_mcp.connection")

_DEFAULT_HOST = os.environ.get("FUSION_MCP_HOST", "localhost")
_DEFAULT_PORT = int(os.environ.get("FUSION_MCP_PORT", "9876"))
_RECV_BUF = 65536
_TIMEOUT = 30.0  # matches the add-in's bridge timeout
_PING_TIMEOUT = 5.0
_MAX_RETRIES = 2
_RETRY_DELAY = 1.0  # seconds between reconnect attempts


class Fusion360Connection:
    """Persistent TCP connection to the Fusion 360 add-in socket server."""

    def __init__(self, host: str = _DEFAULT_HOST, port: int = _DEFAULT_PORT):
        self.host = host
        self.port = port
        self._sock: socket.socket | None = None

    # ------------------------------------------------------------------
    # Connect / disconnect
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        if self._sock is not None:
            return True
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(_PING_TIMEOUT)
            s.connect((self.host, self.port))
            self._sock = s
            log.info("Connected to Fusion 360 at %s:%s", self.host, self.port)
            return True
        except Exception as exc:
            log.error("Failed to connect: %s", exc)
            self._sock = None
            return False

    def disconnect(self):
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    def reconnect(self) -> bool:
        """Drop the existing socket and open a fresh one."""
        self.disconnect()
        return self.connect()

    @property
    def connected(self) -> bool:
        return self._sock is not None

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    def ping(self) -> bool:
        """Send a ping and return True if the add-in responds."""
        try:
            self.send_command("ping")
            return True
        except Exception:
            return False

    def ensure_connected(self) -> bool:
        """Verify the connection is alive (via ping), reconnect if not."""
        if self._sock is not None and self.ping():
            return True
        # Connection is dead — try to reconnect
        log.warning("Connection lost, attempting reconnect...")
        return self.reconnect()

    # ------------------------------------------------------------------
    # Send / receive
    # ------------------------------------------------------------------

    def send_command(self, command_type: str,
                     params: dict[str, Any] | None = None,
                     retries: int = _MAX_RETRIES) -> dict:
        """Send a JSON command and block until a JSON response arrives.

        On connection failure, retries up to ``retries`` times with a
        fresh socket before raising.
        """
        if not self._sock and not self.connect():
            raise ConnectionError(
                "Not connected to Fusion 360.  "
                "Make sure the add-in is running.")

        payload = json.dumps({
            "type": command_type,
            "params": params or {},
        }) + "\n"

        try:
            self._sock.sendall(payload.encode("utf-8"))
            self._sock.settimeout(_TIMEOUT)
            response = self._recv_json()
        except (socket.timeout, OSError, ConnectionError) as exc:
            log.error("Socket error: %s", exc)
            self.disconnect()
            if retries > 0:
                log.info("Retrying (%d left)...", retries)
                time.sleep(_RETRY_DELAY)
                if self.connect():
                    return self.send_command(command_type, params,
                                            retries=retries - 1)
            raise ConnectionError(f"Lost connection to Fusion 360: {exc}") from exc

        if response.get("status") == "error":
            raise RuntimeError(response.get("message", "Unknown error"))

        return response.get("result", {})

    def _recv_json(self) -> dict:
        """Read newline-delimited JSON from the socket."""
        buf = b""
        while True:
            chunk = self._sock.recv(_RECV_BUF)
            if not chunk:
                raise ConnectionError("Connection closed by Fusion 360")
            buf += chunk

            # Look for a complete newline-terminated message
            if b"\n" in buf:
                line, _rest = buf.split(b"\n", 1)
                return json.loads(line)

            # Fallback: try to parse the whole buffer as JSON
            try:
                return json.loads(buf)
            except json.JSONDecodeError:
                continue


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_connection: Fusion360Connection | None = None


def get_connection(*, host: str = _DEFAULT_HOST,
                   port: int = _DEFAULT_PORT) -> Fusion360Connection:
    """Return (and lazily create) a shared connection."""
    global _connection
    if _connection is not None:
        return _connection
    _connection = Fusion360Connection(host, port)
    _connection.connect()
    return _connection


def reset_connection():
    """Drop the cached connection (e.g. after an error)."""
    global _connection
    if _connection:
        _connection.disconnect()
    _connection = None
