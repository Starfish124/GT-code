"""Smart model routing — 3B-first speed ladder.

    tiny (3B)  → the RESIDENT DEFAULT: routing, small talk,   (instant — one
                 questions, quick coding. Stays hot in RAM.    model, no swaps)
    fast (8B)  → escalation for a substantial coding task      (worth a load)
    brain (14B)→ genuine reasoning: architecture, planning,    (worth a load;
                 building a whole app from scratch              → 8B on slow HW)

The thing that makes a local agent feel slow is SWAPPING models: each swap
costs 10-60s of (re)loading before the first token, and on a CPU/iGPU box that
can only hold one model at a time, mixing a 3B router with an 8B answerer means
every turn reloads. So GT keeps ONE small 3B resident and answers almost
everything with it — routing, chat, and everyday coding all hit the same hot
model, so there are no swaps. The 8B/14B load only when a request genuinely
needs them (a real build or deep plan). Cheap regex decides instantly; only a
substantial, ambiguous request spends one word on the 3B classifier to decide
whether it's worth escalating.
"""

import re

from . import machine

# Obvious small talk -> tiny model, no LLM call needed.
_SMALL_TALK = re.compile(
    r"^\s*(hi|hey|hello|yo|sup|thanks|thank you|ty|ok|okay|cool|nice|"
    r"good morning|good night|bye|gm|gn)\b",
    re.I,
)
# Signals that a message is real work (files, code, docs) rather than chat —
# drives temperature + whether engineering playbooks load. High-precision
# tokens only: nouns, file extensions and office types, so plain chit-chat
# ("read any good books?", "who are you?") is NOT swept in. Bare verbs like
# "read/open/show" are deliberately excluded for that reason.
_CODE_HINT = re.compile(
    r"\b(code|bug|error|stack ?trace|function|class|refactor|implement|"
    r"compile|test|install|run|files?|folders?|director(y|ies)|repo|git|"
    r"debug|api|regex|script|"
    r"frontend|backend|website|web ?app|webpage|server|database|deploy|"
    r"host(ing)?|html|css|react|vue|svelte|node|python|"
    r"excel|spreadsheet|powerpoint|slides?|\bdeck\b|word doc(ument)?|"
    r"\.py|\.js|\.ts|\.rs|\.go|\.java|\.c|\.cpp|\.sh|"
    r"\.ya?ml|\.json|\.md|\.txt|\.toml|\.ini|\.cfg|\.xml|\.csv|\.log|\.env|"
    r"\.html|\.css|\.xlsx|\.pptx|\.docx|traceback)\b",
    re.I,
)
# "Make me a <thing>" — a concrete build request. A creation verb anywhere
# ahead of a known deliverable in the same sentence. Deliberately tolerant of
# the article ("a/an/the/this/my/another/…" or none) and of words in between,
# because "create THE famous game called flappy bird" is every bit a build as
# "make me a todo app" — the rigid old pattern missed exactly that and dropped
# it onto the 3B as small talk (the flappy-bird bug).
_BUILD_HINT = re.compile(
    r"\b(make|build|create|write|implement|develop|generate|program|"
    r"scaffold|set ?up|put together|whip up|spin up|clone|recreate|remake)\b"
    r"[^.?!]*?\b(app|application|web ?site|web ?page|landing page|page|"
    r"frontend|back ?end|full[- ]?stack|api|service|micro-?service|game|tool|"
    r"cli|dashboard|bot|script|program|server|database|db|extension|plugin|"
    r"feature|function|class|component|module|form|endpoint|ui|clone|"
    r"prototype|widget|calculator|to-?do|chat ?bot|scraper|crawler|"
    r"spreadsheet|excel|powerpoint|deck|slides?|word doc(ument)?)s?\b",
    re.I,
)
# Signals that real REASONING is needed -> brain 14B is worth its load time.
# Checked BEFORE _CODE_HINT: starting something new is a planning task even
# when it mentions code words ("make a simple frontend and backend").
_PLAN_HINT = re.compile(
    r"\b(architect(ure)?|blueprint|from scratch|new (app|project|"
    r"service|platform)|overhaul|rewrite (the )?(whole|entire)|"
    r"migrate|restructure|strategy|trade-?offs?|compare .* approaches|"
    r"full[- ]?stack platform|design the (architecture|system))\b",
    re.I,
)


