"""
Fusion360 MCP Server — stdio transport.

Bridges Claude Code ↔ Fusion 360 add-in via TCP socket on localhost.
Supports ``--mode mock`` for testing without Fusion running.
"""

import json
import logging
import os
import re

import anyio
import click
import mcp.types as types
from mcp.server.lowlevel import Server

from .connection import get_connection, reset_connection
from .mock import mock_command
from .tools import get_tool_by_name, get_tool_list

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("fusion360_mcp.server")


def _send(
    mode: str,
    command_type: str,
    params: dict | None = None,
    *,
    host: str = "localhost",
    port: int = 9876,
) -> dict:
    """Route a command through either the real TCP connection or mock."""
    if mode == "mock":
        return mock_command(command_type, params)
    conn = get_connection(host=host, port=port)
    return conn.send_command(command_type, params)


@click.command()
@click.option("--mode", type=click.Choice(["socket", "mock"]),
              default="socket",
              help="'socket' connects to Fusion, 'mock' returns test data")
@click.option("--host", type=str,
              default=lambda: os.environ.get("FUSION_MCP_HOST", "localhost"),
              help=("Host where the Fusion 360 add-in listens "
                    "(env: FUSION_MCP_HOST). Use the Windows LAN IP when "
                    "the MCP server runs on a different machine."))
@click.option("--port", type=int,
              default=lambda: int(os.environ.get("FUSION_MCP_PORT", "9876")),
              help="TCP port the Fusion 360 add-in listens on "
                   "(env: FUSION_MCP_PORT)")
