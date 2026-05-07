# ARCHITECTURE

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
  SPY, ^VIX, TLT, HYG (500일 히스토리, HMM 학습 포함)
        │
        ▼
[fetcher.py]  fetch_signal_prices() / fetch_fred_data()
        │  DataFrame (종목 × 날짜) + FRED extras (hy_spread, curve_10y2y)
        ▼
[features.py]  compute_features() / compute_feature_matrix()
        │  {momentum_1m, momentum_3m, realized_vol, vix, credit_signal,
        │   hy_spread*, curve_10y2y*}   (* FRED_API_KEY 설정 시)
        ▼
[regime.py]
  detect_regime()           ← 규칙 기반 5-레짐 분류
  HmmRegimeClassifier       ← GaussianHMM(5상태) 앙상블
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
  ├─ get_deferred()          이전 run의 지연 매수 로드
  └─ record_sell() / add_deferred()  실행 후 기록
        │
        ▼
[executor.py]  KisRebalancer
  ├─ get_portfolio_state()   KIS API → 현재 비중 + 드로우다운 + orphan 탐지
  ├─ sell_orphans()          유니버스 외 보유 종목 전량 매도
  ├─ _split_buy_orders()     버퍼 여유분 기준 즉시/지연 분류
  ├─ _wait_for_fill()        미체결 대기 루프 (100초마다 가격 조정, 1000초 타임아웃)
  └─ rebalance()             매도 우선 주문 실행 + 지연 매수 반환
        │
        ▼
[pykis]  한국투자증권 KIS API — 지정가 주문 체결
        │
        ▼
[state.json]  영속 상태
  peak_krw / confirmed_regime / candidate_regime / candidate_count
  last_switch_date / trigger_krw / trigger_usd
  saved_blended_targets / saved_realized_vol / saved_regime / saved_confidence
  pending_sells / deferred_buys / last_rebalanced_{krw,usd}_at
```

---

## 모듈별 책임

### fetcher.py
- `fetch_signal_prices(tickers, lookback_days)` — yfinance로 가격 히스토리 수집
- `fetch_usd_krw(fallback)` — KRW=X 실시간 환율 조회
- `fetch_fred_data()` — FRED API로 HY OAS(BAMLH0A0HYM2)·10Y-2Y(T10Y2Y) 조회

### features.py
- `compute_features(prices, fred_data)` — 최신 단일 시점 피처 dict 반환
  - `momentum_1m/3m`: SPY 1·3개월 수익률
  - `realized_vol`: SPY 21일 표준편차 × √252 (연환산)
  - `vix`: ^VIX 최신 종가
  - `credit_signal`: HYG - TLT 1개월 수익률 차 (FRED 있으면 HY 스프레드 기반으로 대체)
- `compute_feature_matrix(prices)` — HMM 학습용 일별 피처 DataFrame (최소 65일 warm-up 후)

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
  - 추론: 최근 60일 시퀀스 → 마지막 시점 사후 확률 (전이 행렬 문맥 반영)
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
  1순위 — commodity + managed_futures (KRW 대체재 없음, 전액 보장)
  2순위 — equity_factor + equity_sector + equity_individual (예산 내 비례)
  3순위 — bond_usd (잔여 예산)
  잔여 → 전항목 비례 확대 (USD 계좌 100% 소진)
  ```

- `apply_risk_controls(weights, drawdown, thresholds, equity_tickers)` — equity만 단계 축소 (채권·금·현금 유지)

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
- `record_sell(ticker, amount_krw, currency)` — 매도 체결 시 T+2 결제 예정일 기록
- `purge_settled()` — 결제일 경과 항목 정리
- `get_deferred() / clear_deferred() / add_deferred()` — 지연 매수 대기열 관리
- `to_dict()` → state.json `pending_sells` / `deferred_buys` 키로 영속화

### executor.py — KisRebalancer
- `_init_clients(auth_path)` — config.accounts 기반 pykis 클라이언트 생성 (동일 acc_no 단일 클라이언트 재사용)
- `get_portfolio_state()` — 전 계좌 잔고 합산 → (total_krw, total_usd_krw, total_krw_only, current_weights, drawdown)
  - orphan(유니버스 외 보유)은 `_orphan_holdings`에 별도 수집
  - 드로우다운은 state.json peak_krw 대비 낙폭 (orphan 포함 전체 자산 기준)
- `sell_orphans(side)` — 잔고 재조회 후 orphan 전량 매도
- `rebalance(...)` — 매도 우선 정렬, 버퍼 기반 즉시/지연 분류, 주문 실행
- `_wait_for_fill(order, reorder, ...)` — 미체결 대기 루프: 100초마다 0.1% 가격 조정 재주문, 1000초 타임아웃
- `_build_orders(...)` — per_ticker_drift_threshold 적용, (ticker, currency, amount_diff_krw) 목록 생성
- `_execute_order(ticker, currency, amount_diff_krw)` — KRW 환산 금액 기준 매수/매도
- `_execute_exact_sell(ticker, currency, qty, client)` — 수량 지정 전량 매도 (orphan 정리 전용)

### run.py — 파이프라인 진입점
- `_run_market_analysis(config, state)` — 단계 1-3: 데이터 수집 → 피처 → 레짐
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

## 레짐 정의 (5개)

| 레짐 | 조건 | 전략 |
|------|------|------|
| **Goldilocks** | 성장↑ + 인플레 안정 | Equity 최대화 (TSLA+PLTR 풀 포지션) |
| **Reflation** | 성장↑ + 인플레↑ | DBC·XLE·Gold·VTV 강화, Satellite 축소 |
| **Slowdown** | 성장↓ | Bond·DBMF·Gold 방어 |
| **Stagflation** | 성장↓ + 인플레↑ | DBC·XLE·Gold·Cash 집중, 장기채 회피 |
| **Crisis** | 유동성 쇼크 | Cash 최대화, DBMF, Bond; Equity 최소 |

