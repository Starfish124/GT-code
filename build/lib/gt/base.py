"""Shared tool plumbing: the Tool base class and the per-call context.

Lives in its own module so tool modules (tools.py, office.py) can both import
it without importing each other.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass
class Ctx:
    """Shared state handed to every tool call."""
    cwd: Path
    memory: object
    approve: Callable[..., bool]          # (title, detail, key=None) -> bool
    config: object
    ask: Callable[[str], str] = None      # ask the user a question mid-task

    def resolve(self, path: str) -> Path:
        p = Path(str(path)).expanduser()
        if not p.is_absolute():
            p = self.cwd / p
        return p


class Tool:
    name = ""
    description = ""
    args: dict = {}          # arg_name -> human description
    changes_system = False   # if True, requires approval

    def run(self, args: dict, ctx: Ctx) -> str:
        raise NotImplementedError
