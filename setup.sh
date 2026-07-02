#!/usr/bin/env bash
# GT-Code one-shot setup for macOS / Linux. Safe to re-run any time.
#   1. venv + Python dependencies
#   2. Ollama (auto-installed if missing)
#   3. the local models GT needs (~8 GB the first time)
#   4. a global "gt" command so you can run GT from any folder
# LM Studio is OPTIONAL — GT falls back to Ollama without it.
set -e
cd "$(dirname "$0")"

echo "=== GT-Code setup ==="
if ! command -v python3 >/dev/null 2>&1; then
  echo "[ERROR] python3 not found. Install Python 3.10+ first."
  exit 1
fi

# --- venv + deps ---
[ -x .venv/bin/python ] || { echo "Creating virtual environment in .venv ..."; python3 -m venv .venv; }
echo "Installing Python dependencies ..."
./.venv/bin/python -m pip install -q --upgrade pip
./.venv/bin/python -m pip install -q -r requirements.txt

# --- Ollama: install automatically if missing ---
if ! command -v ollama >/dev/null 2>&1; then
  echo "Ollama not found — installing it ..."
  if command -v brew >/dev/null 2>&1; then
    brew install ollama
  else
    curl -fsSL https://ollama.com/install.sh | sh
  fi
fi

# --- make sure the Ollama server is running ---
if ! ollama list >/dev/null 2>&1; then
  echo "Starting Ollama ..."
  (ollama serve >/dev/null 2>&1 &)
  sleep 4
fi

# --- pull the models GT needs (skips anything already downloaded) ---
echo
echo "Downloading local models — the slow part, ~8 GB on a fresh machine:"
echo "  qwen3:8b (coder) + llama3.2:3b (router) + nomic-embed-text (memory)"
for m in qwen3:8b llama3.2:3b nomic-embed-text; do
  ollama pull "$m" || echo "[WARN] could not pull $m — run 'ollama pull $m' later."
done

# --- put a global "gt" command on PATH ---
mkdir -p "$HOME/.local/bin"
cat > "$HOME/.local/bin/gt" <<EOF
#!/usr/bin/env bash
# Launch GT-Code from anywhere; it works on the folder you run it in.
exec "$PWD/.venv/bin/python" -m gt "\$@"
EOF
chmod +x "$HOME/.local/bin/gt" start.sh
case ":$PATH:" in
  *":$HOME/.local/bin:"*)
    echo "Installed the 'gt' command." ;;
  *)
    echo "[i] Installed ~/.local/bin/gt — add this to your shell profile to use it:"
    echo '    export PATH="$HOME/.local/bin:$PATH"' ;;
esac

# --- LM Studio is optional: bigger brain when present ---
echo
if curl -fsm 3 http://localhost:1234/v1/models >/dev/null 2>&1; then
  echo "[i] LM Studio detected on :1234 — GT will use it for the 'brain' role."
else
  echo "[i] LM Studio not detected — that's fine: GT runs everything on Ollama."
  echo "    For a bigger 'brain', install LM Studio, load a big model, and"
  echo "    start its server (Developer tab). GT picks it up on next launch."
fi

echo
echo "=== Setup complete! ==="
echo "cd into any project and type:  gt"
