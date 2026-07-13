"""Deterministic lifecycle hooks — user-owned guarantees around GT's loop.

Claude Code's hooks, ported (roadmap #5). Prompts STEER a model; hooks
GUARANTEE. Some rules must always hold no matter what the model decides —
never touch .env, lint after every write, log every turn, ping me when a
session ends. A hook is a shell command of YOURS, declared in config.yaml,
that GT executes at a fixed lifecycle point, every time, deterministically.

Events:
  session_start / session_end   GT launches / quits
  user_prompt                   before a turn — stdout becomes [context]
  pre_tool                      before every tool call — exit code 2 BLOCKS it
  post_tool                     after a tool call — stdout is appended to the
                                tool result the model sees
  turn_end                      after GT's final answer each turn

The contract (mirrors Claude Code):
  - every hook gets a JSON payload on stdin, plus GT_EVENT / GT_TOOL / GT_CWD
    environment variables (so a simple .bat needn't parse JSON)
  - `match` is a regex on the tool name (pre_tool/post_tool; omit = all tools)
  - exit 0: fine — stdout is used where the event allows
  - exit 2 on pre_tool: BLOCK the call; stdout+stderr are given to the model
  - anything else (other exit codes, timeouts, crashes) fails OPEN: GT warns
    and carries on. A broken hook must never brick the agent — only a
    deliberate exit 2 stops work.
"""

import json
import os
import re
import subprocess


class Hooks:
    EVENTS = ("session_start", "session_end", "user_prompt",
              "pre_tool", "post_tool", "turn_end")

    def __init__(self, config, console):
        cfg = (getattr(config, "data", {}) or {}).get("hooks", {}) or {}
        # Secure-by-default: hooks run arbitrary shell commands, so an absent
        # key means OFF. They must be explicitly enabled in a trusted config.
        self.enabled = bool(cfg.get("enabled", False))
        self.timeout = int(cfg.get("timeout", 30))
        self.console = console
        self.hooks = {ev: [h for h in (cfg.get(ev) or [])
                           if isinstance(h, dict) and h.get("command")]
                      for ev in self.EVENTS}

    # ---- queries --------------------------------------------------------------

    def any_for(self, event) -> bool:
        return bool(self.enabled and self.hooks.get(event))

    def describe(self):
        """[(event, match, command)] for /hooks (glass-box)."""
        out = []
        for ev in self.EVENTS:
            for spec in self.hooks.get(ev, []):
                out.append((ev, spec.get("match", ""), spec["command"]))
        return out

    # ---- the event API used by the agent/shell --------------------------------

    def pre_tool(self, tool, args, cwd):
        """(allowed, message) — exit code 2 from any matching hook blocks."""
        if not self.any_for("pre_tool"):
            return True, ""
        payload = {"event": "pre_tool", "tool": tool,
                   "args": _slim(args), "cwd": str(cwd)}
        for spec in self._matching("pre_tool", tool):
            code, out, err = self._run_one(spec, payload)
            if code == 2:
                msg = "\n".join(s for s in (out, err) if s).strip() \
                      or "blocked by a pre_tool hook"
                self.console.print(f"[yellow]· hook pre_tool blocked {tool}: "
                                   f"{msg.splitlines()[0][:120]}[/yellow]")
                return False, msg[:1000]
            self._warn_if_odd("pre_tool", spec, code, err)
        return True, ""

    def post_tool(self, tool, args, result, cwd):
        """Extra text to append to the tool result ('' when none)."""
        if not self.any_for("post_tool"):
            return ""
        payload = {"event": "post_tool", "tool": tool, "args": _slim(args),
                   "result": (result or "")[:2000], "cwd": str(cwd)}
        extras = []
        for spec in self._matching("post_tool", tool):
            code, out, err = self._run_one(spec, payload)
            if code == 0 and out:
                extras.append(out)
                self.console.print(f"[dim]· hook post_tool ({tool}) added "
                                   f"{len(out)} chars[/dim]")
            else:
                self._warn_if_odd("post_tool", spec, code, err)
        return "\n".join(extras)[:1500]

    def user_prompt(self, prompt, cwd):
        """Context to inject with the user's message ('' when none)."""
        if not self.any_for("user_prompt"):
            return ""
        payload = {"event": "user_prompt", "prompt": prompt[:2000],
                   "cwd": str(cwd)}
        extras = []
        for spec in self.hooks["user_prompt"]:
            code, out, err = self._run_one(spec, payload)
            if code == 0 and out:
                extras.append(out)
                self.console.print(f"[dim]· hook user_prompt added "
                                   f"{len(out)} chars of context[/dim]")
            else:
                self._warn_if_odd("user_prompt", spec, code, err)
        return "\n".join(extras)[:1500]

    def fire(self, event, payload):
        """Run an event's hooks for their side effects (start/end/turn_end)."""
        if not self.any_for(event):
            return
        for spec in self.hooks[event]:
            code, out, err = self._run_one(spec, {"event": event, **payload})
            if code == 0 and out:
                self.console.print(f"[dim]· hook {event}: "
                                   f"{out.splitlines()[0][:120]}[/dim]")
            else:
                self._warn_if_odd(event, spec, code, err)

    # ---- plumbing --------------------------------------------------------------

    def _matching(self, event, tool):
        for spec in self.hooks.get(event, []):
            pat = spec.get("match")
            if not pat or re.search(pat, tool):
                yield spec

    def _run_one(self, spec, payload):
        """(exit_code, stdout, stderr) — (None, '', reason) on timeout/crash."""
        cmd = spec["command"]
        env = dict(os.environ,
                   GT_EVENT=str(payload.get("event", "")),
                   GT_TOOL=str(payload.get("tool", "")),
                   GT_CWD=str(payload.get("cwd", "")))
        try:
            p = subprocess.run(
                cmd, shell=True, cwd=payload.get("cwd") or None, env=env,
                input=json.dumps(payload), capture_output=True,
                encoding="utf-8", errors="replace",
                timeout=int(spec.get("timeout", self.timeout)))
        except subprocess.TimeoutExpired:
            return None, "", f"timed out: {cmd}"
        except Exception as e:
            return None, "", f"failed to run: {e}"
        return p.returncode, (p.stdout or "").strip()[:2000], \
            (p.stderr or "").strip()[:2000]

    def _warn_if_odd(self, event, spec, code, err):
        # Exit 0 (fine) and 2-on-pre_tool (handled) are expected; everything
        # else is a hook problem — say so, quietly, and fail open.
        if code == 0:
            return
        detail = err or f"exit code {code}"
        self.console.print(f"[dim yellow]· hook {event} "
                           f"({spec['command'][:50]}) — {detail[:120]} "
                           f"(ignored)[/dim yellow]")


def _slim(args):
    """The args dict with huge values (file contents) cut for the payload."""
    if not isinstance(args, dict):      # a model can mis-format args — never
        return {"raw": str(args)[:2000]}  # let that crash a hook, or GT
    slim = {}
    for k, v in args.items():
        s = str(v)
        slim[k] = s[:2000] + "…" if len(s) > 2000 else v
    return slim
