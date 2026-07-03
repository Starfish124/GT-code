---
name: project-setup
triggers: new project, scaffold, boilerplate, setup project, initialize, init, structure, from scratch, create app
priority: 3
---
# Project-scaffolding playbook — new projects that start clean

## Always, for any new project
- Ask (ask_user) what's unclear FIRST: stack, scope, where it will run.
  Then present the file plan before writing anything.
- Create: README.md (what it is, how to run it, 10 lines minimum),
  .gitignore matched to the stack, and a dependency manifest
  (pyproject.toml / package.json) — never bare loose scripts for anything
  with more than one file.
- git init + an initial commit once the skeleton runs.

## Python project
```
project/
  pyproject.toml        # deps + entry point
  src/project/…         # package code (or flat project/ for small tools)
  tests/test_basic.py   # at least one real test from day one
  README.md
```
venv first: `python -m venv .venv`, install into it, never global pip.

## Web app (no framework asked)
Single self-contained index.html (see frontend playbook). If a backend is
needed: FastAPI main.py + static/ folder, one command to run.

## Node/React (when asked for)
Use the standard generator (npm create vite@latest) rather than hand-
rolling config; then strip demo cruft before adding features.

## Definition of "scaffolded"
The project RUNS: the hello-world path works end-to-end (run_command to
prove it), the README's run instructions are true, and the structure has
an obvious place for the next feature. Then build features incrementally,
verifying each.
