"""Confidence-gated planning — weigh the readings before building anything.

Roadmap step 6, and the user's own idea: "multi-variant reasoning /
probability weighting / what's assigned vs not". The flappy-bird class of
failure isn't only protocol noise — it's an agent EXECUTING a request it has
misread, at full speed. Bias-to-action is right for clear requests and wrong
for ambiguous ones; the trick is knowing which one you're holding.

Before a NEW build-ish task starts, GT spends one small model call asking:
what are the plausible READINGS of this request, what did the user actually
SPECIFY versus leave to defaults, and how confident are we in the main
reading? The returned confidence then routes deterministically:

    confidence >= min_confidence   -> build now (bias-to-action unchanged)
    ask_below..min_confidence      -> plan first, wait for the user's "go"
    < ask_below (with a question)  -> ask ONE clarifying question, then build

Guard rails so this never becomes the question-spam GT already cured:
  - fires ONLY in auto mode, on work turns that look like a NEW build
    (creation-verb requests or very long specs) — never on chat, quick
    tasks ("read config.yaml"), mid-task turns (todos active), or forced
    /mode code|plan sessions (those are explicit user intent);
  - at most ONE question, and only when the model actually produced one;
  - fail-open everywhere: gate offline / output unparseable -> build as
    before. The gate may only add care, never block work.
"""

import re
from dataclasses import dataclass

from .llm import LLMError

GATE_SYSTEM = """You triage ONE request that is about to be handed to a \
coding agent which builds things immediately. Judge how unambiguous the \
request is BEFORE any work starts.

Think it through: what are the 2-3 plausible READINGS of the request? What \
did the user SPECIFY (assigned), and what is left open (defaulted)? Open \
details with obvious defaults (stack, file names, ports) do NOT make a \
request ambiguous — the agent picks sensible defaults. A request is \
ambiguous only when the readings lead to GENUINELY DIFFERENT builds and \
picking the wrong one wastes real work.

Reply in EXACTLY this format — three lines, nothing else:
confidence: <0-100 — how sure you are that the main reading is what the user wants>
reading: <one line — the main reading of the request>
question: <the ONE short question that best resolves the ambiguity, or: none>"""


@dataclass
class Assessment:
    confidence: int
    reading: str
    question: str


_CONF = re.compile(r"(?im)^\s*confidence\s*[:=]\s*(\d{1,3})")
_READ = re.compile(r"(?im)^\s*reading\s*[:=]\s*(.+)$")
_QUES = re.compile(r"(?im)^\s*question\s*[:=]\s*(.+)$")

# Short go-aheads that turn a gated plan into a build on the next turn.
# Deliberately NOT "make it": "make it a website instead" is a redirect.
AFFIRM = re.compile(
    r"(?i)^\s*(go( ahead)?|yes|yep|yeah|ok(ay)?|do it|build it|"
    r"proceed|start|ship it|sounds good|looks good|lgtm|ga)\b.{0,60}$")


class IntentGate:
    def __init__(self, llm, config, console):
        cfg = (getattr(config, "data", {}) or {}).get("intent_gate", {}) or {}
        self.llm = llm
        self.console = console
        self.enabled = bool(cfg.get("enabled", True))
        self.min_confidence = int(cfg.get("min_confidence", 75))
        self.ask_below = int(cfg.get("ask_below", 45))
        self.gate_len = int(cfg.get("gate_len", 240))

    def should_gate(self, user_msg, conversational, mode, todos) -> bool:
        """Gate only a NEW, build-shaped task in auto mode.

        Forced modes are explicit user intent (/mode code = "just build"),
        an active checklist means we're mid-task, and conversation/quick
        work requests aren't builds — none of those pay the gate.
        """
        if not self.enabled or mode != "auto" or conversational or todos:
            return False
        from .router import _BUILD_HINT, _PLAN_HINT
        text = (user_msg or "").strip()
        if _PLAN_HINT.search(text):
            return False        # the user asked for planning — no gate needed
        return bool(_BUILD_HINT.search(text)) or len(text) >= self.gate_len

    def assess(self, user_msg, role):
        """One small triage call -> Assessment, or None (fail-open)."""
        try:
            out = self.llm.chat(role, [
                {"role": "system", "content": GATE_SYSTEM},
                {"role": "user", "content": user_msg}],
                stream=False, temperature=0.0)
        except LLMError:
            return None
        m = _CONF.search(out or "")
        if not m:
            return None
        confidence = max(0, min(100, int(m.group(1))))
        reading = (_READ.search(out).group(1).strip()
                   if _READ.search(out) else "")
        question = (_QUES.search(out).group(1).strip()
                    if _QUES.search(out) else "")
        if question.lower().rstrip(".!") in ("none", "n/a", "no", "-", ""):
            question = ""
        return Assessment(confidence, reading[:200], question[:300])

    def decide(self, a) -> str:
        """'build' | 'plan' | 'ask' from the confidence thresholds."""
        if a.confidence >= self.min_confidence:
            return "build"
        if a.confidence < self.ask_below and a.question:
            return "ask"
        return "plan"
