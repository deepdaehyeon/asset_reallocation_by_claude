#!/usr/bin/env bash
# 거래 직후 헤드리스 claude로 트레이딩 리뷰를 생성해 Slack으로 전송.
# 로그 tail을 미리 추출해 prompt로 넘긴다 (파일 도구/권한 프롬프트 불필요).
#   사용:  review_and_notify.sh           → 리뷰 생성 후 Slack 전송
#         review_and_notify.sh --dry     → 리뷰만 출력 (Slack 전송 안 함)
set -uo pipefail

REPO="/Users/kimdhyeon/asset_reallocation_by_claude"
PYTHON="/opt/homebrew/Caskroom/miniforge/base/bin/python3"
CLAUDE="/opt/homebrew/bin/claude"
cd "$REPO"

# .env 로드 (launchd는 셸 환경을 상속받지 않음)
export $(grep -v '^\s*#' "$REPO/.env" | sed 's/ *= */=/' | xargs)

CONTEXT=$(
    echo "=== orders.csv (최근 30줄) ==="
    tail -n 30 "$REPO/trading/logs/orders.csv" 2>/dev/null
    echo
    echo "=== logs/krw.log (오늘 아침 KRW 거래, 최근 90줄) ==="
    tail -n 90 "$REPO/logs/krw.log" 2>/dev/null
    echo
    echo "=== logs/usd.log (지난밤 US 거래, 최근 90줄) ==="
    tail -n 90 "$REPO/logs/usd.log" 2>/dev/null
    echo
    echo "=== logs/monitor.log (레짐 모니터링, 최근 130줄) ==="
    tail -n 130 "$REPO/logs/monitor.log" 2>/dev/null
)

read -r -d '' PROMPT <<'EOF'
당신은 이 레짐 기반 자산배분 트레이딩 시스템의 분석가입니다.
시스템의 최우선 목표는 '리스크 회피'이며, 5개 레짐
(Goldilocks/Reflation/Slowdown/Stagflation/Crisis)에 따라 비중을 조정합니다.
최근 HMM 라벨 정렬(stabilize_mapping)을 도입했고, 로그의 '매핑 ... state 변화 N/5'에서
N이 0에 가까울수록 레짐 신호가 안정적입니다(가짜 플립 없음).

아래는 오늘 아침 KRW 거래(10:00)와 지난밤 US 거래(23:30)의 실제 실행 로그입니다.
이걸 바탕으로 한국어 트레이딩 리뷰를 작성하세요.

포함할 내용:
1. 레짐 판단과 신호 안정성 (state 변화 N/5, blend %, 신뢰도)
2. 실제 체결된 주문 (매수/매도, 종목, 회전 방향) — churn인지 정상 리밸런싱인지 판단
3. 에러/이상징후 (주문 실패, API rate limit, '장운영일자 상이' 오류, 지연매수 등) — 있으면 반드시 강조
4. 리스크 상태 (총자산, 드로우다운, 비중 변화가 위험 증가/감소 방향인지)
5. 한 줄 종합

형식 규칙:
- Slack mrkdwn 사용: 볼드는 *단일 별표*. 헤더(#)·마크다운 표·구분선(---)은 쓰지 말 것.
- 간결하게. 핵심만. 과장 금지. 전체 20줄 내외.
- 이상이 없으면 '이상 없음'이라고 명확히 쓰고, 문제를 지어내지 말 것.

=== 로그 시작 ===
EOF

REVIEW=$(printf '%s\n%s\n' "$PROMPT" "$CONTEXT" | "$CLAUDE" -p --model sonnet 2>/dev/null)

if [[ -z "$REVIEW" ]]; then
    echo "[review] 빈 리뷰 — 전송 안 함" >&2
    exit 1
fi

if [[ "${1:-}" == "--dry" ]]; then
    echo "$REVIEW"
    exit 0
fi

echo "$REVIEW" | "$PYTHON" "$REPO/scripts/slack_review.py"
