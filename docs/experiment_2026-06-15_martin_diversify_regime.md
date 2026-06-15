# 레짐별 Martin+상관분산 규칙 구성 — 방향은 상식적이나 현행 regime_targets에 OOS 열세 (미채택)

## SUMMARY
- **무엇을**: 정적판([[experiment_2026-06-15_martin_diversify_portfolio]])이 "정적은 왜 쓰냐"는 지적을 받아, 3규칙(①최고비중=Martin1위 ②붕괴방지=1위와 상관최저 산입 ③나머지=Martin순×분산도)을 *각 레짐 라벨된 날*의 자산군 Martin·상관으로 계산해 regime_targets를 통째로 재구성. 정적과 달리 full 엔진(레짐스위칭+vol targeting+core30)을 그대로 위에 얹어(방어엔진 동일) 4지표 OOS 비교. 개별주 제외 변형 동시 검증. `scripts/build_martin_diversify_regime.py`.
- **핵심 수치**: 규칙이 찾은 레짐 비중은 방향상 상식적 — Crisis 금49+채권30+EM20, Stagflation 채권30+금29+MF20+tips19, Slowdown 채권30+금24+MF20, Reflation 원자재30(1위)+밸류24+채권20, Goldilocks 개별주30+주식+현금20. 그러나 백테스트는 전부 열세: TEST(OOS) 현행 Martin **3.97** vs 전기간構(lookahead) 3.17 vs TRAIN構(정직 OOS) **1.43**. TRAIN構은 in-sample TRAIN조차 Martin −0.21(720일 underwater). 낙폭은 더 작으나(MaxDD −12.6%·Ulcer 2.62) CAGR 7.8% vs 18.6%로 수익붕괴 → Martin 폭락.
- **채택 여부·결론**: **미채택.** ① 손으로 짠 현행 regime_targets가 규칙기반 재구성보다 낫다 — 가치는 비중 재도출이 아니라 스위칭+vol 엔진([[feedback-regime-targets-no-tuning]]·[[project-voltarget-blend-defense-engine]])에 있음을 *방어엔진 동일 조건에서* 재확인. ② 레짐조건 Martin은 일반화 실패(TRAIN構이 in-sample조차 −0.21): 2010~2018 레짐날에 좋던 방어자산(채권·금)에 과配 → 2019~2025 수익붕괴. ③ **생존편향은 이번 OOS 패배의 원인 아님**: '개별주 제외'가 '전체'와 소수점까지 동일 = TRAIN構에선 equity_individual 비중 0(과거엔 안 빛남). 미래승자 효과는 전기간構 Goldilocks 1위(M28)에서만.

## 규칙이 찾은 레짐별 비중 (전기간·전체포함, %)
| 자산군 | Goldil | Reflat | Slowdo | Stagfl | Crisis |
|---|---|---|---|---|---|
| equity_etf | 9.5 | 3.7 | 0 | 0 | 0 |
| equity_factor | 13.1 | 24.4 | 0 | 0 | 0 |
| equity_sector | 6.4 | 10.8 | 0 | 0 | 0 |
| equity_individual | 30.0 | 0 | 15.0 | 0 | 0 |
| equity_developed | 9.1 | 3.2 | 0 | 0 | 0 |
| equity_emerging | 2.2 | 6.1 | 0 | 0 | 20.0 |
| commodity | 1.5 | 30.0 | 0 | 0 | 0 |
| managed_futures | 8.3 | 0 | 20.1 | 20.0 | 0 |
| bond_usd | 0 | 0 | 0 | 2.1 | 0.6 |
| bond_tips | 0 | 0 | 11.0 | 19.4 | 0 |
| bond_krw | 0 | 20.0 | 30.1 | 30.0 | 30.0 |
| gold | 0 | 1.8 | 23.8 | 28.6 | 49.4 |
| cash | 20.0 | 0 | 0 | 0 | 0 |

레짐별 1위/분산슬롯(전기간): Goldilocks indiv(M28)/cash, Reflation commodity(M48)/bond,
Slowdown bond(M4.5)/MF(corr−0.56), Stagflation bond(M8.6)/MF(−0.45), Crisis bond(M5.3)/EM(−0.45).

## 백테스트 (full 엔진: 레짐스위칭+vol+core, drift)
| 창 | 전략 | Martin | CAGR | Ulcer | 최장UW | 롤3y최악 | MaxDD |
|---|---|---|---|---|---|---|---|
| TRAIN | 현행 | 1.61 | 8.8% | 2.97 | 307 | 4.9% | −10.6% |
| TRAIN | 신규(전기간構·전체) | 1.49 | 7.4% | 2.31 | 339 | 4.9% | −8.0% |
| TRAIN | 신규(TRAIN構·전체) | −0.21 | 3.4% | 2.71 | 720 | −0.9% | −10.2% |
| TRAIN | 신규(TRAIN構·개별주제외) | −0.21 | 3.4% | 2.71 | 720 | −0.9% | −10.2% |
| **TEST** | **현행** | **3.97** | 18.6% | 3.68 | 409 | 11.9% | −14.4% |
| TEST | 신규(전기간構·전체·lookahead) | 3.17 | 15.1% | 3.51 | 571 | 8.1% | −13.1% |
| TEST | 신규(TRAIN構·전체·OOS) | 1.43 | 7.8% | 2.62 | 505 | 3.4% | −12.6% |
| TEST | 신규(TRAIN構·개별주제외·OOS) | 1.43 | 7.8% | 2.62 | 505 | 3.4% | −12.6% |

## 해석
- 방어엔진을 동일하게 맞춘(정적판의 vol 부재 교란 제거) 공정 비교에서도 현행이 이김 → 정적판 OOS
  열세가 "vol 부재 탓만"은 아니었음. 비중 재구성 자체가 가치를 못 더함.
- TRAIN構이 *in-sample TRAIN조차* −0.21인 건, 레짐조건 Martin이 2010~2018의 방어자산(채권·금)에
  과配하고 그게 full 엔진의 blend·core30로 증폭되어 강세장(2019~)에 수익을 못 낸 탓. 과방어·저수익.
- per-regime 비중 미세화는 엔진이 흡수/역효과라는 기존 결론(ablation·shrink·stagflation 시리즈)과 정합.

## 한계
- 레짐조건 일수익 비연속 → CAGR 연율화 근사(Martin은 비율이라 비교적 robust). in-sample 구성·프록시.
- 소표본 레짐(Stagflation 163·Crisis 179일) Martin·corr 불안정. rule 레짐 라벨 사용.
- 노브(W1·W2·α·β) 미튜닝(정적판 기본값). 라이브 반영은 사용자 확인 후 — 현재 미반영.
