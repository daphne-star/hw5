# Campus Customs — Multi-Agent Operations

Five AI agents run the back office of a campus apparel shop. Open tickets land on a board —
customer orders, rent, unpaid bills, discount requests — and the team works them: checking stock,
chasing vendors, preparing payments, drafting replies. A human watches from a dashboard and
approves anything that moves money.

Three pieces talk to each other:

| | | |
|---|---|---|
| **MCP server** | `mcp_server/` | 21 tools over the shop database. The agents' only way in, and where the shop's rules are enforced. |
| **FastAPI backend** | `backend/` | The five agents, the delegation between them, and the API the browser calls. Holds the API key. |
| **React dashboard** | `frontend/` | The board, the agents working live, the approvals queue. Holds nothing secret. |

---

## What you need first

- **Python 3.11+** (developed on 3.13)
- **Node 18+** (developed on 24)
- **A Portkey API key**

### The environment variable

One variable, `PORTKEY_API_KEY`. Create a `.env` at the project root — the same folder as this
README:

```powershell
Copy-Item .env.example .env
notepad .env
```

```
PORTKEY_API_KEY=your-key-here
```

It is read at runtime and never hard-coded, logged, written into a trace, or sent to the browser.
`.env` is in `.gitignore`; keep it that way. The frontend talks only to the backend, and the
backend is the only thing that ever sees the key.

> The loader checks this folder first, then walks up the directory tree. That means a `.env` in a
> parent folder also works on your machine — but it will not exist for anyone else who clones the
> repo, so put the real one here.

### The database

`data/campus_customs.db` comes from the assignment's `data.zip`. Unzip it so the file sits at:

```
data/campus_customs.db
```

**That file is never written to.** It is the reset point.

---

## Setup

From the project root, in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

If PowerShell refuses to run the activate script, allow signed local scripts for your user once:

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

Then the dashboard's dependencies:

```powershell
npm install --prefix frontend
```

> **npm 11 gotcha.** npm now blocks package install scripts by default, and Vite's bundler needs
> its one. If `npm run dev` later complains that esbuild is missing its binary, run:
> ```powershell
> npm install-scripts approve esbuild --prefix frontend
> npm rebuild esbuild --prefix frontend
> ```

---

## Copy the original database for a clean run

Before any run you want to grade or demo, make a fresh working copy:

```powershell
python scripts/reset_db.py
```

This copies `data/campus_customs.db` over `data/campus_customs_new.db` and reapplies the tables
and guards the original does not ship with — `approvals`, `purchase_orders`, `drafts`, the
`tickets.assignee` column, and four SQLite triggers that stop cash rising, stop it going negative,
stop new cash accounts, and stop payment records being edited. Copying the file wipes all of that,
so the script puts it back every time.

Everything the app does happens against the **copy**. The original stays byte-for-byte untouched,
which is what makes a clean run repeatable.

`output/audit_trail.json` is **not** touched by a reset. Resetting rewinds the shop; it does not
rewind the record of what was done to it.

---

## Start the architecture

Two terminals, both at the project root.

**Terminal 1 — backend.** This also starts the MCP server: the backend launches
`python -m mcp_server.server` as a subprocess over stdio for each ticket run, so there is nothing
separate to start.

```powershell
.\.venv\Scripts\Activate.ps1
uvicorn backend.main:app --reload --port 8000
```

Check it came up — this reports whether a key was found, as a boolean, never the key:

```powershell
curl.exe http://localhost:8000/api/health
```

Interactive API docs: <http://localhost:8000/docs>

**Terminal 2 — dashboard.**

```powershell
npm run dev --prefix frontend
```

Open <http://localhost:5173>.

CORS on the backend allows `localhost:5173` and `localhost:4173` only, so the dashboard must be
reached on those ports.

### Running the MCP server on its own

Only needed if you want to point another MCP client at it:

```powershell
python -m mcp_server.server
```

---

## Using it

1. **Pick a ticket** on the board and press **start run**. The Boss triages it, delegates, and
   makes the final call.
2. **Watch the team.** The five agents show what each is doing as it happens, streamed from the
   backend. When the run ends, a recap opens showing what each agent contributed.
3. **Decide the money.** Anything that would spend cash lands in the approvals queue with the
   amount, the account, and the balance after. Approving it pays it; rejecting it does not.
4. **Reset when you want to start over** — the panel at the bottom right, behind a confirm step.

A ticket often comes back **blocked** rather than resolved. That is the correct outcome when an
approval is still pending or a vendor is owed money, and the Boss is instructed not to claim
otherwise.

### From the command line instead

```powershell
python scripts/run_ticket.py 101                      # full team, Boss first
python scripts/run_ticket.py 101 --agent Inventory    # one agent
python scripts/run_ticket.py 101 --out output/run.json
```

---

## Reset the database

Three ways, all equivalent:

| How | What |
|---|---|
| `python scripts/reset_db.py` | From the terminal. |
| The **Reset** panel in the dashboard | Behind a confirm step that says what will be lost. |
| `POST /api/reset` with `{"confirm": true}` | Refused while a run is in flight. |

All three restore `data/campus_customs_new.db` from the original and leave the audit trail alone.

---

## Tests

```powershell
python -m pytest
```

41 tests. Most of them prove a refusal actually fires — overdraft, unpaid vendor, payment without
approval, an attempted cash increase — and that nothing changes when one does. Each gets its own
throwaway copy of the database; your working copy is untouched.

Other checks worth knowing about:

```powershell
python scripts/smoke_read_tools.py      # call every read tool once, see real output
python scripts/smoke_mcp_server.py      # list the MCP surface, prove reads are read-only
python scripts/summarize_tickets.py     # write output/resolved_tickets.json
```

---

## Layout

```
mcp_server/   MCP server: 21 tools, the shop's rules, the cash triggers
backend/      FastAPI app, the five agents, prompts, the audit trail
frontend/     React + TypeScript + Vite dashboard
data/         campus_customs.db (original) · campus_customs_new.db (working copy)
scripts/      reset, smoke tests, CLI run, summaries
tests/        pytest
output/       the data harness, design notes, audit trail, run evidence
```

Worth reading: `output/harness.md` (the database, the agents, the tools, the API, the dashboard,
and the safety questions a real business would face) and `output/design.md` (why the dashboard
looks the way it does).

---

## The rules, and where they live

Enforced in tool code and in the database, not in a prompt — an agent can be talked into *wanting*
something, but not into the database allowing it.

- A payment needs a recorded human approval for that exact amount, kind, and account. Approvals
  are single-use.
- A payment that would overdraw an account is refused outright.
- Cash only ever goes out. No tool raises a balance, and a trigger refuses it even if one tried.
- A vendor with an open unpaid invoice will not ship — checked *before* the approval, so being
  approved buys no way past it.
- The shop never emails or calls anyone. Customer messages are saved as drafts; there is no send
  tool.
- `desk.date_today` is "today". The real calendar is never used to decide what is overdue.

Every AI call goes through Portkey using `gpt-5.6-luna`, and no other model anywhere — there is a
test that greps the project to make sure.
