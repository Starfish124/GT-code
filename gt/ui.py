"""Terminal rendering helpers.

streaming_markdown() gives you an on_token callback that live-previews the
model's output while it streams in, then prints the complete reply once at the
end. The live preview is deliberately a small, PLAIN-TEXT tail: re-parsing the
whole reply as Markdown a dozen times a second is O(n²), makes the region
re-layout every tick (the "page flicker" while GT codes), and floods Windows
consoles once the buffer is taller than the viewport. Markdown is rendered
exactly once, when the reply is complete.

The `collapse` hook is how tool-step replies stay compact (the Claude Code
look): the caller inspects the finished text and returns None to print it in
full (a real answer), or a short summary line to print instead (an
intermediate step whose code/JSON the user never needs to scroll past —
the "> tool" step line that follows carries the outcome).
"""

import time
from contextlib import contextmanager

from rich.live import Live
from rich.markdown import Markdown
from rich.text import Text

from .interrupt import esc_interrupts
from .theme import CODE_THEME


class _Buffer:
    __slots__ = ("text",)

    def __init__(self):
        self.text = ""


class _Waiting:
    """Renders a live status with an elapsed counter — Live's auto-refresh
    re-calls __rich__ every tick, so the seconds count up on screen while the
    model loads / prefills the prompt before the first token. The message names
    the stage so a long wait is explained, not just spinning."""

    _FRAMES = "|/-\\"          # plain ASCII, renders everywhere (no emoji)

    def __init__(self, label: str):
        self.label = label
        self.t0 = time.perf_counter()

    def __rich__(self) -> Text:
        elapsed = time.perf_counter() - self.t0
        frame = self._FRAMES[int(elapsed * 4) % len(self._FRAMES)]
        if elapsed < 3:
            hint = ""
        elif elapsed < 12:
            hint = "  (loading the model into RAM — one-time per boot, then instant)"
        else:
            hint = "  (large prompt on CPU is slow — this is prompt-reading, not stuck)"
        return Text(f"{frame} {self.label} — {elapsed:.1f}s{hint}"
                    f"  · esc interrupts", style="dim")


# How much of the in-progress reply the live preview shows: a few lines, like
# a status window — not the whole reply scrolling by. Small on purpose (see
# module docstring: flicker, O(n²) re-parsing, Windows console flooding).
_TAIL_LINES = 6
_TAIL_CHARS = 600


def _tail_text(full: str) -> Text:
    lines = full[-2000:].splitlines()[-_TAIL_LINES:]
    tail = "\n".join(lines)[-_TAIL_CHARS:]
    head = "… " if len(full) > len(tail) else ""
    t = Text(head + tail, style="dim")
    t.append("\n· generating — esc interrupts (work so far is kept)",
             style="dim italic")
    return t


@contextmanager
def streaming_markdown(console, refresh_per_second: int = 10,
                       min_chars: int = 24, waiting_label: str = "thinking",
                       collapse=None):
    """Context manager yielding (on_token, buffer).

    Until the first token arrives, a live elapsed counter shows what GT is
    waiting for. While streaming, only a small plain-text TAIL of the reply is
    shown in a transient live region; when the reply is complete the preview
    vanishes and the text is printed once — in full as Markdown, unless
    `collapse(text)` returns a summary line to print instead ('' = nothing).
    """
    buf = _Buffer()
    state = {"rendered": 0}

    try:
        # Esc stops the generation (same handling as Ctrl-C) — active only
        # while the model is talking, so it can never eat a keystroke meant
        # for a permission prompt.
        with esc_interrupts(), Live(console=console, auto_refresh=True,
                                    refresh_per_second=refresh_per_second,
                                    transient=True) as live:

            live.update(_Waiting(waiting_label))

            def on_token(tok: str):
                buf.text += tok
                if "\n" in tok or (len(buf.text) - state["rendered"]) >= min_chars:
                    state["rendered"] = len(buf.text)
                    live.update(_tail_text(buf.text))

            yield on_token, buf
    finally:
        # The transient preview is gone — print the reply once. Runs on
        # errors/Ctrl-C too, so a partial reply is never lost.
        if buf.text.strip():
            summary = collapse(buf.text) if collapse else None
            if summary is None:
                console.print(Markdown(buf.text, code_theme=CODE_THEME))
            elif summary:
                console.print(Text(summary, style="dim"))
