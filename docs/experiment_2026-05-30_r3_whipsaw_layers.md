# R3 — whipsaw 억제 레이어 단순화 (엔진 이식 후 그리드 스윕)

- **일자**: 2026-05-30
- **스크립트**: `scripts/compare_r3_whipsaw.py` + `backtest/engine.py::_run_triggered`
- **구간**: 2010-01-01 ~ 2025-04-30 (W-FRI cadence, tx_cost 0.1%, drawdown OFF, floor 0.50)

## 배경 / 동기

라이브 시스템에는 whipsaw 억제 장치가 세 경로에 흩어져 있다:

- **경로 A (노출 비중)**: blend_smoothing(α=0.5) + 연속 블렌딩 — `blend_probs`에서 자산군 비중 산출
- **경로 B (확정 레짐 라벨)**: confidence fallback + confirmation_count 히스테리시스
- **경로 C (매매 트리거)**: drift_threshold + regime_changed 강제 트리거

핵심 관찰: 자산군 비중은 `blend_probs`가 결정하고(run.py:601), **확정 레짐 라벨은 vol_targeting
티어 선택 + regime_changed 트리거에만** 쓰인다. 그런데 이 라벨 하나를 두 겹(fallback +
confirmation)으로 누르고, regime_changed가 drift 밴드를 우회한다. redundancy 의심.

## 엔진 이식 (선행 작업)

기존 백테스트 엔진은 경로 B·C 레이어를 **모델링하지 않았다**:
- `_run_drift`의 트리거는 emergency OR (drift>thr AND cooldown)뿐 — regime_changed 없음
- `_get_regime`은 ensemble_regime을 바로 반환 — confirmation 히스테리시스·confidence fallback 미적용

→ `_run_triggered`를 신설해 라이브 run.py 트리거 경로를 충실히 재현:
주간 cadence로 레짐 평가 → confidence fallback → 날짜 인지 confirmation 히스테리시스
→ regime_changed 트리거(토글) + drift 트리거. 기존 `_run_calendar`/`_run_drift`는 무손상.

토글: `regime_filter.confirmation_count`, `regime_filter.confidence_threshold`(0=fallback off),
`rebalancing.regime_change_trigger`(false=강제 트리거 제거).

## 결과 (2×2×2 그리드)

rc = regime_change_trigger, cf = confirmation_count, fb = confidence fallback

| variant       | rc  | cf | fb  | Sharpe | MaxDD   | Calmar | Vol   | CAGR  | 리밸 | tx    |
|---------------|-----|----|-----|-------:|--------:|-------:|------:|------:|----:|------:|
| **base*** (현재) | on  | 2  | on  | 0.713  | -11.35% | 0.845  | 7.84% | 9.59% | 718 | 5.37% |
| rc_off        | off | 2  | on  | 0.713  | -11.35% | 0.845  | 7.84% | 9.59% | 716 | 5.37% |
| cf1           | on  | 1  | on  | 0.753  | -10.24% | 0.964  | 7.80% | 9.87% | 714 | 5.59% |
| fb_off        | on  | 2  | off | 0.724  | -11.36% | 0.853  | 7.85% | 9.69% | 719 | 5.44% |
| rc_off+cf1    | off | 1  | on  | 0.753  | -10.24% | 0.964  | 7.80% | 9.87% | 714 | 5.59% |
| rc_off+fb_off | off | 2  | off | 0.725  | -11.36% | 0.853  | 7.85% | 9.69% | 717 | 5.44% |
| cf1+fb_off    | on  | 1  | off | 0.754  | -9.72%  | 1.016  | 7.79% | 9.88% | 719 | 5.89% |
| **all_off**   | off | 1  | off | 0.754  | -9.72%  | 1.016  | 7.79% | 9.88% | 719 | 5.89% |

(*현재 config)

## 해석 (레버별 분해)

