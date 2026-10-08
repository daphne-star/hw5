"""Database access for the Campus Customs MCP server.

Everything here reads `data/campus_customs_new.db`, the working copy. The pristine
`data/campus_customs.db` is the reset point and is never opened by this module.

Two things are enforced here rather than left to the caller:

* Connections are opened through a SQLite URI with `mode=ro`, so a stray `UPDATE`
  in a read tool fails at the driver instead of corrupting the working copy.
* Every query goes through `query()` / `query_one()`, which take a parameter tuple.
  No tool builds SQL by string formatting.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WORKING_DB = PROJECT_ROOT / "data" / "campus_customs_new.db"


class DatabaseMissing(RuntimeError):
    """The working copy does not exist yet."""


def connect_readonly() -> sqlite3.Connection:
    """Open the working database read-only. Writes raise sqlite3.OperationalError."""
    if not WORKING_DB.exists():
        raise DatabaseMissing(
            f"{WORKING_DB.name} not found. Run `python scripts/reset_db.py` first."
        )

    uri = f"{WORKING_DB.as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """Open a read-write connection inside one all-or-nothing transaction.

    A payment touches three tables — it inserts the payment, lowers the balance, and
    closes the invoice. Those have to land together or not at all, because the balance
    is a stored number rather than a sum over the payments table, and a half-applied
    payment would leave the two permanently disagreeing.

    Foreign keys are on, so a reference to a row that does not exist fails here rather
    than becoming a dangling id. Any exception rolls the whole thing back.
    """
    if not WORKING_DB.exists():
        raise DatabaseMissing(
            f"{WORKING_DB.name} not found. Run `python scripts/reset_db.py` first."
        )

    conn = sqlite3.connect(WORKING_DB, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN IMMEDIATE")
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")
    finally:
        conn.close()


def query(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    """Run a parameterized SELECT and return rows as dicts."""
    with connect_readonly() as conn:
        return [dict(row) for row in conn.execute(sql, params)]


def query_one(sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    """Run a parameterized SELECT and return the first row as a dict, or None."""
    rows = query(sql, params)
    return rows[0] if rows else None


def today() -> str:
    """The shop's current date from `desk.date_today`, as `YYYY-MM-DD`.

    This is the only notion of "now" in the project. The system clock is never used
    to decide what is due or overdue.
    """
    row = query_one("SELECT date_today FROM desk LIMIT 1")
    if row is None:
        raise RuntimeError("desk table is empty; the shop has no date.")
    return str(row["date_today"])


def days_between(start: str, end: str) -> int:
    """Whole days from `start` to `end`, both `YYYY-MM-DD`. Negative if end precedes start."""
    from datetime import date

    return (date.fromisoformat(end) - date.fromisoformat(start)).days


def add_days(start: str, days: int) -> str:
    """`start` (`YYYY-MM-DD`) shifted forward by `days`, returned in the same format."""
    from datetime import date, timedelta

    return (date.fromisoformat(start) + timedelta(days=days)).isoformat()
