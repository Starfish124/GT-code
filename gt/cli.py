"""GT-Code interactive terminal.

Run with:  python -m gt   (or start.bat on Windows)
"""

import sys
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .config import Config
from .llm import LLM, LLMError
from .memory import Memory, chunk_text
from .router import Router
from .improve import Improver
from .agent import Agent

BANNER = r"""
  ___ _____    ___         _
 / __|_   _|__/ __|___  __| |___
| (_ | | |___| (__/ _ \/ _` / -_)
 \___| |_|    \___\___/\__,_\___|   local coding agent
"""

HELP = """\
[bold]Commands[/bold]
  /help              show this help
  /models            list model ids your Ollama + LM Studio actually serve
  /model <role|off>  pin a model role (brain/fast/tiny) or 'off' to auto-route
  /route             toggle smart auto-routing on/off
  /auto              toggle auto-approve (skip y/n prompts for writes & commands)
  /cd <path>         change the workspace directory GT operates in
  /index <path>      embed a file or folder into memory for RAG
  /remember <text>   save a note to long-term memory
  /lessons           show lessons GT has learned about itself
  /memory            memory stats
  /forget <kind|all> clear memory (kinds: note, lesson, doc)
  /reset             clear the current conversation history
  /quit  /exit       leave

Anything else is a request for GT. It will route to a model, use tools
(reading/writing files, running commands), and remember what it learns.
"""


