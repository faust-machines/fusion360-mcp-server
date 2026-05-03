#!/bin/bash
# Wrapper for running fusion360-mcp-server from inside a Docker devcontainer.
#
# The fusion360-mcp-server Python process connects to localhost:9876 to reach
# the Fusion add-in. From inside a Docker container, "localhost" is the
# container itself — not the host running Fusion. This wrapper starts a Python
# TCP relay that forwards localhost:9876 → host.docker.internal:9876, then
# execs the MCP server, which connects to localhost:9876 unchanged.
#
# Combined with a netsh portproxy + firewall rule on the Windows host
# (see fusion-mcp-bridge.ps1), this lets MCP clients running in a devcontainer
# reach the Fusion add-in transparently.
#
# Configure your MCP client's stdio command to point at this script. Example:
#   .vscode/mcp.json or .claude/settings.json:
#     "command": "/absolute/path/to/devcontainer/fusion-mcp-wrapper.sh"

set -e

# Start TCP relay: container localhost:9876 → host.docker.internal:9876
python3 - <<'EOF' &
import socket, threading

def pipe(src, dst):
    try:
        while chunk := src.recv(4096):
            dst.sendall(chunk)
    finally:
        src.close()
        dst.close()

srv = socket.socket()
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(('127.0.0.1', 9876))
srv.listen(5)
while True:
    client, _ = srv.accept()
    try:
        remote = socket.socket()
        remote.connect(('host.docker.internal', 9876))
        for a, b in [(client, remote), (remote, client)]:
            threading.Thread(target=pipe, args=(a, b), daemon=True).start()
    except Exception:
        client.close()
EOF

# Let the relay bind before the MCP server tries to connect
sleep 0.5

# Locate uvx — prefer PATH, fall back to uv's default install location
UVX="$(command -v uvx || true)"
if [ -z "$UVX" ] && [ -x "${HOME}/.local/bin/uvx" ]; then
    UVX="${HOME}/.local/bin/uvx"
fi
if [ -z "$UVX" ]; then
    echo "ERROR: uvx not found. Install uv first:" >&2
    echo "  curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
    exit 1
fi

exec "$UVX" fusion360-mcp-server --mode socket
