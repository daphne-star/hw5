"""The delegation guards, tested without spending a token.

`_delegate` is called directly with a stub bridge, so the cycle, depth, and budget
checks are exercised on their own. Whether the model behaves is a separate question;
whether these guards fire is not left to it.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from backend import config
from backend.agents import AGENTS
from backend.runner import TicketRun

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class StubBridge:
    """Stands in for the MCP session. Nothing here should reach it."""

    def schemas_for(self, names):
        return []

    async def call(self, name, arguments):  # pragma: no cover - should never run
        raise AssertionError(f"guard test unexpectedly called the tool {name!r}")


def delegate(run: TicketRun, to: str, depth: int, chain: tuple[str, ...]) -> dict:
    return asyncio.run(
        run._delegate({"agent": to, "task": "anything"}, "Boss", depth, chain)
    )


@pytest.fixture
def run() -> TicketRun:
    return TicketRun(StubBridge(), ticket_id=101)


def test_delegating_to_an_agent_already_in_the_chain_is_blocked(run):
    """A -> B -> A would ping-pong until a budget ran out. It stops at the second A."""
    result = delegate(run, "Boss", depth=2, chain=("Boss", "Inventory"))

    assert result["ok"] is False
    assert result["error"]["code"] == "cycle_blocked"
    assert "would loop" in result["error"]["message"]
    assert run.trace.entries[-1]["limit"] == "cycle"


def test_delegation_stops_at_the_depth_limit(run):
    """Three deep is the end of the chain, however reasonable the next hop looks."""
    chain = ("Boss", "Inventory", "Accounting")
    result = delegate(run, "Facilities", depth=config.MAX_DEPTH, chain=chain)

    assert result["ok"] is False
    assert result["error"]["code"] == "limit_reached"
    assert run.trace.entries[-1]["limit"] == "max_depth"


def test_delegation_budget_is_shared_across_the_whole_run(run):
    """Splitting work across more agents does not buy more handoffs."""
    run.trace.delegations_used = config.MAX_DELEGATIONS

    result = delegate(run, "Inventory", depth=1, chain=("Boss",))

    assert result["ok"] is False
    assert run.trace.entries[-1]["limit"] == "max_delegations"


def test_delegating_to_a_nonexistent_agent_is_refused(run):
    """A hallucinated teammate gets a refusal naming the real team, not a crash."""
    result = delegate(run, "Legal", depth=1, chain=("Boss",))

    assert result["ok"] is False
    assert result["error"]["code"] == "not_found"
    assert "Inventory" in result["error"]["message"]


def test_tool_budget_is_shared_and_enforced(run):
    """Once the run's tool budget is gone, no agent gets another call."""
    run.trace.tool_calls_used = config.MAX_TOOL_CALLS

    class Call:
        id = "c1"

        class function:
            name = "get_today"
            arguments = "{}"

    result = asyncio.run(run._dispatch(Call(), "Inventory", 2, ("Boss", "Inventory")))

    assert result["ok"] is False
    assert result["error"]["code"] == "limit_reached"
    assert run.trace.entries[-1]["limit"] == "max_tool_calls"


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


def test_every_agent_can_reach_every_other_agent():
    """Full connectivity: the delegate tool offers the whole team, minus yourself."""
    from backend.runner import _delegate_schema

    for name in AGENTS:
        options = set(_delegate_schema(name)["function"]["parameters"]["properties"]["agent"]["enum"])
        assert options == set(AGENTS) - {name}


def test_payment_tools_are_not_handed_to_agents_that_should_not_have_them():
    """The division of labour is the allowlist, not a sentence in a prompt."""
    assert "make_payment" not in AGENTS["Inventory"].tools
    assert "make_payment" not in AGENTS["Boss"].tools
    assert "make_payment" not in AGENTS["Customer Service"].tools
    assert "make_payment" in AGENTS["Accounting"].tools

    assert "create_purchase_order" in AGENTS["Inventory"].tools
    assert "create_purchase_order" not in AGENTS["Facilities"].tools

    assert "save_draft_message" in AGENTS["Customer Service"].tools
    assert all(
        "save_draft_message" not in a.tools for n, a in AGENTS.items() if n != "Customer Service"
    )


def test_every_agent_prompt_file_exists_and_says_something():
    for agent in AGENTS.values():
        text = agent.system_prompt()
        assert len(text) > 400, f"{agent.name}'s prompt is too thin to be useful"
        assert "delegate_to" in text  # the shared context tells them how to hand off


def test_only_one_model_name_appears_anywhere_in_the_project():
    """A stray model name is an explicit deduction, so check rather than trust."""
    # No capture group: findall must return the whole model name, not just the prefix.
    pattern = re.compile(r"\b(?:gpt|claude|gemini|llama|mistral|grok|deepseek)[\w.\-]*", re.I)
    skip = {".git", "node_modules", "__pycache__", ".pytest_cache", "output", "data"}

    found: dict[str, set[str]] = {}
    for path in PROJECT_ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in {".py", ".md", ".ts", ".tsx", ".json"}:
            continue
        if any(part in skip for part in path.parts):
            continue
        if path.name == Path(__file__).name:
            continue  # this file names other providers in order to look for them
        for match in pattern.findall(path.read_text(encoding="utf-8", errors="ignore")):
            found.setdefault(match.lower(), set()).add(path.name)

    assert set(found) <= {config.MODEL}, f"model names other than {config.MODEL}: {found}"
