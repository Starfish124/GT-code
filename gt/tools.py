"""The agent's tools + a tiny execution context.

Every tool declares a name, a description, and its args so the system prompt can
teach the model how to call it. Tools that change the machine (write/edit/run)
go through ctx.approve() so the user stays in control unless auto-approve is on.
"""

import atexit
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

from .base import Ctx, Tool  # noqa: F401  (re-exported; agent.py imports Ctx from here)
from .office import OFFICE_TOOLS

# A browser-ish UA so search engines / sites don't reject the request.
_USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")


def _file_preview(content: str, head_lines: int = 6, path=None):
    """A tight summary for the write-permission prompt — never the whole file.

    Dumping a 4000-char HTML file into the approval box buries the one thing
    the user is deciding on (which file, how big) under a wall of code. Show
    the size and just the first few lines instead — syntax-highlighted when
    the file type is recognisable from `path`, plain text otherwise.
    """
    if not content.strip():
        return "(empty file)"
    lines = content.splitlines()
    size = f"{len(lines)} line{'s' if len(lines) != 1 else ''}, {len(content)} chars"
    head = "\n".join(lines[:head_lines])
    more = (f"… (+{len(lines) - head_lines} more lines)"
            if len(lines) > head_lines else "")
    if path is not None:
        try:
            from rich.console import Group
            from rich.syntax import Syntax
            from rich.text import Text
            from .theme import CODE_THEME
            lexer = Syntax.guess_lexer(str(path), code=content)
            parts = [Text(size, style="dim"),
                     Syntax(head, lexer, theme=CODE_THEME, word_wrap=True,
                            background_color="default")]
            if more:
                parts.append(Text(more, style="dim"))
            return Group(*parts)
        except Exception:
            pass                    # unknown type/old rich — plain preview
    return f"{size}\n{head}" + (f"\n{more}" if more else "")


# --------------------------------------------------------------------------- #
#  File tools
# --------------------------------------------------------------------------- #

class ReadFile(Tool):
    name = "read_file"
    description = "Read and return the contents of a text file."
    args = {"path": "File path (relative to the workspace, or absolute)."}
    required = ("path",)

    def run(self, args, ctx):
        p = ctx.resolve(args["path"])
        if ctx.confine_enabled() and ctx.outside_workspace(p):
            if not ctx.approve(f"Read {p}",
                               "reading a file outside the workspace",
                               force=True):
                return ("DENIED: reading outside the workspace was declined. "
                        "Work with files inside the launch folder.")
        if not p.exists():
            return f"ERROR: file not found: {p}"
        if p.is_dir():
            return f"ERROR: {p} is a directory (use list_dir)."
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return f"ERROR reading {p}: {e}"
        if len(text) > 20000:
            text = text[:20000] + "\n... [truncated at 20000 chars]"
        return text or "(file is empty)"


class WriteFile(Tool):
    name = "write_file"
    description = ("Create or overwrite a file. For more than a couple of "
                   "lines, omit 'content' and put the raw file body in a "
                   "second fenced code block right after the json block.")
    args = {"path": "File path.",
            "content": "Full text to write (or use a content block instead)."}
    required = ("path", "content")
    # The fenced content block is a prompt-protocol escape hatch; natively the
    # arguments arrive structured, so content is just a normal argument.
    native_description = "Create or overwrite a file with the given content."
    changes_system = True

    def run(self, args, ctx):
        p = ctx.resolve(args["path"])
        content = args.get("content", "")
        guard = _office_script_guard(p, content, ctx)
        if guard:
            return guard
        outside = ctx.confine_enabled() and ctx.outside_workspace(p)
        if not ctx.approve(f"Write {p}", _file_preview(content, path=p),
                           key="files", force=outside):
            return ("DENIED: writing outside the workspace was declined."
                    if outside else "DENIED: user declined the write.")
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        except Exception as e:
            return f"ERROR writing {p}: {e}"
        note = _stub_note(p, content)
        return f"OK: wrote {len(content)} chars to {p}{note}"


# The "write a script instead of calling the tool" detour, proven live on
# qwen3:8b: it read a CSV and aggregated it perfectly with the stdlib, then
# for the ACTUAL deliverable wrote a brand-new pandas/openpyxl generator
# script (and went on to pip install its imports) instead of calling the
# create_excel tool it already has. Catch the detour at the moment the
# generator script is created — before the permission prompt — and teach the
# native-tool path. Only fires on a NEW .py file whose content bears an
# Office-file-writing signature, and never when the user's own request asked
# for a script (then the script IS the deliverable).
_OFFICE_SCRIPT = re.compile(
    r"(?im)^\s*(?:import|from)\s+(?:openpyxl|xlsxwriter|docx|pptx)\b"
    r"|\.to_excel\s*\(|\bExcelWriter\s*\(")
_WANTS_SCRIPT = re.compile(r"(?i)\bscripts?\b|\.py\b|\bpython (?:file|program|code)\b")


