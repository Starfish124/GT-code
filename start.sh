#!/usr/bin/env bash
# Launch GT-Code (macOS / Linux).
cd "$(dirname "$0")"
if [ ! -x ".venv/bin/python" ]; then
  echo "[!] No virtual environment found. Running setup first..."
  ./setup.sh
fi
exec ./.venv/bin/python -m gt "$@"
