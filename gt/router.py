"""Smart model routing.

Cheap heuristics first (instant), then a one-word classification from the tiny
3B model to decide whether a request needs the heavy 'brain' model or can be
served fast. Falls back to the configured default on any error.
"""

import re

# Obvious small talk -> tiny model, no LLM call needed.
_SMALL_TALK = re.compile(
    r"^\s*(hi|hey|hello|yo|sup|thanks|thank you|ty|ok|okay|cool|nice|"
    r"good morning|good night|bye|gm|gn)\b",
    re.I,
)
# Strong signals the request is real coding/agentic work -> brain, skip classifier.
_CODE_HINT = re.compile(
    r"\b(code|bug|error|stack ?trace|function|class|refactor|implement|"
    r"compile|test|install|run|file|repo|git|debug|api|regex|script|"
    r"\.py|\.js|\.ts|\.rs|\.go|\.java|\.c|\.cpp|\.sh|traceback)\b",
    re.I,
)


class Router:
    def __init__(self, llm, config, console=None):
        self.llm = llm
        self.config = config
        self.console = console
        self.enabled = config.router.get("enabled", True)
        self.default_role = config.router.get("default", "brain")

    def route(self, user_msg) -> str:
        if not self.enabled:
            return self.default_role

        text = (user_msg or "").strip()
        if not text:
            return self.default_role

        # --- heuristic fast-paths (no LLM call) ---
        if len(text) < 40 and _SMALL_TALK.search(text):
            return "tiny"
        if len(text) > 240 or _CODE_HINT.search(text):
            return "brain"

        # --- tiny-model classifier for the ambiguous middle ---
        try:
            label = self._classify(text)
        except Exception:
            return self.default_role
        return {"simple": "fast", "code": "brain", "complex": "brain"}.get(
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
