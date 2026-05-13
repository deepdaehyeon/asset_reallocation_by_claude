# SYSTEM ARCHITECTURE

*최종 갱신: 2026-05-09*

---

## 실행 모드

```
python run.py --mode monitor   # 08:50 KST — 시장 분석 + 트리거 계산 → state.json 저장
python run.py --mode krw       # 09:10 KST — 국장(KRW) 리밸런싱 실행
python run.py --mode usd       # 23:00 KST — 미장(USD) 리밸런싱 실행
```

모든 모드에 `--dry-run` 추가 시 계좌 조회·주문 없이 레짐/비중만 출력.

---

## 데이터 흐름

```
[yfinance]
  SPY, ^VIX, TLT, HYG, DX-Y.NYB, DJP (500일 히스토리)
        │
        ▼
[fetcher.py]  fetch_signal_prices() / fetch_fred_data() / fetch_usd_krw()
        │  DataFrame (종목 × 날짜) + FRED extras
        ▼
[features.py]  compute_features() / compute_feature_matrix() / compute_rolling_correlation()
        │  {momentum_1m, momentum_3m, realized_vol, vix, credit_signal,
        │   dxy_mom_1m, commodity_mom_1m, + FRED 8종}
        ▼
[regime.py]
  detect_regime()           ← 규칙 기반 5-레짐 분류
  HmmRegimeClassifier       ← GaussianHMM(5상태) 앙상블
  BalancedRFClassifier      ← 클래스 균형 RF (HMM 0.6 + RF 0.4 블렌딩)
  ensemble_regime()         ← override_threshold(60%) 초과 시 HMM 채택
  compute_rule_confidence() ← 신뢰도 [0.0, 1.0]
  RegimeFilter.update()     ← 히스테리시스 필터 (3회 확인 + 5일 쿨다운)
        │  confirmed_regime + blend_probs {regime: prob}
        ▼
[portfolio.py]
  blend_regime_targets()         ← HMM 확률로 연속 블렌딩 (Continuous Exposure)
  apply_vol_targeting()          ← realized_vol > 10% 시 equity 축소
  apply_class_caps()             ← 자산군별 최대 비중 상한
  derive_account_weights()       ← 계좌별(KRW/USD) 종목 비중 도출
  apply_risk_controls()          ← 드로우다운 단계별 equity 축소
  enforce_buffer_floor()         ← 버퍼(469830) 최소 7% 보장
  apply_synthetic_reallocation() ← USD 지연 매수 → KRW 합성 노출
        │  target_usd {ticker: frac}, target_krw {ticker: frac}
        ▼
[settlement.py]  SettlementTracker
  ├─ purge_settled()         T+2 만기 매도 기록 정리
  ├─ get_deferred()          이전 run의 지연 매수 로드 (만료 항목 자동 폐기)
  └─ record_sell() / add_deferred()  실행 후 기록 (deferred에 5영업일 만료일 포함)
        │
        ▼
[executor.py]  KisRebalancer
  ├─ get_portfolio_state()   KIS API → 현재 비중 + 드로우다운 + orphan 탐지
  │                          (peak_krw는 self._peak_krw로 노출, 저장은 run.py에서)
  ├─ sell_orphans()          유니버스 외 보유 종목 전량 매도
  ├─ _split_buy_orders()     버퍼 여유분 기준 즉시/지연 분류
  ├─ _wait_for_fill()        미체결 대기 루프 (retry_interval초마다 가격 조정, max_retries회 타임아웃)
  └─ rebalance()             월간 회전율 검사 → 매도 우선 주문 실행 + 지연 매수 반환
        │
        ▼
[pykis]  한국투자증권 KIS API — 지정가 주문 체결
        │
        ▼
[state.json]  영속 상태 (아래 키 목록 참고)
```

---

## 모듈별 책임

### fetcher.py

- `fetch_signal_prices(tickers, lookback_days)` — yfinance로 가격 히스토리 수집
- `fetch_usd_krw(fallback)` — KRW=X 실시간 환율 조회
- `fetch_fred_data()` — FRED API로 HY OAS(BAMLH0A0HYM2)·10Y-2Y(T10Y2Y) 등 조회

### features.py

