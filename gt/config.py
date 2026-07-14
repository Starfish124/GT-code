"""Loads config.yaml and resolves logical model roles to concrete endpoints."""

import os
import re
from pathlib import Path
import yaml

# The GT-code project root when running from a git clone (editable install).
ROOT = Path(__file__).resolve().parent.parent
# Per-user home for installs where the package doesn't live in the repo
# (plain `pip install gt-code`) — config + data land here instead.
USER_DIR = Path.home() / ".gt"

DEFAULT_CONFIG = """\
# GT-Code configuration (auto-created). The first-launch wizard evaluates this
# machine and overrides the model line-up; /setup re-runs it, /doctor inspects.
providers:
  ollama:
    base_url: http://localhost:11434/v1

models:
  brain:    {provider: ollama, model: "qwen3:14b"}
  fast:     {provider: ollama, model: "qwen3:8b"}
  tiny:     {provider: ollama, model: "qwen2.5:1.5b"}
  reviewer: {provider: ollama, model: "qwen2.5:1.5b"}
  analyst:  {provider: ollama, model: "qwen2.5:1.5b"}
  embed:    {provider: ollama, model: "nomic-embed-text"}

router:
  enabled: true
  default: tiny         # small-model-first: resident tiny for everyday turns,
  escalate_len: 160     # escalate only for real work. See config.yaml.
  work_min_role: fast   # smallest model allowed to run tools (tiny talks, fast codes)

agent:
  max_steps: 20
  auto_approve: false
  tool_protocol: auto   # native function calling where supported, else prompt-JSON

performance:
  thinking: false
  keep_alive: 8h        # keep the model hot: cold load paid once per boot
  num_ctx: 8192
  llm_timeout: 1800     # no-bytes budget; CPU load+prefill can run 10+ min

memory:
  auto_learn: false     # OFF: don't persist task-derived "lessons" (may capture
                        # client details). Opt in only outside confidential work.
  recall_k: 5
  min_score: 0.28

compaction:
  enabled: true         # summarize old turns instead of dropping them
  keep_turns: 5
  max_chars: 1500

project_memory:
  enabled: true         # load GT.md (project instructions) every work turn
  max_chars: 6000

intent_gate:
  enabled: true         # weigh a new build request before executing it
  min_confidence: 75    # >= builds now; below plans first
  ask_below: 45         # < asks one clarifying question instead

hooks:
  enabled: false        # OFF: hooks run your shell commands (code execution);
  timeout: 30           # enable only with a config you control. See repo config.yaml.

subagents:
  enabled: true         # run_agent: read-only research in a separate context
  max_steps: 8
  max_report_chars: 3000

skills:
  enabled: true
  max: 2

profile:
  enabled: false        # OFF: opt-in behavioural profiling (see repo config.yaml)

web:
  enabled: false        # OFF: keep GT local/air-gapped; enable per task if needed

security:
  confine_to_workspace: true  # keep reads/writes/commands inside the launch folder

data_dir: data
"""


def find_config() -> Path:
    """Locate config.yaml, wherever GT was installed from.

    Order: $GT_CONFIG → the repo root (git-clone install) → ~/.gt/config.yaml.
    If none exists, a default is created at ~/.gt/config.yaml so `gt` always
    starts, from any directory, on any kind of install.
    """
    env = os.environ.get("GT_CONFIG")
    if env:
        return Path(env).expanduser()
    repo_cfg = ROOT / "config.yaml"
    if repo_cfg.exists():
        return repo_cfg
    user_cfg = USER_DIR / "config.yaml"
    if not user_cfg.exists():
        user_cfg.parent.mkdir(parents=True, exist_ok=True)
        user_cfg.write_text(DEFAULT_CONFIG, encoding="utf-8")
    return user_cfg


CONFIG_PATH = ROOT / "config.yaml"  # kept for backwards compatibility

# Model ids in config.yaml that are obviously templates, not real ids.
_PLACEHOLDERS = ("your-", "<", "confirm", "changeme")
# Substrings that mark a model as embedding-only (never route chat to these).
_EMBED_HINTS = ("embed", "bge", "minilm", "nomic")


def _is_placeholder(model_id: str) -> bool:
    low = model_id.lower()
    return any(p in low for p in _PLACEHOLDERS)


def _match(want: str, served: list) -> str | None:
    """Exact id first, then a case-insensitive substring match either way
    (config says 'qwen3', server says 'qwen3:8b-instruct' → match)."""
    if want in served:
        return want
    low = want.lower()
    if _is_placeholder(low):
        return None
    for mid in served:
        if low in mid.lower() or mid.lower() in low:
            return mid
    return None