def _office_script_guard(path, content, ctx):
    if not str(path).lower().endswith(".py") or path.exists():
        return None          # editing/rewriting an existing script is maintenance
    if _WANTS_SCRIPT.search(ctx.user_msg or ""):
        return None
    if not _OFFICE_SCRIPT.search(content):
        return None
    return ("ERROR: this script's only job is to generate an Office file — "
            "that is exactly what GT's native create_excel / create_word / "
            "create_powerpoint tools do, directly, with no script and no "
            "extra libraries. Do NOT write a generator script: if values "
            "still need computing, use the stdlib (csv, json, "
            "collections.defaultdict/Counter), then call the create_* tool "
            "with the results as plain lists/dicts (e.g. create_excel with "
            "sheets=[{name, headers, rows}]). Write a generator script only "
            "when the user explicitly asked for a script.")


# A file that is only a skeleton reads as success to a small model — observed
# live: 'flappy bird' shipped an HTML whose whole body was '<!-- Game content
# goes here -->', then 'its blank just a white page'. The write succeeds (a
# skeleton is sometimes wanted) but the result SAYS it's a stub, so the model
# fixes it in the same turn instead of announcing the task done.
_STUB_MARK = re.compile(
    r"(?i)(?:goes here|content here|your (?:code|content|logic|game) here|"
    r"placeholder|to be implemented|implement(?:ation)? (?:this |goes )?later|"
    r"todo:? implement|fill (?:this |me )?in|coming soon|"
    r"rest of (?:the )?(?:code|file|logic))")
_HTML_BODY = re.compile(r"(?is)<body[^>]*>(.*?)</body>")


def _stub_note(path, content):
    m = _STUB_MARK.search(content)
    if m:
        return (f"\nNOTE: the content contains a placeholder ('{m.group(0)}')"
                " — the user asked for a WORKING result, not a skeleton. "
                "Write the real content now (write_file again with the full "
                "implementation).")
    if str(path).lower().endswith((".html", ".htm")):
        body = _HTML_BODY.search(content)
        inner = re.sub(r"(?s)<!--.*?-->", "", body.group(1)).strip() if body else ""
        if body and not inner and "<script" not in content.lower():
            return ("\nNOTE: the page <body> is empty — it will render as a "
                    "blank white page. Write the real markup and script now.")
    return ""


class EditFile(Tool):
    name = "edit_file"
    description = ("Replace an exact substring in a file. `find` must match "
                   "exactly once. Use for small, surgical edits.")
    args = {
        "path": "File path.",
        "find": "Exact text to find (must be unique in the file).",
        "replace": "Text to replace it with.",
    }
    required = ("path", "find", "replace")
    changes_system = True

    def run(self, args, ctx):
        p = ctx.resolve(args["path"])
        if not p.exists():
            return f"ERROR: file not found: {p}"
        find = args.get("find", "")
        replace = args.get("replace", "")
        # ''.count('') is len+1, so an empty find used to report "matched
        # 4321 times" on a 4320-char file (observed live) — catch it plainly.
        if not find:
            return ("ERROR: 'find' is empty. read_file the file first, then "
                    "pass the exact snippet to replace.")
        try:
            text = p.read_text(encoding="utf-8")
        except Exception as e:
            return f"ERROR reading {p}: {e}"
        count = text.count(find)
        if count == 0:
            return "ERROR: `find` text not found. Read the file first to copy it exactly."
        if count > 1:
            return (f"ERROR: `find` matched {count} times; it must be unique. "
                    "Include more surrounding context.")
        detail = f"- {find}\n+ {replace}"
        outside = ctx.confine_enabled() and ctx.outside_workspace(p)
        if not ctx.approve(f"Edit {p}", detail, key="files", force=outside):
            return ("DENIED: editing outside the workspace was declined."
                    if outside else "DENIED: user declined the edit.")
        try:
            p.write_text(text.replace(find, replace), encoding="utf-8")
        except Exception as e:
            return f"ERROR writing {p}: {e}"
        return f"OK: edited {p}"


class ListDir(Tool):
    name = "list_dir"
    description = "List files and folders in a directory."
    args = {"path": "Directory path (default: workspace root)."}

    def run(self, args, ctx):
        p = ctx.resolve(args.get("path", "."))
        if not p.exists():
            return f"ERROR: not found: {p}"
        if not p.is_dir():
            return f"ERROR: {p} is not a directory."
        entries = []
        for child in sorted(p.iterdir()):
            entries.append(("[dir] " if child.is_dir() else "      ") + child.name)
        return f"{p}:\n" + "\n".join(entries) if entries else f"{p} is empty."


