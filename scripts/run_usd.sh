#!/usr/bin/env bash
set -euo pipefail

REPO="/Users/kimdhyeon/asset_reallocation_by_claude"
PYTHON="/opt/homebrew/Caskroom/miniforge/base/bin/python3"

# launchd는 shell 환경변수를 상속받지 않으므로 .env를 직접 로드
export $(grep -v '^\s*#' "$REPO/.env" | sed 's/ *= */=/' | xargs)

# 2026-06-19: 모니터링을 미장 마감 1시간 전(비서머타임 기준 05:00 KST)에 한 번만 돌고
# 바로 이어서 usd 실행 — 국장/미장이 각자 다른 시각의 모니터링 결과를 써서 drift가
# 어긋나던 문제(한쪽 거래가 다른 쪽 drift를 깎아 트리거를 놓침) 방지.
"$PYTHON" "$REPO/trading/run.py" --mode monitor
"$PYTHON" "$REPO/trading/run.py" --mode usd
"$REPO/scripts/push_logs.sh"
