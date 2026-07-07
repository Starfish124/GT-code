---
name: backend
triggers: backend, api, rest, server, endpoint, database, fastapi, flask, express, node, sql, auth, crud
priority: 4
---
# Backend playbook — APIs that survive contact with real clients

## Project shape (any language)
- Layers: routes/handlers → services (logic) → data access. Handlers stay
  thin; logic lives where tests can reach it without HTTP.
- Config from environment variables with sane defaults; never hardcode
  secrets, ports, or connection strings. Ship a .env.example.
- Python default: FastAPI + uvicorn (typed, validated, self-documenting).
  Node default: Express + a validation lib.

## REST conventions
- Nouns for resources: GET /invoices, POST /invoices, GET /invoices/{id},
  PATCH to update, DELETE to remove. Verbs only for true actions
  (/invoices/{id}/send).
- Status codes: 200/201/204 success; 400 bad input; 401 unauthenticated;
  403 forbidden; 404 missing; 409 conflict; 500 = a bug on our side.
- ONE error shape everywhere: `{"error": {"code": "...", "message": "..."}}`.
- Paginate every list endpoint (limit/offset), cap limit.

## Boundaries
- Validate ALL input at the edge (pydantic / schema lib) — types, ranges,
  lengths. Reject unknown fields on write endpoints.
- SQL only with parameterized queries — string-built SQL is an instant
  bug and an injection hole.
- Hash passwords with bcrypt/argon2; never log secrets, tokens, or
  full request bodies containing personal data.

## Running & hosting it
- Pick the port yourself (8000 Python / 3000 Node, or the framework
  default) — never ask the user.
- A server never exits: start it with "background": true, wait, then
  check_process for "listening on ...". Foreground = guaranteed timeout.
- Verify with a real request (`curl http://localhost:<port>/health` or an
  actual endpoint) before declaring it hosted. Stop it with stop_process
  when done unless the user wants it left running.
- Default to local + SQLite/JSON for demos; cloud DBs (Firebase, Atlas)
  need accounts/keys — only when the user explicitly has them.

## Reliability
- Timeouts on every outbound call; catch specific exceptions, return the
  standard error shape, log with context (request id, not stack spam).
- Health endpoint (GET /health) that checks the DB.
- Write at least: one happy-path test per endpoint + one validation-
  failure test. Run them before declaring the API done.
