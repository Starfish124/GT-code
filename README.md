GT-Code your own local coding agent! (please scroll lower to see trouble shooting)

A small, self-hosted CLI coding assistant, like Claude Code, but running
**on your machine** with your local models — no cloud LLM, no API keys. It uses
3B, 8B and 14B parameter models (input needed for other models).

**On the network:** the model itself runs locally (Ollama on localhost) and GT
sends **no telemetry, analytics, or update checks** — ever. The one way data can
leave the machine is the optional `web_search` / `web_fetch` tools, which are
**OFF by default** (`web.enabled: false`); with them off GT is fully
air-gapped. Turn them on per task if you need internet access. See
`config.yaml` for every privacy/security default (web, profiling, hooks,
lesson-learning are all opt-in; file/command access is confined to the folder
you launch GT in).

On **first launch it evaluates your hardware** (RAM, CPU, GPU/VRAM), tells you
what this machine can handle, and offers to download the right models — 3B
minimum, 14B maximum (27B+ is deliberately unsupported: too slow to be a
useful interactive agent on consumer hardware). It then works like Claude
Code: **asks clarifying questions**, **presents an architecture plan** before
building, executes with tools (files, shell, web, **Excel / PowerPoint /
Word**), verifies its work, and **asks permission** as it goes — with
"always allow" grants per action kind, just like Claude Code.

```
You type ─► Router (3B picks a model) ─► Brain (qwen3:14b / 8B — never 27B+)
                                              │
              clarify (ask_user) ─► plan ─► execute ─► verify
                                              │
            ┌─────────────────────────────────┼──────────────────────┐
       Agent tool-loop                    RAG memory              Self-improve
   read / write / edit files,        nomic-embed + sqlite      reviewer distills
   run commands, web search,         cosine vector search      a reusable lesson
   Excel / PowerPoint / Word
```

The model line-up is picked **per machine** by the first-launch wizard:

| Tier | Needs | brain | fast | tiny |
|------|-------|-------|------|------|
| **Full** | ≥16 GB RAM (or ≥11 GB VRAM) | `qwen3:14b` | `qwen3:8b` | `qwen2.5:1.5b` |
| **Standard** | ≥10 GB RAM | `qwen3:8b` | `qwen3:8b` | `qwen2.5:1.5b` |
| **Minimum** | anything less | `qwen2.5:1.5b` | `qwen2.5:1.5b` | `qwen2.5:1.5b` |

Every default model is **Apache-2.0** (Qwen + `nomic-embed-text`) — no Meta Llama
licence anywhere in the line-up, so nothing needs a licence review to deploy.
(`nomic-embed-text` is always installed for memory/RAG; the 1.5B + embed models
are the minimum for GT to function.) Re-evaluate any time with `/setup`,
inspect with `/doctor`.

---

## Quickstart — any new machine, three commands

The only thing you need pre-installed is **Python 3.10+** on PATH
(https://www.python.org/downloads/ — on Windows, check **“Add python.exe to
PATH”** during install). Then:

```bat
git clone https://github.com/Starfish124/GT-code.git
cd GT-code
setup.bat          ::  macOS/Linux:  ./setup.sh
```

The script installs GT-Code into **its own `.venv` inside the GT-code folder**
(one environment for GT itself — never one per project), **installs Ollama** if
missing (winget on Windows, brew/curl elsewhere), pulls the tiny baseline
models (~2.3 GB), installs a global **`gt` command**, and self-tests that the
command works from a different folder. Then:

```bat
cd C:\any\project
gt
```

GT always runs from its own environment and **operates on whatever folder you
launch it from** — your projects need no venv, no setup, nothing. `gt <path>`
opens a specific folder; `gt --version` checks the install.

**First launch runs the setup wizard**: it probes your hardware, shows a
verdict ("16 GB RAM comfortably fits a 14B model…"), and asks before
downloading each recommended model. Nothing is pulled without your consent.
Subsequent launches skip straight to work.

**No config editing needed.** GT saves the wizard's choice and re-verifies at
every launch what Ollama actually serves, falling back gracefully.

---

## Use it

```
gt› build me a todo app with a REST API
```

For a task like that, GT behaves like Claude Code:

1. **asks** 1–3 short questions (which stack? database? auth?) — answer inline,
2. **presents its plan** — components, files, steps — and confirms it,
3. **executes** step by step, asking permission per action kind:
   ```
   ╭─ Permission needed — Run command ─╮
   │ npm install express               │
   ╰───────────────────────────────────╯
     [y] yes once   [a] always allow 'cmd:npm'   [n] no
   ```
