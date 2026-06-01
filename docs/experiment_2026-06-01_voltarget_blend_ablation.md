# vol targeting · blend 절제 — 두 장치가 밥값을 하는가

- **일자**: 2026-06-01
- **스크립트**: `scripts/ablate_voltarget_blend.py`
- **구간**: 2010-01-01 ~ 2025-04-30 (current config, 타이밍=rule 고정)
- **코드 변경**: 없음 (진단/시뮬레이션).

## 배경

사용자 의심: *"어떤 신호든 vol targeting·blend가 희석해버리니, 이 둘이 정말 유의미한
장치인가?"* 층 0·1·2 및 상관진단의 '노이즈' 결론이 모두 **"두 장치가 흡수한다"는 가정**
위에 서 있었으므로, 가정 자체를 검증한다. `_target_weights`만 토글하는 2×2 절제
(blend ON/OFF × vol targeting ON/OFF). blend OFF = acting regime one-hot(HMM 혼합 제거).

## 결과

| cell | CAGR | Sharpe | MaxDD | Calmar | COVID | Bear22 |
|---|---:|---:|---:|---:|---:|---:|
| **full (현행)** | 9.7% | 0.73 | **-9.9%** | **0.98** | -9.1% | **-8.2%** |
| blend_off (one-hot + vt) | 11.6% | **0.88** | -14.0% | 0.83 | -8.4% | -10.1% |
| vt_off (blend + vt off) | 11.4% | 0.78 | -12.9% | 0.88 | -12.9% | -12.7% |
| both_off (≈정적 targets) | 12.2% | 0.81 | -20.1% | 0.61 | -12.2% | -17.4% |

**델타 (cell − full)**:

| cell | ΔSharpe | ΔMaxDD | ΔCalmar | ΔCOVID | ΔBear22 |
|---|---:|---:|---:|---:|---:|
| blend_off | +0.147 | -4.11pp | -0.149 | +0.77pp | -1.89pp |
| vt_off | +0.042 | -3.08pp | -0.097 | **-3.80pp** | **-4.50pp** |
| both_off | +0.078 | **-10.19pp** | **-0.370** | -3.04pp | **-9.16pp** |

## 핵심 발견

1. **두 장치는 수익/Sharpe를 깎는다 (의심의 맞은 절반).** full이 네 칸 중 CAGR·Sharpe **최저**.
   둘 다 끄면 CAGR 9.7→12.2%·Sharpe 0.73→0.81. Sharpe/수익 극대화가 목표면 방해물.
2. **그러나 희석기가 아니라 낙폭 억제 엔진이다 (틀린 절반).** 끌수록 드로우다운 붕괴 —
   both_off **MaxDD -9.9→-20.1%**(2배), **Calmar 0.98→0.61**, Bear22 -8.2→-17.4%.
3. **둘은 다른 일을 한다 (상보적).**
   - **vol targeting = 급성 위기 방어수.** vt_off가 **COVID(-3.80pp)·Bear22(-4.50pp)** 위기창을
     특히 키움. vol 급등 시 동적 디리스킹이 위기 방어의 실제 엔진.
   - **blend = 평시 낙폭 평활.** blend_off가 Sharpe를 가장 많이 올리나(+0.147, one-hot 공격성)
     집계 MaxDD를 키움. HMM 가중 혼합이 '한 레짐 올인'을 차단. (COVID는 오히려 +0.77pp —
     blend 가치는 비-COVID 평시 낙폭에 있음.)
   - **부분적으로 독립**: both_off MaxDD(-20.1%) > 각각(blend_off -14.0% / vt_off -12.9%) →
     두 장치가 다른 실패 모드를 막아 손상이 합산적.

## 종합 결론

- **지금까지의 '노이즈' 결론은 모순이 아니라 바로 이 때문이다.** regime_targets 비중·새 피처가
  엔드투엔드로 안 먹히는 이유 = 시스템이 과평활 연극이라서가 아니라, **vol targeting(신호 무관·
  리스크 구동)이 목표(리스크 회피)의 무거운 짐을 다 지고 있어서**. 두 장치가 load-bearing이라는
  사실이 **신호 튜닝이 노이즈인 이유 그 자체** — 일관됨.
- **결정은 목표 의존적.** 프로젝트 목표 = 리스크 회피이고 full이 **MaxDD·Calmar·Bear22 전부
  최고**(Calmar 0.98 압도) → 현행이 목표에 명확히 정합. 대가는 Sharpe ~0.08·CAGR ~2.5pp.
  Calmar/MaxDD 관점에선 full이 dominant. Sharpe/CAGR가 목표라면 두 장치를 줄여야 함.

## 한계

- 단일 구간 in-sample. blend OFF는 one-hot(acting regime)으로 구현 — 다른 비중 스킴은 결과가
  다를 수 있음. drift·class cap은 켠 채 측정(이 둘만 격리). Sharpe는 상방 변동성도 벌하므로
  vol targeting의 'Sharpe 비용'은 일부 상방 클리핑(하방 방어의 대가)임에 유의.
