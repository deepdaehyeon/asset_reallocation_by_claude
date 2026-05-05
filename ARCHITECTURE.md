# ARCHITECTURE
_system_archi.md Phase 1·2 구현체_

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
        │  {momentum_1m, momentum_3m, realized_vol, vix, credit_signal}
        ▼
[regime.py]  detect_regime()
        │  "Risk-On" | "Neutral" | "Risk-Off" | "High-Vol"
        ▼
[portfolio.py]  get_target_weights()
              + apply_risk_controls()       ← 드로우다운 스케일
              + enforce_buffer_floor()      ← 버퍼 최소 비중 보장 (신규)
              + apply_synthetic_reallocation() ← USD 지연 매수 → KRW 합성 (신규)
        │  {ticker: target_fraction}
        ▼
[settlement.py]  SettlementTracker         (신규)
  ├─ purge_settled()        T+2 만기된 매도 기록 정리
  ├─ get_deferred()         이전 run의 지연 매수 로드
  └─ record_sell() / add_deferred()   실행 후 기록
        │
        ▼
[executor.py]  KisRebalancer
  ├─ get_portfolio_state()   KIS API → 현재 비중 + 드로우다운
  ├─ _split_buy_orders()     버퍼 여유분 기준 즉시/지연 분류 (신규)
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

### regime.py
- 규칙 기반 4단계 레짐 분류
- 우선순위: High-Vol → Risk-Off → Risk-On → Neutral
- 신호 2개 이상 충족 시 해당 레짐 판정

```
High-Vol  : realized_vol > 25% OR VIX > 35
Risk-Off  : (mom1m<-3%, mom3m<-5%, VIX>25, credit<-3%) 중 2개 이상
Risk-On   : (mom1m>2%, mom3m>4%, VIX<18, credit>2%) 중 2개 이상
Neutral   : 그 외
```

### portfolio.py
- `get_target_weights(regime, config)`: config.yaml의 레짐별 비중 로드
- `apply_risk_controls(weights, drawdown, thresholds)`: 드로우다운 스케일
- `enforce_buffer_floor(weights, buffer_tickers, buffer_min)`: **[신규]** 버퍼 최소 비중 보장
- `apply_synthetic_reallocation(target, deferred_buys, synthetic_pairs, total_krw)`: **[신규]** USD 지연 매수 → KRW 합성 노출

```
drawdown ≤ -30%  → 전량 현금화 (scale 0.0)
drawdown ≤ -20%  → 비중 50% 축소 (scale 0.5)
drawdown ≤ -10%  → 비중 20% 축소 (scale 0.8)
그 외             → 변경 없음 (scale 1.0)

버퍼 플로어 (설정값 buffer_min=7%):
  469830 + SHY 합계 < 7% → 부족분을 비-버퍼 자산에서 pro-rata 차감 후 469830에 추가

합성 노출 (이전 run의 deferred_buys 기준):
  IEF 지연 2% → 305080 목표비중 +2% (다음 run에서 IEF 매수 성공 시 자동 소멸)
```

### settlement.py — SettlementTracker [신규]
- **`record_sell(ticker, amount_krw, currency)`**: 매도 체결 시 T+2 결제 예정일과 함께 기록
- **`purge_settled()`**: 결제일이 지난 항목 정리 (매 run 시작 시 호출)
- **`get_deferred() / clear_deferred()`**: 지연 매수 대기열 접근
- **`to_dict()`**: state.json 직렬화 → `pending_sells` / `deferred_buys` 키로 저장

### executor.py — KisRebalancer
- **`_init_clients()`**: config.accounts 기반 pykis 클라이언트 생성
  - 동일 acc_no는 단일 클라이언트 재사용 (64378890-01 KRW·USD 공유)
- **`get_portfolio_state()`**: 전 계좌 잔고 합산
  - 유니버스 외 보유(orphan)는 분리 경고 후 비중 계산에서 제외
  - 현금 조회: `orderable_amount(price=1)` 프록시 (379800 / QQQ)
  - 비중 분모: 유니버스 보유 + 현금 (orphan 제외 → drift 왜곡 방지)
  - state.json에 고점 저장 → 드로우다운 계산
- **`_split_buy_orders()`**: **[신규]** 현재 버퍼 평가액 기준 greedy 분류
  - 버퍼 = `current_weights[469830 + SHY] × total_krw`
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

KRW_1 (64378890-01 KRW) → 379800, 069500, 411060
KRW_2 (64521213-01 KRW) → 379810, 305080, 469830
USD   (64378890-01 USD) → TSLA, PLTR, IEF, SHY, TLT
```

잔고 읽기는 모든 계좌 합산, 주문 실행은 exec_account 단일 계좌.

---

## 설정 구조 (config.yaml)

```yaml
signal:          # 레짐 신호용 티커 + 조회 기간
accounts:        # KRW_1 / KRW_2 / USD 계좌 정의
universe:        # 종목별 메타 (currency, exec_account, asset_class)
regime_weights:  # 레짐별 목표 비중 테이블
rebalancing:     # drift_threshold, min_order_krw, usd_krw_fallback
risk:            # drawdown_thresholds (mild / moderate / severe)
settlement:      # [신규] 결제 지연 대응 설정
  buffer_tickers:   ["469830", "SHY"]    # 즉시 가용 버퍼 자산
  buffer_min:       0.07                 # 버퍼 최소 비중 (전체 대비 7%)
  synthetic_pairs:                       # USD 지연 → KRW 합성 매핑
    "IEF":  "305080"   # iShares 7-10Y → TIGER 미국채10년
    "TLT":  "305080"   # iShares 20Y+  → TIGER 미국채10년
    "SHY":  "469830"   # iShares 1-3Y  → SOL 초단기채권
    "TSLA": "379800"   # Tesla         → KODEX S&P500
    "PLTR": "379810"   # Palantir      → KODEX 나스닥100
```

---

## 의존성

| 라이브러리 | 용도 |
|---|---|
| yfinance | 레짐 신호용 시장 데이터 |
| python-kis (pykis) | KIS 계좌 잔고 조회 + 주문 실행 |
| pyyaml | config.yaml / auth.yaml 파싱 |
| pandas / numpy | 피처 계산 |
| python-dotenv | 환경 변수 로드 |

---

## T+2 결제 지연 대응 설계

**문제**: 자산 매도 후 결제 완료(T+2)까지 현금 재투입 불가 → 레짐 전환 시 신호-실행 괴리 발생

**해결 (두 가지 방법 조합)**

### 방법 1: Pre-Funding Buffer (실행 버퍼 상시 유지)

- `469830`(SOL 초단기채) + `SHY`(iShares 1-3Y) 를 단순 방어 자산이 아닌 **즉시 가용 실행 자금**으로 재정의
- `enforce_buffer_floor()`로 레짐에 관계없이 항상 최소 7% 유지 (`buffer_min`)
- 매수 주문 시 버퍼에서 즉시 집행 → 매도 결제(T+2) 후 버퍼 복구

```
Day 0: 매도 주문 실행 + 버퍼(469830·SHY)로 매수 즉시 집행
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

## 미구현 (system_archi.md 대비)

| 모듈 | 현황 |
|---|---|
| FRED API / ecos | 미사용 — yfinance proxy로 대체 |
| HMM 레짐 모델 | 미구현 — 규칙 기반으로 대체 |
| LLM 텍스트 신호 | Phase 3 미착수 |
| 변동성 타겟팅 | 미구현 (드로우다운 제어만 구현) |
| Walk-Forward 재학습 | 미구현 |
| 월간 Turnover 상한 | 미구현 |