class SearchFiles(Tool):
    name = "search_files"
    description = "Search file contents for a substring (case-insensitive) under a directory."
    args = {
        "query": "Text to search for.",
        "path": "Directory to search under (default: workspace root).",
    }
    required = ("query",)

    # NOTE: no bare "data" here — user projects legitimately have data/
    # folders. Only GT's OWN data dir (memory db, logs) is skipped, by path.
    _SKIP = {".git", "node_modules", ".venv", "__pycache__", ".idea"}
    _EXET = {".py", ".js", ".ts", ".tsx", ".jsx", ".md", ".txt", ".json", ".yaml",
             ".yml", ".toml", ".cfg", ".ini", ".html", ".css", ".sh", ".bat",
             ".go", ".rs", ".java", ".c", ".cpp", ".h"}

    def run(self, args, ctx):
        root = ctx.resolve(args.get("path", "."))
        query = args.get("query", "")
        if not query:
            return "ERROR: empty query."
        gt_data = str(Path(ctx.config.data_dir))
        q = query.lower()
        hits, scanned = [], 0
        for f in root.rglob("*"):
            if any(part in self._SKIP for part in f.parts):
                continue
            if str(f).startswith(gt_data):
                continue
            if not f.is_file() or f.suffix.lower() not in self._EXET:
                continue
            scanned += 1
            try:
                for i, line in enumerate(f.read_text(encoding="utf-8",
                                                     errors="ignore").splitlines(), 1):
                    if q in line.lower():
                        hits.append(f"{f}:{i}: {line.strip()[:200]}")
                        if len(hits) >= 100:
                            break
            except Exception:
                continue
            if len(hits) >= 100:
                break
        if not hits:
            return f"No matches for '{query}' (scanned {scanned} files)."
        return "\n".join(hits[:100])


# --------------------------------------------------------------------------- #
#  Shell tools
# --------------------------------------------------------------------------- #

def _shell_env() -> dict:
    """Environment that keeps CLIs from waiting on a hidden interactive prompt.

    stdin is closed for every command, so anything that asks a question would
    hang until the timeout. CI=1 + npm_config_yes flip npm/npx/vite/CRA and
    most modern CLIs into non-interactive mode instead.
    """
    env = os.environ.copy()
    env.setdefault("CI", "1")
    env.setdefault("npm_config_yes", "true")
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    env.setdefault("PYTHONIOENCODING", "utf-8")  # python children on Windows
    return env


# Decode command output as UTF-8 with replacement, never the Windows locale:
# text=True alone means cp1252 there, and npm's emoji/box-chars raise
# UnicodeDecodeError inside communicate(), losing the whole command result.
_DECODE = {"encoding": "utf-8", "errors": "replace"}


def _kill_tree(proc: subprocess.Popen):
    """Kill a shell=True command AND its children (npm spawns node spawns...)."""
    try:
        if os.name == "nt":
            subprocess.run(f"taskkill /F /T /PID {proc.pid}",
                           shell=True, capture_output=True)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _popen_kwargs():
    """start_new_session gives us a process group to kill on POSIX."""
    return {"start_new_session": True} if os.name != "nt" else {}


class _BackgroundProcs:
    """Registry of processes GT started with background=true (dev servers)."""

    def __init__(self):
        self.procs = {}   # id -> {"proc", "log", "command"}
        self.next_id = 1
        atexit.register(self.kill_all)

    def start(self, command: str, cwd: Path, log_dir: Path):
        log_dir.mkdir(parents=True, exist_ok=True)
        pid = self.next_id
        self.next_id += 1
        log = log_dir / f"proc-{pid}.log"
        handle = open(log, "w", encoding="utf-8", errors="replace")
        proc = subprocess.Popen(
            command, shell=True, cwd=str(cwd),
            stdout=handle, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, env=_shell_env(), **_popen_kwargs(),
        )
        self.procs[pid] = {"proc": proc, "log": log, "command": command,
                           "handle": handle}
        return pid, proc, log

    def get(self, pid):
        return self.procs.get(pid)

    @staticmethod
    def close_handle(entry):
        try:
            entry["handle"].close()
        except Exception:
            pass

    def kill_all(self):
        for entry in self.procs.values():
            if entry["proc"].poll() is None:
                _kill_tree(entry["proc"])
            self.close_handle(entry)


BACKGROUND = _BackgroundProcs()


_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _log_tail(log: Path, chars: int = 3000) -> str:
    try:
        text = log.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    return _ANSI.sub("", text)[-chars:].strip()


