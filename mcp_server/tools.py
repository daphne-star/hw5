"""Read-only tools over the Campus Customs working database.

These are plain functions so they can be called three ways without change: by the MCP
server in `server.py`, by the smoke-test script, and by tests. Each returns the
standard envelope from `results.py` and never raises across its boundary.

Every tool that involves a date compares against `desk.date_today`, never the system
clock, and every query is parameterized.
"""

from __future__ import annotations

import json
from typing import Any

from . import db
from .results import INVALID_INPUT, NOT_FOUND, err, ok, tool_result

# ---------------------------------------------------------------------------
# Desk
# ---------------------------------------------------------------------------


@tool_result
def get_today() -> dict[str, Any]:
    """Get the shop's current date and any note left at the front desk.

    This date — not the real-world calendar — decides what is due, overdue, or able to
    arrive in time. Call this before any reasoning that involves dates.

    Returns `date_today` as `YYYY-MM-DD` and `notes`, which may be null.
    """
    row = db.query_one("SELECT date_today, notes FROM desk LIMIT 1")
    if row is None:
        return err(NOT_FOUND, "The desk table is empty, so the shop has no date set.")
    return ok(row)


# ---------------------------------------------------------------------------
# Tickets
# ---------------------------------------------------------------------------


@tool_result
def list_tickets(status: str | None = None, ticket_type: str | None = None) -> dict[str, Any]:
    """List tickets on the board, newest first, optionally filtered.

    Args:
        status: Keep only tickets in this state, e.g. `"open"`. Omit for every status.
        ticket_type: Keep only this kind of ticket. Known kinds are `"customer_order"`,
            `"rent_notice"`, and `"price_override"`. Omit for every kind.

    Each ticket carries only the fields its kind needs; the rest are null. A
    `customer_order` has `sku`, `size`, and `qty`; a `rent_notice` has `lease_id`; a
    ticket tied to a bill has `invoice_id`. `created_at` is a full timestamp with a UTC
    offset, unlike every other date in this database, which is a bare `YYYY-MM-DD`.

    Returns a list of tickets and, in `meta`, the count and the filters applied.
    """
    sql = "SELECT * FROM tickets"
    params: list[Any] = []
    where: list[str] = []

    if status is not None:
        where.append("status = ?")
        params.append(status)
    if ticket_type is not None:
        where.append("type = ?")
        params.append(ticket_type)

    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC, id DESC"

    rows = db.query(sql, tuple(params))
    return ok(rows, count=len(rows), status=status, ticket_type=ticket_type)


@tool_result
def get_ticket(ticket_id: int) -> dict[str, Any]:
    """Get one ticket by its id, with the lease and invoice it points at already resolved.

    Args:
        ticket_id: The ticket number, e.g. `101`.

    Saves a second lookup: if the ticket has a `lease_id` or an `invoice_id`, the full
    related row is included under `lease` and `invoice`. Either may be null. When an
    invoice is attached, `invoice.is_overdue` is computed against the shop's today.

    Returns the ticket object, or a `not_found` error if no such ticket exists.
    """
    ticket = db.query_one("SELECT * FROM tickets WHERE id = ?", (ticket_id,))
    if ticket is None:
        return err(
            NOT_FOUND,
            f"No ticket with id {ticket_id}. Use list_tickets to see what is on the board.",
            ticket_id=ticket_id,
        )

    ticket["lease"] = (
        db.query_one("SELECT * FROM leases WHERE id = ?", (ticket["lease_id"],))
        if ticket["lease_id"] is not None
        else None
    )

    invoice = (
        db.query_one("SELECT * FROM invoices WHERE id = ?", (ticket["invoice_id"],))
        if ticket["invoice_id"] is not None
        else None
    )
    if invoice is not None:
        _annotate_invoice(invoice, db.today())
    ticket["invoice"] = invoice

    return ok(ticket)


# ---------------------------------------------------------------------------
# Inventory and pricing
# ---------------------------------------------------------------------------


