"""Run one ticket from the command line and print the trace as a readable timeline.

    python scripts/run_ticket.py 101                 # full team, Boss first
    python scripts/run_ticket.py 101 --agent Inventory
    python scripts/run_ticket.py 101 --out output/ticket_101.json

Useful for watching a handoff happen without the dashboard in the way.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.runner import run_single_agent, run_ticket  # noqa: E402

GLYPH = {
    "tool_call": "  tool ",
    "refusal": "  REFUSED",
    "delegation": "->",
    "message": "  says ",
    "limit": "  LIMIT",
    "error": "  ERROR",
}


def show(result: dict) -> None:
    trace = result["trace"]

    print(f"\n{'=' * 78}\nTicket {trace['ticket_id']}\n{'=' * 78}")
    for entry in trace["entries"]:
        indent = "  " * (entry["depth"] - 1)
        kind = entry["kind"]
        head = f"{entry['seq']:>3} {indent}{entry['agent']:<17}"

        if kind == "delegation":
            print(f"{head} -> {entry['to_agent']}: {entry['task'][:90]}")
        elif kind in ("tool_call", "refusal"):
            args = json.dumps(entry["arguments"])[:70]
            tail = f" [{entry['error_code']}]" if kind == "refusal" else ""
            print(f"{head} {GLYPH[kind]} {entry['tool']}({args}){tail}")
        elif kind == "message":
            text = " ".join(entry["text"].split())[:110]
            print(f"{head} {GLYPH[kind]} {text}")
        else:
            print(f"{head} {GLYPH[kind]} {entry.get('detail', '')[:100]}")

    print(f"\n{'-' * 78}")
    print(f"agents involved : {', '.join(trace['agents_involved'])}")
    print(f"handoffs        : {' | '.join(trace['handoffs']) or 'none'}")
    print(f"tool calls      : {trace['tool_calls_used']}")
    print(f"delegations     : {trace['delegations_used']}")
    print(f"refusals        : {trace['refusal_count']}")
    print(f"\nFinal answer ({result['agent']}):\n{result['answer']}")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ticket_id", type=int)
    parser.add_argument("--agent", help="run this agent alone instead of the full team")
    parser.add_argument("--task", help="override the task given to that agent")
    parser.add_argument("--out", type=Path, help="also write the full result as JSON")
    args = parser.parse_args()

    if args.agent:
        result = await run_single_agent(args.ticket_id, args.agent, args.task)
    else:
        result = await run_ticket(args.ticket_id)

    show(result)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
        print(f"\nWrote {args.out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
