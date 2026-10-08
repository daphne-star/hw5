"""Write tools — the shop's rules, enforced in code.

Every rule here is checked before the write, not described in a prompt. An agent that
argues for an exception still gets the refusal, because the refusal is a branch in a
function rather than an instruction it can reinterpret.

Three rules sit lower still, as SQLite triggers created in `migrations.py`: a cash
balance may not rise, may not go negative, and a payment row may not be edited after it
is written. Those hold even if a tool is called wrongly or a future tool forgets to
check. The checks in this module exist so the refusal comes back readable; the triggers
exist so it comes back at all.

All timestamps use the shop's date from `desk.date_today`, never the system clock.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from . import db
from .migrations import ensure_app_tables
from .results import (
    APPROVAL_REQUIRED,
    CONFLICT,
    INSUFFICIENT_FUNDS,
    INVALID_INPUT,
    NOT_FOUND,
    VENDOR_BLOCKED,
    err,
    ok,
    tool_result,
)

# What a payment or approval can be for.
PAYMENT_KINDS = ("invoice", "rent", "purchase_order")

# Ticket lifecycle. The seed data only ever says "open"; the rest is this project's.
TICKET_STATUSES = ("open", "in_progress", "blocked", "resolved", "rejected")

# Float dollars never compare exactly. Anything under half a cent is the same amount.
CENT = 0.005


# ---------------------------------------------------------------------------
# Approvals
# ---------------------------------------------------------------------------


@tool_result
def request_approval(
    kind: str,
    amount: float,
    account: str,
    reason: str,
    requested_by: str,
    ref_id: int | None = None,
    ticket_id: int | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Ask a human to approve money leaving the shop. Creates a pending approval.

    Nothing is paid by calling this. It puts a request on the approvals queue for a
    person to decide; `make_payment` will not move money without the id this returns,
    already approved.

    Args:
        kind: What the money is for — `"invoice"`, `"rent"`, or `"purchase_order"`.
        amount: Dollars requested. Must be positive, and must match the eventual payment
            exactly — an approval for $840 cannot authorize a payment of $850.
        account: Which cash account it would come from, e.g. `"checking"`.
        reason: Why this should be paid, in a sentence. The human reads this.
        requested_by: Which agent is asking.
        ref_id: What it refers to — an invoice id, a lease id, a purchase order id.
        ticket_id: The ticket this belongs to, so the request shows up in context.
        details: What the approval is actually for, as structured fields rather than
            prose. **Required in practice for `purchase_order`**: pass
            `{"vendor_id": 1, "sku": "CC-TEE-WHITE", "size": "S", "qty": 12}` so the
            order can be placed the moment a human approves it. Without this, an
            approved purchase order is just a signature with nothing attached to it,
            and someone has to run the ticket again to work out what was meant.

    Returns the created approval with `status: "pending"`.
    """
    if kind not in PAYMENT_KINDS:
        return err(INVALID_INPUT, f"kind must be one of {PAYMENT_KINDS}, not {kind!r}.")
    if amount is None or amount <= 0:
        return err(INVALID_INPUT, f"amount must be positive, got {amount!r}.")
    if not reason or not reason.strip():
        return err(INVALID_INPUT, "reason is required — a human has to read it to decide.")

    with db.transaction() as conn:
        ensure_app_tables(conn)
        today = _today(conn)

        if _fetch(conn, "SELECT name FROM cash_accounts WHERE name = ?", (account,)) is None:
            return err(NOT_FOUND, f"No cash account named {account!r}.", account=account)
        if ticket_id is not None and _fetch(
            conn, "SELECT id FROM tickets WHERE id = ?", (ticket_id,)
        ) is None:
            return err(NOT_FOUND, f"No ticket with id {ticket_id}.", ticket_id=ticket_id)

        cursor = conn.execute(
            "INSERT INTO approvals "
            "(kind, ref_id, amount, account, reason, requested_by, ticket_id, status, "
            " details, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
            (kind, ref_id, amount, account, reason.strip(), requested_by, ticket_id,
             json.dumps(details) if details else None, today),
        )
        approval = _fetch(conn, "SELECT * FROM approvals WHERE id = ?", (cursor.lastrowid,))

    return ok(_with_details(approval))


