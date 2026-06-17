# 트레이딩 부수 기능 지도 + 상호작용 (실험 범위 설정용)

> **요약**: ① 기본 로직(레짐 감지·블렌딩·vol targeting·레짐 기반 리밸런싱)을 제외하고, 실제 매매에 영향을 주는 모든 부수 기능을 코드(`engine.py`·`portfolio.py`·`regime.py`·`run.py`·`executor.py`·`settlement.py`)·`config.yaml`에서 추출해 (A) 비중 형성, (B) 리밸런싱 트리거, (C) 실행·계좌·결제 3계층으로 리스트업했다. ② 각 기능의 현재 on/off·값·핵심 상호작용·근거 문서를 표로 정리하고, 한 기능 실험이 다른 기능에 의해 교란/희석되는 조합(예: core30이 vol·gate를 위성 70%로 희석, vol_targeting↔drawdown_scaling 이중축소, floor 실험↔리밸 모드)을 상호작용 맵에 명시했다. ③ 이 문서는 새 기능 실험 전 반드시 확인해 "함께 켜져 있어 결과를 흐릴 수 있는 기능"을 사용자에게 미리 알리고 실험 범위(고정/토글 대상)를 합의하기 위한 레퍼런스다(CLAUDE.md 규칙5).

- 최종 갱신: 2026-06-17
- 범위: **기본 엔진 로직(레짐 블렌딩·vol targeting 포함) + 모든 부수 기능**. 매매에 영향을 주는 기능 전부.
- 상태 표기: ✅켜짐 / ⛔꺼짐(코드·knob 보존) / 🔒고정값

---

## A. 비중 형성 (시그널 → 목표 비중)

엔진 적용 순서(`engine.py` `_target_weights`/`_compute_targets`):
`blend_regime_targets` → **corroboration_gate**(blend) → **vol_targeting** → **core_satellite** → **class caps(동적/정적)** → **derive_account_weights**.

### A0. 기본 엔진 로직 (핵심 2대 — 시스템의 뼈대)

| # | 기능 | 위치 | 상태/값 | 하는 일 | 핵심 상호작용 |
|---|---|---|---|---|---|
| A0-1 | **레짐 블렌딩 (blend_regime_targets)** | `portfolio.py:11` | ✅ | 단일 레짐을 고르지 않고 HMM 사후확률로 5개 레짐 목표비중을 가중평균(연속 노출). 갑작스런 전환 충격 완화 | **거의 모든 A′ 기능(A7~A18)이 이 blend를 가공.** core30(A1)이 30%를 덮고, vol(A0-2)이 그 위에서 equity 축소. 평활(A12·A13)이 이 blend를 늦춤 |
| A0-2 | **vol targeting** | `portfolio.py:168` | ✅ floor 0.65, 레짐별 목표 | 포트폴리오 EWMA 변동성이 레짐 목표(G0.13~C0.06) 초과 시 equity를 비례 축소(floor까지), 축소분 cash | **A6 drawdown_scaling과 이중축소(끈 이유).** A1 core30이 위성 70%로 희석. A4 VIX캡과 같은 위기에 동시 작동. floor 결론이 B1 리밸 모드에 의존 |
| A0-2b | **blend_target_vol (목표변동성 확률블렌드)** | `portfolio.py:198`, `config.vol_targeting.blend_target_vol` | ⛔ false(백테스트만 검증) | 목표변동성을 확정레짐 단계 선택 대신 blend 확률 가중평균(연속). target_vol=Σp·vol | A7 regime_timing_source(룰 단계)를 대체하는 경로 — 켜면 vol 단계가 룰이 아닌 blend로. A/B: 4지표 중립·tx↓·위기방어 무손상. **라이브 run.py 미배선(fails-closed)**, OFF 유지. [[experiment_2026-06-17_voltarget_blend]] |

### A1~. 부수 기능

