"""GT-Code interactive terminal.

Run with:  python -m gt   (or start.bat on Windows)
"""

import sys
import threading
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
from .permissions import Permissions
from . import wizard, machine

BANNER = r"""
  ___ _____    ___         _
 / __|_   _|__/ __|___  __| |___
| (_ | | |___| (__/ _ \/ _` / -_)
 \___| |_|    \___\___/\__,_\___|
"""

HELP = """\
[bold]Commands[/bold]
  /help              show this help
  /setup             re-run first-launch setup (evaluate machine, download models)
  /doctor            show this machine's hardware + which models are live
  /models            list model ids your Ollama + LM Studio actually serve
  /model <role|off>  pin a model role (brain/fast/tiny) or 'off' to auto-route
  /route             toggle smart auto-routing on/off
  /think             toggle deep thinking (Qwen3 reasoning mode; off = snappy)
  /temp [code [chat]] show or set sampling temperature (code vs conversation)
  /skills            list the expert playbooks GT injects per request
  /auto              toggle auto-approve (skip y/n prompts for writes & commands)
  /permissions       show granted permissions; '/permissions clear' revokes all
  /cd <path>         change the workspace directory GT operates in
  /index <path>      embed a file or folder into memory for RAG
  /remember <text>   save a note to long-term memory
  /lessons           show lessons GT has learned about itself
  /memory            memory stats
  /forget <kind|all> clear memory (kinds: note, lesson, doc)
  /reset             clear the current conversation history
  /quit  /exit       leave

Anything else is talk or work. Ask a question and GT just answers; ask for a
build and it goes straight to it — using tools (files, commands, web,
Excel/PowerPoint/Word) and asking permission before it changes anything.
"""


