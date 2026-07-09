"""Project memory — GT.md, the project's own standing instructions.

Claude Code loads CLAUDE.md into every conversation so the agent starts each
session already knowing the project's stack, conventions and commands. This is
GT's version of that layer — the fix for universal failure mode #4 (amnesia:
"every session starts from zero"). A plain markdown file, written by the user
or by /init, that rides in the WORK system prompt on every turn.

Three layers, most specific last (so it wins the model's attention):

  1. user    — ~/.gt/GT.md                 your personal, cross-project rules
  2. project — GT.md found by walking UP from the workspace directory
               (AGENTS.md and CLAUDE.md are honoured as fallbacks, so a repo
               already set up for another agent just works)
  3. local   — GT.local.md next to the project file: personal, per-project
               overrides. Add it to .gitignore — it never ships.

Design constraints (same rules as the preference profile):
  - Loaded ONCE per session — and again only on /cd, /init, /memory reload or
    a '# note'. The injected block is byte-stable between loads, so Ollama's
    KV prefix cache stays valid across turns.
  - Injected ONLY into the work/plan prompt. Small talk keeps the lean chat
    prompt, so a greeting never pays prefill for the project brief.
  - Capped at project_memory.max_chars so a sprawling file can't blow the
    context window (the cap itself nudges: keep GT.md concise).
"""

from pathlib import Path

from .config import USER_DIR

# The user-level layer — one file of cross-project instructions.
USER_FILE = USER_DIR / "GT.md"

# Project-file names, in preference order. GT.md is ours; AGENTS.md and
# CLAUDE.md mean the repo already briefs coding agents — read theirs too.
PROJECT_NAMES = ("GT.md", "AGENTS.md", "CLAUDE.md")

LOCAL_NAME = "GT.local.md"

_CREATE_HEADER = ("# GT.md — project notes for GT\n"
                  "# GT loads this file into context every turn. Keep it "
                  "short and factual.\n")


def find_project_file(cwd) -> Path | None:
    """The nearest project-memory file, walking up from the workspace.

    Checks each directory for GT.md, then AGENTS.md, then CLAUDE.md. Stops
    after the repository root (the first directory holding .git) or the home
    directory — a GT.md lying around ABOVE the repo belongs to something else.
    """
    d = Path(cwd).resolve()
    home = Path.home().resolve()
    for folder in (d, *d.parents):
        for name in PROJECT_NAMES:
            f = folder / name
            if f.is_file():
                return f
        if (folder / ".git").exists() or folder == home:
            break
    return None


def _read(path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore").strip()
    except OSError:
        return ""


def load(cwd, max_chars=6000, user_file=USER_FILE):
    """Assemble the merged project-memory block for the system prompt.

    Returns (text, files): the labelled, capped block ('' when nothing is
    found) and the list of Paths that contributed — shown by /memory so the
    user always knows exactly what GT is reading (glass-box).
    """
    layers = [("your instructions (applies to every project)", user_file)]
    project = find_project_file(cwd)
    if project:
        layers.append(("this project's instructions", project))
        local = project.parent / LOCAL_NAME
        if local.is_file():
            layers.append(("your local overrides for this project", local))

    parts, files = [], []
    for label, path in layers:
        if not (path and path.is_file()):
            continue
        body = _read(path)
        if not body:
            continue
        files.append(path)
        parts.append(f"[{label} — {path.name}]\n{body}")

    text = "\n\n".join(parts)
    if len(text) > max_chars:
        text = (text[:max_chars]
                + "\n… [project memory truncated — keep GT.md concise]")
    return text, files


def append_note(cwd, note) -> Path:
    """'# always use pytest' → a bullet in the project's GT.md.

    Appends to the nearest GT.md; if the project only has an AGENTS.md or
    CLAUDE.md, those belong to another tool — the note goes into a NEW GT.md
    beside them instead. No project file at all → create GT.md in the
    workspace. Returns the file written.
    """
    found = find_project_file(cwd)
    if found is not None and found.name == "GT.md":
        target = found
    elif found is not None:
        target = found.parent / "GT.md"
    else:
        target = Path(cwd).resolve() / "GT.md"

    existing = _read(target) if target.is_file() else ""
    body = existing if existing else _CREATE_HEADER.rstrip()
    target.write_text(f"{body}\n- {note.strip()}\n", encoding="utf-8")
    return target
