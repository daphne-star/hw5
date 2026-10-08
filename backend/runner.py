"""The agent loop and the delegation between agents.

One `TicketRun` per ticket. It owns the MCP session, the shared trace, and the budgets
every agent draws from, so a run cannot quietly cost more by splitting work across more
agents.

Three guards sit on delegation:

* **depth** — how long a chain may get, Boss to Inventory to Accounting being three.
* **cycles** — an agent already in the current chain cannot be delegated to again, so
  A to B to A stops at the second A instead of ringing back and forth.
* **budget** — total delegations and total tool calls for the whole run, shared.

When a guard fires it is recorded in the trace as a `limit` entry and handed back to the
calling agent as a refusal, so the agent gets a chance to finish with what it has rather
than being cut off mid-thought.
"""

from __future__ import annotations

import json
from typing import Any

from . import audit, config
from .agents import AGENTS, BOSS, roster
from .mcp_bridge import McpBridge, open_bridge
from .trace import RunTrace

DELEGATE_TOOL = "delegate_to"


def _delegate_schema(current_agent: str) -> dict[str, Any]:
    """The delegation tool, offered to every agent. This is what makes the team connected."""
    others = [name for name in AGENTS if name != current_agent]
    return {
        "type": "function",
        "function": {
            "name": DELEGATE_TOOL,
            "description": (
                "Hand a task to another agent and get their answer back. Use this when "
                "something needs a tool you do not have, or judgment that belongs to "
                "someone else. You may delegate to anyone on the team, not only the "
                "Boss.\n\nThe team:\n" + roster() + "\n\nAsk for one specific thing and "
                "include the ticket id. You will get their reply as the result."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "agent": {
                        "type": "string",
                        "enum": others,
                        "description": "Which agent to hand this to.",
                    },
                    "task": {
                        "type": "string",
                        "description": (
                            "What you need from them, in one or two sentences, with "
                            "the ticket id and any numbers they will need."
                        ),
                    },
                },
                "required": ["agent", "task"],
            },
        },
    }


class TicketRun:
    """One ticket, one MCP session, one trace, one set of budgets."""

    def __init__(
        self, bridge: McpBridge, ticket_id: int, trace: RunTrace | None = None
    ) -> None:
        self.bridge = bridge
        self.ticket_id = ticket_id
        # An externally supplied trace lets the run registry watch entries arrive while
        # the run is still going, instead of only seeing them once it finishes.
        self.trace = trace if trace is not None else RunTrace(ticket_id=ticket_id)

    # --- budgets ----------------------------------------------------------

    def _tool_budget_left(self) -> bool:
        return self.trace.tool_calls_used < config.MAX_TOOL_CALLS

    def _delegation_budget_left(self) -> bool:
        return self.trace.delegations_used < config.MAX_DELEGATIONS

    # --- the loop ---------------------------------------------------------

    async def invoke(
        self, agent_name: str, task: str, depth: int = 1, chain: tuple[str, ...] = ()
    ) -> str:
        """Run one agent until it answers, and return what it said.

        `chain` is the delegation path that led here — used for the cycle guard, and
        included in the prompt so the agent knows who asked.
        """
        agent = AGENTS[agent_name]
        chain = (*chain, agent_name)

        tools = self.bridge.schemas_for(agent.tools) + [_delegate_schema(agent_name)]
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": agent.system_prompt()},
            {"role": "user", "content": self._framing(task, chain)},
        ]

        client = config.get_client()

        for turn in range(config.MAX_TURNS):
            try:
                response = client.chat.completions.create(
                    model=config.MODEL,
                    messages=messages,
                    tools=tools,
                    tool_choice="auto",
                )
            except Exception as exc:  # the gateway, the network, a bad request
                detail = f"{type(exc).__name__}: {exc}"
                self.trace.error(agent_name, depth, detail)
                return f"[{agent_name} could not reach the model: {detail}]"

            choice = response.choices[0].message
            calls = choice.tool_calls or []

            if choice.content:
                self.trace.message(agent_name, depth, choice.content)

            if not calls:
                return choice.content or f"[{agent_name} returned nothing]"

            messages.append(
                {
                    "role": "assistant",
                    "content": choice.content,
                    "tool_calls": [
                        {
                            "id": c.id,
                            "type": "function",
                            "function": {
                                "name": c.function.name,
                                "arguments": c.function.arguments,
                            },
                        }
                        for c in calls
                    ],
                }
            )

            for call in calls:
                result = await self._dispatch(call, agent_name, depth, chain)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": json.dumps(result, default=str),
                    }
                )

        # Out of turns. Ask once for whatever it has rather than returning nothing.
        self.trace.limit(
            agent_name, depth, "max_turns",
            f"{agent_name} used all {config.MAX_TURNS} turns without a final answer.",
        )
        messages.append(
            {
                "role": "user",
                "content": (
                    "You are out of turns. Give your answer now from what you already "
                    "know, and say plainly what you could not finish."
                ),
            }
        )
        try:
            final = config.get_client().chat.completions.create(
                model=config.MODEL, messages=messages
            )
            text = final.choices[0].message.content or f"[{agent_name} ran out of turns]"
        except Exception as exc:
            text = f"[{agent_name} ran out of turns and could not summarize: {exc}]"

        self.trace.message(agent_name, depth, text)
        return text

    def _framing(self, task: str, chain: tuple[str, ...]) -> str:
        """The user-turn message an agent starts from."""
        lines = [f"Ticket {self.ticket_id}.", "", task]
        if len(chain) > 1:
            lines += ["", f"You were asked by {chain[-2]}. Chain so far: {' -> '.join(chain)}."]
        return "\n".join(lines)

    # --- tool dispatch ----------------------------------------------------

    async def _dispatch(
        self, call: Any, agent_name: str, depth: int, chain: tuple[str, ...]
    ) -> dict[str, Any]:
        """Run one tool call — a delegation, or a real MCP tool."""
        name = call.function.name
        try:
            arguments = json.loads(call.function.arguments or "{}")
        except json.JSONDecodeError as exc:
            result = {
                "ok": False,
                "error": {"code": "invalid_input", "message": f"Unreadable arguments: {exc}"},
            }
            self.trace.tool_call(agent_name, depth, name, {}, result)
            return result

        if name == DELEGATE_TOOL:
            return await self._delegate(arguments, agent_name, depth, chain)

        if not self._tool_budget_left():
            detail = f"Run hit {config.MAX_TOOL_CALLS} tool calls. No more are available."
            self.trace.limit(agent_name, depth, "max_tool_calls", detail)
            return {"ok": False, "error": {"code": "limit_reached", "message": detail}}

        self.trace.tool_calls_used += 1
        result = await self.bridge.call(name, arguments)
        self.trace.tool_call(agent_name, depth, name, arguments, result)
        return result

    async def _delegate(
        self, arguments: dict[str, Any], from_agent: str, depth: int, chain: tuple[str, ...]
    ) -> dict[str, Any]:
        """Hand work to another agent, subject to the three guards."""
        to_agent = arguments.get("agent")
        task = arguments.get("task", "")

        if to_agent not in AGENTS:
            detail = f"There is no agent called {to_agent!r}. The team is: {', '.join(AGENTS)}."
            return {"ok": False, "error": {"code": "not_found", "message": detail}}

        if to_agent in chain:
            # The cycle guard. Without it, two agents each convinced the other owns the
            # problem will pass it back and forth until a budget runs out.
            detail = (
                f"{to_agent} is already working on this ticket further up the chain "
                f"({' -> '.join(chain)}), so handing it back would loop. Answer with what "
                "you have, or say what you need from them and let them pick it up."
            )
            self.trace.limit(from_agent, depth, "cycle", detail)
            return {"ok": False, "error": {"code": "cycle_blocked", "message": detail}}

        if depth >= config.MAX_DEPTH:
            detail = (
                f"Delegation depth {config.MAX_DEPTH} reached ({' -> '.join(chain)}). "
                "Answer with what you have and say what is still open."
            )
            self.trace.limit(from_agent, depth, "max_depth", detail)
            return {"ok": False, "error": {"code": "limit_reached", "message": detail}}

        if not self._delegation_budget_left():
            detail = f"Run hit {config.MAX_DELEGATIONS} delegations. Finish with what you have."
            self.trace.limit(from_agent, depth, "max_delegations", detail)
            return {"ok": False, "error": {"code": "limit_reached", "message": detail}}

        self.trace.delegation(from_agent, to_agent, depth, task)
        answer = await self.invoke(to_agent, task, depth=depth + 1, chain=chain)
        return {"ok": True, "data": {"agent": to_agent, "answer": answer}}


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