- `compute_features(prices, fred_data)` — 최신 단일 시점 피처 dict 반환
  - `momentum_1m/3m`: SPY 1·3개월 수익률
  - `realized_vol`: SPY 21일 표준편차 × √252 (연환산)
  - `vix`: ^VIX 최신 종가
  - `credit_signal`: HYG - TLT 1개월 수익률 차 (FRED 있으면 HY 스프레드 기반)
  - `dxy_mom_1m`: 달러 인덱스 1M 모멘텀
  - `commodity_mom_1m`: DJP 1M 모멘텀
  - FRED 8종: `cpi_yoy`, `cpi_mom_zscore`, `unrate_chg_3m`, `breakeven_5y`, `m2_yoy`, `fed_bs_yoy`, `hy_spread_zscore`, `curve_10y2y`
- `compute_feature_matrix(prices, fred_history)` — HMM/RF 학습용 일별 피처 DataFrame
- `compute_rolling_correlation(prices, window=60) → float` — SPY·TLT·HYG·GLD·DJP 간 평균 롤링 상관계수. > 0.8 시 경고 기준.

### regime.py

- `detect_regime(features)` — 규칙 기반 5-레짐 분류

```
Crisis      : realized_vol > 30% OR VIX > 40
Stagflation : 성장↓(≥2) + 인플레↑(≥1)
Slowdown    : 성장↓(≥2)
Goldilocks  : 성장↑(≥2) + 인플레↓(≥1)
Reflation   : 성장↑(≥2) + 인플레↑(≥1)
혼재        : 성장 방향성으로 보수적 판단
```

- `HmmRegimeClassifier` — GaussianHMM(5상태, diag covariance) 비지도 학습
  - 학습: 500일 역사 피처 행렬
  - 레이블 매핑: 규칙 기반 다수결로 HMM 상태 → 레짐 매핑
  - 추론: 최근 60일 시퀀스 → 마지막 시점 사후 확률
- `BalancedRFClassifier` — 클래스 균형 Random Forest. HMM 0.6 + RF 0.4 블렌딩. Crisis/Stagflation 소수 레짐 탐지 강화.
- `ensemble_regime(rule, hmm_probs, threshold=0.60)` — HMM이 60% 이상 + 규칙 기반 25% 미만일 때만 HMM 채택
- `compute_rule_confidence(features, regime)` — 기여 신호 수 / 최대 신호 수
- `RegimeFilter` — 히스테리시스: N회 연속 확인(기본 3) + 쿨다운(기본 5일)
- `DEFAULT_REGIME = "Slowdown"` — 신뢰도(40%) 미달 시 보수적 폴백

### portfolio.py

- `blend_regime_targets(regime_probs, config)` — HMM 사후 확률 가중 평균 (Continuous Exposure)
- `apply_vol_targeting(targets, realized_vol, config)` — scale = clip(target_vol/rvol, 0.65, 1.0) → equity 축소분 cash 이동
- `apply_class_caps(targets, class_max)` — 자산군 상한 초과분 cash로 이동
- `derive_account_weights(targets, config, total_usd_krw, total_krw_only)` — USD/KRW 계좌별 종목 비중 계산
**USD 배정 우선순위:**
  ```
  1순위 — commodity + managed_futures (전액 보장)
  2순위 — equity_factor + equity_sector + equity_individual (예산 내 비례)
  3순위 — bond_usd (잔여 예산)
  잔여 → 전항목 비례 확대 (USD 계좌 100% 소진)
  ```
- `apply_risk_controls(weights, drawdown, thresholds, equity_tickers)` — equity만 단계 축소
  ```
  drawdown ≤ -10%  → equity × 0.75
  drawdown ≤ -20%  → equity × 0.40
  drawdown ≤ -30%  → equity = 0.0
  ```
- `enforce_buffer_floor(weights, buffer_tickers, buffer_min)` — 버퍼(469830) ≥ 7%, 부족분은 비버퍼 자산 pro-rata 차감
- `apply_synthetic_reallocation(target, deferred_buys, synthetic_pairs, total_krw)` — USD 지연 매수분 → KRW 동등 자산 임시 증가
- `compute_drift(current, target)` — Σ|current[t] - target[t]|
- `merge_to_total_weights(usd_w, krw_w, total_usd_krw, total_krw_only)` — 계좌별 비중 → 전체 비중 변환

### settlement.py — SettlementTracker

- `_next_business_day(from_date, n)` — n 영업일 후 날짜. 토·일 + 한·미 공휴일 건너뜀 (`holidays` 라이브러리, 미설치 시 주말만 제외)
- `record_sell(ticker, amount_krw, currency)` — 매도 체결 시 T+2 결제 예정일 기록
- `purge_settled()` — 결제일 경과 항목 정리
- `get_deferred()` — 지연 매수 대기열 반환. 5영업일(`DEFERRED_TTL_DAYS`) 초과 항목 자동 폐기.
- `add_deferred(ticker, amount_krw, currency)` — 지연 매수 추가 (5영업일 `expires` 포함)
- `to_dict()` → state.json `pending_sells` / `deferred_buys` 키로 영속화

