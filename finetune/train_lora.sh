#!/usr/bin/env bash
# LoRA fine-tune a small Apache-2.0 Qwen on GT's dataset — Apple Silicon (MLX).
# Prototype step: proves dataset -> weights on a 16GB M4 with a 3B base.
#
# Needs the PINNED versions (see requirements.txt): mlx-lm 0.26.x + transformers
# 4.x. Newer mlx-lm silently trains to nan loss with the transformers it pulls.
set -euo pipefail
cd "$(dirname "$0")/.."

MODEL="${GT_BASE_MODEL:-mlx-community/Qwen2.5-3B-Instruct-4bit}"   # Apache-2.0
DATA="finetune/data"
ADAPTERS="finetune/adapters"

if [ ! -f "$DATA/train.jsonl" ]; then
  echo "no $DATA/train.jsonl — run build_dataset.py first." >&2
  exit 1
fi

# max-seq-length must exceed the longest row: each row carries GT's full runtime
# system prompt (~2.4k tokens), so rows run ~2.5k-3.5k tokens. Truncating below
# that cuts the assistant label and destabilises training. LR is deliberately
# low (2e-5) — a 4-bit base diverges at 1e-4.
python -m mlx_lm.lora \
  --model "$MODEL" \
  --train \
  --data "$DATA" \
  --adapter-path "$ADAPTERS" \
  --fine-tune-type lora \
  --num-layers "${GT_LAYERS:-8}" \
  --batch-size "${GT_BATCH:-1}" \
  --iters "${GT_ITERS:-300}" \
  --learning-rate "${GT_LR:-2e-5}" \
  --max-seq-length "${GT_MAXLEN:-3584}" \
  --val-batches 2 \
  --steps-per-report 20 \
  --steps-per-eval 50 \
  --save-every 100

echo "LoRA adapters saved to $ADAPTERS/"
echo "sanity-check a prompt:  python -m mlx_lm.generate --model $MODEL \\"
echo "  --adapter-path $ADAPTERS --prompt 'can you use the internet?'"