@tool_result
def get_inventory(sku: str | None = None, size: str | None = None) -> dict[str, Any]:
    """Look up stock on hand, by SKU and size.

    Args:
        sku: Product code, e.g. `"CC-TEE-WHITE"`. Omit to see every product.
        size: Size code: `"S"`, `"M"`, `"L"`, `"XL"` for apparel, or `"OS"` for one-size
            goods like hats and mugs. Omit to see every size of the given SKU.

    Stock is tracked per `(sku, size)` pair. A size with `qty` 0 is out of stock but
    still listed — a missing row means the shop does not carry that size at all, which
    is a different thing. Stock in another size does not help fill an order.

    Returns matching rows with `qty` and `location`, plus the total units in `meta`.
    """
    sql = "SELECT sku, name, size, qty, location FROM inventory"
    params: list[Any] = []
    where: list[str] = []

    if sku is not None:
        where.append("sku = ?")
        params.append(sku)
    if size is not None:
        where.append("size = ?")
        params.append(size)

    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY sku, size"

    rows = db.query(sql, tuple(params))
    if not rows and sku is not None:
        known = [r["sku"] for r in db.query("SELECT DISTINCT sku FROM inventory ORDER BY sku")]
        return err(
            NOT_FOUND,
            f"No inventory row for sku {sku!r}"
            + (f" in size {size!r}." if size else ".")
            + " The shop may not carry it.",
            sku=sku,
            size=size,
            known_skus=known,
        )

    return ok(rows, count=len(rows), total_units=sum(r["qty"] for r in rows))


@tool_result
def get_pricing(sku: str | None = None) -> dict[str, Any]:
    """Get unit cost and list price for a product, with its margin worked out.

    Args:
        sku: Product code, e.g. `"CC-HOOD-NAVY"`. Omit for the whole price list.

    Pricing is per SKU, not per size — every size of a product costs the same. Each row
    adds `margin_per_unit` (list price minus unit cost) and `margin_rate` (that over
    list price, rounded to four places). Those are the numbers a discount request has to
    be judged against: a discount that drops the price below `unit_cost` sells at a loss.

    Returns matching price rows, or a `not_found` error if the SKU has no price set.
    """
    sql = "SELECT sku, unit_cost, list_price FROM pricing"
    params: tuple[Any, ...] = ()
    if sku is not None:
        sql += " WHERE sku = ?"
        params = (sku,)
    sql += " ORDER BY sku"

    rows = db.query(sql, params)
    if not rows and sku is not None:
        known = [r["sku"] for r in db.query("SELECT sku FROM pricing ORDER BY sku")]
        return err(
            NOT_FOUND,
            f"No pricing row for sku {sku!r}.",
            sku=sku,
            known_skus=known,
        )

    for row in rows:
        row["margin_per_unit"] = round(row["list_price"] - row["unit_cost"], 2)
        row["margin_rate"] = (
            round((row["list_price"] - row["unit_cost"]) / row["list_price"], 4)
            if row["list_price"]
            else None
        )

    return ok(rows, count=len(rows))


# ---------------------------------------------------------------------------
# Vendors
# ---------------------------------------------------------------------------


@tool_result
def list_vendors(specialty_contains: str | None = None) -> dict[str, Any]:
    """List vendors with their lead times and the earliest date an order could land.

    Args:
        specialty_contains: Case-insensitive substring of the specialty, e.g. `"apparel"`
            or `"mug"`. Omit to list every vendor.

    There is no vendor-to-SKU table. Matching a product to a vendor means reading
    `specialty` — "apparel reprint" covers tees and hoodies, "mugs and small goods"
    covers mugs, "local courier" moves things that already exist rather than making new
    stock. Each row adds `earliest_arrival`, which is the shop's today plus `lead_days`.

    Whether a vendor will actually ship is a separate question — check `list_invoices`
    for an open invoice against them before planning an order.

    Returns matching vendors and, in `meta`, the date the arrival estimates are based on.
    """
    sql = "SELECT id, name, specialty, lead_days FROM vendors"
    params: tuple[Any, ...] = ()
    if specialty_contains is not None:
        sql += " WHERE LOWER(specialty) LIKE LOWER(?)"
        params = (f"%{specialty_contains}%",)
    sql += " ORDER BY lead_days, id"

    rows = db.query(sql, params)
    today = db.today()
    for row in rows:
        row["earliest_arrival"] = db.add_days(today, row["lead_days"])

    return ok(rows, count=len(rows), as_of=today)


