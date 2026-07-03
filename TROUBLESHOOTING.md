# GT-Code — Troubleshooting Guide

**Start here, always:**

```bat
cd GT-code
doctor.bat          ::  macOS/Linux:  ./doctor.sh
```

The doctor checks every link in the chain, top to bottom, and tells you the fix
for the **first `[FAIL]`** it finds. If you're helping someone remotely, have
them send you the full doctor output — it answers 90% of questions.

## How GT actually launches (so the errors make sense)

```
you type "gt"
   │
   ▼
1. your terminal searches PATH ──────────── "'gt' is not recognized" fails HERE
   │
   ▼
2. finds the shim  (%USERPROFILE%\.gt\bin\gt.cmd  /  ~/.local/bin/gt)
   │
   ▼
3. shim starts GT-code\.venv\Scripts\gt.exe ── "No module named gt" fails HERE
   │        (GT's OWN venv — never your project's)
   ▼
4. GT loads config.yaml ─────────────────── "Config error" fails HERE
   │
   ▼
5. GT talks to Ollama on localhost:11434 ── "Can't reach ollama" fails HERE
   │
   ▼
6. Ollama runs the models ───────────────── "model not found" / slowness HERE
```

Each section below matches one link. GT runs from **its own environment** in
the GT-code folder and operates on **whatever folder you launch it from** —
your projects never need a venv or any setup.

---

## 1. `'gt' is not recognized as an internal or external command` (Windows)

Your terminal can't find the `gt` command on PATH.

**Step 1 — the boring one first: open a NEW terminal.** PATH changes never
reach terminals that were already open. This alone fixes most cases.

