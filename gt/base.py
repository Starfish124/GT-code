"""Shared tool plumbing: the Tool base class and the per-call context.

Lives in its own module so tool modules (tools.py, office.py) can both import
it without importing each other.
"""

from dataclasses import dataclass, field
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
    user_msg: str = ""                    # the request that started this turn
    state: dict = field(default_factory=dict)  # per-turn scratch (e.g. ask_user budget)
    todos: list = field(default_factory=list)  # shared task checklist (write_todos)
    spawn: Callable[[str], str] = None    # run a research sub-agent (run_agent);
                                          # None inside a sub-agent — no nesting

    def resolve(self, path: str) -> Path:
        p = Path(str(path)).expanduser()
        if not p.is_absolute():
            p = self.cwd / p
        return p

    def confine_enabled(self) -> bool:
        """Is workspace confinement on? (config security.confine_to_workspace,
        default True — secure by default even if the key is absent)."""
        sec = getattr(self.config, "security", None)
        if sec is None and hasattr(self.config, "data"):
            sec = self.config.data.get("security", {})
        return bool((sec or {}).get("confine_to_workspace", True))

    def outside_workspace(self, p) -> bool:
        """True if resolved path p escapes the workspace root (self.cwd).

        Uses resolved, symlink-free paths so `..` traversal and absolute paths
        that climb out are both caught. The workspace root itself counts as
        inside. Only meaningful when confine_enabled() is True."""
        try:
            root = self.cwd.resolve()
            target = Path(p).resolve()
        except Exception:
            return True   # if we can't reason about it, treat it as outside
        return root != target and root not in target.parents


class Tool:
    name = ""
    description = ""
    args: dict = {}          # arg_name -> human description
    arg_types: dict = {}     # arg_name -> JSON-schema fragment; default string
    required: tuple = ()     # arg names the model MUST supply
    changes_system = False   # if True, requires approval

    # Some descriptions are protocol-specific (write_file's fenced content
    # block only exists in the prompt-JSON protocol) — this overrides what the
    # native function-calling spec advertises. None = use `description`.
    native_description = None

    def run(self, args: dict, ctx: Ctx) -> str:
        raise NotImplementedError

    def spec(self) -> dict:
        """This tool as a native function-calling spec (Ollama/OpenAI shape).

        Built from the SAME `args` descriptions the prompt protocol shows, so
        the two protocols never drift apart; `arg_types` adds real JSON types
        where "string" is wrong (booleans, numbers, arrays)."""
        props = {}
        for arg, desc in self.args.items():
            p = dict(self.arg_types.get(arg) or {"type": "string"})
            p.setdefault("description", desc)
            props[arg] = p
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.native_description or self.description,
                "parameters": {
                    "type": "object",
                    "properties": props,
                    "required": list(self.required),
                },
            },
        }
