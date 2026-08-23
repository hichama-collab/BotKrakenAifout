#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON_BIN="${PYTHON_BIN:-python3}"

if [ ! -d ".venv" ]; then
  "$PYTHON_BIN" -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if [ -f dashboard/requirements.txt ]; then
  python -m pip install -r dashboard/requirements.txt
fi
if [ -f requirements-dev.txt ]; then
  python -m pip install -r requirements-dev.txt
fi

echo "OK: venv ready (.venv), bot dependencies installed, dashboard dependencies installed, dev dependencies installed"
