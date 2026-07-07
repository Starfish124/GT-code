"""The agent's tools + a tiny execution context.

Every tool declares a name, a description, and its args so the system prompt can
teach the model how to call it. Tools that change the machine (write/edit/run)
go through ctx.approve() so the user stays in control unless auto-approve is on.
"""

import atexit
import os
import re
import signal
import subprocess
import time
from pathlib import Path

from .base import Ctx, Tool  # noqa: F401  (re-exported; agent.py imports Ctx from here)
from .office import OFFICE_TOOLS

# A browser-ish UA so search engines / sites don't reject the request.
_USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")


# --------------------------------------------------------------------------- #
#  File tools
# --------------------------------------------------------------------------- #

class ReadFile(Tool):
    name = "read_file"
    description = "Read and return the contents of a text file."
    args = {"path": "File path (relative to the workspace, or absolute)."}

    def run(self, args, ctx):
        p = ctx.resolve(args["path"])
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
    description = "Create or overwrite a file with the given content."
    args = {"path": "File path.", "content": "Full text to write."}
    changes_system = True

    def run(self, args, ctx):
        p = ctx.resolve(args["path"])
        content = args.get("content", "")
        preview = content if len(content) < 800 else content[:800] + "\n... [more]"
        if not ctx.approve(f"Write {p}", preview, key="files"):
            return "DENIED: user declined the write."
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        except Exception as e:
            return f"ERROR writing {p}: {e}"
        return f"OK: wrote {len(content)} chars to {p}"


class EditFile(Tool):
    name = "edit_file"
    description = ("Replace an exact substring in a file. `find` must match "
                   "exactly once. Use for small, surgical edits.")
    args = {
        "path": "File path.",
        "find": "Exact text to find (must be unique in the file).",
        "replace": "Text to replace it with.",
    }
    changes_system = True

    def run(self, args, ctx):
        p = ctx.resolve(args["path"])
        if not p.exists():
            return f"ERROR: file not found: {p}"
        find = args.get("find", "")
        replace = args.get("replace", "")
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
        if not ctx.approve(f"Edit {p}", detail, key="files"):
            return "DENIED: user declined the edit."
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

    _SKIP = {".git", "node_modules", ".venv", "__pycache__", "data", ".idea"}
    _EXET = {".py", ".js", ".ts", ".tsx", ".jsx", ".md", ".txt", ".json", ".yaml",
             ".yml", ".toml", ".cfg", ".ini", ".html", ".css", ".sh", ".bat",
             ".go", ".rs", ".java", ".c", ".cpp", ".h"}

    def run(self, args, ctx):
        root = ctx.resolve(args.get("path", "."))
        query = args.get("query", "")
        if not query:
            return "ERROR: empty query."
        q = query.lower()
        hits, scanned = [], 0
        for f in root.rglob("*"):
            if any(part in self._SKIP for part in f.parts):
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
    return env


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
        self.procs[pid] = {"proc": proc, "log": log, "command": command}
        return pid, proc, log

    def get(self, pid):
        return self.procs.get(pid)

    def kill_all(self):
        for entry in self.procs.values():
            if entry["proc"].poll() is None:
                _kill_tree(entry["proc"])


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
    changes_system = True

    def run(self, args, ctx):
        command = str(args.get("command", "")).strip()
        if not command:
            return "ERROR: empty command."

        workdir = ctx.resolve(str(args.get("cwd") or "."))
        if not workdir.is_dir():
            return f"ERROR: cwd does not exist: {workdir}"

        from .permissions import command_keys
        detail = command if workdir == ctx.cwd else f"(in {workdir})  {command}"
        if not ctx.approve("Run command", detail, key=command_keys(command)):
            return "DENIED: user declined to run the command."

        if str(args.get("background", "")).lower() in ("true", "1", "yes"):
            return self._run_background(command, workdir, ctx)
        return self._run_foreground(command, workdir, ctx, args)

    def _run_foreground(self, command, workdir, ctx, args):
        default_t = int(ctx.config.agent.get("command_timeout", 300))
        try:
            timeout = min(max(int(args.get("timeout") or default_t), 5), 1800)
        except (TypeError, ValueError):
            timeout = default_t
        try:
            proc = subprocess.Popen(
                command, shell=True, cwd=str(workdir),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                stdin=subprocess.DEVNULL, env=_shell_env(), **_popen_kwargs(),
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

    def run(self, args, ctx):
        entry = CheckProcess._entry(args)
        if isinstance(entry, str):
            return entry
        if entry["proc"].poll() is not None:
            return (f"process {args.get('id')} had already exited "
                    f"(code {entry['proc'].returncode}).")
        _kill_tree(entry["proc"])
        return f"OK: stopped process {args.get('id')} ({entry['command']})."


# --------------------------------------------------------------------------- #
#  Interaction tool — lets GT clarify requirements like Claude Code does
# --------------------------------------------------------------------------- #

class AskUser(Tool):
    name = "ask_user"
    description = ("Ask the user ONE short question and get their typed answer. "
                   "Use it to clarify ambiguous requirements before starting, "
                   "to present a plan and confirm it, or to choose between "
                   "options. Bundle everything you need into it — you get at "
                   "most 3 questions per task. Never ask about details you can "
                   "decide yourself (ports, file names, folder layout). Don't "
                   "use it for permission to run tools — those handle approval "
                   "themselves.")
    args = {"question": "The question to ask (keep it short and concrete)."}

    MAX_PER_TURN = 3

    def run(self, args, ctx):
        question = str(args.get("question", "")).strip()
        if not question:
            return "ERROR: empty question."
        if not ctx.ask:
            return "ERROR: interactive input is not available right now."
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
#  Memory tool
# --------------------------------------------------------------------------- #

class Recall(Tool):
    name = "recall"
    description = ("Search long-term memory (notes, lessons, indexed docs) for "
                   "anything relevant to a query.")
    args = {"query": "What to look up."}

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
            r = requests.get(url, headers={"User-Agent": _USER_AGENT}, timeout=20)
            r.raise_for_status()
        except Exception as e:
            return f"ERROR fetching {url}: {e}"

        ctype = r.headers.get("Content-Type", "")
        if "html" not in ctype and "xml" not in ctype:
            return r.text[:15000]  # already plain text / json / code

        soup = BeautifulSoup(r.text, "html.parser")
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
    Recall(), AskUser(), WebSearch(), WebFetch(),
] + OFFICE_TOOLS
REGISTRY = {t.name: t for t in ALL_TOOLS}

# Tools that reach the internet — filtered out when web.enabled is false.
_WEB_TOOLS = {"web_search", "web_fetch"}


def active_tools(config) -> list:
    """Return the tools available given config (drops web tools if disabled)."""
    web_on = getattr(config, "web", {}).get("enabled", True)
    return [t for t in ALL_TOOLS if web_on or t.name not in _WEB_TOOLS]


def tool_docs(tools=None) -> str:
    """Render a tool list for the system prompt (defaults to all tools)."""
    lines = []
    for t in (tools if tools is not None else ALL_TOOLS):
        arg_str = ", ".join(f'"{k}": <{v}>' for k, v in t.args.items())
        flag = "  (asks for approval)" if t.changes_system else ""
        lines.append(f'- {t.name}: {t.description}{flag}\n    args: {{{arg_str}}}')
    return "\n".join(lines)
