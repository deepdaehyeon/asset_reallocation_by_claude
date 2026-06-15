# 레짐 발생확률 + 전이확률 — 현재 detect_regime 기준 재계산

## SUMMARY
- **무엇을**: 현재 라이브 config·`detect_regime`으로 2010-01~2025-04 일별 레짐을 분류해 ① 발생확률(시간 점유율), ② 전이확률(self 포함 하루단위 마르코프 + self 제외 전환사건)을 재계산. 기존 `docs/regime_transition_matrix_2026-05-28.md`(6d86b79 시점)를 최신 코드로 갱신. `scripts/regime_transition_probs.py`.
- **핵심 수치**: 발생확률 — Goldilocks **59.3%** / Slowdown 25.3% / Reflation 6.2% / Crisis 4.8% / Stagflation 4.4%(3725일·14.8년). 하루 지속확률(대각선) — Goldi 91.8%·Crisis 87.6%·Slow 78.8%·Refl 76.6%·Stag 63.2%. 전환 시 행선지(self 제외) — Goldi→Slow 76.9%, Slow→Goldi 71.0%(둘 사이 진동), Stag→Slow 73.3%, Crisis→Stag 40.9%·Crisis→Refl 31.8%. 평균 연속일수 Goldi 12.1·Crisis 7.8·Slow 4.7·Refl 4.3·Stag 2.7일.
- **채택 여부·결론**: 진단/참고용(처방 아님). ① 시장의 60%는 골디락스, 4분의 1은 둔화 — 위험-온 레짐이 시간 대부분. ② **Goldilocks↔Slowdown 진동이 전체 전환의 핵심**(서로 71~77%). ③ Stagflation은 가장 불안정(평균 2.7일·지속 63%)해 다른 레짐으로 빨리 빠짐 — 비중 아닌 *구조* 개선 대상([[project-stagflation-subregime-todo]])과 정합. ④ 구 문서와 차이: Crisis→Reflation이 0%→31.8%로 바뀜(파이프라인·평활 변화로 라벨 달라짐). raw rule 레짐이라 라이브 RegimeFilter·blend 평활 적용 시 전환 빈도는 더 낮음.

## 발생확률 (시간 점유율) — 전체 3725일, 14.8년
| 레짐 | 일수 | 발생확률 | 평균연속 | 진입횟수 |
|---|---:|---:|---:|---:|
| Goldilocks | 2210 | 59.3% | 12.1일 | 182 |
| Slowdown | 942 | 25.3% | 4.7일 | 200 |
| Reflation | 231 | 6.2% | 4.3일 | 54 |
| Crisis | 179 | 4.8% | 7.8일 | 23 |
| Stagflation | 163 | 4.4% | 2.7일 | 60 |

## 전이확률 (a) self 포함 — P(내일=j | 오늘=i), 대각선=하루 지속확률
| from\to | Goldi | Refl | Slow | Stag | Crisis |
|---|---:|---:|---:|---:|---:|
| **Goldi** | **91.8%** | 1.6% | 6.3% | 0.2% | 0.0% |
| **Refl** | 14.7% | **76.6%** | 5.2% | 0.4% | 3.0% |
| **Slow** | 15.1% | 1.1% | **78.8%** | 4.8% | 0.3% |
| **Stag** | 1.8% | 0.6% | 27.0% | **63.2%** | 7.4% |
| **Crisis** | 1.7% | 3.9% | 1.7% | 5.1% | **87.6%** |

## 전이확률 (b) self 제외 — 레짐이 바뀔 때 어디로 가나
| from\to | Goldi | Refl | Slow | Stag | Crisis |
|---|---:|---:|---:|---:|---:|
| **Goldi** | 0.0% | 19.8% | **76.9%** | 2.7% | 0.5% |
| **Refl** | **63.0%** | 0.0% | 22.2% | 1.9% | 13.0% |
| **Slow** | **71.0%** | 5.0% | 0.0% | 22.5% | 1.5% |
| **Stag** | 5.0% | 1.7% | **73.3%** | 0.0% | 20.0% |
| **Crisis** | 13.6% | 31.8% | 13.6% | **40.9%** | 0.0% |

## 해석
- **Goldilocks 지배**: 시간의 59%·하루 지속 91.8%. 위험-온이 시장의 정상상태.
- **Goldi↔Slow 진동축**: 전환 시 Goldi→Slow 76.9%, Slow→Goldi 71.0% — 강세장↔둔화 왕복이 전환의 대부분. 두 레짐 합산 시간 점유율 84.6%.
- **Stagflation transitory**: 평균 2.7일(최단)·하루 지속 63.2%(최저), 전환 시 73.3%가 Slowdown으로. 짧게 스쳐가는 통과 상태 — 고정 비중으로 잡기 어려움, 하위국면 분기 필요([[project-stagflation-subregime-todo]]).
- **Crisis 경로**: 진입은 주로 Stagflation(self 제외 Stag→Crisis 20%)·Reflation(13%)에서. 탈출 시 Stag 40.9%·Refl 31.8%. 구 문서의 "Crisis→Reflation 0%"는 현재 코드에서 31.8%로 바뀜 — V자 반등 경로가 현 분류기에선 존재.

## 한계
- rule **raw** 레짐(일별 detect_regime). 라이브는 RegimeFilter(연속확인·쿨다운)+blend 평활 적용 → 실제 전환 빈도는 더 낮고 self-지속 확률은 더 높음.
- 일단위 전이라 경계 라벨 노이즈가 self-transition을 부풀릴 수 있음(그래서 self 포함/제외 둘 다 표시).
- 소표본 레짐(Stagflation 163·Crisis 179일) 전이확률 추정 불안정. in-sample 전체기간 단일 경로.
- 발생확률 = 경험적 시간점유율(stationary 근사)로, 표본 외 미래확률 보장 아님.