def _guess_embed(served: list) -> str | None:
    for mid in served:
        if any(h in mid.lower() for h in _EMBED_HINTS):
            return mid
    return None


def _guess_chat(served: list) -> str | None:
    """Pick the chat model with the largest parameter count in its id
    (e.g. '...-28b' beats 'qwen3:8b'). Ties/no-number → first listed."""
    def size(mid):
        nums = re.findall(r"(\d+(?:\.\d+)?)\s*b\b", mid.lower())
        return max((float(n) for n in nums), default=0)
    chat = [m for m in served if not any(h in m.lower() for h in _EMBED_HINTS)]
    return max(chat, key=size, default=None)


class Config:
    def __init__(self, data: dict, home: Path = ROOT):
        self.data = data
        self.home = home  # the folder that owns config.yaml (repo or ~/.gt)
        self.providers = data.get("providers", {})
        self.models = data.get("models", {})
        self.router = data.get("router", {})
        self.agent = data.get("agent", {})
        self.memory = data.get("memory", {})
        self.web = data.get("web", {})
        self.security = data.get("security", {})
        self.performance = data.get("performance", {})
        # data (memory db, history, permissions, setup marker) lives NEXT TO
        # the config file, never in the user's project directory.
        raw = data.get("data_dir", "data")
        self.data_dir = Path(raw) if Path(raw).is_absolute() else home / raw

    def model_for(self, role: str) -> dict:
        """Resolve a role like 'brain' to {provider, model, base_url}."""
        m = self.models.get(role)
        if not m:
            raise KeyError(
                f"No model role '{role}' defined in config.yaml (models: "
                f"{', '.join(self.models) or 'none'})"
            )
        prov = self.providers.get(m["provider"])
        if not prov:
            raise KeyError(f"Role '{role}' points at unknown provider '{m['provider']}'")
        return {
            "role": role,
            "provider": m["provider"],
            "model": m["model"],
            "base_url": prov["base_url"].rstrip("/"),
        }

    def provider_base(self, provider: str) -> str:
        return self.providers[provider]["base_url"].rstrip("/")

    def auto_resolve(self, llm, console=None):
        """Best-effort startup pass so GT 'just works' on a fresh machine:

        * ping Ollama and report reachability,
        * match every role's configured model id against what's actually
          served (exact -> fuzzy -> best guess for placeholders like
          'your-28b-model'), so a role whose exact model isn't pulled still
          resolves to a live one.

        Only mutates the in-memory config — config.yaml is never edited.
        """
        say = console.print if console else (lambda *a, **k: None)

        # what does each provider actually serve right now?
        served = {}
        for name, prov in self.providers.items():
            try:
                served[name] = llm.list_models(prov["base_url"])
                say(f"[green]ok[/green] {name}: {len(served[name])} model(s) "
                    f"at {prov['base_url']}")
            except Exception:
                served[name] = None
                say(f"[yellow]--[/yellow] {name}: not reachable at "
                    f"{prov['base_url']} [dim](is Ollama running?)[/dim]")

        for role, m in self.models.items():
            ids = served.get(m["provider"])
            if not ids:
                say(f"[red]{role}: no reachable model — requests needing "
                    f"'{role}' will fail[/red]")
                continue
            want = m["model"]
            # The analyst is a specific small model (qwen2.5:1.5b) — never guess a
            # substitute for it, or it'd silently run on a big model. Leave it
            # exact; the Profiler simply no-ops when it isn't pulled.
            got = _match(want, ids)
            if got is None and role == "analyst":
                continue
            guess_fn = _guess_embed if role == "embed" else _guess_chat
            if got is None:
                got = guess_fn(ids)
                if got:
                    hint = (f" [yellow](for the full {role}, run: "
                            f"ollama pull {want} — or /setup)[/yellow]"
                            if role == "brain" else "")
                    say(f"[dim]{role}: '{want}' not served — "
                        f"using '{got}'[/dim]{hint}")
            elif got != want:
                say(f"[dim]{role}: '{want}' -> matched '{got}'[/dim]")
            if got:
                m["model"] = got

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        path = Path(path) if path else find_config()
        if not path.exists():
            raise FileNotFoundError(f"config.yaml not found at {path}")
        with open(path, "r", encoding="utf-8") as f:
            return cls(yaml.safe_load(f) or {}, home=path.resolve().parent)
