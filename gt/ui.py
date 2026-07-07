"""Terminal rendering helpers.

streaming_markdown() gives you an on_token callback that live-renders the
model's output as Markdown while it streams in — code blocks, lists, bold, etc.
appear formatted in real time instead of as raw text, and there's no second
"re-render" pass afterward.
"""

import time
from contextlib import contextmanager

from rich.live import Live
from rich.markdown import Markdown
from rich.text import Text


class _Buffer:
    __slots__ = ("text",)

    def __init__(self):
        self.text = ""


class _Waiting:
    """Renders 'waiting…' with a live elapsed counter — Live's auto-refresh
    re-calls __rich__ every tick, so the seconds count up on screen while
    the model loads / prefills before the first token."""

    _FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, label: str):
        self.label = label
        self.t0 = time.perf_counter()

    def __rich__(self) -> Text:
        elapsed = time.perf_counter() - self.t0
        frame = self._FRAMES[int(elapsed * 8) % len(self._FRAMES)]
        hint = "  (loading the model into RAM — first call costs this once)" \
            if elapsed > 4 else ""
        return Text(f"{frame} {self.label} — {elapsed:.1f}s{hint}", style="dim")


# How much of the in-progress reply the live preview shows. Small on purpose:
# repainting a buffer taller than the terminal floods Windows consoles (they
# can't redraw above the viewport, so every refresh appends a full duplicate
# copy of the reply), and re-parsing all of it as Markdown 12×/s is O(n²).
_TAIL_CHARS = 1200


@contextmanager
def streaming_markdown(console, refresh_per_second: int = 12,
                       min_chars: int = 24, waiting_label: str = "thinking"):
    """Context manager yielding (on_token, buffer).

    Until the first token arrives, a live elapsed counter shows what GT is
    waiting for. While streaming, only the TAIL of the reply is shown in a
    transient live region; when the reply is complete the preview vanishes
    and the full text is rendered once, properly, as Markdown.
    """
    buf = _Buffer()
    state = {"rendered": 0}

    try:
        with Live(console=console, auto_refresh=True,
                  refresh_per_second=refresh_per_second,
                  transient=True) as live:

            live.update(_Waiting(waiting_label))

            def render():
                tail = buf.text[-_TAIL_CHARS:]
                if len(buf.text) > _TAIL_CHARS:
                    nl = tail.find("\n")
                    tail = "…\n" + (tail[nl + 1:] if nl != -1 else tail)
                live.update(Markdown(tail) if tail.strip() else "")

            def on_token(tok: str):
                buf.text += tok
                if "\n" in tok or (len(buf.text) - state["rendered"]) >= min_chars:
                    state["rendered"] = len(buf.text)
                    render()

            yield on_token, buf
    finally:
        # The transient preview is gone — print the complete reply once.
        # Runs on errors/Ctrl-C too, so a partial reply is never lost.
        if buf.text.strip():
            console.print(Markdown(buf.text))
