# CHANGE LOG

*최종 갱신: 2026-05-27*

## 2026-05-27

### RF forward-looking 라벨 도입 (자기참조 끊기, 옵트인)

- `BalancedRFClassifier(forward_window=N)` 추가. N>0이면 t의 라벨로 t+N 시점의 `detect_regime()`를 사용 → 자기참조 끊김.
- `hmm.rf_forward_window` config 키. 기본 0(기존 동작 유지, 옵트인).
- 표본 부족 시(`len(fm) <= N+1`) 안전하게 룰 라벨로 폴백.
- 비교 백테스트 스크립트 `scripts/compare_rf_label.py` 추가 (rule baseline vs forward N=21 / 63).
- 실험 노트: `docs/experiment_2026-05-27_rf_forward_label.md`.
- **백테스트 결과 (2010~2025)**: forward 라벨이 baseline 대비 Sharpe -0.02~-0.07, MaxDD -1.5pp 악화 → **채택 보류**(`rf_forward_window=0` 유지). 코드는 옵트인 옵션으로 보존.

### RF forward 라벨 Round 2 (FRED 포함 + 옵션 2 quantile binning)

후속 실험으로 두 가지를 같이 검증:
- (a) FRED 매크로 7개(`cpi_yoy`/`cpi_mom_zscore`/`unrate_chg_3m`/`breakeven_5y`/`m2_yoy`/`fed_bs_yoy`/`curve_10y2y`) 포함 재실험
- (b) `BalancedRFClassifier(label_mode='quantile')` 추가 — t의 라벨로 t+N 시점의 `(momentum_1m, realized_vol)` 학습 분포 분위 매핑. `detect_regime` 호출 없음.

지원 작업:
- `trading/fetcher.py`에 프로젝트 루트 `.env` 자동 로딩 (`FRED_API_KEY` 등) + ICE 빈 응답 가드 (HY 스프레드 라이선스 회수 대응).
- `hmm.rf_label_mode` config 키 추가 (`rule_at_future` | `quantile`).
- `scripts/compare_rf_label.py`를 5개 시나리오 비교로 확장.

