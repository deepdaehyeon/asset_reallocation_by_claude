# PROJECT STATE
_최종 갱신: 2026-05-08_

## 유니버스 변경 이력

| 날짜 | 변경 |
|---|---|
| 2026-05 이전 | Risk-On/Off/Neutral/High-Vol 4레짐 체계 |
| 2026-05-07 | Goldilocks/Reflation/Slowdown/Stagflation/Crisis 5레짐 전환 |
| 2026-05-07 | equity_individual: USMV 제거, TSLA 36%+PLTR 64% 유지 |
| 2026-05-07 | equity_factor: VTV(60%) + AVUV(40%) 추가 |
| 2026-05-07 | equity_sector: XLE 신규 추가 (Reflation·Stagflation 수혜) |
| 2026-05-07 | managed_futures base 비중 12% → 레짐별 5~12%로 세분화 |

---

## 구현 완료 범위

- [x] yfinance 기반 레짐 신호 수집
- [x] 모멘텀·변동성·VIX·크레딧 피처 계산
- [x] 규칙 기반 5레짐 감지 (Goldilocks·Reflation·Slowdown·Stagflation·Crisis)
- [x] 레짐별 자산군 목표 비중 + 자산 라우팅 (config.yaml)
- [x] GaussianHMM(5상태) 앙상블 — 500일 학습, 최근 60일 시퀀스 추론
- [x] 레짐 연속 블렌딩 (Continuous Exposure) — HMM 사후 확률 가중 평균
- [x] 레짐 신뢰도 출력 + 40% 미달 시 Slowdown 폴백
- [x] 레짐 전환 히스테리시스 필터 (3회 연속 + 5일 쿨다운)
- [x] FRED API 연동 (HY 스프레드, 장단기 금리차)
- [x] 변동성 타겟팅 (target_vol 10%, floor 0.65)
- [x] 자산군별 최대 비중 상한 (class_max_weight)
- [x] 드로우다운 제어 (equity 단계적 축소, 채권·금·현금 유지)
- [x] KIS 멀티 계좌 잔고 조회 + 유니버스/orphan 분리
- [x] 유니버스 외 보유 종목 자동 매도 (sell_orphans)
- [x] 매도 우선 주문 실행
- [x] 직전 고점 기반 드로우다운 추적 (state.json)
- [x] T+2 결제 지연 대응: Pre-Funding Buffer (469830 ≥ 7%)
- [x] T+2 결제 지연 대응: Synthetic Exposure (USD 지연 → KRW 합성)
- [x] SettlementTracker — pending_sells / deferred_buys 영속화
- [x] 환율 자동 조회 (yfinance KRW=X, 실패 시 config 폴백)
- [x] 주문 결과 로깅 (logs/orders.csv)
- [x] Slack 알림 (시작·완료·오류, 레짐·신뢰도·비중·주문내역 포함)
- [x] 웹 컨트롤 패널 (FastAPI + WebSocket, http://\<IP>:8080)
- [x] Prometheus + Grafana 모니터링 대시보드 (Docker Compose)
- [x] monitor/krw/usd 3모드 분리 실행 — 계좌별 드리프트·트리거 독립 관리
- [x] 미결제 폴링 루프 추출 (`_wait_for_fill`) — 코드 중복 제거
- [x] 모든 내부 지연 import 제거 — 모듈 상단 통합

