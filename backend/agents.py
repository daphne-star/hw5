"""Who the agents are and which tools each one can reach.

The allowlists are the real division of labour. Inventory cannot pay for anything
because `make_payment` is not in its list — not because its prompt asks it not to. An
agent that needs something outside its list has to delegate, which is what produces
visible handoffs instead of one agent quietly doing the whole job.

Every agent gets `delegate_to`, so the team is fully connected: any agent may hand work
to any other, not only back to the Boss.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

# Facts about the shop every agent needs, so no one has to spend a turn asking.
SHARED_CONTEXT = """
## How this shop works

- The shop's today comes from `get_today`. Never use the real calendar.
- Stock is tracked per (sku, size). Stock in another size cannot fill an order.
- Vendors are matched to products by their `specialty` text. A vendor with an open
  invoice against them will not ship anything new until it is paid.
- Cash is one stored balance and only ever goes down. No revenue arrives.
- No money moves without a human approving the exact amount first.
- The shop never emails or calls anyone. Customer messages are saved as drafts.

## Tools

Every tool returns `{"ok": true, "data": ...}` or
`{"ok": false, "error": {"code": ..., "message": ...}}`.

A refusal is a correct answer, not an obstacle. Read the message — it says what would
have to change — and act on that. Do not retry the same call hoping for a different
result, and do not look for a way around the rule. There isn't one.

If a task needs something outside your tools, use `delegate_to`. Ask for what you need
in one clear sentence and include the ticket id.
""".strip()


@dataclass(frozen=True)
class Agent:
    name: str
    role: str
    prompt_file: str
    tools: list[str] = field(default_factory=list)

    def system_prompt(self) -> str:
        """The agent's own brief, plus the shop facts everyone shares."""
        own = (PROMPTS_DIR / self.prompt_file).read_text(encoding="utf-8").strip()
        return f"{own}\n\n---\n\n{SHARED_CONTEXT}"


# Reads everyone is allowed. Knowing the date and the state of the board is not a
# privilege, and withholding it just costs a delegation.
COMMON_READS = ["get_today", "list_tickets", "get_ticket"]

# Every agent can annotate the ticket it is working on.
COMMON_WRITES = ["add_ticket_note"]

AGENTS: dict[str, Agent] = {
    "Boss": Agent(
        name="Boss",
        role="Triages each ticket, routes it, and makes the final call.",
        prompt_file="boss.md",
        tools=[
            *COMMON_READS,
            "get_inventory",
            "get_cash_accounts",
            "list_invoices",
            "list_approvals",
            "list_purchase_orders",
            "list_drafts",
            "list_payments",
            *COMMON_WRITES,
            "assign_ticket",
            "set_ticket_status",
        ],
    ),
    "Inventory": Agent(
        name="Inventory",
        role="Checks stock, spots shortfalls, and works out who can restock in time.",
        prompt_file="inventory.md",
        tools=[
            *COMMON_READS,
            "get_inventory",
            "get_pricing",
            "list_vendors",
            "list_invoices",
            "list_purchase_orders",
            "create_purchase_order",
            *COMMON_WRITES,
        ],
    ),
    "Accounting": Agent(
        name="Accounting",
        role="Watches cash and invoices, checks margins, prepares payments for approval.",
        prompt_file="accounting.md",
        tools=[
            *COMMON_READS,
            "get_cash_accounts",
            "list_invoices",
            "list_payments",
            "get_pricing",
            "list_vendors",
            "list_approvals",
            "request_approval",
            "make_payment",
            *COMMON_WRITES,
        ],
    ),
    "Facilities": Agent(
        name="Facilities",
        role="Handles the building side — leases, rent, the landlord.",
        prompt_file="facilities.md",
        tools=[
            *COMMON_READS,
            "list_leases",
            "get_cash_accounts",
            "list_payments",
            "list_approvals",
            "request_approval",
            *COMMON_WRITES,
        ],
    ),
    "Customer Service": Agent(
        name="Customer Service",
        role="Drafts messages to customers. Never sends them.",
        prompt_file="customer_service.md",
        tools=[
            *COMMON_READS,
            "get_inventory",
            "get_pricing",
            "list_purchase_orders",
            "list_drafts",
            "save_draft_message",
            *COMMON_WRITES,
        ],
    ),
}

BOSS = "Boss"


def roster() -> str:
    """The team, as a list an agent can read when deciding who to delegate to."""
    return "\n".join(f"- **{a.name}** — {a.role}" for a in AGENTS.values())
