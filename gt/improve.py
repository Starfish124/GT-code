"""Self-improving loop.

After a task completes, the reviewer model reads the interaction and — only
when something actually went wrong or got corrected — distills ONE reusable
lesson. Lessons are stored in vector memory and retrieved automatically on
future, similar requests. This is "learning" via retrieval, not fine-tuning:
cheap, transparent, and fully local.

The gate matters as much as the extraction: a reviewer that "learns" from
every routine turn fills memory with generic self-help ("ask clarifying
questions", "set clear boundaries") that gets recalled into future prompts
and actively steers small models toward bad behavior.
"""

import re

# A "lesson" that is really code, a schema/format instruction, or a task list
# is never useful as recalled guidance — and is actively DANGEROUS. Observed
# live on a small model: a write_todos SCHEMA got saved as a lesson
# ("Use a JSON list of objects with 'task' and 'status' properties… Example:
# [{'task': 'Create a flappy bird'…}]"), then recall surfaced it at 0.45 on
# the unrelated question "what models are available?" and the model latched
# onto the flappy-bird example and built flappy bird instead of answering.
# We reject this shape on SAVE and skip it on RECALL, so existing poison in a
# user's memory.db stops derailing turns even before a manual /forget lesson.
_CODE_SHAPE = re.compile(r"```|\{[^}\n]*\}|\[[^\]\n]*\]", re.S)
_SCHEMA_TALK = re.compile(
    r"(?i)list of objects|as shown in|propert(?:y|ies)\b|\bschema\b"
    r"|'?(?:task|status)'?\s*:")


def is_noise_lesson(text: str) -> bool:
    """True for 'lessons' that are code / JSON / a schema directive / a task
    list — reject on save, skip on recall."""
    if not text or len(text.strip()) < 8:
        return True
    return bool(_CODE_SHAPE.search(text) or _SCHEMA_TALK.search(text))


class Improver:
    def __init__(self, llm, memory, reviewer_role="reviewer"):
        self.llm = llm
        self.memory = memory
        self.reviewer_role = reviewer_role

    # Lessons matching these are generic slogans, not learnings — never store.
    _BANNED = ("clarify", "clarifying", "clear boundaries", "communicate",
               "best practice", "be specific", "specific details",
               "user experience", "double-check", "always ask")

    def learn(self, user_msg, assistant_msg, trace=()):
        """Extract and store a lesson. Returns the lesson text, or None."""
        actions = "\n".join(trace)[:1200] if trace else "(no tools were used)"
        messages = [
            {
                "role": "system",
                "content": (
                    "You maintain the lesson memory of an AI coding assistant. "
                    "Most interactions teach NOTHING new — your default answer "
                    "is exactly: NONE\n"
                    "Extract ONE lesson only when the transcript shows "
                    "something concretely going wrong: a tool error, a failed "
                    "or timed-out command, the user correcting the assistant, "
                    "or a workaround that was discovered. The lesson must name "
                    "the concrete situation and the fix (e.g. 'Use Vite "
                    "instead of create-react-app — CRA times out'). NEVER "
                    "output generic advice about asking questions, clarity, "
                    "boundaries, or communication. NEVER output JSON, code, "
                    "brackets, a schema, or a task list — those are not lessons. "
                    "Write a single imperative sentence, or exactly: NONE"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"User request:\n{user_msg[:1500]}\n\n"
                    f"Tools the assistant ran (and how they went):\n{actions}\n\n"
                    f"Assistant's final answer:\n{assistant_msg[:1500]}\n\n"
                    "Lesson:"
                ),
            },
        ]
        lesson = self.llm.chat(
            self.reviewer_role, messages, stream=False, temperature=0.2
        ).strip()

        # Reject empties / refusals / rambles / generic slogans.
        if not lesson or "NONE" in lesson.upper()[:8] or len(lesson) > 400:
            return None
        low = lesson.lower()
        if any(b in low for b in self._BANNED):
            return None
        # Reject code / schema / task-list "lessons" (the flappy-bird poison).
        if is_noise_lesson(lesson):
            return None

        # Skip near-duplicates of an existing lesson.
        existing = self.memory.search(lesson, k=1, kinds=["lesson"])
        if existing and existing[0][0] > 0.93:
            return None

        self.memory.add(lesson, kind="lesson", source="self-improve")
        return lesson
