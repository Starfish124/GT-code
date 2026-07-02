#!/usr/bin/env bash
# GT-Code one-time setup for macOS / Linux (handy for testing on the build machine).
set -e
cd "$(dirname "$0")"

echo "=== GT-Code setup ==="
if ! command -v python3 >/dev/null 2>&1; then
  echo "[ERROR] python3 not found. Install Python 3.10+ first."
  exit 1
fi

echo "Creating virtual environment in .venv ..."
python3 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -r requirements.txt

echo
echo "=== Setup complete! ==="
echo "Run ./start.sh to launch GT."
