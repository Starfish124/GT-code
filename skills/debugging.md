---
name: debugging
triggers: debug, bug, error, fix, broken, crash, traceback, exception, not working, fails, stacktrace
priority: 3
---
# Debugging playbook — find the cause, don't guess at fixes

## The loop
1. **Reproduce** — run the failing thing (run_command) and capture the
   ACTUAL error. Never debug from a description when you can get the real
   output.
2. **Read the error properly** — the last line names the exception; the
   FIRST frame inside the user's own code (not the library) is usually
   where to look. Line numbers are gold.
3. **Locate** — read_file around that line; search_files for the failing
   symbol if the origin isn't obvious.
4. **Hypothesize ONE cause**, check it cheaply (read the code, add a
   targeted print, run a 3-line reproduction) before changing anything.
5. **Fix the root cause**, not the symptom — wrapping it in try/except is
   almost never the fix.
6. **Re-run the exact original command** to confirm, and watch for the
   error simply moving one line down.

## Classic culprits (check before deep-diving)
- Wrong directory / relative path (print cwd, use absolute paths)
- Stale state: old venv, cached .pyc, server not restarted after edit
- Off-by-one, None where a value was assumed, mutated shared default
- Encoding/newlines on files that crossed Windows↔Unix
- Version mismatch: the installed library ≠ the docs being followed

## Discipline
- Change ONE thing per iteration; re-run each time.
- If two hypotheses fail, STOP and re-read the error and the data — the
  wrong assumption is usually upstream of where you're looking.
- Keep the user informed: state the cause you found in one sentence, then
  the fix — not a narration of every attempt.
