---
name: code-quality
triggers: review, refactor, clean up, code review, code quality, readable, maintainable, naming, lint, type hints, docstring
priority: 2
---
# Code-quality playbook

## Do these first — not optional
- NEVER describe, quote, cite a line of, or report a finding about code you
  have not read THIS turn. Reviewing is not editing: read_file it first, or
  say plainly you have not read it. An invented finding is worse than none.
- Report only what you ran and saw: "I ran X, it printed Y." Never "tests
  pass" or "ready to send" unless you watched it happen.
- Run Python with GT's own interpreter (`sys.executable`). A bare `python3`
  on PATH is a DIFFERENT, broken interpreter without GT's packages. Never
  write `python3 ...` or `pip install ...`.
- pytest / ruff / black / mypy DO NOT EXIST here — do NOT install them. To
  verify, run the code itself (or the project's own test command), and say
  that is what you verified.
- No `-c` one-liners. Write a real .py file, then run it.

## What good code looks like
- Names carry meaning: `retry_count` not `n`, `parse_invoice` not `do_it`.
  Functions = verbs, booleans ask (`is_valid`).
- One thing per function; if describing it needs "and", split it. Prefer
  <=40 lines; guard clauses, not deep nesting.
- Validate inputs early; raise real errors with actionable messages.
- Comments explain WHY, never narrate WHAT. No commented-out code.
- Match the existing file's style — consistency beats taste.


## Python
- Type hints on public functions; dataclasses; pathlib; f-strings; context
  managers for files. `except SpecificError`, never bare `except`.
