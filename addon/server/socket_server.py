"""
Fusion360 MCP Socket Server

TCP socket on localhost:9876.  Each client gets its own daemon thread.
All Fusion API work is dispatched through the EventBridge so it runs on
the main thread.

The accept loop auto-restarts on transient socket errors so the bridge
stays alive even if the listening socket dies.
"""

import json
import socket
import threading
import time
import traceback

from . import get_logger

log = get_logger("socket")

_RESTART_DELAY = 2.0  # seconds before rebinding after socket error
_MAX_RESTARTS = 10  # consecutive restart cap before giving up


class Fusion360MCPServer:
    """TCP server that receives JSON commands and dispatches via EventBridge."""

    def __init__(self, event_bridge, host="localhost", port=9876):
        self.host = host
        self.port = port
        self._bridge = event_bridge
        self._running = False
        self._socket = None
        self._accept_thread = None
        self._clients: set[socket.socket] = set()
        self._clients_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self):
        if self._running:
            return False

        self._running = True
        self._accept_thread = threading.Thread(
            target=self._accept_loop_with_restart, daemon=True
        )
        self._accept_thread.start()
        return True

    def stop(self):
        self._running = False
        self._close_socket()
        # Close accepted client sockets so per-client threads exit
        # instead of blocking on recv() against a stopped server.
        with self._clients_lock:
            clients = list(self._clients)
        for c in clients:
            try:
                c.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                c.close()
            except Exception:
                pass
        if self._accept_thread and self._accept_thread.is_alive():
            self._accept_thread.join(timeout=2.0)
        self._accept_thread = None
        log.info("Server stopped")

    def is_running(self):
        return self._running

    # ------------------------------------------------------------------
    # Socket helpers
    # ------------------------------------------------------------------

    def _bind_socket(self):
        """Create, bind, and listen.  Returns True on success."""
        self._close_socket()
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.settimeout(1.0)
            s.bind((self.host, self.port))
            s.listen(5)
            self._socket = s
            log.info("Server listening on %s:%s", self.host, self.port)
            return True
        except Exception as exc:
            log.error("Bind failed: %s", exc)
            self._close_socket()
            return False

    def _close_socket(self):
        if self._socket:
            try:
                self._socket.close()
            except Exception:
                pass
            self._socket = None

    # ------------------------------------------------------------------
    # Accept loop with automatic restart
    # ------------------------------------------------------------------

    def _accept_loop_with_restart(self):
        """Outer loop: rebinds the socket up to _MAX_RESTARTS times."""
        restarts = 0

        while self._running:
            if not self._bind_socket():
                restarts += 1
                if restarts > _MAX_RESTARTS:
                    log.error("Exceeded %d restart attempts — giving up", _MAX_RESTARTS)
                    break
                log.warning(
                    "Retrying bind in %.1fs (attempt %d/%d)",
                    _RESTART_DELAY,
                    restarts,
                    _MAX_RESTARTS,
                )
                time.sleep(_RESTART_DELAY)
                continue

            # Reset counter on a successful bind
            restarts = 0

            try:
                self._accept_loop()
            except Exception:
                if self._running:
                    log.error("Accept loop crashed:\n%s", traceback.format_exc())

            # If we get here and still running, the socket died — restart
            if self._running:
                log.warning("Socket lost — restarting in %.1fs", _RESTART_DELAY)
                self._close_socket()
                time.sleep(_RESTART_DELAY)

    def _accept_loop(self):
        """Inner loop: accepts clients until the socket errors out."""
        while self._running:
            try:
                client, addr = self._socket.accept()
                log.info("Client connected: %s", addr)
                with self._clients_lock:
                    self._clients.add(client)
                t = threading.Thread(
                    target=self._handle_client, args=(client,), daemon=True
                )
                t.start()
            except socket.timeout:
                continue
            except OSError:
                if self._running:
                    raise  # bubble up so outer loop can restart
                break

    # ------------------------------------------------------------------
    # Per-client handler (daemon thread)
    # ------------------------------------------------------------------

    def _handle_client(self, client: socket.socket):
        client.settimeout(None)
        buf = b""

        try:
            while self._running:
                chunk = client.recv(65536)
                if not chunk:
                    break
                buf += chunk

                # Newline-delimited JSON messages
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        command = json.loads(line)
                    except json.JSONDecodeError:
                        self._send(
                            client, {"status": "error", "message": "Invalid JSON"}
                        )
                        continue
                    self._dispatch(client, command)

                # Fallback: try raw JSON blob (no newline framing)
                if buf:
                    stripped = buf.strip()
                    if stripped:
                        try:
                            command = json.loads(stripped)
                            buf = b""
                            self._dispatch(client, command)
                        except json.JSONDecodeError:
                            pass  # incomplete — wait for more data
        except Exception:
            if self._running:
                log.debug("Client handler error:\n%s", traceback.format_exc())
        finally:
            with self._clients_lock:
                self._clients.discard(client)
            try:
                client.close()
            except Exception:
                pass
            log.info("Client disconnected")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _dispatch(self, client, command):
        """Submit command to bridge and send response back to client."""
        try:
            response = self._bridge.submit(command)
        except Exception as exc:
            response = {"status": "error", "message": str(exc)}
        self._send(client, response)

    @staticmethod
    def _send(client, data: dict):
        try:
            payload = json.dumps(data) + "\n"
            client.sendall(payload.encode("utf-8"))
        except Exception as exc:
            log.warning("Failed to send response to client: %s", exc)
