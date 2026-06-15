# 사용자 레짐별 비중 재설계 OOS — 학습창 +0.22 / 검증창 −0.92 (전형적 과적합, 미채택)

## SUMMARY
- **무엇을**: 사용자가 발생확률(Goldi 59%·Slow 25%)을 고려해 레짐별 핵심 비중을 직접 재설계(Goldi eqETF40·eqFac10·gold5·cash5 / Refl comm15·eqFac12·eqSec10·MF5 / Slow bond25·gold10·tips10 / Stag gold14·bond14·tips10·comm12 / Crisis bond25·cash20·gold10). 적은 값은 고정, 나머지는 드롭한 자산들의 현재 비중을 비례 유지(option 3)해 채움. 엔진 전체 ON(vol·core30·blend·drift)에서 비중만 교체해 4지표 워크포워드 OOS 비교. `scripts/exp_user_regime_redesign_oos.py`.
- **핵심 수치**: TRAIN(in-sample) Martin 1.61→**1.83(Δ+0.22)**·CAGR 8.8→9.5% — 학습창에선 개선. 그러나 TEST(OOS) Martin 3.97→**3.05(Δ−0.92)**·Ulcer 3.68→**4.49**·MaxDD −14.4→**−16.1%**·회복 409→**564일**로 전부 악화. CAGR도 18.6→17.7%로 하락 — OOS는 수익·방어 동반 악화.
- **채택 여부·결론**: **미채택, 라이브 미반영.** ① [[feedback-regime-targets-no-tuning]]·[[experiment_2026-06-15_voltarget_threshold_sweep_oos]]에서 본 **TRAIN↔TEST 부호역전의 재현** — 학습창 좋아 보이는 비중이 OOS에서 정확히 역전. 손으로 짠 합리적 비중조차 in-sample 신호를 따라가면 과적합. ② 방어레짐 비중을 늘렸는데 OOS 방어가 더 나빠진 건(Ulcer·MaxDD·회복 악화), 비중 변경이 vol targeting·core30과 충돌해 오히려 집중도·타이밍을 흐트린 탓으로 추정. ③ 사용자 본인이 말한 핵심("진짜 손볼 건 비중이 아니라 레짐 정의")이 데이터로 재확인 — 다음 단계는 비중이 아니라 **Goldilocks↔Slowdown 분리 정밀화**.

## 신규 비중 (option 3: 적은 값 고정 + 드롭자산 현재비중 비례)
| 레짐 | 구성 |
|---|---|
| Goldilocks | eqETF 40·eqIND 22.9·eqFac 10·comm 5.7·MF 5.7·gold 5·cash 5·eqDEV 3.4·eqEMG 2.3 |
| Reflation | eqETF 22.4·comm 15·eqFac 12·eqIND 10.2·eqSec 10·gold 8.1·cash 8.1·tips 5.1·MF 5·eqDEV 2·eqEMG 2 |
| Slowdown | bond 25·eqETF 17.6·eqIND 11.7·gold 10·tips 10·cash 9.4·eqFac 5.9·MF 5.9·eqDEV 2.3·eqEMG 2.3 |
| Stagflation | gold 14·bond 14·cash 13.7·comm 12·tips 10·eqFac 7.8·eqIND 7.8·MF 6.9·eqETF 4.9·eqSec 4.9·eqDEV 2·eqEMG 2 |
| Crisis | bond 25·cash 20·eqETF 11.2·tips 11.2·gold 10·eqFac 6.7·eqIND 5.6·comm 5.6·eqDEV 2.2·eqEMG 2.2 |

(% 표기. Transition 미지정 → 현행 유지.)

## 백테스트 (full 엔진: 레짐스위칭+vol+core, drift, 4지표)
| 창 | 설정 | Martin | CAGR | Ulcer | 최장UW | 롤3y최악 | MaxDD |
|---|---|---:|---:|---:|---:|---:|---:|
| TRAIN | 현행 | 1.61 | 8.8% | 2.97 | 307 | 4.9% | −10.6% |
| TRAIN | 신규(재설계) | **1.83** | 9.5% | 3.01 | 342 | 5.8% | −10.9% |
| **TEST** | **현행** | **3.97** | 18.6% | 3.68 | 409 | 11.9% | −14.4% |
| TEST | 신규(재설계) | **3.05** | 17.7% | 4.49 | 564 | 11.0% | −16.1% |

## 해석
- **부호역전**: TRAIN ΔMartin +0.22 → TEST Δ−0.92. in-sample(잔잔)에 맞춘 비중이 OOS(COVID·Bear22)에서 역전. [[feedback-regime-targets-no-tuning]] 시리즈와 동일 메커니즘.
- **방어를 늘렸는데 방어가 악화**: 방어레짐(Slow/Stag/Crisis)에 bond·gold·tips를 키웠으나 OOS Ulcer 3.68→4.49·MaxDD −14.4→−16.1%·회복 409→564일로 더 나빠짐. 정적 비중을 키운 게 vol targeting(고변동시 동적 축소)·core30(30% Goldi 고정)과 어긋나 집중도·타이밍을 흐트렸기 때문으로 보임 — 엔진이 이미 동적으로 하던 일을 정적 비중으로 덮어쓰면 손해.
- **사용자 가설 재확인**: "비중이 아니라 레짐 정의가 진짜 레버"라는 본인 직관이 맞음. 비중 재설계는(합리적이어도) OOS에서 못 이김 → 다음은 Goldilocks↔Slowdown 분리 정밀화([[experiment_2026-06-15_regime_occurrence_transition_probs]]의 84.6% 구간).

## 한계
- core30이 자산 30% Goldilocks 고정 → Goldi 외 레짐 비중변경 효과 일부 무력. vol targeting이 고변동시 비중 재조정 → 정적 비중 효과 흡수.
- 단일 경로(COVID·Bear22 각1회)·USD단일·동시점(엔진내). Transition 미변경.
- option 3 채움(드롭자산 현재비중 비례)은 사용자 선택. 다른 채움 규칙(정규화·현금)이면 수치 달라질 수 있으나 부호역전 결론은 robust할 가능성 높음(시리즈 일관).
