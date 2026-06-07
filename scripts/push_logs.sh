#!/usr/bin/env bash
# 로그 파일을 git에 커밋하고 push — 원격 Claude 에이전트가 읽을 수 있도록
set -euo pipefail

REPO="/Users/kimdhyeon/asset_reallocation_by_claude"
PYTHON="/opt/homebrew/Caskroom/miniforge/base/bin/python3"
cd "$REPO"

# state.db → STATUS.md 스냅샷 갱신 (GitHub에서 상태 열람용)
"$PYTHON" "$REPO/scripts/snapshot_state.py" || true

git add logs/ STATUS.md
if git diff --cached --quiet; then
    echo "[push_logs] 변경 없음, skip"
    exit 0
fi

DATE=$(date '+%Y-%m-%d %H:%M')
git commit -m "logs: auto-update $DATE"
git push origin main
echo "[push_logs] 완료: $DATE"
