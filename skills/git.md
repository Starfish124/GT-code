---
name: git
triggers: git, commit, commits, rebase, pull request, version control, gitignore, git init
priority: 2
---
# Git — a commit is a permanent record you must defend

RULE 1 — NEVER describe a change you have not read. Run `git diff` THIS turn
before writing any commit message. If the diff showed you no reason, the body
states WHAT changed and stops. Never write a cause ("failed on flaky
networks", "users reported") you did not read in the diff or the user's
message. History cannot be edited.

RULE 2 — Stage files BY NAME: `git add path/a path/b`. Not `-A`, not `.` —
those sweep in .env, secrets, build output, node_modules. A secret in a commit
is in history forever: rotate it; deleting the file later does NOT scrub it.

RULE 3 — `git push --force`, `git push -f`, `git reset --hard`, `git clean -fd`
destroy work and MOST ARE NOT BLOCKED — no prompt will save you. Undo instead:
`git revert <sha>` (anything pushed), `git reset --soft HEAD~1` (last commit),
`git restore --staged <file>`. Never force-push or rebase a shared branch.

## Committing
There is no git tool. Every git action goes through `run_command`.
- Multi-line: `git commit -m "<subject>" -m "<body>"` — two -m flags become two
  paragraphs. Never put \n inside -m. Never run bare `git commit`: it opens an
  editor with no stdin and hangs.
- Subject: imperative, <=60 chars, no period. "Add rate limiting to login".
- One change per commit. Work on a branch: `git switch -c feat/thing`.
- Then run `git log --oneline -3` and `git status` and READ the output.
  Emitting the call is not proof it ran. If the tree is dirty, say so.
