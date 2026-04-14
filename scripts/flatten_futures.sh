#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# shellcheck source=/dev/null
source .venv/bin/activate
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
if [[ "${1:-}" == "--live" ]]; then
  shift
  exec python scripts/flatten_binance_futures.py "$@"
fi
exec python scripts/flatten_binance_futures.py --demo "$@"