class Router:
    def __init__(self, llm, config, console=None):
        self.llm = llm
        self.config = config
        self.console = console
        self.enabled = config.router.get("enabled", True)
        # The resident 3B handles everyday turns; only escalate when needed.
        self.default_role = config.router.get("default", "tiny")
        # Longer, ambiguous requests are worth one classifier word to decide
        # whether they should escalate off the 3B. Short turns never pay it.
        self.escalate_len = int(config.router.get("escalate_len", 160))

        # On a CPU-only machine a 14B crawls (the corporate-laptop case), so
        # prefer the 8B: route work that would go to 'brain' to 'fast' instead.
        # Detected once at startup; /model brain still forces the 14B.
        self.prefer_fast = False
        self.slow_hw = None
        if config.router.get("prefer_fast_on_slow", True):
            try:
                hw = machine.probe()
                if machine.slow_for_large_models(hw):
                    self.prefer_fast = True
                    self.slow_hw = hw
            except Exception:
                pass

    def route(self, user_msg) -> str:
        return self._cap(self._pick(user_msg))

    def _cap(self, role) -> str:
        """On a slow (CPU-only) box the 8B AND 14B both crawl — single-digit
        tok/s, minutes per turn (a real transcript showed the 8B at 2 tok/s with
        a 4.5-minute prefill). There's no usable escalation there, so keep
        EVERYTHING on the resident 3B for responsiveness. /model fast|brain still
        forces a bigger (slower) model when the user is willing to wait."""
        if self.prefer_fast and role in ("brain", "fast") \
                and "tiny" in self.config.models:
            return "tiny"
        return role

    def _pick(self, user_msg) -> str:
        if not self.enabled:
            return self.default_role

        text = (user_msg or "").strip()
        if not text:
            return self.default_role

        # --- heuristic fast-paths (no LLM call) ---
        # Small talk stays on the resident 3B.
        if len(text) < 40 and _SMALL_TALK.search(text):
            return "tiny"
        # Architecture / planning / a very long spec earns the 14B; a concrete
        # "make me a <thing>" build earns a strong coding model too. Both are
        # capped back to the resident 3B on a slow box (see _cap).
        if _PLAN_HINT.search(text) or _BUILD_HINT.search(text) or len(text) > 600:
            return "brain"

        # Everything else — quick questions, small fixes, everyday coding —
        # answers on the always-hot 3B. That's the whole point: no model swap,
        # no reload, instant. Only a *substantial* ambiguous request is worth
        # a classifier word to decide whether it should escalate off the 3B.
        if len(text) <= self.escalate_len:
            return self.default_role
        try:
            label = self._classify(text)
        except Exception:
            return self.default_role
        # 'simple' quick answers stay resident; a big coding job earns the 8B;
        # something genuinely multi-step earns the brain.
        return {"simple": self.default_role, "code": "fast",
                "complex": "brain"}.get(label, self.default_role)

    def _classify(self, text) -> str:
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a request router. Classify the user's message into "
                    "EXACTLY one lowercase word:\n"
                    "  simple  = small talk or a quick factual question\n"
                    "  code    = programming, files, commands, debugging\n"
                    "  complex = multi-step reasoning, analysis, or planning\n"
                    "Reply with ONLY that one word, nothing else."
                ),
            },
            {"role": "user", "content": text[:1000]},
        ]
        out = self.llm.chat("tiny", messages, stream=False, temperature=0).lower()
        for label in ("simple", "code", "complex"):
            if label in out:
                return label
        return "complex"
