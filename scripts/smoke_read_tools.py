"""Call every read tool once and print what comes back.

Not a test suite — a look at real output, so the data can be eyeballed before any agent
is pointed at it. Calls the tool functions directly rather than over MCP transport;
`scripts/smoke_mcp_server.py` covers the transport side.

Usage:
    python scripts/smoke_read_tools.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp_server import tools  # noqa: E402

# (label, callable, kwargs) — one representative call per tool, plus two deliberate
# misses at the end so the error shape is visible too.
CALLS = [
    ("get_today", tools.get_today, {}),
    ("list_tickets(status='open')", tools.list_tickets, {"status": "open"}),
    ("get_ticket(101)", tools.get_ticket, {"ticket_id": 101}),
    ("get_inventory('CC-TEE-WHITE', 'S')", tools.get_inventory,
     {"sku": "CC-TEE-WHITE", "size": "S"}),
    ("get_pricing('CC-HOOD-NAVY')", tools.get_pricing, {"sku": "CC-HOOD-NAVY"}),
    ("list_vendors(specialty_contains='apparel')", tools.list_vendors,
     {"specialty_contains": "apparel"}),
    ("list_leases()", tools.list_leases, {}),
    ("get_cash_accounts()", tools.get_cash_accounts, {}),
    ("list_invoices(status='open')", tools.list_invoices, {"status": "open"}),
    ("list_payments()", tools.list_payments, {}),
    ("get_ticket(999)  [expected miss]", tools.get_ticket, {"ticket_id": 999}),
    ("get_inventory('CC-NOPE')  [expected miss]", tools.get_inventory, {"sku": "CC-NOPE"}),
]


def main() -> int:
    failures = []

    for label, func, kwargs in CALLS:
        print("=" * 78)
        print(label)
        print("-" * 78)
        result = func(**kwargs)
        print(json.dumps(result, indent=2, ensure_ascii=False))

        expected_miss = "[expected miss]" in label
        if result["ok"] == expected_miss:
            failures.append(label)

    print("=" * 78)
    if failures:
        print(f"Unexpected results from: {', '.join(failures)}")
        return 1

    print(f"All {len(CALLS)} calls returned the shape they should.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