async def run_single_agent(
    ticket_id: int,
    agent_name: str,
    task: str | None = None,
    trace: RunTrace | None = None,
) -> dict:
    """Run one agent on one ticket, with no delegation expected. Used to test an agent alone."""
    if agent_name not in AGENTS:
        raise KeyError(agent_name)

    before = audit.snapshot()
    async with open_bridge() as bridge:
        run = TicketRun(bridge, ticket_id, trace)
        answer = await run.invoke(
            agent_name,
            task or f"Look at ticket {ticket_id} and report what you find.",
        )
    after = audit.snapshot()

    return _finish(ticket_id, agent_name, "single_agent", answer, run, before, after)


async def run_ticket(ticket_id: int, trace: RunTrace | None = None) -> dict:
    """Put a ticket in front of the Boss and let the team resolve it."""
    before = audit.snapshot()
    async with open_bridge() as bridge:
        run = TicketRun(bridge, ticket_id, trace)
        answer = await run.invoke(
            BOSS,
            (
                f"Ticket {ticket_id} is on the board. Read it, decide who should handle "
                "it, delegate, and make the final call. When you are done, set the "
                "ticket's status with a note explaining what was decided and why."
            ),
        )
    after = audit.snapshot()

    return _finish(ticket_id, BOSS, "full_team", answer, run, before, after)


def _finish(
    ticket_id: int,
    entry_agent: str,
    mode: str,
    answer: str,
    run: TicketRun,
    before: dict,
    after: dict,
) -> dict:
    """Append the run to the audit trail and shape the response.

    Every run is recorded, including ones that failed or were cut short by a limit —
    a log that only keeps the tidy runs is not an audit trail.
    """
    trace = run.trace.to_dict()
    entry = audit.append_run(
        ticket_id=ticket_id,
        entry_agent=entry_agent,
        mode=mode,
        answer=answer,
        trace=trace,
        before=before,
        after=after,
        model=config.MODEL,
    )

    return {
        "ticket_id": ticket_id,
        "agent": entry_agent,
        "answer": answer,
        "run_id": entry["run_id"],
        "changes": entry["changes"],
        "trace": trace,
    }
