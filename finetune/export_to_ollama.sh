#!/usr/bin/env bash
# Fuse the LoRA into the base, convert to GGUF, register with Ollama so GT can
# load it like any other local model.
set -euo pipefail
cd "$(dirname "$0")/.."

MODEL="${GT_BASE_MODEL:-mlx-community/Qwen2.5-3B-Instruct-4bit}"
ADAPTERS="finetune/adapters"
FUSED="finetune/fused"
GGUF_DIR="finetune/gguf"
NAME="${GT_OLLAMA_NAME:-gt-qwen-tuned}"
LLAMA_CPP="${LLAMA_CPP:-$HOME/llama.cpp}"

# 1) fuse adapter into a standalone model
python -m mlx_lm.fuse --model "$MODEL" --adapter-path "$ADAPTERS" \
  --save-path "$FUSED"

# 2) convert -> GGUF -> quantize (needs a llama.cpp checkout at $LLAMA_CPP)
if [ ! -d "$LLAMA_CPP" ]; then
  echo "llama.cpp not found at $LLAMA_CPP — clone it or set LLAMA_CPP=..." >&2
  echo "  git clone https://github.com/ggerganov/llama.cpp $LLAMA_CPP && make -C $LLAMA_CPP" >&2
  exit 1
fi
mkdir -p "$GGUF_DIR"
python "$LLAMA_CPP/convert_hf_to_gguf.py" "$FUSED" \
  --outfile "$GGUF_DIR/$NAME-f16.gguf" --outtype f16
"$LLAMA_CPP/llama-quantize" "$GGUF_DIR/$NAME-f16.gguf" \
  "$GGUF_DIR/$NAME-q4_k_m.gguf" Q4_K_M

# 3) Modelfile + register with Ollama
cat > "$GGUF_DIR/Modelfile" <<EOF
FROM ./$NAME-q4_k_m.gguf
PARAMETER temperature 0.3
PARAMETER num_ctx 8192
EOF
ollama create "$NAME" -f "$GGUF_DIR/Modelfile"

echo
echo "created ollama model: $NAME"
echo "wire it into GT — in config.yaml set e.g. models.fast.model: \"$NAME\","
echo "then A/B it against the base with tests/smoke_test.py-style tasks."
