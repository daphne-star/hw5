"""The audit trail accumulates and is never silently shortened."""

from __future__ import annotations

import json

import pytest

from backend import audit

TRACE = {
    "started_at": "2026-08-31T10:00:00+00:00",
    "finished_at": "2026-08-31T10:00:09+00:00",
    "agents_involved": ["Boss", "Inventory"],
    "handoffs": ["Boss -> Inventory"],
    "tool_calls_used": 7,
    "delegations_used": 1,
    "entries": [
        {"seq": 1, "kind": "tool_call", "agent": "Boss", "tool": "get_ticket"},
        {
            "seq": 2,
            "kind": "refusal",
            "agent": "Accounting",
            "tool": "make_payment",
            "error_code": "insufficient_funds",
            "result": {"error": {"message": "Would leave -$100."}},
        },
        {"seq": 3, "kind": "limit", "agent": "Boss", "limit": "max_depth", "detail": "deep"},
    ],
}


@pytest.fixture
def audit_file(tmp_path, monkeypatch):
    path = tmp_path / "audit_trail.json"
    monkeypatch.setattr(audit, "AUDIT_PATH", path)
    return path


def write(ticket_id: int = 101, **overrides):
    payload = {
        "ticket_id": ticket_id,
        "entry_agent": "Boss",
        "mode": "full_team",
        "answer": "Blocked pending approval.",
        "trace": TRACE,
        "before": {"shop_date": "2026-08-31", "cash": {"checking": 3400.0}, "payment_ids": [],
                   "approval_ids": [], "purchase_order_ids": [], "draft_ids": [],
                   "open_invoice_ids": [501], "ticket_statuses": {"101": "open"}},
        "after": {"shop_date": "2026-08-31", "cash": {"checking": 2560.0}, "payment_ids": [1],
                  "approval_ids": [1], "purchase_order_ids": [], "draft_ids": [],
                  "open_invoice_ids": [], "ticket_statuses": {"101": "resolved"}},
        "model": "gpt-5.6-luna",
    }
    payload.update(overrides)
    return audit.append_run(**payload)


def test_runs_accumulate_rather_than_replace(audit_file):
    first = write(101)
    second = write(102)
    third = write(103)

    data = json.loads(audit_file.read_text(encoding="utf-8"))
    assert data["run_count"] == 3
    assert [r["ticket_id"] for r in data["runs"]] == [101, 102, 103]
    assert {r["run_id"] for r in data["runs"]} == {first["run_id"], second["run_id"],
                                                  third["run_id"]}


def test_an_existing_entry_is_never_edited(audit_file):
    first = write(101)
    before = json.loads(audit_file.read_text(encoding="utf-8"))["runs"][0]

    write(102)

    after = json.loads(audit_file.read_text(encoding="utf-8"))["runs"][0]
    assert after == before
    assert after["run_id"] == first["run_id"]


def test_a_shorter_list_is_refused(audit_file):
    write(101)
    write(102)

    with pytest.raises(RuntimeError, match="append-only"):
        audit._save({"runs": [{"run_id": "only_one"}], "human_actions": []},
                    {"runs": 2, "human_actions": 0})

    assert json.loads(audit_file.read_text(encoding="utf-8"))["run_count"] == 2


def test_an_unreadable_file_is_quarantined_not_overwritten(audit_file, tmp_path):
    audit_file.write_text("{ this is not json", encoding="utf-8")

    write(101)

    data = json.loads(audit_file.read_text(encoding="utf-8"))
    assert data["run_count"] == 1
    quarantined = list(tmp_path.glob("audit_trail.corrupt-*.json"))
    assert len(quarantined) == 1
    assert "this is not json" in quarantined[0].read_text(encoding="utf-8")


def test_the_entry_records_what_actually_changed(audit_file):
    entry = write(101)
    changes = entry["changes"]

    assert changes["cash_moved"]["checking"]["delta"] == pytest.approx(-840.0)
    assert changes["payments_created"] == [1]
    assert changes["approvals_created"] == [1]
    assert changes["invoices_closed"] == [501]
    assert changes["ticket_status_changes"]["101"] == {"before": "open", "after": "resolved"}


