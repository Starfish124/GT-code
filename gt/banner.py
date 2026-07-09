"""The GT-Code startup banner — a 3D 'GT CODE' wordmark in Grant Thornton purple.

The letters are rendered in the figlet "ANSI Shadow" style (block glyphs with a
built-in drop-shadow), then painted top-to-bottom with a violet→deep-purple
gradient so they read as lit-from-above extruded 3D type. On a terminal that
can't encode the block characters (a legacy code page), it falls back to a
plain-ASCII wordmark so GT still starts cleanly anywhere.
"""

import sys

from rich.text import Text


# figlet "ANSI Shadow" glyphs — 6 rows tall. Each glyph is padded to its own
# width at render time, so only the raw art needs to live here.
_GLYPHS = {
    "G": [" ██████╗ ", "██╔════╝ ", "██║  ███╗", "██║   ██║", "╚██████╔╝", " ╚═════╝ "],
    "T": ["████████╗", "╚══██╔══╝", "   ██║   ", "   ██║   ", "   ██║   ", "   ╚═╝   "],
    "C": [" ██████╗", "██╔════╝", "██║     ", "██║     ", "╚██████╗", " ╚═════╝"],
    "O": [" ██████╗ ", "██╔═══██╗", "██║   ██║", "██║   ██║", "╚██████╔╝", " ╚═════╝ "],
    "D": ["██████╗ ", "██╔══██╗", "██║  ██║", "██║  ██║", "██████╔╝", "╚═════╝ "],
    "E": ["███████╗", "██╔════╝", "█████╗  ", "██╔══╝  ", "███████╗", "╚══════╝"],
}

# Vertical gradient: light lavender highlight at the top face, deep Grant
# Thornton purple in the extruded shadow at the bottom → a 3D sheen.
_GRADIENT = ["#B98CE0", "#A56FD6", "#8E4EC6", "#7B3FB0", "#6A2C91", "#4E2170"]

_GT_PURPLE = "#7B3FB0"     # brand-ish purple for the subtitle
_AUTHOR = "Sarvesh Singh"
_LEFT_PAD = "  "


def _assemble(words):
    """Join glyph words into 6 combined rows (1-col gutter between letters,
    a wider gap between words)."""
    rows = [""] * 6
    for w, word in enumerate(words):
        if w:
            for r in range(6):
                rows[r] += "   "        # gap between words
        for c, ch in enumerate(word):
            glyph = _GLYPHS[ch]
            width = max(len(line) for line in glyph)
            for r in range(6):
                if c:
                    rows[r] += " "       # gutter between letters
                rows[r] += glyph[r].ljust(width)
    return rows


def _supports_block(console) -> bool:
    """Can this terminal actually encode the block-drawing characters?"""
    enc = (getattr(getattr(console, "file", None), "encoding", None)
           or getattr(sys.stdout, "encoding", None) or "")
    try:
        "█╗╝═║".encode(enc or "utf-8")
        return True
    except (UnicodeEncodeError, LookupError):
        return False


def render(console, version: str):
    """Print the startup banner to a rich Console."""
    if _supports_block(console):
        rows = _assemble(["GT", "CODE"])
        console.print()
        for row, color in zip(rows, _GRADIENT):
            console.print(Text(_LEFT_PAD + row, style=f"bold {color}"))
    else:
        # Pure-ASCII fallback for legacy code pages (no block characters).
        art = (r"   ___ _____    ___ ___  ___  ___ ",
               r"  / __|_   _|  / __/ _ \|   \| __|",
               r" | (_ | | |   | (_| (_) | |) | _| ",
               r"  \___| |_|    \___\___/|___/|___|")
        console.print()
        for line in art:
            console.print(f"[bold {_GT_PURPLE}]{line}[/]")
    console.print(f"[{_GT_PURPLE}]{_LEFT_PAD}created by [bold]{_AUTHOR}[/bold]"
                  f"[/]   [dim]· build v{version}[/dim]")
    console.print(f"[dim]{_LEFT_PAD}local coding agent · 3B-first, runs fully "
                  f"on your machine[/dim]")
