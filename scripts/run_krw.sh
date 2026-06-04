#!/usr/bin/env bash
set -euo pipefail

REPO="/Users/kimdhyeon/asset_reallocation_by_claude"
PYTHON="/opt/homebrew/Caskroom/miniforge/base/bin/python3"

export $(grep -v '^\s*#' "$REPO/.env" | sed 's/ *= */=/' | xargs)

"$PYTHON" "$REPO/trading/run.py" --mode krw || RUN_RC=$?

# 거래 직후 헤드리스 claude 리뷰 → Slack (거래 실패해도 리뷰는 실행해 보고)
"$REPO/scripts/review_and_notify.sh" || true

exit ${RUN_RC:-0}
