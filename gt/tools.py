"""The agent's tools + a tiny execution context.

Every tool declares a name, a description, and its args so the system prompt can
teach the model how to call it. Tools that change the machine (write/edit/run)
go through ctx.approve() so the user stays in control unless auto-approve is on.
"""

import subprocess
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
#  Shell tool
# --------------------------------------------------------------------------- #

class RunCommand(Tool):
    name = "run_command"
    description = ("Run a shell command in the workspace and return its output. "
                   "On Windows this is cmd; on macOS/Linux it's the default shell.")
    args = {"command": "The command line to execute."}
    changes_system = True

    def run(self, args, ctx):
        command = args.get("command", "").strip()
        if not command:
            return "ERROR: empty command."
        from .permissions import command_key
        if not ctx.approve("Run command", command, key=command_key(command)):
            return "DENIED: user declined to run the command."
        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=str(ctx.cwd),
                capture_output=True,
                text=True,
                timeout=180,
            )
        except subprocess.TimeoutExpired:
            return "ERROR: command timed out after 180s."
        except Exception as e:
            return f"ERROR launching command: {e}"
        out = (proc.stdout or "")[-6000:]
        err = (proc.stderr or "")[-3000:]
        parts = [f"exit code: {proc.returncode}"]
        if out:
            parts.append(f"stdout:\n{out}")
        if err:
            parts.append(f"stderr:\n{err}")
        return "\n".join(parts)


# --------------------------------------------------------------------------- #
#  Interaction tool — lets GT clarify requirements like Claude Code does
# --------------------------------------------------------------------------- #

class AskUser(Tool):
    name = "ask_user"
    description = ("Ask the user ONE short question and get their typed answer. "
                   "Use it to clarify ambiguous requirements before starting, "
                   "to present a plan and confirm it, or to choose between "
                   "options. Don't use it for permission to run tools — those "
                   "handle approval themselves.")
    args = {"question": "The question to ask (keep it short and concrete)."}

    def run(self, args, ctx):
        question = str(args.get("question", "")).strip()
        if not question:
            return "ERROR: empty question."
        if not ctx.ask:
            return "ERROR: interactive input is not available right now."
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
    SearchFiles(), RunCommand(), Recall(), AskUser(),
    WebSearch(), WebFetch(),
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
