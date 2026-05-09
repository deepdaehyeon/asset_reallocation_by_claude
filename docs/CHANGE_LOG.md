# CHANGE LOG
_최종 갱신: 2026-05-09_

## 2026-05-09

### 리스크·안정성
- **Turnover 상한**: `rebalance()` 앞 월간 회전율 30% 초과 시 경고·차단 (`max_monthly_turnover` config 키)
- **자산 상관 모니터링**: `features.py`에 `compute_rolling_correlation()` 추가. 평균 상관 > 0.8 시 경고 출력 (비중 축소 없음)
- **공휴일 처리**: `_next_business_day()`에 `holidays` 라이브러리 적용 — 한·미 공휴일 스킵

### 실행 레이어
- **지연 매수 TTL**: `deferred_buys` 항목에 5영업일 만료일(`expires`) 추가. `get_deferred()`에서 만료 항목 자동 정리
- **환율 캐싱**: `_fetch_usd_krw()`가 `state.json`에 1시간 캐시 (`usd_krw_rate`, `usd_krw_at` 키)
- **주문 재시도 파라미터**: `_wait_for_fill(max_retries=10, retry_interval=100)` 명시적 분리

### 설계
- **peak 사이드 이펙트 분리**: `get_portfolio_state()` 내 `save_state()` 제거 → `self._peak_krw`로 노출, `run.py`에서 저장
- **state.json 스키마 검증**: `load_state()`에 `JSONDecodeError` 포착 + `peak_krw` 타입 검증 추가
- **config 오류 처리**: `main()`에 `FileNotFoundError`·`YAMLError` catch + 안내 메시지

---

## 2026-05-08

- **BalancedRF 앙상블**: `BalancedRFClassifier` 추가. HMM 0.6 + RF 0.4 블렌딩. Crisis/Stagflation recall 강화
- **매크로 피처 파이프라인**: DXY(달러 인덱스)·Commodity(DJP)·FRED 8종 피처 추가 (`features.py`)
- **KRW 계좌 통합 관리**: 계좌별 잔고 비율 비례 주문 분산 — 동일 비중 자동 유지 (`executor.py`)
- **Neutral 레짐 버그 수정**: 3곳 수정 (`regime.py`)
- **CLAUDE.md 프로젝트 협업 규칙 추가**: 실험 결과 저장 + 코드 수정 후 커밋·푸시 규칙

---

## 2026-05-07

### 유니버스 변경
- `equity_individual`: USMV 제거, TSLA 36% + PLTR 64% 유지
- `equity_factor`: VTV(60%) + AVUV(40%) 추가
- `equity_sector`: XLE 신규 추가 (Reflation·Stagflation 수혜)
- `managed_futures`: base 비중 12% → 레짐별 5~12%로 세분화

### 레짐 체계 전환
- Risk-On / Risk-Off / Neutral / High-Vol 4레짐 → **Goldilocks · Reflation · Slowdown · Stagflation · Crisis 5레짐**
- 레짐 분류 품질 메트릭 추가: MCC / Macro-F1 / Balanced Accuracy / 위험 레짐 오판 비용 (`backtest/metrics.py`)

---

## 2026-05 이전 (초기 구현)

- Slack 알림 (시작·완료·오류, 레짐·신뢰도·비중·주문내역)
- 환율 자동 조회 (yfinance KRW=X, 실패 시 config 폴백)
- 주문 결과 로깅 (`logs/orders.csv`)
- FRED API 연동 (HY OAS·10Y-2Y)
- GaussianHMM(5상태) 앙상블 + 레짐 연속 블렌딩 (Continuous Exposure)
- 레짐 신뢰도 + 히스테리시스 필터 (3회 연속 확인 + 5일 쿨다운)
- 변동성 타겟팅 + 자산군별 최대 비중 상한
- 드로우다운 제어 (equity 단계적 축소, 채권·금·현금 유지)
- T+2 결제 지연 대응: Pre-Funding Buffer + Synthetic Exposure
- 웹 컨트롤 패널 (FastAPI + WebSocket) + Prometheus + Grafana
- monitor / krw / usd 3모드 분리 실행
- 유니버스 외 보유 종목 자동 매도 (`sell_orphans`)
