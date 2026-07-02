# GT-Code — your own local coding agent

A small, self-hosted CLI coding assistant — like Claude Code, but running
**entirely on your machine** against your local models. No cloud, no API keys,
no data leaving your PC.

It routes each request to the right local model, runs an **agentic tool loop**
(read/write/edit files, run commands, search, **web search**), keeps a
**vector memory** of notes and indexed docs, and **improves itself** by
distilling reusable lessons from past work. Answers **stream in as live
markdown** — formatted code, lists, and headings appear as they're generated.

```
You type  ─►  Router (llama3.2:3b picks a model)  ─►  Brain (28B / Qwen 8B)
                                                          │
                        ┌─────────────────────────────────┼──────────────────────┐
                   Agent tool-loop                    RAG memory              Self-improve
              read / write / edit files,        nomic-embed + sqlite       Hermes distills a
              run commands, search files,       cosine vector search       reusable "lesson"
              web search + fetch, recall
```

Your model line-up (edit freely in `config.yaml`):

| Role       | Default model            | Runs on    | Job |
|------------|--------------------------|------------|-----|
| `brain`    | your 28B                 | LM Studio  | Heavy reasoning & coding |
| `fast`     | `qwen3:8b`               | Ollama     | Medium tasks, quick help |
| `tiny`     | `llama3.2:3b`            | Ollama     | Router + trivia (instant) |
| `reviewer` | `hermes`                 | LM Studio  | Extracts lessons (self-improve) |
| `embed`    | `nomic-embed-text`       | Ollama     | Embeddings for memory/RAG |

---

## Part 1 — What you need on the Windows PC

1. **Python 3.10+** — https://www.python.org/downloads/
   During install, **check “Add python.exe to PATH.”**
2. **Ollama** — https://ollama.com/download (runs a local server on `:11434`)
3. **LM Studio** — https://lmstudio.ai (hosts your bigger models on `:1234`)
4. This **`GT-code` folder**, copied anywhere on the PC (e.g. `C:\GT-code`).

---

## Part 2 — Pull the Ollama models

Open a terminal (PowerShell or CMD) and run:

```bat
ollama pull qwen3:8b
ollama pull llama3.2:3b
ollama pull nomic-embed-text
```

Verify they’re there:

```bat
ollama list
```

Ollama’s server starts automatically. You can confirm it’s up by opening
http://localhost:11434 in a browser (it should say “Ollama is running”).

---

## Part 3 — Load the LM Studio models & start its server

1. Open **LM Studio**.
2. Download / confirm your **28B model** and **Hermes** (Search tab).
3. Go to the **Developer** tab (the `>_` icon) → **Start Server**.
   It should say it’s listening on `http://localhost:1234`.
4. **Load** the model(s) you want served (top model selector). LM Studio serves
   whichever models are loaded; you can load more than one.

> Tip: keep the server running whenever you use GT. If GT says “Can’t reach
> lmstudio,” this server isn’t started.

---

## Part 4 — Install GT

Double-click **`setup.bat`** (or run it from a terminal in the folder).
It creates a private virtual environment in `.venv\` and installs the six
dependencies. One-time, ~1 minute.

If Windows SmartScreen blocks the `.bat`, right-click → **Run anyway**, or run
it from a terminal:

```bat
cd C:\GT-code
setup.bat
```

---

## Part 5 — Point GT at your real model names (important!)

Model ids differ between machines. Launch GT once and ask it what’s actually
being served:

```bat
start.bat
```

At the `gt›` prompt, type:

```
/models
```

You’ll get two tables — the exact ids from Ollama and LM Studio. Open
**`config.yaml`** and replace the values marked `<-- CONFIRM` with those exact
strings. The most important one is `brain` (your 28B in LM Studio); the guessed
name almost certainly won’t match.

Example, if `/models` showed your LM Studio 28B as `glm-4-9b-chat` and Hermes as
`hermes-3-llama-3.1-8b`:

```yaml
models:
  brain:
    provider: lmstudio
    model: "glm-4-9b-chat"
  reviewer:
    provider: lmstudio
    model: "hermes-3-llama-3.1-8b"