| # | 기능 | 위치 | 상태/값 | 하는 일 | 핵심 상호작용 |
|---|---|---|---|---|---|
| A1 | **core_satellite (core30)** | `portfolio.py:58` | ✅ ratio 0.30, Goldilocks | 자산 30%를 정적 Goldilocks로 고정, 70%만 엔진 운용 | **vol_targeting·corroboration_gate·blend 효과를 위성 70%로 희석.** 코어는 vol 면제(회복 앵커) |
| A2 | **corroboration_gate (레버 C)** | `regime.py:1214`, `engine.py:240` | ⛔ gamma 0.0 | rule이 risk-on인데 HMM이 단독 방어면 방어질량 회수 | core30이 효과 희석, crisis_priority_threshold가 면제, blend에 직접 작용 |
| A3 | **class_max_weight (정적 상한)** | `portfolio.py:124` | ✅ | 자산군별 비중 상한, 초과분 cash | vol/core 적용 *후* 작동. equity_etf·bond·cash는 흡수자산이라 cap 없음 |
| A4 | **dynamic_class_caps (VIX 동적 상한)** | `portfolio.py:144` | ✅ | VIX>30 시 commodity·equity_individual 상한 50%↓, >25 시 25%↓ | A3 위에 덧씌움. VIX 신호가 vol_targeting과 같은 위기에 동시 작동 |
| A5 | **transition_phase** | `engine.py`, `regime_filter` | ⛔ days 0 | 레짐 전환 후 N일 Transition 비중(risk-off) | 켜면 blend·vol과 별개로 진입 비중 깎음(over-protection으로 보류) |
| A6 | **drawdown_scaling (risk controls)** | `portfolio.py:388`, `run.py:520` | ⛔ | DD -10/-20/-30%에 equity 75/40/10%로 축소 | **vol_targeting과 이중 축소(redundant)** — 끈 핵심 이유. 켜면 회복기간 악화 |
| A6b | **stagflation_subregime (실질금리 분기)** | `engine.py:_subregime_config` | ⛔ enabled false | 긴축형 스태그(real_rate_chg_3m≥0)면 Stagflation의 비중(tightening_targets)·vol목표(tightening_vol)·floor(tightening_floor)를 교체 | blend·vol_targeting에 작용. **세 채널 전부 OOS 개선 실패**: 비중=core30·vol흡수(experiment_2026-06-15_stagflation_subregime_oos), vol목표=floor클램프로 무효, floor=역효과(MaxDD무방어·수익훼손, experiment_2026-06-15_stagflation_voltighten_floor_oos). knob 보존·off. 진단(실질금리 분기축)은 견고하나 vol 채널로 개선 불가 |

## A′. 레짐 라벨/신뢰도 가공 (acting regime·blend를 흔드는 기능)

