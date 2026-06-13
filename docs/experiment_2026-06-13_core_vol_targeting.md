# 실험: 코어(30% Goldilocks)에도 vol targeting을 적용할까 — 현행 시스템 + 고정 4지표

> **요약**: ① 현행 core+satellite는 vol targeting을 위성 70%에만 적용하고 코어 30%(정적 Goldilocks)는 풀 equity로 두는데(회복 앵커), "코어 equity도 고변동 구간에 깎으면 방어가 더 낫지 않나"를 검증하려고 A(현행, 코어 면제) vs B(core 혼합 후 결합 포트폴리오 전체에 vol) vs vol OFF를 floor 0.65에서 스윕했다. ② B는 낙폭 *깊이*를 확실히 줄였으나(MaxDD -12.8 vs -14.9%, COVID -12.7 vs -14.9%, Bear22 -10.3 vs -12.2%, Ulcer 3.08 vs 3.41) 그 대가로 최장 underwater 409→523일(+114일)·Martin 2.54→2.49·롤3y중앙 11.5→10.5%·CAGR 12.6→11.7%로 악화해, 4지표 1차 기준(Martin·롤링CAGR·회복기간)에서 A가 3/4 우위였다(B는 Ulcer만 우위). ③ 코어 vol 면제 유지(현행 A) — 정적 코어는 회복 구간 풀참여 앵커이고, 깎으면 remetric이 발견한 "깊이↔길이 트레이드오프"대로 깊이는 줄지만 underwater가 길어져 회복기간·Martin 가중 기준에서 순손해. 단 우선순위가 *낙폭 깊이*라면 B가 유리(위기 -2pp).

- 날짜: 2026-06-13
- 코드: `scripts/experiment_core_vol_targeting.py` (라이브/엔진/config 변경 없음 — 진단)
- 기간: 2010-01-01 ~ 2025-04-30, USD 단일통화, 현행 config(core30 enabled, floor 0.65) 로드

## 동기

사용자 질문: core+satellite에서 vol targeting은 위성 70%에만 작용하고 코어 30%는 풀 equity로
남는다(`engine.py:332→333` 순서: vol(위성 blend) → core 혼합). 코어 equity도 고변동 구간에
깎으면 하락 방어가 더 좋아지지 않을까?

## 방법

`CoreVolEngine`(BacktestEngine 서브클래스)에서 `_target_weights`의 두 줄 순서만 토글:

- **A 현행(코어 면제)**: `vol_targeting(위성 blend)` → `core_satellite 혼합`. 코어 equity 안 깎임.
- **B 코어도 깎기**: `core_satellite 혼합` → `vol_targeting(결합 포트폴리오)`. eff_vol도 결합
  포트폴리오 기준 재측정. 코어 Goldilocks equity도 같은 레짐 target_vol·floor로 축소.
- (참고) vol OFF.

floor는 현행 0.65 고정. 나머지 config·기간·universe 동일.

## 결과

| 전략 | 롤3y최악 | 롤3y중앙 | 롤5y최악 | Ulcer | 회복일 | 최장UW | **Martin** | CAGR | MaxDD | 리밸 | tx | COVID | Bear22 |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| **A 현행(코어 면제)** | 4.8% | 11.5% | 5.3% | 3.41 | 82 | **409** | **2.54** | 12.6% | -14.9% | 651 | 3.14% | -14.9% | -12.2% |
| B 코어도 깎기 | 4.3% | 10.5% | 4.6% | **3.08** | 84 | 523 | 2.49 | 11.7% | **-12.8%** | 602 | 3.54% | -12.7% | -10.3% |
| vol OFF | 5.4% | 12.2% | 6.1% | 3.80 | 110 | 417 | 2.44 | 13.2% | -17.6% | 650 | 2.22% | -17.6% | -14.2% |

## 해석

### 판정 — A가 4지표 3/4 우위
- **Martin(1차)**: A 2.54 > B 2.49.
- **롤링 CAGR**: 롤3y최악 4.8 vs 4.3, 중앙 11.5 vs 10.5, 롤5y최악 5.3 vs 4.6 — 전부 A 우위.
  코어까지 깎으면 최악 진입시점 수익도 같이 낮아진다.
- **회복기간**: 회복일 82≈84(무승부), 최장UW **409 vs 523**(+114일) — A 명확 우위.
- **Ulcer**: B 3.08 < A 3.41 — B 우위(유일).

### 깊이 ↔ 길이 트레이드오프 (remetric 발견의 재현)
B는 낙폭 *깊이*를 확실히 줄인다: MaxDD -12.8 vs -14.9%, COVID -12.7 vs -14.9%, Bear22
-10.3 vs -12.2%. 그러나 정적 코어(Goldilocks 풀 equity)는 **반등 구간에 풀 참여하는 회복
앵커**인데, 이를 vol로 깎으면 회복 참여가 줄어 **물려있는 기간이 길어진다**(UW +114일) +
CAGR·롤3y중앙 하락. 이는 `experiment_2026-06-12_remetric_high_priority`가 vol floor·drawdown
scaling에서 일관되게 본 "equity를 깊이/오래 축소 → 깊이↓ 길이↑"와 동일 메커니즘이다.
**core30의 vol 면제는 이 비용에 대한 의도된 구조적 답**이다.

### 기준 의존성 (중요)
CLAUDE.md 규칙4가 회복기간·Martin을 1차로 가중하므로(3~5년 장기보유자 = underwater 길이가
체감 고통) A가 이긴다. **만약 우선순위가 낙폭 *깊이*였다면 B가 이긴다** — B는 위기 낙폭을
약 2pp 줄인다. 결론은 평가 기준에 직접 의존한다.

## 결론

- **코어 vol 면제 유지(현행 A).** config 변경 없음. 코어를 깎으면 깊이는 줄지만 4지표 1차
  기준(회복기간·Martin·롤링CAGR)에서 순손해.
- 본 실험은 "core30이 왜 vol을 면제받는가"의 사후 정당화이기도 하다 — 코어는 깊이 방어
  장치가 아니라 회복 참여 앵커다. 깊이 방어는 위성 vol targeting + blend가 담당.

## 한계

- in-sample·USD 단일통화. B의 tx 3.14→3.54%(회전 +0.40%p)는 부차적이나, 라이브에선 코어까지
  vol로 흔들면 합성 회전이 더 늘 수 있음([[project-live-turnover-vs-backtest-gap]]).
- eff_vol을 B에서 결합 포트폴리오 기준으로 재측정했으므로 A 대비 scale이 미세하게 다를 수
  있으나, 결론(깊이↔길이 트레이드오프)을 바꿀 크기는 아니다.
