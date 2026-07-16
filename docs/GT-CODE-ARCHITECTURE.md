# GT Code Architecture

A local coding agent. No server, no cloud, nothing leaves the machine.

| | |
|---|---|
| **Author** | Sarvesh Singh |
| **Source** | github.com/Starfish124/GT-code (private; full source available for line-by-line review) |
| **Status** | v0.7.4 — demoed to compliance and the tech lead 14 July 2026; in user testing (Dylan, Max) |
| **Stack** | Python 3.10+, Ollama, ~7,300 lines of package code + 2,700 lines of tests. Developed with Claude Code as the engineering assistant; zero runtime dependency on any external AI |
| **Licence cost** | Zero. Apache-2.0 open-weight models, open-source stack. No subscription, no per-seat fee, no vendor contract |

## What it is

GT Code is a command-line coding assistant modelled on Claude Code, running entirely on the user's own laptop with local open-weight models. It was built because external AI tools are banned for GT business use.

It gives builders the same working pattern those tools offer: it weighs ambiguous requests before acting, asks clarifying questions or presents a plan when unsure, executes with permission checks, tracks multi-step work on a visible checklist, and verifies its own output. It also produces finished Excel, PowerPoint and Word documents. Every step is shown on screen as it happens — the design principle throughout is glass-box, not black-box.

## How a request flows

```
You type ─► Router (resident 1.5B classifies) ─► chat answers instantly on the 1.5B
                    │                             tool work runs on the 8B (work floor)
                    │                             architecture/planning on the 14B — never 27B+
                    ▼
        Intent gate (build-shaped requests only)
        ≥75% confident ─► build now
        45–74%         ─► present a plan, wait for "go"
        <45%           ─► ask ONE clarifying question
                    │
      clarify ─► plan (write_todos checklist) ─► execute ─► verify ─► report
                    │
   ┌────────────────┼────────────────────┬──────────────────┬─────────────────┐
   Agent tool-loop        Sub-agents          Memory layers        Lifecycle hooks
   17 tools: files,       read-only research  GT.md project        user-defined shell
   shell, todos,          in a separate       memory + RAG         commands at fixed
   Office docs,           context — only      (sqlite + vectors)   points; pre_tool
   web (off by default)   the report returns  + session summary    can veto any call
```

Three models share the work, all Apache-2.0:

| Role | Model | Job |
|---|---|---|
| tiny | Qwen 2.5 1.5B | Resident: routes each request, chat, quick answers. Instant, always hot in RAM |
| fast | Qwen3 8B | The tool-work floor — anything that writes files or runs commands runs at least here |
| brain | Qwen3 14B | Planning and architecture only, where deep reasoning earns its load time |

Anything larger than 14B is deliberately unsupported: too slow for interactive use on consumer hardware. Routing is small-model-first with a work floor: regex heuristics answer small talk and capability questions on the resident 1.5B for free; only the ambiguous middle spends a one-word classification; and any turn that will run tools escalates to the 8B (`router.work_min_role`), because live testing showed a 1.5B is an excellent router and conversationalist but drops tool calls as prose. On CPU-only laptops the 14B is skipped in favour of the 8B (`prefer_fast_on_slow`). A `/turbo` profile can swap the resident model to a 0.5B on the slowest machines.

## The components