# ---------------------------------------------------------------------------
# Facilities
# ---------------------------------------------------------------------------


@tool_result
def list_leases() -> dict[str, Any]:
    """List the shop's leases with rent amounts and how close the next payment is.

    Each lease adds `days_until_due` (negative once the date has passed) and
    `is_overdue`, both measured against the shop's today rather than the real calendar.

    Returns every lease, plus the date the comparison was made against in `meta`.
    """
    rows = db.query(
        "SELECT id, space_name, landlord, monthly_rent, next_due, notes "
        "FROM leases ORDER BY next_due, id"
    )
    today = db.today()
    for row in rows:
        row["days_until_due"] = db.days_between(today, row["next_due"])
        row["is_overdue"] = row["next_due"] < today

    return ok(rows, count=len(rows), as_of=today)


# ---------------------------------------------------------------------------
# Money
# ---------------------------------------------------------------------------


@tool_result
def get_cash_accounts(name: str | None = None) -> dict[str, Any]:
    """Get cash balances. This is the only record of how much money the shop has.

    Args:
        name: Account name, e.g. `"checking"`. Accounts are keyed by name, not by id.
            Omit for every account.

    The balance is a stored number, not a sum of the `payments` table, so it is the
    figure to check before planning a payment. In this shop cash only ever leaves: no
    revenue arrives, and no balance goes up.

    Returns matching accounts with `balance` and the `date` the balance is stated as of,
    plus the combined total in `meta`.
    """
    sql = "SELECT name, balance, date FROM cash_accounts"
    params: tuple[Any, ...] = ()
    if name is not None:
        sql += " WHERE name = ?"
        params = (name,)
    sql += " ORDER BY name"

    rows = db.query(sql, params)
    if not rows and name is not None:
        known = [r["name"] for r in db.query("SELECT name FROM cash_accounts ORDER BY name")]
        return err(
            NOT_FOUND,
            f"No cash account named {name!r}.",
            name=name,
            known_accounts=known,
        )

    return ok(rows, count=len(rows), total_balance=round(sum(r["balance"] for r in rows), 2))


@tool_result
def list_invoices(status: str | None = None, vendor_id: int | None = None) -> dict[str, Any]:
    """List bills the shop owes, flagged for whether they are overdue.

    Args:
        status: Keep only invoices in this state, e.g. `"open"` or `"paid"`. Omit for all.
        vendor_id: Keep only invoices from this vendor. Omit for all.

    Each row adds the vendor's name, `is_overdue` (open and past the shop's today), and
    `days_overdue`. An invoice with status `"open"` means that vendor is still owed
    money, which is what blocks them from shipping anything new.

    Returns matching invoices, with the open count and total owed in `meta`.
    """
    sql = (
        "SELECT i.id, i.vendor_id, v.name AS vendor_name, i.amount, i.due_date, "
        "i.status, i.description "
        "FROM invoices i LEFT JOIN vendors v ON v.id = i.vendor_id"
    )
    params: list[Any] = []
    where: list[str] = []

    if status is not None:
        where.append("i.status = ?")
        params.append(status)
    if vendor_id is not None:
        where.append("i.vendor_id = ?")
        params.append(vendor_id)

    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY i.due_date, i.id"

    rows = db.query(sql, tuple(params))
    today = db.today()
    for row in rows:
        _annotate_invoice(row, today)

    open_rows = [r for r in rows if r["status"] == "open"]
    return ok(
        rows,
        count=len(rows),
        as_of=today,
        open_count=len(open_rows),
        open_total=round(sum(r["amount"] for r in open_rows), 2),
    )


