"""The permanent record of every agent run.

`output/audit_trail.json` accumulates one entry per run, for the life of the project. It
is **append-only**: existing entries are never edited, reordered, or removed, and
`scripts/reset_db.py` does not touch it. Resetting the shop rewinds the data; it does
not rewind the history of what the agents did to it. That separation is the point — if
a run did something wrong, the evidence survives the reset that hides it.

Three things protect the file:

* the writer refuses to write a list shorter than the one on disk;
* writes go to a temp file and are moved into place, so an interrupted write cannot
  leave a half-file behind;
* a file that will not parse is moved aside with a timestamp rather than overwritten.

Each entry records what the run did *and* what changed in the shop while it ran, taken
from before-and-after snapshots. An agent's own summary of what it did is a claim; the
snapshot diff is what actually happened.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp_server import db

PROJECT_ROOT = Path(__file__).resolve().parent.parent
AUDIT_PATH = PROJECT_ROOT / "output" / "audit_trail.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Shop snapshots
# ---------------------------------------------------------------------------


def snapshot() -> dict[str, Any]:
    """The state of everything a run could change, read straight from the database."""
    try:
        return {
            "shop_date": db.today(),
            "cash": {
                row["name"]: row["balance"]
                for row in db.query("SELECT name, balance FROM cash_accounts")
            },
            "payment_ids": [r["id"] for r in db.query("SELECT id FROM payments ORDER BY id")],
            "approval_ids": [r["id"] for r in db.query("SELECT id FROM approvals ORDER BY id")],
            "purchase_order_ids": [
                r["id"] for r in db.query("SELECT id FROM purchase_orders ORDER BY id")
            ],
            "draft_ids": [r["id"] for r in db.query("SELECT id FROM drafts ORDER BY id")],
            "open_invoice_ids": [
                r["id"] for r in db.query("SELECT id FROM invoices WHERE status = 'open'")
            ],
            "ticket_statuses": {
                str(r["id"]): r["status"] for r in db.query("SELECT id, status FROM tickets")
            },
        }
    except Exception as exc:  # a snapshot must never be the thing that breaks a run
        return {"error": f"{type(exc).__name__}: {exc}"}


def _diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """What the run actually changed, as opposed to what it says it changed."""
    if "error" in before or "error" in after:
        return {"error": "snapshot unavailable"}

    cash_moved = {
        name: {
            "before": balance,
            "after": after["cash"].get(name),
            "delta": round(after["cash"].get(name, balance) - balance, 2),
        }
        for name, balance in before["cash"].items()
        if after["cash"].get(name) != balance
    }

    status_changes = {
        ticket_id: {"before": status, "after": after["ticket_statuses"].get(ticket_id)}
        for ticket_id, status in before["ticket_statuses"].items()
        if after["ticket_statuses"].get(ticket_id) != status
    }

    def added(key: str) -> list[int]:
        return sorted(set(after[key]) - set(before[key]))

    return {
        "cash_moved": cash_moved,
        "payments_created": added("payment_ids"),
        "approvals_created": added("approval_ids"),
        "purchase_orders_created": added("purchase_order_ids"),
        "drafts_created": added("draft_ids"),
        "invoices_closed": sorted(set(before["open_invoice_ids"]) - set(after["open_invoice_ids"])),
        "ticket_status_changes": status_changes,
    }


# ---------------------------------------------------------------------------
# The file
# ---------------------------------------------------------------------------


def _load() -> dict[str, Any]:
    """Read the audit file, preserving anything unreadable rather than clobbering it."""
    if not AUDIT_PATH.exists():
        return {"created_at": _now(), "note": "Append-only. Entries are never edited or removed.",
                "runs": [], "human_actions": []}

    try:
        data = json.loads(AUDIT_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        quarantine = AUDIT_PATH.with_suffix(f".corrupt-{datetime.now():%Y%m%d-%H%M%S}.json")
        AUDIT_PATH.replace(quarantine)
        return {
            "created_at": _now(),
            "note": "Append-only. Entries are never edited or removed.",
            "recovered_from": quarantine.name,
            "runs": [],
            "human_actions": [],
        }

    if not isinstance(data, dict) or not isinstance(data.get("runs"), list):
        return {"created_at": _now(), "note": "Append-only.", "runs": [], "human_actions": []}
    data.setdefault("human_actions", [])
    return data


def _save(data: dict[str, Any], previous: dict[str, int]) -> None:
    """Write atomically, and only if nothing would be lost from either list."""
    for key, count in previous.items():
        if len(data.get(key, [])) < count:
            raise RuntimeError(
                f"Refusing to write {len(data.get(key, []))} {key} over {count} on disk. "
                "The audit trail is append-only."
            )

    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = AUDIT_PATH.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    os.replace(temp, AUDIT_PATH)


def read_runs() -> list[dict[str, Any]]:
    """Every run recorded so far, oldest first. Read-only; never modifies the file."""
    if not AUDIT_PATH.exists():
        return []
    try:
        data = json.loads(AUDIT_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    runs = data.get("runs") if isinstance(data, dict) else None
    return runs if isinstance(runs, list) else []


def append_run(
    *,
    ticket_id: int,
    entry_agent: str,
    mode: str,
    answer: str,
    trace: dict[str, Any],
    before: dict[str, Any],
    after: dict[str, Any],
    model: str,
) -> dict[str, Any]:
    """Add one run to the audit trail and return the entry that was written.

    Never raises into the caller's path — a run that succeeded should not be reported
    as failed because the log could not be written. A write failure is recorded in the
    returned entry instead.
    """
    data = _load()
    previous = {"runs": len(data["runs"]), "human_actions": len(data["human_actions"])}

    entry = {
        "run_id": f"run_{uuid.uuid4().hex[:12]}",
        "recorded_at": _now(),
        "mode": mode,  # "full_team" or "single_agent"
        "ticket_id": ticket_id,
        "entry_agent": entry_agent,
        "model": model,
        "shop_date": before.get("shop_date"),
        "started_at": trace.get("started_at"),
        "finished_at": trace.get("finished_at"),
        "agents_involved": trace.get("agents_involved", []),
        "handoffs": trace.get("handoffs", []),
        "tool_calls_used": trace.get("tool_calls_used", 0),
        "delegations_used": trace.get("delegations_used", 0),
        "refusals": [
            {
                "seq": e["seq"],
                "agent": e["agent"],
                "tool": e.get("tool"),
                "code": e.get("error_code"),
                "message": (e.get("result", {}).get("error", {}) or {}).get("message"),
            }
            for e in trace.get("entries", [])
            if e["kind"] == "refusal"
        ],
        "limits_hit": [
            {"seq": e["seq"], "agent": e["agent"], "limit": e.get("limit"),
             "detail": e.get("detail")}
            for e in trace.get("entries", [])
            if e["kind"] == "limit"
        ],
        "errors": [
            {"seq": e["seq"], "agent": e["agent"], "detail": e.get("detail")}
            for e in trace.get("entries", [])
            if e["kind"] == "error"
        ],
        "changes": _diff(before, after),
        "final_answer": answer,
        "trace": trace,
    }

    data["runs"].append(entry)
    data["last_appended_at"] = entry["recorded_at"]
    data["run_count"] = len(data["runs"])

    try:
        _save(data, previous)
    except Exception as exc:
        entry["audit_write_error"] = f"{type(exc).__name__}: {exc}"

    return entry


def append_human_action(
    *,
    action: str,
    decided_by: str,
    approval: dict[str, Any],
    executed: dict[str, Any] | None,
    before: dict[str, Any],
    after: dict[str, Any],
) -> dict[str, Any]:
    """Record a decision a person made, with the same guarantees as an agent run.

    A human approving $840 is the single most consequential thing that happens in this
    shop, and until this existed it was recorded only as `payments.approved_by` — a
    column the same process can rewrite. It belongs in the append-only trail beside the
    agent runs, with the before-and-after diff showing what the click actually moved.
    """
    data = _load()
    previous = {"runs": len(data["runs"]), "human_actions": len(data["human_actions"])}

    result = (executed or {}).get("result", {}) if executed else {}
    entry = {
        "action_id": f"act_{uuid.uuid4().hex[:12]}",
        "recorded_at": _now(),
        "action": action,
        "decided_by": decided_by,
        "approval_id": approval.get("id"),
        "kind": approval.get("kind"),
        "amount": approval.get("amount"),
        "account": approval.get("account"),
        "ticket_id": approval.get("ticket_id"),
        "reason": approval.get("reason"),
        "requested_by": approval.get("requested_by"),
        "shop_date": before.get("shop_date"),
        "executed_action": (executed or {}).get("action"),
        "executed_ok": bool(result.get("ok")) if executed else None,
        "executed_error": (result.get("error") or {}).get("message") if executed else None,
        "changes": _diff(before, after),
    }

    data["human_actions"].append(entry)
    data["last_appended_at"] = entry["recorded_at"]
    data["human_action_count"] = len(data["human_actions"])

    try:
        _save(data, previous)
    except Exception as exc:
        entry["audit_write_error"] = f"{type(exc).__name__}: {exc}"

    return entry


def read_human_actions() -> list[dict[str, Any]]:
    """Every human decision recorded so far, oldest first."""
    if not AUDIT_PATH.exists():
        return []
    try:
        data = json.loads(AUDIT_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    actions = data.get("human_actions") if isinstance(data, dict) else None
    return actions if isinstance(actions, list) else []
