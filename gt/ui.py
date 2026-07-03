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


@contextmanager
def streaming_markdown(console, refresh_per_second: int = 12,
                       min_chars: int = 24, waiting_label: str = "thinking"):
    """Context manager yielding (on_token, buffer).

    Until the first token arrives, a live elapsed counter shows what GT is
    waiting for. Then tokens re-render as Markdown; re-parsing on every token
    is wasteful, so we refresh on newlines or every `min_chars` characters.
    """
    buf = _Buffer()
    state = {"rendered": 0}

    with Live(console=console, auto_refresh=True,
              refresh_per_second=refresh_per_second,
              vertical_overflow="visible") as live:

        live.update(_Waiting(waiting_label))

        def render():
            live.update(Markdown(buf.text) if buf.text.strip() else "")

        def on_token(tok: str):
            buf.text += tok
            if "\n" in tok or (len(buf.text) - state["rendered"]) >= min_chars:
                state["rendered"] = len(buf.text)
                render()

        yield on_token, buf
        render()  # flush the final tail so nothing is left unrendered