**Round 2 결과**: baseline Sharpe 0.58, MaxDD -12.5%, 위험레짐 미감지 10일 / forward 라벨 모두 위험레짐 미감지 30일(3배 증가). 자동 판정은 forward_rule_21(Sharpe 0.60)을 추천했으나 위험감지 악화로 실질 권고는 **baseline 유지**. FRED 매크로 자체가 성능을 향상시키지 못한 점은 publication lag(#3) 미반영 가능성 시사 → **#3 처리 후 본 실험 재실행 권장**.

### FRED publication lag 적용 (외부 비평 #3)

- `trading/fetcher.fetch_fred_history()`에 시리즈별 publication lag(영업일) 적용:
  CPI 30, UNRATE 25, M2 30, WALCL 7, BEI/T10Y2Y/HY 각 1.
  → reference date 기준 raw 시리즈를 발표일로 shift 후 daily reindex.
- `fetch_fred_data()`(라이브)는 변경 없음. FRED API가 라이브에서는 이미 "발표된 데이터만" 반환하므로 lag 불필요.
- 일별 시리즈가 calendar day 기반일 때 BDay shift 후 중복이 생기는 케이스를 안전하게 처리 (마지막 값 유지).

### RF forward 라벨 Round 3 (FRED + lag 적용)

가장 큰 발견: **publication lag 적용이 lookahead bias의 영향을 제거**.

| Round | 조건                            | baseline Sharpe | baseline MaxDD |
|-------|---------------------------------|----------------:|---------------:|
| 1     | FRED 없음                       |  0.78           | -11.0%         |
| 2     | FRED 포함 + lag 미적용         |  0.58           | -12.5%         |
| **3** | **FRED 포함 + lag 적용**       | **0.70**        | **-9.4%**      |

Round 2 → 3로 baseline Sharpe +0.12, MaxDD +3.1pp 개선. Round 2가 사후 발표된 매크로를 누설한 결과였음이 확인됨.

forward 라벨 채택 결론은 lag 적용 후에도 변함없음 (Round 3에서도 위험감지 20→35일 악화). **`rf_forward_window=0` 유지**. publication lag fix는 라이브에 반영 완료.

### 레짐 분류기 진단 도구 + 캘리브레이션 검증 (외부 비평 #4, #6)

- `backtest/engine.py`: `_get_regime()`이 `combined_conf` 함께 반환. result DataFrame에 컬럼 추가.
- `scripts/regime_diagnostics.py`: 백테스트 1회 실행 후 5개 진단 출력 (#6 forward return 분리도·전환 적시성·whipsaw 빈도, #4 calibration reliability·threshold 민감도).
- 결과 JSON: `docs/regime_diagnostics_20260527.json`, 분석 노트: `docs/experiment_2026-05-27_regime_observability.md`.

**핵심 진단 결과 (2010~2025, FRED 포함)**:
- Goldilocks 평균 forward 21일 수익률 +0.71% — Reflation(+1.25%)·Stagflation(+0.88%)·Slowdown(+0.82%)보다 낮음 (이론과 반대). Crisis 평균이 +0.38% 양수 → Crisis 진입이 후행적.
- whipsaw 비율 57.6% (243건 중 140건이 21일 내 직전 레짐 복귀). 분류 신호 자체가 매우 noisy.
- confidence 단조성 Spearman ρ = **-0.325 (음수)** → `(rule_conf + hmm_conf)/2` 산식이 단조성 보장 못함. 외부 비평이 정확했음.

라이브 동작은 변경 없음 (진단 도구). 후속 개선 작업: (1) 신뢰도 산식을 min/product로 교체 후 비교, (2) whipsaw 억제 강화(confirmation/cooldown 상향), (3) detect_regime 임계값 재검토.

### 신뢰도 산식 옵션 추가 (외부 비평 #4 본질 해결)

- `regime.compute_combined_confidence(rule, hmm, method)` 함수 추가 — `mean` / `min` / `product`.
- `regime_filter.confidence_method` config 키 (기본 `mean`, 호환 유지). run.py와 backtest engine 모두 함수 사용.
- 비교 스크립트 `scripts/compare_confidence_methods.py` + 실험 노트 `docs/experiment_2026-05-27_confidence_formula.md`.

**비교 결과 (2010~2025, FRED 포함)**:

| method     | Sharpe | MaxDD  | **Spearman ρ** | fallback @0.40 |
|------------|-------:|-------:|---------------:|---------------:|
| mean       | 0.70   | -9.4%  | **-0.325**     | 38.8%         |
| **min**    | 0.70   | -9.4%  | **+0.595**     | 72.4%         |
| product    | 0.70   | -9.4%  | **+0.681**     | 83.2%         |

단조성이 -0.325에서 +0.60(min)/+0.68(product)로 극적 회복. 백테스트 성과는 동일(엔진이 conf 폴백 미적용). **min을 새 기본으로 권장**, 단 fallback이 mean 38.8% → min 72.4%로 증가하므로 `confidence_threshold`도 0.40 → ~0.20으로 동시 조정 필요. 채택 시 config 변경만으로 즉시 적용 (별도 코드 변경 없음).

→ **사용자 채택**: `confidence_method=min`, `confidence_threshold=0.20` 적용.

### blend_probs EWMA 평활 (외부 비평 #6-c 본질 해결)

- `regime_filter.blend_smoothing_alpha` config 키 추가. `new = α·prev + (1-α)·raw` 후 정규화.
- `backtest/engine.py`: `_prev_blend` 인스턴스 상태, 워크포워드 사이 유지.
- `trading/run.py`: `state.json`의 `prev_blend_probs`로 일별 호출 영속화.
- 비교 스크립트: `scripts/compare_blend_smoothing.py` / 실험 노트: `docs/experiment_2026-05-27_blend_smoothing.md`.

**비교 결과 (2010~2025, FRED + confidence_method=min)**:

| α   | Sharpe | MaxDD  | whipsaw      | Crisis일 | 채택 |
|-----|-------:|-------:|-------------:|---------:|------|
| 0   | 0.700  | -9.42% | 140 (57.6%)  | 217      | baseline |
| **0.5** | **0.687** | **-10.60%** | **86 (47.3%)** | **168** | **✓** |
| 0.7 | 0.677  | -11.80%| 91 (49.7%)   | 183      | ✗ MaxDD -2.4pp |
| 0.9 | 0.722  | -13.67%| 107 (53.5%)  | 183      | ✗ MaxDD -4.3pp |

α=0.5에서 whipsaw 10.3pp 감소, 전환 횟수 243→182(거래비용 절감), Sharpe 거의 동등. Crisis 진입 감소(-49일, -23%)는 부작용이지만 MaxDD 악화가 -1.18pp로 통제됨. 기본값은 0.0 유지(호환), 채택 결정 시 config만 변경.

→ **사용자 채택**: `blend_smoothing_alpha=0.5` 적용.

### detect_regime 임계 시뮬레이션 (외부 비평 #6-a 분리도 검증)

- `scripts/compare_detect_thresholds.py`: 코드 변경 없이 walk-forward features로 4개 시나리오 재적용 후 분리도만 검증.
- 실험 노트: `docs/experiment_2026-05-27_detect_thresholds.md`.

**핵심 발견**:
- detect_regime **자체로는** Crisis(+3.84%)·Stagflation(+2.05%)이 가장 우월. 진단의 Crisis 후행(+0.38%) 문제는 룰이 아니라 ensemble + RegimeFilter 단계.
- Goldilocks 임계 조정(growth_min 2→3)으로 평균 +0.81→+0.93% 미세 개선, 순위 5/5 → 4/5. 분리도 본질적 회복은 아님.
- flip_fallback은 오히려 악화. very_strict는 추가 개선 없음.

**채택 보류** — 임계 조정만으로 의미 있는 회복 없음. 본질 문제(ensemble 후행)는 이미 적용된 `blend_smoothing_alpha=0.5`·Crisis 비대칭 hysteresis로 부분 처리. 더 본질적 재설계(레짐 framework 자체를 forward return quantile 기반으로 교체)는 별도 큰 작업.

### 레짐 분류 안전 fix 묶음 (외부 비평 반영)

외부 리뷰의 6개 비평 중 단독 결정 가능한 4개 항목을 한 번에 적용.
잔여 항목인 RF forward-looking label(#1)과 FRED publication lag/vintage(#3)는 별도 작업.

- **HMM 매핑 가중치 외부화**: `_unsupervised_state_mapping`의 growth/infl score 합성 가중치(curve·hy_zscore·cpi·vix·commodity)를 하드코딩에서 `hmm.mapping_weights` config로 분리. 캘리브레이션 가능.
- **Crisis 임계 통일**: HMM unsupervised의 Crisis state 식별 임계를 룰 기반 `detect_regime`과 동일한 `realized_vol >= 0.30`으로 통일 (기존 0.25). `hmm.crisis_rvol_threshold` / `crisis_rvol_ratio`로 노출.
- **레짐별 hysteresis override**: `regime_filter.per_regime`에서 레짐별 `confirmation_count`/`cooldown_days` 지정 가능. Crisis는 `confirm=1, cooldown=0`으로 빠른 진입.
- **신뢰도 폴백 변경**: confidence < threshold일 때 항상 Slowdown으로 폴백하던 동작을 "이전 확정 레짐 유지"(없으면 DEFAULT)로 교체. 강세장 초입의 체계적 편향 제거.
- **매핑 안정성·legacy 폴백 빈도 로깅**: `state.json`에 `hmm_state_to_regime`/`hmm_mapping_method`/`hmm_total_runs`/`hmm_legacy_fallback_count` 누적. 매 학습 후 매핑 변화 state 수·레짐 집합 차이를 출력하고 누적 legacy 사용 비율을 함께 표시.
- 백테스트(`backtest/engine.py`)도 동일 config 인자 전달하도록 갱신.

## 2026-05-10

### 실거래 리스크 제어 일치

- **드로우다운 스케일다운 “현금 재배분” 반영**: drawdown 구간에서 equity 축소분을 현금성 티커로 재배치해 타깃 합이 무너지는 문제를 방지.
  - KRW: `settlement.buffer_tickers`(기본 `469830`)로 이동
  - USD: `SHY`로 이동

### 백테스트 데이터 품질

- `**261220` 프록시 매핑**: KRX `261220`(원유선물) 데이터 404로 인한 공백 제거를 위해 `USO`로 프록시 매핑 (`backtest/data.py`)

### 레짐 모델 안정화

- **HMM 학습 안정성 개선**: 표준화 후 클리핑, `min_covar`로 정규화, seed 재시도 후 최선 모델 선택.
- **HMM 수렴 로그 정리**: 학습 구간에서 stderr 출력 억제(수렴 스팸 로그로 인한 가독성 저하 방지).

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