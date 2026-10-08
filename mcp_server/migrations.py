"""Tables and triggers the shop needs but the original database does not ship with.

`campus_customs.db` has no place to park a *pending* approval, no purchase orders, no
customer drafts, and no assignee on a ticket. Those are added here.

This runs after every reset, because `scripts/reset_db.py` copies the original file
wholesale and anything added to the working copy goes with it.

The two cash triggers are the important part. "Cash only ever goes out" and "no payment
may overdraw an account" are enforced by SQLite itself, so they hold even if a tool is
called wrong, a future tool forgets to check, or an agent finds a path we did not
anticipate. Application code checks these rules too, in order to return a readable
refusal — the triggers are the floor underneath that.
"""

from __future__ import annotations

import sqlite3

# Columns added to tables that already exist: (table, column, type).
# `approvals.details` is listed so a database created before the column existed picks it
# up without being rebuilt.
ADDED_COLUMNS = [
    ("tickets", "assignee", "TEXT"),
    ("approvals", "details", "TEXT"),
]

SCHEMA = [
    # A request for a human to approve money leaving the shop. Created pending, then
    # decided. Single-use: once a payment consumes it, it cannot authorize another.
    """
    CREATE TABLE IF NOT EXISTS approvals (
        id INTEGER PRIMARY KEY,
        kind TEXT NOT NULL,
        ref_id INTEGER,
        amount REAL NOT NULL,
        account TEXT NOT NULL,
        reason TEXT NOT NULL,
        requested_by TEXT NOT NULL,
        ticket_id INTEGER,
        status TEXT NOT NULL DEFAULT 'pending',
        decided_by TEXT,
        decided_at TEXT,
        consumed_by_payment_id INTEGER,
        details TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY (ticket_id) REFERENCES tickets(id),
        CHECK (status IN ('pending', 'approved', 'rejected')),
        CHECK (amount > 0)
    )
    """,
    # An order placed with a vendor. A commitment, not a cash movement — the vendor
    # invoices separately.
    """
    CREATE TABLE IF NOT EXISTS purchase_orders (
        id INTEGER PRIMARY KEY,
        vendor_id INTEGER NOT NULL,
        sku TEXT NOT NULL,
        size TEXT NOT NULL,
        qty INTEGER NOT NULL,
        unit_cost REAL NOT NULL,
        total_cost REAL NOT NULL,
        ordered_on TEXT NOT NULL,
        expected_arrival TEXT NOT NULL,
        approval_id INTEGER,
        ticket_id INTEGER,
        status TEXT NOT NULL DEFAULT 'placed',
        notes TEXT,
        FOREIGN KEY (vendor_id) REFERENCES vendors(id),
        FOREIGN KEY (ticket_id) REFERENCES tickets(id),
        FOREIGN KEY (approval_id) REFERENCES approvals(id),
        CHECK (qty > 0)
    )
    """,
    # Customer messages. They are written here and shown on the board. Nothing sends them.
    """
    CREATE TABLE IF NOT EXISTS drafts (
        id INTEGER PRIMARY KEY,
        ticket_id INTEGER,
        recipient TEXT NOT NULL,
        subject TEXT NOT NULL,
        body TEXT NOT NULL,
        written_by TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY (ticket_id) REFERENCES tickets(id)
    )
    """,
    # Cash may only fall.
    """
    CREATE TRIGGER IF NOT EXISTS cash_never_increases
    BEFORE UPDATE OF balance ON cash_accounts
    FOR EACH ROW WHEN NEW.balance > OLD.balance
    BEGIN
        SELECT RAISE(ABORT, 'cash_increase_forbidden: a cash balance may only go down');
    END
    """,
    # Cash may not fall below zero.
    """
    CREATE TRIGGER IF NOT EXISTS cash_never_negative
    BEFORE UPDATE OF balance ON cash_accounts
    FOR EACH ROW WHEN NEW.balance < 0
    BEGIN
        SELECT RAISE(ABORT, 'insufficient_funds: a cash balance may not go negative');
    END
    """,
    # Closes the obvious way around the first trigger: a brand new, funded account.
    """
    CREATE TRIGGER IF NOT EXISTS cash_no_new_accounts
    BEFORE INSERT ON cash_accounts
    BEGIN
        SELECT RAISE(ABORT, 'cash_increase_forbidden: cash accounts may not be created');
    END
    """,
    # Every payment must name its approval, and a payment row is never edited after the fact.
    """
    CREATE TRIGGER IF NOT EXISTS payments_are_immutable
    BEFORE UPDATE ON payments
    BEGIN
        SELECT RAISE(ABORT, 'conflict: payment records are an audit trail and cannot be changed');
    END
    """,
]


def ensure_app_tables(conn: sqlite3.Connection) -> None:
    """Create the app's own tables and guards on an open read-write connection.

    Idempotent — safe to call on every reset and before any write.
    """
    for statement in SCHEMA:
        conn.execute(statement)

    for table, column, column_type in ADDED_COLUMNS:
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")
