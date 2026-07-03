#!/usr/bin/env bash
# Launch GT-Code from its own venv, WITHOUT changing your working folder —
# GT operates on the directory you run this from.
GT_HOME="$(cd "$(dirname "$0")" && pwd)"

if [ ! -x "$GT_HOME/.venv/bin/gt" ]; then
  echo "[!] GT-Code is not set up yet. Running setup first..."
  (cd "$GT_HOME" && ./setup.sh)
fi

exec "$GT_HOME/.venv/bin/gt" "$@"
