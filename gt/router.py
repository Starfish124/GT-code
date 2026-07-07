"""Smart model routing — the speed ladder.

    tiny (3B)  → routes requests + small talk         (instant)
    fast (8B)  → the DEFAULT workhorse: everyday      (snappy, stays hot
                 coding, edits, tool loops             in RAM)
    brain (14B)→ genuine reasoning only: architecture,
                 planning, complex multi-step design   (worth the load time)

Sending everything to the 14B is what makes a local agent feel slow: each
swap can cost 10-60s of model (re)loading before the first token. So the 8B
does the work by default and the 14B is reserved for requests that actually
need deep reasoning. Cheap regex heuristics decide instantly; only the
ambiguous middle costs one word from the 3B classifier.
"""

import re

# Obvious small talk -> tiny model, no LLM call needed.
_SMALL_TALK = re.compile(
    r"^\s*(hi|hey|hello|yo|sup|thanks|thank you|ty|ok|okay|cool|nice|"
    r"good morning|good night|bye|gm|gn)\b",
    re.I,
)
# Signals of everyday coding/agentic work -> fast 8B, skip the classifier.
_CODE_HINT = re.compile(
    r"\b(code|bug|error|stack ?trace|function|class|refactor|implement|"
    r"compile|test|install|run|file|repo|git|debug|api|regex|script|"
    r"frontend|backend|website|web ?app|webpage|server|database|deploy|"
    r"host(ing)?|html|css|react|vue|svelte|node|python|"
    r"\.py|\.js|\.ts|\.rs|\.go|\.java|\.c|\.cpp|\.sh|traceback)\b",
    re.I,
)
# Signals that real REASONING is needed -> brain 14B is worth its load time.
# Checked BEFORE _CODE_HINT: starting something new is a planning task even
# when it mentions code words ("make a simple frontend and backend").
_PLAN_HINT = re.compile(
    r"\b(architect(ure)?|design|plan|blueprint|from scratch|new (app|project|"
    r"service|platform)|build me|overhaul|rewrite (the )?(whole|entire)|"
    r"migrate|restructure|complex|strategy|trade-?offs?|compare .* approaches|"
    r"full-?stack|"
    r"(make|build|create|write)\s+(me\s+)?an?\s+(\w+[- ]){0,3}?(app|application|"
    r"website|site|page|frontend|backend|api|service|game|tool|dashboard|bot)s?)\b",
    re.I,
)


class Router:
    def __init__(self, llm, config, console=None):
        self.llm = llm
        self.config = config
        self.console = console
        self.enabled = config.router.get("enabled", True)
        self.default_role = config.router.get("default", "fast")

    def route(self, user_msg) -> str:
        if not self.enabled:
            return self.default_role

        text = (user_msg or "").strip()
        if not text:
            return self.default_role

        # --- heuristic fast-paths (no LLM call) ---
        if len(text) < 40 and _SMALL_TALK.search(text):
            return "tiny"
        if _PLAN_HINT.search(text) or len(text) > 600:
            return "brain"
        if _CODE_HINT.search(text):
            return "fast"

        # --- tiny-model classifier for the ambiguous middle ---
        try:
            label = self._classify(text)
        except Exception:
            return self.default_role
        return {"simple": "fast", "code": "fast", "complex": "brain"}.get(
            label, self.default_role
        )

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