| Component | Where | What it does |
|---|---|---|
| Agent loop | `gt/agent.py` | Bounded tool loop (max 20 steps) with a **hybrid tool protocol**: native function calling on models whose chat template supports it (asked of Ollama once per model, cached), portable prompt-JSON on the rest — so Qwen and anything else behave identically. Anti-stall rails: repeated-failure refusal, per-tool error cut-off, mimicry and code-dump detection with corrective nudges |
| Intent gate | `gt/intent.py` | One small triage call before a new build-shaped task scores confidence in the main reading. Deterministic thresholds: ≥75 builds, 45–74 plans first (plan turns are enforced at the *tool* level — a planning turn physically cannot write), <45 asks exactly one question. Fail-open: it adds care, never blocks work |
| Task checklist | `write_todos` in `gt/tools.py` | Multi-step work lives on an explicit checklist, re-injected every turn and shown with `/todos` — the plan survives interruptions, model swaps and context compaction |
| Permissions | `gt/permissions.py` | Every state-changing action asks first: yes once / always allow / no. Grants are readable (`files`, `docs`, `cmd:git` prefixes) and persist locally. Destructive commands (rm -rf variants, forced pushes, pipe-to-shell, encoded PowerShell, registry writes, fork bombs) **always** prompt, even with standing grants or auto-approve on. Chained commands are split — every segment needs its own grant. Only explicit answer tokens grant; a stray word can never be read as permission |
| Workspace confinement | `gt/base.py` | File and command access is confined to the folder GT was launched from. Any path that escapes it always prompts and can never be remembered as a grant |
| Tools | `gt/tools.py`, `gt/office.py` | 17 tools: file read/write/edit/list/search, shell execution (per-call timeout, process-tree kill, background mode for dev servers), process management, research sub-agents, memory recall, one-question-at-a-time user prompts, optional web search, and real document output through openpyxl, python-pptx and python-docx. All pure Python, no native builds |
| Sub-agents | `gt/subagent.py` | `run_agent` sends a research task into a separate context on the already-loaded model. Strictly read-only (no writes, no commands, no permission prompts), step-budgeted, cannot nest — only its short report returns, so broad exploration never floods the conversation |
| Lifecycle hooks | `gt/hooks.py` | User-authored shell commands at fixed points (session start/end, per prompt, before/after each tool, per turn). A `pre_tool` hook exiting 2 **blocks** the call — a hard policy guarantee, not a prompt suggestion. Fail-open with timeouts so a broken hook never bricks the agent. **Off by default** — enabling means a config file grants code execution |
| Skills | `skills/*.md` | Twelve first-party playbooks (Excel, PowerPoint, Word, frontend, backend, testing, git, data, debugging, code quality, project setup, conversation), selected by embedding similarity with keyword fallback, at most two per request. Zero third-party content by policy; teams add their own as markdown in git |
| Memory | `gt/memory.py` | Local sqlite with nomic-embed-text vectors and cosine search in numpy. Notes, indexed docs (`/index` for RAG), lessons. Nothing stored outside the machine; `/forget` wipes any class of it |
| Project memory | `gt/project_memory.py` | GT's CLAUDE.md equivalent: a `GT.md` per project (plus a user-level and a git-ignored local layer) loaded into every work turn. `/init` writes one by exploring the project; `# a note` at the prompt appends to it |
| Auto-compaction | `gt/agent.py` | When history outgrows its bound, the oldest exchanges are distilled into a rolling session summary on the resident model instead of being dropped — early decisions survive a long build. `/compact` runs it on demand |
| Self-improve | `gt/improve.py` | After a failed or corrected task a reviewer model can distil one reusable lesson into memory — learning by retrieval: transparent, inspectable (`/lessons`), reversible. **Off by default**: a lesson drawn from client work could bake client details into local storage. A noise filter rejects malformed lessons on save *and* recall |
| Preference profile | `gt/profile.py` | Optional, **off by default**, periodic-only analyst pass that distils working preferences. Glass-box: `/profile` shows it, one-time consent notice, `clear` wipes it |
| Routing | `gt/router.py` | The speed ladder above, plus the hardware probe that flags CPU-only machines |
| Hardware wizard | `gt/machine.py`, `gt/wizard.py` | First-launch hardware probe and per-machine model selection (below). Versioned: a lineup change ships to existing installs automatically on the next launch |
| LLM client | `gt/llm.py` | Native Ollama `/api/chat`: models stay hot between requests (`keep_alive: 8h`), reasoning mode off by default for speed, exact per-turn metrics (load / prefill / tok/s) shown to the user. Stall-tolerant: a CPU prefill can legitimately run 10+ minutes silently, so the no-bytes budget is 30 minutes and the error message diagnoses RAM pressure rather than blaming the server |
| Terminal UX | `gt/ui.py`, `gt/interrupt.py` | Streaming preview, syntax-highlighted code in answers and write-approval prompts, compact one-line tool steps, **Esc interrupts a running reply** (partial work is kept), timing line after every response |

## No server required

Everything runs on the device:

- **Models** — Ollama serves from localhost
- **Memory** — a sqlite file in the install folder
- **Settings and permissions** — local JSON

There is no backend, no database server, no API key and no account. Network egress is **off by default**: web search/fetch is the only network feature, it ships disabled (`web.enabled: false`), and while disabled the tools are removed from the model's view entirely — the machine can be fully offline and GT Code keeps working. A grep of the source finds exactly one external endpoint (DuckDuckGo, inside the disabled web tool) and no telemetry, analytics or update-check code of any kind.