---

## 투자 유니버스

| 종목 | 이름 | 통화 | 자산군 | 계좌 |
|------|------|------|--------|------|
| 379800 | KODEX S&P500 | KRW | equity_etf | KRW_1 |
| 379810 | KODEX 나스닥100 | KRW | equity_etf | KRW_2 |
| VTV | Vanguard Value ETF | USD | equity_factor | USD |
| AVUV | Avantis US Small Cap Value | USD | equity_factor | USD |
| XLE | Energy Select Sector SPDR | USD | equity_sector | USD |
| TSLA | Tesla | USD | equity_individual | USD |
| PLTR | Palantir | USD | equity_individual | USD |
| IEF | iShares 7-10Y Treasury | USD | bond_usd | USD |
| SHY | iShares 1-3Y Treasury | USD | bond_usd | USD |
| 305080 | TIGER 미국채10년 | KRW | bond_krw | KRW_2 |
| 411060 | ACE KRX금현물 | KRW | gold | KRW_1 |
| DBC | Invesco DB Commodity Index | USD | commodity | USD |
| DBMF | iMGP DBi Managed Futures | USD | managed_futures | USD |
| 469830 | SOL 초단기채권 | KRW | cash | KRW_2 |

---

## 계좌 구조

```
KRW_1 (64378890-01 KRW) → 379800, 411060
KRW_2 (64521213-01 KRW) → 379810, 305080, 469830
USD   (64378890-01 USD) → VTV, AVUV, XLE, TSLA, PLTR, IEF, SHY, DBC, DBMF
```

잔고 읽기: 모든 계좌 합산  
주문 실행: `universe[ticker].exec_account` 단일 계좌

---

## 설정 구조 (config.yaml)

```yaml
signal:          # 레짐 신호용 티커 + 조회 기간
accounts:        # KRW_1 / KRW_2 / USD 계좌 정의
universe:        # 종목별 메타 (currency, exec_account, asset_class)
regime_targets:  # 레짐별 자산군 목표 비중
asset_routing:   # 자산군 → 종목 내부 비율
  equity_etf:         379800(64%) / 379810(36%)
  equity_factor:      VTV(60%) / AVUV(40%)
  equity_sector:      XLE(100%)
  equity_individual:  TSLA(36%) / PLTR(64%)
  commodity:          DBC(100%)
  managed_futures:    DBMF(100%)
  bond_usd:           IEF(58%) / SHY(42%)
  bond_krw:           305080(100%)
  gold:               411060(100%)
  cash:               469830(100%)
class_max_weight:     # 자산군 상한 (equity_individual 20%, commodity 20%, managed_futures 12%, gold 18%)
account_ratio_fallback: {usd: 0.30, krw: 0.70}
vol_targeting:   # enabled, target_vol=10%, floor=0.65
hmm:             # enabled, lookback_days=500, predict_lookback=60, override_threshold=0.60
regime_filter:   # confirmation_count=3, cooldown_days=5, confidence_threshold=0.40
rebalancing:     # drift_threshold=5%, per_ticker_drift_threshold=5%, min_rebalance_interval_days=7
risk:            # drawdown_thresholds: mild=-10%, moderate=-20%, severe=-30%
settlement:      # buffer_tickers=["469830"], buffer_min=7%, synthetic_pairs
```

---

## T+2 결제 지연 대응

**문제**: 매도 후 T+2까지 현금 재투입 불가 → 레짐 전환 시 신호-실행 괴리

**해결 (두 방법 조합)**

### Pre-Funding Buffer
- `469830`(SOL 초단기채)을 KRW 계좌의 최소 7%로 항상 유지
- 매수 시 버퍼에서 즉시 집행 → 매도 결제(T+2) 후 버퍼 자동 복구

```
Day 0: 매도 실행 + 버퍼(469830)로 매수 즉시 집행
Day 2: 매도 결제 완료 → 다음 리밸런싱 시 버퍼 복구
```

### Synthetic Exposure
- 버퍼 부족 시: USD 매수를 지연하되 KRW 동등 자산으로 임시 노출 유지
- `deferred_buys` → state.json 저장 → 다음 run에서 `apply_synthetic_reallocation()`

```
예시: IEF 매수 지연
  → 305080(TIGER 미국채10년) 목표비중 임시 증가
  → 다음 run IEF 매수 성공 시 305080 초과분 자연 정리
synthetic_pairs: TSLA→379800, PLTR→379810, VTV→379800,
                 AVUV→379800, XLE→379800, IEF→305080,
                 SHY→469830, DBC→469830, DBMF→469830
```

---

## 모니터링

```bash
# 웹 컨트롤 패널
python trading/server.py        # http://localhost:8080

# Prometheus + Grafana
docker-compose up -d            # prometheus:9090 + grafana:3000
```

주요 메트릭: 레짐 인덱스, 신뢰도, 드로우다운, 자산 총액, 미결제 건수, 후보 레짐 카운트

---

## 의존성

| 라이브러리 | 용도 |
|---|---|
| yfinance | 레짐 신호용 시장 데이터 |
| pykis (python-kis) | KIS 계좌 잔고 조회 + 주문 실행 |
| pyyaml | config.yaml / auth.yaml 파싱 |
| pandas / numpy | 피처 계산 |
| hmmlearn | GaussianHMM 레짐 앙상블 |
| scikit-learn | StandardScaler (HMM 전처리) |
| fredapi | FRED API 연동 (선택, FRED_API_KEY 필요) |
| slack-sdk | Slack 알림 |
| fastapi + uvicorn | 웹 컨트롤 패널 |
| prometheus-client | 메트릭 노출 |