@tool_result
def record_approval_decision(
    approval_id: int, decision: str, decided_by: str
) -> dict[str, Any]:
    """Record a human's approve-or-reject decision on a pending approval.

    Args:
        approval_id: The approval being decided.
        decision: `"approved"` or `"rejected"`.
        decided_by: The name of the person deciding. Not an agent — a human.

    A decision is final: an approval that has already been decided cannot be decided
    again, so a rejection cannot be quietly reversed into an approval.

    Returns the updated approval with `decided_by` and `decided_at` filled in.
    """
    if decision not in ("approved", "rejected"):
        return err(INVALID_INPUT, f"decision must be 'approved' or 'rejected', got {decision!r}.")
    if not decided_by or not decided_by.strip():
        return err(INVALID_INPUT, "decided_by is required — an approval needs a name on it.")

    with db.transaction() as conn:
        ensure_app_tables(conn)
        today = _today(conn)

        approval = _fetch(conn, "SELECT * FROM approvals WHERE id = ?", (approval_id,))
        if approval is None:
            return err(NOT_FOUND, f"No approval with id {approval_id}.", approval_id=approval_id)
        if approval["status"] != "pending":
            return err(
                CONFLICT,
                f"Approval {approval_id} was already {approval['status']} by "
                f"{approval['decided_by']} on {approval['decided_at']}. Decisions are final.",
                approval_id=approval_id,
                current_status=approval["status"],
            )

        conn.execute(
            "UPDATE approvals SET status = ?, decided_by = ?, decided_at = ? WHERE id = ?",
            (decision, decided_by.strip(), today, approval_id),
        )
        updated = _fetch(conn, "SELECT * FROM approvals WHERE id = ?", (approval_id,))

    return ok(_with_details(updated))


# ---------------------------------------------------------------------------
# Money out
# ---------------------------------------------------------------------------


