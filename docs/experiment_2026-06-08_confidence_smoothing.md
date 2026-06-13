# 실험: 신뢰도 가변 blend 평활 (confidence_smoothing)

> **요약**: ① 신뢰도가 낮을 때(anomaly 스파이크 등) 새 blend 채택 속도를 늦추는 가변 EWMA α(신뢰도 비례)를 구현하고 conf_ref 0.2~0.5 스윕으로 검증했다. ② ref=0.3에서 tx 누적 4.57%→4.04%(12% 감소)를 Sharpe +0.04·Calmar +0.08·MaxDD -0.5pp 개선과 함께 달성했으며, 회전 건수가 아닌 건당 회전 폭이 줄어 사용자의 "빠른 회전" 우려를 정량적으로 완화했다. ③ ref=0.3이 sweet spot으로 채택 후보(옵트인 기본값 off) — 위기 방어 약화는 노이즈 범위이나 차이 폭이 작아 과대 해석을 금하며, 라이브 채택 여부는 사용자 결정이다.

- 날짜: 2026-06-08
- 코드: `apply_blend_smoothing` (trading/regime.py), run.py·engine.py 배선, config `regime_filter.confidence_smoothing`
- 스윕: `scripts/sweep_confidence_smoothing.py`

## 동기

기존 시스템은 **신뢰도가 blend 비중을 전혀 조절하지 않았다.** 신뢰도(`combined_conf`)는
확정 레짐 라벨을 동결하는 게이트(run.py)와 vol-targeting 티어·표시용으로만 쓰였고,
실제 자산 비중을 결정하는 `blend_probs`는 신뢰도와 무관하게 그대로 채택됐다.

→ 2026-06-08 아침처럼 신뢰도 16%(anomaly 86%)·HMM이 Slowdown 100%로 급변한 상황에서
   blend가 즉시 65/35로 회전하며 지난주 매도한 채권(305080)을 되사는 값비싼 왕복 거래 발생.
   사용자 우려: "회전이 너무 빨라서 무섭다." (거래 상한은 시스템을 망가뜨린다고 거부)

## 메커니즘

저신뢰 레짐 전환 시 새 blend 채택 속도를 늦춘다 (가변 EWMA α):

```
eff_α = 1 - (1 - blend_smoothing_alpha) · clip(confidence / conf_ref, 0, 1)
new_blend = eff_α · prev_blend + (1 - eff_α) · raw_blend
```

- 신뢰도 낮음 → α↑ (관성↑, 새 레짐 채택↓ = 가짜 플립 억제)
- 신뢰도 높음(≥ conf_ref) → α = blend_smoothing_alpha (기존 고정 평활)
- 신뢰도 = `compute_combined_confidence(rule_conf, raw_blend[rule_regime], method) × (1 - anomaly_penalty·anomaly_score)`
  — **평활 전 raw blend + rule_regime 기준**, anomaly 패널티 포함 (라이브/백테스트 동일 산식)
- **Crisis 면제**: `raw_blend[Crisis] ≥ crisis_priority_threshold(0.40)`이면 감쇠 건너뛰고
  고정 α 사용 → 위기 빠른 진입 보존

검증(2026-06-08 conf≈0.16, conf_ref=0.4): conf_norm=0.4 → eff_α=0.8 → 채택 0.2.
Goldilocks 0.83 / Slowdown 0.17 (실제 65/35 대비 회전 대폭 완화). Crisis blend 高이면 우회 확인.

## 결과 (2010-01 ~ 2025-04, drift 1.5%, α=0.5, timing=rule, conf_method=min)

| cell | CAGR | Sharpe | MaxDD | Calmar | 리밸 | tx누적 | COVID | Bear22 |
|---|---|---|---|---|---|---|---|---|
| **off** (기준선) | 10.1% | 0.80 | -9.9% | 1.02 | 534 | 4.57% | -9.2% | -8.2% |
| on/ref=0.2 | 10.5% | 0.84 | -9.5% | 1.10 | 540 | 4.21% | -9.5% | -8.3% |
| **on/ref=0.3** | 10.4% | 0.84 | **-9.4%** | **1.10** | 542 | 4.04% | -9.4% | -8.5% |
| on/ref=0.4 | 10.2% | 0.82 | -9.7% | 1.05 | 540 | 3.79% | -9.7% | -8.7% |
| on/ref=0.5 | 10.2% | 0.82 | -9.9% | 1.03 | 540 | 3.68% | -9.9% | -8.8% |

## 해석

- **위험조정 비열위 (소폭 개선)**: 모든 on 셀이 기준선 이상. ref=0.2~0.3에서 Sharpe +0.04,
  Calmar +0.08, 전기간 MaxDD -9.9→-9.4% (+0.5pp), CAGR +0.3~0.4pp.
- **회전 폭 완화 (핵심 목표 달성)**: 리밸 횟수는 거의 동일(534→540~542)인데 tx 누적은
  단조 감소(4.57→3.68%). **회전 건수가 아니라 건당 회전 폭이 줄었다** — 큰 급회전을 억제하는
  의도와 정확히 일치. conf_ref↑일수록 감쇠 강해져 비용 더 감소.
- **위기 방어 (미세 약화, 노이즈 범위)**: COVID/Bear22 윈도 낙폭이 conf_ref↑일수록 0.2~0.6pp
  깊어짐. 원인: 위기 디리스킹의 상당 부분이 Slowdown/Stagflation에서 나오는데 이들은 Crisis
  면제(blend[Crisis]≥0.40) 대상이 아니라 함께 감쇠됨. 단 ref=0.2~0.3에서는 -0.2~-0.3pp로
  미미하고, **전기간 MaxDD는 오히려 개선**되어 실질 영향 작음.

## 결론

ref=0.3이 스위트스폿: 회전 비용 -12%(4.57→4.04%)를 위험조정 손실 없이(오히려 Sharpe/Calmar/
MaxDD 개선) 달성, 위기 방어 약화는 노이즈 범위. 사용자의 "빠른 회전" 우려를 정량적으로 완화.

차이 폭 자체는 작아(per-regime 미세튜닝 노이즈와 유사 수준) 과대 해석은 금물. 다만 기준선
대비 **무비용 + 명확한 회전 완화**라는 방향성은 일관됨. config는 옵트인(enabled: false) 유지 —
라이브 채택 여부는 사용자 결정. 채택 시 권장값 `conf_ref: 0.3`.
