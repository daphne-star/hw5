"""MCP server exposing the Campus Customs shop database.

Every tool registered here is read-only and marked as such with `readOnlyHint`, so a
client can tell at a glance that nothing on this surface changes the shop. Write tools
arrive later and will be registered separately.

Run it over stdio:

    python -m mcp_server.server
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from . import tools, write_tools

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True)

# Writes change the shop. Not destructive in the "deletes data" sense — nothing here
# removes a row — but each one is a real action a human will see on the board.
WRITES = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False)

server = MCPServer(
    name="campus-customs",
    title="Campus Customs shop",
    version="0.1.0",
    instructions=(
        "Tools for the Campus Customs shop database.\n\n"
        "The shop's today comes from get_today, not from the real calendar. Use it for "
        "every judgment about what is due, overdue, or able to arrive in time.\n\n"
        "Stock is tracked per (sku, size) pair; stock in another size cannot fill an "
        "order. Vendors are matched to products by their specialty text, and a vendor "
        "with an open invoice against them will not ship anything new.\n\n"
        "Cash is a single stored balance and only ever goes down. No payment happens "
        "without an approved approval for that exact amount, and the shop never emails "
        "or calls anyone — customer messages are saved as drafts.\n\n"
        "Every tool returns {ok: true, data: ...} or {ok: false, error: {code, message}}. "
        "A refusal is a correct answer, not an obstacle: read the message, which says "
        "what would have to change, and act on that instead of retrying."
    ),
)

for _tool in tools.READ_TOOLS:
    server.add_tool(_tool, annotations=READ_ONLY, structured_output=True)

for _tool in write_tools.WRITE_TOOLS:
    server.add_tool(_tool, annotations=WRITES, structured_output=True)


def main() -> None:
    server.run()


if __name__ == "__main__":
    main()
