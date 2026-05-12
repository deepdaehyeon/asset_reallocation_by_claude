#!/usr/bin/env bash
# 로그 파일을 git에 커밋하고 push — 원격 Claude 에이전트가 읽을 수 있도록
set -euo pipefail

REPO="/Users/kimdhyeon/Desktop/asset_reallocation_by_claude"
cd "$REPO"

git add logs/
if git diff --cached --quiet; then
    echo "[push_logs] 변경 없음, skip"
    exit 0
fi

DATE=$(date '+%Y-%m-%d %H:%M')
git commit -m "logs: auto-update $DATE"
git push origin main
echo "[push_logs] 완료: $DATE"