@tool_result
def make_payment(
    kind: str, ref_id: int | None, amount: float, account: str, approval_id: int
) -> dict[str, Any]:
    """Pay money out of a cash account. Requires an approval, and refuses to overdraw.

    Args:
        kind: What is being paid — `"invoice"`, `"rent"`, or `"purchase_order"`.
        ref_id: What it pays — the invoice id, lease id, or purchase order id.
        amount: Dollars to pay. Must match the approval exactly.
        account: Which cash account to draw on, e.g. `"checking"`.
        approval_id: An approval with status `"approved"`, for this exact amount, kind,
            and reference. There is no way to pay without one.

    Refused when: the approval is missing, still pending, rejected, already spent, or
    for a different amount or purpose; or when the balance would go below zero. A
    refusal is the correct outcome, not an obstacle — pay something smaller, pay
    something else first, or ask for an approval that matches.

    On success the payment is recorded, the balance drops, the approval is marked spent,
    and an invoice being paid is closed — all in one transaction, so the balance and the
    payment record can never disagree.

    Returns the payment, the new balance, and what it settled.
    """
    if kind not in PAYMENT_KINDS:
        return err(INVALID_INPUT, f"kind must be one of {PAYMENT_KINDS}, not {kind!r}.")
    if amount is None or amount <= 0:
        return err(INVALID_INPUT, f"amount must be positive, got {amount!r}.")

    with db.transaction() as conn:
        ensure_app_tables(conn)
        today = _today(conn)

        # --- the approval must exist, be approved, be unspent, and match ---
        approval = _fetch(conn, "SELECT * FROM approvals WHERE id = ?", (approval_id,))
        if approval is None:
            return err(
                APPROVAL_REQUIRED,
                f"No approval with id {approval_id}. Call request_approval first and wait "
                "for a human to decide it.",
                approval_id=approval_id,
            )
        if approval["status"] != "approved":
            return err(
                APPROVAL_REQUIRED,
                f"Approval {approval_id} is {approval['status']}, not approved. "
                "Money cannot move until a human approves it.",
                approval_id=approval_id,
                approval_status=approval["status"],
            )
        if approval["consumed_by_payment_id"] is not None:
            return err(
                APPROVAL_REQUIRED,
                f"Approval {approval_id} was already spent on payment "
                f"{approval['consumed_by_payment_id']}. Each approval authorizes one payment.",
                approval_id=approval_id,
                payment_id=approval["consumed_by_payment_id"],
            )
        if abs(approval["amount"] - amount) > CENT:
            return err(
                APPROVAL_REQUIRED,
                f"Approval {approval_id} is for ${approval['amount']:,.2f}, not "
                f"${amount:,.2f}. An approval covers the exact amount it was given.",
                approval_id=approval_id,
                approved_amount=approval["amount"],
                requested_amount=amount,
            )
        if approval["kind"] != kind or (approval["ref_id"] or None) != (ref_id or None):
            return err(
                APPROVAL_REQUIRED,
                f"Approval {approval_id} covers {approval['kind']} "
                f"{approval['ref_id']}, not {kind} {ref_id}.",
                approval_id=approval_id,
            )
        if approval["account"] != account:
            return err(
                APPROVAL_REQUIRED,
                f"Approval {approval_id} is against account {approval['account']!r}, "
                f"not {account!r}.",
                approval_id=approval_id,
            )

        # --- the account must exist and must not be overdrawn ---
        row = _fetch(conn, "SELECT name, balance FROM cash_accounts WHERE name = ?", (account,))
        if row is None:
            return err(NOT_FOUND, f"No cash account named {account!r}.", account=account)

        balance = row["balance"]
        if balance - amount < -CENT:
            return err(
                INSUFFICIENT_FUNDS,
                f"Paying ${amount:,.2f} from {account!r} would leave "
                f"${balance - amount:,.2f}. The balance is ${balance:,.2f} and may not go "
                "negative. Nothing was paid.",
                account=account,
                balance=balance,
                requested=amount,
                short_by=round(amount - balance, 2),
            )

        # --- what is being settled must exist, and must be settled in full ---
        invoice = None
        if kind == "invoice":
            invoice = _fetch(conn, "SELECT * FROM invoices WHERE id = ?", (ref_id,))
            if invoice is None:
                return err(NOT_FOUND, f"No invoice with id {ref_id}.", invoice_id=ref_id)
            if invoice["status"] != "open":
                return err(
                    CONFLICT,
                    f"Invoice {ref_id} is already {invoice['status']}.",
                    invoice_id=ref_id,
                    status=invoice["status"],
                )
            # Paying an invoice marks it `paid` outright — the schema has nowhere to
            # record a part-payment, no running balance and no amount-paid column. So a
            # short payment would close the bill, unblock the vendor, and quietly lose
            # the remainder. Settle it in full or not at all.
            if abs(invoice["amount"] - amount) > CENT:
                return err(
                    INVALID_INPUT,
                    f"Invoice {ref_id} is for ${invoice['amount']:,.2f}, and this payment is "
                    f"${amount:,.2f}. An invoice is paid in full or not at all — this shop "
                    "cannot record a part-payment, so a short one would close the bill and "
                    "lose the rest. Nothing was paid.",
                    invoice_id=ref_id,
                    invoice_amount=invoice["amount"],
                    requested_amount=amount,
                    difference=round(amount - invoice["amount"], 2),
                )
        elif kind == "rent":
            if _fetch(conn, "SELECT id FROM leases WHERE id = ?", (ref_id,)) is None:
                return err(NOT_FOUND, f"No lease with id {ref_id}.", lease_id=ref_id)

        # --- apply, all together ---
        cursor = conn.execute(
            "INSERT INTO payments (kind, ref_id, amount, account, paid_at, approved_by) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (kind, ref_id, amount, account, today, approval["decided_by"]),
        )
        payment_id = cursor.lastrowid

        conn.execute(
            "UPDATE cash_accounts SET balance = balance - ?, date = ? WHERE name = ?",
            (amount, today, account),
        )
        conn.execute(
            "UPDATE approvals SET consumed_by_payment_id = ? WHERE id = ?",
            (payment_id, approval_id),
        )
        if kind == "invoice":
            conn.execute("UPDATE invoices SET status = 'paid' WHERE id = ?", (ref_id,))

        payment = _fetch(conn, "SELECT * FROM payments WHERE id = ?", (payment_id,))
        new_balance = _fetch(
            conn, "SELECT balance FROM cash_accounts WHERE name = ?", (account,)
        )["balance"]

    return ok(
        {
            "payment": payment,
            "account": account,
            "balance_before": balance,
            "balance_after": new_balance,
            "invoice_closed": ref_id if kind == "invoice" else None,
            "approval_id": approval_id,
        }
    )


