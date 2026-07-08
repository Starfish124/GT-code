#!/usr/bin/env bash
# LoRA fine-tune a small Apache-2.0 Qwen on GT's dataset — Apple Silicon (MLX).
# Prototype step: proves dataset -> weights on the 16GB M4 with a 3B base.
set -euo pipefail
cd "$(dirname "$0")/.."

MODEL="${GT_BASE_MODEL:-mlx-community/Qwen2.5-3B-Instruct-4bit}"   # Apache-2.0
DATA="finetune/data"
ADAPTERS="finetune/adapters"

if [ ! -f "$DATA/train.jsonl" ]; then
  echo "no $DATA/train.jsonl — run build_dataset.py first." >&2
  exit 1
fi

python -m mlx_lm.lora \
  --model "$MODEL" \
  --train \
  --data "$DATA" \
  --adapter-path "$ADAPTERS" \
  --iters "${GT_ITERS:-400}" \
  --batch-size "${GT_BATCH:-2}" \
  --num-layers "${GT_LAYERS:-8}" \
  --learning-rate 1e-4 \
  --max-seq-length 2048 \
  --steps-per-report 20 \
  --steps-per-eval 100 \
  --save-every 100

echo "LoRA adapters saved to $ADAPTERS/"
echo "sanity-check a prompt:  python -m mlx_lm.generate --model $MODEL \\"
echo "  --adapter-path $ADAPTERS --prompt 'can you use the internet?'"
