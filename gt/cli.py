"""GT-Code interactive terminal.

Run with:  python -m gt   (or start.bat on Windows)
"""

import io
import sys
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
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
from .profile import Profiler
from .theme import PURPLE
from . import wizard, machine, banner

# The interactive prompt, in GT purple (prompt_toolkit wants a lowercase hex).
_PROMPT = HTML(f'<b><style fg="{PURPLE.lower()}">gt› </style></b>')

# The 1B the /turbo speed profile swaps the resident model to.
TURBO_MODEL = "llama3.2:1b"

HELP = """\
[bold]Commands[/bold]
  /help              show this help
  /setup             re-run first-launch setup (evaluate machine, download models)
  /doctor            show this machine's hardware + which models are live
  /models            list the model ids Ollama is actually serving
  /mode [chat|code|plan|auto]  force GT's behaviour: chat = only talk, code =
                     always build, plan = plan first, auto = decide per message
  /model <role|off>  pin a model role (brain/fast/tiny) or 'off' to auto-route
  /turbo [on|off]    swap the resident model to a 1B for maximum speed (revert
                     with /turbo off) — great on a slow/CPU-only machine
  /benchmark         time a standard set of turns on THIS machine (shareable)
  /profile [clear|update]  show what GT has learned about your preferences over
                     time (glass-box); 'update' relearns now, 'clear' wipes it
  /todos             show GT's current task checklist (its plan for a build)
  /compact           squeeze the conversation into a short session summary now
                     (GT also does this automatically when history fills up)
  /route             toggle smart auto-routing on/off
  /think             toggle deep thinking (Qwen3 reasoning mode; off = snappy)
  /temp [code [chat]] show or set sampling temperature (code vs conversation)
  /skills            list playbooks GT injects per request; '/skills import
                     <path|git-url>' bulk-imports an Agent-Skills library
  /auto              toggle auto-approve (skip y/n prompts for writes & commands)
  /permissions       show granted permissions; '/permissions clear' revokes all
  /cd <path>         change the workspace directory GT operates in
  /init              explore this project and write its GT.md (project memory)
  /index <path>      embed a file or folder into memory for RAG
  /remember <text>   save a note to long-term memory
  /lessons           show lessons GT has learned about itself
  /memory [reload]   memory stats + the GT.md files loaded into context
  /forget <kind|all> clear memory (kinds: note, lesson, doc)
  /reset  /clear     clear the current conversation history
  /quit  /exit       leave

Start a line with [bold]#[/bold] to jot a project note ("# always use pytest") —
it lands in this project's GT.md, which GT reads on every turn.

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

        # Learns the user's preferences from each session (periodically, never
        # per-turn) using the analyst model — see profile.py.
        self.profiler = Profiler(self.llm, self.config,
                                 self.config.data_dir / "profile.json")

    # ---- interaction callbacks handed to the agent/tools ---------------------

    def _ask_user(self, question):
        """Backs the ask_user tool — GT asks, the user types an answer."""
        self.console.print(Panel(Text(question), title="GT asks",
                                 border_style=PURPLE, expand=False))
        try:
            return self.session.prompt("  your answer › ").strip()
        except (EOFError, KeyboardInterrupt):
            return ""

    # ---- main loop ----------------------------------------------------------

    def run(self):
        from . import __version__
        banner.render(self.console, __version__)
        self.console.print(f"\n[dim]workspace:[/dim] {self.agent.cwd}")
        self._show_project_memory_line()
        wizard.ensure(self.config, self.llm, self.console, self.session.prompt)
        self._startup_check()
        if self.router.prefer_fast:
            gpu = (self.router.slow_hw or {}).get("gpu") or "no GPU"
            self.console.print(f"[dim]· CPU-only machine ({gpu}) — everyday turns "
                               f"run on the 3B; heavy builds escalate to the 8B, "
                               f"not the 14B (/model brain forces it)[/dim]")
        if self.config.data.get("profile", {}).get("enabled", True):
            self.agent.profile_summary = self.profiler.summary()
        self._warmup(self.config.router.get("default", "tiny"))
        self.console.print(f"\n[dim]Try:[/dim] [{PURPLE}]what can you do?[/{PURPLE}]"
                           f"   [dim]·[/dim]   [{PURPLE}]build me a todo app[/{PURPLE}]"
                           f"   [dim]·[/dim]   [{PURPLE}]/benchmark[/{PURPLE}]")
        self.console.print("[dim]/help for commands · /quit to exit · just "
                           "type to talk or build[/dim]\n")
        while True:
            try:
                text = self.session.prompt(self._prompt_str()).strip()
            except EOFError:                 # Ctrl-D: leave, but learn first
                self._finish()
                self.console.print("bye")
                return
            except KeyboardInterrupt:        # Ctrl-C at the prompt: just leave
                self.console.print("\nbye")
                return
            if not text:
                continue
            if text.startswith("#"):
                self._note(text.lstrip("#").strip())
                continue
            if text.startswith("/"):
                if self._command(text):
                    self._finish()
                    self.console.print("bye")
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
            self._maybe_periodic_profile()

    # ---- slash commands -----------------------------------------------------

    def _command(self, text):
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd in ("/quit", "/exit"):
            return True   # run() runs the exit hook, then prints bye
        elif cmd == "/help":
            self.console.print(HELP)
        elif cmd == "/models":
            self._show_models()
        elif cmd == "/model":
            self._set_model(arg)
        elif cmd == "/mode":
            self._mode(arg)
        elif cmd == "/turbo":
            self._turbo(arg)
        elif cmd == "/benchmark":
            self._benchmark()
        elif cmd == "/profile":
            self._profile(arg)
        elif cmd == "/todos":
            self._todos_cmd()
        elif cmd == "/compact":
            self._compact()
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
            self._skills(arg)
        elif cmd == "/setup":
            wizard.ensure(self.config, self.llm, self.console,
                          self.session.prompt, force=True)
            self._startup_check()
        elif cmd == "/doctor":
            self._doctor()
        elif cmd == "/cd":
            self._change_dir(arg)
        elif cmd == "/init":
            self._init_project()
        elif cmd == "/index":
            self._index(arg)
        elif cmd == "/remember":
            self._remember(arg)
        elif cmd == "/lessons":
            self._show_kind("lesson", "Learned lessons")
        elif cmd == "/memory":
            self._memory_cmd(arg)
        elif cmd == "/forget":
            self._forget(arg)
        elif cmd in ("/reset", "/clear"):
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
                           f"background — the first load takes ~30s on a CPU "
                           f"box, then it stays hot all session (and, with "
                           f"keep_alive, across launches)[/dim]")
        # Prewarm with the REAL system prompt so the first turn reuses the KV
        # cache instead of paying the whole system-prompt prefill.
        self.agent.prewarm(role)

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

    def _skills(self, arg):
        parts = arg.split(maxsplit=1)
        sub = parts[0].lower() if parts else ""
        if sub == "import":
            self._skills_import(parts[1].strip() if len(parts) > 1 else "")
        elif sub in ("reindex", "index"):
            n = self.agent.reload_skills()
            self.console.print(f"[green]reindexing {n} skills…[/green] "
                               "[dim](ranking runs in the background)[/dim]")
        else:
            self._show_skills()

    def _show_skills(self):
        skills = self.agent.skills
        if not skills:
            self.console.print("[dim]no skills loaded (skills/ folder missing "
                               "or skills.enabled: false).[/dim]")
            return
        # Library skills carry a category; the hand-written core ones don't.
        bundled = [s for s in skills if not s.category]
        library = [s for s in skills if s.category]
        idx = self.agent.skill_index
        state = ("ready" if idx and idx.ready else
                 "building…" if idx else "keyword-only")
        self.console.print(
            f"[bold]{len(skills)} playbooks[/bold] — {len(bundled)} core, "
            f"{len(library)} in the library  ·  semantic index: [cyan]{state}[/cyan]")

        table = Table(title="Core playbooks")
        table.add_column("skill", style="cyan")
        table.add_column("~words")
        table.add_column("triggers", style="dim")
        for s in sorted(bundled, key=lambda s: s.name):
            table.add_row(s.name, str(s.words), ", ".join(s.triggers[:8])
                          + ("…" if len(s.triggers) > 8 else ""))
        self.console.print(table)
        if library:
            cats = {}
            for s in library:
                cats[s.category or "?"] = cats.get(s.category or "?", 0) + 1
            summary = ", ".join(f"{k} ({v})" for k, v in
                                sorted(cats.items(), key=lambda x: -x[1]))
            self.console.print(f"[dim]imported library: {summary}[/dim]")
        self.console.print("[dim]Add your own: a .md file with name/triggers/"
                           "priority front matter in ~/.gt/skills/. Bulk import: "
                           "/skills import <path|git-url>[/dim]")

    def _skills_import(self, src):
        if not src:
            self.console.print("[yellow]usage: /skills import <folder-path or "
                               "git-url>   — a tree of Agent-Skills SKILL.md "
                               "files you control or trust (e.g. /skills import "
                               "./my-skills). GT bundles none itself.[/yellow]")
            return
        from .skills import LIBRARY_DIR
        from . import skill_import
        try:
            with self.console.status("[cyan]importing skills…[/cyan]"):
                count, cats, label = skill_import.import_library(src, LIBRARY_DIR)
        except Exception as e:
            self.console.print(f"[red]import failed:[/red] {e}")
            return
        top = ", ".join(f"{k} ({v})" for k, v in
                        sorted(cats.items(), key=lambda x: -x[1])[:6])
        self.console.print(f"[green]imported {count} skills[/green] from {label} "
                           f"[dim]→ {LIBRARY_DIR}[/dim]\n[dim]{top}[/dim]")
        n = self.agent.reload_skills()
        self.console.print(f"[dim]· {n} playbooks now loaded; embedding them for "
                           f"semantic search in the background…[/dim]")

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
        for role in ("brain", "fast", "tiny", "reviewer", "analyst", "embed"):
            try:
                spec = self.config.model_for(role)
                self.console.print(f"  {role:<9} {spec['model']}  "
                                   f"[dim]({spec['provider']})[/dim]")
            except KeyError:
                self.console.print(f"  {role:<9} [red]unconfigured[/red]")
        if self.router.prefer_fast:
            self.console.print("[bold]Routing[/bold]  3B-first, CPU-only — "
                               "everyday turns stay on the 3B; substantial "
                               "builds escalate to the 8B (the 14B is skipped "
                               "here). [dim]/model brain forces the 14B; set "
                               "router.prefer_fast_on_slow: false to allow "
                               "it[/dim]")
        else:
            self.console.print("[bold]Routing[/bold]  3B-first — the resident "
                               "3B answers everyday turns; 8B for substantial "
                               "coding, 14B for architecture/full builds")
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

    def _todos_cmd(self):
        """/todos — show GT's current task checklist (glass-box)."""
        from .tools import render_todos
        if not self.agent.todos:
            self.console.print("[dim]no active checklist — GT writes one when it "
                               "takes on a multi-step task.[/dim]")
            return
        done = sum(1 for t in self.agent.todos if t["status"] == "done")
        self.console.print(f"[bold {PURPLE}]Task checklist[/bold {PURPLE}] "
                           f"[dim]({done}/{len(self.agent.todos)} done)[/dim]")
        self.console.print(render_todos(self.agent.todos))

    def _compact(self):
        """/compact — fold the conversation into the rolling session summary.

        Also shows the current summary when there is nothing new to fold, so
        it doubles as the glass-box view of what auto-compaction has kept.
        """
        ag = self.agent
        if not ag.history:
            if ag.session_summary:
                self._show_summary(ag.session_summary)
            else:
                self.console.print("[dim]nothing to compact yet — the "
                                   "conversation is empty.[/dim]")
            return
        n = max(1, len(ag.history) // 2)
        self.console.print(f"[dim]· compacting {n} exchange(s) on the "
                           f"resident model…[/dim]")
        try:
            summary = ag.compact_now()
        except KeyboardInterrupt:
            self.console.print("[dim]· skipped — conversation left "
                               "untouched.[/dim]")
            return
        if not summary:
            self.console.print("[yellow]couldn't summarize (is Ollama "
                               "running?) — conversation left "
                               "untouched.[/yellow]")
            return
        self._show_summary(summary)

    def _show_summary(self, summary):
        self.console.print(f"[bold {PURPLE}]Session summary[/bold {PURPLE}] "
                           f"[dim](rides in context every turn; /reset "
                           f"clears it)[/dim]")
        self.console.print(summary)

    def _prompt_str(self):
        """The gt› prompt, tagged with the mode when it isn't auto (gt[code]›)."""
        m = self.agent.mode
        tag = "" if m == "auto" else f"[{m}]"
        return HTML(f'<b><style fg="{PURPLE.lower()}">gt{tag}› </style></b>')

    def _mode(self, arg):
        """/mode [auto|chat|code|plan] — force GT's behaviour, or show it."""
        if not arg:
            self.console.print(
                f"mode: [bold {PURPLE}]{self.agent.mode}[/bold {PURPLE}]   "
                f"[dim](auto · chat · code · plan)[/dim]\n"
                f"[dim]auto = GT decides per message  ·  chat = only talk, never "
                f"builds  ·  code = always build (full tools, keeps going)  ·  "
                f"plan = propose a plan first and wait for your go-ahead. "
                f"Set with /mode <name>.[/dim]")
            return
        m = self.agent.set_mode(arg)
        if not m:
            self.console.print("[yellow]unknown mode. try: auto, chat, code, "
                               "plan[/yellow]")
            return
        blurb = {
            "auto": "GT decides per message.",
            "chat": "conversation only — GT will not build.",
            "code": "always coding — full tools, builds without asking to start.",
            "plan": "planning — GT proposes a plan and waits for your go-ahead.",
        }[m]
        self.console.print(f"mode → [bold {PURPLE}]{m}[/bold {PURPLE}]  "
                           f"[dim]{blurb}[/dim]")
        if m in ("code", "plan"):   # pre-load the model this mode will use
            self._warmup(self.agent._forced_role("fast" if m == "code" else "brain"))

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

    def _turbo(self, arg):
        """Swap the resident (tiny) model to a 1B for maximum speed, or back.

        On a slow/CPU-only box the 3B's load + prefill dominate; a 1B loads and
        runs noticeably faster at some cost to quality. Session-only — builds
        still escalate to the 8B/14B, and /turbo off restores the 3B.
        """
        tiny = self.config.models.get("tiny")
        if not tiny:
            self.console.print("[yellow]no 'tiny' role configured.[/yellow]")
            return
        arg = arg.lower().strip()
        on = getattr(self, "_turbo_on", False)

        if arg in ("off", "0", "no", "false"):
            if not on:
                self.console.print("[dim]turbo is already off.[/dim]")
                return
            tiny["model"] = self._turbo_saved
            self._turbo_on = False
            self.console.print(f"turbo [bold]off[/bold] — resident model back to "
                               f"[bold {PURPLE}]{tiny['model']}[/bold {PURPLE}].")
            self._warmup("tiny")
            return

        if on:
            self.console.print(f"[dim]turbo already on ({tiny['model']}). "
                               f"/turbo off to revert.[/dim]")
            return
        try:
            served = self.llm.list_models(self.config.provider_base("ollama"))
        except LLMError:
            served = []
        if not any("llama3.2" in s and "1b" in s.lower() for s in served):
            self.console.print(
                f"[yellow]{TURBO_MODEL} isn't pulled yet.[/yellow] It's a "
                f"~1.3 GB download that loads and runs faster than the 3B on a "
                f"CPU box.\n  Pull it:  [bold]ollama pull {TURBO_MODEL}[/bold]"
                f"   then run /turbo again.")
            return
        self._turbo_saved = tiny["model"]
        tiny["model"] = TURBO_MODEL
        self._turbo_on = True
        self.console.print(f"turbo [bold]on[/bold] — resident model is now "
                           f"[bold {PURPLE}]{TURBO_MODEL}[/bold {PURPLE}] "
                           f"(faster, a little less capable; builds still "
                           f"escalate to the 8B/14B). [dim]/turbo off to "
                           f"revert.[/dim]")
        self._warmup("tiny")

    def _benchmark(self):
        """Time a standard set of turns on THIS machine so a user can see (and
        share) how GT performs on their hardware."""
        import time
        seq = [("hi", "chat"),
               ("what can you do?", "chat"),
               ("can you browse the internet?", "chat"),
               ("list the files in this folder", "work"),
               ("read the file config.yaml", "work")]
        self.console.print(
            f"[bold {PURPLE}]Benchmark[/bold {PURPLE}] — timing {len(seq)} turns "
            f"[dim](the first turn includes any one-time model load)[/dim]")

        # Run against the real model but swallow the answers, and keep the
        # user's actual conversation untouched.
        real_console = self.agent.console
        saved = (list(self.agent.history), set(self.agent._seen_skills),
                 set(self.agent._seen_memory), self.agent.session_summary)
        self.agent.console = Console(file=io.StringIO())
        rows = []
        try:
            for msg, kind in seq:
                t0 = time.perf_counter()
                try:
                    self.agent.run(msg)
                    wall = time.perf_counter() - t0
                    rows.append((msg, kind, wall, dict(self.llm.last_metrics or {})))
                except Exception as e:
                    rows.append((msg, kind, None, str(e)[:40]))
        finally:
            self.agent.console = real_console
            (self.agent.history, self.agent._seen_skills,
             self.agent._seen_memory, self.agent.session_summary) = saved

        table = Table(title="per-turn timing")
        table.add_column("turn", style=PURPLE)
        table.add_column("kind", style="dim")
        table.add_column("load_s", justify="right")
        table.add_column("prompt_tok", justify="right")
        table.add_column("tok/s", justify="right")
        table.add_column("total_s", justify="right")
        for msg, kind, wall, m in rows:
            if wall is None:
                table.add_row(msg[:28], kind, "-", "-", "-", f"[red]{m}[/red]")
                continue
            table.add_row(msg[:28], kind, f"{m.get('load_s', 0):.1f}",
                          str(m.get("prompt_tokens", 0)),
                          f"{m.get('tps', 0):.0f}", f"{wall:.1f}")
        self.console.print(table)
        warm = [r[2] for r in rows if r[2] is not None][1:]  # exclude cold turn 1
        if warm:
            self.console.print(f"[dim]after warm-up: avg {sum(warm)/len(warm):.1f}s"
                               f" · slowest {max(warm):.1f}s per turn[/dim]")

    # ---- preference profile (learned by the analyst model) -----------------

    def _profile(self, arg):
        """/profile — show what GT has learned; 'clear' wipes it, 'update' runs
        the analyst on the session so far."""
        arg = arg.lower().strip()
        if arg == "clear":
            self.profiler.clear()
            self.agent.profile_summary = ""
            self.console.print("profile cleared — GT will relearn from scratch.")
            return
        if arg == "update":
            self._run_profile_analysis(manual=True)
            return
        summary = self.profiler.summary()
        if not summary:
            m = self.profiler.model_id()
            avail = self.profiler.available()
            self.console.print(
                "[dim]No preferences learned yet.[/dim] GT builds a short profile "
                "of your habits when you exit (or run [bold]/profile update[/bold])."
                + ("" if avail else
                   f"\n[yellow]Enable it:[/yellow] [bold]ollama pull {m}[/bold]")
                + f"\n[dim]Glass-box — it's plain JSON at {self.profiler.path}.[/dim]")
            return
        self.console.print(f"[bold {PURPLE}]What GT has learned about you[/bold "
                           f"{PURPLE}]")
        self.console.print(summary)
        self.console.print(f"[dim]· {self.profiler.path}  ·  /profile update to "
                           f"refresh now  ·  /profile clear to wipe[/dim]")

    def _run_profile_analysis(self, manual=False):
        pcfg = self.config.data.get("profile", {})
        if not pcfg.get("enabled", True):
            return
        log = self.agent.session_log
        if not log:
            if manual:
                self.console.print("[dim]nothing to analyze yet — ask GT a few "
                                   "things first.[/dim]")
            return
        if not manual and len(log) < int(pcfg.get("min_turns", 4)):
            return
        if not self.profiler.available():
            if manual:
                m = self.profiler.model_id()
                self.console.print(f"[yellow]analyst model '{m}' isn't pulled.[/"
                                   f"yellow] Run: [bold]ollama pull {m}[/bold]")
            return
        self.console.print(f"[dim]· learning from this session ({len(log)} turns) "
                           f"with {self.profiler.model_id()} — one moment "
                           f"(Ctrl-C to skip)…[/dim]")
        try:
            _, msg = self.profiler.update(log)
            self.agent.profile_summary = self.profiler.summary()
            self.console.print(f"[dim]· {msg}[/dim]")
        except KeyboardInterrupt:
            self.console.print("[dim]· skipped.[/dim]")

    def _maybe_periodic_profile(self):
        """Optionally analyse every N turns (config profile.every_turns > 0).
        Off by default — mid-session it costs a model load on a one-model box."""
        pcfg = self.config.data.get("profile", {})
        n = int(pcfg.get("every_turns", 0) or 0)
        log = self.agent.session_log
        if pcfg.get("enabled", True) and n > 0 and log and len(log) % n == 0:
            self._run_profile_analysis(manual=False)

    def _finish(self):
        """Exit hook — learn from the session before leaving (config on_quit)."""
        pcfg = self.config.data.get("profile", {})
        if pcfg.get("enabled", True) and pcfg.get("on_quit", True):
            try:
                self._run_profile_analysis(manual=False)
            except Exception:
                pass   # never let profiling block a clean exit

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
        # A new workspace means a different project — pick up ITS GT.md.
        self.agent.reload_project_memory()
        self._show_project_memory_line()

    # ---- project memory (GT.md — see project_memory.py) ---------------------

    @staticmethod
    def _fmt_path(p):
        try:
            return "~/" + str(Path(p).relative_to(Path.home()))
        except ValueError:
            return str(p)

    def _show_project_memory_line(self):
        files = self.agent.project_memory_files
        if files:
            names = ", ".join(self._fmt_path(p) for p in files)
            self.console.print(f"[dim]· project memory: {names} "
                               f"({len(self.agent.project_memory)} chars in "
                               f"context every work turn)[/dim]")

    def _note(self, note):
        """A '# …' line at the prompt — jot a standing note into GT.md."""
        if not note:
            self.console.print("[yellow]usage: # <note>   e.g. "
                               "# always use pytest, never unittest[/yellow]")
            return
        from .project_memory import append_note
        try:
            target = append_note(self.agent.cwd, note)
        except OSError as e:
            self.console.print(f"[red]couldn't write the note: {e}[/red]")
            return
        self.agent.reload_project_memory()
        self.console.print(f"[green]noted[/green] → {self._fmt_path(target)} "
                           f"[dim](GT reads it every work turn)[/dim]")

    def _init_project(self):
        """/init — GT explores the workspace and writes its GT.md brief."""
        from .project_memory import find_project_file
        found = find_project_file(self.agent.cwd)
        task = (
            "Write the GT.md project brief for this workspace. First explore: "
            "list the files, and read the README and the package manifests you "
            "find (package.json, pyproject.toml, requirements.txt, go.mod, "
            "Cargo.toml…) plus one or two central source files. Then write "
            "GT.md at the workspace root covering, briefly: what the project "
            "is; the stack and key dependencies; how to run it and how to run "
            "the tests (exact commands); the layout (what lives where); and "
            "any conventions to follow when changing code. Keep it under 60 "
            "lines of plain markdown — it is loaded into your context on "
            "every future turn, so concise beats complete.")
        if found is not None and found.name == "GT.md":
            task += (" A GT.md already exists — read it first and improve it "
                     "in place, keeping anything that is still true.")
        elif found is not None:
            task += (f" Note: the project has a {found.name} written for "
                     f"another agent — read it and fold what still holds "
                     f"into GT.md.")
        self.agent.run(task)
        self.agent.reload_project_memory()
        self._show_project_memory_line()

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

    def _memory_cmd(self, arg):
        """/memory — vector-memory stats + the GT.md layers in context.
        '/memory reload' re-reads the GT.md files after an outside edit."""
        if arg.strip().lower() == "reload":
            self.agent.reload_project_memory()
            if self.agent.project_memory_files:
                self._show_project_memory_line()
            else:
                self.console.print("[dim]no GT.md found for this workspace — "
                                   "start one with /init or a '# note'.[/dim]")
            return
        self.console.print(
            f"notes: {self.memory.count('note')}   "
            f"lessons: {self.memory.count('lesson')}   "
            f"doc-chunks: {self.memory.count('doc')}   "
            f"total: {self.memory.count()}"
        )
        files = self.agent.project_memory_files
        if files:
            self.console.print("[bold]Project memory (in context every work "
                               "turn)[/bold]")
            for p in files:
                try:
                    size = p.stat().st_size
                except OSError:
                    size = 0
                self.console.print(f"  • {self._fmt_path(p)}  [dim]({size} "
                                   f"bytes)[/dim]")
            self.console.print("[dim]edit the file(s) any time, then /memory "
                               "reload · add a note fast: # <note>[/dim]")
        else:
            self.console.print("[dim]no GT.md project memory for this "
                               "workspace — /init writes one, or jot a note "
                               "with '# <note>'.[/dim]")

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