class RunCommand(Tool):
    name = "run_command"
    description = ("Run a shell command and return its output. On Windows this "
                   "is cmd; on macOS/Linux the default shell. Each call starts "
                   "fresh in the workspace root — `cd` does NOT persist; use "
                   "the cwd arg instead. Commands cannot answer interactive "
                   "prompts, so always pass non-interactive flags (-y, --yes).")
    args = {
        "command": "The command line to execute.",
        "cwd": "Optional: directory to run in (relative to the workspace).",
        "timeout": "Optional: seconds before the command is killed "
                   "(default 300; raise it for installs/builds, max 1800).",
        "background": "Optional: true to start a long-lived process (dev "
                      "server, watcher) and return immediately. Inspect it "
                      "later with check_process / stop_process.",
    }
    arg_types = {"timeout": {"type": "integer"},
                 "background": {"type": "boolean"}}
    required = ("command",)
    changes_system = True

    def run(self, args, ctx):
        command = str(args.get("command", "")).strip()
        if not command:
            return "ERROR: empty command."

        workdir = ctx.resolve(str(args.get("cwd") or "."))
        if not workdir.is_dir():
            return f"ERROR: cwd does not exist: {workdir}"
        workdir, command = self._fold_cd(command, workdir, ctx)

        guard = self._self_venv_guard(command, workdir)
        if guard:
            return guard
        guard = self._flail_guard(command)
        if guard:
            return guard

        # Install-cascade breaker (proven live: one broken environment chased
        # as pip → pip3 → -m pip → brew for 300+ seconds — every command line
        # DIFFERENT, so the identical-call rail never fired): once a pip
        # install of a package has failed this turn, every further attempt to
        # install that same package is refused, whatever the spelling.
        pkgs = self._pip_pkgs(command)
        already = pkgs & ctx.state.get("pip_failed", set())
        if already:
            pkg = sorted(already)[0]
            return (f"ERROR: a pip install of '{pkg}' already failed this "
                    f"turn. Retrying with a different pip spelling (pip / "
                    f"pip3 / -m pip) or from a different folder hits the "
                    f"SAME broken environment, not a different installer — "
                    f"it will fail the same way. Stop chasing the install: "
                    f"solve the task with the stdlib and GT's own tools, or "
                    f"report the missing dependency to the user as the "
                    f"blocker.")

        from .permissions import command_keys
        detail = command if workdir == ctx.cwd else f"(in {workdir})  {command}"
        outside = ctx.confine_enabled() and ctx.outside_workspace(workdir)
        if not ctx.approve("Run command", detail, key=command_keys(command),
                           force=outside):
            return ("DENIED: running a command outside the workspace was declined."
                    if outside else "DENIED: user declined to run the command.")

        if str(args.get("background", "")).lower() in ("true", "1", "yes"):
            return self._run_background(command, workdir, ctx)
        result = self._run_foreground(command, workdir, ctx, args)
        if pkgs and re.match(r"exit code: (?!0\b)", result):
            ctx.state.setdefault("pip_failed", set()).update(pkgs)
        return result

    _CD = re.compile(r"^cd\s+(\"[^\"]+\"|'[^']+'|[^&;|]+?)\s*(?:&&|;)\s*(.+)$",
                     re.S)

    # Everything after `-m venv` / `virtualenv` up to the next chain operator.
    _VENV = re.compile(r"(?:-m\s+venv|\bvirtualenv)\s+([^&|;\n]+)", re.I)

    @classmethod
    def _self_venv_guard(cls, command, workdir):
        """Refuse to (re)create the venv that is running GT itself.

        Live on Windows: GT was launched from its own repo, the model ran
        `python -m venv .venv` there, and creation failed with Errno 13
        because that .venv's python.exe IS the process running GT. Catch it
        before the user is even prompted and steer the model to a project
        folder instead.
        """
        if sys.prefix == sys.base_prefix:   # GT not in a venv — nothing to protect
            return None
        try:
            own = Path(sys.prefix).resolve()
        except OSError:
            return None
        for m in cls._VENV.finditer(command):
            for tok in m.group(1).split():
                tok = tok.strip("\"'")
                if tok.startswith("-"):     # a flag like --clear, keep looking
                    continue
                t = Path(tok).expanduser()
                t = t if t.is_absolute() else workdir / t
                try:
                    hit = t.resolve() == own
                except OSError:
                    hit = False
                if hit:
                    return (f"ERROR: '{tok}' is the Python environment running "
                            "GT itself — recreating it would fail (its python "
                            "is in use) and would break GT. Work in a project "
                            "folder instead: create the venv in a subdirectory "
                            "or under a different name.")
                break                       # only the first non-flag token is the target
        return None

    # Packages GT's own document tools never need — a CSV/JSON small enough
    # for a person to hand over is fully handled by the stdlib (csv, json,
    # collections). Installing one of these is the exact rabbit hole proven
    # live TWICE: an unrelated system Python's pip failing, then chasing it
    # through brew reinstalls for minutes with nothing ever built. The data
    # playbook already says not to; this makes it a hard refusal, not advice.
    _DATA_LIBS = re.compile(
        r"\b(pandas|numpy|scipy|matplotlib|scikit-learn|sklearn)\b", re.I)
    _PIP_INSTALL = re.compile(r"\bpip3?\s+install\b|-m\s+pip\s+install\b", re.I)

    # A `python -c "..."` one-liner with a compound statement (with/for/if/
    # while/def/class/try) chained after a semicolon cannot parse — Python's
    # grammar does not allow a compound statement as a simple_stmt. Proven
    # live twice: `import csv; with open(...) as f: for row in ...` fails
    # before any real work happens, then gets retried in slight variants.
    _DASH_C = re.compile(r"(?:^|[\s&|;])python3?\s+-c\s+", re.I)
    _COMPOUND_AFTER_SEMI = re.compile(r";\s*(with|for|if|while|def|class|try)\b")

    @classmethod
    def _pip_pkgs(cls, command):
        """Package names named by any pip-install segment of a command line
        (empty set if it isn't one). 'pip' itself is ignored — upgrading pip
        is plumbing, not a dependency chase."""
        pkgs = set()
        for seg in re.split(r"[&|;]+", command):
            m = cls._PIP_INSTALL.search(seg)
            if m:
                pkgs |= {t.strip("\"'").lower() for t in seg[m.end():].split()
                         if t and not t.startswith("-")}
        pkgs.discard("pip")
        return pkgs

    @classmethod
    def _flail_guard(cls, command):
        """Refuse the two run_command patterns proven live to burn an entire
        turn's step budget without producing anything: installing a
        data-science library GT's tools never need, and a `python -c`
        one-liner that cannot parse. Each refusal teaches the working
        alternative instead of just denying, so the model recovers instead
        of retrying a cosmetic variant of the same dead end."""
        if cls._PIP_INSTALL.search(command):
            m = cls._DATA_LIBS.search(command)
            if m:
                pkg = m.group(1)
                return (f"ERROR: GT's tools do not need '{pkg}'. A CSV/JSON "
                        f"of any size a person would hand you fits in memory "
                        f"as plain Python: read it with the stdlib csv "
                        f"module, aggregate with collections.defaultdict or "
                        f"Counter, and pass the result straight to "
                        f"create_excel / create_word / create_powerpoint — "
                        f"they take plain lists/dicts, not a DataFrame. "
                        f"Do NOT install {pkg}; write a short .py script "
                        f"instead (write_file it, then run_command it).")
        if cls._DASH_C.search(command) and cls._COMPOUND_AFTER_SEMI.search(command):
            return ("ERROR: a `python -c` one-liner cannot contain a "
                    "compound statement (with/for/if/while/def/class/try) "
                    "after a semicolon — that is a guaranteed SyntaxError, "
                    "not a shell-quoting issue, and retrying a reworded "
                    "variant will fail the same way. Write a real script "
                    "instead: write_file a short .py file with normal "
                    "indentation, then run_command it (e.g. "
                    "\"<python>\" script.py).")
        return None

    @classmethod
    def _fold_cd(cls, command, workdir, ctx):
        """Fold a leading `cd X && …` into the working directory.

        Models routinely write `cd app && npm install` AND pass cwd="app" —
        the doubled cd then fails with 'no such file or directory'. Resolve
        the cd target against the given cwd first, then the workspace root,
        and run the rest of the command there. Unresolvable targets are left
        untouched so the shell can report the real error.
        """
        for _ in range(3):  # a couple of chained cds at most
            m = cls._CD.match(command.strip())
            if not m:
                break
            target = m.group(1).strip().strip("\"'")
            t = Path(target).expanduser()
            for cand in ([t] if t.is_absolute()
                         else [workdir / t, ctx.cwd / t]):
                if cand.is_dir():
                    workdir, command = cand, m.group(2).strip()
                    break
            else:
                break
        return workdir, command

    def _run_foreground(self, command, workdir, ctx, args):
        default_t = int(ctx.config.agent.get("command_timeout", 300))
        try:
            timeout = min(max(int(args.get("timeout") or default_t), 5), 1800)
        except (TypeError, ValueError):
            timeout = default_t
        try:
            proc = subprocess.Popen(
                command, shell=True, cwd=str(workdir),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL, env=_shell_env(),
                **_DECODE, **_popen_kwargs(),
            )
        except Exception as e:
            return f"ERROR launching command: {e}"
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            try:
                out, err = proc.communicate(timeout=5)
            except Exception:
                out, err = "", ""
            partial = (f"\npartial stdout:\n{out[-3000:]}" if out else "") + \
                      (f"\npartial stderr:\n{err[-1500:]}" if err else "")
            return (f"ERROR: command killed after {timeout}s.{partial}\n"
                    "If it is a long install/build, retry with a bigger "
                    '"timeout". If it is a server/watcher that never exits, '
                    'rerun it with "background": true.')
        out = (out or "")[-6000:]
        err = (err or "")[-3000:]
        parts = [f"exit code: {proc.returncode}"]
        if out:
            parts.append(f"stdout:\n{out}")
        if err:
            parts.append(f"stderr:\n{err}")
        return "\n".join(parts)

    def _run_background(self, command, workdir, ctx):
        log_dir = Path(ctx.config.data_dir) / "logs"
        try:
            pid, proc, log = BACKGROUND.start(command, workdir, log_dir)
        except Exception as e:
            return f"ERROR launching background command: {e}"
        time.sleep(3)  # long enough to catch instant crashes + first output
        tail = _log_tail(log, 2000)
        if proc.poll() is not None:
            return (f"ERROR: background process exited immediately "
                    f"(exit code {proc.returncode}).\noutput:\n{tail}")
        return (f"OK: started background process {pid}: {command}\n"
                f"early output:\n{tail}\n"
                f'Use {{"tool": "check_process", "args": {{"id": {pid}}}}} to '
                "see status/output and verify it works (e.g. request the URL "
                "it serves), and stop_process to stop it.")


