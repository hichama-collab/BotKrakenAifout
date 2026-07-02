#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${1:-"$ROOT/../kraken-aifout-bot-review.zip"}"

rm -f "$OUT"
cd "$ROOT"

zip -r "$OUT" . \
  -x ".git" \
  -x ".git/*" \
  -x ".venv" \
  -x ".venv/*" \
  -x "__pycache__" \
  -x "__pycache__/*" \
  -x "*/__pycache__" \
  -x "*/__pycache__/*" \
  -x ".pytest_cache" \
  -x ".pytest_cache/*" \
  -x "data/logs" \
  -x "data/logs/*" \
  -x "data/runtime" \
  -x "data/runtime/*" \
  -x "data/*.sqlite3" \
  -x "data/**/*.sqlite3" \
  -x "*.pyc" \
  -x ".env" \
  -x ".service.env" \
  -x "ip.txt" \
  -x ".btc_range.env" \
  -x ".btc_range_dash.env" \
  -x "dashboard/botdash.env" \
  -x "config/.service.env" \
  -x "logs/*" \
  -x "venv/*" \
  -x "*.pyo"

echo "Created $OUT"
