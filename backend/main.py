"""FastAPI backend for Campus Customs.

The browser talks to this; this talks to Portkey and to the MCP server. The API key
lives here and goes no further — no route returns it, no response embeds it, and the
only place it is read is `config.get_client()`. `/api/health` reports whether a key is
present as a boolean and nothing more.

Every read and write of the shop goes through the MCP server, including the ones a human
triggers. Clicking Approve runs the same `record_approval_decision` and `make_payment`
tools an agent would, so the rules apply identically to both.

Run it:
    uvicorn backend.main:app --reload --port 8000
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from . import audit, config, shop
from .agents import AGENTS
from .models import DecisionRequest, ResetRequest, RunRequest
from .runner import run_single_agent, run_ticket
from .runs import DONE, FAILED, registry
from .trace import RunTrace

app = FastAPI(
    title="Campus Customs",
    version="0.2.0",
    description="Multi-agent shop operations. The API key never leaves this process.",
)

# The Vite dev server. Credentials are not allowed because the API has no cookie auth —
# widening this later should be a deliberate decision, not an accident.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",  # vite preview
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Health and metadata
# ---------------------------------------------------------------------------


@app.get("/api/health", tags=["meta"])
def health() -> dict:
    """Whether the backend is configured. Reports that a key exists, never the key."""
    return {
        "ok": True,
        "model": config.MODEL,
        "portkey_key_loaded": config.key_is_available(),
        "agents": list(AGENTS),
        "active_runs": [r.run_id for r in registry.active()],
        "limits": {
            "max_turns": config.MAX_TURNS,
            "max_depth": config.MAX_DEPTH,
            "max_delegations": config.MAX_DELEGATIONS,
            "max_tool_calls": config.MAX_TOOL_CALLS,
        },
    }


@app.get("/api/agents", tags=["meta"])
def list_agents() -> dict:
    """The team and what each agent can reach. Useful for reading a trace."""
    return {
        "agents": [
            {"name": a.name, "role": a.role, "tools": a.tools} for a in AGENTS.values()
        ]
    }


# ---------------------------------------------------------------------------
# The board
# ---------------------------------------------------------------------------


@app.get("/api/tickets", tags=["board"])
async def get_tickets(status: str | None = None) -> dict:
    """Every ticket with its status and assignee, optionally filtered by status.

    Each ticket is annotated with whether a run is currently working on it, so the board
    can show that without a second request.
    """
    result = await shop.call("list_tickets", {"status": status} if status else {})
    if not result.get("ok"):
        raise HTTPException(502, result["error"])

    tickets = result["data"]
    for ticket in tickets:
        active = registry.active_for_ticket(ticket["id"])
        ticket["active_run_id"] = active.run_id if active else None

    return {"tickets": tickets, "count": len(tickets)}


@app.get("/api/tickets/{ticket_id}", tags=["board"])
async def get_ticket(ticket_id: int) -> dict:
    """One ticket, with its lease and invoice resolved and its runs listed."""
    result = await shop.call("get_ticket", {"ticket_id": ticket_id})
    if not result.get("ok"):
        code = 404 if result["error"]["code"] == "not_found" else 502
        raise HTTPException(code, result["error"])

    runs = [r.snapshot() for r in registry.all() if r.ticket_id == ticket_id]
    return {"ticket": result["data"], "runs": runs}


@app.get("/api/cash", tags=["board"])
async def get_cash() -> dict:
    """Current cash balances, with what is owed against them.

    Returns the open invoice total and upcoming rent alongside the balance, because a
    balance on its own does not say whether the shop can afford what is coming.
    """
    accounts, invoices, leases = await shop.call_many(
        [("get_cash_accounts", {}), ("list_invoices", {"status": "open"}), ("list_leases", {})]
    )
    if not accounts.get("ok"):
        raise HTTPException(502, accounts["error"])

    return {
        "accounts": accounts["data"],
        "total_balance": accounts.get("meta", {}).get("total_balance", 0),
        "open_invoices_total": invoices.get("meta", {}).get("open_total", 0),
        "upcoming_rent": sum(lease["monthly_rent"] for lease in shop.unwrap(leases, [])),
        "as_of": invoices.get("meta", {}).get("as_of"),
    }


@app.get("/api/approvals", tags=["board"])
async def get_approvals(status: str | None = None) -> dict:
    """Approval requests. `status=pending` is the queue a human needs to act on."""
    result = await shop.call("list_approvals", {"status": status} if status else {})
    if not result.get("ok"):
        raise HTTPException(502, result["error"])
    return {"approvals": result["data"], **result.get("meta", {})}


@app.get("/api/drafts", tags=["board"])
async def get_drafts(ticket_id: int | None = None) -> dict:
    """Customer messages written to the board. None of them have been sent."""
    result = await shop.call("list_drafts", {"ticket_id": ticket_id} if ticket_id else {})
    if not result.get("ok"):
        raise HTTPException(502, result["error"])
    return {"drafts": result["data"], "delivered": False}


@app.get("/api/purchase-orders", tags=["board"])
async def get_purchase_orders() -> dict:
    """Restock orders placed with vendors, and when each is expected."""
    result = await shop.call("list_purchase_orders", {})
    if not result.get("ok"):
        raise HTTPException(502, result["error"])
    return {"purchase_orders": result["data"], **result.get("meta", {})}


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


@app.post("/api/tickets/{ticket_id}/run", status_code=202, tags=["runs"])
async def start_run(ticket_id: int, body: RunRequest | None = None) -> dict:
    """Start a run on a ticket. Returns immediately with a run id to watch.

    One run per ticket at a time — two agents editing the same ticket would interleave
    their notes and race on its status.
    """
    body = body or RunRequest()

    if body.agent and body.agent not in AGENTS:
        raise HTTPException(404, f"No agent named {body.agent!r}. Team: {list(AGENTS)}")

    existing = registry.active_for_ticket(ticket_id)
    if existing:
        raise HTTPException(
            409,
            {
                "message": f"Ticket {ticket_id} already has a run in flight.",
                "run_id": existing.run_id,
            },
        )

    check = await shop.call("get_ticket", {"ticket_id": ticket_id})
    if not check.get("ok"):
        raise HTTPException(404, check["error"])

    agent = body.agent or "Boss"
    mode = "single_agent" if body.agent else "full_team"

    async def work(trace: RunTrace) -> dict:
        if body.agent:
            return await run_single_agent(ticket_id, body.agent, body.task, trace=trace)
        return await run_ticket(ticket_id, trace=trace)

    record = registry.start(ticket_id, mode, agent, work)
    return {
        "run_id": record.run_id,
        "ticket_id": ticket_id,
        "mode": mode,
        "agent": agent,
        "status": record.status,
        "poll": f"/api/runs/{record.run_id}",
        "stream": f"/api/runs/{record.run_id}/stream",
    }


@app.get("/api/runs", tags=["runs"])
def list_runs() -> dict:
    """Every run this process has started, newest first, without their trace entries."""
    runs = [
        {k: v for k, v in r.snapshot().items() if k != "entries"}
        for r in reversed(registry.all())
    ]
    return {"runs": runs, "active": [r.run_id for r in registry.active()]}


@app.get("/api/runs/{run_id}", tags=["runs"])
def get_run(run_id: str, since: int = Query(0, description="Only entries after this seq.")) -> dict:
    """Poll one run. Send the last `seq` you saw as `since` to get only what is new."""
    record = registry.get(run_id)
    if record is None:
        raise HTTPException(404, f"No run {run_id!r} in this process. Finished runs from "
                                 "earlier sessions are in output/audit_trail.json.")
    return record.snapshot(since=since)


@app.get("/api/runs/{run_id}/stream", tags=["runs"])
async def stream_run(run_id: str, since: int = 0) -> StreamingResponse:
    """Server-sent events: each trace entry as it happens, then a final `done` event.

    The dashboard uses this to show agents working live. It closes itself when the run
    finishes, so a client does not have to decide when to stop listening.
    """
    record = registry.get(run_id)
    if record is None:
        raise HTTPException(404, f"No run {run_id!r} in this process.")

    async def events():
        seen = since
        while True:
            for entry in [e for e in record.trace.entries if e["seq"] > seen]:
                seen = entry["seq"]
                yield f"event: entry\ndata: {json.dumps(entry, default=str)}\n\n"

            if record.status in (DONE, FAILED):
                summary = {k: v for k, v in record.snapshot().items() if k != "entries"}
                yield f"event: done\ndata: {json.dumps(summary, default=str)}\n\n"
                return

            # A keepalive comment stops an idle proxy from closing the connection
            # during a long model call, when no entries are being produced.
            yield ": waiting\n\n"
            await asyncio.sleep(0.4)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/human-actions", tags=["approvals"])
def human_actions(limit: int = Query(50, ge=1, le=500)) -> dict:
    """Every decision a person made, newest first, from the append-only audit trail."""
    actions = list(reversed(audit.read_human_actions()))[:limit]
    return {"human_actions": actions, "count": len(actions)}


@app.get("/api/events", tags=["runs"])
def recent_events(limit: int = Query(50, ge=1, le=500)) -> dict:
    """Recent agent activity across every run, newest first.

    Live runs in this process come first; older runs are filled in from the audit trail,
    so the feed survives a restart.
    """
    events: list[dict[str, Any]] = registry.recent_events(limit)

    if len(events) < limit:
        known = {e["run_id"] for e in events}
        for run in reversed(audit.read_runs()):
            if run["run_id"] in known or len(events) >= limit:
                continue
            for entry in reversed(run.get("trace", {}).get("entries", [])):
                if len(events) >= limit:
                    break
                events.append({**entry, "run_id": run["run_id"],
                               "ticket_id": run["ticket_id"], "historical": True})

    return {"events": events[:limit], "count": min(len(events), limit)}


# ---------------------------------------------------------------------------
# Human decisions
# ---------------------------------------------------------------------------


@app.post("/api/approvals/{approval_id}/decision", tags=["approvals"])
async def decide_approval(approval_id: int, body: DecisionRequest) -> dict:
    """Record a human's approve-or-reject, and carry out what was approved.

    Approving a payment pays it; approving a purchase order places it. Both go through
    the same MCP tools an agent uses, so every rule still applies — an approved payment
    that would overdraw the account is still refused, and an approved order to a vendor
    with an unpaid invoice is still refused. A refusal here is reported, not worked
    around.
    """
    if body.decision not in ("approved", "rejected"):
        raise HTTPException(422, "decision must be 'approved' or 'rejected'.")
    if not body.decided_by.strip():
        raise HTTPException(422, "decided_by is required — an approval needs a name on it.")

    decision = await shop.call(
        "record_approval_decision",
        {"approval_id": approval_id, "decision": body.decision,
         "decided_by": body.decided_by.strip()},
    )
    if not decision.get("ok"):
        code = 404 if decision["error"]["code"] == "not_found" else 409
        raise HTTPException(code, decision["error"])

    approval = decision["data"]
    before = audit.snapshot()

    executed = None
    if body.decision == "approved" and body.execute:
        executed = await _execute(approval)

    # A person moving the shop's money belongs in the append-only trail beside the
    # agent runs, not only in a database column the same process can rewrite.
    entry = audit.append_human_action(
        action=body.decision,
        decided_by=body.decided_by.strip(),
        approval=approval,
        executed=executed,
        before=before,
        after=audit.snapshot(),
    )

    return {"approval": approval, "executed": executed, "action_id": entry["action_id"],
            "changes": entry["changes"]}


async def _execute(approval: dict[str, Any]) -> dict[str, Any]:
    """Carry out an approved request. Returns the tool's envelope either way."""
    kind = approval["kind"]

    if kind in ("invoice", "rent"):
        return {
            "action": "make_payment",
            "result": await shop.call(
                "make_payment",
                {
                    "kind": kind,
                    "ref_id": approval["ref_id"],
                    "amount": approval["amount"],
                    "account": approval["account"],
                    "approval_id": approval["id"],
                },
            ),
        }

    if kind == "purchase_order":
        details = approval.get("details") or {}
        required = ("vendor_id", "sku", "size", "qty")
        missing = [field for field in required if field not in details]
        if missing:
            # The approval was granted but carries no order to place. Say so rather
            # than guessing at a vendor or a quantity.
            return {
                "action": "create_purchase_order",
                "result": {
                    "ok": False,
                    "error": {
                        "code": "invalid_input",
                        "message": (
                            f"Approval {approval['id']} has no {', '.join(missing)} in its "
                            "details, so there is nothing to order. The approval stands; "
                            "re-run the ticket so Inventory can place the order."
                        ),
                    },
                },
            }

        return {
            "action": "create_purchase_order",
            "result": await shop.call(
                "create_purchase_order",
                {
                    "vendor_id": details["vendor_id"],
                    "sku": details["sku"],
                    "size": details["size"],
                    "qty": details["qty"],
                    "approval_id": approval["id"],
                    "ticket_id": approval.get("ticket_id"),
                    "notes": f"Placed on approval {approval['id']}.",
                },
            ),
        }

    return {"action": None, "result": {"ok": False, "error": {
        "code": "invalid_input", "message": f"Nothing to execute for kind {kind!r}."}}}


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------


@app.post("/api/reset", tags=["admin"])
def reset_database(body: ResetRequest) -> dict:
    """Reset the working database to the original. Requires `confirm: true`.

    Discards everything the agents have done to the shop and restores
    `campus_customs.db`. Refused while a run is in flight, which would otherwise leave
    the run writing into a database that no longer matches what it read.

    `output/audit_trail.json` is **not** touched. Resetting rewinds the shop, not the
    record of what was done to it.
    """
    if not body.confirm:
        raise HTTPException(422, "Set confirm: true. This discards all shop state.")

    active = registry.active()
    if active:
        raise HTTPException(
            409,
            {
                "message": "A run is in flight. Wait for it to finish before resetting.",
                "active_runs": [r.run_id for r in active],
            },
        )

    from scripts.reset_db import reset

    path = reset()
    return {
        "ok": True,
        "database": path.name,
        "audit_trail_preserved": True,
        "note": "Shop data restored to the original. The audit trail was not touched.",
    }