@tool_result
def list_payments(kind: str | None = None) -> dict[str, Any]:
    """List money that has already left the shop, and who approved each payment.

    Args:
        kind: Keep only payments of this kind, e.g. `"invoice"` or `"rent"`. Omit for all.

    Starts empty — every row here is written during a run. `ref_id` points at whatever
    `kind` says it does: an invoice id for an invoice payment, a lease id for rent.
    `approved_by` can never be blank, so a payment with no human approval behind it
    cannot exist in this table.

    Returns matching payments, with the count and total paid out in `meta`.
    """
    sql = "SELECT id, kind, ref_id, amount, account, paid_at, approved_by FROM payments"
    params: tuple[Any, ...] = ()
    if kind is not None:
        sql += " WHERE kind = ?"
        params = (kind,)
    sql += " ORDER BY paid_at, id"

    rows = db.query(sql, params)
    return ok(rows, count=len(rows), total_paid=round(sum(r["amount"] for r in rows), 2))


@tool_result
def list_approvals(status: str | None = None) -> dict[str, Any]:
    """List approval requests and what humans decided about them.

    Args:
        status: Keep only `"pending"`, `"approved"`, or `"rejected"`. Omit for all.

    `pending` means a human has not looked at it yet — the money has not moved and will
    not until they do. An approval with `consumed_by_payment_id` set has already been
    spent and cannot authorize anything else.

    Returns matching approvals, with the pending count in `meta`.
    """
    sql = "SELECT * FROM approvals"
    params: tuple[Any, ...] = ()
    if status is not None:
        sql += " WHERE status = ?"
        params = (status,)
    sql += " ORDER BY id"

    rows = db.query(sql, params)
    for row in rows:
        raw = row.get("details")
        if isinstance(raw, str) and raw:
            try:
                row["details"] = json.loads(raw)
            except json.JSONDecodeError:
                row["details"] = {"unparsed": raw}
    pending = [r for r in rows if r["status"] == "pending"]
    return ok(rows, count=len(rows), pending_count=len(pending))


@tool_result
def list_purchase_orders(status: str | None = None) -> dict[str, Any]:
    """List restock orders placed with vendors, and when each is expected to land.

    Args:
        status: Keep only orders in this state, e.g. `"placed"`. Omit for all.

    Each row carries the vendor's name and `expected_arrival`, computed from the
    vendor's lead time when the order was placed.

    Returns matching purchase orders, with the committed spend in `meta`.
    """
    sql = (
        "SELECT p.*, v.name AS vendor_name FROM purchase_orders p "
        "LEFT JOIN vendors v ON v.id = p.vendor_id"
    )
    params: tuple[Any, ...] = ()
    if status is not None:
        sql += " WHERE p.status = ?"
        params = (status,)
    sql += " ORDER BY p.id"

    rows = db.query(sql, params)
    return ok(rows, count=len(rows), committed=round(sum(r["total_cost"] for r in rows), 2))


@tool_result
def list_drafts(ticket_id: int | None = None) -> dict[str, Any]:
    """List customer messages written to the board. None of them have been sent.

    Args:
        ticket_id: Keep only drafts for this ticket. Omit for all.

    This shop does not send email or call anyone. Everything here is waiting for a human
    to read and act on, which is the intended end state, not an unfinished one.

    Returns matching drafts.
    """
    sql = "SELECT * FROM drafts"
    params: tuple[Any, ...] = ()
    if ticket_id is not None:
        sql += " WHERE ticket_id = ?"
        params = (ticket_id,)
    sql += " ORDER BY id"

    rows = db.query(sql, params)
    return ok(rows, count=len(rows), delivered=False)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _annotate_invoice(invoice: dict[str, Any], today: str) -> None:
    """Add `is_overdue` and `days_overdue` to an invoice row, in place."""
    overdue = invoice["status"] == "open" and invoice["due_date"] < today
    invoice["is_overdue"] = overdue
    invoice["days_overdue"] = db.days_between(invoice["due_date"], today) if overdue else 0


# Every read tool, in the order the smoke test and the MCP server register them.
READ_TOOLS = [
    get_today,
    list_tickets,
    get_ticket,
    get_inventory,
    get_pricing,
    list_vendors,
    list_leases,
    get_cash_accounts,
    list_invoices,
    list_payments,
    list_approvals,
    list_purchase_orders,
    list_drafts,
]
