"""Write `output/resolved_tickets.json` — where every ticket ended up and how it got there.

Two sources, used for different things on purpose:

* **The database** is authoritative for anything factual — final status, payments made,
  approvals and who decided them, drafts written. These are the shop's own records.
* **The audit trail** supplies what each agent contributed, taken from the most recent
  run against each ticket. An agent's account of its own work is a claim, so the
  `what_changed` block beside it comes from the database rather than from the agent.

Only the latest run per ticket is summarized, because an earlier run against a shop
state that has since been reset does not describe how the ticket reached where it is.
Earlier runs are counted, not discarded — they are all still in the audit trail.

Usage:
    python scripts/summarize_tickets.py
    python scripts/summarize_tickets.py --out output/elsewhere.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend import audit  # noqa: E402
from mcp_server import db  # noqa: E402

DEFAULT_OUT = PROJECT_ROOT / "output" / "resolved_tickets.json"

# How far an agent's closing remark is carried into the summary. The whole thing is in
# the audit trail; this is a readable extract, not a replacement for it.
REPORT_CHARS = 600


def latest_run_per_ticket(runs: list[dict]) -> dict[int, dict]:
    """The most recent run against each ticket, plus how many came before it."""
    latest: dict[int, dict] = {}
    counts: dict[int, int] = {}
    for run in runs:
        ticket_id = run["ticket_id"]
        counts[ticket_id] = counts.get(ticket_id, 0) + 1
        latest[ticket_id] = run
    for ticket_id, run in latest.items():
        run["_earlier_runs"] = counts[ticket_id] - 1
    return latest


def contributions(run: dict | None) -> list[dict]:
    """What each agent actually did in this run, in the order they first acted."""
    if run is None:
        return []

    entries = run.get("trace", {}).get("entries", [])
    agents: list[str] = []
    for entry in entries:
        if entry["agent"] not in agents:
            agents.append(entry["agent"])

    summary = []
    for agent in agents:
        mine = [e for e in entries if e["agent"] == agent]
        messages = [e for e in mine if e["kind"] == "message"]
        summary.append(
            {
                "agent": agent,
                "tools_used": [e["tool"] for e in mine if e["kind"] == "tool_call"],
                "delegated_to": [e["to_agent"] for e in mine if e["kind"] == "delegation"],
                "refusals": [
                    {"tool": e.get("tool"), "code": e.get("error_code"),
                     "message": (e.get("result", {}).get("error") or {}).get("message")}
                    for e in mine if e["kind"] == "refusal"
                ],
                "limits_hit": [e.get("limit") for e in mine if e["kind"] == "limit"],
                "reported": messages[-1]["text"][:REPORT_CHARS] if messages else None,
            }
        )
    return summary


def human_approvals_for(ticket_id: int, approvals: list[dict], payments: list[dict]) -> list[dict]:
    """Every approval raised against this ticket, and what the human did with it.

    Read from the database rather than the audit trail, so a `pending` here means the
    money genuinely has not moved — not merely that no one logged a decision.
    """
    by_payment = {p["id"]: p for p in payments}
    out = []
    for approval in approvals:
        if approval["ticket_id"] != ticket_id:
            continue
        payment = by_payment.get(approval["consumed_by_payment_id"])
        out.append(
            {
                "approval_id": approval["id"],
                "kind": approval["kind"],
                "amount": approval["amount"],
                "account": approval["account"],
                "requested_by": approval["requested_by"],
                "reason": approval["reason"],
                "status": approval["status"],
                "decided_by": approval["decided_by"],
                "decided_at": approval["decided_at"],
                "executed": (
                    {
                        "payment_id": payment["id"],
                        "amount": payment["amount"],
                        "paid_at": payment["paid_at"],
                        "approved_by": payment["approved_by"],
                    }
                    if payment
                    else None
                ),
            }
        )
    return out


def still_open(ticket: dict, approvals: list[dict], purchase_orders: list[dict]) -> list[str]:
    """What is keeping this ticket from being finished, in plain language."""
    reasons = []

    pending = [
        a for a in approvals
        if a["ticket_id"] == ticket["id"] and a["status"] == "pending"
    ]
    for approval in pending:
        reasons.append(
            f"Approval {approval['id']} for ${approval['amount']:,.2f} "
            f"({approval['kind'].replace('_', ' ')}) is waiting on a human."
        )

    approved_orders = [
        a for a in approvals
        if a["ticket_id"] == ticket["id"]
        and a["kind"] == "purchase_order"
        and a["status"] == "approved"
    ]
    placed = {p["ticket_id"] for p in purchase_orders}
    if approved_orders and ticket["id"] not in placed:
        reasons.append("A purchase order was approved but has not been placed yet.")

    if ticket["status"] == "blocked" and not reasons:
        reasons.append("Marked blocked by the Boss; see the ticket notes for what is in the way.")

    return reasons


def build() -> dict:
    tickets = db.query("SELECT * FROM tickets ORDER BY id")
    approvals = db.query("SELECT * FROM approvals ORDER BY id")
    payments = db.query("SELECT * FROM payments ORDER BY id")
    orders = db.query("SELECT * FROM purchase_orders ORDER BY id")
    drafts = db.query("SELECT * FROM drafts ORDER BY id")
    cash = db.query("SELECT name, balance, date FROM cash_accounts")

    runs = latest_run_per_ticket(audit.read_runs())

    summarized = []
    for ticket in tickets:
        run = runs.get(ticket["id"])
        summarized.append(
            {
                "id": ticket["id"],
                "subject": ticket["subject"],
                "type": ticket["type"],
                "requester": ticket["requester"],
                "final_status": ticket["status"],
                "assignee": ticket["assignee"],
                "outcome": {
                    "summary": run.get("final_answer") if run else None,
                    "decided_by_agent": run.get("entry_agent") if run else None,
                    "still_open": still_open(ticket, approvals, orders),
                    "notes": ticket["notes"],
                },
                "what_changed": {
                    "payments": [
                        {k: p[k] for k in ("id", "kind", "amount", "account", "paid_at",
                                           "approved_by")}
                        for p in payments
                        if any(
                            a["ticket_id"] == ticket["id"]
                            and a["consumed_by_payment_id"] == p["id"]
                            for a in approvals
                        )
                    ],
                    "purchase_orders": [
                        {k: o[k] for k in ("id", "vendor_id", "sku", "size", "qty",
                                           "total_cost", "expected_arrival", "status")}
                        for o in orders
                        if o["ticket_id"] == ticket["id"]
                    ],
                    "drafts": [
                        {k: d[k] for k in ("id", "recipient", "subject", "written_by")}
                        for d in drafts
                        if d["ticket_id"] == ticket["id"]
                    ],
                },
                "run": (
                    {
                        "run_id": run["run_id"],
                        "recorded_at": run["recorded_at"],
                        "mode": run["mode"],
                        "entry_agent": run["entry_agent"],
                        "agents_involved": run["agents_involved"],
                        "handoffs": run["handoffs"],
                        "tool_calls_used": run["tool_calls_used"],
                        "delegations_used": run["delegations_used"],
                        "earlier_runs_on_this_ticket": run["_earlier_runs"],
                    }
                    if run
                    else None
                ),
                "agent_contributions": contributions(run),
                "human_approvals": human_approvals_for(ticket["id"], approvals, payments),
            }
        )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "shop_date": db.today(),
        "sources": {
            "facts": "data/campus_customs_new.db — final status, payments, approvals, drafts",
            "agent_work": "output/audit_trail.json — latest run per ticket",
            "note": (
                "Agent contributions are what each agent said and did. The what_changed "
                "block is read from the database, so a claim and its effect can be "
                "compared rather than conflated."
            ),
        },
        "cash": {
            "accounts": cash,
            "total_balance": round(sum(a["balance"] for a in cash), 2),
        },
        "totals": {
            "tickets": len(tickets),
            "by_status": {
                status: sum(1 for t in tickets if t["status"] == status)
                for status in sorted({t["status"] for t in tickets})
            },
            "payments_made": len(payments),
            "paid_out": round(sum(p["amount"] for p in payments), 2),
            "approvals_pending": sum(1 for a in approvals if a["status"] == "pending"),
            "purchase_orders_placed": len(orders),
            "drafts_written": len(drafts),
            "messages_sent": 0,
        },
        "tickets": summarized,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    report = build()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")

    print(f"Wrote {args.out.relative_to(PROJECT_ROOT)}")
    for ticket in report["tickets"]:
        agents = ", ".join(c["agent"] for c in ticket["agent_contributions"]) or "no run recorded"
        approvals = ticket["human_approvals"]
        decided = sum(1 for a in approvals if a["status"] != "pending")
        print(
            f"  #{ticket['id']} {ticket['final_status']:<9} {agents}"
            f"  | approvals: {decided}/{len(approvals)} decided"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
