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

from .hooks import Hooks
from .improve import is_noise_lesson
from .intent import IntentGate, AFFIRM
from .prompts import build_system, turn_context
from .skills import load_skills, select, skills_block, SkillIndex
from .tools import Ctx, active_tools, tool_docs, capability_summary, render_todos
from .ui import streaming_markdown
from .llm import LLMError
from .theme import PURPLE


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


def _norm_args(args):
    """Coerce a call's args to a dict — models sometimes JSON-encode the
    whole dict as a string (observed live on the 3B). Normalized HERE, at
    extraction, so everything downstream (attach_content_block, hooks, the
    digest) can rely on a dict."""
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            return {}
    return args if isinstance(args, dict) else {}


def _as_tool_call(obj, known=None):
    """Normalize one parsed JSON object into {"tool", "args"} — or None.

    Accepts GT's own {"tool", "args"} shape, and — when `known` names the
    available tools — the {"name", "parameters"/"arguments"} shape models
    trained on native function calling leak into their TEXT (observed live:
    llama3.2 answered with its native-format call as prose). Requiring the
    name to be a known tool keeps ordinary JSON in answers from matching.
    """
    if not isinstance(obj, dict):
        return None
    if "tool" in obj:
        obj["args"] = _norm_args(obj.get("args", {}))
        return obj
    name = obj.get("name")
    if known is not None and isinstance(name, str) and name in known:
        args = obj.get("arguments", obj.get("parameters", {}))
        return {"tool": name, "args": _norm_args(args)}
    return None


def extract_tool_call(text, known=None):
    """Return one valid tool call parsed out of the text, or None.

    Fenced blocks win (last one — models think first, then act). Otherwise
    scan for bare JSON objects and take the FIRST tool call, so a spray of
    several calls executes sequentially, one per agent step. `known` (an
    iterable of tool names) additionally unlocks the leaked native-format
    shape — see _as_tool_call.
    """
    for block in reversed(_FENCE.findall(text)):
        try:
            obj = json.loads(block)
        except Exception:
            continue
        call = _as_tool_call(obj, known)
        if call:
            return call
    for obj in _scan_objects(text):
        call = _as_tool_call(obj, known)
        if call:
            return call
    return None


def native_calls(raw):
    """Normalize Ollama-shape tool calls into GT's {"tool", "args"} form.

    Ollama returns [{"function": {"name": ..., "arguments": {...}}}, ...];
    arguments occasionally arrive as a JSON string instead of an object (same
    small-model failure the prompt protocol repairs in _run_tool), so both are
    accepted. Nameless entries are dropped.
    """
    calls = []
    for item in raw or []:
        fn = (item or {}).get("function") or {}
        name = fn.get("name")
        if not name:
            continue
        args = fn.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = {}
        calls.append({"tool": name,
                      "args": args if isinstance(args, dict) else {}})
    return calls


def lead_line(text):
    """The first short PROSE line of a reply — what's worth showing when a
    tool-step reply is collapsed instead of dumped ('Building: single-file
    canvas game — starting now.'). Fenced code, JSON and bracketed
    bookkeeping lines are skipped; '' when there is no prose at all."""
    in_fence = False
    for line in (text or "").splitlines():
        s = line.strip()
        if s.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or not s:
            continue
        if s[0] in "{[<" or '"tool"' in s or '"name"' in s:
            continue
        return s if len(s) <= 120 else s[:120] + "…"
    return ""


# Any fenced block (```lang\n…```) — used to lift raw write_file content.
_ANYFENCE = re.compile(r"```[^\n`]*\n(.*?)```", re.S)

# Questions about GT's OWN abilities — conversation, not a task, even when they
# carry a code word ('test', 'run', 'file'). Checked after PLAN_HINT so a real
# "build me an app" still counts as work.
_CAPABILITY_Q = re.compile(
    r"(?i)\b(what can (you|u) do"
    r"|can (you|u) (use|access|reach|browse|search|read|see|open|get on|"
    r"connect to|go on)"
    r"|can (you|u) (deploy|spawn|launch|run|create|make|use) (sub[- ]?)?agents?"
    r"|do (you|u) (have|support|work|know|use)"
    r"|are (you|u) able to"
    r"|do (you|u) have access"
    r"|what (?:llm |ml )?(?:models|tools|capabilities|commands)"
    r"|is it possible for (you|u))\b")

# Identity / small-talk questions that never need a tool — get the lean prompt
# so they answer instantly instead of prefilling the whole toolset.
_CHITCHAT = re.compile(
    r"(?i)\b(who (are|r) (you|u)|what (are|r) (you|u)|what'?s your name"
    r"|who (made|built|created|wrote) (you|u)|how (are|r) (you|u)"
    r"|how'?s it going|how do (you|u) (do|feel)|what'?s up"
    r"|tell me about (yourself|you)|what do (you|u) think)\b")


def attach_content_block(call, reply):
    """Let write_file take its content as a raw fenced block after the json.

    Escaping a whole code file into a JSON string is the single most fragile
    thing the prompt protocol asks of a small model — long contents routinely
    arrive with unterminated strings or bad escapes. With this, the json part
    stays tiny ({"path": …}) and the file body rides in a second fence with
    no escaping at all.
    """
    if call.get("tool") != "write_file":
        return call
    args = call.setdefault("args", {})
    if args.get("content"):
        return call
    blocks = [b for b in _ANYFENCE.findall(reply) if '"tool"' not in b]
    if blocks:
        args["content"] = blocks[-1]
    return call


# The one arg per tool that identifies WHAT it acted on, for the turn digest.
_DIGEST_ARGS = ("command", "path", "question", "query", "url", "task", "id")


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
    failed = (first.startswith(("ERROR", "DENIED", "REFUSED"))
              or bool(re.match(r"exit code: (?!0\b)", first)))
    return f"- {'FAILED ' if failed else ''}{name}({arg}) -> {first}"


# 'I'll now create the frontend…' with no tool call is an announcement, not
# an answer. Requires an action verb after the intent phrase so that a normal
# closing line like 'Let me know if you need anything' never matches.
_INTENT = re.compile(
    r"(?i)\b(?:i'?ll|i will|let'?s|let me|i'?m going to|i am going to|"
    r"now,? i(?:'ll| will)?|proceeding to)\s+"
    r"(?:now\s+|first\s+|then\s+|just\s+)*"
    r"(?:create|proceed|start|set(?:ting)? up|build|write|install|make|add|"
    r"update|run|fix|implement|generate|configure|move|continue|go ahead)")

# "thanks, maybe later" / "not now" after a presented plan — the user is
# declining, not steering the task, so the plan lapses silently instead of
# getting the carry-out-the-plan push (see _resolve_turn).
_DEFER = re.compile(r"(?i)\b(later|not (?:now|yet|today)|maybe|hold (?:off|on)|"
                    r"wait|skip|nah|never ?mind|don'?t|forget it)\b")

