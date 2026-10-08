# Project Instructions — Campus Customs Multi-Agent Operations (HW5)

These rules apply to every file in this project and to every change made to it. They are not
suggestions to an agent at runtime; where a rule can be enforced in code, enforce it in code.

## Model and AI calls

- Every AI call goes through **Portkey**, using the OpenAI client pointed at the Portkey gateway.
- The only model permitted anywhere in this project is **`gpt-5.6-luna`**.
- No other model, for any purpose: not for summarizing, not for embeddings, not for a "cheap"
  helper or classifier call, not in a test fixture, not in a comment showing an alternative.
- There must be exactly one place in the codebase where the model name is written. Everything
  else imports it from there.

## Secrets

- The API key is `PORTKEY_API_KEY`, read from the environment at runtime.
- It is loaded from the `.env` at the project root. `.env` is never committed.
- Never hard-code the key, never log it, never include it in an error message or a trace, never
  expose it to client-side code. The browser never sees it; the frontend talks only to our own
  backend, and the backend holds the key.

## No contact with the outside world

- The only external network call this project may make is to Portkey.
- Never send email. Never call, message, or otherwise contact a vendor. Never hit a third-party
  API, webhook, or SMTP server.
- Anything a human would "send" is a **draft written to the database** and shown on the board.
  There is no send tool, and no tool may be given one.

## Money

- Money moves only with a recorded human approval. A payment tool must refuse to execute without
  an approval record that matches the amount being paid.
- Cash only ever leaves the shop in this project. No tool may increase a cash balance.
- A payment that would take an account negative must be refused, not clamped, not deferred.
- Approvals are recorded with who decided, what they decided, and when.

## Data

- `data/campus_customs.db` is the pristine original and is **never written to**. It is the reset
  point.
- All application reads and writes go to `data/campus_customs_new.db`, a copy of the original.
  That copy is disposable and is not committed.
- `desk.date_today` is "today" for the shop. Never use the real system clock to decide what is
  due or overdue.

## Web app styling

- Pastel visual style: soft pastel colors, readable contrast, gentle borders, restrained rounded
  corners.

## Layout

```
mcp_server/   MCP server exposing database tools to the agents
backend/      FastAPI app and the agent team
frontend/     React + Vite + TypeScript dashboard
data/         campus_customs.db (original) and campus_customs_new.db (working copy)
scripts/      setup and reset utilities
docs/         notes written during the build
output/       evidence captured from full runs
```
