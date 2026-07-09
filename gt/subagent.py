"""Sub-agents — GT's Task tool (Claude Code's Agent/Task, scaled down).

Failure modes #5 (monolithic context) and #8 (no sub-agents): a broad
exploration done in the MAIN loop — read fifteen files, grep the tree, chase
a question across the web — pours every observation into the conversation,
where it bloats the prompt (and prefill time) for the rest of the session and
buries the plan the model is supposed to be working. The fix is delegation:
the main agent hands a one-job brief to a sub-agent that runs its own tool
loop in a FRESH message list, and only the final report comes back.

Deliberately scaled to GT's constraints:
  - Same model, new context. The sub-agent runs on the role that spawned it —
    already loaded, so there is no swap churn on a one-model box. What it buys
    is context isolation, not parallelism.
  - READ-ONLY toolset (read/list/search/recall/web). No writes, no commands —
    so it never needs a permission prompt and can be left to work; anything
    that would need changes belongs in its report, for the main agent to do.
  - No nesting, no questions: a sub-agent cannot spawn another one (run_agent
    isn't in its toolset; Ctx.spawn is None) and cannot ask_user — if the
    brief is ambiguous it investigates the likely reading and says so.
  - Step-budgeted: after subagents.max_steps tool calls it is told to write
    the report from what it has. The report is capped before it returns.
"""

import platform
from pathlib import Path

from .base import Ctx
from .llm import LLMError

# The safe subset a sub-agent may use — observation only, nothing that
# changes the machine or blocks on the user.
READONLY_TOOLS = ("read_file", "list_dir", "search_files", "recall",
                  "web_search", "web_fetch")

SUB_SYSTEM = """You are a research sub-agent inside GT-Code, a local coding \
agent. You were spawned for exactly ONE job — the brief in the user message. \
Investigate it with your read-only tools, then end with ONE final report. \
Nobody sees your intermediate steps: ONLY your final message returns to the \
main agent, so the report must stand alone — name exact files, paths, line \
numbers, versions and commands you found, quote the snippets that matter, \
and say plainly what you could NOT find or verify.

# Workspace
Working directory: {cwd}
Operating system: {os}

# How to act
Respond with ONE fenced json block per step and nothing else:

```json
{{"tool": "<tool_name>", "args": {{ ... }}}}
```

When you have what you need (or your tools can't get more), reply in plain \
prose with NO json block — that prose IS your report.

Rules:
- Investigate, never guess: your FIRST step is list_dir on "." or a \
search_files for the key term. Only read_file paths you have actually seen \
in a listing or a search result — invented paths just waste your steps.
- Use RELATIVE paths, exactly as they appear in listings and results (e.g. \
"src/settings.py", "src" for a folder). NEVER construct an absolute path — \
long absolute paths get mistyped and fail.
- You are READ-ONLY. You cannot write files, run commands, or change \
anything. If the job would need changes, gather the facts and put the \
recommendation in your report.
- You cannot ask anyone questions — never address the user. If the brief is \
ambiguous, investigate the most likely reading and note the assumption.
- Stay in the workspace: use web_search/web_fetch ONLY when the brief \
explicitly needs outside information (a library's docs, an error message). \
What a workspace file literally says ALWAYS outranks a web result — never \
let generic web content override something you read in a file.
- Be economical: STOP and report as soon as you have the answer — extra \
tool calls after that only bury it. You have at most {max_steps} calls.
- Report format: findings first, then the evidence. Every claim must come \
from a tool result you actually saw, naming the file it came from — never \
fill gaps from general knowledge.

# Available tools
{tools}"""


def subagent_tools(config):
    """The read-only toolset (respects web.enabled; excludes run_agent)."""
    from .tools import active_tools
    return [t for t in active_tools(config) if t.name in READONLY_TOOLS]


# What each tool acted on, for the one-line step trace.
_TARGET_ARGS = ("path", "query", "url", "id")


