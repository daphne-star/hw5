"""What the routes read from and write to the shop, all of it through the MCP server.

The API does not query SQLite directly. Every read and every write opens an MCP session
and calls the same tools the agents call, so the dashboard and the agents cannot drift
apart — and a rule enforced in a tool applies to a human clicking Approve exactly as it
applies to an agent.

Opening a session per request is wasteful at scale and correct here: the shop has 24
rows, and a session that outlives a request would need its own lifecycle management for
no benefit at this size.
"""

from __future__ import annotations

from typing import Any

from .mcp_bridge import open_bridge


async def call(name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """Call one MCP tool and return its envelope."""
    async with open_bridge() as bridge:
        return await bridge.call(name, arguments or {})


async def call_many(calls: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    """Call several tools over one session, in order."""
    async with open_bridge() as bridge:
        return [await bridge.call(name, args) for name, args in calls]


def unwrap(result: dict[str, Any], default: Any = None) -> Any:
    """The `data` of a successful envelope, or `default` if the call was refused."""
    return result.get("data") if result.get("ok") else default
