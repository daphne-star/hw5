"""The shop's rules, proved to fire.

Each test does the thing the rules forbid and checks three things: the call was refused,
the refusal carries the right code, and **nothing changed** — no cash moved, no row
appeared. A refusal that still half-applies is worse than no rule at all.

The seed shop: today is 2026-08-31, checking holds $3,400, invoice 501 is $840 open and
overdue to Bulldog Print Co (vendor 1), and rent of $2,400 is due 2026-09-02.
"""

from __future__ import annotations

import sqlite3

import pytest

from conftest import balance_of
from mcp_server import db, tools, write_tools

CHECKING_START = 3400.0
INVOICE_501 = 840.0
BULLDOG = 1  # vendor with the open invoice
ELM_CITY = 2  # vendor with no open invoice


# ---------------------------------------------------------------------------
# Overdraft
# ---------------------------------------------------------------------------


def test_payment_larger_than_balance_is_refused(shop, approved):
    """A payment that would overdraw the account is refused, and no cash moves."""
    too_much = CHECKING_START + 1
    approval_id = approved("rent", too_much, ref_id=1)

    result = write_tools.make_payment(
        kind="rent", ref_id=1, amount=too_much, account="checking", approval_id=approval_id
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "insufficient_funds"
    assert result["error"]["details"]["short_by"] == pytest.approx(1.0)
    assert balance_of() == CHECKING_START
    assert tools.list_payments()["data"] == []


def test_second_payment_that_would_overdraw_is_refused(shop, approved):
    """The guard is on the running balance, not the opening one.

    Pay the invoice first, legitimately. The rent payment that follows was affordable
    an hour ago and is not any more.
    """
    first = write_tools.make_payment(
        kind="invoice", ref_id=501, amount=INVOICE_501, account="checking",
        approval_id=approved("invoice", INVOICE_501, ref_id=501),
    )
    assert first["ok"], first
    assert balance_of() == pytest.approx(CHECKING_START - INVOICE_501)  # 2560.00

    # Rent is 2400, which fits. A 3000 bill no longer does.
    result = write_tools.make_payment(
        kind="rent", ref_id=1, amount=3000.0, account="checking",
        approval_id=approved("rent", 3000.0, ref_id=1),
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "insufficient_funds"
    assert balance_of() == pytest.approx(CHECKING_START - INVOICE_501)


def test_database_trigger_refuses_an_overdraft_even_without_the_tool(shop):
    """The floor under the tool: SQLite itself rejects a negative balance."""
    with pytest.raises(sqlite3.IntegrityError, match="insufficient_funds"):
        with db.transaction() as conn:
            conn.execute("UPDATE cash_accounts SET balance = -1 WHERE name = 'checking'")

    assert balance_of() == CHECKING_START


# ---------------------------------------------------------------------------
# Payment without approval
# ---------------------------------------------------------------------------


def test_payment_without_an_approval_is_refused(shop):
    """No approval id that exists means no payment."""
    result = write_tools.make_payment(
        kind="invoice", ref_id=501, amount=INVOICE_501, account="checking", approval_id=999
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "approval_required"
    assert balance_of() == CHECKING_START
    assert tools.list_invoices()["data"][0]["status"] == "open"


def test_payment_on_a_pending_approval_is_refused(shop):
    """Requesting approval is not the same as having it. A human has to decide."""
    request = write_tools.request_approval(
        kind="invoice", amount=INVOICE_501, account="checking",
        reason="Bulldog Print Co is blocking our restock.", requested_by="Accounting",
        ref_id=501,
    )
    assert request["data"]["status"] == "pending"

    result = write_tools.make_payment(
        kind="invoice", ref_id=501, amount=INVOICE_501, account="checking",
        approval_id=request["data"]["id"],
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "approval_required"
    assert result["error"]["details"]["approval_status"] == "pending"
    assert balance_of() == CHECKING_START


def test_payment_on_a_rejected_approval_is_refused(shop):
    """A human said no. That is the end of it."""
    request = write_tools.request_approval(
        kind="rent", amount=2400.0, account="checking", reason="Monthly rent.",
        requested_by="Facilities", ref_id=1,
    )
    write_tools.record_approval_decision(
        approval_id=request["data"]["id"], decision="rejected", decided_by="Daphne"
    )

    result = write_tools.make_payment(
        kind="rent", ref_id=1, amount=2400.0, account="checking",
        approval_id=request["data"]["id"],
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "approval_required"
    assert result["error"]["details"]["approval_status"] == "rejected"
    assert balance_of() == CHECKING_START


def test_approval_for_a_different_amount_is_refused(shop, approved):
    """An approval covers the exact amount it was given, not a convenient neighbour."""
    approval_id = approved("invoice", INVOICE_501, ref_id=501)

    result = write_tools.make_payment(
        kind="invoice", ref_id=501, amount=INVOICE_501 + 100, account="checking",
        approval_id=approval_id,
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "approval_required"
    assert result["error"]["details"]["approved_amount"] == INVOICE_501
    assert balance_of() == CHECKING_START


def test_a_part_payment_against_an_invoice_is_refused(shop, approved):
    """Short-paying would close the bill in full and lose the rest.

    The invoices table has no running balance and no amount-paid column, so
    `make_payment` marks an invoice `paid` outright. Accepting $100 against the $840
    bill would settle it on paper, unblock Bulldog Print Co, and silently write off
    $740 the shop still owes.
    """
    approval_id = approved("invoice", 100.0, ref_id=501)

    result = write_tools.make_payment(
        kind="invoice", ref_id=501, amount=100.0, account="checking",
        approval_id=approval_id,
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_input"
    assert result["error"]["details"]["difference"] == pytest.approx(-740.0)

    # Nothing moved, and crucially the vendor is still blocked.
    assert balance_of() == CHECKING_START
    assert tools.list_invoices()["data"][0]["status"] == "open"
    assert tools.list_payments()["data"] == []

    blocked = write_tools.create_purchase_order(
        vendor_id=BULLDOG, sku="CC-TEE-WHITE", size="S", qty=1,
        approval_id=approved("purchase_order", 8.0),
    )
    assert blocked["error"]["code"] == "vendor_blocked"


def test_overpaying_an_invoice_is_refused_too(shop, approved):
    """The guard is on the difference, not just on paying too little."""
    approval_id = approved("invoice", INVOICE_501 + 60, ref_id=501)

    result = write_tools.make_payment(
        kind="invoice", ref_id=501, amount=INVOICE_501 + 60, account="checking",
        approval_id=approval_id,
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_input"
    assert result["error"]["details"]["difference"] == pytest.approx(60.0)
    assert balance_of() == CHECKING_START


def test_an_approval_cannot_be_spent_twice(shop, approved):
    """One approval, one payment. Replaying it does not work."""
    approval_id = approved("rent", 100.0, ref_id=1)

    first = write_tools.make_payment(
        kind="rent", ref_id=1, amount=100.0, account="checking", approval_id=approval_id
    )
    assert first["ok"], first

    second = write_tools.make_payment(
        kind="rent", ref_id=1, amount=100.0, account="checking", approval_id=approval_id
    )

    assert second["ok"] is False
    assert second["error"]["code"] == "approval_required"
    assert balance_of() == pytest.approx(CHECKING_START - 100.0)
    assert tools.list_payments()["meta"]["count"] == 1


def test_a_decided_approval_cannot_be_decided_again(shop):
    """A rejection cannot be quietly flipped to an approval on a second pass."""
    request = write_tools.request_approval(
        kind="rent", amount=50.0, account="checking", reason="Test.",
        requested_by="Accounting", ref_id=1,
    )
    approval_id = request["data"]["id"]
    write_tools.record_approval_decision(approval_id, "rejected", "Daphne")

    flip = write_tools.record_approval_decision(approval_id, "approved", "Accounting")

    assert flip["ok"] is False
    assert flip["error"]["code"] == "conflict"
    assert tools.list_approvals()["data"][0]["status"] == "rejected"


# ---------------------------------------------------------------------------
# Vendor with an unpaid invoice
# ---------------------------------------------------------------------------


def test_purchase_order_to_a_vendor_with_an_unpaid_invoice_is_refused(shop, approved):
    """Bulldog Print Co is owed $840 and will not ship until that is settled."""
    approval_id = approved("purchase_order", 8.0 * 12)

    result = write_tools.create_purchase_order(
        vendor_id=BULLDOG, sku="CC-TEE-WHITE", size="S", qty=12, approval_id=approval_id
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "vendor_blocked"
    assert result["error"]["details"]["total_owed"] == INVOICE_501
    assert result["error"]["details"]["unpaid_invoices"][0]["id"] == 501
    assert tools.list_purchase_orders()["data"] == []


def test_the_vendor_block_is_checked_before_the_approval(shop, approved):
    """Being approved does not buy a way past a blocked vendor.

    The order is deliberate: an agent that gets a human to sign off on an order to
    Bulldog Print Co still cannot place it, and the message says to pay the invoice.
    """
    approval_id = approved("purchase_order", 8.0 * 12)

    result = write_tools.create_purchase_order(
        vendor_id=BULLDOG, sku="CC-TEE-WHITE", size="S", qty=12, approval_id=approval_id
    )

    assert result["error"]["code"] == "vendor_blocked"
    assert "Settle the invoice first" in result["error"]["message"]
    # The approval is untouched, so it can still be used once the invoice is paid.
    assert tools.list_approvals()["data"][0]["consumed_by_payment_id"] is None


def test_paying_the_invoice_unblocks_the_vendor(shop, approved):
    """The refusal names a way through, and the way through actually works."""
    paid = write_tools.make_payment(
        kind="invoice", ref_id=501, amount=INVOICE_501, account="checking",
        approval_id=approved("invoice", INVOICE_501, ref_id=501),
    )
    assert paid["ok"], paid

    result = write_tools.create_purchase_order(
        vendor_id=BULLDOG, sku="CC-TEE-WHITE", size="S", qty=12,
        approval_id=approved("purchase_order", 8.0 * 12),
    )

    assert result["ok"] is True
    assert result["data"]["purchase_order"]["expected_arrival"] == "2026-09-05"  # +5 lead days


def test_an_unblocked_vendor_can_be_ordered_from(shop, approved):
    """Elm City Gifts owes nothing, so a mug restock goes through."""
    result = write_tools.create_purchase_order(
        vendor_id=ELM_CITY, sku="CC-MUG-CREST", size="OS", qty=24,
        approval_id=approved("purchase_order", 3.5 * 24),
    )

    assert result["ok"] is True
    assert result["data"]["purchase_order"]["total_cost"] == pytest.approx(84.0)
    assert result["data"]["purchase_order"]["expected_arrival"] == "2026-09-03"  # +3 lead days


# ---------------------------------------------------------------------------
# Cash may only go out
# ---------------------------------------------------------------------------


def test_raising_a_balance_is_refused_by_the_database(shop):
    """There is no tool that adds cash, and the database will not accept one either."""
    with pytest.raises(sqlite3.IntegrityError, match="cash_increase_forbidden"):
        with db.transaction() as conn:
            conn.execute("UPDATE cash_accounts SET balance = balance + 1000")

    assert balance_of() == CHECKING_START


def test_creating_a_funded_account_is_refused(shop):
    """The obvious way around the first guard: a brand new account with money in it."""
    with pytest.raises(sqlite3.IntegrityError, match="cash_increase_forbidden"):
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO cash_accounts (name, balance, date) VALUES (?, ?, ?)",
                ("slush_fund", 10_000.0, "2026-08-31"),
            )

    assert tools.get_cash_accounts()["meta"]["total_balance"] == CHECKING_START


def test_a_negative_payment_is_refused(shop, approved):
    """Paying minus five hundred dollars is a deposit wearing a hat."""
    result = write_tools.make_payment(
        kind="rent", ref_id=1, amount=-500.0, account="checking", approval_id=1
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_input"
    assert balance_of() == CHECKING_START


def test_a_payment_record_cannot_be_edited_afterwards(shop, approved):
    """Payments are an audit trail. Rewriting one is not an available move."""
    write_tools.make_payment(
        kind="rent", ref_id=1, amount=100.0, account="checking",
        approval_id=approved("rent", 100.0, ref_id=1),
    )

    with pytest.raises(sqlite3.IntegrityError, match="audit trail"):
        with db.transaction() as conn:
            conn.execute("UPDATE payments SET amount = 1 WHERE id = 1")


# ---------------------------------------------------------------------------
# Nothing is ever sent
# ---------------------------------------------------------------------------


def test_there_is_no_send_tool(shop):
    """The absence is the feature. Drafts are stored and shown, never delivered."""
    names = {tool.__name__ for tool in write_tools.WRITE_TOOLS}
    assert not any("send" in name or "email" in name for name in names)

    saved = write_tools.save_draft_message(
        recipient="Tauhid Zaman", subject="Your Bulldog tee",
        body="Your size S is out of stock; a reprint is on order.",
        written_by="Customer Service", ticket_id=101,
    )

    assert saved["ok"] is True
    assert saved["meta"]["delivered"] is False
    assert tools.list_drafts(ticket_id=101)["meta"]["count"] == 1


# ---------------------------------------------------------------------------
# The happy path, so the refusals mean something
# ---------------------------------------------------------------------------


def test_an_approved_payment_moves_cash_and_closes_the_invoice(shop, approved):
    """One transaction: payment recorded, balance down, invoice closed, approval spent."""
    approval_id = approved("invoice", INVOICE_501, ref_id=501)

    result = write_tools.make_payment(
        kind="invoice", ref_id=501, amount=INVOICE_501, account="checking",
        approval_id=approval_id,
    )

    assert result["ok"] is True
    assert result["data"]["balance_before"] == CHECKING_START
    assert result["data"]["balance_after"] == pytest.approx(2560.0)
    assert result["data"]["payment"]["approved_by"] == "Daphne"
    assert result["data"]["payment"]["paid_at"] == "2026-08-31"  # shop time, not today
    assert tools.list_invoices(status="open")["meta"]["count"] == 0
    assert tools.list_approvals()["data"][0]["consumed_by_payment_id"] == 1


def test_ticket_tools_update_status_assignee_and_notes(shop):
    """Status, assignee, and notes each stick, and notes accumulate rather than overwrite."""
    assert write_tools.assign_ticket(101, "Inventory", note="Stock check")["ok"]
    assert write_tools.set_ticket_status(101, "blocked", note="Vendor owed $840")["ok"]
    result = write_tools.add_ticket_note(101, "Waiting on approval 1.", author="Boss")

    ticket = result["data"]
    assert ticket["assignee"] == "Inventory"
    assert ticket["status"] == "blocked"
    assert "Needs a tee in size S." in ticket["notes"]  # original note survives
    assert "Vendor owed $840" in ticket["notes"]
    assert "[Boss] Waiting on approval 1." in ticket["notes"]


def test_an_invalid_ticket_status_is_refused(shop):
    """The status vocabulary is closed; a made-up state does not get written."""
    result = write_tools.set_ticket_status(101, "mostly_done")

    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_input"
    assert tools.get_ticket(101)["data"]["status"] == "open"
