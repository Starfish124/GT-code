---
name: project-setup
triggers: scaffold, boilerplate, from scratch, new project, bootstrap, starter
priority: 3
---
# Project-scaffolding playbook

## Hard rules — read first
- Never fabricate. Every command you put in the README, or claim "works",
  must be one you actually ran this turn and saw exit 0. No invented
  install steps, commands, or features.
- Pick the stack yourself and say it in one line. Never ask_user which
  stack / port / name — only ask on a fork only the user can decide.
- Make the venv with GT's own interpreter — the one running GT
  (`sys.executable`), never a bare `python` / `python3` (that is a
  DIFFERENT, broken interpreter). Then install with `.venv/bin/pip`.

## Definition of "scaffolded" — the goal
The project RUNS: the hello-world path works end-to-end, proven this turn
with run_command (exit 0). The structure has an obvious place for the next
feature. Then build features incrementally, verifying each.

## Always, for any new project
- Create: README.md, .gitignore for the stack, a dependency manifest
  (pyproject.toml / package.json) — never loose scripts past one file.
- Write the README LAST, from the commands you really ran — no line quota.
- Write the .gitignore and let the USER run git; do not commit for them.

## Layouts
Python: pyproject.toml (deps + entry point), src/project/ (or flat for
small tools), tests/test_basic.py, README.md.
Web, no framework: one self-contained index.html (see frontend playbook).
Backend: FastAPI main.py + static/, one command to run.
React, if asked: `npm create vite@latest`, then strip demo cruft.
