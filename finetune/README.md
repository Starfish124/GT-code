# GT-Code fine-tuning (LoRA)

First-party, license-clean track to bake GT's behaviours into a small local
model's weights — instead of teaching them by prompt every turn. This is
**dev/training tooling**, not part of GT's runtime; it produces an Ollama model
GT then loads like any other.

## Why (and why not)

The prompt/skills work already gets ~80% of the benefit for ~1% of the effort.
LoRA is worth it to make the behaviours we keep patching **reliable in the
weights** on the specific small model:

- emit GT's **tool-call JSON protocol** correctly, first time (the flakiest thing)
- **talk vs build** — answer a question, don't scaffold; build fully when asked
- answer **capability questions** directly ("can you use the internet?")
- never **echo the user's own question** back via ask_user

Trade-off: real dataset + training + eval + export work, plus
catastrophic-forgetting risk. Keep rank small, epochs few, and hold out an eval.

## Decisions (locked)

- **Base to tune:** a small **Qwen (Apache-2.0)** — corporate-clean. Prototype
  on a 3B locally.
- **Trainer:** **MLX-LM LoRA** on Apple Silicon (this 16GB M4 Mac) for the
  prototype. Scale an 8B/14B QLoRA on an NVIDIA GPU later if the 3B proves out.
- **Data:** **distillation from an Apache-2.0 Qwen teacher** (qwen3:14b, or the
  already-local qwen2.5-coder:7b) generating correct GT-protocol traces, plus a
  hand-authored gold seed set. **No third-party datasets, no closed-model
  outputs** — clean provenance for a corporate setting.

## Pipeline

```
seed_data.py     curated tasks + hand-authored GOLD examples (the behaviours)
      │
build_dataset.py teacher (Ollama) answers each task under GT's REAL system
      │          prompt; every row is VALIDATED (tool tasks must parse as a GT
      │          tool call; chat/capability tasks must NOT) → data/*.jsonl
      │
train_lora.sh    MLX-LM LoRA on a Qwen 3B (4-bit) → adapters/
      │
export_to_ollama.sh  fuse adapter → GGUF (llama.cpp) → Modelfile → `ollama create
      │              gt-qwen3-tuned` → set it as a GT role in config.yaml
      │
eval: reuse GT's smoke-style tasks — measure tool-call accuracy + no regressions
```

## Run it

```bash
cd ~/GT-code
python -m pip install -r finetune/requirements.txt        # mlx-lm, requests

# 1. build the dataset (teacher defaults to qwen3:14b; use what's pulled)
python finetune/build_dataset.py --teacher qwen2.5-coder:7b --out finetune/data
#    …or just the guaranteed-correct hand-authored gold set, no teacher:
python finetune/build_dataset.py --gold-only --out finetune/data

# 2. train the LoRA on a local Qwen 3B (4-bit)
bash finetune/train_lora.sh

# 3. export to Ollama and wire into GT
bash finetune/export_to_ollama.sh
```

## Data format

MLX chat JSONL — one conversation per line, trained on the assistant turns:

```json
{"messages":[{"role":"system","content":"<GT's real system prompt>"},
             {"role":"user","content":"can you use the internet?"},
             {"role":"assistant","content":"Yes — I can search and read the web."}]}
```

Using GT's **real** system prompt (from `gt.prompts`) keeps training
on-distribution, so the tuned model behaves the same way the runtime drives it.

## Status

- [x] dataset foundation: `seed_data.py`, `build_dataset.py` (validated rows)
- [ ] MLX LoRA training run on a 3B
- [ ] GGUF export + `ollama create` + GT wiring
- [ ] eval harness (reuse the smoke tasks) + A/B vs base
