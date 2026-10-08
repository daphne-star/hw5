"""Request bodies the API accepts.

Kept apart from `main.py` so the routes read as routes. These are the only shapes the
browser is allowed to send; everything the API *returns* is assembled from MCP tool
results, which already carry their own envelope and need no second definition here.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class RunRequest(BaseModel):
    """Start a run on a ticket."""

    agent: str | None = Field(
        default=None,
        description="Start with this agent instead of the Boss. Delegation still works.",
    )
    task: str | None = Field(
        default=None,
        description="Override the agent's opening task.",
    )


class DecisionRequest(BaseModel):
    """A human approving or rejecting a request for money."""

    decision: str = Field(description="'approved' or 'rejected'.")
    decided_by: str = Field(description="The name of the human deciding. Not an agent.")
    execute: bool = Field(
        default=True,
        description=(
            "On approval, also carry the action out — pay the invoice or rent, or place "
            "the purchase order. Set false to record the decision only."
        ),
    )


class ResetRequest(BaseModel):
    """Restore the working database from the original."""

    confirm: bool = Field(
        default=False,
        description="Must be true. Resetting discards shop state.",
    )
