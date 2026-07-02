#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

SYMBOL="${1:-}"
if [ -z "$SYMBOL" ]; then
  if [ -f "symbol" ]; then
    SYMBOL="$(head -n 1 symbol | tr -d ' \t\r\n')"
  fi
fi

if [ -z "$SYMBOL" ]; then
  echo "Usage: DRY_RUN=1 PROFILE=strict STRATEGY=momentum ./scripts/start.sh BTC/USDC"
  echo "Or put SYMBOL in ./symbol then run ./scripts/start.sh"
  exit 1
fi

# shellcheck disable=SC1091
source .venv/bin/activate

PROFILE="${PROFILE:-strict}"
STRATEGY="${STRATEGY:-momentum}"
DRY_RUN="${DRY_RUN:-1}"
QUOTE_ASSET="${QUOTE_ASSET:-USDC}"

export PROFILE
export STRATEGY
export DRY_RUN
export QUOTE_ASSET

exec python3 main.py "$SYMBOL"
