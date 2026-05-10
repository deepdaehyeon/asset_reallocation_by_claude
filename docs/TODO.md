# TODO

*최종 갱신: 2026-05-10*

> **방향 원칙** — 뉴스 NLP·LLM·Transformer·Deep Learning 추가는 현 단계에서 독이 될 가능성이 크다. 해당 방향은 보류.

## 현재 구현 보완 — 완료 (2026-05-09)

### 리스크 엔진

- **Turnover 상한** — `rebalance()` 앞 총 주문금액/포트폴리오 비율 계산, `max_monthly_turnover`(기본 30%) 초과 시 경고·차단.
- **자산 간 상관 모니터링** — `compute_rolling_correlation()` 추가. 평균 롤링 상관계수 > 0.8 시 경고 출력 (비중 축소 액션 없음).
- **공휴일 처리** — `_next_business_day()`에 `holidays` 라이브러리(한·미 공휴일) 적용. 미설치 시 graceful fallback.

### 실행 레이어

- **지연 매수 TTL** — `add_deferred()` 호출 시 5영업일 만료일(`expires`) 저장. `get_deferred()`에서 만료 항목 자동 정리.
- **환율 캐싱** — `_fetch_usd_krw()`가 `state.json`에 환율 캐시(유효기간 1시간). monitor/krw/usd 3회 실행 시 조회 1회로 감소.
- **주문 재시도 상한** — `_wait_for_fill(max_retries=10, retry_interval=100)` 파라미터 분리.

### 상태 관리

- `**get_portfolio_state()` 사이드 이펙트 분리** — 메서드 내 `save_state()` 제거. 신규 peak을 `self._peak_krw`로 노출, `run.py`에서 저장.
- **state.json 스키마 검증** — `load_state()`에 `JSONDecodeError` 포착 및 `peak_krw` 타입 검증 추가.

### 설정

- `**config.yaml` 로드 실패 처리** — `main()`에 `FileNotFoundError`·`YAMLError` catch + 안내 메시지.

---

## 리스크 & 포트폴리오 구성 개선

### 변동성 타게팅

- **Portfolio-Level EWMA Volatility** — `compute_portfolio_ewma_vol()` 기반 EWMA(λ=0.94) 포트폴리오 변동성 적용 (실거래 `run.py`, 백테스트 `engine.py`)
- **Dynamic Risk Budget** — 레짐별 목표 변동성 차등 적용 (`config.yaml: vol_targeting.regime_target_vol`)
  - Goldilocks: 12~~14%, Reflation: 10~~12%, Slowdown: 8~~10%, Crisis: 5~~7%

### 비중 한도

- **Equity Floor 수정** — severe drawdown에서도 equity 최소 비중 유지 (`config.yaml: risk.drawdown_thresholds.equity_floor_pct`)
- **Dynamic Caps** — VIX 기반 동적 상한(`apply_dynamic_class_caps`): VIX>25/30 구간에서 `commodity`/`equity_individual` 상한 축소
- **DBMF Volatility-Relative Cap** — DBMF trailing vol > threshold 시 비중 상한 자동 축소.

---

## 레짐 판단 모델 고도화

- **레짐 분류 품질 메트릭** — MCC / Macro-F1 / Balanced Accuracy / 위험 레짐 오판 비용 (`backtest/metrics.py`)
- **BalancedRF 앙상블** — `BalancedRFClassifier`. HMM 0.6 + RF 0.4 블렌딩으로 Crisis/Stagflation recall 강화.
- **Markov Switching Model** (Stage 2) — `statsmodels.tsa.MarkovAutoregression` 도입.
  - 근거: 금융 레짐 모델의 학문적 표준 (Hamilton 1989), 전환 확률 행렬 해석 가능.
  - 제약: 다변량 입력 제한 → PCA 2~3 성분 축약 후 학습.
  - 위치: `trading/regime.py` `MarkovSwitchingClassifier` 클래스, `engine.py`·`run.py` 연동.
- **Regime Transition Matrix 분석** — `model.transmat_` 활용. 레짐 간 전환 확률, 평균 체류 기간, Transition Entropy 계산. Slowdown→Crisis 위험 상승 시 선제적 비중 조정 로직 연동.
- **RF Anomaly Detector로 역할 변경** — HMM 0.6 + RF 0.4 블렌딩 대신 RF를 "현재 상태가 과거 어떤 레짐과도 다름" 탐지용 이상치 감지기로 전환.

### 피처 계층 분리

- **Structural / Tactical Feature 분리** — 현재 slow-moving macro 위주에서 fast risk signal 추가.
  - Tactical (fast): VIX term structure, VVIX, SPY breadth collapse, Credit ETF intraday stress
  - 레짐 전환 탐지 속도 개선 목적.

---

## Phase 3 — 운영 자동화

- **cron 설정** — crontab 예시 (서버 KST):
  ```bash
  50 8  * * 1-5  cd /path && python trading/run.py --mode monitor >> logs/monitor.log 2>&1
  10 9  * * 1-5  cd /path && python trading/run.py --mode krw     >> logs/krw.log 2>&1
  50  23 * * 1-5  cd /path && python trading/run.py --mode usd     >> logs/usd.log 2>&1
  ```
- **MLflow 모델 추적** — 레짐 판정 히스토리, HMM 학습 파라미터, 신호 IC/IR 기록.
- **Walk-Forward 백테스트** — 2년 학습 / 6개월 검증 슬라이딩 윈도우. `backtest/walk_forward.py` 신설.

### 실행 레이어

- **Order State Machine** — 주문 상태를 `PENDING → PARTIAL → FILLED / CANCELLED / EXPIRED` 로 명시적 관리. partial fill 누적 추적.
- **Slippage Tracking** — 예상 가격 vs 체결 가격 차이를 로그로 저장. 장기 운영 분석 기반.

### 데이터 품질

- **Data Validation Layer** — `validate_prices(df)`: missing%, stale timestamp, zero variance, abnormal gap 체크. 검증 실패 시 `fallback_to_cached_signal()`.

### 모니터링 메트릭 강화

- **추가 메트릭** — realized turnover, fill latency, slippage, regime transition frequency, prediction entropy, feature drift 기록.

---

## Phase 4 — 상태 영속성 강화

- **SQLite 마이그레이션** — `state.json` → SQLite(`state.db`) 전환. atomic transaction으로 corruption 방지.
  - 테이블: `state_current`, `state_history`, `executions`, `fills`, `regime_history`, `metrics`
  - `sqlite3` 직접 사용 (SQLAlchemy 불필요).
  - 최소 단계: write-temp + atomic rename 패턴 우선 적용 가능.

---

## Phase 5 — 백테스트 고도화

- **거래비용 모델링** — KIS 위탁 수수료(온라인 0.014%) + 스프레드 모델 추가.
- **AVUV·XLE 데이터 부재 구간** — AVUV 2019-09, XLE 1998-12 이전 비중 재배분 로직 추가.

---

## Phase 6 — 아키텍처 리팩터링 (장기)

- **Bounded Context 분리** — `run.py` orchestration 비대화 방지. trigger/persistence/risk/scheduling 분리.
  ```
  engine/
    pipeline/   signals/   regimes/
    allocation/ execution/ state/   monitoring/
  ```
  `run.py`는 `pipeline.execute(mode)` 수준으로 축소 목표.

