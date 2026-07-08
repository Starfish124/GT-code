"""Build a GT-Code fine-tuning dataset — validated, on-distribution, clean.

Each row is a chat conversation whose SYSTEM message is GT's REAL runtime system
prompt (so the tuned model sees exactly what the agent will feed it). The
assistant turn is either a hand-authored gold answer or a teacher model's answer
that PASSED validation for its task kind:

  tool / build  → must contain a parseable GT tool-call JSON block
  chat / capab. → must NOT contain a tool call (it should just talk)

Teacher is any Apache-2.0 Qwen served by Ollama (qwen3:14b for the real run, or
the already-local qwen2.5-coder:7b). Output is MLX-LM chat JSONL split into
train/valid. No third-party datasets, no closed-model output.

Usage:
  python finetune/build_dataset.py --teacher qwen2.5-coder:7b --out finetune/data
  python finetune/build_dataset.py --gold-only --out finetune/data
"""

import argparse
import json
import platform
import sys
from pathlib import Path

import requests

# import GT itself so training matches runtime exactly
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gt.prompts import build_system                       # noqa: E402
from gt.tools import tool_docs, active_tools, capability_summary  # noqa: E402
from gt.agent import extract_tool_call                    # noqa: E402
from gt.config import Config                              # noqa: E402
from finetune.seed_data import GOLD, TASKS                # noqa: E402

OLLAMA = "http://localhost:11434/api/chat"


def gt_system_prompt():
    """GT's exact runtime system prompt (workspace-neutral for training)."""
    cfg = Config.load()
    tools = active_tools(cfg)
    return build_system(cwd="/workspace", os_name=platform.system(),
                        tools=tool_docs(tools),
                        capabilities=capability_summary(tools))


def ask_teacher(model, system, user, temperature, timeout=180):
    if "qwen3" in model.lower():           # keep the teacher's reasoning off
        system = system + "\n/no_think"
    payload = {"model": model, "stream": False, "think": False,
               "options": {"temperature": temperature},
               "messages": [{"role": "system", "content": system},
                            {"role": "user", "content": user}]}
    r = requests.post(OLLAMA, json=payload, timeout=timeout)
    r.raise_for_status()
    return (r.json().get("message") or {}).get("content", "").strip()


def valid_for_kind(kind, text):
    """A row is only kept if the answer matches what the kind demands."""
    if not text:
        return False
    has_call = extract_tool_call(text) is not None
    if kind in ("tool", "build"):
        return has_call                      # must act
    return not has_call                      # chat / capability must just talk


def row(system, user, assistant):
    return {"messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user},
                         {"role": "assistant", "content": assistant}]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--teacher", default="qwen3:14b",
                    help="Ollama model that generates traces (Apache-2.0 Qwen)")
    ap.add_argument("--out", default="finetune/data", help="output dir")
    ap.add_argument("--gold-only", action="store_true",
                    help="skip the teacher; emit only the hand-authored gold set")
    ap.add_argument("--valid-frac", type=float, default=0.12)
    args = ap.parse_args()

    system = gt_system_prompt()
    rows, kept, dropped = [], 0, 0

    # 1) gold — always valid, but sanity-check the tool ones really parse
    for g in GOLD:
        if not valid_for_kind(g["kind"], g["assistant"]):
            print(f"  ! gold example failed its own check ({g['kind']}): "
                  f"{g['user'][:40]}", file=sys.stderr)
            continue
        rows.append(row(system, g["user"], g["assistant"]))
    print(f"gold: {len(rows)} examples")

    # 2) teacher-generated, validated
    if not args.gold_only:
        temp = {"tool": 0.3, "build": 0.3, "chat": 0.7, "capability": 0.5}
        for i, (kind, user) in enumerate(TASKS, 1):
            try:
                ans = ask_teacher(args.teacher, system, user, temp.get(kind, 0.4))
            except Exception as e:
                print(f"  [{i}/{len(TASKS)}] {kind}: teacher error: {e}",
                      file=sys.stderr)
                dropped += 1
                continue
            if valid_for_kind(kind, ans):
                rows.append(row(system, user, ans))
                kept += 1
                mark = "✓"
            else:
                dropped += 1
                mark = "✗ (wrong shape for kind)"
            print(f"  [{i}/{len(TASKS)}] {kind:<10} {mark}  {user[:44]}")
        print(f"teacher: kept {kept}, dropped {dropped}")

    # 3) split + write MLX-LM's train.jsonl / valid.jsonl
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    n_valid = max(1, int(len(rows) * args.valid_frac)) if len(rows) > 8 else 0
    valid, train = rows[:n_valid], rows[n_valid:]
    for name, chunk in (("train", train), ("valid", valid)):
        if not chunk:
            continue
        with (out / f"{name}.jsonl").open("w", encoding="utf-8") as f:
            for r in chunk:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"wrote {out / (name + '.jsonl')}: {len(chunk)} rows")
    print(f"\ntotal usable rows: {len(rows)}")


if __name__ == "__main__":
    main()
