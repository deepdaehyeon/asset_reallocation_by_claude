#!/usr/bin/env bash
set -euo pipefail

REPO="/Users/kimdhyeon/asset_reallocation_by_claude"
PYTHON="/opt/homebrew/Caskroom/miniforge/base/bin/python3"

# launchd는 shell 환경변수를 상속받지 않으므로 .env를 직접 로드
export $(grep -v '^\s*#' "$REPO/.env" | sed 's/ *= */=/' | xargs)

"$PYTHON" "$REPO/trading/run.py" --mode usd
"$REPO/scripts/push_logs.sh"
