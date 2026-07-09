"""Live smoothness test — drives the REAL resident model through many turns.

Unlike smoke_test.py (fully offline), this needs Ollama running with the tiny
model pulled. It proves the things unit tests can't: that the model stays hot
(no reload churn between turns), routing is stable, and per-turn latency stays
flat instead of creeping up over a session.

Run:  python -m tests.live_smoke        (from the repo root, in GT's venv)
Exits non-zero if a reload happens after warm-up, routing drifts, or a turn
errors — so it can gate a release. Skips cleanly if Ollama isn't reachable.
"""

import io
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rich.console import Console

from gt.config import Config
from gt.llm import LLM, LLMError
from gt.memory import Memory
from gt.router import Router
from gt.improve import Improver
from gt.agent import Agent

# One chat-heavy round with a couple of quick work turns — the everyday shape.
SEQUENCE = ["hi", "thanks!", "what can you do?", "can you browse the internet?",
            "who are you?", "list the files in this folder"]
ROUNDS = 3


def main():
    report = Console()
    quiet = Console(file=io.StringIO())
    cfg = Config.load()
    llm = LLM(cfg)
    try:
        cfg.auto_resolve(llm, quiet)
        # fail fast + clear if there's no model server
        llm.list_models(cfg.provider_base("ollama"))
    except LLMError as e:
        report.print(f"[yellow]SKIP:[/yellow] Ollama not reachable ({e}). "
                     "Start Ollama and pull the tiny model, then re-run.")
        return 0

    mem = Memory(llm, cfg.data_dir / "memory.db")
    router = Router(llm, cfg, quiet)
    agent = Agent(llm=llm, config=cfg, memory=mem, router=router, console=quiet,
                  improver=Improver(llm, mem), approve=lambda *a, **k: True, ask=None)

    # record the routed role for each turn without touching agent internals
    seen = {}
    _orig = router.route
    router.route = lambda m: seen.__setitem__("role", _orig(m)) or seen["role"]

    default_role = cfg.router.get("default", "tiny")
    report.print(f"[bold]Live smoothness test[/bold] — warming "
                 f"{cfg.model_for(default_role)['model']}…")
    agent.prewarm(default_role)
    time.sleep(8)

    rows, reloads, misroutes = [], 0, 0
    n = 0
    for rnd in range(1, ROUNDS + 1):
        for msg in SEQUENCE:
            n += 1
            t0 = time.perf_counter()
            try:
                agent.run(msg)
            except Exception as e:
                report.print(f"[red]turn {n} errored:[/red] {e}")
                return 1
            wall = time.perf_counter() - t0
            m = dict(llm.last_metrics or {})
            role = seen.get("role", "?")
            load = m.get("load_s", 0)
            if n > 1 and load > 0.5:            # a reload after warm-up = the bug
                reloads += 1
            # everyday turns must stay on the resident model, never swap
            if role != default_role:
                misroutes += 1
            rows.append((rnd, msg, role, load, wall))

    report.print(f"\n{n} turns · {ROUNDS} rounds\n")
    report.print(f"{'rnd':>3}  {'message':<30}{'role':<7}{'load_s':>7}{'wall_s':>8}")
    for rnd, msg, role, load, wall in rows:
        flag = "  <-- RELOAD" if load > 0.5 else ""
        report.print(f"{rnd:>3}  {msg[:29]:<30}{role:<7}{load:>7.1f}{wall:>8.1f}{flag}")

    warm = [r[4] for r in rows[1:]]
    report.print(f"\nreloads after warm-up : {reloads}  (want 0)")
    report.print(f"mis-routed turns      : {misroutes}  (want 0)")
    report.print(f"avg turn after warm-up: {sum(warm)/len(warm):.2f}s")
    ok = reloads == 0 and misroutes == 0
    report.print("[green]PASS — smooth: no reloads, routing stable[/green]" if ok
                 else "[red]FAIL[/red]")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