# ---------------------------------------------------------------------------
# Purchasing
# ---------------------------------------------------------------------------


@tool_result
def create_purchase_order(
    vendor_id: int,
    sku: str,
    size: str,
    qty: int,
    approval_id: int,
    ticket_id: int | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Place a restock order with a vendor. Refused if that vendor is owed money.

    Args:
        vendor_id: Which vendor to order from. Check `list_vendors` for who handles what.
        sku: Product code to restock, e.g. `"CC-TEE-WHITE"`.
        size: Size to restock. Stock is per `(sku, size)`, so this matters.
        qty: How many units. Must be positive.
        approval_id: An approved approval of kind `"purchase_order"` for the order's
            total cost — a purchase order commits the shop to spend, so a human signs off.
        ticket_id: The ticket this order is for.
        notes: Anything the vendor or a later reader needs to know.

    **A vendor with an open invoice against them will not ship.** That is checked first,
    before anything else, so an approved order to a blocked vendor is still refused. The
    way through is to pay the invoice, not to re-ask.

    No cash moves here. An order is a commitment; the vendor invoices separately.

    Returns the purchase order with its `expected_arrival`, computed from the vendor's
    lead time and the shop's today.
    """
    if qty is None or qty <= 0:
        return err(INVALID_INPUT, f"qty must be positive, got {qty!r}.")

    with db.transaction() as conn:
        ensure_app_tables(conn)
        today = _today(conn)

        vendor = _fetch(conn, "SELECT * FROM vendors WHERE id = ?", (vendor_id,))
        if vendor is None:
            return err(NOT_FOUND, f"No vendor with id {vendor_id}.", vendor_id=vendor_id)

        # Checked before the approval, so being approved never buys a way past it.
        unpaid = [
            dict(r)
            for r in conn.execute(
                "SELECT id, amount, due_date FROM invoices "
                "WHERE vendor_id = ? AND status = 'open' ORDER BY due_date",
                (vendor_id,),
            )
        ]
        if unpaid:
            owed = round(sum(r["amount"] for r in unpaid), 2)
            return err(
                VENDOR_BLOCKED,
                f"{vendor['name']} will not ship new product while owed ${owed:,.2f} on "
                f"invoice(s) {', '.join(str(r['id']) for r in unpaid)}. Settle the invoice "
                "first, or order from a different vendor.",
                vendor_id=vendor_id,
                vendor_name=vendor["name"],
                unpaid_invoices=unpaid,
                total_owed=owed,
            )

        price = _fetch(conn, "SELECT unit_cost FROM pricing WHERE sku = ?", (sku,))
        if price is None:
            return err(NOT_FOUND, f"No pricing row for sku {sku!r}, so cost is unknown.", sku=sku)
        total_cost = round(price["unit_cost"] * qty, 2)

        refused = _check_approval(
            conn, approval_id, kind="purchase_order", amount=total_cost, require_ref=False
        )
        if refused is not None:
            return refused

        expected = db.add_days(today, vendor["lead_days"])
        cursor = conn.execute(
            "INSERT INTO purchase_orders "
            "(vendor_id, sku, size, qty, unit_cost, total_cost, ordered_on, expected_arrival, "
            " approval_id, ticket_id, status, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'placed', ?)",
            (vendor_id, sku, size, qty, price["unit_cost"], total_cost, today, expected,
             approval_id, ticket_id, notes),
        )
        conn.execute(
            "UPDATE approvals SET consumed_by_payment_id = ? WHERE id = ?",
            (-cursor.lastrowid, approval_id),  # negative marks a PO, not a payment
        )
        order = _fetch(conn, "SELECT * FROM purchase_orders WHERE id = ?", (cursor.lastrowid,))

    return ok({"purchase_order": order, "vendor": vendor["name"]})


# ---------------------------------------------------------------------------
# Customer messages
# ---------------------------------------------------------------------------


@tool_result
def save_draft_message(
    recipient: str,
    subject: str,
    body: str,
    written_by: str,
    ticket_id: int | None = None,
) -> dict[str, Any]:
    """Write a customer message to the board as a draft. Nothing is sent.

    Args:
        recipient: Who the message is addressed to.
        subject: One-line subject.
        body: The message itself.
        written_by: Which agent wrote it.
        ticket_id: The ticket it belongs to.

    This shop never emails anyone and never calls a vendor. A message written here is
    stored and shown to a human on the dashboard, and that is the whole of it — there is
    no send tool, by design. Write the draft as though a person will read it, because
    one will.

    Returns the stored draft.
    """
    if not body or not body.strip():
        return err(INVALID_INPUT, "body is required — an empty draft helps nobody.")

    with db.transaction() as conn:
        ensure_app_tables(conn)
        today = _today(conn)

        if ticket_id is not None and _fetch(
            conn, "SELECT id FROM tickets WHERE id = ?", (ticket_id,)
        ) is None:
            return err(NOT_FOUND, f"No ticket with id {ticket_id}.", ticket_id=ticket_id)

        cursor = conn.execute(
            "INSERT INTO drafts (ticket_id, recipient, subject, body, written_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (ticket_id, recipient, subject, body.strip(), written_by, today),
        )
        draft = _fetch(conn, "SELECT * FROM drafts WHERE id = ?", (cursor.lastrowid,))

    return ok(draft, delivered=False, note="Stored on the board. Nothing was sent.")


# ---------------------------------------------------------------------------
# Tickets
# ---------------------------------------------------------------------------


@tool_result
def set_ticket_status(ticket_id: int, status: str, note: str | None = None) -> dict[str, Any]:
    """Move a ticket to a new state.

    Args:
        ticket_id: The ticket to update.
        status: One of `"open"`, `"in_progress"`, `"blocked"`, `"resolved"`, `"rejected"`.
            Use `"blocked"` when something outside the shop's control is in the way, and
            say what in the note.
        note: A line appended to the ticket's notes explaining the change.

    Returns the updated ticket.
    """
    if status not in TICKET_STATUSES:
        return err(INVALID_INPUT, f"status must be one of {TICKET_STATUSES}, not {status!r}.")
    return _update_ticket(ticket_id, {"status": status}, note)


@tool_result
def assign_ticket(ticket_id: int, assignee: str, note: str | None = None) -> dict[str, Any]:
    """Put a ticket in a particular agent's hands.

    Args:
        ticket_id: The ticket to assign.
        assignee: Which agent owns it now, e.g. `"Inventory"` or `"Accounting"`.
        note: A line appended to the ticket's notes, usually why.

    Returns the updated ticket.
    """
    if not assignee or not assignee.strip():
        return err(INVALID_INPUT, "assignee is required.")
    return _update_ticket(ticket_id, {"assignee": assignee.strip()}, note)


@tool_result
def add_ticket_note(ticket_id: int, note: str, author: str) -> dict[str, Any]:
    """Append a note to a ticket without changing its state.

    Args:
        ticket_id: The ticket to annotate.
        note: What to record — a finding, a decision, a reason something was refused.
        author: Which agent is writing.

    Notes accumulate; nothing is overwritten. This is where the reasoning behind a
    decision should live, so a human reading the board later can follow it.

    Returns the updated ticket.
    """
    if not note or not note.strip():
        return err(INVALID_INPUT, "note is required.")
    return _update_ticket(ticket_id, {}, f"[{author}] {note.strip()}")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _with_details(approval: dict[str, Any] | None) -> dict[str, Any] | None:
    """Parse the `details` JSON blob back into an object for the caller."""
    if approval is None:
        return None
    raw = approval.get("details")
    if isinstance(raw, str) and raw:
        try:
            approval["details"] = json.loads(raw)
        except json.JSONDecodeError:
            approval["details"] = {"unparsed": raw}
    return approval


def _fetch(conn: sqlite3.Connection, sql: str, params: tuple) -> dict[str, Any] | None:
    """First row of a parameterized query as a dict, or None."""
    row = conn.execute(sql, params).fetchone()
    return dict(row) if row is not None else None


def _today(conn: sqlite3.Connection) -> str:
    """The shop's date, read inside the open transaction."""
    row = conn.execute("SELECT date_today FROM desk LIMIT 1").fetchone()
    if row is None:
        raise RuntimeError("desk table is empty; the shop has no date.")
    return str(row["date_today"])


def _check_approval(
    conn: sqlite3.Connection,
    approval_id: int,
    kind: str,
    amount: float,
    require_ref: bool = True,
) -> dict[str, Any] | None:
    """Return a refusal if the approval cannot authorize this, or None if it can."""
    approval = _fetch(conn, "SELECT * FROM approvals WHERE id = ?", (approval_id,))
    if approval is None:
        return err(
            APPROVAL_REQUIRED,
            f"No approval with id {approval_id}. Call request_approval first.",
            approval_id=approval_id,
        )
    if approval["status"] != "approved":
        return err(
            APPROVAL_REQUIRED,
            f"Approval {approval_id} is {approval['status']}, not approved.",
            approval_id=approval_id,
            approval_status=approval["status"],
        )
    if approval["consumed_by_payment_id"] is not None:
        return err(
            APPROVAL_REQUIRED,
            f"Approval {approval_id} has already been used.",
            approval_id=approval_id,
        )
    if approval["kind"] != kind:
        return err(
            APPROVAL_REQUIRED,
            f"Approval {approval_id} covers {approval['kind']}, not {kind}.",
            approval_id=approval_id,
        )
    if abs(approval["amount"] - amount) > CENT:
        return err(
            APPROVAL_REQUIRED,
            f"Approval {approval_id} is for ${approval['amount']:,.2f}, but this comes to "
            f"${amount:,.2f}.",
            approval_id=approval_id,
            approved_amount=approval["amount"],
            actual_amount=amount,
        )
    return None


def _update_ticket(
    ticket_id: int, fields: dict[str, Any], note: str | None
) -> dict[str, Any]:
    """Apply field updates and an optional appended note to one ticket."""
    with db.transaction() as conn:
        ensure_app_tables(conn)
        today = _today(conn)

        ticket = _fetch(conn, "SELECT * FROM tickets WHERE id = ?", (ticket_id,))
        if ticket is None:
            return err(NOT_FOUND, f"No ticket with id {ticket_id}.", ticket_id=ticket_id)

        if fields:
            assignments = ", ".join(f"{column} = ?" for column in fields)
            conn.execute(
                f"UPDATE tickets SET {assignments} WHERE id = ?",  # columns are literals above
                (*fields.values(), ticket_id),
            )

        if note:
            existing = ticket["notes"] or ""
            appended = f"{existing}\n{today}: {note}".strip()
            conn.execute("UPDATE tickets SET notes = ? WHERE id = ?", (appended, ticket_id))

        updated = _fetch(conn, "SELECT * FROM tickets WHERE id = ?", (ticket_id,))

    return ok(updated)


WRITE_TOOLS = [
    request_approval,
    record_approval_decision,
    make_payment,
    create_purchase_order,
    save_draft_message,
    set_ticket_status,
    assign_ticket,
    add_ticket_note,
]
