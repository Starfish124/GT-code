#!/usr/bin/env bash
# ============================================================
#  GT-Code doctor - diagnoses a broken install, step by step.
#  Run from the GT-code folder; the FIRST [FAIL] is where your
#  install breaks. Send the full output when reporting a problem.
# ============================================================
cd "$(dirname "$0")"
FAILED=0
ok()   { printf ' [ OK ] %s\n' "$1"; }
fail() { printf ' [FAIL] %s\n         FIX: %s\n' "$1" "$2"; FAILED=1; }
warn() { printf ' [WARN] %s\n         %s\n' "$1" "$2"; }

echo
echo "=== GT-Code doctor ==="
echo "Folder : $PWD"
echo "System : $(uname -s) $(uname -m)"
echo

# 1. Python
if command -v python3 >/dev/null 2>&1; then
  ok "1. Python found: $(python3 --version 2>&1)"
else
  fail "1. python3 is not installed / not on PATH" \
       "install Python 3.10+ (brew install python / apt install python3), then re-run ./setup.sh"
fi

# 2. venv
if [ -x ".venv/bin/python" ]; then
  ok "2. Virtual environment exists: .venv"
else
  fail "2. No .venv here - setup never completed" "run ./setup.sh and watch for errors"
fi

# 3. GT installed in the venv
if [ -x ".venv/bin/gt" ]; then
  ok "3. GT is installed in the venv: .venv/bin/gt"
else
  fail "3. .venv/bin/gt missing - the pip install step failed" \
       "re-run ./setup.sh; if it fails, run: ./.venv/bin/python -m pip install -e . and send the output"
fi

# 4. GT runs
if ./.venv/bin/gt --version >/dev/null 2>&1; then
  ok "4. GT runs: $(./.venv/bin/gt --version)"
else
  fail "4. gt exists but does not run" "run ./.venv/bin/gt --version and send the output"
fi

# 5. global shim
if [ -x "$HOME/.local/bin/gt" ]; then
  ok "5. Global command exists: ~/.local/bin/gt"
else
  fail "5. ~/.local/bin/gt is missing" "re-run ./setup.sh - it creates the global command"
fi

# 6. ~/.local/bin on PATH
case ":$PATH:" in
  *":$HOME/.local/bin:"*) ok "6. ~/.local/bin is on your PATH" ;;
  *) fail "6. ~/.local/bin is NOT on your PATH" \
          "add to your shell profile:  export PATH=\"\$HOME/.local/bin:\$PATH\"  then open a new terminal" ;;
esac

# 7. gt resolves
if command -v gt >/dev/null 2>&1; then
  ok "7. 'gt' resolves to: $(command -v gt)"
else
  warn "7. 'gt' does not resolve in THIS terminal" \
       "if 5 and 6 are OK, open a NEW terminal (PATH changes need one)"
fi

# 8. Ollama installed
if command -v ollama >/dev/null 2>&1; then
  ok "8. Ollama is installed"
else
  fail "8. Ollama is not installed" \
       "brew install ollama  (or: curl -fsSL https://ollama.com/install.sh | sh)"
fi

# 9. Ollama server
if curl -fsm 4 http://localhost:11434/v1/models >/dev/null 2>&1; then
  ok "9. Ollama server is responding"
else
  fail "9. Ollama server not responding on localhost:11434" \
       "start it:  ollama serve &   then re-run ./doctor.sh"
fi

# 10. models
if curl -fsm 4 http://localhost:11434/v1/models 2>/dev/null | grep -q '"id"'; then
  ok "10. Ollama has models available"
else
  warn "10. Ollama serves no models yet" \
       "type gt and let the wizard download them, or: ollama pull qwen2.5:1.5b"
fi

echo
if [ "$FAILED" = "1" ]; then
  echo "=== RESULT: problems found - fix the FIRST [FAIL] above, then re-run ./doctor.sh ==="
  echo "    Full guide: TROUBLESHOOTING.md in this folder"
else
  echo "=== RESULT: everything checks out. Open a NEW terminal and type: gt ==="
fi
echo
