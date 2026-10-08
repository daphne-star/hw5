"""Reset the working database.

Copies the pristine `data/campus_customs.db` over `data/campus_customs_new.db`.
The original is never written to; it is the reset point. Everything the app does
happens against the copy, so a full run can always be started from clean state.

Usage:
    python scripts/reset_db.py
"""

from __future__ import annotations

import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp_server.migrations import ensure_app_tables  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ORIGINAL_DB = PROJECT_ROOT / "data" / "campus_customs.db"
WORKING_DB = PROJECT_ROOT / "data" / "campus_customs_new.db"

# SQLite side files that would otherwise survive the copy and contradict it.
SIDECAR_SUFFIXES = ("-journal", "-wal", "-shm")


def reset() -> Path:
    if not ORIGINAL_DB.exists():
        raise FileNotFoundError(
            f"Original database not found at {ORIGINAL_DB}. "
            "Unzip data.zip into data/ before running this."
        )

    for suffix in SIDECAR_SUFFIXES:
        sidecar = WORKING_DB.with_name(WORKING_DB.name + suffix)
        sidecar.unlink(missing_ok=True)

    shutil.copyfile(ORIGINAL_DB, WORKING_DB)

    # The original has nowhere to park a pending approval, no purchase orders, no
    # drafts, and no cash guards. Add them back every time, since the copy wipes them.
    with sqlite3.connect(WORKING_DB) as conn:
        ensure_app_tables(conn)
        tables = sorted(
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%'"
            )
        )

    if not tables:
        raise RuntimeError(f"Copied database at {WORKING_DB} contains no tables.")

    return WORKING_DB


def main() -> int:
    try:
        path = reset()
    except (FileNotFoundError, RuntimeError, OSError) as exc:
        print(f"Reset failed: {exc}", file=sys.stderr)
        return 1

    print(f"Reset {path.relative_to(PROJECT_ROOT)} from {ORIGINAL_DB.name}, "
          "with the app's tables and cash guards in place.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
