"""One shared trace per ticket, recording everything every agent did.

A ticket run is a tree — the Boss delegates, that agent delegates again — but the trace
is deliberately flat and ordered. Each entry carries which agent acted, how deep in the
delegation chain it was, and what came back, so the whole run reads top to bottom.

Timestamps here are real wall-clock UTC, because they describe the run. Dates written
*into the shop* use `desk.date_today` instead. The two are different clocks on purpose.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

TOOL_CALL = "tool_call"
DELEGATION = "delegation"
MESSAGE = "message"
REFUSAL = "refusal"
LIMIT = "limit"
ERROR = "error"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass
class RunTrace:
    """Everything that happened while resolving one ticket."""

    ticket_id: int
    started_at: str = field(default_factory=_now)
    entries: list[dict[str, Any]] = field(default_factory=list)

    # Budgets, shared across every agent in the run.
    tool_calls_used: int = 0
    delegations_used: int = 0

    def _add(self, kind: str, agent: str, depth: int, **fields: Any) -> dict[str, Any]:
        entry = {
            "seq": len(self.entries) + 1,
            "at": _now(),
            "agent": agent,
            "depth": depth,
            "kind": kind,
            **fields,
        }
        self.entries.append(entry)
        return entry

    def tool_call(
        self, agent: str, depth: int, name: str, arguments: dict, result: dict
    ) -> dict[str, Any]:
        """Record a tool call and what the shop said back.

        A refused call is logged as a refusal rather than a tool call, so scanning the
        trace for "where did the rules bite" does not mean reading every result body.
        """
        refused = not result.get("ok", True)
        code = (result.get("error") or {}).get("code") if refused else None
        return self._add(
            REFUSAL if refused else TOOL_CALL,
            agent,
            depth,
            tool=name,
            arguments=arguments,
            result=result,
            error_code=code,
        )

    def delegation(
        self, from_agent: str, to_agent: str, depth: int, task: str
    ) -> dict[str, Any]:
        self.delegations_used += 1
        return self._add(DELEGATION, from_agent, depth, to_agent=to_agent, task=task)

    def message(self, agent: str, depth: int, text: str) -> dict[str, Any]:
        """An agent's own words — its answer, or its reasoning between tool calls."""
        return self._add(MESSAGE, agent, depth, text=text)

    def limit(self, agent: str, depth: int, which: str, detail: str) -> dict[str, Any]:
        """A budget ran out. Recorded loudly, because it means the answer is partial."""
        return self._add(LIMIT, agent, depth, limit=which, detail=detail)

    def error(self, agent: str, depth: int, detail: str) -> dict[str, Any]:
        return self._add(ERROR, agent, depth, detail=detail)

    # --- summary ----------------------------------------------------------

    def agents_involved(self) -> list[str]:
        """Every agent that acted, in the order they first did."""
        seen: list[str] = []
        for entry in self.entries:
            if entry["agent"] not in seen:
                seen.append(entry["agent"])
        return seen

    def handoffs(self) -> list[str]:
        return [
            f"{e['agent']} -> {e['to_agent']}" for e in self.entries if e["kind"] == DELEGATION
        ]

    def refusals(self) -> list[dict[str, Any]]:
        return [e for e in self.entries if e["kind"] == REFUSAL]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticket_id": self.ticket_id,
            "started_at": self.started_at,
            "finished_at": _now(),
            "agents_involved": self.agents_involved(),
            "handoffs": self.handoffs(),
            "tool_calls_used": self.tool_calls_used,
            "delegations_used": self.delegations_used,
            "refusal_count": len(self.refusals()),
            "entries": self.entries,
        }
