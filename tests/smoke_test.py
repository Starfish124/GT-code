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
check("all 9 tools registered", len(REGISTRY) == 9)
check("tool_docs mentions run_command", "run_command" in tool_docs())
check("tool_docs mentions web_search", "web_search" in tool_docs())

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
ctx = Ctx(cwd=tmp, memory=None, approve=lambda *_: True, config=cfg)
WriteFile().run({"path": "hello.txt", "content": "hi there"}, ctx)
check("read_file returns what write_file wrote",
      ReadFile().run({"path": "hello.txt"}, ctx) == "hi there")
check("list_dir sees the new file", "hello.txt" in ListDir().run({"path": "."}, ctx))
check("approval denial blocks a write",
      "DENIED" in WriteFile().run(
          {"path": "no.txt", "content": "x"},
          Ctx(cwd=tmp, memory=None, approve=lambda *_: False, config=cfg)))
# cleanup
(tmp / "hello.txt").unlink(missing_ok=True)
tmp.rmdir()

print("\nrouter heuristics (no LLM call)")
r = Router(llm=None, config=cfg)
check("small talk -> tiny", r.route("hi") == "tiny")
check("code hint -> brain", r.route("fix the bug in main.py") == "brain")
check("long prompt -> brain", r.route("please " + "explain " * 60) == "brain")

print(f"\n{'='*40}\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