### H1 — regime_change_trigger: 완전 redundant ✅
rc만 다른 모든 쌍이 사실상 동일:
- base* ↔ rc_off: 지표 전부 동일, 리밸 718↔716
- cf1 ↔ rc_off+cf1: **완전 일치** (0.753 / -10.24% / 0.964 / 714)
- cf1+fb_off ↔ all_off: **완전 일치** (0.754 / -9.72% / 1.016 / 719)

drift 밴드가 주당 ~90%(718/796주) 발동하므로, 레짐 변화가 비중을 의미 있게 움직이면 drift가
이미 트리거한다. regime_changed 강제 트리거는 **순수 redundant** — 제거해도 Sharpe Δ≤0.001,
리밸 ≤2회 차이. **백테스트 기준 안전하게 제거 가능.**

### H3 — confirmation_count 2→1: 오히려 개선
cf만 다른 쌍:
- base*(cf2) 0.713 → cf1 0.753 (Sharpe +0.040, MaxDD -11.35%→-10.24%, Calmar +0.119)
- fb_off(cf2) 0.724 → cf1+fb_off 0.754 (Sharpe +0.030, MaxDD -11.36%→-9.72%)

히스테리시스(2회 확정)가 vol_targeting의 방어 티어(Crisis 0.06 / Stagflation 0.08) 전환을
지연시켜 위험 구간 초입에서 equity를 늦게 줄인다. confirm=1이면 vol 티어가 더 빨리 반응 →
MaxDD 개선. cf=1이 **in-sample 우위**.

### H2 — confidence fallback 제거: 중립~소폭 개선
fb만 다른 쌍: base* 0.713 → fb_off 0.724, cf1 0.753 → cf1+fb_off 0.754(MaxDD -10.24%→-9.72%).
제거 시 소폭 개선 또는 무차별.

### 종합
- **현재 config(base*, cf2+fb on)는 layer-free 대비 Sharpe 0.713 vs 0.754, MaxDD -11.35% vs
  -9.72%로 오히려 손해.** 경로 B 히스테리시스가 vol 티어 반응을 늦춰 risk timing을 악화.
- 최적 `all_off` = vol 스윕 floor_0.50 sweet spot(Sharpe 0.754, Calmar ~1.016)과 정확히 일치 —
  경로 B 레이어를 최소화하면 확정 레짐이 blend를 잘 추종해 calendar baseline 성능을 회복.

## ⚠ 백테스트의 한계 (live-only 신호)

- **anomaly 패널티 미모델링**: `_get_regime`의 combined_conf는 anomaly penalty를 **제외**한다
  (engine.py:152). confidence fallback의 실제 라이브 역할은 **anomaly 스파이크로 신뢰도가 급락할
  때 이전 확정 레짐을 유지**하는 것([[trading-review-queue]] G 항목)인데, 백테스트는 이 경로를
  못 본다. → fb 제거의 in-sample 이득은 fallback의 진짜 방어 가치를 과소평가했을 수 있다.
- **일별 노이즈 미반영**: 백테스트는 주간 cadence라 confirm=1의 라이브 flip-flop 위험을 과소평가.
  단 확정 레짐은 vol 티어만 건드리고 비중은 blend_probs(평활)가 결정하므로 영향은 제한적.

## 권장 / 결정 (2026-05-30, 사용자 H1+H3 채택)

- **H1 (regime_change_trigger 제거)** ✅ 적용. 백테스트 redundant + 논리적으로 drift가 포섭.
  `rebalancing.regime_change_trigger: false` + run.py:137 게이팅.
- **H3 (confirmation_count 2→1)** ✅ 적용. in-sample Sharpe +0.04·MaxDD +1.1pp 개선.
  확정 레짐은 vol 티어만 건드리고 비중은 blend_probs(평활)가 결정하므로 flip-flop 위험 제한적.
- **H2 (confidence fallback 제거)** ⏸ 보류. in-sample 우위는 있으나 백테스트가 anomaly 패널티를
  모델링하지 않아 fallback의 진짜 방어 가치(G 항목)를 과소평가. fallback 유지, 라이브 데이터
  누적 후 재조정.