class CheckProcess(Tool):
    name = "check_process"
    description = ("Check a background process started by run_command: is it "
                   "still running, and what has it printed so far.")
    args = {"id": "The process id returned when it was started."}
    arg_types = {"id": {"type": "integer"}}
    required = ("id",)

    def run(self, args, ctx):
        entry = self._entry(args)
        if isinstance(entry, str):
            return entry
        code = entry["proc"].poll()
        status = "RUNNING" if code is None else f"EXITED (code {code})"
        return (f"process {args.get('id')}: {status} — {entry['command']}\n"
                f"output:\n{_log_tail(entry['log'])}")

    @staticmethod
    def _entry(args):
        try:
            pid = int(args.get("id"))
        except (TypeError, ValueError):
            return "ERROR: pass the numeric process id."
        entry = BACKGROUND.get(pid)
        if not entry:
            known = ", ".join(str(k) for k in BACKGROUND.procs) or "none"
            return f"ERROR: no background process {pid} (known: {known})."
        return entry


class StopProcess(Tool):
    name = "stop_process"
    description = "Stop a background process started by run_command."
    args = {"id": "The process id returned when it was started."}
    arg_types = {"id": {"type": "integer"}}
    required = ("id",)

    def run(self, args, ctx):
        entry = CheckProcess._entry(args)
        if isinstance(entry, str):
            return entry
        if entry["proc"].poll() is not None:
            BACKGROUND.close_handle(entry)
            return (f"process {args.get('id')} had already exited "
                    f"(code {entry['proc'].returncode}).")
        _kill_tree(entry["proc"])
        BACKGROUND.close_handle(entry)
        return f"OK: stopped process {args.get('id')} ({entry['command']})."