4. **verifies** — runs the code/tests and fixes failures before reporting done.

It can also produce real documents:

```
gt› make an excel file comparing our three pricing tiers
gt› turn README.md into a 5-slide powerpoint
gt› write a word doc summarising this codebase
```

Destructive-looking commands (`rm -rf`, `format`, `del /s`, forced pushes…)
**always** prompt in red — even with `/auto` on or a standing grant.

---

## Commands

| Command | What it does |
|--------|--------------|
| `/help` | List commands |
| `/setup` | Re-run the wizard (re-evaluate hardware, download models) |
| `/doctor` | Hardware report + model line-up + provider health |
| `/models` | Show the exact model ids Ollama + LM Studio serve |
| `/model <role\|off>` | Pin a model (`brain`/`fast`/`tiny`) or `off` to auto-route |
| `/route` | Toggle smart auto-routing |
| `/think` | Toggle deep-thinking mode (slower, more careful; off by default) |
| `/skills` | List the expert playbooks GT injects per request |
| `/auto` | Toggle auto-approve (dangerous commands still prompt) |
| `/permissions` | List standing grants; `/permissions clear` revokes all |
| `/hooks` | List the lifecycle hooks configured in `config.yaml` |
| `/compact` | Fold the conversation into a rolling session summary (also automatic at context pressure) |
| `/cd <path>` | Change the working directory GT operates in |
| `/init` | Explore the project and write its `GT.md` (project memory) |
| `/index <path>` | Embed a file/folder into memory for RAG |
| `/remember <text>` | Save a note to long-term memory |
| `/lessons` | Show lessons GT has learned about itself |
| `/memory [reload]` | Memory stats + the `GT.md` files loaded into context |
| `/forget <note\|lesson\|doc\|all>` | Clear memory |
| `# <note>` | Jot a standing note into the project's `GT.md` |
| `/reset` | Clear the current conversation |
| `/quit` | Exit |

---

## Skills — embedded expertise the local models don't have

Local models can execute, but they arrive with no *taste*: they've never
seen what a consultant-grade spreadsheet or a designed landing page looks
like. GT ships with a curated **skills library** (`skills/*.md`) — expert
playbooks written once, injected automatically:

| Playbook | Kicks in when you ask for… |
|---|---|
| `excel` | spreadsheets — summary sheet first, headers with units, totals, assumptions |
| `powerpoint` | decks — takeaway titles, one idea per slide, speaker notes |
| `word-docs` | reports — executive summary, verb-led recommendations |
| `frontend` | HTML/UI — design system, spacing scale, hover states, single-file rule |
| `backend` | APIs — REST conventions, validation at the edge, one error shape |
| `code-quality` | any code — naming, small functions, verify-before-done |
| `debugging` | bugs — reproduce → read the error → one hypothesis at a time |
| `project-setup` | new projects — scaffold, README, git, prove hello-world runs |

Per request GT matches trigger keywords and injects at most 2 playbooks
(~1-2k tokens) into the model's context — you'll see
`· playbooks: excel` before it starts. `/skills` lists them all.

**Why playbooks and not gigabytes of docs:** an 8K context window means the
model can only *see* a few thousand tokens of guidance at once. Two sharp,
curated pages at exactly the right moment beat 3 GB of retrieved
documentation fragments. For big reference corpora you control, that's what
`/index` (RAG) is for — index MDN, your style guide, or a framework's docs
and GT recalls the relevant chunks.

**Add your own:** drop a `.md` into `~/.gt/skills/` (or `skills/` in the
repo) with the same front matter (`name`, `triggers`, `priority`) — same-name
files in `~/.gt/skills/` override the shipped ones. Team-wide skills are just
files in git.

## Performance — why GT feels snappy (and what the numbers mean)

Local agents usually feel slow for reasons that have nothing to do with model
quality. GT attacks all of them:

**The speed ladder.** The 3B routes and handles small talk (instant), the
**8B is the workhorse** for everyday coding and stays hot in RAM, and the 14B
is reserved for what actually needs deep reasoning — architecture, planning,
complex design. Sending everything to the biggest model is what kills local
agents: every model swap can cost 10–60 s of loading before the first token.

**Hidden "thinking" off by default.** Qwen3 models silently generate hundreds
of internal reasoning tokens before answering — measured on the same question:
13 tokens without thinking vs 134 with. GT disables it by default and gives
you `/think` to turn it back on when you *want* slow-and-careful.

