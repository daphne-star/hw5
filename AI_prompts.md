# HW5 prompts

## before starting

I'm building a multi-agent app for a class assignment. Before we write code, create an `AGENTS.md` at the project root with the rules you must follow for the whole project:
- All AI calls go through Portkey using the OpenAI client, model `gpt-5.6-luna`, and no other model — not for summarizing, not for embeddings, not for a "cheap" helper call.
- The API key comes from `PORTKEY_API_KEY` in the root `.env`, read at runtime. Never hard-code it, never log it, never ship it to the browser.
- Never send any real emails, never call a vendor, never hit any external API other than Portkey.Anything "sent" is a draft written to the database.
- Money moves only with a recorded human approval.

Then scaffold the repo: `mcp_server/`, `backend/` (FastAPI), `frontend/` (React + Vite + TS), `data/`, `scripts/`. Add a `.gitignore` that excludes `.env`, `node_modules/`, `__pycache__/`, and `data/campus_customs_new.db`. Don't write any feature code yet — scaffold only.

---

## Problem 2

In `data/` there's `campus_customs.db`. Don't modify it — it's my reset point. Write `scripts/reset_db.py` that copies it over `data/campus_customs_new.db`, and run it.

Then, against the **copy**, dump the full schema and a few sample rows per table (desk, tickets, inventory, pricing, vendors, leases, cash_accounts, payments, invoices) and write `output/harness.md` describing: what each table means, the primary and foreign keys, what `desk.date_today` is set to, what an open ticket looks like, how I'd tell whether something is overdue (compare it against 'desk.date_today'), where vendor lead times live, and how cash balances are represented. Write a short line for each field.

Read-only analysis plus those two files — no app code yet.

---

## Problem 3

Build an MCP server in `mcp_server/` with **read-only** tools over `data/campus_customs_new.db`. There should be tools that list and get tickets, get today's date from `desk`, look up inventory by SKU and size, get pricing, list vendors with lead times, list leases, get cash account balances, list invoices and payments with their status.

Rules: parameterized SQL only, every tool returns structured JSON with a clear error shape, and every tool has a docstring an LLM can actually read. No agent code yet. Give me a small script that calls each tool once so I can see real output.

---

## Problem 5

Now add the **write** tools to the MCP server. These must enforce the shop's rules in code, not in a prompt, because an agent will try to talk its way around them:
- `make_payment` refuses if the balance would go negative, and refuses unless it's given an approval record for that exact amount. Record the payment and update the invoice and the cash balance in one transaction.
- `create_purchase_order` refuses for any vendor that has an open unpaid invoice.
- Cash only ever goes **out** — no tool may increase a cash balance.
- `request_approval` creates a pending approval. `record_approval_decision` marks it approved or rejected with who decided and when.
- `save_draft_message` stores a customer draft on the board. There is no send tool.
- Ticket tools to update status, assignee, and append a note.

Write pytest tests proving each refusal actually fires: overdraft, unpaid-vendor, payment without approval, attempted cash increase. Run them and show me the results.

Wire up a single agent — Inventory — in `backend/`. It talks to the MCP server for tools and to `gpt-5.6-luna` through Portkey, key from `PORTKEY_API_KEY`. Put its system prompt in `backend/prompts/inventory.md`: check stock by SKU and size, spot shortfalls against open tickets, work out which vendor can restock in time using lead times and `desk.date_today`.

Add a FastAPI route that takes one ticket ID, runs this agent, and returns its answer plus a trace of every tool call with arguments and results. Cap the loop at a small number of turns so a confused run can't burn tokens.

Add the rest of the agents, each with its own prompt file: Boss (triages each ticket, routes it, makes the final call), Accounting (cash, invoices, margins; prepares payments and POs for approval), Facilities (leases, rent, the building side), Customer Service (drafts customer messages).

Full connectivity: any agent can delegate to any other agent, not only back to the Boss. Implement delegation as a tool every agent has, with a depth limit and a cycle guard so A→B→A can't spin. Every delegation and every tool call goes into one shared trace per ticket, with timestamps and which agent did it. The Boss decides when a ticket is resolved and writes the resolution back to the ticket.

---

## Problem 6

Make a html (output/desk_tickets.html) file with five tabs:
- ticket 101
- ticket 102
- ticket 103
- cash
- reflection

the ticket tabs should each be divided into 'expected' and 'actual'. write in the following (do not paraphrase) for the expected sections:

ticket 101: Boss will call inventory, Inventory will check the stock for the product, inventory will call customer service, customer service agent will reply to the email with the corresponding stock info.
ticket 102: Boss will call facilities, facilities will check the lease terms and agreements, facilities will call accounting, accounting will prepare a rent payment pending human approval.
ticket 103: Boss will call inventory, inventory will check stock information, inventory will call accounting, accounting will check margins, accounting will call boss to make decision on whether discount goes through. Boss will call customer service to draft the decision message to the customer.

keep the other tabs and the actual sections empty for now.

---

## Problem 7

Add backend routes with FastAPI that do the following: return ticket info with status, start a run on a ticket when given a ticket id, return recent agent events, approve a payment or purchase after a human clicks approval, get the current cash balances, and reset the database to the original. Stream or poll run progress so the UI can show the agents working live. CORS for the Vite dev server. The API key never leaves the backend.

in output/harness, list each route with one line (what URL/what it does).

---

## Problem 8

Build a React + TypeScript + Vite dashboard. Pastel visual style: soft pastel colors, readable contrast, gentle borders, restrained rounded corners — calm, not candy.

at the top, have a ticket board that lists all tickets, grouped by status. the ticket should be marked resolved when a run finishes. The board should have a column with a button/toggle that allows you to pick a ticket and start an agent run on it

below this ticket board, build an interface with the five agents (with designs as 2d american girl doll cartoons) that shows what each is saying or doing during a run. in the end, have a styled pop-up window that shows what each agent did on the ticket. Show live progress while a run is going. No secrets in frontend code.

at the bottom, have these in a row: 
- an approvals queue with approve/reject buttons that show what's being paid, from which account, and the balance after. there should be a very demure very mindful kaching cash machine sound every time I make an approval.
- a cash summary
- a reset button behind a confirm step. 

write output/design.md to describe the design choices I made.

---

## Problem 11

Write a README: how to copy the original database for a clean run, how to start the architecture (MCP server / FastAPI backend / React board), setup and run instructions for Windows PowerShell, the env var needed, and how to reset the database.