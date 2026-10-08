# Design notes — the Campus Customs dashboard

What the dashboard is for, and why it looks and behaves the way it does.

The job is narrow: let a person watch five agents work a ticket, and decide the one thing the
agents are not allowed to decide — whether money leaves the shop. Everything below follows from
that.

---

## Colour

Pastel, but not sweet. Every surface colour sits around 88–94% lightness with low chroma, so
five of them can be on screen simultaneously without any one shouting.

| Token | Hex | Used for |
|---|---|---|
| `--sky` | `#e2ecf5` | Open tickets, run buttons |
| `--lilac` | `#ebe6f4` | In progress, delegation, the running state |
| `--clay` | `#f2e3db` | Blocked, refusals |
| `--mint` | `#e3efe7` | Resolved, cash, approve |
| `--blush` | `#f6e6ea` | Rejected, reset, errors |
| `--butter` | `#f7efdd` | The approvals queue |

Text is `#3c3a40` — a warm near-black rather than pure black. It clears 4.5:1 against every
surface above while reading softer than `#000`. Secondary text is `#6f6b76`, which still clears
4.5:1 on white; `#9b96a2` is used only for labels large enough or incidental enough not to carry
meaning alone.

**Colour is never the only signal.** Every status column is labelled in words. A refusal says
`refused: insufficient funds`, not just a warm tint. A running ticket shows a pulsing dot *and*
the word "running". Someone who cannot distinguish mint from clay loses nothing.

## Shape and weight

Corners are 8px on cards and 6px on controls. Restrained on purpose — the pastels already soften
everything, and rounder corners on top of them tips into a toy.

Borders are 1px in a **tint of the fill**, never grey (`--mint` pairs with `--mint-edge`). This
is what keeps pastel surfaces from reading as washed out: a grey border against a low-chroma fill
makes the fill look like a mistake, while a slightly darker version of the same hue reads as
deliberate.

Shadows are nearly invisible (`0 1px 2px` at 4% opacity) and used only on ticket cards, to lift
them off their column. Nothing else floats.

Numbers use `font-variant-numeric: tabular-nums` so balances line up as they change.

---

## Layout: three bands, top to bottom

**The board** is first because it is where work starts. Five columns, one per status, always
rendered even when empty. A fixed layout means a ticket moving from Open to Blocked is a visible
*move* rather than a card changing colour where it sits — the movement is the information.

**The team** is in the middle, where the eye lands while waiting. Five dolls in a row with what
each is doing underneath.

**The bottom row** is approvals, cash, reset — in that order, left to right, with approvals
widest. Approvals is the only panel that asks the person for something, so it gets the space and
the leftmost position. Reset is last, smallest, and behind a confirm step, because it is the only
irreversible control on the page.

---

## The agents

Each agent is an inline SVG drawn in flat colour with American-girl-doll proportions: oversized
round head, small body, no gradients or shading.

Five near-identical figures would be unreadable, so each carries **one prop naming her job** —
a bell for the Boss, a clipboard for Inventory, a ledger and coins for Accounting, a key ring for
Facilities, a letter for Customer Service. The prop does more work than the outfit colour; it is
what makes the row scannable at a glance.

**Expression follows state**, and state is read off the trace:

| State | How she looks | When |
|---|---|---|
| `idle` | level brows, small smile, dimmed to 50% | has not acted, or is not the current actor |
| `working` | brows lifted, dashed ring turning beside her | her tool call is the most recent entry |
| `speaking` | open mouth, mint bubble | she just reported back |
| `blocked` | brows drawn down, flat mouth, clay bubble | her last entry was a refusal or a limit |
| `done` | eyes closed in a smile | the run has finished |

The ring is the only moving element, and it turns **only while that agent is actually doing
something** — motion means activity rather than decoration. Everything animated is wrapped in
`prefers-reduced-motion: reduce`.

Idle agents fade to 50% rather than disappearing, so the team stays five people and it is obvious
who was *not* involved.

### Tool names become sentences

