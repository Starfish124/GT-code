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
                    "boundaries, or communication. "
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

        # Skip near-duplicates of an existing lesson.
        existing = self.memory.search(lesson, k=1, kinds=["lesson"])
        if existing and existing[0][0] > 0.93:
            return None

        self.memory.add(lesson, kind="lesson", source="self-improve")
        return lesson
