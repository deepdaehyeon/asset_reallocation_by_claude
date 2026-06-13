# 검증: 레짐별 종목(ETF) 선택이 데이터로 타당한가

> **요약**: ① regime_targets는 이론적으로 손으로 배정됐고 한 번도 "그 레짐에서 비중을 늘린 자산이 유니버스 내 다른 ETF보다 실제 유리했는지" 데이터로 확인된 적이 없어, 라이브 acting regime(rule, 일별 detect_regime)으로 2010~2025를 레짐별로 나눠 각 ETF(개별주식 제외, KRW는 프록시)의 동시점 연환산수익·Sharpe + forward 21일을 측정해 순위와 regime_targets 비중을 대조했다. ② **5개 레짐 중 4개(Goldilocks·Slowdown·Crisis·Reflation)는 비중확대 자산이 동시점 상위권으로 타당**했다 — Goldilocks는 equity_etf(SPY Sharpe 3.31·QQQ 3.11)가 2·3위, Slowdown/Crisis는 채권·현금·금이 상위·주식/원자재가 하위로 방어 배분이 정확히 맞았고, Reflation은 commodity(DBC 3.45 1위)·에너지(218420)가 상위. **Stagflation만 동시점 순위가 하위(재검토 플래그)였으나 이는 분류 후행(V자) 때문**으로, forward 21일로 보면 인플레 헤지 베팅(DBC +34%·에너지 +61%·gold +25%)이 오히려 최상위라 act-on 기준으론 타당하다. ③ **두 약점 발견**: (a) **Goldilocks의 gold 10%**는 동시점 Sharpe -0.03·연수익 -0.5%로 사실상 죽은 비중(13/15위) — 축소 후보, (b) **managed_futures(DBMF)**는 동시점 약하나(Reflation -0.06·Stagflation -0.23) forward 최상위 + 2019년 상장 단기샘플이라 판정 보류. 결론: 종목 선택은 대체로 데이터로 정당, gold-in-Goldilocks만 재검토 권고.

- 날짜: 2026-06-13
- 코드: `scripts/validate_regime_asset_selection.py` (진단 — 라이브/엔진/config 변경 없음)
- 기간: 2010-01-01 ~ 2025-04-30, 일별 rule 레짐 라벨, ETF 15종(개별주식 4종 제외)
- 방법(사용자 합의): 레짐=라이브 rule acting regime, 수익=동시점 메인 + forward 21일 보조

## 방법

`regime_timing_source=rule`이라 acting regime = `detect_regime(features)`(HMM/평활은 비중만
건드리고 acting regime은 안 바꿈) → 일별로 detect_regime만 호출해 라이브와 동일한 레짐 라벨 산출.
레짐별로 각 ETF의 (동시점) 연환산수익·변동성·Sharpe와 (보조) forward 21거래일 수익을 측정,
Sharpe 내림차순 순위와 regime_targets의 자산군 비중을 대조했다. ▲ = 그 레짐이 자산군 평균보다
비중확대, ▽ = 0/축소.

레짐별 일수: Goldilocks 2210, Slowdown 942, Reflation 231, Crisis 179, Stagflation 163.

## 결과 (레짐별 Sharpe 순위 — 비중확대 자산 위주 발췌)

### Goldilocks (2210일) — ✅ 타당
| 순위 | 티커 | 자산군 | 레짐비중 | 연수익 | Sharpe | fwd21연 |
|--:|---|---|--:|--:|--:|--:|
| 2 | 379800(SPY) | equity_etf | **42%▲** | 34.1% | 3.31 | 10.8% |
| 3 | 379810(QQQ) | equity_etf | **42%▲** | 43.1% | 3.11 | 15.9% |
| 4 | VTV | equity_factor | 5% | 30.0% | 3.04 | 8.6% |
| 13 | 411060(GLD) | gold | 10% | **-0.5%** | **-0.03** | 4.5% |
| 14/15 | IEF/305080 | bond | 0%▽ | -4.0% | -0.67 | 1.7% |

비중확대한 equity_etf가 2·3위. 채권은 0%로 적절히 배제(하위). **단 gold 10%가 13위(Sharpe -0.03)
= Goldilocks에선 죽은 비중.**

### Reflation (231일) — ✅ 타당 (MF 단서)
| 순위 | 티커 | 자산군 | 레짐비중 | 연수익 | Sharpe | fwd21연 |
|--:|---|---|--:|--:|--:|--:|
| 1 | DBC | commodity | **16%▲** | 61.8% | 3.45 | 14.4% |
| 5 | 218420(XLE) | equity_sector | **7%▲** | 75.2% | 2.16 | 21.9% |
| 7 | 379800(SPY) | equity_etf | 22%▲ | 23.3% | 1.34 | 5.9% |
| 12 | DBMF | managed_futures | **12%▲** | -0.9% | **-0.06** | 11.5% |

commodity·에너지 강하게 1·5위로 타당. **managed_futures 12%는 동시점 12위(-0.06)지만 forward
11.5%로 전체 최상위** — 추세추종이 분류 시점 이후 작동.

### Slowdown (942일) — ✅ 타당 (가장 깨끗)
| 순위 | 티커 | 자산군 | 레짐비중 | 연수익 | Sharpe |
|--:|---|---|--:|--:|--:|
| 3 | 305080(IEF) | bond_krw | **27%▲** | 13.9% | 2.11 |
| 5 | 468370(TIP) | bond_tips | 7% | 8.2% | 1.51 |
| 6 | 411060(GLD) | gold | 14% | 19.8% | 1.33 |
| 9/10 | 379810/379800 | equity_etf | 15% | -7~8% | -0.4 |
| 15 | DBC | commodity | 5% | -23.4% | -1.30 |

