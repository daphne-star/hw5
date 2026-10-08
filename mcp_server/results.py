"""The one response shape every tool returns.

Success:
    {"ok": true,  "data": <object or list>, "meta": {...}}

Failure:
    {"ok": false, "error": {"code": "not_found", "message": "...", "details": {...}}}

A tool never raises at its boundary and never returns a bare string. An agent reading
the result can branch on `ok` alone, and on `error.code` when it needs to tell a
missing row apart from a refused action.
"""

from __future__ import annotations

import functools
import sqlite3
from typing import Any, Callable

# Error codes a caller can branch on.
NOT_FOUND = "not_found"
INVALID_INPUT = "invalid_input"
DB_UNAVAILABLE = "db_unavailable"
READ_ONLY_VIOLATION = "read_only_violation"
INTERNAL = "internal_error"

# Refusals — the shop's rules saying no. These are not failures to be retried or worked
# around; they are the correct outcome, and the message says what would have to change.
INSUFFICIENT_FUNDS = "insufficient_funds"
APPROVAL_REQUIRED = "approval_required"
VENDOR_BLOCKED = "vendor_blocked"
CASH_INCREASE_FORBIDDEN = "cash_increase_forbidden"
CONFLICT = "conflict"

REFUSAL_CODES = frozenset(
    {INSUFFICIENT_FUNDS, APPROVAL_REQUIRED, VENDOR_BLOCKED, CASH_INCREASE_FORBIDDEN, CONFLICT}
)


def ok(data: Any, **meta: Any) -> dict[str, Any]:
    """Wrap a successful result. Extra keyword arguments become `meta`."""
    result: dict[str, Any] = {"ok": True, "data": data}
    if meta:
        result["meta"] = meta
    return result


def err(code: str, message: str, **details: Any) -> dict[str, Any]:
    """Wrap a failure. `message` is written to be read by a model, not a developer."""
    error: dict[str, Any] = {"code": code, "message": message}
    if details:
        error["details"] = details
    return {"ok": False, "error": error}


def tool_result(func: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
    """Catch anything a tool body throws and return it in the standard error shape.

    Without this, an exception crosses the MCP boundary as a transport error and the
    agent sees a stack trace instead of something it can act on.
    """

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return func(*args, **kwargs)
        except sqlite3.IntegrityError as exc:
            # A database trigger refused the write. The triggers raise messages shaped
            # "code: explanation", so the refusal keeps its meaning instead of surfacing
            # as a generic integrity error.
            head, _, tail = str(exc).partition(": ")
            if head in REFUSAL_CODES:
                return err(head, tail or head, enforced_by="database trigger")
            return err(CONFLICT, f"The database rejected this write: {exc}")
        except sqlite3.OperationalError as exc:
            if "readonly" in str(exc).lower():
                return err(
                    READ_ONLY_VIOLATION,
                    "This tool may only read. The write was rejected by the database.",
                    sqlite_error=str(exc),
                )
            return err(DB_UNAVAILABLE, f"Database error: {exc}")
        except (RuntimeError, OSError) as exc:
            return err(DB_UNAVAILABLE, str(exc))
        except (ValueError, TypeError) as exc:
            return err(INVALID_INPUT, str(exc))
        except Exception as exc:  # last resort; the agent still gets a usable shape
            return err(INTERNAL, f"{type(exc).__name__}: {exc}")

    return wrapper