# --------------------------------------------------------------------------- #
#  Interaction tool — lets GT clarify requirements like Claude Code does
# --------------------------------------------------------------------------- #

def _echoes_user(question: str, user_msg: str) -> bool:
    """True if `question` just parrots the user's own message back at them.

    Small models often turn a question they should ANSWER ("can you use the
    internet?") into an ask_user call with the very same words — a maddening
    loop. Compare the word sets: a near-identical restatement, or a subset of
    what the user just said, is an echo, not a real clarifying question.
    """
    q = set(re.findall(r"[a-z0-9]+", (question or "").lower()))
    u = set(re.findall(r"[a-z0-9]+", (user_msg or "").lower()))
    if len(q) < 2 or not u:
        return False
    overlap = len(q & u)
    return (overlap / len(q | u) >= 0.6) or (overlap >= 2 and q <= u)


class AskUser(Tool):
    name = "ask_user"
    description = ("Ask the user ONE short question and get their typed answer. "
                   "ONLY for a genuine decision that blocks you and that you "
                   "cannot reasonably make yourself. NEVER use it to answer or "
                   "repeat a question the user asked you — answer that directly "
                   "in prose. Never ask about details you can decide yourself "
                   "(stack, ports, file names, folder layout) — pick a sensible "
                   "default and note it. You get at most 3 questions per task. "
                   "Don't use it for permission to run tools — those handle "
                   "approval themselves.")
    args = {"question": "The question to ask (keep it short and concrete)."}
    required = ("question",)

    MAX_PER_TURN = 3

    def run(self, args, ctx):
        question = str(args.get("question", "")).strip()
        if not question:
            return "ERROR: empty question."
        if not ctx.ask:
            return "ERROR: interactive input is not available right now."
        # Don't hand the user their own question back — answer it directly.
        if _echoes_user(question, getattr(ctx, "user_msg", "")):
            return ("ERROR: that just repeats the user's own message back to "
                    "them. Do NOT ask it. ANSWER it directly in prose from "
                    "what you know and what you can do (your tools/capabilities "
                    "are listed in your instructions). Use ask_user only for a "
                    "NEW decision the user has not already stated.")
        asked = ctx.state.get("asks", 0)
        if asked >= self.MAX_PER_TURN:
            return ("ERROR: you have used all 3 questions for this task. Stop "
                    "asking — pick sensible defaults for anything still "
                    "unclear, state them, and continue with the work.")
        ctx.state["asks"] = asked + 1
        answer = ctx.ask(question)
        return f"The user answered: {answer}" if answer.strip() \
            else "The user gave no answer — use your best judgment and continue."


# --------------------------------------------------------------------------- #
#  Task tracking — the model's external memory (Claude Code's TodoWrite)
# --------------------------------------------------------------------------- #

