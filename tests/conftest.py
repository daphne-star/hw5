"""Each test gets its own throwaway copy of the shop.

`db.WORKING_DB` is module-level, so pointing it at a temp file redirects every tool —
reads and writes alike — without the tools knowing. Nothing here touches the real
`data/campus_customs_new.db`, and the pristine original is only ever read.
"""

from __future__ import annotations

import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from mcp_server import db  # noqa: E402
from mcp_server.migrations import ensure_app_tables  # noqa: E402

ORIGINAL_DB = PROJECT_ROOT / "data" / "campus_customs.db"


@pytest.fixture
def shop(tmp_path, monkeypatch) -> Path:
    """A fresh copy of the shop database, with the app's tables and guards applied."""
    working = tmp_path / "campus_customs_new.db"
    shutil.copyfile(ORIGINAL_DB, working)

    with sqlite3.connect(working) as conn:
        ensure_app_tables(conn)

    monkeypatch.setattr(db, "WORKING_DB", working)
    return working


@pytest.fixture
def approved(shop):
    """Make an approval and have a human approve it. Returns its id.

    Most refusals need a *valid* approval in hand, so that the thing being tested is the
    rule under test and not a missing approval.
    """
    from mcp_server import write_tools

    def _approve(kind: str, amount: float, ref_id=None, account: str = "checking") -> int:
        request = write_tools.request_approval(
            kind=kind,
            amount=amount,
            account=account,
            reason=f"test approval for {kind} {ref_id}",
            requested_by="Accounting",
            ref_id=ref_id,
        )
        assert request["ok"], request
        approval_id = request["data"]["id"]

        decision = write_tools.record_approval_decision(
            approval_id=approval_id, decision="approved", decided_by="Daphne"
        )
        assert decision["ok"], decision
        return approval_id

    return _approve


def balance_of(account: str = "checking") -> float:
    """Current balance, read straight from the database rather than through a tool."""
    row = db.query_one("SELECT balance FROM cash_accounts WHERE name = ?", (account,))
    return row["balance"]