The trace says `get_cash_accounts`; the doll says *"checking the balance"*. There is a lookup
table from tool name to plain language in `AgentStage.tsx`. The raw names are still visible in
the ticker below and in the recap modal — the translation is for the glance, not a replacement.

---

## Live progress

Server-sent events are the primary channel: the backend pushes each trace entry as it is
produced, and the dolls update as it arrives. **Polling is the fallback**, not the design — if
the stream drops, the hook switches to `GET /api/runs/{id}?since=N`, which returns only entries
after the last sequence number seen. The UI looks identical either way, and the section header
quietly says "polling" when it has fallen back.

Sequence numbers rather than indexes mean a reconnect cannot duplicate or skip an entry.

A seven-row ticker under the dolls shows the most recent steps with raw tool names, for anyone
who wants the detail while it is happening.

---

## The recap modal

Opens on its own when a run finishes.

It is **grouped by agent, not chronological**. The ticker already gave the timeline; what a
timeline cannot answer is "what was each of them responsible for", because three agents interleave
in it. One block per agent: how many tool calls, who she asked for help, which tools she used, and
the last thing she said.

Refusals and limits are **pulled out into their own section**. A refusal is usually the most
important event in a run and the easiest to lose among forty tool calls.

The delegation chain runs across the top as pills (`Boss → Inventory`, `Inventory → Accounting`),
which is where the hand-offs that did *not* go through the Boss become visible.

Escape closes it, focus moves into it on open, and clicking the backdrop dismisses it.

---

## Approvals

Every approval card answers three questions before the person has to ask:

- **what is being paid** — kind, amount, and the agent's stated reason
- **from which account** — named explicitly, not assumed
- **the balance after** — computed and shown next to the balance now

If the result would be negative, the figure turns warm and a line appears saying the shop will
refuse it. The UI does not block the button: the person can still click it, the backend refuses,
and the refusal is reported. Hiding the button would teach that the UI is the rule; letting the
backend refuse shows where the rule actually lives.

The outcome line after a decision says what happened in the shop's terms — *"Done — make
payment"*, or *"Approved, but refused: …"*. An approval that was granted and then refused by the
rules is the case most worth surfacing clearly, and it has its own wording.

`decided_by` is sent with every decision and ends up in the payment record and the audit trail,
so the question "who approved this" has a name in the answer.

### The sound

Synthesised with the Web Audio API — no audio file, nothing to load.

Demure and mindful in practice means: **quiet** (peak gain 0.09), **short** (~420ms), and a soft
two-note bell rather than a register clang. Two sine partials a fifth apart (E6, then B6 seventy
milliseconds behind and softer), fast attack, exponential tail. Closer to a shop doorbell than a
slot machine.

It plays **only on approval, never on rejection**. A rejection is not a small celebration.

---

## Reset

Two steps. The first click swaps the panel for a sentence saying exactly what will be lost —
every payment, approval, order, and draft — and notes that the audit trail is kept. Only then
does a destructive-looking button appear.

The backend refuses to reset while a run is in flight, and says so.

---

## No secrets

The dashboard holds no credentials and has nowhere to put them. The only configuration is a base
URL. The Portkey key lives in the backend process; `/api/health` reports its presence as a
boolean, which is what the header dot shows — *"key loaded on the server"*, never the key.

Verified by pulling the key from `.env` and searching every API response the dashboard reads,
including `/api/events` with full tool results: zero occurrences.

---

## One deliberate departure from the brief

The brief said a ticket should be **marked resolved when a run finishes**. The dashboard instead
refetches when a run ends and shows whatever status the Boss actually wrote — which is often
`blocked`.

That is not an oversight. On ticket 101 the Boss correctly concluded the work could not be
completed: the tee is out of stock, the vendor is owed $840 and will not ship, and two approvals
were still pending. Painting that card green would be the dashboard overriding the one agent whose
job is to make the final call, and it would quietly turn an honest "blocked" into a false
"resolved" — the exact failure mode the safety notes call out as the costly one.

What the board does instead: the card moves to the column the Boss chose, the run button becomes
"run again", and a "see what happened" link appears next to it. Completion is visible without
claiming success that did not happen.
