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
from pathlib import Path

from rich.text import Text

from .prompts import build_system
from .tools import Ctx, active_tools, tool_docs
from .ui import streaming_markdown
from .llm import LLMError


# Matches ```json { ... } ``` (and bare ``` ... ```) fenced blocks.
_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)


def extract_tool_call(text):
    """Return the last valid {"tool": ...} object in the text, or None."""
    candidates = list(_FENCE.findall(text))
    if not candidates:
        stripped = text.strip()
        if stripped.startswith("{") and '"tool"' in stripped:
            candidates = [stripped]
    for block in reversed(candidates):
        try:
            obj = json.loads(block)
        except Exception:
            continue
        if isinstance(obj, dict) and "tool" in obj:
            obj.setdefault("args", {})
            return obj
    return None


class Agent:
    def __init__(self, llm, config, memory, router, console, improver, approve):
        self.llm = llm
        self.config = config
        self.memory = memory
        self.router = router
        self.console = console
        self.improver = improver
        self.approve = approve

        self.cwd = Path.cwd()
        self.history = []          # [{role, content}] — user msgs + final answers
        self.force_role = None     # set via /model to pin a model
        self.max_steps = int(config.agent.get("max_steps", 12))

        # Tools available this session (web tools dropped if web.enabled=false).
        self.tools = {t.name: t for t in active_tools(config)}
        self.tool_docs = tool_docs(self.tools.values())

    # ---- public API ---------------------------------------------------------

    def run(self, user_msg):
        role = self.force_role or self.router.route(user_msg)
        self.console.print(f"[dim]· routed to [bold]{role}[/bold] "
                           f"({self.config.model_for(role)['model']})[/dim]")

        memory_block = self._recall(user_msg)
        system = build_system(
            cwd=str(self.cwd),
            os_name=platform.system(),
            tools=self.tool_docs,
            memory_block=memory_block,
        )

        # Working message list for this turn (includes intermediate tool steps).
        messages = [{"role": "system", "content": system}] + self.history + [
            {"role": "user", "content": user_msg}
        ]

        ctx = Ctx(cwd=self.cwd, memory=self.memory,
                  approve=self.approve, config=self.config)

        final_answer = None
        for step in range(1, self.max_steps + 1):
            try:
                with streaming_markdown(self.console) as (on_token, _buf):
                    reply = self.llm.chat(role, messages, stream=True,
                                          on_token=on_token)
            except LLMError as e:
                self.console.print(f"[red]LLM error:[/red] {e}")
                return

            messages.append({"role": "assistant", "content": reply})
            call = extract_tool_call(reply)

            if not call:
                final_answer = reply.strip()
                break

            observation = self._run_tool(call, ctx)
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

        # Self-improvement (optional, best-effort, never blocks the user).
        if final_answer and self.config.memory.get("auto_learn", True):
            self._maybe_learn(user_msg, final_answer)

        return final_answer

    def reset(self):
        self.history.clear()

    # ---- internals ----------------------------------------------------------

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
