#!/usr/bin/env bash
# GT-Code one-shot setup for macOS / Linux. Safe to re-run any time.
#   1. venv + Python dependencies
#   2. Ollama (auto-installed if missing)
#   3. the baseline local models (GT's first launch offers the bigger ones)
#   4. a global "gt" command so you can run GT from any folder
# LM Studio is OPTIONAL — GT falls back to Ollama without it.
set -e
cd "$(dirname "$0")"

echo "=== GT-Code setup ==="
if ! command -v python3 >/dev/null 2>&1; then
  echo "[ERROR] python3 not found. Install Python 3.10+ first."
  exit 1
fi

# --- venv + editable install ---
[ -x .venv/bin/python ] || { echo "Creating virtual environment in .venv ..."; python3 -m venv .venv; }
echo "Installing GT-Code into its own environment ..."
./.venv/bin/python -m pip install -q --upgrade pip
# Editable install: puts the 'gt' package AND a real 'gt' command inside the
# venv, so GT launches from ANY folder (no more "No module named gt").

# Document tools are optional - Excel/PowerPoint/Word creation.
read -r -p "Install document tools (Excel/PowerPoint/Word creation)? [Y/n] " DOCTOOLS || DOCTOOLS=Y
if [ "$DOCTOOLS" = "n" ] || [ "$DOCTOOLS" = "N" ]; then
  ./.venv/bin/python -m pip install -q -e .
else
  ./.venv/bin/python -m pip install -q -e ".[docs]"
fi

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

# --- pull the baseline models (skips anything already downloaded) ---
# GT's first launch evaluates this machine's hardware and offers the bigger
# models (up to 14B) only if the machine can actually run them.
echo
echo "Downloading the baseline models (~2.3 GB on a fresh machine):"
echo "  llama3.2:3b (minimum) + nomic-embed-text (memory)"
for m in llama3.2:3b nomic-embed-text; do
  ollama pull "$m" || echo "[WARN] could not pull $m — run 'ollama pull $m' later."
done

# --- put a global "gt" command on PATH ---
# The shim calls the venv's own gt entry point: GT always runs from its OWN
# venv here in the GT-code folder, and operates on whatever folder you're in.
mkdir -p "$HOME/.local/bin"
cat > "$HOME/.local/bin/gt" <<EOF
#!/usr/bin/env bash
exec "$PWD/.venv/bin/gt" "\$@"
EOF
chmod +x "$HOME/.local/bin/gt" start.sh
case ":$PATH:" in
  *":$HOME/.local/bin:"*)
    echo "Installed the 'gt' command." ;;
  *)
    echo "[i] Installed ~/.local/bin/gt — add this to your shell profile to use it:"
    echo '    export PATH="$HOME/.local/bin:$PATH"' ;;
esac

# --- sanity check: the command must work from a DIFFERENT directory ---
if (cd /tmp && "$HOME/.local/bin/gt" --version >/dev/null 2>&1); then
  echo "Self-test OK: 'gt' works from any folder."
else
  echo "[WARN] 'gt' self-test failed — run it as: $PWD/.venv/bin/gt"
fi

# --- LM Studio is optional: bigger brain when present ---
echo
if curl -fsm 3 http://localhost:1234/v1/models >/dev/null 2>&1; then
  echo "[i] LM Studio detected on :1234 — GT will use it for the 'brain' role."
else
  echo "[i] LM Studio not detected — that's fine: GT runs everything on Ollama."
  echo "    GT's first launch evaluates this machine and downloads the best"
  echo "    models for it (3B minimum, 14B maximum — bigger is too slow)."
fi

echo
echo "=== Setup complete! ==="
echo "cd into any project and type:  gt"
