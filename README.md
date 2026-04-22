# Fusion360 MCP Server

> **Beta** — This project is under active development. APIs and tool behavior may change between releases. Use at your own discretion. Feedback and bug reports welcome via [GitHub Issues](https://github.com/faust-machines/fusion360-mcp-server/issues).

MCP server that connects AI coding agents to Autodesk Fusion 360 for CAD automation.

Tested with [Claude Code](https://docs.anthropic.com/en/docs/claude-code). Works with any MCP-compatible client — OpenCode, Codex, Cursor, or anything that speaks the [Model Context Protocol](https://modelcontextprotocol.io).

## How it works

```
Any MCP Client ←(stdio MCP)→ This Server ←(TCP :9876)→ Fusion360MCP Add-in ←(CustomEvent)→ Fusion Main Thread
```

Two components:

1. **MCP Server** (this repo) — Python process that speaks MCP protocol to Claude and forwards commands over TCP
2. **Fusion360MCP Add-in** (installed in Fusion's AddIns folder) — runs inside Fusion 360, executes API calls safely on the main thread

## Prerequisites

- [uv](https://docs.astral.sh/uv/) (Python package manager)
- Autodesk Fusion 360
- An MCP-compatible client (Claude Code, OpenCode, Codex, Cursor, etc.)

## Installation

### 1. Install the Fusion 360 Add-in

**Quick install (symlink for development):**
```bash
./scripts/install-addon.sh
```

**Manual install:**
```bash
# macOS
cp -r addon ~/Library/Application\ Support/Autodesk/Autodesk\ Fusion\ 360/API/AddIns/Fusion360MCP

# Windows (PowerShell)
Copy-Item -Recurse addon "$env:APPDATA\Autodesk\Autodesk Fusion 360\API\AddIns\Fusion360MCP"
```

Then start it in Fusion: **Shift+S → Add-Ins → Fusion360MCP → Run**

You should see `[MCP] Server listening on localhost:9876` in the TEXT COMMANDS window.

### 2. Connect your MCP client

The MCP server is published on [PyPI](https://pypi.org/project/fusion360-mcp-server/) — no need to clone this repo.

#### Claude Code

```bash
claude mcp add fusion360 -- uvx fusion360-mcp-server --mode socket
```

#### Other MCP clients

The server runs over **stdio**, so any MCP-compatible client can launch it. The command is:

```
uvx fusion360-mcp-server --mode socket
```

<details>
<summary><strong>Cursor</strong> (~/.cursor/mcp.json)</summary>

```json
{
  "mcpServers": {
    "fusion360": {
      "command": "uvx",
      "args": [
        "fusion360-mcp-server",
        "--mode", "socket"
      ]
    }
  }
}
```
</details>

### 3. Verify

Call the `ping` tool from your client. If it returns `{"pong": true}`, everything is connected.

### Uninstalling

1. Remove the `fusion360` entry from your MCP client config
2. Stop the add-in in Fusion (Shift+S → Add-Ins → Fusion360MCP → Stop)
3. Delete the add-in folder from Fusion's AddIns directory

## Available Tools (83)

### Scene & Query
| Tool | Description |
|------|-------------|
| `ping` | Health check (instant, no Fusion API) |
| `get_scene_info` | Design name, bodies, sketches, features, camera |
| `get_object_info` | Detailed info about a named body or sketch |
| `list_components` | List all components in the design |

### Design Type Safety
| Tool | Description |
|------|-------------|
| `get_design_type` | Check if design is in parametric or direct mode |
| `set_design_type` | Switch design type (parametric/direct recovery) |

### Sketching
| Tool | Description |
|------|-------------|
| `create_sketch` | New sketch on xy/yz/xz plane, optional offset |
| `draw_rectangle` | Rectangle in most recent sketch |
| `draw_circle` | Circle in most recent sketch |
| `draw_line` | Line in most recent sketch |
| `draw_arc` | Arc (center + start + sweep angle) |
| `draw_spline` | Fit-point or control-point spline |
| `create_polygon` | Regular polygon (3–64 sides) |
| `add_constraint` | Geometric constraint (coincident, parallel, tangent, etc.) |
| `add_dimension` | Driving dimension (distance, angle, radial, diameter) |
| `offset_curve` | Offset connected sketch curves |
| `trim_curve` | Trim at intersections |
| `extend_curve` | Extend to nearest intersection |
| `project_geometry` | Project edges/bodies onto sketch plane |

### Features
| Tool | Description |
|------|-------------|
| `extrude` | Extrude a sketch profile |
| `revolve` | Revolve a profile around an axis |
| `sweep` | Sweep a profile along a path |
| `loft` | Loft between two or more profiles |
| `fillet` | Round edges (all/top/bottom/vertical) |
| `chamfer` | Chamfer edges |
| `shell` | Hollow out a body |
| `mirror` | Mirror a body across a plane |
| `create_hole` | Hole feature on a body face |
| `rectangular_pattern` | Pattern in rows and columns |
| `circular_pattern` | Pattern around an axis |
| `create_thread` | Add threads (cosmetic or modeled) |
| `draft_faces` | Draft/taper faces for mold release |
| `split_body` | Split a body using a plane |
| `split_face` | Split faces of a body |
| `offset_faces` | Push/pull faces by a distance |
| `scale_body` | Scale uniformly or non-uniformly |
| `suppress_feature` | Suppress a timeline feature |
| `unsuppress_feature` | Re-enable a suppressed feature |

### Body Operations
| Tool | Description |
|------|-------------|
| `move_body` | Translate a body by (x, y, z) |
| `rename_body` | Rename a body (searches root and all components) |
| `boolean_operation` | Join/cut/intersect two bodies |
| `delete_all` | Clear the design |
| `undo` | Undo last operation (with design-type safety guard) |

### Direct Primitives
| Tool | Description |
|------|-------------|
| `create_box` | Box (via TemporaryBRepManager) |
| `create_cylinder` | Cylinder |
| `create_sphere` | Sphere |
| `create_torus` | Torus |

### Surface Operations
| Tool | Description |
|------|-------------|
| `patch_surface` | Create a patch surface from boundary edges |
| `stitch_surfaces` | Stitch surface bodies into one |
| `thicken_surface` | Thicken a surface into a solid |
| `ruled_surface` | Ruled surface from an edge |
| `trim_surface` | Trim a surface with another body |

### Sheet Metal
| Tool | Description |
|------|-------------|
| `create_flange` | Create a flange on an edge |
| `create_bend` | Add a bend |
| `flat_pattern` | Create flat pattern |
| `unfold` | Unfold specific bends |

### Construction Geometry
| Tool | Description |
|------|-------------|
| `create_construction_plane` | Offset, angle, midplane, 3-point, tangent |
| `create_construction_axis` | Two-point, intersection, edge, perpendicular |

### Assembly
| Tool | Description |
|------|-------------|
| `create_component` | Create a sub-assembly component |
| `add_joint` | Joint between two components |
| `create_as_built_joint` | Joint from current positions |
| `create_rigid_group` | Lock components together |

### Inspection & Analysis
| Tool | Description |
|------|-------------|
| `measure_distance` | Minimum distance between entities |
| `measure_angle` | Angle between entities |
| `get_physical_properties` | Mass, volume, area, center of mass |
| `create_section_analysis` | Section plane through model |
| `check_interference` | Detect collisions between components |

### Appearance
| Tool | Description |
|------|-------------|
| `set_appearance` | Assign material appearance from library |

### Parameters
| Tool | Description |
|------|-------------|
| `get_parameters` | List all user parameters |
| `create_parameter` | Create a new parameter |
| `set_parameter` | Update a parameter value |
| `delete_parameter` | Remove a parameter |

### Export
| Tool | Description |
|------|-------------|
| `export_stl` | Export body as STL (supports bodies inside components) |
| `export_step` | Export body as STEP (supports bodies inside components) |
| `export_f3d` | Export design as Fusion archive |

### CAM / Manufacturing
| Tool | Description |
|------|-------------|
| `cam_create_setup` | Create a manufacturing setup (milling/turning/cutting) |
| `cam_create_operation` | Add a machining operation (face, contour, adaptive, drilling, etc.) |
| `cam_generate_toolpath` | Generate toolpaths for operations |
| `cam_post_process` | Post-process to G-code (fanuc, grbl, haas, etc.) |
| `cam_list_setups` | List all manufacturing setups |
| `cam_list_operations` | List operations in a setup |
| `cam_get_operation_info` | Get operation details (strategy, tool, parameters) |

### Code Execution
| Tool | Description |
|------|-------------|
| `execute_code` | Run arbitrary Python in Fusion (REPL-style) |

## MCP Protocol Features

- **Tool annotations** — each tool is tagged with `readOnlyHint`, `destructiveHint`, and `idempotentHint` so MCP clients can auto-approve safe operations
- **Resources** — `fusion360://status`, `fusion360://design`, `fusion360://parameters` for passive state inspection
- **Resource templates** — `fusion360://body/{name}`, `fusion360://component/{name}` for dynamic entity lookup
- **Prompts** — `create-box`, `model-threaded-bolt`, `sheet-metal-enclosure` workflow templates
- **Structured errors** — tool results include `isError=True` when the add-in reports failures
- **Mock mode** — `--mode mock` returns plausible test data without Fusion running (all responses include `"mode": "mock"`)

## Development

```bash
uv sync --dev       # install deps
uv run pytest -v    # run tests (171 tests)
uv run ruff check   # lint
```

## Notes

- All Fusion API units are **centimeters** (Fusion's internal unit).
- One operation per tool call. Batching multiple operations crashes the add-in.
- Commands time out after 30 seconds.
- Add-in logs to `~/fusion360mcp.log`.
- The `undo` tool includes a design-type safety guard — it checks before/after and auto-redoes if the undo would switch from parametric to direct mode.

## Acknowledgements

Inspired by [BlenderMCP](https://github.com/ahujasid/blender-mcp) — the socket bridge architecture originated there.

Also built on ideas from the existing Fusion 360 MCP ecosystem:
- [ArchimedesCrypto/fusion360-mcp-server](https://github.com/ArchimedesCrypto/fusion360-mcp-server)
- [Joe-Spencer/fusion-mcp-server](https://github.com/Joe-Spencer/fusion-mcp-server)
- [JustusBraitinger/FusionMCP](https://github.com/JustusBraitinger/FusionMCP)
- [zkbkb/fusion-mcp](https://github.com/zkbkb/fusion-mcp)
- [mycelia1/fusion360-mcp-server](https://github.com/mycelia1/fusion360-mcp-server)
- [sockcymbal/autodesk-fusion-mcp-python](https://github.com/sockcymbal/autodesk-fusion-mcp-python)

## License

MIT
