# ARCHITECTURE

---

## 데이터 흐름

```
[yfinance]
  SPY, ^VIX, TLT, HYG (130일 히스토리)
        │
        ▼
[fetcher.py]  fetch_signal_prices()
        │  DataFrame (종목 × 날짜)
        ▼
[features.py]  compute_features()
        │  {momentum_1m, momentum_3m, realized_vol, vix, credit_signal,
        │   hy_spread, curve_10y2y}   ← FRED API 연동 시 추가 피처
        ▼
[regime.py]  detect_regime()
        │  "Risk-On" | "Neutral" | "Risk-Off" | "High-Vol"
        │  + regime_probs {regime: prob}  ← HMM 앙상블 사후 확률
        ▼
[portfolio.py]
  blend_regime_targets()        ← HMM 확률로 연속 블렌딩 (Continuous Exposure)
  apply_class_caps()            ← 자산군별 최대 비중 상한
  apply_vol_targeting()         ← 실현 변동성 > 10% 시 equity 축소
  derive_account_weights()      ← 계좌별 종목 비중 도출
  apply_risk_controls()         ← 드로우다운 스케일
  enforce_buffer_floor()        ← 버퍼 최소 비중 보장
  apply_synthetic_reallocation() ← USD 지연 매수 → KRW 합성
        │  {ticker: target_fraction}
        ▼
[settlement.py]  SettlementTracker
  ├─ purge_settled()        T+2 만기된 매도 기록 정리
  ├─ get_deferred()         이전 run의 지연 매수 로드
  └─ record_sell() / add_deferred()   실행 후 기록
        │
        ▼
[executor.py]  KisRebalancer
  ├─ get_portfolio_state()   KIS API → 현재 비중 + 드로우다운
  ├─ _split_buy_orders()     버퍼 여유분 기준 즉시/지연 분류
  └─ rebalance()             drift > 5%p 시 즉시 주문 실행 + 지연 매수 반환
        │
        ▼
[pykis (KIS API)]  한국투자증권 주문 체결
        │
        ▼
[state.json]   peak_krw + pending_sells + deferred_buys 영속화
```

---

## 모듈별 책임

### fetcher.py
- yfinance로 레짐 신호용 주가 히스토리 수집
- 반환: `pd.DataFrame` (columns = 종목 심볼)

### features.py
- 레짐 판단에 필요한 수치 피처 계산
- `momentum_1m/3m`: SPY 1·3개월 수익률
- `realized_vol`: SPY 21일 표준편차 × √252
- `vix`: ^VIX 최신 종가
- `credit_signal`: HYG - TLT 1개월 수익률 차 (양수 = Risk-On)
- `hy_spread`, `curve_10y2y`: FRED API 연동 시 추가 (BAMLH0A0HYM2, T10Y2Y)

### regime.py
- 규칙 기반 4단계 레짐 분류 + HMM 앙상블
- 우선순위: High-Vol → Risk-Off → Risk-On → Neutral

```
High-Vol  : realized_vol > 25% OR VIX > 35
Risk-Off  : (mom1m<-3%, mom3m<-5%, VIX>25, credit<-3%) 중 2개 이상
Risk-On   : (mom1m>2%, mom3m>4%, VIX<18, credit>2%) 중 2개 이상
Neutral   : 그 외
```

- HMM(GaussianHMM 4상태) 앙상블: override_threshold(기본 60%) 초과 시 채택
- 신뢰도 < 40% 시 Neutral 자동 폴백
- RegimeFilter: N회 연속 확인(기본 3회) + 쿨다운(기본 5일)로 잦은 전환 방지

### portfolio.py

**`blend_regime_targets(regime_probs, config)`**  
HMM 사후 확률을 가중치로 자산군 목표 비중을 연속 혼합한다.  
Risk-On 70% / Neutral 30% → 비중도 7:3 가중 평균. 레짐 오판 시 양방향 슬리피지 완화.

