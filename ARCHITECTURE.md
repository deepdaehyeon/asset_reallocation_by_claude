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
[portfolio.py]  get_target_weights()  +  apply_risk_controls()
        │  {ticker: target_fraction}  (드로우다운 스케일 적용 후)
        ▼
[executor.py]  KisRebalancer
  ├─ get_portfolio_state()   KIS API → 현재 비중 + 드로우다운
  └─ rebalance()             drift > 5%p 시 매도 우선 주문 실행
        │
        ▼
[pykis (KIS API)]  한국투자증권 주문 체결
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

```
drawdown ≤ -30%  → 전량 현금화 (scale 0.0)
drawdown ≤ -20%  → 비중 50% 축소 (scale 0.5)
drawdown ≤ -10%  → 비중 20% 축소 (scale 0.8)
그 외             → 변경 없음 (scale 1.0)
```

### executor.py — KisRebalancer
- **`_init_clients()`**: config.accounts 기반 pykis 클라이언트 생성
  - 동일 acc_no는 단일 클라이언트 재사용 (64378890-01 KRW·USD 공유)
- **`get_portfolio_state()`**: 전 계좌 잔고 합산
  - 유니버스 외 보유(orphan)는 분리 경고 후 비중 계산에서 제외
  - 현금 조회: `orderable_amount(price=1)` 프록시 (379800 / QQQ)
  - 비중 분모: 유니버스 보유 + 현금 (orphan 제외 → drift 왜곡 방지)
  - state.json에 고점 저장 → 드로우다운 계산
- **`rebalance()`**: drift ≥ threshold 시 리밸런싱 실행
  - 매도 우선 정렬 (amount < 0 먼저)
  - 각 주문은 exec_account(config.yaml per-ticker) 지정 계좌로 라우팅
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

## 미구현 (system_archi.md 대비)

| 모듈 | 현황 |
|---|---|
| FRED API / ecos | 미사용 — yfinance proxy로 대체 |
| HMM 레짐 모델 | 미구현 — 규칙 기반으로 대체 |
| LLM 텍스트 신호 | Phase 3 미착수 |
| 변동성 타겟팅 | 미구현 (드로우다운 제어만 구현) |
| Walk-Forward 재학습 | 미구현 |
| Slack 알림 | 미구현 |
| 월간 Turnover 상한 | 미구현 |