def main(mode: str, host: str, port: int) -> int:
    """Fusion360 MCP Server — connects Claude to Fusion 360."""

    app = Server("fusion360-mcp-server")

    # ── tools ────────────────────────────────────────────────────────

    @app.list_tools()
    async def list_tools() -> list[types.Tool]:
        return get_tool_list()

    @app.call_tool()
    async def call_tool(
        name: str, arguments: dict,
    ) -> list[types.ContentBlock]:
        tool_def = get_tool_by_name(name)
        if not tool_def:
            raise ValueError(f"Unknown tool: {name}")

        try:
            result = _send(mode, name, arguments, host=host, port=port)
        except Exception as exc:
            reset_connection()
            content = [types.TextContent(
                type="text",
                text=f"Error ({name}): {exc}\n\n"
                     "Make sure Fusion 360 is running and the "
                     "Fusion360MCP add-in is started.",
            )]
            return types.CallToolResult(
                content=content, isError=True,
            )

        # Detect errors from the add-in
        is_error = False
        if isinstance(result, dict):
            status = result.get("status", "")
            if status == "error" or "error" in result:
                is_error = True

        # Format response
        lines = [f"**{name}** {'ERROR' if is_error else 'OK'}"]
        if isinstance(result, dict):
            for k, v in result.items():
                lines.append(f"  {k}: {v}")
        else:
            lines.append(f"  {result}")

        content = [types.TextContent(type="text", text="\n".join(lines))]
        if is_error:
            return types.CallToolResult(
                content=content, isError=True,
            )
        return content

    # ── resources ────────────────────────────────────────────────────

    @app.list_resources()
    async def list_resources() -> list[types.Resource]:
        return [
            types.Resource(
                uri="fusion360://status",
                name="Connection Status",
                description="Check whether Fusion 360 is reachable",
                mimeType="application/json",
            ),
            types.Resource(
                uri="fusion360://design",
                name="Design Tree",
                description="Full design tree: bodies, sketches, features, components",
                mimeType="application/json",
            ),
            types.Resource(
                uri="fusion360://parameters",
                name="User Parameters",
                description="All user-defined parameters in the design",
                mimeType="application/json",
            ),
        ]

    @app.read_resource()
    async def read_resource(uri: str) -> str:
        if uri == "fusion360://status":
            try:
                result = _send(mode, "ping", port=port)
                return json.dumps(
                    {"connected": True, "ping": result}, indent=2,
                )
            except Exception as exc:
                reset_connection()
                return json.dumps(
                    {"connected": False, "error": str(exc)}, indent=2,
                )

        if uri == "fusion360://design":
            try:
                result = _send(mode, "get_scene_info", port=port)
                return json.dumps(result, indent=2)
            except Exception as exc:
                reset_connection()
                return json.dumps({"error": str(exc)}, indent=2)

        if uri == "fusion360://parameters":
            try:
                result = _send(
                    mode, "get_parameters", port=port,
                )
                return json.dumps(result, indent=2)
            except Exception as exc:
                reset_connection()
                return json.dumps({"error": str(exc)}, indent=2)

        # ── resource template matches ─────────────────────────────
        body_match = re.match(r"^fusion360://body/(.+)$", uri)
        if body_match:
            name = body_match.group(1)
            try:
                result = _send(
                    mode, "get_object_info",
                    {"name": name}, port=port,
                )
                return json.dumps(result, indent=2)
            except Exception as exc:
                reset_connection()
                return json.dumps({"error": str(exc)}, indent=2)

        comp_match = re.match(
            r"^fusion360://component/(.+)$", uri,
        )
        if comp_match:
            name = comp_match.group(1)
            try:
                result = _send(
                    mode, "get_object_info",
                    {"name": name}, port=port,
                )
                return json.dumps(result, indent=2)
            except Exception as exc:
                reset_connection()
                return json.dumps({"error": str(exc)}, indent=2)

        raise ValueError(f"Unknown resource: {uri}")

    # ── resource templates ────────────────────────────────────────────

    @app.list_resource_templates()
    async def list_resource_templates() -> list[types.ResourceTemplate]:
        return [
            types.ResourceTemplate(
                uriTemplate="fusion360://body/{name}",
                name="Body Info",
                description="Detailed info about a named body",
                mimeType="application/json",
            ),
            types.ResourceTemplate(
                uriTemplate="fusion360://component/{name}",
                name="Component Info",
                description="Info about a named component",
                mimeType="application/json",
            ),
        ]

    # ── prompts ───────────────────────────────────────────────────────

    _PROMPTS = {
        "create-box": types.Prompt(
            name="create-box",
            description=(
                "Guide for creating a parametric box in Fusion 360"
            ),
            arguments=[
                types.PromptArgument(
                    name="length",
                    description="Box length in cm",
                    required=False,
                ),
                types.PromptArgument(
                    name="width",
                    description="Box width in cm",
                    required=False,
                ),
                types.PromptArgument(
                    name="height",
                    description="Box height in cm",
                    required=False,
                ),
            ],
        ),
        "model-threaded-bolt": types.Prompt(
            name="model-threaded-bolt",
            description=(
                "Step-by-step guide for modeling a "
                "threaded bolt in Fusion 360"
            ),
            arguments=[
                types.PromptArgument(
                    name="designation",
                    description=(
                        "Thread designation (e.g. M10x1.5)"
                    ),
                    required=False,
                ),
            ],
        ),
        "sheet-metal-enclosure": types.Prompt(
            name="sheet-metal-enclosure",
            description=(
                "Guide for creating a sheet metal enclosure"
            ),
            arguments=[
                types.PromptArgument(
                    name="length",
                    description="Enclosure length in cm",
                    required=False,
                ),
                types.PromptArgument(
                    name="width",
                    description="Enclosure width in cm",
                    required=False,
                ),
                types.PromptArgument(
                    name="height",
                    description="Enclosure height in cm",
                    required=False,
                ),
            ],
        ),
    }

    @app.list_prompts()
    async def list_prompts() -> list[types.Prompt]:
        return list(_PROMPTS.values())

    @app.get_prompt()
    async def get_prompt(
        name: str, arguments: dict | None = None,
    ) -> types.GetPromptResult:
        prompt = _PROMPTS.get(name)
        if not prompt:
            raise ValueError(f"Unknown prompt: {name}")
        args = arguments or {}

        if name == "create-box":
            length = args.get("length", "10")
            width = args.get("width", "5")
            height = args.get("height", "3")
            text = (
                f"Create a parametric box in Fusion 360:\n"
                f"1. create_sketch on xy plane\n"
                f"2. draw_rectangle width={width} height={length}\n"
                f"3. extrude height={height}\n"
                f"4. get_scene_info to verify"
            )
        elif name == "model-threaded-bolt":
            desig = args.get("designation", "M10x1.5")
            text = (
                f"Model a threaded bolt ({desig}):\n"
                f"1. create_sketch on xy plane\n"
                f"2. draw_circle for bolt shaft\n"
                f"3. extrude to bolt length\n"
                f"4. create_thread designation={desig}\n"
                f"5. Create hex head sketch + extrude\n"
                f"6. chamfer head edges"
            )
        elif name == "sheet-metal-enclosure":
            length = args.get("length", "20")
            width = args.get("width", "10")
            height = args.get("height", "5")
            text = (
                f"Create a sheet metal enclosure "
                f"({length}x{width}x{height} cm):\n"
                f"1. create_sketch on xy plane\n"
                f"2. draw_rectangle {width}x{length}\n"
                f"3. extrude to sheet thickness\n"
                f"4. create_flange on each edge\n"
                f"5. flat_pattern to verify unfold"
            )
        else:
            text = f"No template for prompt: {name}"

        return types.GetPromptResult(
            description=prompt.description,
            messages=[
                types.PromptMessage(
                    role="user",
                    content=types.TextContent(
                        type="text", text=text,
                    ),
                ),
            ],
        )

    # ── run ──────────────────────────────────────────────────────────

    from mcp.server.stdio import stdio_server

    async def arun():
        async with stdio_server() as streams:
            await app.run(streams[0], streams[1],
                          app.create_initialization_options())

    anyio.run(arun)
    return 0
