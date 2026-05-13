#!/usr/bin/env bash
set -euo pipefail

REPO="/Users/kimdhyeon/Desktop/asset_reallocation_by_claude"
PYTHON="/opt/homebrew/Caskroom/miniforge/base/bin/python3"

"$PYTHON" "$REPO/trading/run.py" --mode usd
"$REPO/scripts/push_logs.sh"