For compliance this means there is no data flow to assess beyond the laptop itself. Client code and data physically cannot leave the device, because there is nowhere for them to go.

## Running on any GT laptop

Installation is three commands on Windows, macOS or Linux. Python 3.10+ is the only prerequisite:

```bat
git clone https://github.com/Starfish124/GT-code.git
cd GT-code
setup.bat          ::  macOS/Linux:  ./setup.sh
```

The setup script creates its own isolated environment, installs Ollama if missing, pulls a ~1.3 GB baseline, and installs a global `gt` command that it self-tests from another folder. Projects need no setup of their own — `gt` operates on whatever folder you launch it from (and warns if you launch it from its own install folder).

The known constraint: GT laptops are not all the same. GT Code handles this with a hardware wizard on first launch. It probes RAM, CPU and GPU using only standard-library calls, tells the user plainly what the machine can handle, and downloads only the models they approve:

| Tier | Hardware needed | brain | fast | tiny |
|---|---|---|---|---|
| Full | 16 GB RAM or 11 GB VRAM | qwen3:14b | qwen3:8b | qwen2.5:1.5b |
| Standard | 10 GB RAM or 7 GB VRAM | qwen3:8b | qwen3:8b | qwen2.5:1.5b |
| Minimum | anything less | qwen2.5:1.5b | qwen2.5:1.5b | qwen2.5:1.5b |

The wizard's choice is saved locally and re-checked against what Ollama actually serves at every launch, with graceful fallback; when the recommended line-up changes in a release, existing installs migrate automatically on their next launch. `/setup` re-evaluates after a hardware change; `/doctor` prints a full report (hardware, live models, tool protocol per model, routing mode), and a standalone `doctor.bat`/`doctor.sh` diagnoses the whole launch chain for support.

In practice the same install works across the whole fleet. A 32 GB machine gets the full three-model ladder, a 16 GB machine the same, a 10 GB machine works with the 8B as its brain, and even the smallest laptop still runs the 1.5B and stays useful for everyday tasks. Nobody is blocked by their hardware — the experience scales with it.

## Compliance summary

- **No data leaves the device.** Local models via Ollama on localhost, local sqlite memory, local config. No cloud endpoint, no vendor processing GT or client data, no telemetry — verified by inspection, not just asserted
- **Offline by default.** Web access ships disabled and is a single switch; off means zero network activity
- **Privacy-sensitive features are opt-in, never opt-out.** Lesson-learning, preference profiling and lifecycle hooks all ship disabled, each for a stated reason; profiling shows a one-time consent notice; `/forget` wipes notes, lessons, docs, history, logs or everything
- **Human in control.** Permission prompts on every meaningful action, a hard block on destructive commands that no grant or auto-approve can silence, workspace confinement on file and shell access, an Esc key that stops the model mid-reply, and every step visible on screen as it happens
- **Corporate-clean licensing.** The entire model line-up (Qwen 2.5 0.5B/1.5B, Qwen3 8B/14B, nomic-embed-text) is Apache-2.0 as published by the model owners — no Meta community licence anywhere in the stack
- **Zero third-party content.** All twelve playbooks and every prompt are first-party; nothing is copied from other AI products
- **Reviewable and tested.** ~7,300 lines of Python, small enough to audit in a day, with an offline test suite of 496 checks covering the permission system, web gating, confinement, routing and tool protocol — regressions of real observed failures are pinned as tests

## Honest limitations

- **CPU prefill latency.** On laptops without a dedicated GPU, prompt prefill on the 8B/14B can take minutes (documented live: ~13 minutes under memory pressure). GT mitigates — resident small model, bounded history, models held in RAM, honest progress display — but cannot remove the hardware floor
- **Small-model quirks.** Local 1.5B–14B models occasionally mangle a tool call or overstate what they did; the agent loop's rails (stall detection, mimicry detection, checklist re-injection, verify-before-report prompting) reduce but do not remove this. Quality sits below frontier cloud models — that is the explicit trade for full data locality
- **Open items for a security review.** Memory is per-user but not per-project isolated and not encrypted at rest; dependencies are not yet pin-locked with hashes; the repository has no LICENSE file (an internal-IP decision for the firm, deliberately not made unilaterally)

## Status and next step

Production-ready for individual use: v0.7.4, demonstrated to compliance and the technical lead on 14 July 2026, in user testing with Dylan and Max, feedback being processed.

Requested next step: a formal security review to approve GT Code as internal tooling for builders across Advisory, using the open items above as the review's starting scope.
