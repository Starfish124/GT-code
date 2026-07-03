---
name: code-quality
triggers: code, function, class, refactor, clean, review, python, script, module, typescript, library
priority: 2
---
# Code-quality playbook — what "good" looks like in any file you write

## Universal rules
- Complete, runnable code only — no TODOs, no "implement later", no
  placeholder bodies. If something can't be done, say so in prose.
- Names carry meaning: `retry_count` not `n`, `parse_invoice` not `do_it`.
  Functions = verbs, values = nouns, booleans read as questions
  (`is_valid`, `has_access`).
- Small units: a function does one thing; if it needs "and" to describe,
  split it. Prefer ≤40 lines per function, guard clauses over deep nesting.
- Fail loudly at the boundary: validate inputs early, raise/return real
  errors with actionable messages — never swallow exceptions silently.
- Comments explain WHY (constraints, gotchas), never narrate WHAT the
  next line does. No commented-out code left behind.
- Match the existing project's style, naming and structure when editing —
  consistency beats personal preference.

## Python specifics
- Type hints on public functions; dataclasses for records; pathlib over
  os.path; f-strings; context managers for files/connections.
- except SpecificError, never bare except; `if __name__ == "__main__":`
  for scripts; requirements/deps declared, not assumed.

## Workflow discipline
- Read a file before editing it. Make surgical edits (edit_file) rather
  than rewriting whole files — smaller diffs, fewer regressions.
- After creating or changing code, RUN it (run_command) or run the tests;
  fix what fails; only then report done, honestly describing what you
  verified.
