---
name: debugging
triggers: debug, bug, error, broken, crash, traceback, exception, not working, fails, stacktrace
priority: 3
---
# Debugging — find the cause, don't guess

## Hard rules
- NEVER report a fix you didn't verify. Re-run the failing command and read the output before saying it works.
- NEVER install, upgrade, or recreate an environment (no pip, venv, brew) to fix a bug. If a dependency looks wrong, say so and stop.
- To run code, write a small repro `.py` and run it with run_command. Never a `python3 -c` one-liner (chained statements = SyntaxError) or bare `python3`.

## The loop
1. **Reproduce** — run the failing thing (run_command), capture the ACTUAL error.
2. **Read the error** — the last line names the exception; look at the first frame in the user's own code.
3. **Locate** — search_files for the symbol. read_file has NO line-range arg and cuts off at 20000 chars; for one line use run_command `sed -n '1290,1320p' file`.
4. **Hypothesize ONE cause** and check it by reading the code first.
5. **Fix the root cause**, not the symptom — try/except is rarely the fix. Re-read the file right before every edit_file so your `find` text matches.
6. **Confirm** — re-run the command, adding a flag so it isn't a byte-identical repeat.

## Classic culprits
- Wrong directory / relative path (use absolute paths)
- Cached .pyc, or server not restarted after an edit
- Off-by-one, unexpected None, mutated shared default

## Discipline
- Change ONE thing per iteration; re-run each time.
- If two hypotheses fail, STOP and re-read the error — the wrong assumption is usually upstream.
