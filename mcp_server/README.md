# Campus Customs MCP server

The agents' only way into the shop's data. Rather than letting each agent open the database
and write its own SQL, every lookup goes through a named tool defined here — so there is one
place where queries live, one response shape to read, and one place to enforce what the shop
allows. Twenty-one tools: thirteen that read the shop, eight that change it.

## Database

Reads `data/campus_customs_new.db`, the working copy. The pristine `data/campus_customs.db` is
the reset point and is never opened by this server — recreate the copy any time with
`python scripts/reset_db.py`.

Read tools open a SQLite `mode=ro` URI, so a write attempted from one fails at the driver rather
than reaching the file. Write tools open a separate read-write connection and do all their work
inside one `BEGIN IMMEDIATE` transaction with foreign keys on, so a payment's three edits land
together or not at all. All SQL is parameterized.

The shop's today comes from `desk.date_today` (`2026-08-31` in the seed data), never the system
clock. Anything date-dependent is computed server-side against it.

## Read tools

| Tool | Arguments | Returns |
|---|---|---|
| `get_today` | — | The shop's date and the front-desk note |
| `list_tickets` | `status`, `ticket_type` | Tickets on the board, newest first |
| `get_ticket` | `ticket_id` | One ticket, with its lease and invoice resolved inline |
| `get_inventory` | `sku`, `size` | Stock on hand per `(sku, size)` |
| `get_pricing` | `sku` | Unit cost, list price, `margin_per_unit`, `margin_rate` |
| `list_vendors` | `specialty_contains` | Vendors, `lead_days`, and `earliest_arrival` |
| `list_leases` | — | Leases, rent, `days_until_due`, `is_overdue` |
| `get_cash_accounts` | `name` | Balances, keyed by account name |
| `list_invoices` | `status`, `vendor_id` | Bills owed, with `is_overdue` and `days_overdue` |
| `list_payments` | `kind` | Money already paid out, and who approved it |
| `list_approvals` | `status` | Approval requests and what humans decided |
| `list_purchase_orders` | `status` | Restock orders and when each is expected |
| `list_drafts` | `ticket_id` | Customer messages on the board. None sent |

## Write tools

| Tool | Refuses when |
|---|---|
| `request_approval` | Amount is not positive, or no reason is given |
| `record_approval_decision` | The approval was already decided — decisions are final |
| `make_payment` | No approved approval, wrong amount/kind/account, approval already spent, or the balance would go negative |
| `create_purchase_order` | The vendor has an open invoice — checked *before* the approval, so approval buys no way past it |
| `save_draft_message` | The body is empty. Nothing is ever sent |
| `set_ticket_status` | The status is outside `open`/`in_progress`/`blocked`/`resolved`/`rejected` |
| `assign_ticket` | No assignee given |
| `add_ticket_note` | No note given |

## Rules enforced below the tools

`migrations.py` adds the tables the original database lacks (`approvals`,
`purchase_orders`, `drafts`, and `tickets.assignee`) plus four SQLite triggers that hold
regardless of which tool is called:

| Trigger | Refuses |
|---|---|
| `cash_never_increases` | Any update that raises a balance |
| `cash_never_negative` | Any update that takes a balance below zero |
| `cash_no_new_accounts` | Creating a cash account — the way around the first trigger |
| `payments_are_immutable` | Editing a payment record after the fact |

The tool checks exist so a refusal comes back readable. The triggers exist so it comes
back at all. `scripts/reset_db.py` reapplies all of this after every reset, because
copying the original file wipes it.

Read arguments are optional filters unless the tool takes an id. Every tool carries a docstring
written for a model to read; reads are registered with `read_only_hint=True` and writes with
`read_only_hint=False`, so a client can tell the two halves apart.

## Response shape

```jsonc
{"ok": true,  "data": {...} | [...], "meta": {"count": 3, "as_of": "2026-08-31"}}
{"ok": false, "error": {"code": "not_found", "message": "...", "details": {...}}}
```

Failure codes: `not_found`, `invalid_input`, `db_unavailable`, `read_only_violation`,
`internal_error`.

Refusal codes — the shop's rules saying no, which is a correct outcome rather than an error to
retry: `insufficient_funds`, `approval_required`, `vendor_blocked`, `cash_increase_forbidden`,
`conflict`. Each message says what would have to change.

Tools never raise across the MCP boundary — a failure comes back in this shape so an agent can
branch on it.

## Files

- `db.py` — read-only connection, shop-date helpers
- `results.py` — the response envelope and the error-catching decorator
- `migrations.py` — the app's own tables and the cash triggers
- `tools.py` — the thirteen read tools, as plain functions
- `write_tools.py` — the eight write tools
- `server.py` — MCP registration

Both tool modules hold plain functions, so the same code serves the MCP server, the smoke
scripts, and the tests without a transport in the way.

## Audit trail

The MCP server records nothing itself — it answers calls. Every *run* of the agent team is
appended to `output/audit_trail.json` by `backend/audit.py`, including which agents acted, every
tool call and result, every refusal, and a before-and-after diff of the shop. That file is
append-only and is not touched by `scripts/reset_db.py`: resetting rewinds the data, not the
record of what was done to it.

## Running

```bash
python -m mcp_server.server        # serve over stdio
python scripts/smoke_read_tools.py # call every tool once and print the output
python scripts/smoke_mcp_server.py # list the MCP surface, write output/mcp_smoke.json
python -m pytest                   # 21 tests, one per refusal
```

Requires `mcp>=2.0`. In version 2 the server class is `MCPServer` (formerly `FastMCP`) and the
Python models use snake_case — `input_schema`, `read_only_hint`.