채권·금이 상위, 주식·원자재가 하위 — 방어 배분이 데이터와 정확히 일치.

### Stagflation (163일) — ⚠️ 동시점 하위지만 forward로 타당 (분류 후행)
| 순위 | 티커 | 자산군 | 레짐비중 | 연수익(동시점) | Sharpe | **fwd21연** |
|--:|---|---|--:|--:|--:|--:|
| 6 | 411060(GLD) | gold | **18%▲** | 13.1% | 0.69 | **25.4%** |
| 8 | DBC | commodity | **18%▲** | -62.6% | -2.25 | **34.2%** |
| 9 | 218420(XLE) | equity_sector | **5%▲** | -112.9% | -3.01 | **60.7%** |

동시점 순위(중앙 8위)만 보면 "재검토"로 찍히나, 이는 **rule이 Stagflation을 인플레/성장쇼크의
*저점 근처에서 늦게* 라벨링하기 때문**이다. 라벨된 날의 동시점 수익은 직전 급락의 꼬리라 처참하지만,
**라벨 이후(forward) 인플레 헤지(commodity·에너지·gold)가 전체 최상위로 반등**한다. "Stagflation이라고
판단했을 때 지금부터 뭘 들까"가 의사결정이므로 forward가 더 적절 → **인플레 헤지 선택은 forward로 정당.**

### Crisis (179일) — ✅ 타당 (V자 equity 베팅 포함)
| 순위 | 티커 | 자산군 | 레짐비중 | 연수익(동시점) | Sharpe | **fwd21연** |
|--:|---|---|--:|--:|--:|--:|
| 1 | 469830(BIL) | cash | **28%▲** | 0.6% | 2.19 | 0.2% |
| 4 | 305080(IEF) | bond_krw | **20%▲** | 13.2% | 1.18 | 8.4% |
| 6 | 379810(QQQ) | equity_etf | 10% | 21.4% | 0.43 | **68.3%** |
| 9 | 468370(TIP) | bond_tips | **10%▲** | -2.4% | -0.19 | 11.1% |
| 15 | DBMF | managed_futures | 0%▽ | -35.8% | -2.20 | -4.4% |

현금·채권이 상위로 방어 타당. **equity_etf 10%는 동시점 평범하나 forward 49~68%** — config가
명시한 "분류 후행 V-shape 보상 활용"이 데이터로 확인됨. MF 0%도 적절(꼴찌).

## 발견 요약

1. **Goldilocks·Slowdown·Crisis·Reflation = 동시점 기준 타당.** 비중확대 자산이 상위권.
2. **Stagflation = 동시점 하위, forward 최상위.** 분류 후행(V자)이 원인. act-on 기준(forward)으론
   인플레 헤지 선택이 정당. 동시점만 보면 오판한다.
3. **약점 (a) Goldilocks gold 10%**: 동시점 Sharpe -0.03·연 -0.5%·13/15위, forward도 4.5%로 미지근.
   Goldilocks에선 사실상 죽은 비중 → **축소 후보**(전천후 헤지 명목이나 순수 호황엔 기여 거의 없음).
4. **약점 (b) managed_futures(DBMF)**: 동시점 약함(Reflation -0.06·Slowdown -0.33·Stagflation -0.23)
   이나 forward는 Reflation/Stagflation 최상위. **2019년 상장이라 샘플 짧음(94~777일)** → 판정 보류.
5. **(주의) cash가 모든 레짐 Sharpe 1위**는 변동성 ~0.3%의 분모 효과(분자 작아도 Sharpe 폭증)일 뿐,
   "최고 자산"이 아니라 "안정 앵커". 순위에서 cash는 참고만.

## 권고

| 항목 | 권고 | 근거 |
|---|---|---|
| Goldilocks gold 10% | **축소 검토** | 동시점 -0.5%/Sharpe -0.03/13위, forward도 미지근. 호황엔 기여 거의 없음 |
| managed_futures 비중 | 유지(보류) | 동시점 약하나 forward 최상위 + 단기샘플. 별도 장기검증 필요 |
| 나머지 레짐 선택 | **유지** | 데이터로 타당(Stagflation은 forward 기준 타당) |

* gold 축소는 별도 스윕으로 4지표(전체 포트폴리오) 영향 확인 후 결정 — 본 검증은 레짐 조건부
  자산 단독 성과이지 포트폴리오 결합 효과(상관·분산)는 미반영.

## 한계

- **프록시 대체**(KRW ETF→US ETF, 환율 무시): 검증 대상은 "기초지수/자산 선택"이지 정확한 KRW 상품 아님.
- **소표본**: Stagflation 163일·Crisis 179일은 사실상 2020 COVID + 2022에 집중. AVUV·DBMF 2019년 상장.
- **레짐 조건부 단독 성과**: 포트폴리오 결합 시 상관·분산 효과 미반영(gold는 단독 성과 약해도 분산
  헤지 가치가 있을 수 있음 — 그래서 "축소 검토"이지 "제거"가 아님).
- Sharpe는 cash류 저변동 자산을 과대평가 → 순위는 연수익·forward와 함께 해석.
- in-sample. 레짐 라벨 자체가 rule 기반이라 detect_regime 규칙 변경 시 라벨이 달라짐.