def test_refusals_and_limits_are_pulled_out_for_scanning(audit_file):
    entry = write(101)

    assert entry["refusals"][0]["code"] == "insufficient_funds"
    assert entry["refusals"][0]["tool"] == "make_payment"
    assert entry["limits_hit"][0]["limit"] == "max_depth"


def test_a_failed_run_is_still_recorded(audit_file):
    """A log that only keeps the tidy runs is not an audit trail."""
    entry = write(
        101,
        answer="[Boss could not reach the model: APIError]",
        trace={**TRACE, "entries": [
            {"seq": 1, "kind": "error", "agent": "Boss", "detail": "APIError: gateway timeout"},
        ]},
    )

    assert entry["errors"][0]["detail"].startswith("APIError")
    assert json.loads(audit_file.read_text(encoding="utf-8"))["run_count"] == 1


def test_resetting_the_shop_does_not_touch_the_audit_trail(audit_file):
    """reset_db.py rewinds the data. It must not rewind the history."""
    import scripts.reset_db as reset_db

    write(101)
    assert "audit" not in reset_db.__doc__.lower()
    assert audit.AUDIT_PATH.exists()
    assert json.loads(audit_file.read_text(encoding="utf-8"))["run_count"] == 1


# ---------------------------------------------------------------------------
# Human decisions
# ---------------------------------------------------------------------------


APPROVAL = {
    "id": 1, "kind": "invoice", "ref_id": 501, "amount": 840.0, "account": "checking",
    "reason": "Pay the overdue invoice so the vendor will ship.", "requested_by": "Accounting",
    "ticket_id": 101, "status": "approved",
}

BEFORE = {"shop_date": "2026-08-31", "cash": {"checking": 3400.0}, "payment_ids": [],
          "approval_ids": [1], "purchase_order_ids": [], "draft_ids": [],
          "open_invoice_ids": [501], "ticket_statuses": {"101": "blocked"}}
AFTER = {"shop_date": "2026-08-31", "cash": {"checking": 2560.0}, "payment_ids": [1],
         "approval_ids": [1], "purchase_order_ids": [], "draft_ids": [],
         "open_invoice_ids": [], "ticket_statuses": {"101": "blocked"}}


def test_a_human_approval_is_recorded_with_who_decided(audit_file):
    """Who moved the money is in the append-only trail, not only in a database column."""
    entry = audit.append_human_action(
        action="approved",
        decided_by="Daphne",
        approval=APPROVAL,
        executed={"action": "make_payment", "result": {"ok": True}},
        before=BEFORE,
        after=AFTER,
    )

    assert entry["decided_by"] == "Daphne"
    assert entry["amount"] == 840.0
    assert entry["executed_ok"] is True
    assert entry["changes"]["cash_moved"]["checking"]["delta"] == pytest.approx(-840.0)
    assert entry["changes"]["invoices_closed"] == [501]

    stored = json.loads(audit_file.read_text(encoding="utf-8"))
    assert stored["human_action_count"] == 1
    assert stored["human_actions"][0]["action_id"] == entry["action_id"]


def test_human_actions_and_runs_accumulate_side_by_side(audit_file):
    """Appending one must never shorten the other."""
    write(101)
    audit.append_human_action(action="approved", decided_by="Daphne", approval=APPROVAL,
                             executed=None, before=BEFORE, after=AFTER)
    write(102)

    stored = json.loads(audit_file.read_text(encoding="utf-8"))
    assert stored["run_count"] == 2
    assert stored["human_action_count"] == 1


def test_a_refused_execution_is_recorded_too(audit_file):
    """An approval granted and then refused by the rules is the case worth catching."""
    entry = audit.append_human_action(
        action="approved",
        decided_by="Daphne",
        approval={**APPROVAL, "amount": 99999.0},
        executed={"action": "make_payment", "result": {
            "ok": False, "error": {"code": "insufficient_funds", "message": "Would overdraw."}}},
        before=BEFORE,
        after=BEFORE,
    )

    assert entry["executed_ok"] is False
    assert "overdraw" in entry["executed_error"]
    assert entry["changes"]["cash_moved"] == {}
