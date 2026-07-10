"""Esc-to-interrupt — stop a running reply with one key, like Claude Code.

While the model is generating, a tiny daemon thread watches the keyboard; a
standalone Esc press raises KeyboardInterrupt in the MAIN thread (see _fire),
which lands in the exact same handling as Ctrl-C — the turn stops and the
work done so far is kept. Ctrl-C still works everywhere; Esc is the lighter
reflex.

Scoped deliberately to GENERATION only (ui.streaming_markdown wraps itself in
it): the watcher consumes keystrokes, so it must never be active while GT is
actually asking the user something (permission prompts, ask_user) — those all
happen between generations, after a reply has been parsed.

Fail-open by design: stdin not a real terminal (tests, pipes), an exotic
console, any error at all -> the watcher silently doesn't run and Ctrl-C
remains the way to interrupt.
"""

import _thread
import os
import signal
import sys
import threading
import time
from contextlib import contextmanager

_POLL = 0.05     # seconds between keyboard checks — instant to a human


def _fire():
    """Interrupt the main thread NOW.

    On POSIX a real SIGINT is delivered TO the main thread (pthread_kill),
    which EINTRs even a blocking network read — Esc works mid-prefill, when
    no tokens are flowing and no bytecode is running. Windows has no
    pthread_kill; interrupt_main() lands at the next bytecode boundary
    (i.e. with the next token), same as Ctrl-C behaves there."""
    try:
        signal.pthread_kill(threading.main_thread().ident, signal.SIGINT)
    except Exception:
        _thread.interrupt_main()


def _watch_windows(stop):
    import msvcrt
    while not stop.is_set():
        while msvcrt.kbhit():
            ch = msvcrt.getwch()
            if ch in ("\x00", "\xe0"):       # arrow/function keys arrive as
                if msvcrt.kbhit():           # two reads — swallow the pair,
                    msvcrt.getwch()          # they are not an interrupt
                continue
            if ch == "\x1b":
                _fire()
                return
        time.sleep(_POLL)


def _watch_posix(stop):
    import select
    import termios
    import tty
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        while not stop.is_set():
            r, _, _ = select.select([sys.stdin], [], [], _POLL)
            if not r:
                continue
            ch = os.read(fd, 1)
            if ch != b"\x1b":
                continue                     # typed-ahead text: ignore
            # Arrow keys etc. are ESC + more bytes right behind; a lone Esc
            # press has none. Only the lone press interrupts.
            if select.select([sys.stdin], [], [], 0.02)[0]:
                while select.select([sys.stdin], [], [], 0)[0]:
                    os.read(fd, 1)
                continue
            _fire()
            return
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _watch(stop):
    try:
        (_watch_windows if os.name == "nt" else _watch_posix)(stop)
    except Exception:
        pass    # no Esc on this terminal — Ctrl-C still interrupts


@contextmanager
def esc_interrupts():
    """Press Esc while this context is active -> KeyboardInterrupt in the
    main thread. No-op when stdin isn't an interactive terminal."""
    try:
        interactive = sys.stdin.isatty()
    except Exception:
        interactive = False
    if not interactive:
        yield
        return
    stop = threading.Event()
    watcher = threading.Thread(target=_watch, args=(stop,), daemon=True)
    watcher.start()
    try:
        yield
    finally:
        stop.set()
        # Wait for the watcher to restore the terminal before anyone else
        # (prompt_toolkit, input()) reads from it.
        watcher.join(timeout=0.5)
