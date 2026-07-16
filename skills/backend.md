---
name: backend
triggers: backend, api, rest, server, endpoint, database, fastapi, flask, express, node, sql, auth, crud
priority: 4
---
# Backend playbook

NEVER say an API is running, live, hosted, working or ready unless a request
you made THIS TURN came back with a real response. No response = say plainly
that you could not verify it. Never describe an endpoint you never called.

## Start it, then prove it
- Pick the port yourself (8000). Never ask the user.
- Start the server with run_command "background": true, then check_process for
  "listening on". Foreground = guaranteed timeout.
- Then curl it, every time:
  run_command: curl -s http://localhost:8000/health
  Only once you SEE the response body may you say it works. Curl one real
  endpoint too. stop_process when done.

## Python — zero installs
fastapi, uvicorn and flask are NOT installed. Do NOT pip install them, do NOT
run brew, do NOT run `python3 -m venv` (its pip is missing here) — that burns
the whole turn. Never call `python3`.
write_file a real .py file using the stdlib http.server + json, then:
run_command: "/Users/sarveshsingh/GT-code/.venv/bin/python api.py"
Persist to sqlite3 or a JSON file (stdlib). Never a cloud DB — Firebase and
Atlas need accounts you lack. Node: Express is fine, npm works.

## Shape
- routes -> service logic -> data access.
- Validate every input at the edge; reject bad types/ranges with 400.
- One error shape: {"error": {"code": "...", "message": "..."}}
- Parameterized SQL only — never string-built. bcrypt for passwords.
- Add GET /health. Never log secrets or personal data.
