"""The agentic tool loop — the heart of GT.

Flow per user turn:
  1. Router picks a model role for this request.
  2. Pull relevant memory/lessons via embeddings.
  3. Loop: call the model (streamed) -> if it emitted a tool call, run it and
     feed the result back -> repeat until it returns a plain-text answer or we
     hit max_steps.
  4. Optionally let the self-improve loop extract a lesson.
"""

import json
import platform
import re
import threading
from pathlib import Path

from rich.text import Text

from .prompts import build_system
from .skills import load_skills, select, skills_block
from .tools import Ctx, active_tools, tool_docs
from .ui import streaming_markdown
from .llm import LLMError


# Matches ```json { ... } ``` (and bare ``` ... ```) fenced blocks.
_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)

_DECODER = json.JSONDecoder()


def _scan_objects(text):
    """Yield every top-level {...} JSON object found anywhere in the text.

    Small models often emit several calls run together ('{...}; {...}') with
    no fence — raw_decode picks each one out of the surrounding noise.
    """
    idx = 0
    while True:
        start = text.find("{", idx)
        if start == -1:
            return
        try:
            obj, consumed = _DECODER.raw_decode(text[start:])
        except ValueError:
            idx = start + 1
            continue
        yield obj
        idx = start + consumed


def extract_tool_call(text):
    """Return one valid {"tool": ...} object from the text, or None.

    Fenced blocks win (last one — models think first, then act). Otherwise
    scan for bare JSON objects and take the FIRST tool call, so a spray of
    several calls executes sequentially, one per agent step.
    """
    for block in reversed(_FENCE.findall(text)):
        try:
            obj = json.loads(block)
        except Exception:
            continue
        if isinstance(obj, dict) and "tool" in obj:
            obj.setdefault("args", {})
            return obj
    for obj in _scan_objects(text):
        if isinstance(obj, dict) and "tool" in obj:
            obj.setdefault("args", {})
            return obj
    return None