| # | 기능 | 위치 | 상태/값 | 하는 일 | 핵심 상호작용 |
|---|---|---|---|---|---|
| A7 | **regime_timing_source** | `regime_filter` | ✅ rule | acting regime을 rule로(앙상블보다 +3~5d 빠른 위험 진입) | vol_targeting 티어 전환 타이밍 결정. blend는 항상 HMM |
| A8 | **confirmation_count / cooldown (히스테리시스)** | `regime_filter` | 🔒 cf 1, cd 0 | 확정 레짐 전환 지연 | 확정레짐=vol 티어만, 비중은 blend가 결정 |
| A9 | **per_regime override (Crisis)** | `regime_filter.per_regime` | ✅ Crisis cf1/cd0 | 위기는 즉시 확정 | crisis_priority_threshold와 함께 위기 빠른 진입 |
| A10 | **crisis_priority_threshold** | `hmm` | ✅ 0.40 | blend[Crisis]≥0.40 즉시 Crisis + gate·smoothing 면제 | A2·A12·A13 면제 트리거 |
| A11 | **confidence_threshold + method + fallback** | `regime_filter` | ✅ thr 0.20, min | 신뢰도 미달 시 이전 확정 레짐 유지 | A12·anomaly가 신뢰도 입력 |
| A12 | **confidence_smoothing (가변 평활)** | `regime_filter` | ✅ ref 0.3 | 신뢰도 낮으면 새 blend 채택 지연(관성↑) | A13(α)·anomaly·crisis_priority와 결합 |
| A13 | **blend_smoothing_alpha (EWMA 평활)** | `regime_filter` | ✅ 0.5 | blend_probs 평활(whipsaw 억제) | A12가 이 α를 신뢰도로 가변화 |
| A14 | **anomaly (IsolationForest)** | `anomaly` | ✅ penalty 0.5 | 이상시장 → 신뢰도 하향 | A11·A12 입력(독립신호, detect_regime 자기참조 없음) |
| A15 | **stabilize_mapping + deadband** | `hmm` | ✅ db 0.3 | HMM state→regime 라벨 고정(가짜 플립 차단) | 레짐 안정성 → 리밸 빈도·blend에 간접 영향 |
| A16 | **override_threshold** | `hmm` | ✅ 0.50 | HMM/RF 다수결 채택 속도 | 레짐 결정 속도 |
| A17 | **RF 앙상블 (rf_weight/label_mode/forward_window)** | `hmm` | ✅ w0.40, rule_at_future, fw0 | HMM 0.6 + RF 0.4 확률 블렌딩 | blend_probs 자체를 바꿈. fw>0이면 forward-looking(자기참조 끊김) |
| A18 | **min_covar / use_forward_hmm** | `hmm` | ✅ 1e-3 / ⛔ | HMM 방출 뾰족함(포화) / 1-step 예측 | min_covar↑면 포화 완화 → blend 분산 |
| A19 | **feature_smoothing (노이즈 피처 5일 평활)** | `features.py` `compute_features`/`compute_feature_matrix`, `config.feature_smoothing` | ✅ window 5, 6피처 | HMM 입력 빠른 시장 피처(vix_term_structure·vix·credit_signal·momentum_1m·commodity_mom_1m·dxy_mom_1m)를 5일 rolling 평균으로 평활 → 일별 표류↓ | **HMM 입력 자체를 늦춤** → blend·acting regime·vol 티어 전부 간접 영향. 백테스트는 리밸일만 재계산이라 회전 감소 미반영(본 효과는 라이브). [[experiment_2026-06-17_noise_smoothing_seed_fix]] |
| A20 | **hmm.fit_seeds (fit 시드 고정)** | `regime.py` `HmmRegimeClassifier`, `config.hmm.fit_seeds` | ✅ [42] | 매일 비지도 재학습 시 사용할 시드 목록. 단일 [42]면 학습이 결정적 → 센트로이드 점프·앵커 매핑 흔들림 제거. None이면 [42,7,13] 다중→최고점수 | **A15 stabilize_mapping(앵커)과 직결**: 다중 시드면 일별 승자 교체로 센트로이드 점프 → 앵커 매핑 실패. 단일 시드가 그 churn 근원 제거 |

## B. 리밸런싱 트리거 (언제 매매할지)

`run.py:_compute_trigger` 우선순위: ①DD비상 → ②지연매수 → ③쿨다운 → ④레짐전환 → ⑤drift.