class WriteTodos(Tool):
    name = "write_todos"
    description = (
        "Track a multi-step task as a checklist so you never lose the plan "
        "half-way through. Use it for ANY task of ~3+ steps: write the whole "
        "plan first, then keep exactly ONE item 'doing' as you work and flip it "
        "to 'done' before starting the next. Always pass the FULL list — it "
        "replaces the previous one. This is your external memory; it survives "
        "even if the conversation is compacted, so consult and update it instead "
        "of trying to hold every step in your head.")
    args = {"todos": 'List of {"task": <short text>, "status": '
                     '"pending"|"doing"|"done"}.'}
    arg_types = {"todos": {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "task": {"type": "string"},
                "status": {"type": "string",
                           "enum": ["pending", "doing", "done"]},
            },
            "required": ["task"],
        },
    }}
    required = ("todos",)

    _STATUS = {"pending", "doing", "done"}

    def run(self, args, ctx):
        raw = args.get("todos")
        # Small models JSON-encode the list itself as a string (observed
        # live, repeatedly) — repair the well-formed case instead of erroring.
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except ValueError:
                pass
        if not isinstance(raw, list):
            return ("ERROR: 'todos' must be a JSON list of "
                    '{"task": ..., "status": ...} objects.')
        items = []
        for t in raw:
            if isinstance(t, str) and t.strip():
                items.append({"task": t.strip()[:200], "status": "pending"})
            elif isinstance(t, dict) and str(t.get("task", "")).strip():
                status = str(t.get("status", "pending")).lower()
                items.append({"task": str(t["task"]).strip()[:200],
                              "status": status if status in self._STATUS
                              else "pending"})
        if not items:
            return "ERROR: no valid todos in the list."
        if items == list(ctx.todos):
            # Live: after a plan turn, the build turn burned 250+ tokens (and
            # minutes of CPU prefill) re-sending the identical checklist.
            return ("OK: checklist unchanged — it is already recorded, do not "
                    "resend it. Start on the next pending item now.")
        ctx.todos[:] = items          # replace in place so the agent's list updates
        done = sum(1 for t in items if t["status"] == "done")
        doing = next((t["task"] for t in items if t["status"] == "doing"), None)
        tail = f" · now: {doing}" if doing else ""
        return f"OK: {done}/{len(items)} done{tail}."


def render_todos(todos) -> str:
    """A compact checklist for the user AND for re-injection into context."""
    mark = {"done": "[x]", "doing": "[~]", "pending": "[ ]"}
    return "\n".join(f"  {mark.get(t['status'], '[ ]')} {t['task']}" for t in todos)


# --------------------------------------------------------------------------- #
#  Memory tool
# --------------------------------------------------------------------------- #

class RunAgent(Tool):
    name = "run_agent"
    description = ("Send a research sub-agent to investigate something and "
                   "report back. It reads files and searches the codebase/web "
                   "in its OWN separate context — only its final report comes "
                   "back to you, so a broad exploration never floods your "
                   "conversation. It is READ-ONLY (cannot write or run "
                   "anything) and cannot ask questions. Use it when answering "
                   "needs MANY reads or searches (map a codebase, find every "
                   "usage, research a library); for one or two known files, "
                   "just read them yourself.")
    args = {"task": "the job — one clear, self-contained brief; include any "
                    "paths/names you already know"}
    required = ("task",)

    def run(self, args, ctx):
        task = str(args.get("task") or "").strip()
        if not task:
            return "ERROR: run_agent needs a 'task' brief."
        if ctx.spawn is None:
            return ("ERROR: sub-agents are not available here — do the "
                    "research yourself with read_file/search_files.")
        return ctx.spawn(task)


class Recall(Tool):
    name = "recall"
    description = ("Search long-term memory (notes, lessons, indexed docs) for "
                   "anything relevant to a query.")
    args = {"query": "What to look up."}
    required = ("query",)

    def run(self, args, ctx):
        hits = ctx.memory.search(args.get("query", ""), k=6)
        if not hits:
            return "No relevant memories."
        return "\n".join(f"[{kind} {score:.2f}] {text}" for score, kind, text, _ in hits)


# --------------------------------------------------------------------------- #
#  Web tools (keyless — DuckDuckGo HTML; require internet)
# --------------------------------------------------------------------------- #

def _unwrap_ddg(href: str) -> str:
    """DuckDuckGo wraps result links in a /l/?uddg=<encoded> redirect."""
    from urllib.parse import urlparse, parse_qs, unquote
    if "uddg=" in href:
        try:
            qs = parse_qs(urlparse(href).query)
            if qs.get("uddg"):
                return unquote(qs["uddg"][0])
        except Exception:
            pass
    if href.startswith("//"):
        return "https:" + href
    return href


class WebSearch(Tool):
    name = "web_search"
    description = ("Search the web (DuckDuckGo) and return the top results as "
                   "title / url / snippet. Use when you need current info.")
    args = {"query": "What to search for."}
    required = ("query",)

    def run(self, args, ctx):
        query = args.get("query", "").strip()
        if not query:
            return "ERROR: empty query."
        try:
            import requests
            from bs4 import BeautifulSoup
        except ImportError:
            return ("ERROR: web tools need 'beautifulsoup4'. "
                    "Run setup again (pip install -r requirements.txt).")
        try:
            r = requests.post(
                "https://html.duckduckgo.com/html/",
                data={"q": query},
                headers={"User-Agent": _USER_AGENT},
                timeout=15,
            )
            r.raise_for_status()
        except Exception as e:
            return f"ERROR: web search failed (offline?): {e}"

        soup = BeautifulSoup(r.text, "html.parser")
        results = []
        for res in soup.select(".result"):
            a = res.select_one(".result__a")
            if not a:
                continue
            title = a.get_text(" ", strip=True)
            url = _unwrap_ddg(a.get("href", ""))
            snip_el = res.select_one(".result__snippet")
            snippet = snip_el.get_text(" ", strip=True) if snip_el else ""
            results.append((title, url, snippet))
            if len(results) >= 5:
                break
        if not results:
            return f"No results for '{query}'."
        return "\n\n".join(
            f"{i}. {t}\n   {u}\n   {s}" for i, (t, u, s) in enumerate(results, 1)
        )