class Agent:
    def __init__(self, llm, config, memory, router, console, improver,
                 approve, ask=None):
        self.llm = llm
        self.config = config
        self.memory = memory
        self.router = router
        self.console = console
        self.improver = improver
        self.approve = approve
        self.ask = ask

        self.cwd = Path.cwd()
        self.history = []          # [{role, content}] — user msgs + final answers
        self.force_role = None     # set via /model to pin a model
        self.max_steps = int(config.agent.get("max_steps", 12))

        # Tools available this session (web tools dropped if web.enabled=false).
        self.tools = {t.name: t for t in active_tools(config)}
        self.tool_docs = tool_docs(self.tools.values())

        # Expert playbooks, matched per-request and injected into context.
        skills_cfg = config.data.get("skills", {})
        self.skills = load_skills() if skills_cfg.get("enabled", True) else []
        self.skills_max = int(skills_cfg.get("max", 2))

    # ---- public API ---------------------------------------------------------

    def run(self, user_msg):
        role = self.force_role or self.router.route(user_msg)
        self.console.print(f"[dim]· routed to [bold]{role}[/bold] "
                           f"({self.config.model_for(role)['model']})[/dim]")

        memory_block = self._recall(user_msg)
        picked = select(self.skills, user_msg, limit=self.skills_max)
        if picked:
            self.console.print(
                f"[dim]· playbooks: {', '.join(s.name for s in picked)}[/dim]")
        system = build_system(
            cwd=str(self.cwd),
            os_name=platform.system(),
            tools=self.tool_docs,
            memory_block=memory_block,
            skills_block=skills_block(picked),
        )

        # Working message list for this turn (includes intermediate tool steps).
        messages = [{"role": "system", "content": system}] + self.history + [
            {"role": "user", "content": user_msg}
        ]

        ctx = Ctx(cwd=self.cwd, memory=self.memory,
                  approve=self.approve, config=self.config, ask=self.ask)

        model_id = self.config.model_for(role)["model"]
        final_answer = None
        for step in range(1, self.max_steps + 1):
            try:
                with streaming_markdown(
                    self.console,
                    waiting_label=f"waiting for {role} ({model_id})",
                ) as (on_token, _buf):
                    reply = self.llm.chat(role, messages, stream=True,
                                          on_token=on_token)
            except LLMError as e:
                self.console.print(f"[red]LLM error:[/red] {e}")
                return
            self._print_timing()

            messages.append({"role": "assistant", "content": reply})
            call = extract_tool_call(reply)

            if not call:
                final_answer = reply.strip()
                break

            observation = self._run_tool(call, ctx)
            # Cap what goes back into context — huge tool outputs slow every
            # later step's prefill without adding much signal.
            if len(observation) > 6000:
                observation = observation[:6000] + "\n... [truncated for context]"
            messages.append({
                "role": "user",
                "content": f"[tool result: {call['tool']}]\n{observation}",
            })
        else:
            self.console.print(
                f"[yellow]Reached max_steps ({self.max_steps}); stopping.[/yellow]"
            )

        # Persist only the clean turn to conversational history (keeps context small).
        self.history.append({"role": "user", "content": user_msg})
        if final_answer:
            self.history.append({"role": "assistant", "content": final_answer})

        # Self-improvement — in the background so the prompt returns NOW
        # instead of making the user wait for the reviewer model.
        if final_answer and self.config.memory.get("auto_learn", True):
            threading.Thread(target=self._maybe_learn,
                             args=(user_msg, final_answer),
                             daemon=True).start()

        return final_answer

    def reset(self):
        self.history.clear()

    # ---- internals ----------------------------------------------------------

    def _print_timing(self):
        """One dim line of real numbers after every model response, straight
        from Ollama: where the time went and how fast generation ran."""
        m = self.llm.last_metrics
        if not m:
            return
        parts = []
        if m.get("load_s", 0) > 0.5:
            parts.append(f"model load {m['load_s']:.1f}s")
        if m.get("prefill_s", 0) > 0.5:
            parts.append(f"prompt {m['prefill_s']:.1f}s"
                         + (f" ({m['prompt_tokens']} tok)" if m.get("prompt_tokens") else ""))
        if m.get("tps"):
            parts.append(f"{m['tps']:.0f} tok/s × {m['tokens']} tok")
        parts.append(f"total {m.get('total_s', 0):.1f}s")
        self.console.print(f"[dim]  ⏱ {' · '.join(parts)}[/dim]")

    def _run_tool(self, call, ctx):
        name = call.get("tool")
        args = call.get("args", {}) or {}
        tool = self.tools.get(name)
        if not tool:
            self.console.print(f"[red]· unknown tool: {name}[/red]")
            return (f"ERROR: unknown tool '{name}'. Available: "
                    f"{', '.join(self.tools)}")
        try:
            result = tool.run(args, ctx)
        except Exception as e:
            result = f"ERROR running {name}: {e}"
        # Compact, markup-safe trace of the tool result the user can follow.
        shown = result if len(result) < 1200 else result[:1200] + "\n... [truncated]"
        self.console.print(f"[bold cyan]⚙ {name}[/bold cyan]")
        self.console.print(Text(shown, style="dim"))
        return result

    def _recall(self, query):
        k = int(self.config.memory.get("recall_k", 5))
        min_score = float(self.config.memory.get("min_score", 0.28))
        try:
            hits = self.memory.search(query, k=k, min_score=min_score)
        except LLMError:
            return ""  # embeddings offline — degrade gracefully
        if not hits:
            return ""
        return "\n".join(f"- ({kind}) {text}" for _, kind, text, _ in hits)

    def _maybe_learn(self, user_msg, answer):
        try:
            lesson = self.improver.learn(user_msg, answer)
        except LLMError:
            return
        if lesson:
            self.console.print(f"[dim]✎ learned: {lesson}[/dim]")
