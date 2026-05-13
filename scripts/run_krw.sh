#!/usr/bin/env bash
set -euo pipefail

REPO="/Users/kimdhyeon/Desktop/asset_reallocation_by_claude"
PYTHON="/opt/homebrew/Caskroom/miniforge/base/bin/python3"

export $(grep -v '^\s*#' "$REPO/.env" | sed 's/ *= */=/' | xargs)

"$PYTHON" "$REPO/trading/run.py" --mode krw