# What to feed back for each kind of stall, instead of ending the turn.
_NUDGE = {
    "badjson": ("[system] That looked like a tool call but it was NOT valid "
                "JSON — usually an unterminated string or missing closing "
                "braces on a long file. Re-send it as ONE complete ```json "
                "block. For write_file, OMIT \"content\" from the json and "
                "put the raw file body in a second fenced block right after "
                "it — no JSON escaping needed."),
    "failed": ("[system] Your last tool call FAILED and you stopped without "
               "finishing. Diagnose and fix it now — reply with your next "
               "tool call. Give a final prose answer only when the task is "
               "done or truly impossible."),
    "intent": ("[system] You ANNOUNCED what you will do but did not do it. "
               "Do it now — reply with the tool call, in this same turn. "
               "Never end a reply with 'I'll now…' or ask whether to "
               "proceed: the user already told you to. If a genuine "
               "decision is needed, use the ask_user tool."),
    "echo":   ("[system] You are repeating your previous message without "
               "acting. Stop announcing. Make the next tool call now, or "
               "use ask_user if you are blocked on a real decision."),
    "mimicry": ("[system] You WROTE a bookkeeping block ('[actions taken "
                "this turn]' / '[tool result: ...]') as your reply. Those "
                "blocks are written by GT AFTER you actually call a tool — "
                "writing one yourself performs NOTHING; no file was touched, "
                "no command ran. Do the work for real now: make the actual "
                "tool call, or answer in plain prose with no bracketed "
                "blocks."),
    "codedump": ("[system] You pasted a whole file into the CHAT as your "
                 "answer. Nothing on disk changed — the user needs the FILE "
                 "updated, not code to copy by hand. Send that content as a "
                 "write_file call now (or edit_file for a small change). "
                 "Code in chat does nothing."),
    # badjson, but the turn runs on NATIVE function calling — 're-send a json
    # block' would be the wrong advice, so point at the right channel.
    "badjson_native": ("[system] You wrote a tool call as TEXT, but it did "
                       "not parse. On this model tool calls go through the "
                       "native function-calling interface — never as JSON in "
                       "your reply. Make the call properly now, or reply in "
                       "plain prose if the task is done."),
}


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
        # Interaction mode: 'auto' classifies each turn; chat/code/plan make the
        # behaviour STRICT and predictable (set with /mode). See _resolve_turn.
        self.mode = "auto"
        # Learned preferences (set by the shell from the Profiler at startup;
        # session-stable so it rides in the prompt without breaking the cache)
        # and a lightweight per-session request log the analyst reads later.
        self.profile_summary = ""
        self.session_log = []      # [{"user", "outcome"}]
        # The write_todos checklist — the model's external memory for a
        # multi-step task. Persists across turns (survives "continue"); /reset
        # or a genuinely new task clears it.
        self.todos = []
        # The GT.md project-memory layer (see project_memory.py): loaded once
        # here, re-loaded on /cd, /init, /memory reload or a '# note' — never
        # per turn, so the work prompt stays byte-stable for the KV cache.
        pm_cfg = config.data.get("project_memory", {})
        self.pm_enabled = bool(pm_cfg.get("enabled", True))
        self.pm_max_chars = int(pm_cfg.get("max_chars", 6000))
        self.project_memory = ""
        self.project_memory_files = []
        self.reload_project_memory()
        self.max_steps = int(config.agent.get("max_steps", 12))
        # Keep the working history bounded (in entries = 2 * turns) so a long
        # session's prompt — and therefore its prefill time on a CPU box —
        # doesn't creep up turn after turn. /reset clears.
        self.max_history = int(config.agent.get("max_history_turns", 10)) * 2
        # Auto-compaction (Claude Code's fix for failure mode #2, context
        # collapse): when history outgrows its bound, the oldest exchanges are
        # DISTILLED into this rolling summary by the resident model instead of
        # silently dropped — the goal and early decisions survive a long build.
        # It rides as a message at the FRONT of history (never in the system
        # prompt, which stays byte-stable and KV-cached). /compact runs it on
        # demand; /reset clears it.
        comp_cfg = config.data.get("compaction", {})
        self.compaction_enabled = bool(comp_cfg.get("enabled", True))
        self.keep_turns = int(comp_cfg.get("keep_turns",
                                           max(1, self.max_history // 4)))
        self.summary_max_chars = int(comp_cfg.get("max_chars", 1500))
        self.session_summary = ""

        # Deterministic lifecycle hooks (hooks.py): the user's own commands
        # that ALWAYS run at fixed points — pre_tool can veto a call outright.
        self.hooks = Hooks(config, console)

        # Confidence-gated planning (intent.py): a new build-shaped request
        # gets one small triage call weighing its plausible readings; low
        # confidence routes to plan-first or ONE clarifying question instead
        # of blind execution. _pending_plan marks "a gated plan awaits the
        # user's go" — the next affirmative turn builds.
        self.intent_gate = IntentGate(llm, config, console)
        self._pending_plan = False
        # The model role the current task is running on — while the task is
        # live (open todos OR recent tool work — small builds never write a
        # checklist), follow-up turns stick to it (see _resolve_turn).
        self._task_role = None
        self._task_live = False
        # Set when a turn picks up a pending plan (an affirmative OR a
        # steer) — run() then reminds the model that the plan was only ever
        # PRESENTED and nothing exists yet (observed live: told to 'start
        # the game' after a plan, the 8B tried to run files it never wrote).
        self._plan_pickup = False

        # Tools available this session (web tools dropped if web.enabled=false).
        self.tools = {t.name: t for t in active_tools(config)}
        self.tool_docs = tool_docs(self.tools.values())
        # Tool protocol (roadmap #7): 'auto' uses NATIVE function calling on
        # models whose chat template supports it (asked of Ollama once per
        # model) and falls back to the portable prompt-JSON protocol
        # everywhere else; 'native'/'prompt' force one side.
        self.tool_protocol = str(
            config.agent.get("tool_protocol", "auto")).lower()
        self.tool_specs = [t.spec() for t in self.tools.values()]
        # A truthful "what you can do" clause built from the active tools —
        # so GT answers capability questions instead of asking them. Append the
        # actual model line-up so "what models are available?" gets a real
        # answer from context instead of spawning a research sub-agent.
        self.capabilities = capability_summary(self.tools.values())
        try:
            lineup = ", ".join(
                f"{r}={self.config.models[r]['model']}"
                for r in ("brain", "fast", "tiny") if r in self.config.models)
            if lineup:
                self.capabilities += (
                    f". You run on these local models (see /models for what's "
                    f"actually served): {lineup}")
        except Exception:
            pass

        # Expert playbooks, matched per-request and injected into context.
        skills_cfg = config.data.get("skills", {})
        self.skills = load_skills() if skills_cfg.get("enabled", True) else []
        self.skills_max = int(skills_cfg.get("max", 2))
        self.skills_inject_chars = int(skills_cfg.get("inject_max_chars", 1500))
        # Semantic skill selection over a (potentially large) imported library.
        # Cached, and off the main thread; until it's ready, selection falls
        # back to keyword matching.
        self.skill_index = None
        if self.skills and skills_cfg.get("select", "auto") != "keyword":
            self.skill_index = SkillIndex(
                self.llm, Path(config.data_dir) / "skills_emb.db")
        # Embedding the whole library is seconds of CPU. Doing it at startup
        # steals the CPU from the one-time model load (making the first "Hi"
        # slower), and a pure-chat session never needs it — so defer the build
        # to the first WORK turn that actually selects a playbook.
        self._skill_index_started = False

        # What's already been injected this session (it lives on in history,
        # so re-sending it would only waste context tokens).
        self._seen_skills = set()
        self._seen_memory = set()

    # ---- public API ---------------------------------------------------------

    def run(self, user_msg):
        # In auto mode GT classifies the turn; a forced /mode bypasses that so a
        # coding session never slips back into "conversation" and refuses to act
        # (the flappy-bird failure). chat_only is the strict subset that gets the
        # lean, no-tools prompt AND no injected playbook.
        role, conversational, chat_only, prompt_mode = self._resolve_turn(user_msg)
        # Picking up a plan (by "go" or by a steer): nothing has been built
        # yet, and small models happily 'run' files that don't exist. Say so.
        plan_block = ""
        if self._plan_pickup:
            self._plan_pickup = False
            plan_block = ("Last turn you only PRESENTED A PLAN — none of it "
                          "has been executed and no files were created. This "
                          "message steers that task: carry the plan out now "
                          "(adjusted for this message). If asked to start or "
                          "run the result, create the planned files first, "
                          "then run.")
        # Remembered so a sub-agent spawned this turn runs on the SAME
        # (already-loaded) model — context isolation without swap churn.
        self._turn_role = role
        temperature = self._chat_temp() if conversational else self._task_temp()
        # Hybrid tool protocol: native function calling when this turn's model
        # supports it (a per-model fact — asked of Ollama once and cached),
        # the portable prompt-JSON everywhere else. Chat turns carry no tools.
        native = prompt_mode != "chat" and self._use_native(role)
        prefix = f"[{self.mode} mode] " if self.mode != "auto" else ""
        self.console.print(f"[dim]· {prefix}[bold {PURPLE}]{role}[/bold {PURPLE}]"
                           f"[dim] ({self.config.model_for(role)['model']}) · "
                           f"T={temperature:g} {'chat' if conversational else 'code'}"
                           f"{' · native tools' if native else ''}"
                           f"[/dim]")

        # Confidence gate: before a NEW build starts, weigh the plausible
        # readings of the request. High confidence builds immediately (the
        # bias-to-action stays); medium plans first; low asks ONE question.
        # Fail-open: any gate problem means "build as before".
        clarify_block = ""
        if self.intent_gate.should_gate(user_msg, conversational, self.mode,
                                        self.todos):
            self.console.print("[dim]· weighing the request before building "
                               "(intent check)…[/dim]")
            a = self.intent_gate.assess(user_msg, role)
            verdict = self.intent_gate.decide(a) if a else "build"
            if a:
                self.console.print(f"[dim]· intent: {a.reading or user_msg[:60]}"
                                   f" · confidence {a.confidence}% → "
                                   f"{verdict}[/dim]")
            if verdict == "ask" and not self.ask:
                verdict = "plan"        # nobody to ask — plan instead
            if verdict == "ask":
                answer = (self.ask(a.question) or "").strip()
                if answer:
                    clarify_block = f"Q: {a.question}\nA: {answer}"
                # no answer = "just use defaults"; carry on and build
            elif verdict == "plan":
                prompt_mode = "plan"
                self._pending_plan = True
                self.console.print(f"[dim]· confidence below "
                                   f"{self.intent_gate.min_confidence}% — "
                                   f"planning first; say 'go' to build[/dim]")

        # Conversation doesn't need RAG recall — skip the embedding round-trip
        # (and the noise of self-help lessons in small talk). Work turns still
        # pull relevant memory/lessons. This also protects the resident 3B's
        # warm KV cache: no extra model touched before a plain answer.
        memory_block = "" if conversational else self._recall(user_msg)
        # Conversation gets ONLY the light conversation playbook (no heavy
        # engineering playbooks bloating a simple prompt); work gets up to N
        # relevant ones, with the conversation playbook kept out of that pool.
        if conversational:
            if chat_only:
                candidates = []          # lean prompt already covers this
            else:
                conv = next((s for s in self.skills
                             if s.name == "conversation"), None)
                candidates = [conv] if conv else []
        else:
            self._ensure_skill_index()   # first work turn kicks off embedding
            pool = [s for s in self.skills if s.name != "conversation"]
            candidates = select(pool, user_msg, limit=self.skills_max,
                                index=self.skill_index)
        picked = [s for s in candidates if s.name not in self._seen_skills]
        self._seen_skills.update(s.name for s in picked)
        if picked:
            self.console.print(
                f"[dim]· playbooks: {', '.join(s.name for s in picked)}[/dim]")

        # The system prompt is byte-stable for the whole session; per-turn
        # context (playbooks, memory) rides on the user message instead. This
        # keeps Ollama's KV prefix cache valid across turns — follow-ups pay
        # prefill only for the NEW tokens, not the whole conversation again.
        # Small talk / capability questions get the lean ~200-token prompt so
        # the resident 3B prefills a greeting in a fraction of the tokens;
        # anything that might need to act keeps the full toolset. plan mode adds
        # a plan-first directive (see prompts.build_system).
        system = build_system(cwd=str(self.cwd), os_name=platform.system(),
                              tools=self.tool_docs,
                              capabilities=self.capabilities, mode=prompt_mode,
                              profile=self.profile_summary,
                              project_memory=self.project_memory,
                              protocol="native" if native else "prompt")
        # Re-inject the current checklist on work turns so the model re-reads
        # its plan every turn (this is the "external memory" that survives across
        # 'continue' and any compaction — the flappy-bird fix).
        todos_block = render_todos(self.todos) if (self.todos and not conversational) else ""
        user_content = turn_context(
            user_msg, skills_block(picked, self.skills_inject_chars),
            memory_block, todos_block,
            hook_block=self.hooks.user_prompt(user_msg, cwd=self.cwd),
            clarify_block=clarify_block, plan_block=plan_block)

        # Working message list for this turn (includes intermediate tool steps).
        # The session summary (if any) rides between the system prompt and the
        # verbatim history: the system prompt prefix stays KV-cached, and only
        # the (much smaller) history region re-prefills after a compaction.
        messages = ([{"role": "system", "content": system}]
                    + self._summary_context() + self.history
                    + [{"role": "user", "content": user_content}])

        # Plan turns (forced /mode plan or a gated low-confidence build) are
        # enforced at the TOOL level, not just in the prompt — observed live:
        # a 3B ignored the plan directive and started writing files anyway.
        self._plan_turn = (prompt_mode == "plan")

        ctx = Ctx(cwd=self.cwd, memory=self.memory,
                  approve=self.approve, config=self.config, ask=self.ask,
                  user_msg=user_msg, todos=self.todos,
                  spawn=self._spawn_subagent)

        model_id = self.config.model_for(role)["model"]
        final_answer = None
        trace = []                 # digest of tool steps, persisted to history
        compact_warned = False
        interrupted = False
        nudged = set()             # stall kinds already nudged this turn
        todo_reminded = bool(self.todos)  # already has a plan → no reminder
        failed_calls = {}          # (tool, args) of calls that FAILED this turn
        tool_errors = {}           # tool -> count of ERROR/DENIED results

        def _collapse(text):
            """Decide what a finished reply prints as (see ui.py): a real
            answer prints in full; a tool-step reply — including a broken
            ATTEMPT at one — collapses to its one prose lead line, so the
            user sees '> write_file index.html', not pages of code/JSON."""
            has_call = bool(native
                            and getattr(self.llm, "last_tool_calls", None))
            if not has_call:
                has_call = (extract_tool_call(text, known=self.tools)
                            is not None)
            if not has_call and not ('"tool"' in text and "{" in text):
                return None
            return lead_line(text)
        try:
            for step in range(1, self.max_steps + 1):
                if self._fit_context(messages) and not compact_warned:
                    self.console.print("[dim]· compacted older tool output to "
                                       "fit the context window[/dim]")
                    compact_warned = True
                ptok = sum(len(m["content"]) for m in messages) // 4
                try:
                    with streaming_markdown(
                        self.console,
                        waiting_label=f"{role} ({model_id}) · reading "
                                      f"~{ptok}-token prompt",
                        collapse=_collapse,
                    ) as (on_token, _buf):
                        reply = self.llm.chat(role, messages, stream=True,
                                              temperature=temperature,
                                              on_token=on_token,
                                              tools=(self.tool_specs
                                                     if native else None))
                except LLMError as e:
                    self.console.print(f"[red]LLM error:[/red] {e}")
                    return
                self._print_timing()

                # Native path: the calls arrive structured through the API —
                # nothing to parse, and several may come in one response.
                # Prompt path: parse ONE fenced-JSON call out of the reply.
                raw_calls = (list(getattr(self.llm, "last_tool_calls", None)
                                  or []) if native else [])
                if raw_calls:
                    # Keep Ollama's own shape in the message so the model's
                    # chat template re-renders its calls on later steps.
                    messages.append({"role": "assistant", "content": reply,
                                     "tool_calls": raw_calls})
                    calls = native_calls(raw_calls)
                else:
                    # Also the native-mode repair: a confused model that
                    # writes its call as text JSON still gets it executed.
                    messages.append({"role": "assistant", "content": reply})
                    call = extract_tool_call(reply, known=self.tools)
                    calls = [attach_content_block(call, reply)] if call else []

                if not calls:
                    # Small models stall in recognizable ways instead of
                    # finishing: give-up prose after a FAILED step, announcing
                    # work without doing it ('I'll now create…'), repeating
                    # their previous answer verbatim, or faking a bookkeeping
                    # block. Each kind is nudged at most once per turn — if
                    # the model STILL answers prose after the nudge, that's
                    # its answer (e.g. declaring the task impossible). EXCEPT
                    # mimicry: a faked '[actions taken]' block is never a
                    # legitimate answer, so it is nudged every time
                    # (max_steps still bounds the turn).
                    stall = self._stall_reason(reply.strip(), trace)
                    if (stall and step < self.max_steps
                            and (stall == "mimicry" or stall not in nudged)):
                        nudged.add(stall)
                        self.console.print(f"[dim]· GT stalled ({stall}) — "
                                           f"nudging it to act[/dim]")
                        nudge = ("badjson_native"
                                 if (stall == "badjson" and native) else stall)
                        messages.append({"role": "user",
                                         "content": _NUDGE[nudge]})
                        continue
                    final_answer = reply.strip()
                    break

                for call in calls:
                    # Loop breaker (observed live: the same failing
                    # create_powerpoint call retried 4x while the model
                    # narrated fake successes): an IDENTICAL call that
                    # already failed this turn is refused, not re-run.
                    key = (call["tool"],
                           json.dumps(call.get("args") or {},
                                      sort_keys=True, default=str))
                    if key in failed_calls:
                        observation = (
                            f"REFUSED: you already made exactly this "
                            f"{call['tool']} call and it failed: "
                            f"{failed_calls[key]}\nRepeating it cannot "
                            f"succeed. Fix the cause first, take a genuinely "
                            f"different approach, or report the blocker to "
                            f"the user as your final answer.")
                        self._show_step(call["tool"], call.get("args") or {},
                                        observation)
                    elif tool_errors.get(call["tool"], 0) >= 3:
                        # Three strikes on GT's OWN validation/permission
                        # errors (never on real command runs — a failing
                        # build is legitimate debugging): a model that
                        # couldn't shape the arguments three times is
                        # flailing, not converging (observed live: write_
                        # todos x3 with differently-broken strings).
                        observation = (
                            f"REFUSED: {call['tool']} has now failed 3 times "
                            f"this turn — stop calling it. Finish the task "
                            f"another way, or report the blocker to the user "
                            f"as your final answer.")
                        self._show_step(call["tool"], call.get("args") or {},
                                        observation)
                    else:
                        observation = self._run_tool(call, ctx)
                        first = next((l.strip() for l in
                                      observation.splitlines() if l.strip()),
                                     "")
                        if first.startswith(("ERROR", "DENIED")):
                            failed_calls[key] = first[:150]
                            tool_errors[call["tool"]] = (
                                tool_errors.get(call["tool"], 0) + 1)
                        elif re.match(r"exit code: (?!0\b)", first):
                            failed_calls[key] = first[:150]
                    trace.append(digest_line(call["tool"],
                                             call.get("args") or {},
                                             observation))
                    # Cap what goes back into context — huge tool outputs slow
                    # every later step's prefill without adding much signal.
                    if len(observation) > 6000:
                        observation = (observation[:6000]
                                       + "\n... [truncated for context]")
                    if raw_calls:
                        # Native calls get native results: the tool role is
                        # what the model's chat template was trained on.
                        messages.append({"role": "tool",
                                         "tool_name": call["tool"],
                                         "content": observation})
                    else:
                        messages.append({
                            "role": "user",
                            "content": (f"[tool result: {call['tool']}]"
                                        f"\n{observation}"),
                        })

                # Claude-Code-style system-reminder: a multi-step task with no
                # checklist is how the model loses the plan (it made a PowerPoint
                # for a game). Nudge it once to externalise the plan with
                # write_todos so it works the task, not its own confusion.
                if (not conversational and not self.todos and step >= 3
                        and not todo_reminded):
                    todo_reminded = True
                    messages.append({"role": "user", "content": (
                        "[system-reminder] You are several steps into a "
                        "multi-step task with no checklist. Call write_todos now "
                        "with the FULL plan, then work through it one item at a "
                        "time — do not try to hold the whole plan in your head.")})
            else:
                self.console.print(
                    f"[yellow]Reached the step limit ({self.max_steps}) — "
                    f"stopping here. {len(trace)} tool step(s) were taken; "
                    f"they are kept, so say 'continue' to pick up where GT "
                    f"left off.[/yellow]"
                )
        except KeyboardInterrupt:
            # Don't lose the turn: whatever was already done stays in history
            # so the next message ('continue', 'go ahead') picks up from here.
            interrupted = True
            self.console.print("\n[yellow](interrupted — keeping the work "
                               "done so far in this turn)[/yellow]")

        # A work turn that actually ran tools marks the task LIVE and sets
        # the sticky task role: follow-ups run on this same (already warm)
        # model instead of being re-classified as chat. A work turn that
        # concludes in pure prose releases the liveness — the task is done
        # or was never real work (an interrupted turn keeps it: 'continue'
        # must land back on the working model). Plan turns touch neither.
        if trace and not conversational:
            self._task_role = role
            # A plan turn's trace is just denied write attempts — only real
            # WORK marks the task live.
            if prompt_mode == "work":
                self._task_live = True
        elif (not conversational and prompt_mode == "work"
              and not trace and not interrupted):
            self._task_live = False

        # Persist the turn. The user message goes in AS SENT (cache prefix
        # stays valid); intermediate tool chatter is replaced by a compact
        # digest so the next turn knows what was actually done.
        self.history.append({"role": "user", "content": user_content})
        # Keep the HISTORY copy of a long answer lean (the user already saw the
        # full text live) so a verbose reply doesn't bloat every later prefill.
        answer = final_answer
        if answer and len(answer) > 1200:
            answer = answer[:1200] + "… [answer trimmed in history]"
        stored = answer
        if trace:
            digest = "\n".join(trace)
            if len(digest) > 2000:
                digest = digest[:2000] + "\n… [more steps]"
            stored = ((answer or
                       "(I was stopped before giving a final answer.)")
                      + f"\n\n[actions taken this turn]\n{digest}")
        elif interrupted:
            stored = "(interrupted before I did anything)"
        if stored:
            self.history.append({"role": "assistant", "content": stored})
        if len(self.history) > self.max_history:
            self._autocompact()

        # Lightweight log of what the user asked, for the periodic preference
        # analyst (profile.py). Bounded; kept across /reset since it's about the
        # whole session, not the current conversation thread.
        self.session_log.append(
            {"user": user_msg,
             "outcome": (final_answer or ("(interrupted)" if interrupted
                                          else ""))[:200]})
        if len(self.session_log) > 60:
            self.session_log = self.session_log[-60:]

        # turn_end hooks — deterministic per-turn side effects (logging,
        # notifications). After persist, so the turn is fully settled.
        self.hooks.fire("turn_end", {
            "prompt": user_msg, "answer": (final_answer or "")[:2000],
            "steps": len(trace), "cwd": str(self.cwd)})

        # Self-improvement — in the background so the prompt returns NOW
        # instead of making the user wait for the reviewer model. Fire it only
        # when there's plausibly something to learn: a FAILED step or a genuinely
        # multi-step task. Skipped on conversation and on routine 1-2 step
        # successes — the reviewer is a full inference and would otherwise
        # occupy the single Ollama runner and slow the user's next turn.
        worth_learning = (any(t.startswith("- FAILED") for t in trace)
                          or len(trace) >= 3)
        if (final_answer and self.config.memory.get("auto_learn", False)
                and not conversational and worth_learning):
            threading.Thread(target=self._maybe_learn,
                             args=(user_msg, final_answer, tuple(trace)),
                             daemon=True).start()

        return final_answer

    def prewarm(self, role):
        """Load the model AND cache the lean chat prompt in the background.

        Two things happen here: the weights load into RAM, and the exact system
        prompt a first turn will use gets prefilled so that turn reuses Ollama's
        KV cache instead of re-prefilling it. We warm the CHAT prompt (not the
        full one) because the first turn is almost always a greeting — and the
        lean prompt is a fraction of the tokens, so the warmup itself finishes
        sooner and "Hi" reuses it instantly. A first WORK turn still pays one
        full-prompt prefill, which is expected for building.
        """
        def go():
            try:
                system = build_system(cwd=str(self.cwd),
                                      os_name=platform.system(),
                                      tools=self.tool_docs,
                                      capabilities=self.capabilities,
                                      mode="chat", profile=self.profile_summary)
                self.llm.chat(role,
                              [{"role": "system", "content": system},
                               {"role": "user", "content": "Reply: OK"}],
                              stream=False, timeout=300)
            except Exception:
                pass  # best-effort; real calls surface any error
        threading.Thread(target=go, daemon=True).start()

    def reset(self):
        self.history.clear()
        self._seen_skills.clear()
        self._seen_memory.clear()
        self.todos.clear()
        self.session_summary = ""
        self._pending_plan = False
        self._task_role = None
        self._task_live = False
        self._plan_pickup = False

    def compact_now(self):
        """Fold the WHOLE conversation into the session summary (/compact).

        History restarts light — one short summary instead of every turn —
        but nothing important is forgotten. Returns the new summary, or None
        if summarizing failed (the conversation is then left untouched).
        """
        if not self.history:
            return self.session_summary or None
        summary = self._summarize(self.history)
        if not summary:
            return None
        self.session_summary = summary
        self.history = []
        return summary

    def _spawn_subagent(self, task):
        """Backs the run_agent tool — a fresh-context, read-only research
        loop (see subagent.py). Only its final report enters this
        conversation; the exploration itself stays in the sub-agent's own
        message list and is thrown away with it."""
        from .subagent import run_subagent
        role = getattr(self, "_turn_role", None) or "tiny"
        try:
            model = self.config.model_for(role)["model"]
        except KeyError:
            model = "?"
        brief = task if len(task) <= 70 else task[:70] + "…"
        self.console.print(f"[dim]· sub-agent ({model}) researching: "
                           f"{brief}[/dim]")
        report, steps = run_subagent(self.llm, self.config, self.memory,
                                     self.cwd, task, role,
                                     console=self.console)
        self.console.print(f"[dim]· sub-agent done ({steps} tool step(s)) — "
                           f"only its report enters the conversation[/dim]")
        return report

    def reload_project_memory(self):
        """(Re-)read the GT.md layers for the current workspace.

        Called at startup and whenever they could have changed (/cd, /init,
        /memory reload, a '# note') — never per turn. Returns the files loaded.
        """
        if not self.pm_enabled:
            self.project_memory, self.project_memory_files = "", []
            return []
        from .project_memory import load
        self.project_memory, self.project_memory_files = load(
            self.cwd, self.pm_max_chars)
        return self.project_memory_files

    def reload_skills(self):
        """Re-scan skill dirs and rebuild the semantic index (after an import)."""
        self.skills = load_skills()
        if self.skills and self.skill_index is not None:
            self._skill_index_started = True
            self._build_skill_index_async(announce=True)
        return len(self.skills)

    def _ensure_skill_index(self):
        """Start embedding the skill library on the first work turn that needs
        it (deferred from startup so it doesn't steal CPU from the model load)."""
        if self.skill_index is not None and not self._skill_index_started:
            self._skill_index_started = True
            self._build_skill_index_async()

    def _build_skill_index_async(self, announce=False):
        n = len(self.skills)

        def go():
            try:
                added = self.skill_index.build(self.skills)
            except LLMError:
                return          # embeddings offline — keyword matching stands in
            except Exception:
                return
            if announce or added:
                self.console.print(
                    f"[dim]· skill index ready: {n} playbooks searchable"
                    + (f" ({added} newly embedded)" if added else "")
                    + "[/dim]")

        threading.Thread(target=go, daemon=True).start()

    # ---- internals ----------------------------------------------------------

    def _task_temp(self):
        return float(self.config.performance.get("temperature", 0.3))

    def _chat_temp(self):
        return float(self.config.performance.get("temperature_chat",
                                                 self._task_temp()))

    def _is_conversational(self, user_msg):
        """True for plain talk, False for coding/building/planning.

        Order matters: a real build/plan request is always work; then a question
        about GT's own abilities ("do you have access to the internet?", "what
        can you do?") is conversation EVEN IF it contains a code word like
        'test', 'run' or 'file' (that was the "lets test it out, do you have
        internet?" false-positive that pulled in 3 code playbooks); otherwise
        fall back to the router's code signal. Drives both temperature and
        whether engineering playbooks get injected.
        """
        from .router import _CODE_HINT, _PLAN_HINT, _BUILD_HINT
        text = user_msg or ""
        # A build request ("make me a todo app", "create the famous game called
        # flappy bird") is WORK even with no code-hint word in it — this is the
        # flappy-bird miss that dropped a build onto the conversation playbook.
        if _PLAN_HINT.search(text) or _BUILD_HINT.search(text):
            return False
        if _CAPABILITY_Q.search(text):
            return True
        return not _CODE_HINT.search(text)

    def _chat_only(self, user_msg):
        """True only for turns that DEFINITELY need no tools — small talk and
        capability questions. These get the lean chat system prompt.

        Stricter than _is_conversational (which also drives temperature): any
        hint of code or building keeps the FULL toolset, so a request like
        "make an excel of Q3 sales" or "hi, read main.py" can still act. Only a
        greeting or a "can you…/what can you do?" question — which is answered
        from the capability list, never a tool — drops to the lean prompt.
        """
        from .router import _SMALL_TALK, _CODE_HINT, _PLAN_HINT, _BUILD_HINT
        text = user_msg or ""
        if (_PLAN_HINT.search(text) or _CODE_HINT.search(text)
                or _BUILD_HINT.search(text)):
            return False
        if _CAPABILITY_Q.search(text) or _CHITCHAT.search(text):
            return True
        return len(text.strip()) < 40 and bool(_SMALL_TALK.search(text))

    MODES = ("auto", "chat", "code", "plan")

    def set_mode(self, mode):
        """Set the interaction mode; returns the canonical name or None."""
        m = (mode or "").strip().lower()
        m = {"conversation": "chat", "conversational": "chat", "talk": "chat",
             "coding": "code", "build": "code", "planning": "plan"}.get(m, m)
        if m not in self.MODES:
            return None
        self.mode = m
        return m

    def _forced_role(self, preferred):
        """Model role for a forced code/plan mode: the preferred model, but
        capped back to the resident 3B on a slow box (and 'tiny' if absent)."""
        if preferred not in self.config.models:
            preferred = "tiny"
        return self.router._cap(preferred)

    def _resolve_turn(self, user_msg):
        """(role, conversational, chat_only, prompt_mode) for this turn.

        auto → classify the message. A forced mode is STRICT: it ignores the
        classifier so behaviour is predictable (a coding session stays coding).
        A /model pin still wins over the mode's default model.
        """
        # A gated plan from last turn: an affirmative ("go", "do it") builds
        # NOW — the plan is in history, so the work prompt picks it up. Any
        # other message means the user redirected; the pending plan lapses.
        pending, self._pending_plan = self._pending_plan, False
        if self.mode == "chat":
            return (self.force_role or "tiny"), True, True, "chat"
        if self.mode == "code":
            return (self.force_role or self._forced_role("fast")), False, False, "work"
        if self.mode == "plan":
            return (self.force_role or self._forced_role("brain")), False, False, "plan"
        if pending and AFFIRM.match(user_msg or ""):
            self._plan_pickup = True
            return (self.force_role or self._forced_role("fast")), False, False, "work"
        # The user answered a plan with a steer instead of "go" — that's
        # still the planned task: stay a work turn on the working model and
        # pick the plan up (adjusted). A decline ("thanks, maybe later")
        # must NOT get the carry-it-out push, so it lapses silently.
        if (pending and not self._chat_only(user_msg)
                and not _DEFER.search(user_msg or "")):
            self._plan_pickup = True
            return (self.force_role or self._forced_role("fast")), False, False, "work"
        # "use the 8b (model) on this" — honour a natural-language model
        # request for this turn (observed live: the user asked exactly that
        # and the router ignored it). The executed role then becomes the
        # sticky task role below, so it persists for the task.
        asked = self._asked_role(user_msg)
        if asked:
            return asked, False, False, "work"
        role = self.force_role or self.router.route(user_msg)
        conversational = self._is_conversational(user_msg)
        chat_only = self._chat_only(user_msg)
        # Mid-task continuity (observed live: the 8B built flappy bird, then
        # 'start the game' and 'its blank just a white page' dropped to the
        # 3B at chat temperature): while the task is live, a follow-up IS
        # work on that task — it stays a work turn and runs on the model
        # that is doing the task (already warm). "Live" = the checklist has
        # open items OR the last work turn ran tools (_task_live — small
        # builds often never write todos, and the stickiness must not depend
        # on the model remembering to). Genuine small talk (chat_only) still
        # slips through as chat; a work turn that concludes in prose
        # releases _task_live (see run()).
        task_live = (any(t.get("status") != "done" for t in self.todos)
                     or (self._task_live and self._task_role))
        if not chat_only and not self.force_role and task_live:
            return self._higher_role(role, self._task_role), False, False, "work"
        return role, conversational, chat_only, ("chat" if chat_only else "work")

    # "use the 8b" / "use the 14b model" / "use the 1.5b" — a size (whole or
    # decimal), mapped to whichever role currently serves a model of that size.
    _MODEL_ASK = re.compile(r"(?i)\buse\s+(?:the\s+)?(\d{1,2}(?:\.\d)?)\s*b\b")

    def _asked_role(self, user_msg):
        m = self._MODEL_ASK.search(user_msg or "")
        if not m:
            return None
        size = m.group(1) + "b"
        for role in ("tiny", "fast", "brain"):
            spec = self.config.models.get(role) or {}
            if size in str(spec.get("model", "")).lower():
                return role
        return None

    _ROLE_RANK = {"tiny": 0, "fast": 1, "brain": 2}

    def _higher_role(self, routed, task_role):
        """The stronger of the freshly-routed role and the sticky task role —
        a follow-up never DOWNGRADES mid-task, but 'now redesign the whole
        architecture' can still escalate past it."""
        if not task_role:
            return routed
        return max(routed, task_role, key=lambda r: self._ROLE_RANK.get(r, 0))

    def _use_native(self, role):
        """Native function calling for this role's model? (config + probe)

        'auto' asks the LLM client whether the served model's template
        supports tools (cached per model); anything that can't answer —
        including stub LLMs in tests — means the portable prompt protocol."""
        if self.tool_protocol == "prompt":
            return False
        if self.tool_protocol == "native":
            return True
        probe = getattr(self.llm, "supports_tools", None)
        if not callable(probe):
            return False
        try:
            return bool(probe(role))
        except Exception:
            return False

    def _turn_temperature(self, user_msg):
        """Warm for conversation, tight for code (a byte-identical prompt either
        way, so Ollama's KV cache is unaffected)."""
        return self._chat_temp() if self._is_conversational(user_msg) \
            else self._task_temp()

    def _stall_reason(self, text, trace):
        """Classify a no-tool-call reply that shouldn't end the turn.

        Returns 'badjson' | 'failed' | 'intent' | 'echo' | 'mimicry' |
        'codedump' | None. None means the reply is a legitimate final answer.
        """
        # Digest mimicry (observed live on the 3B): with a digest in history,
        # the model EMITS a fake '[actions taken this turn]' / '[tool result:'
        # block as its answer instead of acting — anywhere in the reply (live
        # it appeared mid-line, after a parroted prompt example). Checked
        # first — a mimicked digest is a corrupted answer in every mode, plan
        # turns included. GT itself never puts these markers in an answer.
        if "[actions taken this turn]" in text or "[tool result" in text:
            return "mimicry"
        # A reply that contains '"tool"' but parsed to no call is an
        # ATTEMPTED tool call with broken JSON (models truncate long
        # write_file contents) — without this it becomes the 'final answer'
        # as a raw JSON blob. The '"name"' variant is the leaked native
        # function-calling shape (llama-style) with unparseable arguments.
        if "{" in text and ('"tool"' in text
                            or ('"name"' in text
                                and ('"parameters"' in text
                                     or '"arguments"' in text))):
            return "badjson"
        # A plan turn's whole job is prose that ANNOUNCES future work and
        # stops — the intent/failed nudges would shove it into building
        # (observed live: the nudge pushed a model straight into the
        # write_file that plan mode then had to deny).
        if getattr(self, "_plan_turn", False):
            return None
        # A whole file pasted into the CHAT as the "answer" (observed live:
        # a 3B answered a blank-page bug report with a complete replacement
        # HTML file in prose — nothing on disk changed). Only mid-task: a
        # big fence in a from-scratch explanation can be a legitimate answer.
        if (self.todos or trace) and any(
                len(b.strip().splitlines()) >= 15
                for b in _ANYFENCE.findall(text)):
            return "codedump"
        if trace and trace[-1].startswith("- FAILED"):
            return "failed"
        # A plan-pickup turn (the user approved or steered a presented plan)
        # that has executed NOTHING yet — observed live: told to 'start the
        # game' after a plan, the 8B re-described the plan in prose and
        # ended the turn with zero files written. The approval already
        # happened; prose is not an acceptable answer until a tool has run.
        if getattr(self, "_pickup_turn", False) and not trace:
            return "pickup"
        # Verbatim repeat of the previous turn's answer — the 'yes' → same
        # message → 'yes' lock. Compare against the prose part only (the
        # stored history entry may carry an [actions taken] digest).
        if self.history and self.history[-1]["role"] == "assistant":
            prev = self.history[-1]["content"].split(
                "\n\n[actions taken this turn]")[0].strip()
            if prev and text == prev:
                return "echo"
        if _INTENT.search(text):
            return "intent"
        return None

    def _summary_context(self):
        """The rolling session summary as a context message ('' → none)."""
        if not self.session_summary:
            return []
        return [{"role": "user", "content": (
            "[context: session summary — the earlier part of this "
            "conversation, compacted. Treat it as things that already "
            "happened.]\n" + self.session_summary)}]

    def _autocompact(self):
        """Fold the oldest exchanges into the rolling session summary.

        The history bound used to just drop old turns — fast, but the goal
        and decisions from early in a long build vanished with them (failure
        mode #2, context collapse). Now the resident model distills the
        overflow before it goes. Runs at the END of a turn (the user already
        has their answer), at most once every keep_turns exchanges, on the
        ALWAYS-HOT resident model — no swap, no reload. Degrades to the plain
        drop when disabled, unreachable, or Ctrl-C'd.
        """
        # Clamp what's kept verbatim to half the history bound, so a small
        # max_history_turns can't leave keep_turns covering the whole window
        # (overflow would always be empty and compaction would never fire).
        keep = max(2, min(self.keep_turns * 2, self.max_history // 2))
        overflow, kept = self.history[:-keep], self.history[-keep:]
        if not self.compaction_enabled or not overflow:
            self.history = self.history[-self.max_history:]
            return
        self.console.print(f"[dim]· context pressure — compacting "
                           f"{max(1, len(overflow) // 2)} older exchange(s) "
                           f"into the session summary (Ctrl-C skips)[/dim]")
        try:
            summary = self._summarize(overflow)
        except KeyboardInterrupt:
            summary = ""
        if summary:
            self.session_summary = summary
            self.history = kept
            self.console.print(f"[dim]· session summary updated "
                               f"({len(summary)} chars) — /compact shows "
                               f"it[/dim]")
        else:
            # Summarizer offline/skipped: fall back to the old behaviour so a
            # long session still can't bloat the prompt.
            self.history = self.history[-self.max_history:]

    def _summarizer_role(self):
        """Summarize on the RESIDENT model — never a bigger one that would
        evict it from RAM (the reviewer sits on the 3B for the same reason)."""
        if "tiny" in self.config.models:
            return "tiny"
        return self.config.router.get("default", "tiny")

    def _summarize(self, entries):
        """Distill history entries into a short handoff summary ('' on failure)."""
        lines = []
        for m in entries:
            who = "user" if m["role"] == "user" else "gt"
            text = m["content"]
            # History user entries can carry injected [context: ...] blocks —
            # summarize what the user actually typed.
            if who == "user":
                text = text.split("\n\n[user request]\n")[-1]
            if len(text) > 700:
                text = text[:700] + "…"
            lines.append(f"{who}: {text}")
        prev = self.session_summary or "(none)"
        messages = [
            {"role": "system", "content": (
                "You compress the earlier part of a coding session into a "
                "short handoff summary, so the assistant can keep working "
                "without the full transcript. Merge the previous summary "
                "with the new exchanges. Keep ONLY what still matters going "
                "forward: the user's goal and key decisions or constraints; "
                "files created or changed and important commands; what is "
                "DONE versus still OPEN; anything the user corrected or "
                "forbade. Drop pleasantries and dead ends. Output plain "
                "bullet lines only, at most 12, no headings, no preamble.")},
            {"role": "user", "content": (
                f"Previous summary:\n{prev}\n\nNew exchanges to fold in:\n"
                + "\n".join(lines) + "\n\nUpdated summary (bullets only):")},
        ]
        try:
            out = self.llm.chat(self._summarizer_role(), messages,
                                stream=False, temperature=0.2)
        except LLMError:
            return ""
        out = (out or "").strip()
        if len(out) > self.summary_max_chars:
            out = out[:self.summary_max_chars] + "…"
        return out

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
            return sum(len(m.get("content") or "") for m in messages) // 4 > budget
        if not over():
            return False
        compacted = False
        # Tool observations under either protocol: '[tool result:' user
        # messages (prompt-JSON) or role='tool' messages (native).
        tool_idx = [i for i, m in enumerate(messages)
                    if m["role"] == "tool"
                    or (m["role"] == "user"
                        and (m.get("content") or "").startswith("[tool result:"))]
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
            parts.append(f"{m['tps']:.0f} tok/s x {m['tokens']} tok")
        parts.append(f"total {m.get('total_s', 0):.1f}s")
        self.console.print(f"[dim]  time: {' · '.join(parts)}[/dim]")

    def _run_tool(self, call, ctx):
        name = call.get("tool")
        args = call.get("args", {}) or {}
        # Small models sometimes JSON-encode the args dict as a STRING
        # ('"args": "{\\"todos\\": ...}"' — observed live on the 3B). Decode
        # it instead of letting every tool (and hook) choke on a str.
        if isinstance(args, str):
            try:
                parsed = json.loads(args)
                args = parsed if isinstance(parsed, dict) else {}
            except ValueError:
                args = {}
        elif not isinstance(args, dict):
            args = {}
        call["args"] = args      # the digest sees the repaired form too
        tool = self.tools.get(name)
        if not tool:
            self.console.print(f"[red]· unknown tool: {name}[/red]")
            return (f"ERROR: unknown tool '{name}'. Available: "
                    f"{', '.join(self.tools)}")
        # A plan turn only PROPOSES: mutating tools are refused outright, so
        # the plan-first promise holds even when the model ignores the prompt.
        if getattr(self, "_plan_turn", False) and getattr(tool,
                                                          "changes_system",
                                                          False):
            result = (f"DENIED: PLAN MODE is active — this turn only "
                      f"proposes, it never builds. Do not call {name} now; "
                      f"present the plan in plain prose and STOP. The user "
                      f"approves with 'go', and the next turn builds.")
            self._show_step(name, args, result)
            return result
        # pre_tool hooks run FIRST — a standing user rule (exit code 2) vetoes
        # the call before it happens, no matter what the model decided.
        allowed, why = self.hooks.pre_tool(name, args, cwd=self.cwd)
        if not allowed:
            result = (f"DENIED by a pre_tool hook — a standing rule the user "
                      f"configured, not a one-off refusal: {why}\n"
                      f"Do NOT retry the same call; adapt or report it.")
            self._show_step(name, args, result)
            return result
        try:
            result = tool.run(args, ctx)
        except Exception as e:
            result = f"ERROR running {name}: {e}"
        # post_tool hooks can append to what the model sees (lint output,
        # a reminder, a build status) — deterministic feedback, every time.
        extra = self.hooks.post_tool(name, args, result, cwd=self.cwd)
        if extra:
            result = f"{result}\n[post_tool hook output]\n{extra}"
        self._show_step(name, args, result)
        return result

    # What each tool acted on, for the one-line step header (path, cmd, …).
    _STEP_TARGET = ("path", "command", "query", "url", "task", "id", "question")

    def _show_step(self, name, args, result):
        """A clean, minimal trace of one tool step — status, not a code dump.

        The header names the tool and what it touched; the body is a trimmed
        few-line result. File CONTENTS (read_file, write previews) are shown as
        a size, never dumped — that noise is exactly what made GT feel cluttered.
        """
        # The checklist gets its own tidy render — it's the plan, show it whole.
        if name == "write_todos" and self.todos:
            self.console.print(f"[bold {PURPLE}]> write_todos[/bold {PURPLE}]")
            self.console.print(Text(render_todos(self.todos), style="dim"))
            return

        target = next((str(args.get(k)) for k in self._STEP_TARGET
                       if args.get(k)), "")
        if len(target) > 60:
            target = "…" + target[-59:]
        head = f"[bold {PURPLE}]> {name}[/bold {PURPLE}]"
        if target:
            head += f"  [dim]{target}[/dim]"
        self.console.print(head)

        body = (result or "").strip()
        if name == "read_file" and not body.startswith("ERROR"):
            body = f"{body.count(chr(10)) + 1} lines read"
        else:
            lines = body.splitlines()
            if len(lines) > 6 or len(body) > 400:
                body = "\n".join(lines[:6])[:400].rstrip() + "\n…"
        if body:
            self.console.print(Text("   " + body.replace("\n", "\n   "),
                                    style="dim"))

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
                # Skip code/schema/task-list poison even if it's already in the
                # user's memory.db (auto_learn is off now, but old lessons live
                # on) — one such lesson hijacked a small model into building
                # flappy bird on an unrelated question.
                if is_noise_lesson(text):
                    continue
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
            self.console.print(f"[dim]learned: {lesson}[/dim]")