```

Save the file. (No restart tricks needed — just relaunch `start.bat` after edits.)

---

## Part 6 — Use it

```bat
start.bat
```

On launch GT pings both providers so you know everything’s connected:

```
✓ ollama: 3 model(s) at http://localhost:11434/v1
✓ lmstudio: 2 model(s) at http://localhost:1234/v1
```

Now just talk to it. Some things to try:

```
gt› what files are in this folder?
gt› create a python script hello.py that prints the fibonacci sequence to 100
gt› run hello.py
gt› read hello.py and add a docstring
gt› /remember I prefer type hints and f-strings in Python
gt› /index C:\my-project\src        (index a codebase for Q&A)
gt› how does the auth flow work in the code I just indexed?
```

GT will show which model it routed to, each tool it calls (with a preview), and
ask permission before writing files or running commands (unless you `/auto`).

---

## Commands

| Command | What it does |
|--------|--------------|
| `/help` | List commands |
| `/models` | Show the exact model ids Ollama + LM Studio serve |
| `/model <role\|off>` | Pin a model (`brain`/`fast`/`tiny`) or `off` to auto-route |
| `/route` | Toggle smart auto-routing |
| `/auto` | Toggle auto-approve (skip the y/N prompts) |
| `/cd <path>` | Change the working directory GT operates in |
| `/index <path>` | Embed a file/folder into memory for RAG |
| `/remember <text>` | Save a note to long-term memory |
| `/lessons` | Show lessons GT has learned about itself |
| `/memory` | Memory stats |
| `/forget <note\|lesson\|doc\|all>` | Clear memory |
| `/reset` | Clear the current conversation |
| `/quit` | Exit |

---

## How it works (the interesting part)

**Routing** (`gt/router.py`). Cheap heuristics run first (small talk → `tiny`,
anything with code/file signals or long prompts → `brain`). Only genuinely
ambiguous requests cost a one-word classification from the 3B model. Pin a model
anytime with `/model brain`.

**Agent loop** (`gt/agent.py`). GT uses a **prompt-based tool protocol**, not
native function-calling, so it behaves identically across GLM, Hermes, Qwen, and
Llama regardless of backend quirks. The model emits a single JSON block to call
a tool; GT runs it, feeds back the result, and loops until the model returns a
plain-text answer or hits `max_steps`. Output **streams as live markdown**
(`gt/ui.py`) — no raw-then-reformat flicker.

**Web access** (`gt/tools.py`). `web_search` (keyless, via DuckDuckGo) returns
titles/urls/snippets, and `web_fetch` downloads a page and strips it to readable
text. Both need internet and fail gracefully offline. To keep GT fully
air-gapped, set `web.enabled: false` in `config.yaml` and the tools disappear
from GT's toolset entirely.

**Memory / RAG** (`gt/memory.py`). `nomic-embed-text` turns text into vectors
stored in a plain **sqlite** file; search is brute-force cosine similarity in
numpy. No Chroma/FAISS/native builds to fight on Windows. Three kinds share one
store: `note` (things you tell it), `doc` (indexed files), `lesson` (below).
Relevant memories are pulled into context automatically each turn.

**Self-improvement** (`gt/improve.py`). After each task, **Hermes** reads the
interaction and tries to distill one reusable, generalizable lesson (“Prefer
editing over rewriting whole files,” etc.). Lessons go into memory and resurface
on similar future requests. It’s learning by *retrieval*, not fine-tuning — fully
local, transparent, and reversible (`/forget lesson`).

**Safety.** `write_file`, `edit_file`, and `run_command` ask for approval before
touching your machine. `/auto` turns that off when you trust it.

---

## Configuration reference (`config.yaml`)

```yaml
router:
  enabled: true      # auto-pick a model per request
  default: brain     # fallback when unsure
agent:
  max_steps: 12      # tool iterations before GT must answer
  auto_approve: false
memory:
  auto_learn: true   # extract a lesson after each task
  recall_k: 5        # memories pulled into context per turn
  min_score: 0.28    # similarity floor (0..1)
web:
  enabled: true      # web_search / web_fetch tools; false = fully offline
```

To use **only Ollama** (skip LM Studio), point `brain` and `reviewer` at Ollama
models too — e.g. set both providers to `ollama` and pick larger local models.

---

## Extending GT

**Add a tool** — in `gt/tools.py`, subclass `Tool`, then add it to `ALL_TOOLS`:

```python
class WordCount(Tool):
    name = "word_count"
    description = "Count the words in a file."
    args = {"path": "File to count."}
    def run(self, args, ctx):
        p = ctx.resolve(args["path"])
        return f"{len(p.read_text(encoding='utf-8').split())} words"

ALL_TOOLS = [..., WordCount()]
```

The system prompt is generated from the registry, so the model learns the new
tool automatically — no other changes needed. (Web search, file editing, and
shell access already ship built-in — see `gt/tools.py` for examples.)

**Swap models** — just edit the role → model mapping in `config.yaml`.

**Change GT’s personality / rules** — edit `SYSTEM_TEMPLATE` in `gt/prompts.py`.

---

## Troubleshooting

| Symptom | Fix |
|--------|-----|
| `Can't reach lmstudio` | Open LM Studio → Developer tab → **Start Server**, and load a model |
| `Can't reach ollama` | Ensure Ollama is installed/running (open http://localhost:11434) |
| `HTTP 404 ... model not found` | The id in `config.yaml` doesn’t match `/models`. Fix it. |
| `model not found, try pulling it` | `ollama pull nomic-embed-text` (and the others) |
| `'python' is not recognized` | Reinstall Python with **Add to PATH**, or use `py -3` |
| First reply from the 28B is slow | Normal — LM Studio loads it into RAM/VRAM on first call |
| Router feels laggy | `/route` off, or `/model brain` to pin the big model |
| `web search failed (offline?)` | No internet, or set `web.enabled: false` to hide the web tools |

---

## Verify the install (optional)

A dependency-free logic test (no models needed) lives in `tests/`:

```bat
.venv\Scripts\python.exe -m tests.smoke_test
```

It checks config loading, tool-call parsing, chunking, the file tools, web-tool
gating + URL parsing, the streaming renderer, and the router heuristics. All 25
checks should pass.

---

## Layout

```
GT-code/
  config.yaml          your models + settings  (edit this)
  requirements.txt     dependencies
  setup.bat / .sh      one-time install
  start.bat / .sh      launch GT
  gt/
    cli.py             the terminal + slash commands
    router.py          picks a model per request
    agent.py           the agentic tool loop
    tools.py           file / shell / search / web / recall tools
    memory.py          sqlite + nomic-embed vector store
    improve.py         self-improving lesson extractor
    llm.py             OpenAI-compatible client (Ollama + LM Studio)
    ui.py              live streaming-markdown renderer
    prompts.py         system prompt
    config.py          config loader
  tests/
    smoke_test.py      offline sanity checks
```

Built to run offline, on your hardware, under your control. Have fun. 🛠️
