"""First-launch wizard: evaluate the machine, pick models, download them.

On first run (or `/setup`), GT:
  1. probes the hardware and shows what this machine is capable of,
  2. recommends the model tier that fits (3B minimum … 14B maximum),
  3. checks what Ollama already has and offers to `ollama pull` the rest,
  4. saves the chosen line-up to data/setup.json so later launches skip all
     of this and just apply it.
"""

import json
import shutil
import subprocess

from rich.panel import Panel
from rich.table import Table

from . import machine


# Bump when the tier→lineup mapping changes (e.g. the reviewer moved onto the
# 3B for 3B-first). An already-set-up machine whose saved setup.json predates
# this refreshes its lineup automatically on next launch — no manual /setup.
SCHEMA_V = 2


def _marker(config):
    return config.data_dir / "setup.json"


def _apply_lineup(config, lineup: dict):
    """Point every role at the chosen Ollama model (in-memory + this launch's
    auto_resolve still verifies it's actually served)."""
    if not lineup:
        return
    for role, model_id in lineup.items():
        if role in config.models:
            config.models[role]["provider"] = "ollama"
            config.models[role]["model"] = model_id
        else:
            config.models[role] = {"provider": "ollama", "model": model_id}


def _served_ids(llm, config):
    try:
        return llm.list_models(config.provider_base("ollama"))
    except Exception:
        return []


def _is_served(model_id: str, served: list) -> bool:
    base = model_id.split(":")[0].lower()
    for mid in served:
        low = mid.lower()
        if low == model_id.lower() or low.startswith(base):
            return True
    return False


def _hw_table(hw: dict) -> Table:
    t = Table(title="This machine", show_header=False, border_style="dim")
    t.add_column(style="bold")
    t.add_column()
    t.add_row("OS", f"{hw['os']} ({hw['arch']})")
    t.add_row("CPU", f"{hw['cpu']}  ({hw['cores']} cores)")
    t.add_row("RAM", f"{hw['ram_gb']} GB")
    gpu = hw["gpu"] or "none detected"
    if hw["vram_gb"]:
        gpu += f"  ({hw['vram_gb']} GB VRAM)"
    t.add_row("GPU", gpu)
    return t


def _plan_table(rec: dict, served: list) -> Table:
    t = Table(title=f"Recommended tier: {rec['label']}", border_style="cyan")
    t.add_column("role", style="bold")
    t.add_column("model", style="cyan")
    t.add_column("size")
    t.add_column("job", style="dim")
    t.add_column("status")
    listed = set()
    for role, mid in rec["lineup"].items():
        info = machine.CATALOG.get(mid, {})
        status = ("[green]installed[/green]" if _is_served(mid, served)
                  else f"[yellow]download ~{info.get('dl_gb', '?')} GB[/yellow]")
        t.add_row(role, mid if mid not in listed else f"[dim]{mid}[/dim]",
                  info.get("params", "?"), info.get("job", ""), status)
        listed.add(mid)
    return t


def _pull(model_id: str, console) -> bool:
    """Run `ollama pull` with live progress in the user's terminal."""
    console.print(f"[cyan]pulling {model_id} …[/cyan]")
    try:
        proc = subprocess.run(["ollama", "pull", model_id])
        ok = proc.returncode == 0
    except Exception as e:
        console.print(f"[red]could not run ollama pull: {e}[/red]")
        return False
    if ok:
        console.print(f"[green]{model_id} ready[/green]")
    else:
        console.print(f"[red]pull failed for {model_id} — "
                      f"run 'ollama pull {model_id}' manually.[/red]")
    return ok


def ensure(config, llm, console, prompt_fn, force=False):
    """Run the wizard if this machine hasn't been set up yet (or force=True).

    prompt_fn(text) -> str is the shell's input function.
    Always leaves config.models pointing at the saved/chosen line-up.
    """
    state = {}
    try:
        state = json.loads(_marker(config).read_text(encoding="utf-8"))
    except Exception:
        pass

    if state.get("done") and not force:
        lineup = state.get("lineup")
        # Migrate a stale saved lineup to the current tier mapping using the
        # SAVED hardware — no prompts, no downloads (models are already pulled).
        # This is how the reviewer→3B move reaches machines set up before it.
        if state.get("v") != SCHEMA_V and state.get("hardware"):
            try:
                rec = machine.recommend(state["hardware"])
                lineup = rec["lineup"]
                state.update({"v": SCHEMA_V, "tier": rec["tier"], "lineup": lineup})
                _marker(config).write_text(json.dumps(state, indent=2),
                                           encoding="utf-8")
            except Exception:
                pass
        _apply_lineup(config, lineup)
        return

    def ask(q, default_yes=True):
        suffix = " [Y/n] " if default_yes else " [y/N] "
        try:
            ans = prompt_fn(q + suffix).strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        if not ans:
            return default_yes
        return ans in ("y", "yes")

    console.print("\n[bold]Welcome to GT-Code — first-launch setup[/bold]")
    console.print("[dim]Evaluating this machine to pick the models that will "
                  "actually run well on it…[/dim]\n")

    hw = machine.probe()
    rec = machine.recommend(hw)
    console.print(_hw_table(hw))
    console.print(f"\n[bold cyan]Verdict:[/bold cyan] {rec['reason']}\n")

    # --- Ollama present? -------------------------------------------------------
    if not shutil.which("ollama"):
        console.print("[yellow]Ollama is not installed — GT needs it to run "
                      "local models.[/yellow]")
        console.print("  Windows:  winget install -e --id Ollama.Ollama")
        console.print("  macOS:    brew install ollama")
        console.print("  Linux:    curl -fsSL https://ollama.com/install.sh | sh")
        console.print("[dim]Install it, then run /setup to finish. GT will "
                      "start, but nothing works without a model server.[/dim]")
        _apply_lineup(config, rec["lineup"])
        return

    served = _served_ids(llm, config)
    console.print(_plan_table(rec, served))

    missing = [m for m in rec["needed"] if not _is_served(m, served)]
    if missing:
        total = sum(machine.CATALOG.get(m, {}).get("dl_gb", 0) for m in missing)
        console.print(f"\n{len(missing)} model(s) to download "
                      f"(~{total:.1f} GB total). The 3B model and the "
                      f"embedding model are the minimum for GT to function.")
        for m in missing:
            info = machine.CATALOG.get(m, {})
            if ask(f"Download {m} ({info.get('params','?')}, "
                   f"~{info.get('dl_gb','?')} GB)?"):
                _pull(m, console)
    else:
        console.print("\n[green]All recommended models are already "
                      "installed.[/green]")

    _apply_lineup(config, rec["lineup"])
    try:
        _marker(config).parent.mkdir(parents=True, exist_ok=True)
        _marker(config).write_text(json.dumps(
            {"done": True, "v": SCHEMA_V, "hardware": hw, "tier": rec["tier"],
             "lineup": rec["lineup"]}, indent=2), encoding="utf-8")
    except Exception:
        pass
    console.print("[dim]Setup saved — re-evaluate any time with /setup.[/dim]\n")
