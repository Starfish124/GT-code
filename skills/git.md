---
name: git
triggers: git, commit, commits, branch, merge, rebase, pull request, version control, gitignore, staging, checkout, git init, repo, repository
priority: 2
---
# Git playbook — a clean, readable history

The bar: someone reads `git log` and understands what changed and why.

## Before touching history, look
- `git status` and `git diff` first — know what's staged, unstaged, and
  untracked before you commit. Never `git add -A` blind; you'll commit junk
  (node_modules, .env, build output, secrets).
- `git log --oneline -10` to match the project's existing commit style
  (tense, prefixes) before writing your own.

## Starting a repo
- `git init`, then create a `.gitignore` BEFORE the first commit so garbage
  never enters history. Language essentials:
  - Python: `__pycache__/`, `*.pyc`, `.venv/`, `venv/`, `.env`, `*.egg-info/`,
    `.pytest_cache/`, `dist/`, `build/`
  - Node: `node_modules/`, `dist/`, `build/`, `.env*`, `*.log`, `.DS_Store`
  - Always: `.env`, secrets, credentials, large data files, `.DS_Store`.
- First commit small and working ("Initial commit: project scaffold"), not a
  half-built dump.

## Commit messages that earn their place
- Subject line: imperative mood, ≤ ~60 chars, no trailing period.
  "Add rate limiting to login" — NOT "added stuff" / "fixes" / "wip".
- Explain WHY in the body (wrap ~72 cols) when the change isn't obvious; the
  diff already shows the what.
- One logical change per commit — a bug fix and a refactor are two commits.
  A reviewer should be able to revert one without losing the other.
```
Add retry to the S3 upload path

Uploads failed intermittently on flaky networks; a 3x backoff
retry makes the daily sync reliable without masking real errors.
```

## Branching & merging
- Work on a feature branch, never commit straight to `main` for anything
  non-trivial: `git switch -c feat/thing` (or `git checkout -b`).
- Keep branches short-lived and focused. Rebase your own unpushed work to
  tidy it (`git rebase main`); never rebase or force-push shared branches
  others may have pulled.
- Resolve conflicts by reading BOTH sides and understanding intent — don't
  blindly accept one. After resolving, run the tests before committing.

## Safety — these prompt in red, and should
- `git push --force`, `git reset --hard`, `git clean -fd` destroy work. Prefer
  `--force-with-lease` over `--force`; prefer `git revert` (a new undo commit)
  over `reset --hard` on anything shared.
- To undo the last commit but keep the changes: `git reset --soft HEAD~1`.
  To unstage without losing edits: `git restore --staged <file>`.
- Never commit secrets. If one slips in, it's in history — rotate the secret;
  removing the file in a later commit does NOT scrub it.

## Verify
- After committing: `git log --oneline -3` and `git status` (clean tree). The
  history should read as a sequence of small, sensible, working steps.