class GTShell:
    def __init__(self):
        self.console = Console()
        self.config = Config.load()
        self.llm = LLM(self.config)
        self.memory = Memory(self.llm, self.config.data_dir / "memory.db")
        self.router = Router(self.llm, self.config, self.console)
        self.improver = Improver(self.llm, self.memory)

        hist = self.config.data_dir / "history.txt"
        hist.parent.mkdir(parents=True, exist_ok=True)
        self.session = PromptSession(history=FileHistory(str(hist)))

        self.perms = Permissions(
            console=self.console, prompt_fn=self.session.prompt,
            store=self.config.data_dir / "permissions.json",
            auto_approve=bool(self.config.agent.get("auto_approve", False)),
        )

        self.agent = Agent(
            llm=self.llm, config=self.config, memory=self.memory,
            router=self.router, console=self.console,
            improver=self.improver, approve=self.perms.approve,
            ask=self._ask_user,
        )

    # ---- interaction callbacks handed to the agent/tools ---------------------

    def _ask_user(self, question):
        """Backs the ask_user tool — GT asks, the user types an answer."""
        self.console.print(Panel(Text(question), title="GT asks",
                                 border_style="cyan", expand=False))
        try:
            return self.session.prompt("  your answer › ").strip()
        except (EOFError, KeyboardInterrupt):
            return ""

    # ---- main loop ----------------------------------------------------------

    def run(self):
        from . import __version__
        self.console.print(f"[bold cyan]{BANNER}[/bold cyan]", end="")
        self.console.print(f"[dim]  local coding agent · v{__version__}[/dim]\n")
        self.console.print(f"[dim]workspace ›[/dim] {self.agent.cwd}")
        wizard.ensure(self.config, self.llm, self.console, self.session.prompt)
        self._startup_check()
        self._warmup(self.config.router.get("default", "fast"))
        self.console.print("\n[dim]/help for commands · /quit to exit · just "
                           "type to talk or build[/dim]\n")
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
            self.perms.auto_approve = not self.perms.auto_approve
            self.console.print(f"auto-approve: [bold]{'on' if self.perms.auto_approve else 'off'}[/bold]"
                               + ("  [yellow](GT will write files & run commands without asking — "
                                  "dangerous commands still prompt)[/yellow]"
                                  if self.perms.auto_approve else ""))
        elif cmd == "/permissions":
            self._permissions(arg)
        elif cmd == "/think":
            perf = self.config.performance
            perf["thinking"] = not perf.get("thinking", False)
            on = perf["thinking"]
            self.console.print(
                f"deep thinking: [bold]{'on' if on else 'off'}[/bold]  "
                + ("[yellow](slower, more careful — Qwen3 reasons before "
                   "answering)[/yellow]" if on
                   else "[dim](snappy default)[/dim]"))
        elif cmd == "/temp":
            self._temp(arg)
        elif cmd == "/skills":
            self._show_skills()
        elif cmd == "/setup":
            wizard.ensure(self.config, self.llm, self.console,
                          self.session.prompt, force=True)
            self._startup_check()
        elif cmd == "/doctor":
            self._doctor()
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
        """Ping providers and best-effort match config model ids to whatever
        is actually being served (falls back between providers), so GT works
        out of the box on a fresh machine without editing config.yaml."""
        self.config.auto_resolve(self.llm, self.console)

    def _warmup(self, role):
        """Load the model into RAM in the background while the user types
        their first prompt — the first answer then starts instantly instead
        of paying 10-60s of model loading in front of the user."""
        try:
            model = self.config.model_for(role)["model"]
        except KeyError:
            return
        self.console.print(f"[dim]· warming up {role} ({model}) in the "
                           f"background…[/dim]")

        def go():
            try:
                self.llm.chat(role, [{"role": "user", "content": "Reply: OK"}],
                              stream=False, timeout=300)
            except Exception:
                pass  # warmup is best-effort; real calls will surface errors

        threading.Thread(target=go, daemon=True).start()

    def _temp(self, arg):
        perf = self.config.performance
        if not arg:
            self.console.print(
                f"temperature — [bold]code/tools {perf.get('temperature', 0.3):g}[/bold]"
                f"  ·  [bold]conversation {perf.get('temperature_chat', 0.7):g}[/bold]\n"
                f"[dim]softmax sharpness: lower = precise/deterministic, higher = "
                f"varied/natural. GT uses the code value for build & coding turns, "
                f"the conversation value for plain talk. Set: /temp <code> [chat][/dim]")
            return
        try:
            vals = [min(max(float(p), 0.0), 2.0) for p in arg.split()[:2]]
        except ValueError:
            self.console.print("[yellow]usage: /temp <code> [chat]   "
                               "e.g. /temp 0.3 0.7[/yellow]")
            return
        perf["temperature"] = vals[0]
        if len(vals) > 1:
            perf["temperature_chat"] = vals[1]
        self.console.print(
            f"temperature → code/tools [bold]{perf['temperature']:g}[/bold], "
            f"conversation [bold]{perf.get('temperature_chat', perf['temperature']):g}"
            f"[/bold]  [dim](this session)[/dim]")

    def _show_skills(self):
        if not self.agent.skills:
            self.console.print("[dim]no skills loaded (skills/ folder missing "
                               "or skills.enabled: false).[/dim]")
            return
        table = Table(title="Expert playbooks (auto-injected per request)")
        table.add_column("skill", style="cyan")
        table.add_column("~words")
        table.add_column("triggers", style="dim")
        for s in sorted(self.agent.skills, key=lambda s: s.name):
            table.add_row(s.name, str(s.words), ", ".join(s.triggers[:8])
                          + ("…" if len(s.triggers) > 8 else ""))
        self.console.print(table)
        self.console.print("[dim]Add your own: drop a .md file with the same "
                           "front matter into ~/.gt/skills/ (or skills/ in the "
                           "GT-code folder).[/dim]")

    def _permissions(self, arg):
        if arg.strip().lower() == "clear":
            self.perms.clear()
            self.console.print("all granted permissions revoked — GT will ask again.")
            return
        grants = self.perms.list_grants()
        if not grants:
            self.console.print("[dim]no standing permissions — GT asks before "
                               "every write/command. Answer 'a' at a prompt to "
                               "grant one.[/dim]")
            return
        self.console.print("[bold]Always-allowed[/bold] "
                           "([dim]/permissions clear to revoke[/dim])")
        for g in grants:
            self.console.print(f"  • {g}")

    def _doctor(self):
        hw = machine.probe()
        rec = machine.recommend(hw)
        self.console.print(
            f"[bold]Machine[/bold]  {hw['os']} ({hw['arch']})  ·  {hw['cpu']}  "
            f"·  {hw['cores']} cores  ·  {hw['ram_gb']} GB RAM"
            + (f"  ·  GPU: {hw['gpu']}" if hw['gpu'] else "")
            + (f" ({hw['vram_gb']} GB VRAM)" if hw['vram_gb'] else ""))
        self.console.print(f"[bold]Verdict[/bold]  {rec['reason']}")
        self.console.print("[bold]Line-up[/bold]")
        for role in ("brain", "fast", "tiny", "reviewer", "embed"):
            try:
                spec = self.config.model_for(role)
                self.console.print(f"  {role:<9} {spec['model']}  "
                                   f"[dim]({spec['provider']})[/dim]")
            except KeyError:
                self.console.print(f"  {role:<9} [red]unconfigured[/red]")
        self._startup_check()

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
        # Only chat roles are pinnable — pinning 'embed' would route every
        # conversation to an embedding model and break chat outright.
        pinnable = [r for r in ("brain", "fast", "tiny") if r in self.config.models]
        if arg not in pinnable:
            self.console.print(f"[yellow]no pinnable role '{arg}'. "
                               f"roles: {', '.join(pinnable)} (or 'off')[/yellow]")
            return
        self.agent.force_role = arg
        self.console.print(f"pinned to [bold]{arg}[/bold] "
                           f"({self.config.model_for(arg)['model']})")
        self._warmup(arg)  # start loading it now, not at the first prompt

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
        gt_data = str(self.config.data_dir)
        files = [root] if root.is_file() else [
            f for f in root.rglob("*")
            if f.is_file() and f.suffix.lower() in exts
            and not any(p in {".git", "node_modules", ".venv", "__pycache__"}
                        for p in f.parts)
            and not str(f).startswith(gt_data)   # GT's own db/logs, not "data/"
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
    args = sys.argv[1:]
    if args and args[0] in ("--version", "-V"):
        from . import __version__
        print(f"gt-code {__version__}")
        return
    if args and args[0] in ("--help", "-h"):
        print("GT-Code — local CLI coding agent.\n\n"
              "usage: gt [path]\n\n"
              "  gt            start GT in the current directory\n"
              "  gt <path>     start GT with <path> as the workspace\n"
              "  gt --version  print the version\n\n"
              "Inside GT, type /help for commands.")
        return
    if args:
        target = Path(args[0]).expanduser()
        if not target.is_dir():
            print(f"gt: not a directory: {target}", file=sys.stderr)
            sys.exit(1)
        import os
        os.chdir(target)

    try:
        shell = GTShell()
    except FileNotFoundError as e:
        print(f"Config error: {e}", file=sys.stderr)
        sys.exit(1)
    shell.run()


if __name__ == "__main__":
    main()