**Models stay loaded.** GT uses Ollama's native API with `keep_alive: 30m`,
so a model loads once and stays hot. At startup (and whenever you `/model`
pin), GT pre-warms the model **in the background while you type your first
prompt**.

**Nothing blocks you.** Lesson-extraction (the self-improve loop) runs on a
background thread after your answer is already delivered. Oversized tool
outputs are trimmed before re-entering context so later steps don't pay
ever-growing prompt-processing costs.

**You see where time goes.** While waiting you get a live counter
(`⠹ waiting for fast (qwen3:8b) — 3.2s`), and after every response a real
measurement straight from Ollama:

```
⏱ model load 8.2s · prompt 1.1s (1312 tok) · 34 tok/s × 412 tok · total 21.3s
```

If `model load` shows up, that was a one-time cost (model got evicted or
first use). If `prompt` dominates, the context is big. If tok/s is low, the
model is too big for the hardware — `/setup` re-evaluates the tier.

Tuning knobs in `config.yaml` under `performance:`: `thinking`, `keep_alive`,
`num_ctx`.

## How it works (the interesting part)

**Hardware evaluation** (`gt/machine.py`). Pure stdlib probing — RAM via
`GlobalMemoryStatusEx` / `sysctl` / `/proc/meminfo`, GPU via `nvidia-smi`,
Apple Silicon unified memory detected. `recommend()` maps the numbers to the
biggest tier that runs *comfortably*, and never suggests anything above 14B.

**First-launch wizard** (`gt/wizard.py`). Shows the hardware verdict and a
role→model plan with download sizes, checks what Ollama already serves, and
`ollama pull`s only what you approve. The choice is saved to
`data/setup.json` and applied on every launch.

**Permissions** (`gt/permissions.py`). Claude-Code-style: every prompt offers
**yes once / always allow / no**. Grants are coarse and readable — `files`
(writes/edits), `docs` (Excel/PPT/Word), `cmd:git`-style per-command prefixes —
and persist in `data/permissions.json`. A dangerous-command regex overrides
everything and always asks.

**Clarify → plan → execute → verify** (`gt/prompts.py` + the `ask_user` tool).
The system prompt teaches the workflow; `ask_user` lets the model stop
mid-task and ask *you* a question — that's how it clarifies requirements and
confirms its architecture plan before writing code. It's budgeted: at most 3
questions per task, and the prompt tells the model to bundle what it needs
into one question and pick sensible defaults (ports, file names) itself.

**Shell that survives real dev work** (`gt/tools.py`). `run_command` takes
optional `cwd` (because `cd` never persists between commands), `timeout`
(default 300 s, raisable per call for big installs — on timeout the process
*tree* is killed and partial output is returned), and `background: true` for
dev servers and watchers, which never exit and would otherwise always time
out. Background processes log to `data/logs/`, are inspected with
`check_process`, stopped with `stop_process`, and killed on GT exit. Every
command runs with stdin closed and `CI=1` + `npm_config_yes` so CLIs go
non-interactive instead of hanging on an invisible prompt.

**Routing** (`gt/router.py`). Cheap heuristics first (small talk → `tiny`,
code signals → `brain`); only ambiguous requests cost a one-word
classification from the 3B model. Pin with `/model brain`.

**Agent loop** (`gt/agent.py`). A **hybrid tool protocol**: on models whose
chat template supports it (asked of Ollama once per model), tool calls go
through **native function calling** — structured, no JSON-in-prose to break —
with results fed back as real `tool`-role messages; everywhere else GT falls
back to the portable **prompt-based JSON protocol** (one fenced block per
call), so it still works on any model. `agent.tool_protocol` in config.yaml
(`auto`/`native`/`prompt`) forces a side; `/doctor` shows which protocol each
model gets. Either way GT runs the call, feeds back the result, and loops
until a plain-text answer. Output streams as live markdown (`gt/ui.py`).

**Documents** (`gt/office.py`). `create_excel` (openpyxl — bold headers,
auto-width, frozen header row), `create_powerpoint` (python-pptx — title
slide, bullets, speaker notes), `create_word` (python-docx — headings,
paragraphs, bullet lists). All pure-Python, Windows-friendly wheels.

**Memory / RAG** (`gt/memory.py`). `nomic-embed-text` vectors in plain
**sqlite**, brute-force cosine in numpy — no native builds to fight on
Windows. Kinds: `note`, `doc`, `lesson`.