**`apply_class_caps(targets, class_max)`**  
자산군별 최대 비중 상한 적용. 초과분은 cash로 이동.

```
managed_futures   ≤ 10%
equity_individual ≤ 12%
gold              ≤ 18%
```

**`apply_vol_targeting(targets, realized_vol, config)`**  
실현 변동성 > 목표(10%) 시 equity 비례 축소.  
scale = clip(10% / realized_vol, floor=0.65, 1.0) → 축소분 cash 이동.

**`apply_risk_controls(weights, drawdown, thresholds)`**  
드로우다운 단계별 equity 축소. 채권·금·현금은 유지한다(바닥 전량 현금화 방지).

```
drawdown ≤ -10%  → equity × 0.75
drawdown ≤ -20%  → equity × 0.40
drawdown ≤ -30%  → equity = 0.0  (채권·금·현금 유지)
```

**`enforce_buffer_floor(weights, buffer_tickers, buffer_min)`**  
버퍼 자산(469830)이 항상 buffer_min(7%) 이상 유지되도록 비-버퍼 자산 pro-rata 차감.

**`apply_synthetic_reallocation(target, deferred_buys, synthetic_pairs, total_krw)`**  
이전 run에서 지연된 USD 매수에 대해 KRW 동등 자산 비중을 임시 증가시킨다.

### settlement.py — SettlementTracker
- **`record_sell(ticker, amount_krw, currency)`**: 매도 체결 시 T+2 결제 예정일과 함께 기록
- **`purge_settled()`**: 결제일이 지난 항목 정리 (매 run 시작 시 호출)
- **`get_deferred() / clear_deferred()`**: 지연 매수 대기열 접근
- **`to_dict()`**: state.json 직렬화 → `pending_sells` / `deferred_buys` 키로 저장

### executor.py — KisRebalancer
- **`_init_clients()`**: config.accounts 기반 pykis 클라이언트 생성
  - 동일 acc_no는 단일 클라이언트 재사용 (64378890-01 KRW·USD 공유)
- **`get_portfolio_state()`**: 전 계좌 잔고 합산
  - 유니버스 외 보유(orphan)는 분리 경고 후 비중 계산에서 제외
  - 현금 조회: `orderable_amount(price=1)` 프록시
  - 비중 분모: 유니버스 보유 + 현금 (orphan 제외 → drift 왜곡 방지)
  - state.json에 고점 저장 → 드로우다운 계산
- **`_split_buy_orders()`**: 현재 버퍼 평가액 기준 greedy 분류
  - 버퍼 = `current_weights[469830] × total_krw`
  - 큰 매수부터 우선 할당, 초과분은 deferred 반환
- **`rebalance(tracker)`**: drift ≥ threshold 시 리밸런싱 실행
  - 매도 우선 정렬 (amount < 0 먼저)
  - 매도 체결 시 `tracker.record_sell()` 호출
  - 반환: `(order_log, deferred_buys)`
- **`_execute_order()`**: pykis 지정가 주문 + 미체결 시 100초마다 0.1% 가격 조정
  - 1000초 초과 시 타임아웃

---

## 계좌 라우팅 규칙

```
config.yaml universe[ticker].exec_account 에 명시

KRW_1 (64378890-01 KRW) → 379800, 411060
KRW_2 (64521213-01 KRW) → 379810, 305080, 469830
USD   (64378890-01 USD) → TSLA, PLTR, VTV, IEF, SHY, DBC, DBMF
```

잔고 읽기는 모든 계좌 합산, 주문 실행은 exec_account 단일 계좌.

---

## 설정 구조 (config.yaml)