**Step 2 — update + re-run setup** (older installs used a location that
corporate laptops don't have on PATH):

```bat
cd GT-code
git pull
setup.bat
```

Watch the last lines. You want: `Self-test OK: "gt" works from any folder.`

**Step 3 — check the shim exists:**

```bat
dir %USERPROFILE%\.gt\bin
```

You should see `gt.cmd`. Missing → setup didn't finish; re-run `setup.bat`
and read its output for the first error.

**Step 4 — check PATH really contains it:**

```bat
echo %PATH%
```

Look for `C:\Users\<you>\.gt\bin`. If it's not there:

1. Press the Windows key, type **environment variables**, open
   *"Edit environment variables for your account"*.
2. Select the **Path** row in the top (User) list → **Edit** → **New** →
   enter `%USERPROFILE%\.gt\bin` → OK, OK.
3. Open a **new** terminal and try `gt` again.

**Step 5 — corporate laptop specials.** On managed machines, two more things
can bite:

- *Group policy rewrites PATH at login* — if `gt` works today and dies
  tomorrow, your PATH edit was reverted centrally. Use the fallback below and
  flag it to IT.
- *OneDrive-redirected profiles* — if `echo %USERPROFILE%` shows a OneDrive
  path, everything still works, but sync conflicts can corrupt
  `gt.cmd`; delete `%USERPROFILE%\.gt\bin\gt.cmd` and re-run `setup.bat`.

**The fallback that always works** (no PATH involved):

```bat
C:\path\to\GT-code\start.bat
```

or directly: `C:\path\to\GT-code\.venv\Scripts\gt.exe`

## 2. `gt: command not found` (macOS / Linux)

Same story, Unix flavor. In order:

```bash
cd GT-code && git pull && ./setup.sh      # 1. refresh the install
ls -l ~/.local/bin/gt                     # 2. shim exists?
echo $PATH | tr ':' '\n' | grep local     # 3. ~/.local/bin on PATH?
```

If step 3 shows nothing, add this line to `~/.zshrc` (macOS) or `~/.bashrc`
(Linux), then open a new terminal:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

Fallback that always works: `/path/to/GT-code/start.sh`

## 3. `No module named gt`

The shim found Python but the `gt` package isn't installed in the venv —
an install from before v0.2, or a half-finished setup.

```bat
cd GT-code
git pull
setup.bat                                 ::  ./setup.sh
```

Verify it's actually installed:

```bat
.venv\Scripts\python.exe -m pip show gt-code       ::  ./.venv/bin/python -m pip show gt-code
```

You should see `Name: gt-code` and `Version: 0.2.x`. If pip errors here,
send that exact output — it's the root cause.

## 4. `Config error: config.yaml not found`

GT looks for its config in this order:

1. `$GT_CONFIG` / `%GT_CONFIG%` (if you set it)
2. `config.yaml` in the GT-code folder (normal git-clone install)
3. `~/.gt/config.yaml` (auto-created with defaults if nothing else exists)

So this error basically only happens if the GT-code folder was moved or
half-deleted. Re-clone, or delete a stale `%GT_CONFIG%` variable. GT's data
(memory, permissions, downloaded-model markers) lives **next to the config**,
never inside your projects.

## 5. `Can't reach ollama at http://localhost:11434`

**Is it installed?** `ollama --version`
→ no: `winget install -e --id Ollama.Ollama` (Windows) /
`brew install ollama` (macOS), then reopen the terminal.

**Is the server running?**

```bat
ollama list
```

- Errors → start it: on Windows launch **Ollama** from the Start menu (look
  for the llama icon in the system tray); on macOS/Linux run `ollama serve &`.
  Wait ~10 seconds, retry.
- `ollama list` works but GT still can't connect → something owns the port or
  a proxy interferes; check `netstat -ano | findstr 11434` (Windows) /
  `lsof -i :11434` (macOS/Linux).

**Corporate proxy note:** Ollama and pip both need direct internet or proxy
settings. If downloads fail on the office network:

```bat
set HTTPS_PROXY=http://your-proxy:port        ::  export HTTPS_PROXY=... on Unix
```

then re-run `setup.bat` / the `ollama pull`. (Model *inference* is fully
local — the proxy only matters for downloads.)

## 6. Wizard / model problems

**"model not found, try pulling it"** — the wizard's download was skipped or
failed. Either re-run it (`/setup` inside GT) or pull by hand:

```bat
ollama pull llama3.2:3b          :: minimum for GT to function
ollama pull nomic-embed-text     :: memory/RAG
ollama pull qwen3:8b             :: standard tier
ollama pull qwen3:14b            :: full tier (16 GB+ RAM)
```

**Downloads keep dying** — usually disk space (a 14B needs ~10 GB free) or
the proxy issue above. `ollama pull` resumes, so just re-run it.

**GT picked models that feel too slow** — the wizard maps hardware to a tier
(3B / 8B / 14B). Check its reasoning with `/doctor` inside GT. Downgrade
anytime: `/setup`, then decline the big model and GT falls back. First
response after launch is always slower — that's the model loading into RAM.

**Wrong tier detected** (e.g. VM with weird hardware reporting): edit
`config.yaml` `models:` section by hand and set every role to a model you
know runs well; the wizard respects served models at each launch.

## 7. GT starts but behaves oddly

| Symptom | Explanation / fix |
|---|---|
| GT operates on the wrong folder | GT works on the folder you *launched it from*. `gt C:\my\project` or `/cd` inside GT |
| Every action asks permission | That's the default. Answer `a` to always-allow that kind of action, `/auto` to approve everything, `/permissions` to review |
| GT refuses a command even with `/auto` | Destructive patterns (`rm -rf`, `format`, `del /s`, force-push…) always prompt. By design |
| Answers reference stale facts about your code | Clear learned memory: `/forget lesson` or `/forget all` |
| Streaming output garbles the terminal | Use Windows Terminal (not legacy `cmd` console); `winget install Microsoft.WindowsTerminal` |
| Web search fails | Offline or blocked network; set `web.enabled: false` in config.yaml to hide web tools |

## 8. The nuclear option — clean reinstall (2 minutes)

Nothing below touches your projects; GT's own state is all inside GT-code
and `~/.gt`.

```bat
cd GT-code
rmdir /s /q .venv data          ::  Unix:  rm -rf .venv data
git pull
setup.bat                       ::  ./setup.sh
```

Models already downloaded by Ollama survive (they live in Ollama's own
storage), so the reinstall is fast. To also reset GT's memory/permissions on
a pip-style install, delete `~/.gt` too.

## 9. Reporting a problem

Send these three things and it's usually a one-reply fix:

1. Full output of `doctor.bat` / `./doctor.sh`
2. Your OS + terminal (e.g. "Windows 11, company laptop, Windows Terminal")
3. The exact command you typed and the full error (screenshot fine)