def run_subagent(llm, config, memory, cwd, task, role, console=None):
    """Run one research sub-agent to completion.

    Returns (report, steps_taken). Failures come back as an "ERROR: ..."
    report — the main agent treats it like any failed tool observation.
    """
    from .agent import extract_tool_call          # runtime import: no cycle
    from .tools import tool_docs

    sub_cfg = getattr(config, "data", {}).get("subagents", {})
    max_steps = int(sub_cfg.get("max_steps", 8))
    max_report = int(sub_cfg.get("max_report_chars", 3000))
    say = console.print if console else (lambda *a, **k: None)

    tools = {t.name: t for t in subagent_tools(config)}
    system = SUB_SYSTEM.format(cwd=str(cwd), os=platform.system(),
                               max_steps=max_steps,
                               tools=tool_docs(tools.values()))
    # No approve (nothing to approve), no ask (no user), no spawn (no nesting).
    ctx = Ctx(cwd=Path(cwd), memory=memory, approve=lambda *a, **k: False,
              config=config, ask=None, user_msg=task)

    # Seed the workspace listing ourselves: small models GUESS paths when
    # they haven't seen a listing (observed live — a 3B invented config.json
    # and gave up). Handing it the real listing up front costs no step and
    # leaves nothing to guess.
    brief = task
    if "list_dir" in tools:
        try:
            listing = tools["list_dir"].run({"path": "."}, ctx)
            if listing and not listing.startswith("ERROR"):
                # Drop the absolute-path header line: showing the model a long
                # absolute path teaches it to copy (and mistype) it — every
                # name below is usable relative to the workspace as-is.
                if listing.splitlines()[0].rstrip().endswith(":"):
                    listing = "\n".join(listing.splitlines()[1:])
                brief += ("\n\n[workspace listing (relative to the workspace "
                          "root) — already fetched; only read paths shown "
                          "here or found via search_files]\n"
                          + listing[:1000])
        except Exception:
            pass
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": brief}]

    temperature = float(getattr(config, "performance", {}).get("temperature", 0.3))
    steps = 0
    nudged_json = False
    nudged_fail = False
    last_failed = False
    for _ in range(max_steps):
        try:
            reply = llm.chat(role, messages, stream=False,
                             temperature=temperature)
        except LLMError as e:
            return f"ERROR: sub-agent failed: {e}", steps
        messages.append({"role": "assistant", "content": reply})
        call = extract_tool_call(reply)
        if not call:
            text = (reply or "").strip()
            # One retry for the classic small-model failure: an ATTEMPTED
            # tool call with broken JSON must not become the "report".
            if '"tool"' in text and "{" in text and not nudged_json:
                nudged_json = True
                messages.append({"role": "user", "content": (
                    "[system] That looked like a tool call but was not valid "
                    "JSON. Re-send it as ONE complete ```json block — or "
                    "write your final report in plain prose.")})
                continue
            # Same give-up guard as the main loop: prose right after a
            # FAILED step is usually surrender, not a report (observed live —
            # one missing file ended the whole investigation).
            if last_failed and not nudged_fail:
                nudged_fail = True
                messages.append({"role": "user", "content": (
                    "[system] Your last tool call FAILED — that is not the "
                    "end of the job. Use the workspace listing (or "
                    "search_files for the key term) and keep investigating; "
                    "only report once you have checked what IS there.")})
                continue
            return _clip(text, max_report), steps

        name = call.get("tool")
        args = call.get("args") or {}
        tool = tools.get(name)
        if tool is None:
            obs = (f"ERROR: '{name}' is not available to a sub-agent "
                   f"(read-only). Your tools: {', '.join(tools)}. If the job "
                   f"needs changes or commands, recommend them in your report.")
        else:
            try:
                obs = tool.run(args, ctx)
            except Exception as e:
                obs = f"ERROR running {name}: {e}"
        steps += 1
        last_failed = obs.startswith("ERROR")
        target = next((str(args.get(k)) for k in _TARGET_ARGS
                       if args.get(k)), "")
        if len(target) > 60:
            target = "…" + target[-59:]
        say(f"[dim]    agent> {name}  {target}[/dim]")
        if len(obs) > 4000:
            obs = obs[:4000] + "\n... [truncated]"
        messages.append({"role": "user",
                         "content": f"[tool result: {name}]\n{obs}"})

    # Step budget spent — force the report from what it has.
    messages.append({"role": "user", "content": (
        "[system] Step limit reached. Write your final report NOW from what "
        "you already have — plain prose, no tool calls.")})
    try:
        reply = llm.chat(role, messages, stream=False, temperature=temperature)
    except LLMError as e:
        return f"ERROR: sub-agent failed: {e}", steps
    return _clip((reply or "").strip(), max_report), steps


def _clip(text, max_chars):
    if not text:
        return "(the sub-agent returned an empty report)"
    if len(text) > max_chars:
        return text[:max_chars] + "\n… [report clipped]"
    return text
