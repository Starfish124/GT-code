"""Self-improving loop.

After a task completes, the reviewer model (Hermes) reads the interaction and
tries to distill ONE reusable lesson — a generalizable heuristic that would help
GT next time. Lessons are stored in vector memory and retrieved automatically on
future, similar requests. This is "learning" via retrieval, not fine-tuning:
cheap, transparent, and fully local.
"""


class Improver:
    def __init__(self, llm, memory, reviewer_role="reviewer"):
        self.llm = llm
        self.memory = memory
        self.reviewer_role = reviewer_role

    def learn(self, user_msg, assistant_msg):
        """Extract and store a lesson. Returns the lesson text, or None."""
        messages = [
            {
                "role": "system",
                "content": (
                    "You improve an AI coding assistant by extracting ONE short, "
                    "reusable lesson from an interaction — a general heuristic that "
                    "would help it handle similar requests better next time. "
                    "Write a single imperative sentence (start with a verb). "
                    "Do NOT restate what happened; generalize it. "
                    "If nothing is worth generalizing, reply with exactly: NONE"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"User request:\n{user_msg[:1500]}\n\n"
                    f"Assistant's final answer:\n{assistant_msg[:1500]}\n\n"
                    "Lesson:"
                ),
            },
        ]
        lesson = self.llm.chat(
            self.reviewer_role, messages, stream=False, temperature=0.2
        ).strip()

        # Reject empties / refusals / overlong rambles.
        if not lesson or "NONE" in lesson.upper()[:8] or len(lesson) > 400:
            return None

        # Skip near-duplicates of an existing lesson.
        existing = self.memory.search(lesson, k=1, kinds=["lesson"])
        if existing and existing[0][0] > 0.93:
            return None

        self.memory.add(lesson, kind="lesson", source="self-improve")
        return lesson