class WebFetch(Tool):
    name = "web_fetch"
    description = ("Fetch a URL and return its readable text (HTML stripped). "
                   "Use to read a page found via web_search.")
    args = {"url": "The URL to fetch."}
    required = ("url",)

    def run(self, args, ctx):
        url = args.get("url", "").strip()
        if not url:
            return "ERROR: empty url."
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        try:
            import requests
            from bs4 import BeautifulSoup
        except ImportError:
            return "ERROR: web tools need 'beautifulsoup4' (rerun setup)."
        try:
            r = requests.get(url, headers={"User-Agent": _USER_AGENT},
                             timeout=20, stream=True)
            r.raise_for_status()
            # Cap the download at 2 MB — a stray link to a huge file would
            # otherwise be pulled whole and fed to the HTML parser.
            raw = b""
            for chunk in r.iter_content(65536):
                raw += chunk
                if len(raw) > 2_000_000:
                    break
        except Exception as e:
            return f"ERROR fetching {url}: {e}"
        text = raw.decode(r.encoding or "utf-8", errors="replace")

        ctype = r.headers.get("Content-Type", "")
        if "html" not in ctype and "xml" not in ctype:
            return text[:15000]  # already plain text / json / code

        soup = BeautifulSoup(text, "html.parser")
        for tag in soup(["script", "style", "noscript", "header", "footer",
                         "nav", "svg", "form"]):
            tag.decompose()
        title = soup.title.get_text(strip=True) if soup.title else ""
        lines = [ln for ln in (l.strip() for l in
                               soup.get_text("\n").splitlines()) if ln]
        body = "\n".join(lines)[:15000]
        head = f"# {title}\n{url}\n\n" if title else f"{url}\n\n"
        return head + body


# --------------------------------------------------------------------------- #
#  Registry
# --------------------------------------------------------------------------- #

ALL_TOOLS = [
    ReadFile(), WriteFile(), EditFile(), ListDir(),
    SearchFiles(), RunCommand(), CheckProcess(), StopProcess(),
    WriteTodos(), RunAgent(), Recall(), AskUser(), WebSearch(), WebFetch(),
] + OFFICE_TOOLS
REGISTRY = {t.name: t for t in ALL_TOOLS}

# Tools that reach the internet — filtered out when web.enabled is false.
_WEB_TOOLS = {"web_search", "web_fetch"}


def active_tools(config) -> list:
    """The tools available given config — web tools drop out when
    web.enabled is false, run_agent when subagents.enabled is false."""
    # Secure-by-default: an ABSENT web key means OFF, not on. Web egress is only
    # available when it's explicitly enabled in config.
    web_on = getattr(config, "web", {}).get("enabled", False)
    sub_on = getattr(config, "data", {}).get(
        "subagents", {}).get("enabled", True)
    return [t for t in ALL_TOOLS
            if (web_on or t.name not in _WEB_TOOLS)
            and (sub_on or t.name != "run_agent")]


# A short, human phrase for each tool that grants a real capability — used to
# tell GT (and, through it, the user) what it can actually do. Tools that are
# plumbing (list_dir, process control, ask_user) map to None and are omitted.
_CAPABILITY = {
    "read_file": "read files",
    "write_file": "create and write files",
    "edit_file": "edit files",
    "search_files": "search across the codebase",
    "run_command": "run shell commands, scripts and dev servers",
    "run_agent": "delegate research to a read-only sub-agent",
    "recall": "remember things across sessions",
    "web_search": "search the web",
    "web_fetch": "open and read web pages",
    "create_excel": "build Excel spreadsheets",
    "create_powerpoint": "build PowerPoint decks",
    "create_word": "write Word documents",
}


def capability_summary(tools=None) -> str:
    """One readable clause listing what GT can do, from the ACTIVE tools.

    Because web tools drop out when web.enabled is false, the summary is
    always truthful — GT won't claim internet access it doesn't have.
    """
    tools = list(tools if tools is not None else ALL_TOOLS)
    phrases, seen = [], set()
    for t in tools:
        phrase = _CAPABILITY.get(t.name)
        if phrase and phrase not in seen:
            seen.add(phrase)
            phrases.append(phrase)
    return ", ".join(phrases)


def tool_docs(tools=None) -> str:
    """Render a tool list for the system prompt (defaults to all tools)."""
    lines = []
    for t in (tools if tools is not None else ALL_TOOLS):
        arg_str = ", ".join(f'"{k}": <{v}>' for k, v in t.args.items())
        flag = "  (asks for approval)" if t.changes_system else ""
        lines.append(f'- {t.name}: {t.description}{flag}\n    args: {{{arg_str}}}')
    return "\n".join(lines)


def tool_specs(tools=None) -> list:
    """The toolset as native function-calling specs, for Ollama's tools=[...]
    parameter (defaults to all tools). See Tool.spec()."""
    return [t.spec() for t in (tools if tools is not None else ALL_TOOLS)]