| # | 기능 | 위치 | 상태/값 | 하는 일 | 핵심 상호작용 |
|---|---|---|---|---|---|
| B1 | **drift_threshold + 계좌별 side drift** | `run.py:108,150` | ✅ 1.5% | 계좌 내 drift>1.5%p면 트리거 | **백테스트도 이 모드여야 함**(엔진 `_run_drift` vs `_run_calendar`). floor·vol 결론이 모드에 의존([[feedback-backtest-rebal-mode-drift]]) |
| B2 | **min_rebalance_interval_days (쿨다운)** | `rebalancing` | 🔒 0 | 매매 쿨다운 | 0이면 drift>임계 즉시. DD비상·지연매수는 무시 |
| B3 | **drawdown_emergency 트리거** | `run.py:127` | ✅ | DD≤moderate(-20%)면 쿨다운 무시 즉시 | drawdown_scaling(A6, off)과 별개로 *트리거*는 살아있음 |
| B4 | **deferred_buys 트리거** | `run.py:130` | ✅ | 미처리 T+2 매수 있으면 즉시 | C4와 짝 |
| B5 | **regime_change_trigger** | `rebalancing` | ⛔ false | 레짐변경 시 drift 우회 강제리밸 | drift가 이미 포섭(redundant) |
| B6 | **per_ticker_drift_threshold** | `rebalancing` | 🔒 0 | 개별종목 이탈 임계 | min_order_krw(C8)가 잔주문 필터 대체 |
| B7 | **max_run/max_monthly_turnover** | `rebalancing` | ⛔ 0 | 회전율 상한 | 라이브 과회전 억제용(6월 0.30 복구 예정) |

## C. 실행·계좌·결제 (어떻게 체결할지)

| # | 기능 | 위치 | 상태/값 | 하는 일 | 핵심 상호작용 |
|---|---|---|---|---|---|
| C1 | **USD/KRW 분리 + USD 배정 waterfall** | `portfolio.py:235` | ✅ | USD 한도 내 우선순위(commodity·MF→USD equity→intl→bond_usd→cash_usd) 배정, 부족분 equity_etf·bond_krw로 KRW 대체 | **USD 한도가 목표 비중을 왜곡**. 라이브 합성 순환매·과회전 주범([[project-live-turnover-vs-backtest-gap]]) |
| C2 | **usd_cash_min / krw_cash_min** | `rebalancing` | 🔒 1% | 계좌별 최소현금 reserve | waterfall·정규화에 반영 |
| C3 | **T+2 synthetic reallocation** | `portfolio.py:502`, `run.py:569` | ✅ | 지연 USD매수를 KRW 동등자산(synthetic_pairs)으로 임시 노출 | C4와 짝. 라이브 합성 순환매 원인 |
| C4 | **deferred_buys / SettlementTracker** | `settlement.py`, `executor.py` | ✅ | T+2 미결제로 못 산 매수 이연 | B4 트리거·C3 합성과 연동 |
| C5 | **buffer_floor** | `portfolio.py:473` | ⛔ 빈 리스트 | 버퍼자산 최소비중 강제 | 모든 레짐 cash≥0.08이라 redundant |
| C6 | **orderable cap (KRW/USD)** | `executor.py:952,1000` | ✅ | 브로커 주문가능액 초과 매수를 비례 축소 | USD 한도(C1)와 함께 실제 체결 제약 |
| C7 | **illiquid_order_handling** | `executor.py:283` | ✅ 468370 | 얇은 종목 주문 분할·재시도·가격추격 | 신규 KRW-native 종목 추가 시 확장 필요([[feedback-illiquid-fix-over-swap]]) |
| C8 | **min_order_krw** | `rebalancing` | 🔒 1만원 | 잔주문 필터 | B6 대체 |
| C9 | **order_throttle_s** | `rebalancing` | 🔒 0.25s | 주문 간 대기(KIS rate limit) | — |
| C10 | **sell-first-then-buy + orphan 매도** | `executor.py:656,1085` | ✅ | 매도로 현금 확보 후 매수, 유니버스 외 보유 청산 | orphan은 drift/target에서 제외 |
| C11 | **_correct_peak_for_io** | `executor.py:369` | ✅ | 입출금을 drawdown peak 계산에서 보정 | 입금이 DD를 가짜 리셋하는 것 방지 → B3·A6 트리거 정확도 |
| C12 | **_adjust_tick** | `executor.py:238` | ✅ | 호가단위 가격 반올림 | — |

---

## 상호작용 맵 — 실험 시 함께 고려해야 할 조합

한 기능을 실험할 때 **아래 짝이 동시에 켜져 있으면 결과가 교란/희석**된다. 실험 전 사용자에게
"이 기능들을 고정할지/함께 토글할지" 확인하고 범위를 합의한다.

