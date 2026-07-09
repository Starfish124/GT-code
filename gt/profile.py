"""User preference profile — GT learns your habits over time.

A dedicated analyst model (hermes3:3b) periodically reads the session's requests
and distills a short, durable profile of the user's preferences: favoured stack,
language, tone, naming conventions, recurring tasks, dislikes. GT injects a
couple of lines of that profile as context so its defaults match the user
without being told every time.

Design constraints that keep it from undoing the speed work:
  - It runs ONLY periodically — when you exit, or on demand via /profile update
    (and optionally every N turns) — NEVER per turn. The analyst is a different
    model from the resident 3B, so running it costs a one-time load; doing that
    per turn would reintroduce the model-swap churn GT is built to avoid.
  - It's glass-box: the profile is plain readable JSON on disk, shown and wiped
    with /profile, and it no-ops cleanly if hermes3:3b isn't pulled.

This is retrieval/prompt learning (like the reviewer), NOT weight training —
tested with the offline smoke suite + the live harness, not the LoRA pipeline.
The profile it collects could later become training data for the LoRA track.
"""

import json

from .llm import LLMError

# Lines the analyst shouldn't be storing — generic advice, not preferences.
_BANNED = ("clean code", "best practice", "clear communication", "clarify",
           "be specific", "good code", "high quality", "the assistant")

# The whole output reduces to "nothing learned" — treat as no change.
_EMPTY_SIGNALS = ("no durable", "no specific", "no clear pref", "no preference",
                  "not enough", "none ", "nothing ")


class Profiler:
    """Learns + stores a short preference profile using the analyst model."""

    def __init__(self, llm, config, path, role="analyst"):
        self.llm = llm
        self.config = config
        self.path = path
        self.role = role
        cfg = config.data.get("profile", {}) if hasattr(config, "data") else {}
        self.max_obs = int(cfg.get("max_observations", 8))

    # ---- storage ------------------------------------------------------------

    def load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
        return {"observations": [], "turns_analyzed": 0}

    def summary(self) -> str:
        """The profile as bullet lines for prompt injection ('' if empty)."""
        obs = self.load().get("observations", [])
        return "\n".join(f"- {o}" for o in obs[:self.max_obs])

    def clear(self):
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    def _save(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    # ---- availability -------------------------------------------------------

    def model_id(self) -> str:
        try:
            return self.config.model_for(self.role)["model"]
        except Exception:
            return "hermes3:3b"

    def available(self) -> bool:
        """Is the analyst model actually pulled? (No point running otherwise.)

        Matches the FULL id (hermes3:3b), so hermes3:8b does NOT count — we want
        the specific small analyst, not whatever hermes happens to be served.
        """
        want = self.model_id().lower()
        try:
            served = self.llm.list_models(self.config.provider_base("ollama"))
        except Exception:
            return False
        return any(want in s.lower() for s in served)

    # ---- the analysis pass --------------------------------------------------

    def update(self, session_log):
        """Distill the session into the profile and save it.

        session_log: list of {"user": str, "outcome": str}. Returns
        (observations, message) — observations is the new profile list.
        """
        if not session_log:
            return self.load().get("observations", []), "nothing to analyze yet."
        if not self.available():
            m = self.model_id()
            return (self.load().get("observations", []),
                    f"the analyst model '{m}' isn't pulled — run: "
                    f"ollama pull {m}  to enable preference learning.")

        current = self.load()
        obs = current.get("observations", [])
        transcript = "\n".join(f"- {t.get('user', '')[:200]}"
                               for t in session_log[-40:])
        existing = "\n".join(f"- {o}" for o in obs) or "(none yet)"
        try:
            out = self.llm.chat(self.role, self._messages(existing, transcript),
                                stream=False, temperature=0.2)
        except LLMError as e:
            return obs, f"analyst unavailable: {e}"

        new_obs = self._parse(out)
        merged = (new_obs or obs)[:self.max_obs]
        current["observations"] = merged
        current["turns_analyzed"] = current.get("turns_analyzed", 0) + len(session_log)
        self._save(current)
        return merged, f"profile updated — {len(merged)} preference(s) on file."

    def _messages(self, existing, transcript):
        return [
            {"role": "system", "content": (
                "You maintain a concise preference profile of ONE developer using "
                "a local coding assistant. From this session's requests, capture "
                "DURABLE preferences and habits: favoured programming languages, "
                "frameworks / stack, tone (terse vs detailed), naming conventions, "
                "the kinds of tasks they do, and clear dislikes. Merge with the "
                "existing profile — keep what still holds, add what's new. Output "
                "ONLY a short bulleted list, one concrete preference per line, at "
                "most 8 lines. NEVER output generic advice like 'write clean code' "
                "or anything about the assistant itself. If the session shows "
                "nothing durable, repeat the existing profile unchanged.")},
            {"role": "user", "content": (
                f"Existing profile:\n{existing}\n\n"
                f"This session's requests:\n{transcript}\n\n"
                "Updated profile (bulleted lines only):")},
        ]

    def _parse(self, text):
        text = (text or "").strip()
        # A one-liner that just says "nothing learned" means: no change.
        if len(text.splitlines()) <= 1 and any(s in text.lower()
                                               for s in _EMPTY_SIGNALS):
            return []
        obs = []
        for line in text.splitlines():
            line = line.strip().lstrip("-*•").strip()
            if not line or len(line) > 160:
                continue
            low = line.lower()
            if any(b in low for b in _BANNED):
                continue
            if any(s in low for s in _EMPTY_SIGNALS):
                continue
            if low in (o.lower() for o in obs):
                continue
            obs.append(line)
            if len(obs) >= self.max_obs:
                break
        return obs
