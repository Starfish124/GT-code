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

from .prompts import build_system, turn_context
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


# The one arg per tool that identifies WHAT it acted on, for the turn digest.
_DIGEST_ARGS = ("command", "path", "question", "query", "url", "id")


def digest_line(name, args, result):
    """One line remembering a tool step: what ran and how it went.

    These lines are appended to the turn's history entry so follow-up turns
    ('go ahead', 'now add X') know what was already done — files created,
    commands run, and what the user answered to ask_user. Without this the
    model starts every turn amnesiac about its own work.
    """
    arg = next((str(args.get(k)) for k in _DIGEST_ARGS if args.get(k)), "")
    if len(arg) > 100:
        arg = arg[:100] + "…"
    first = ""
    for line in (result or "").strip().splitlines():
        if line.strip():
            first = line.strip()[:150]
            break
    # Mark failures loudly — follow-up turns must not gloss over them and
    # claim the work succeeded.
    failed = (first.startswith(("ERROR", "DENIED"))
              or bool(re.match(r"exit code: (?!0\b)", first)))
    return f"- {'FAILED ' if failed else ''}{name}({arg}) -> {first}"


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

        # What's already been injected this session (it lives on in history,
        # so re-sending it would only waste context tokens).
        self._seen_skills = set()
        self._seen_memory = set()

    # ---- public API ---------------------------------------------------------

    def run(self, user_msg):
        role = self.force_role or self.router.route(user_msg)
        self.console.print(f"[dim]· routed to [bold]{role}[/bold] "
                           f"({self.config.model_for(role)['model']})[/dim]")

        memory_block = self._recall(user_msg)
        picked = [s for s in select(self.skills, user_msg, limit=self.skills_max)
                  if s.name not in self._seen_skills]
        self._seen_skills.update(s.name for s in picked)
        if picked:
            self.console.print(
                f"[dim]· playbooks: {', '.join(s.name for s in picked)}[/dim]")

        # The system prompt is byte-stable for the whole session; per-turn
        # context (playbooks, memory) rides on the user message instead. This
        # keeps Ollama's KV prefix cache valid across turns — follow-ups pay
        # prefill only for the NEW tokens, not the whole conversation again.
        system = build_system(cwd=str(self.cwd), os_name=platform.system(),
                              tools=self.tool_docs)
        user_content = turn_context(user_msg, skills_block(picked), memory_block)

        # Working message list for this turn (includes intermediate tool steps).
        messages = [{"role": "system", "content": system}] + self.history + [
            {"role": "user", "content": user_content}
        ]

        ctx = Ctx(cwd=self.cwd, memory=self.memory,
                  approve=self.approve, config=self.config, ask=self.ask)

        model_id = self.config.model_for(role)["model"]
        final_answer = None
        trace = []                 # digest of tool steps, persisted to history
        compact_warned = False
        interrupted = False
        nudged = False             # one retry-nudge per turn when GT gives up
        try:
            for step in range(1, self.max_steps + 1):
                if self._fit_context(messages) and not compact_warned:
                    self.console.print("[dim]· compacted older tool output to "
                                       "fit the context window[/dim]")
                    compact_warned = True
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
                    # Small models often stop with 'Let me try again…' prose
                    # right after a failed step — that is a plan, not an
                    # answer. Nudge once to keep going instead of ending the
                    # turn (and hallucinating success on the next one).
                    if (trace and trace[-1].startswith("- FAILED")
                            and not nudged and step < self.max_steps):
                        nudged = True
                        self.console.print("[dim]· last step failed — nudging "
                                           "GT to fix it and continue[/dim]")
                        messages.append({
                            "role": "user",
                            "content": ("[system] Your last tool call FAILED "
                                        "and you stopped without finishing. "
                                        "Diagnose and fix it now — reply with "
                                        "your next tool call. Give a final "
                                        "prose answer only when the task is "
                                        "done or truly impossible."),
                        })
                        continue
                    final_answer = reply.strip()
                    break

                observation = self._run_tool(call, ctx)
                trace.append(digest_line(call["tool"], call.get("args") or {},
                                         observation))
                # Cap what goes back into context — huge tool outputs slow
                # every later step's prefill without adding much signal.
                if len(observation) > 6000:
                    observation = observation[:6000] + "\n... [truncated for context]"
                messages.append({
                    "role": "user",
                    "content": f"[tool result: {call['tool']}]\n{observation}",
                })
            else:
                self.console.print(
                    f"[yellow]Reached max_steps ({self.max_steps}); "
                    f"stopping.[/yellow]"
                )
        except KeyboardInterrupt:
            # Don't lose the turn: whatever was already done stays in history
            # so the next message ('continue', 'go ahead') picks up from here.
            interrupted = True
            self.console.print("\n[yellow](interrupted — keeping the work "
                               "done so far in this turn)[/yellow]")

        # Persist the turn. The user message goes in AS SENT (cache prefix
        # stays valid); intermediate tool chatter is replaced by a compact
        # digest so the next turn knows what was actually done.
        self.history.append({"role": "user", "content": user_content})
        stored = final_answer
        if trace:
            digest = "\n".join(trace)
            if len(digest) > 2000:
                digest = digest[:2000] + "\n… [more steps]"
            stored = ((final_answer or
                       "(I was stopped before giving a final answer.)")
                      + f"\n\n[actions taken this turn]\n{digest}")
        elif interrupted:
            stored = "(interrupted before I did anything)"
        if stored:
            self.history.append({"role": "assistant", "content": stored})

        # Self-improvement — in the background so the prompt returns NOW
        # instead of making the user wait for the reviewer model.
        if final_answer and self.config.memory.get("auto_learn", True):
            threading.Thread(target=self._maybe_learn,
                             args=(user_msg, final_answer, tuple(trace)),
                             daemon=True).start()

        return final_answer

    def reset(self):
        self.history.clear()
        self._seen_skills.clear()
        self._seen_memory.clear()

    # ---- internals ----------------------------------------------------------

    def _fit_context(self, messages, keep_recent=2):
        """Keep the message list inside the model's context window.

        Past num_ctx, Ollama silently truncates the prompt — and the system
        prompt with the tool-call instructions is the first thing at risk, so
        the model quietly degrades into prose mid-task. Instead, shrink the
        OLDEST tool observations (the most recent `keep_recent` stay intact);
        the current step rarely needs the full output of step 3. Returns True
        if anything was compacted.
        """
        budget = int(self.config.performance.get("num_ctx", 8192)) - 1024
        def over():
            return sum(len(m["content"]) for m in messages) // 4 > budget
        if not over():
            return False
        compacted = False
        tool_idx = [i for i, m in enumerate(messages)
                    if m["role"] == "user"
                    and m["content"].startswith("[tool result:")]
        for i in (tool_idx[:-keep_recent] if keep_recent else tool_idx):
            if not over():
                break
            head, _, body = messages[i]["content"].partition("\n")
            if len(body) > 300:
                messages[i]["content"] = (
                    f"{head}\n{body[:300]}\n… [older output compacted]")
                compacted = True
        return compacted

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
        # Skip what was already injected this session (it's still in history)
        # and cap lessons — a wall of self-help slogans steers small models
        # more than it helps them.
        fresh, lessons = [], 0
        for _, kind, text, _ in hits:
            if text in self._seen_memory:
                continue
            if kind == "lesson":
                lessons += 1
                if lessons > 2:
                    continue
            fresh.append((kind, text))
            self._seen_memory.add(text)
        if not fresh:
            return ""
        return "\n".join(f"- ({kind}) {text}" for kind, text in fresh)

    def _maybe_learn(self, user_msg, answer, trace=()):
        try:
            lesson = self.improver.learn(user_msg, answer, trace)
        except LLMError:
            return
        if lesson:
            self.console.print(f"[dim]✎ learned: {lesson}[/dim]")
