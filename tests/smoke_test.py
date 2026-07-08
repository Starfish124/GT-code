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
check("all 15 tools registered", len(REGISTRY) == 15)
check("process tools registered",
      {"check_process", "stop_process"} <= set(REGISTRY))
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
skills = load_skills()
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
check("no match -> no injection", select(skills, "hello there") == [])
check("block renders with header",
      skills_block(select(skills, "excel please")).startswith("# Expert playbooks"))
check("playbooks stay context-lean (<700 words each)",
      all(s.words < 700 for s in skills))

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
import inspect as _inspect
from gt.llm import LLM
check("chat() default temperature is None (config-driven, not a hardcoded 0.3)",
      _inspect.signature(LLM.chat).parameters["temperature"].default is None)

print("\nrouter heuristics (no LLM call) — the 3B/8B/14B speed ladder")
r = Router(llm=None, config=cfg)
check("small talk -> tiny", r.route("hi") == "tiny")
check("everyday coding -> fast 8B", r.route("fix the bug in main.py") == "fast")
check("architecture/planning -> brain 14B",
      r.route("design the architecture for a new app") == "brain")
check("very long spec -> brain", r.route("please " + "explain " * 100) == "brain")
check("router default is fast", r.default_role == "fast")
check("new app with frontend+backend -> brain (the transcript case)",
      r.route("make a simple frontend and backend and host it") == "brain")
check("build a react app -> brain", r.route("build a react app") == "brain")
check("everyday frontend fix -> fast",
      r.route("fix the css on my website") == "fast")
check("hosting/deploy chatter -> fast, not the 3B classifier",
      r.route("deploy the server to port 5000") == "fast")

print(f"\n{'='*40}\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
