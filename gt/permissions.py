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

    def approve(self, title: str, detail: str = "", key: str = None) -> bool:
        dangerous = bool(key and key.startswith("cmd:")
                         and _DANGEROUS.search(detail or ""))

        if not dangerous:
            if self.auto_approve:
                self.console.print(f"[dim]auto-approved: {title}[/dim]")
                return True
            if key and (key in self.grants or key in self.session_grants):
                self.console.print(f"[dim]allowed ({key}): {title}[/dim]")
                return True

        border = "red" if dangerous else "yellow"
        header = ("⚠ DANGEROUS — always requires confirmation"
                  if dangerous else "Permission needed")
        self.console.print(Panel(Text(detail or "(no details)"),
                                 title=f"{header} — {title}",
                                 border_style=border, expand=False))
        opts = "[y] yes once   [n] no"
        if key and not dangerous:
            opts = f"[y] yes once   [a] always allow '{key}'   [n] no"
        self.console.print(f"  {opts}")
        try:
            ans = self.prompt_fn("  allow? ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False

        if ans in ("a", "always") and key and not dangerous:
            self.grants.add(key)
            self._save()
            self.console.print(f"[green]✓ '{key}' allowed from now on "
                               f"(manage with /permissions)[/green]")
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
