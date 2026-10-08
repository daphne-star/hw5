"""Check the MCP surface itself: what the server advertises, and that it cannot write.

`smoke_read_tools.py` calls the tool functions directly. This one goes through the MCP
server — the schemas a client would see, a tool invoked by name, and proof that the
read-only connection actually refuses a write.

Prints a readable summary and writes the same findings as JSON to
`output/mcp_smoke.json`, so the surface can be diffed after later changes.

Usage:
    python scripts/smoke_mcp_server.py
    python scripts/smoke_mcp_server.py --out output/elsewhere.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from mcp_server import db, tools, write_tools  # noqa: E402
from mcp_server.server import server  # noqa: E402

DEFAULT_OUT = PROJECT_ROOT / "output" / "mcp_smoke.json"


async def collect() -> dict:
    """Exercise the MCP surface and return everything found as plain JSON-able data."""
    report: dict = {
        "server": {"name": server.name, "version": server.version},
        "database": db.WORKING_DB.name,
        "shop_date": db.today(),
    }

    read_names = {t.__name__ for t in tools.READ_TOOLS}
    write_names = {t.__name__ for t in write_tools.WRITE_TOOLS}

    listed = await server.list_tools()
    report["tool_count"] = len(listed)
    report["tools"] = [
        {
            "name": tool.name,
            "kind": "read" if tool.name in read_names else "write",
            "summary": (tool.description or "").strip().splitlines()[0],
            "parameters": sorted(tool.input_schema.get("properties", {})),
            "required": sorted(tool.input_schema.get("required", [])),
            "read_only_hint": bool(tool.annotations and tool.annotations.read_only_hint),
        }
        for tool in listed
    ]
    # Every read tool must be hinted read-only and every write tool must not be, or a
    # client has no way to tell which half of the surface changes the shop.
    report["hints_correct"] = all(
        t["read_only_hint"] == (t["name"] in read_names) for t in report["tools"]
    )
    report["unregistered"] = sorted(
        (read_names | write_names) - {t["name"] for t in report["tools"]}
    )

    # One real call across the MCP boundary, to show what a client actually receives.
    call = await server.call_tool("list_invoices", {"status": "open"})
    report["sample_call"] = {
        "tool": "list_invoices",
        "arguments": {"status": "open"},
        "is_error": bool(call.is_error),
        "structured_content": call.structured_content,
    }

    # The read-only connection should refuse this. If it does not, the guard is fiction.
    check = {"statement": "UPDATE cash_accounts SET balance = 0"}
    try:
        with db.connect_readonly() as conn:
            conn.execute(check["statement"])
    except sqlite3.OperationalError as exc:
        check["refused"] = True
        check["sqlite_error"] = str(exc)
    else:
        check["refused"] = False
        check["sqlite_error"] = None
    report["read_only_check"] = check

    report["ok"] = (
        report["tool_count"] > 0
        and report["hints_correct"]
        and not report["unregistered"]
        and not report["sample_call"]["is_error"]
        and check["refused"]
    )
    return report


def summarize(report: dict) -> None:
    print(f"{report['tool_count']} tools advertised by {report['server']['name']}:\n")
    for kind in ("read", "write"):
        group = [t for t in report["tools"] if t["kind"] == kind]
        print(f"  -- {kind} ({len(group)}) --")
        for tool in group:
            params = ", ".join(tool["parameters"]) or "no arguments"
            print(f"  {tool['name']}({params})")
            print(f"      {tool['summary']}")
    print(f"\n  read_only hints correct: {report['hints_correct']}")

    sample = report["sample_call"]
    print(f"\nCalled {sample['tool']}{sample['arguments']} over the MCP boundary:")
    print(f"  is_error={sample['is_error']}, "
          f"{len(sample['structured_content'].get('data', []))} row(s) returned")

    check = report["read_only_check"]
    print("\nTried to write through the read-only connection:")
    if check["refused"]:
        print(f"  refused, as it should be: {check['sqlite_error']}")
    else:
        print("  WRITE SUCCEEDED — the connection is not read-only.")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT,
                        help="where to write the JSON report")
    args = parser.parse_args()

    report = await collect()
    summarize(report)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"\nWrote {args.out.relative_to(PROJECT_ROOT)} (ok={report['ok']}).")

    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