### executor.py — KisRebalancer

- `load_state()` — state.json 로드. `JSONDecodeError` 포착 + `peak_krw` 타입 검증.
- `_fetch_usd_krw(fallback)` — USD/KRW 환율 조회. state.json에 1시간 캐시(`usd_krw_rate`, `usd_krw_at`).
- `_init_clients(auth_path)` — config.accounts 기반 pykis 클라이언트 생성 (동일 acc_no 단일 클라이언트 재사용)
- `get_portfolio_state()` — 전 계좌 잔고 합산 → `(total_krw, total_usd_krw, total_krw_only, current_weights, drawdown)` 반환.
  - orphan(유니버스 외 보유)은 `_orphan_holdings`에 별도 수집
  - 신규 peak은 `self._peak_krw`로 노출 — 저장은 호출 측(run.py)에서 `state["peak_krw"] = rebalancer._peak_krw`
- `sell_orphans(side)` — 잔고 재조회 후 orphan 전량 매도
- `rebalance(...)` — 월간 회전율 검사(`max_monthly_turnover`) → 매도 우선 정렬 → 버퍼 기반 즉시/지연 분류 → 주문 실행
- `_wait_for_fill(order, reorder, ..., max_retries=10, retry_interval=100)` — 미체결 대기 루프. `retry_interval`초마다 0.1% 가격 조정 재주문. `max_retries`회 초과 시 타임아웃.
- `_build_orders(...)` — `per_ticker_drift_threshold` 적용, `(ticker, currency, amount_diff_krw, acc_name)` 목록 생성
- `_execute_order(ticker, currency, amount_diff_krw)` — KRW 환산 금액 기준 매수/매도
- `_execute_exact_sell(ticker, currency, qty, client)` — 수량 지정 전량 매도 (orphan 정리 전용)

### run.py — 파이프라인 진입점

- `main()` — `FileNotFoundError`·`yaml.YAMLError` catch + 안내 메시지
- `_run_market_analysis(config, state)` — 단계 1-3: 데이터 수집 → 피처 → 상관 경고 → 레짐
- `_compute_targets(blended, realized_vol, config, ...)` — 단계 5b-5d: vol targeting → caps → 계좌별 비중
- `_apply_risk_controls(target_usd, target_krw, drawdown, ...)` — 단계 7: 드로우다운 + 버퍼 + 합성 노출
- `_compute_side_drifts(...)` — KRW/USD 계좌별 drift 계산
- `_compute_trigger(drift, regime_changed, drawdown, last_rebalanced_at, config)` — 우선순위: 드로우다운 비상 → 쿨다운 → 레짐 전환 → drift
- `run_monitor(config, state, messenger, args)` — 분석 + 트리거 계산 → state.json 저장
- `run_execution(config, state, messenger, args)` — 트리거 확인 → 리밸런싱 실행

### server.py — 웹 컨트롤 패널

- FastAPI + WebSocket, `http://<IP>:8080`
- `/api/state`, `/api/regime`, `/api/config` — REST 조회
- `/ws/run` — run.py 스트리밍 실행 (뮤텍스로 중복 실행 방지)
- `/metrics` — Prometheus 스크레이프 엔드포인트

---

## 레짐별 자산군 목표 비중


| 자산군                           | Goldilocks | Reflation | Slowdown | Stagflation | Crisis |
| ----------------------------- | ---------- | --------- | -------- | ----------- | ------ |
| equity_etf                    | 40%        | 22%       | 15%      | 5%          | 0%     |
| equity_factor (VTV·AVUV)      | 5%         | 8%        | 5%       | 3%          | 3%     |
| equity_sector (XLE)           | 0%         | 7%        | 0%       | 5%          | 0%     |
| equity_individual (TSLA·PLTR) | 20%        | 10%       | 10%      | 8%          | 5%     |
| commodity (DBC)               | 5%         | 16%       | 5%       | 18%         | 5%     |
| managed_futures (DBMF)        | 5%         | 5%        | 12%      | 12%         | 12%    |
| bond_usd (IEF·SHY)            | 0%         | 0%        | 12%      | 5%          | 10%    |
| bond_krw (305080)             | 0%         | 0%        | 15%      | 0%          | 10%    |
| gold (411060)                 | 10%        | 15%       | 14%      | 18%         | 15%    |
| cash (469830)                 | 15%        | 17%       | 12%      | 26%         | 40%    |