**Project memory** (`gt/project_memory.py`). GT's `CLAUDE.md`: a `GT.md`
file of standing project instructions (stack, run/test commands,
conventions) loaded into every work turn, so each session starts already
knowing the project. Three layers — `~/.gt/GT.md` (you, everywhere), the
nearest `GT.md`/`AGENTS.md`/`CLAUDE.md` above the workspace, and a
git-ignored `GT.local.md` for personal overrides. `/init` writes one by
exploring the project; `# <note>` appends a rule in one keystroke;
`/memory` shows exactly what's loaded.

**Self-improvement** (`gt/improve.py`). After each task the `reviewer` model
distills one reusable lesson into memory. Learning by retrieval, not
fine-tuning — transparent and reversible (`/forget lesson`).

**Auto-compaction** (`gt/agent.py`). Bounded history keeps prefill fast, but
old turns used to just fall off — the goal and constraints from early in a
long build vanished with them. Now, when the conversation outgrows
`max_history_turns`, the resident model distills the oldest exchanges into a
rolling session summary that rides at the front of the context (never in the
system prompt, which stays KV-cached). Runs after your answer, on the
always-hot model — no swap, no reload. `/compact` runs it on demand and shows
the summary; degrades to the plain drop when Ollama is unreachable.

**Confidence-gated planning** (`gt/intent.py`). Bias-to-action is right for
clear requests and wrong for ambiguous ones — the flappy-bird failure was an
agent executing a request it had misread, at full speed. Before a NEW
build-shaped task starts, GT spends one small model call weighing the
plausible **readings** of the request (what was specified vs left to
defaults) and scoring its confidence in the main one. The score routes
deterministically: high → build immediately (nothing changes), medium →
present a plan and wait for your "go", low → ask **one** clarifying
question, then build. Never fires on chat, quick tasks, mid-task turns, or
forced `/mode` sessions; fail-open, so the gate can only add care, never
block work.

**Hooks** (`gt/hooks.py`). Prompts steer the model; hooks guarantee. A hook
is your own shell command, declared under `hooks:` in `config.yaml`, that GT
runs deterministically at a fixed lifecycle point: `session_start`,
`session_end`, `user_prompt` (stdout becomes context), `pre_tool` (**exit
code 2 blocks the tool call** — protect `.env`, enforce team policy),
`post_tool` (stdout is appended to the result the model sees — auto-lint
after every write), `turn_end` (log, notify). Each hook gets a JSON payload
on stdin plus `GT_EVENT`/`GT_TOOL`/`GT_CWD` env vars; broken or hanging
hooks fail open, so a bad hook can never brick the agent. `/hooks` shows
what's configured.

**Sub-agents** (`gt/subagent.py`). GT's Task tool: `run_agent` sends a
research sub-agent to investigate in its **own separate context** — read
files, search the codebase, browse the web — and only its final report comes
back, so mapping a codebase never floods (or permanently slows) the main
conversation. Scaled for local models: it runs on the same already-loaded
model (isolation, not parallelism), its toolset is strictly read-only (no
writes, no commands, no permission prompts), it cannot nest or ask questions,
and it gets the workspace listing seeded up front so a small model has
nothing to guess. Step-budgeted; every claim in the report must name the
file it came from.

---

## Configuration reference (`config.yaml`)

```yaml
router:
  enabled: true      # auto-pick a model per request
  default: brain     # fallback when unsure
agent:
  max_steps: 20      # tool iterations before GT must answer
  auto_approve: false
memory:
  auto_learn: true   # extract a lesson after each task
  recall_k: 5        # memories pulled into context per turn
  min_score: 0.28    # similarity floor (0..1)
compaction:
  enabled: true      # summarize old turns instead of dropping them
  keep_turns: 5      # exchanges kept verbatim after a compaction
  max_chars: 1500    # cap on the rolling session summary
subagents:
  enabled: true      # run_agent: read-only research in a separate context
  max_steps: 8       # tool calls before the sub-agent must report
  max_report_chars: 3000
hooks:
  enabled: true      # your commands at lifecycle points (see config.yaml)
  timeout: 30        # per-hook kill timer; failures are fail-open
intent_gate:
  enabled: true      # weigh a new build request before executing it
  min_confidence: 75 # >= builds now; below plans first and waits for "go"
  ask_below: 45      # < asks one clarifying question instead
web:
  enabled: true      # web_search / web_fetch tools; false = fully offline
```

The `models:` section is a default for mid-range machines — the wizard's
per-machine choice (saved in `data/setup.json`) overrides it at launch.
LM Studio remains an optional fallback provider if its server is running.

---

