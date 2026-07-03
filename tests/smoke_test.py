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

print("\nchunking")
chunks = chunk_text("x" * 2500, size=1000, overlap=150)
check("long text splits into overlapping chunks", len(chunks) == 3)
check("empty text -> no chunks", chunk_text("") == [])

print("\ntool registry + docs")
check("all 13 tools registered", len(REGISTRY) == 13)
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
# cleanup
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
store.unlink(missing_ok=True)

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

print("\nrouter heuristics (no LLM call) — the 3B/8B/14B speed ladder")
r = Router(llm=None, config=cfg)
check("small talk -> tiny", r.route("hi") == "tiny")
check("everyday coding -> fast 8B", r.route("fix the bug in main.py") == "fast")
check("architecture/planning -> brain 14B",
      r.route("design the architecture for a new app") == "brain")
check("very long spec -> brain", r.route("please " + "explain " * 100) == "brain")
check("router default is fast", r.default_role == "fast")

print(f"\n{'='*40}\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