### KRW 계좌 레짐별 비중 (KRW:USD = 70:30 기준, 근사값)


| 자산                    | Goldilocks | Slowdown | Stagflation | Crisis |
| --------------------- | ---------- | -------- | ----------- | ------ |
| KODEX S&P500 (379800) | 43%        | 15%      | 5%          | 0%     |
| KODEX 나스닥100 (379810) | 24%        | 9%       | 3%          | 0%     |
| TIGER 미국채10년 (305080) | 0%         | 21%      | 0%          | 14%    |
| ACE KRX금현물 (411060)   | 14%        | 20%      | 26%         | 21%    |
| SOL 초단기채권 (469830)    | 21%        | 17%      | 37%         | 57%    |


### USD 계좌 레짐별 비중 (KRW:USD = 70:30 기준, 근사값)


| 자산   | Goldilocks | Reflation | Slowdown | Stagflation | Crisis |
| ---- | ---------- | --------- | -------- | ----------- | ------ |
| VTV  | 10%        | 14%       | 8%       | 5%          | 5%     |
| AVUV | 7%         | 9%        | 5%       | 3%          | 3%     |
| XLE  | 0%         | 12%       | 0%       | 9%          | 0%     |
| TSLA | 7%         | 4%        | 4%       | 3%          | 2%     |
| PLTR | 13%        | 6%        | 6%       | 5%          | 3%     |
| DBC  | 17%        | 28%       | 9%       | 31%         | 9%     |
| DBMF | 17%        | 9%        | 21%      | 21%         | 21%    |
| IEF  | 0%         | 0%        | 21%      | 9%          | 17%    |
| SHY  | 0%         | 0%        | 15%      | 6%          | 12%    |


---

## 계좌 구조

```
KRW_1 (64378890-01 KRW) → 379800, 411060
KRW_2 (64521213-01 KRW) → 379810, 305080, 469830
USD   (64378890-01 USD) → VTV, AVUV, XLE, TSLA, PLTR, IEF, SHY, DBC, DBMF
```

잔고 읽기: 모든 계좌 합산 (동일 acc_no는 1회만 조회)
KRW 주문: 각 KRW 계좌 잔고 비율 비례 분산 → 계좌 간 동일 비중 자동 유지
USD 주문: `universe[ticker].exec_account` 단일 계좌

---

## state.json 키 목록


| 키                        | 타입        | 설명                          |
| ------------------------ | --------- | --------------------------- |
| `peak_krw`               | float     | 직전 고점 (드로우다운 계산 기준)         |
| `confirmed_regime`       | str       | null                        |
| `candidate_regime`       | str       | null                        |
| `candidate_count`        | int       | 후보 레짐 연속 확인 횟수              |
| `last_switch_date`       | str (ISO) | 마지막 레짐 전환 확정일               |
| `trigger_krw`            | bool      | KRW 실행 트리거 (monitor→krw 전달) |
| `trigger_usd`            | bool      | USD 실행 트리거 (monitor→usd 전달) |
| `trigger_reason_krw/usd` | str       | 트리거 사유                      |
| `trigger_set_at`         | str (ISO) | 트리거 설정 시각                   |
| `saved_blended_targets`  | dict      | 실행 run이 재사용할 자산군 블렌딩 비중     |
| `saved_realized_vol`     | float     | 변동성 타겟팅 재사용                 |
| `saved_regime`           | str       | 모니터링 run의 확정 레짐             |
| `saved_confidence`       | float     | 레짐 신뢰도                      |
| `saved_features`         | dict      | 주요 피처 값 (Slack 표시용)         |
| `last_rebalanced_krw_at` | str (ISO) | 마지막 KRW 리밸런싱 시각 (쿨다운)       |
| `last_rebalanced_usd_at` | str (ISO) | 마지막 USD 리밸런싱 시각 (쿨다운)       |
| `last_run_at`            | str (ISO) | 마지막 실행 시각                   |
| `last_drawdown`          | float     | 마지막 드로우다운                   |
| `last_total_krw`         | float     | 마지막 포트폴리오 총액                |
| `last_drift_krw/usd`     | float     | 마지막 계좌별 drift               |
| `pending_sells`          | list      | T+2 미결제 매도 기록               |
| `deferred_buys`          | list      | 지연 매수 대기열 (expires 포함)      |
| `usd_krw_rate`           | float     | 캐시된 USD/KRW 환율              |
| `usd_krw_at`             | str (ISO) | 환율 캐시 저장 시각 (1시간 TTL)       |


