"""The backend's connection to the MCP server.

The agents do not import `mcp_server.tools` and call it directly. They talk to the MCP
server as a separate process over stdio, the way any MCP client would — so the tool
schemas the model sees are the ones the server actually publishes, and a tool that is
not registered there simply does not exist to an agent.

One session is opened per ticket run and closed at the end.

The session is held open by `open_bridge()` rather than by a class with `__aenter__` and
`__aexit__`. The MCP stdio client runs on anyio task groups, which must be entered and
exited in the same task; stashing them in an `AsyncExitStack` and closing it from
another method fails with "attempted to exit cancel scope in a different task". Nesting
the `async with` blocks inside one generator keeps the whole lifetime in one task.
"""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class McpBridge:
    """An open MCP session, plus the tool schemas translated for the model API."""

    def __init__(self, session: ClientSession) -> None:
        self._session = session
        self._schemas: dict[str, dict[str, Any]] = {}

    async def load_schemas(self) -> None:
        """Fetch the server's tool list and translate it into model-API tool definitions."""
        listed = await self._session.list_tools()
        self._schemas = {
            tool.name: {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description or "",
                    "parameters": _schema_of(tool),
                },
            }
            for tool in listed.tools
        }

    @property
    def tool_names(self) -> list[str]:
        return sorted(self._schemas)

    def schemas_for(self, names: list[str]) -> list[dict[str, Any]]:
        """The tool definitions for the named tools, in the order given.

        A name with no matching tool on the server is dropped rather than guessed at, so
        a typo in an agent's allowlist shows up as a missing tool, not a fake one.
        """
        return [self._schemas[name] for name in names if name in self._schemas]

    def missing(self, names: list[str]) -> list[str]:
        """Names in an allowlist that the server does not actually publish."""
        return [name for name in names if name not in self._schemas]

    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Invoke a tool and return its JSON payload in the server's standard envelope."""
        result = await self._session.call_tool(name, arguments)

        structured = getattr(result, "structuredContent", None) or getattr(
            result, "structured_content", None
        )
        if structured is not None:
            return structured

        # No structured content means the transport itself failed. Keep the envelope
        # shape so an agent handles it the same way as any other refusal.
        text = " ".join(
            block.text for block in result.content if getattr(block, "type", None) == "text"
        )
        return {
            "ok": False,
            "error": {"code": "internal_error", "message": text or "Tool returned nothing."},
        }


def _schema_of(tool: Any) -> dict[str, Any]:
    """The tool's JSON input schema, under whichever name this SDK version uses."""
    return getattr(tool, "inputSchema", None) or getattr(tool, "input_schema", None) or {
        "type": "object",
        "properties": {},
    }


@asynccontextmanager
async def open_bridge() -> AsyncIterator[McpBridge]:
    """Start the MCP server as a subprocess and yield a connected bridge."""
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_server.server"],
        cwd=str(PROJECT_ROOT),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            bridge = McpBridge(session)
            await bridge.load_schemas()
            yield bridge