## Extending GT

**Add a tool** — subclass `Tool` (from `gt/base.py`) in `gt/tools.py`, add it
to `ALL_TOOLS`:

```python
class WordCount(Tool):
    name = "word_count"
    description = "Count the words in a file."
    args = {"path": "File to count."}
    def run(self, args, ctx):
        p = ctx.resolve(args["path"])
        return f"{len(p.read_text(encoding='utf-8').split())} words"
```

The system prompt is generated from the registry, so the model learns the new
tool automatically. Tools that change the machine should call
`ctx.approve(title, detail, key="...")` first.

**Swap models** — `/setup` re-picks per machine, or edit `config.yaml`.

**Change GT’s personality / workflow** — edit `SYSTEM_TEMPLATE` in `gt/prompts.py`.

---

## Troubleshooting

**First move, always:** run the doctor from the GT-code folder — it checks
every link in the launch chain (Python → venv → `gt` command → PATH → Ollama
→ models) and prints the fix for the first thing that's broken:

```bat
doctor.bat          ::  macOS/Linux:  ./doctor.sh
```

The in-depth guide — symptom by symptom, including corporate-laptop PATH
issues, proxies, and the clean-reinstall procedure — is in
**[TROUBLESHOOTING.md](TROUBLESHOOTING.md)**. Quick reference:

| Symptom | Fix |
|--------|-----|
| `Can't reach ollama` | Ensure Ollama is installed/running (open http://localhost:11434) |
| Wizard says Ollama missing | Install it (`winget install Ollama.Ollama` / `brew install ollama`), then `/setup` |
| `model not found, try pulling it` | `/setup` again, or `ollama pull` the model it names |
| `'python' is not recognized` | Reinstall Python with **Add to PATH**, or use `py -3` |
| `No module named gt` when typing `gt` | Your install predates v0.2 — `git pull` in the GT-code folder and re-run `setup.bat` / `./setup.sh` (it now pip-installs GT into its venv and self-tests from another folder) |
| `'gt' is not recognized...` (Windows) | `git pull` + re-run `setup.bat`: it now installs the command to `%USERPROFILE%\.gt\bin` and adds that to your user PATH itself (managed/corporate laptops often don't have the WindowsApps folder on PATH). Then **open a new terminal**. Still stuck? `start.bat` in the GT-code folder always works |
| `gt` opens the wrong folder | GT works on the folder you run it from; use `gt <path>` or `/cd` inside GT |
| First reply from the 14B is slow | Normal — Ollama loads it into RAM/VRAM on first call |
| Router feels laggy | `/route` off, or `/model brain` to pin the big model |
| `web search failed (offline?)` | No internet, or set `web.enabled: false` to hide the web tools |
| `command killed after 300s` | Long install/build: GT can pass a bigger `timeout`; a dev server should run with `background: true` instead. Default lives in `agent.command_timeout` |
| Office tool says a package is missing | `pip install openpyxl python-pptx python-docx` in the `.venv` (or re-run setup) |

---

## Verify the install (optional)

A dependency-free logic test (no models needed) lives in `tests/`:

```bat
.venv\Scripts\python.exe -m tests.smoke_test
```

It checks config loading, tool-call parsing, the file/office/ask tools,
hardware tiering, the permission system, web-tool gating, the streaming
renderer, and the router heuristics. All 42 checks should pass.

---

## Layout

```
GT-code/
  config.yaml          roles + settings (wizard overrides per machine)
  requirements.txt     dependencies
  setup.bat / .sh      one-shot installer (deps + Ollama + baseline models + `gt` cmd)
  start.bat / .sh      launch GT without the global `gt` command
  gt/
    cli.py             the terminal + slash commands
    wizard.py          first-launch setup (hardware → models → downloads)
    machine.py         hardware probe + model tier recommendation
    permissions.py     Claude-Code-style permission grants
    router.py          picks a model per request
    agent.py           the agentic tool loop
    base.py            Tool base class + tool-call context
    tools.py           file / shell (+ background processes) / search / web / ask_user / recall
    office.py          create_excel / create_powerpoint / create_word
    memory.py          sqlite + nomic-embed vector store
    improve.py         self-improving lesson extractor
    llm.py             OpenAI-compatible client (Ollama + LM Studio)
    ui.py              live streaming-markdown renderer
    prompts.py         system prompt (clarify → plan → execute → verify)
    config.py          config loader
  tests/
    smoke_test.py      offline sanity checks
```

Built to run offline, on your hardware, under your control. Have fun. 🛠️
