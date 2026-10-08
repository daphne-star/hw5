"""In-flight runs, so the dashboard can watch agents work instead of waiting on a spinner.

A run is started as a background task and its `RunTrace` is handed to the registry
*before* the work begins. The trace is appended to as the run proceeds, so polling or
streaming the record shows entries arriving live rather than all at once at the end.

This registry is in-process and resets when the server restarts. That is deliberate:
`output/audit_trail.json` is the durable record, and duplicating it in memory would
create two sources of truth that could disagree. What lives here is only what a UI
needs while a run is still happening.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine

from .trace import RunTrace

QUEUED = "queued"
RUNNING = "running"
DONE = "done"
FAILED = "failed"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class RunRecord:
    """One run, from the moment it is requested to the moment it finishes."""

    def __init__(self, run_id: str, ticket_id: int, mode: str, agent: str) -> None:
        self.run_id = run_id
        self.ticket_id = ticket_id
        self.mode = mode
        self.agent = agent
        self.status = QUEUED
        self.requested_at = _now()
        self.finished_at: str | None = None
        self.trace = RunTrace(ticket_id=ticket_id)
        self.answer: str | None = None
        self.error: str | None = None
        self.changes: dict[str, Any] | None = None
        self.task: asyncio.Task | None = None

    def snapshot(self, since: int = 0) -> dict[str, Any]:
        """The run's current state, with trace entries after `since` only.

        `since` is a sequence number, not an index, so a poller sends back the last
        `seq` it saw and gets exactly what it has not seen.
        """
        entries = [e for e in self.trace.entries if e["seq"] > since]
        return {
            "run_id": self.run_id,
            "ticket_id": self.ticket_id,
            "mode": self.mode,
            "agent": self.agent,
            "status": self.status,
            "requested_at": self.requested_at,
            "finished_at": self.finished_at,
            "answer": self.answer,
            "error": self.error,
            "changes": self.changes,
            "agents_involved": self.trace.agents_involved(),
            "handoffs": self.trace.handoffs(),
            "tool_calls_used": self.trace.tool_calls_used,
            "delegations_used": self.trace.delegations_used,
            "entry_count": len(self.trace.entries),
            "last_seq": self.trace.entries[-1]["seq"] if self.trace.entries else 0,
            "entries": entries,
        }

    @property
    def is_active(self) -> bool:
        return self.status in (QUEUED, RUNNING)


class RunRegistry:
    """Every run this process has started, newest last."""

    def __init__(self) -> None:
        self._runs: dict[str, RunRecord] = {}

    def get(self, run_id: str) -> RunRecord | None:
        return self._runs.get(run_id)

    def all(self) -> list[RunRecord]:
        return list(self._runs.values())

    def active(self) -> list[RunRecord]:
        return [r for r in self._runs.values() if r.is_active]

    def active_for_ticket(self, ticket_id: int) -> RunRecord | None:
        return next(
            (r for r in self._runs.values() if r.ticket_id == ticket_id and r.is_active), None
        )

    def start(
        self,
        ticket_id: int,
        mode: str,
        agent: str,
        work: Callable[[RunTrace], Coroutine[Any, Any, dict]],
    ) -> RunRecord:
        """Register a run and kick it off in the background.

        `work` is handed the record's trace so entries land where the registry can see
        them while the run is still going.
        """
        record = RunRecord(f"run_{uuid.uuid4().hex[:12]}", ticket_id, mode, agent)
        self._runs[record.run_id] = record

        async def execute() -> None:
            record.status = RUNNING
            try:
                result = await work(record.trace)
                record.answer = result.get("answer")
                record.changes = result.get("changes")
                record.status = DONE
            except Exception as exc:
                # A crashed run is still a run. It stays in the registry with the
                # reason attached rather than vanishing.
                record.error = f"{type(exc).__name__}: {exc}"
                record.trace.error(agent, 1, record.error)
                record.status = FAILED
            finally:
                record.finished_at = _now()

        record.task = asyncio.create_task(execute())
        return record

    def recent_events(self, limit: int = 50) -> list[dict[str, Any]]:
        """The newest trace entries across every run in this process, newest first."""
        events: list[dict[str, Any]] = []
        for record in self._runs.values():
            for entry in record.trace.entries:
                events.append({**entry, "run_id": record.run_id,
                               "ticket_id": record.ticket_id})
        events.sort(key=lambda e: (e["at"], e["seq"]), reverse=True)
        return events[:limit]


registry = RunRegistry()