class GTShell:
    def __init__(self):
        self.console = Console()
        self.config = Config.load()
        self.llm = LLM(self.config)
        self.memory = Memory(self.llm, self.config.data_dir / "memory.db")
        self.router = Router(self.llm, self.config, self.console)
        self.improver = Improver(self.llm, self.memory)
        self.auto_approve = bool(self.config.agent.get("auto_approve", False))

        self.agent = Agent(
            llm=self.llm, config=self.config, memory=self.memory,
            router=self.router, console=self.console,
            improver=self.improver, approve=self._approve,
        )

        hist = self.config.data_dir / "history.txt"
        hist.parent.mkdir(parents=True, exist_ok=True)
        self.session = PromptSession(history=FileHistory(str(hist)))

    # ---- approval callback handed to the agent/tools ------------------------

    def _approve(self, title, detail=""):
        if self.auto_approve:
            self.console.print(f"[dim]auto-approved: {title}[/dim]")
            return True
        self.console.print(Panel(Text(detail or "(no details)"),
                                 title=f"Approve? {title}",
                                 border_style="yellow", expand=False))
        try:
            ans = self.session.prompt("  allow? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        return ans in ("y", "yes")

    # ---- main loop ----------------------------------------------------------

    def run(self):
        self.console.print(f"[bold cyan]{BANNER}[/bold cyan]")
        self._startup_check()
        self.console.print("Type [bold]/help[/bold] for commands, "
                           "[bold]/quit[/bold] to exit.\n")
        while True:
            try:
                text = self.session.prompt("gt› ").strip()
            except (EOFError, KeyboardInterrupt):
                self.console.print("\nbye 👋")
                return
            if not text:
                continue
            if text.startswith("/"):
                if self._command(text):
                    return  # quit
                continue
            try:
                # The agent streams its own answer as live markdown; nothing
                # to re-print here.
                self.agent.run(text)
            except LLMError as e:
                self.console.print(f"[red]{e}[/red]")
            except KeyboardInterrupt:
                self.console.print("\n[yellow](interrupted)[/yellow]")

    # ---- slash commands -----------------------------------------------------

    def _command(self, text):
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd in ("/quit", "/exit"):
            self.console.print("bye 👋")
            return True
        elif cmd == "/help":
            self.console.print(HELP)
        elif cmd == "/models":
            self._show_models()
        elif cmd == "/model":
            self._set_model(arg)
        elif cmd == "/route":
            self.router.enabled = not self.router.enabled
            self.console.print(f"auto-routing: [bold]{'on' if self.router.enabled else 'off'}[/bold]")
        elif cmd == "/auto":
            self.auto_approve = not self.auto_approve
            self.console.print(f"auto-approve: [bold]{'on' if self.auto_approve else 'off'}[/bold]"
                               + ("  [yellow](GT will write files & run commands without asking)[/yellow]"
                                  if self.auto_approve else ""))
        elif cmd == "/cd":
            self._change_dir(arg)
        elif cmd == "/index":
            self._index(arg)
        elif cmd == "/remember":
            self._remember(arg)
        elif cmd == "/lessons":
            self._show_kind("lesson", "Learned lessons")
        elif cmd == "/memory":
            self._memory_stats()
        elif cmd == "/forget":
            self._forget(arg)
        elif cmd == "/reset":
            self.agent.reset()
            self.console.print("conversation history cleared.")
        else:
            self.console.print(f"[yellow]unknown command {cmd}. try /help[/yellow]")
        return False

    # ---- command implementations -------------------------------------------

    def _startup_check(self):
        """Ping both providers so config problems surface immediately."""
        for name, prov in self.config.providers.items():
            try:
                ids = self.llm.list_models(prov["base_url"])
                self.console.print(f"[green]✓[/green] {name}: {len(ids)} model(s) at {prov['base_url']}")
            except LLMError:
                self.console.print(f"[red]✗[/red] {name}: not reachable at {prov['base_url']} "
                                   f"[dim](start it before using GT)[/dim]")

    def _show_models(self):
        for name, prov in self.config.providers.items():
            table = Table(title=f"{name}  —  {prov['base_url']}")
            table.add_column("model id", style="cyan")
            try:
                for mid in self.llm.list_models(prov["base_url"]):
                    table.add_row(mid)
            except LLMError as e:
                table.add_row(f"[red]{e}[/red]")
            self.console.print(table)
        self.console.print("[dim]Copy the exact ids into config.yaml under models:[/dim]")

    def _set_model(self, arg):
        if arg in ("off", ""):
            self.agent.force_role = None
            self.console.print("model pin cleared — auto-routing decides.")
            return
        if arg not in self.config.models:
            self.console.print(f"[yellow]no role '{arg}'. roles: {', '.join(self.config.models)}[/yellow]")
            return
        self.agent.force_role = arg
        self.console.print(f"pinned to [bold]{arg}[/bold] "
                           f"({self.config.model_for(arg)['model']})")

    def _change_dir(self, arg):
        if not arg:
            self.console.print(f"workspace: {self.agent.cwd}")
            return
        p = Path(arg).expanduser()
        if not p.is_absolute():
            p = self.agent.cwd / p
        if not p.is_dir():
            self.console.print(f"[red]not a directory: {p}[/red]")
            return
        self.agent.cwd = p.resolve()
        self.console.print(f"workspace → {self.agent.cwd}")

    def _index(self, arg):
        if not arg:
            self.console.print("[yellow]usage: /index <file-or-folder>[/yellow]")
            return
        root = Path(arg).expanduser()
        if not root.is_absolute():
            root = self.agent.cwd / root
        if not root.exists():
            self.console.print(f"[red]not found: {root}[/red]")
            return

        exts = {".py", ".js", ".ts", ".tsx", ".jsx", ".md", ".txt", ".json",
                ".yaml", ".yml", ".html", ".css", ".go", ".rs", ".java", ".c",
                ".cpp", ".h", ".sh"}
        files = [root] if root.is_file() else [
            f for f in root.rglob("*")
            if f.is_file() and f.suffix.lower() in exts
            and not any(p in {".git", "node_modules", ".venv", "__pycache__", "data"}
                        for p in f.parts)
        ]
        if not files:
            self.console.print("[yellow]no indexable text files found.[/yellow]")
            return

        total = 0
        with self.console.status("[cyan]embedding…[/cyan]") as _:
            for f in files:
                try:
                    text = f.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue
                items = [(c, "doc", str(f)) for c in chunk_text(text)]
                try:
                    total += self.memory.add_many(items)
                except LLMError as e:
                    self.console.print(f"[red]embedding failed: {e}[/red]")
                    return
        self.console.print(f"[green]indexed {total} chunks from {len(files)} file(s).[/green]")

    def _remember(self, arg):
        if not arg:
            self.console.print("[yellow]usage: /remember <text>[/yellow]")
            return
        try:
            self.memory.add(arg, kind="note", source="user")
            self.console.print("[green]remembered.[/green]")
        except LLMError as e:
            self.console.print(f"[red]{e}[/red]")

    def _show_kind(self, kind, title):
        rows = self.memory.recent(kind=kind, limit=30)
        if not rows:
            self.console.print(f"[dim]no {kind}s yet.[/dim]")
            return
        self.console.print(f"[bold]{title}[/bold]")
        for text, _ in rows:
            self.console.print(f"  • {text}")

    def _memory_stats(self):
        self.console.print(
            f"notes: {self.memory.count('note')}   "
            f"lessons: {self.memory.count('lesson')}   "
            f"doc-chunks: {self.memory.count('doc')}   "
            f"total: {self.memory.count()}"
        )

    def _forget(self, arg):
        arg = arg.lower().strip()
        if arg == "all":
            self.memory.clear()
            self.console.print("cleared ALL memory.")
        elif arg in ("note", "lesson", "doc"):
            self.memory.clear(kind=arg)
            self.console.print(f"cleared all {arg}s.")
        else:
            self.console.print("[yellow]usage: /forget <note|lesson|doc|all>[/yellow]")


def main():
    try:
        shell = GTShell()
    except FileNotFoundError as e:
        print(f"Config error: {e}", file=sys.stderr)
        sys.exit(1)
    shell.run()


if __name__ == "__main__":
    main()