---

## 설정 구조 (config.yaml)

```yaml
signal:            # 레짐 신호용 티커 + 조회 기간
accounts:          # KRW_1 / KRW_2 / USD 계좌 정의
universe:          # 종목별 메타 (currency, exec_account, asset_class)
regime_targets:    # 레짐별 자산군 목표 비중 (위 테이블 기준)
asset_routing:     # 자산군 → 종목 내부 비율
  equity_etf:          379800(64%) / 379810(36%)
  equity_factor:        VTV(60%) / AVUV(40%)
  equity_sector:        XLE(100%)
  equity_individual:    TSLA(36%) / PLTR(64%)
  commodity:            DBC(100%)
  managed_futures:      DBMF(100%)
  bond_usd:             IEF(58%) / SHY(42%)
  bond_krw:             305080(100%)
  gold:                 411060(100%)
  cash:                 469830(100%)
class_max_weight:       # 자산군 상한 (equity_individual 20%, commodity 20%, managed_futures 12%, gold 18%)
account_ratio_fallback: {usd: 0.30, krw: 0.70}
vol_targeting:          # enabled, target_vol=10%, floor=0.65
hmm:                    # enabled, lookback_days=500, predict_lookback=60, override_threshold=0.60, rf_enabled=true, rf_weight=0.40
regime_filter:          # confirmation_count=3, cooldown_days=5, confidence_threshold=0.40
rebalancing:            # drift_threshold=5%, per_ticker_drift_threshold=5%, min_rebalance_interval_days=7
                        # max_monthly_turnover=0.30, usd_krw_fallback=1380.0, min_order_krw=10000
risk:                   # drawdown_thresholds: mild=-10%, moderate=-20%, severe=-30%
settlement:             # buffer_tickers=["469830"], buffer_min=7%, synthetic_pairs
```

---

## T+2 결제 지연 대응

### Pre-Funding Buffer

`469830`(SOL 초단기채)을 KRW 계좌의 최소 7%로 항상 유지.

```
Day 0: 매도 실행 + 버퍼(469830)로 매수 즉시 집행
Day 2: 매도 결제 완료 → 다음 리밸런싱 시 버퍼 복구
```

### Synthetic Exposure

버퍼 부족 시: USD 매수를 지연하되 KRW 동등 자산으로 임시 노출 유지.
`deferred_buys` → state.json 저장 → 다음 run에서 `apply_synthetic_reallocation()`.
5영업일 초과 항목은 `get_deferred()` 호출 시 자동 폐기.

```
synthetic_pairs:
  TSLA→379800, PLTR→379810, VTV→379800, AVUV→379800,
  XLE→379800, IEF→305080, SHY→469830, DBC→469830, DBMF→469830
```

---

## 모니터링

```bash
python trading/server.py        # 웹 컨트롤 패널  http://localhost:8080
docker-compose up -d            # Prometheus:9090 + Grafana:3000
```

주요 메트릭: 레짐 인덱스, 신뢰도, 드로우다운, 자산 총액, 미결제 건수, 후보 레짐 카운트

---

## 의존성


| 라이브러리              | 버전      | 용도                            |
| ------------------ | ------- | ----------------------------- |
| yfinance           | ≥0.2.40 | 레짐 신호용 시장 데이터                 |
| pykis (python-kis) | 2.0.3   | KIS 계좌 잔고 조회 + 주문 실행          |
| pyyaml             | 6.0.2   | config.yaml / auth.yaml 파싱    |
| pandas             | 2.2.2   | 피처 계산                         |
| numpy              | ≥1.26   | 수치 연산                         |
| hmmlearn           | ≥0.3.0  | GaussianHMM 레짐 앙상블            |
| scikit-learn       | ≥1.3.0  | StandardScaler, RandomForest  |
| fredapi            | ≥3.5.0  | FRED API 연동 (FRED_API_KEY 필요) |
| holidays           | ≥0.45   | 한·미 공휴일 캘린더                   |
| slack-sdk          | ≥3.19   | Slack 알림                      |
| fastapi + uvicorn  | ≥0.111  | 웹 컨트롤 패널                      |
| prometheus-client  | ≥0.20.0 | 메트릭 노출                        |


