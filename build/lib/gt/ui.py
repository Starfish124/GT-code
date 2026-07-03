"""Terminal rendering helpers.

streaming_markdown() gives you an on_token callback that live-renders the
model's output as Markdown while it streams in — code blocks, lists, bold, etc.
appear formatted in real time instead of as raw text, and there's no second
"re-render" pass afterward.
"""

from contextlib import contextmanager

from rich.live import Live
from rich.markdown import Markdown


class _Buffer:
    __slots__ = ("text",)

    def __init__(self):
        self.text = ""


@contextmanager
def streaming_markdown(console, refresh_per_second: int = 12,
                       min_chars: int = 24):
    """Context manager yielding (on_token, buffer).

    Feed tokens to on_token; the Live region re-renders the accumulated text as
    Markdown. Re-parsing markdown on every single token is wasteful, so we only
    refresh on a newline or every `min_chars` characters — the stream still
    feels instant but stays cheap for long answers.
    """
    buf = _Buffer()
    state = {"rendered": 0}

    with Live(console=console, auto_refresh=True,
              refresh_per_second=refresh_per_second,
              vertical_overflow="visible") as live:

        def render():
            live.update(Markdown(buf.text) if buf.text.strip() else "")

        def on_token(tok: str):
            buf.text += tok
            if "\n" in tok or (len(buf.text) - state["rendered"]) >= min_chars:
                state["rendered"] = len(buf.text)
                render()

        yield on_token, buf
        render()  # flush the final tail so nothing is left unrendered