```yaml
signal:          # 레짐 신호용 티커 + 조회 기간
accounts:        # KRW_1 / KRW_2 / USD 계좌 정의
universe:        # 종목별 메타 (currency, exec_account, asset_class)
regime_targets:  # 레짐별 자산군 목표 비중 (블렌딩 기준값)
asset_routing:   # 자산군 → 종목 매핑 (within-class 고정 비율)
  equity_etf:    379800(64%) / 379810(36%)
  equity_factor: VTV(100%)
  equity_individual: TSLA(36%) / PLTR(64%)
  commodity:     DBC(100%)
  managed_futures: DBMF(100%)
  bond_usd:      IEF(58%) / SHY(42%)
  bond_krw:      305080(100%)
  gold:          411060(100%)
  cash:          469830(100%)
class_max_weight: # 자산군별 최대 비중 상한
rebalancing:     # drift_threshold, per_ticker_drift_threshold, min_order_krw
vol_targeting:   # enabled, target_vol(10%), floor(0.65)
hmm:             # enabled, lookback_days(500), override_threshold(0.60)
regime_filter:   # confirmation_count(3), cooldown_days(5), confidence_threshold(0.40)
risk:            # drawdown_thresholds (mild / moderate / severe)
settlement:      # 결제 지연 대응 설정
  buffer_tickers:   ["469830"]
  buffer_min:       0.07
  synthetic_pairs:  USD 지연 → KRW 합성 매핑
    TSLA → 379800,  PLTR → 379810
    VTV  → 379800,  IEF  → 305080
    SHY  → 469830,  DBC  → 469830,  DBMF → 469830
```

---

## 의존성

| 라이브러리 | 용도 |
|---|---|
| yfinance | 레짐 신호용 시장 데이터 |
| python-kis (pykis) | KIS 계좌 잔고 조회 + 주문 실행 |
| pyyaml | config.yaml / auth.yaml 파싱 |
| pandas / numpy | 피처 계산 |
| hmmlearn | GaussianHMM 레짐 앙상블 |
| fredapi | FRED API 연동 (선택, FRED_API_KEY 필요) |
| prometheus_client | 메트릭 노출 (Grafana 대시보드 연동) |
| python-dotenv | 환경 변수 로드 |

---

## T+2 결제 지연 대응 설계

**문제**: 자산 매도 후 결제 완료(T+2)까지 현금 재투입 불가 → 레짐 전환 시 신호-실행 괴리 발생

**해결 (두 가지 방법 조합)**

### 방법 1: Pre-Funding Buffer (실행 버퍼 상시 유지)

- `469830`(SOL 초단기채)을 단순 방어 자산이 아닌 **즉시 가용 실행 자금**으로 재정의
- `enforce_buffer_floor()`로 레짐에 관계없이 항상 최소 7% 유지 (`buffer_min`)
- 매수 주문 시 버퍼에서 즉시 집행 → 매도 결제(T+2) 후 버퍼 복구

```
Day 0: 매도 주문 실행 + 버퍼(469830)로 매수 즉시 집행
Day 2: 매도 대금 결제 완료 → 버퍼 자동 복구 (다음 리밸런싱 시)
```

### 방법 2: Synthetic Exposure (합성 노출로 공백 제거)

- 버퍼도 부족한 경우: USD 매수를 지연하되 KRW 동등 자산으로 임시 노출 유지
- `deferred_buys`를 `state.json`에 저장 → 다음 run에서 `apply_synthetic_reallocation()` 적용

```
예시: IEF 매수 불가 (USD 결제 지연)
  → 305080(TIGER 미국채10년) 목표비중 +해당액/총자산 임시 증가
  → 다음 run에서 IEF 매수 성공 시 305080 초과분이 자동 정리됨
```

**결과**: 완전히 비어있는 시간(full cash gap) 제거. 레짐 신호와 실제 포지션의 괴리 최소화.

---

## 모니터링 (Prometheus + Grafana)

`server.py`에서 Prometheus 메트릭을 노출하며 Docker Compose로 Grafana까지 연결된다.

```bash
docker-compose up -d   # prometheus:9090 + grafana:3000 기동
python server.py       # FastAPI + WebSocket 컨트롤 패널 (8080)
```

주요 메트릭: 레짐·신뢰도, 드로우다운, 변동성, 계좌별 자산, 리밸런싱 횟수.
