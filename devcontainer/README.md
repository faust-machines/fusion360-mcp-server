# Devcontainer setup

Run `fusion360-mcp-server` from inside a Docker devcontainer (e.g. VS Code's Dev Containers extension) while Fusion 360 runs on the Windows host.

## The problem

`fusion360-mcp-server` connects to `localhost:9876` to reach the Fusion add-in. From inside a Docker container, `localhost` is the container's own loopback — not the Windows host where Fusion is running. Three things stand in the way:

1. The container's `localhost` does not reach the host
2. The Fusion add-in binds to `127.0.0.1` on Windows, so even traffic that reaches the host gets dropped if it comes in on a different interface
3. Windows Firewall blocks inbound traffic from the Docker subnet by default

## The fix

```
Devcontainer
  MCP client
    └── spawns fusion-mcp-wrapper.sh (stdio MCP subprocess)
         ├── Python TCP relay  localhost:9876 ──────────────────────┐
         └── uvx fusion360-mcp-server  (connects to localhost:9876) ┘
                                                                     │ relayed to
                                                              host.docker.internal:9876
                                                                     │
Windows host (netsh portproxy)                                       │
  Docker bridge IP:9876 ──forwards──► 127.0.0.1:9876 ◄───────────────┘
                                            │
                                     Fusion MCP add-in
```

Two scripts handle it:

1. **`fusion-mcp-bridge.ps1`** (Windows-side, run once as Administrator) — adds `netsh portproxy` rules forwarding the Docker bridge adapter IPs to `127.0.0.1:9876`, plus a Windows Firewall inbound rule scoped to the Docker subnet only. Both rules persist across reboots.
2. **`fusion-mcp-wrapper.sh`** (devcontainer-side, configured as your MCP client's stdio command) — starts a Python TCP relay forwarding the container's `localhost:9876` to `host.docker.internal:9876`, then execs `uvx fusion360-mcp-server --mode socket`. The MCP server connects to `localhost:9876` unchanged; the relay bridges it to the Windows host.

## Setup

### 1. Install the Fusion add-in (Windows)

Follow the main README — copy the `addon` directory into Fusion's add-ins folder and start it via Shift+S → Add-Ins → Fusion360MCP → Run. Confirm in TEXT COMMANDS:

```
[MCP] Server listening on localhost:9876
```

### 2. Run the Windows bridge script (once, as Administrator)

```powershell
.\devcontainer\fusion-mcp-bridge.ps1
```

The script:
1. Discovers Docker bridge adapter IPs (those in the 172.16–31.x range — avoids LAN/VPN adapters)
2. Adds a `netsh portproxy` rule per adapter forwarding to `127.0.0.1:9876`
3. Adds a Windows Firewall inbound rule scoped to those subnets

Idempotent. Re-run after Docker network changes. To remove:

```powershell
.\devcontainer\fusion-mcp-bridge.ps1 -Remove
```

### 3. Verify connectivity from the devcontainer

```bash
python3 -c "
import socket
s = socket.socket()
s.settimeout(3)
s.connect(('host.docker.internal', 9876))
print('OK')
s.close()
"
```

If you get `Connection refused`: Fusion add-in isn't running.  
If you get `Connection timed out`: portproxy or firewall rule missing — re-run the bridge script.

### 4. Configure your MCP client

Point your MCP client's stdio command at the wrapper script (use an absolute path).

**Claude Code (`.claude/settings.json`):**
```json
{
  "mcpServers": {
    "fusion": {
      "command": "/absolute/path/to/devcontainer/fusion-mcp-wrapper.sh",
      "args": []
    }
  }
}
```

**VS Code MCP (`.vscode/mcp.json`):**
```json
{
  "servers": {
    "fusion": {
      "type": "stdio",
      "command": "/absolute/path/to/devcontainer/fusion-mcp-wrapper.sh"
    }
  }
}
```

**Cursor (`~/.cursor/mcp.json`):**
```json
{
  "mcpServers": {
    "fusion": {
      "command": "/absolute/path/to/devcontainer/fusion-mcp-wrapper.sh"
    }
  }
}
```

### 5. Test

Ask your MCP client to call the `ping` tool. If it returns `{"pong": true}`, you're connected end-to-end.

## Prerequisites

- **Docker Desktop** on Windows (the wrapper relies on `host.docker.internal`, which Docker Desktop populates automatically)
- **`uv`** in the devcontainer (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- **Python 3.8+** in the devcontainer (for the TCP relay — uses only stdlib)
- The wrapper script must be executable: `chmod +x devcontainer/fusion-mcp-wrapper.sh`

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| MCP client shows server disconnected | Wrapper failed to start | Run the wrapper manually (`bash devcontainer/fusion-mcp-wrapper.sh`) and check stderr |
| `uvx: not found` | uv not installed in container | Install uv (see Prerequisites) |
| Python relay error: address in use | Stale relay from a previous run | `lsof -ti:9876 \| xargs kill` |
| TCP test: connection refused | Fusion add-in not running on host | Shift+S → Add-Ins → Fusion360MCP → Run |
| TCP test: connection timed out | Bridge script not run, or firewall blocking | Re-run `fusion-mcp-bridge.ps1` as Administrator |
| Bridge script: no Docker adapters found | Docker Desktop not running, or unusual network setup | Check `Get-NetIPAddress` shows a 172.16–31.x adapter |

## Why not bind the add-in to 0.0.0.0?

That would expose the Fusion API on every interface — including LAN and VPN adapters. The bridge approach scopes exposure to the Docker bridge subnet only, with a firewall rule that drops everything else.