| 실험 대상 | 함께 봐야 할 기능 | 이유 (교란 방식) |
|---|---|---|
| vol_targeting / floor / target_vol | **B1 리밸 모드**, A1 core30, A6 drawdown_scaling | floor 결론이 drift vs calendar 리밸에서 뒤집힘([[feedback-backtest-rebal-mode-drift]]). core30이 vol을 70%로 희석. drawdown_scaling과 이중축소 |
| corroboration_gate (A2) | A1 core30, A10 crisis_priority, A12/A13 평활 | core30 희석 + crisis_priority가 게이트 면제. 평활이 blend를 늦춰 게이트 입력 변화 |
| regime_targets 비중 | A1 core30, vol_targeting | core30이 30%를 Goldilocks로 덮어 per-regime 비중 효과 축소([[feedback-regime-targets-no-tuning.md]]) |
| 신뢰도/평활 (A11~A14) | 서로 + A10 crisis_priority | confidence·anomaly·두 평활이 한 신뢰도 파이프라인을 공유. crisis_priority가 전부 면제 |
| 리밸 트리거 (B1·B2·B5) | C1 USD waterfall, B7 turnover cap | 백테스트 회전 ≠ 라이브 회전(USD합성). drift 줄여도 라이브 과회전 안 줄수도([[project-live-turnover-vs-backtest-gap]]) |
| 유니버스 종목 추가/교체 | C1 waterfall, C7 illiquid, A3/A4 caps, asset_routing | KRW/USD 라우팅·유동성·상한·합성쌍(synthetic_pairs) 동시 갱신 필요 |
| drawdown_scaling 재가동 (A6) | vol_targeting, B3 DD트리거 | vol과 이중축소(끈 이유). B3 트리거는 별개로 유지 중 |
| 노이즈 평활/시드 (A19·A20) | A12/A13 평활, A15 앵커, B1 리밸 모드 | A19는 HMM *입력*을 늦추고 A12/A13은 *출력*(blend)을 늦춤 — 회전 감소 효과가 겹쳐 귀속 모호. A20은 A15 앵커 안정성과 직결. 백테스트는 회전 효과 미반영(라이브 모니터링 필요) |

## 백테스트 충실도 체크리스트 (실험 코드 작성 시)

- [ ] `BacktestEngine`에 `drift_threshold`를 config에서 읽어 전달했는가? (안 하면 캘린더 리밸 → 라이브와 다름)
- [ ] `cooldown_days`도 config(`min_rebalance_interval_days`)에서 읽었는가?
- [ ] core_satellite·vol_targeting·평활 등 현행 켜짐 기능을 의도대로 고정/토글했는가?
- [ ] USD 단일통화 백테스트 한계 — 라이브 합성 순환매·실제 회전 미반영을 결론에 명시했는가?
- [ ] **vol targeting `use_portfolio_vol` 경로 死(2026-06-16 발견, 2026-06-17 정정)**: 백테스트·**라이브 양쪽 모두** vol 계산에 신호 티커 가격(SPY·^VIX·TLT·HYG·DX-Y·DJP)을 넘기는데 가중치는 유니버스 티커 키 → 교집합 0(`set(signal.tickers)&set(universe)=∅`) → `port_vol=0` → SPY `realized_vol`(features.py 고정 λ=0.94) 폴백. **∴ config `ewma_lambda`와 '실포트 vol 사용'은 라이브·백테스트 모두에서 死 손잡이이며 둘 사이 괴리는 없음**(이전 '괴리' 서술 철회). vol 반응속도 실험은 양쪽에 유니버스 가격을 주입해 경로를 살린 뒤에야 유효(experiment_2026-06-16_voltarget_lambda_sweep).
- [ ] 평가는 고정 4지표(롤링CAGR·Ulcer·회복기간·Martin)로 했는가?([[feedback-evaluation-metrics-standard]])
