# #2 동시점(실현수익) 재순위 — forward 빼고 "그 레짐 들고 있을 때 번 것"만으로

> **요약**: ① #1에서 모델이 예측기가 아니라 늦은 식별기로 확인됐으므로(라벨 시점엔 움직임이 끝났고 직후는 평균회귀), forward 수익은 *다음 레짐의 회복*을 미리 빌려와 레짐 선택을 과대평가한다 — 라벨이 바뀌면 이미 다른 비중으로 옮겨가 forward 구간을 그 종목으로 들고 있지 않기 때문. 그래서 forward를 완전히 제거하고, 각 ETF·레짐별로 그 레짐으로 라벨된 날의 일별수익만 이어붙인 *레짐 조건부 실현 경로*에 규칙4 4지표(Martin 1차·Ulcer·MaxDD·최장UW)를 적용해 재순위했다. ② **이전 forward 검증의 Stagflation "구제"가 동시점에선 무너진다**: Stagflation 비중확대(▲) 중 **gold만 살아남고(Martin 0.92, 5위) commodity(DBC -2.88, 12위)·에너지(XLE -1.85, 7위)는 하위** — forward로는 인플레헤지가 최상위였지만 *그 레짐을 실제 들고 있는 동안엔* 처참하다. 반면 **Goldilocks·Reflation·Slowdown은 동시점에서도 비중확대 자산이 상위권으로 견고**(Goldilocks equity 1·3위, Reflation DBC 1위, Slowdown 채권 2위). ③ **두 약점 재확인+신규**: (a) **Goldilocks gold 10%는 동시점 Martin -0.14·CAGR -1.4%·10/15위 = 사실상 죽은 비중**(forward·동시점 양쪽에서 일관되게 약함 → 축소 권고 강화), (b) **Crisis bond_tips 10%가 동시점 Martin -1.67·11위로 약함**(Crisis ▲ 중앙순위 11위 "재검토"는 일부 cash의 Martin 분모왜곡 탓이나 bond_tips는 진짜 약점), (c) **DBMF는 전 레짐 동시점 일관 약세**(Reflation -1.58·Slowdown -0.92·Stagflation -3.03·Crisis -4.29, 단 2019 상장 단기). 결론: 종목 선택은 *동시점 기준*으로도 4/5 레짐 견고하나, **Stagflation의 commodity/에너지 비중은 forward 착시였고 동시점으론 정당화 안 됨**, Goldilocks gold·Crisis bond_tips·DBMF가 약점.

- 날짜: 2026-06-13
- 코드: `scripts/rerank_regime_assets_contemp.py` (진단 — 라이브/엔진/config 변경 없음)
- 기간: 2010-01-01 ~ 2025-04-30, 일별 rule acting regime, ETF 15종(개별주식 제외), **forward 제거**
- 선행: #1 [[experiment_2026-06-13_regime_detection_lag]](늦은 식별기 확인), 이전 forward 검증
  [[experiment_2026-06-13_regime_asset_selection_validation]]의 정직한 후속(forward 착시 교정)

## 왜 forward를 버렸나 (#1과의 연결)

#1에서 모델은 **늦은 식별기**로 확인됐다 — 라벨이 붙을 땐 움직임이 이미 끝났고 직후는
평균회귀하며, 에피소드 중앙 길이가 2~3일이라 라벨이 끊임없이 깜빡인다. 따라서:
- **forward 21일 수익 = 다음 레짐의 수익을 미리 빌려온 것.** Stagflation 라벨이 붙고 며칠 뒤면
  이미 다른 레짐으로 깜빡여 *그 종목을 그 비중으로 들고 있지 않다.* forward는 "그 레짐 선택"의
  성과가 아니라 "그 다음에 온 회복"의 성과다 → 레짐 선택을 과대평가.
- **정직한 척도 = 동시점.** 그 레짐으로 라벨된 날, 실제 그 종목을 들고 있을 때 번 것만 모은다.

이전 검증이 Stagflation을 "forward로 보면 타당"이라 구제한 건 *예측 렌즈*였다(사용자 지적이 옳음).
본 실험은 그 렌즈를 빼고 다시 본다.

## 결과 (레짐별 Martin 내림차순 — 비중확대 자산 발췌)

### Goldilocks (2210일) — ✅ 동시점에서도 견고
| 순위 | 티커 | 자산군 | 비중 | CAGR | Martin | MaxDD |
|--:|---|---|--:|--:|--:|--:|
| 1 | 379800(SPY) | equity_etf | **42%▲** | 39.8% | 26.36 | -7.5% |
| 3 | 379810(QQQ) | equity_etf | **42%▲** | 52.4% | 16.95 | -15.4% |
| 10 | 411060(GLD) | gold | 10% | **-1.4%** | **-0.14** | -56.6% |

equity 1·3위. **gold 10%는 10위·Martin -0.14·CAGR -1.4% = 죽은 비중**(forward 4.5%도 미지근했음).

### Reflation (231일) — ✅ 견고 (DBMF만 약)
| 순위 | 티커 | 자산군 | 비중 | CAGR | Martin |
|--:|---|---|--:|--:|--:|
| 1 | DBC | commodity | **16%▲** | 82.4% | 47.85 |
| 4 | 218420(XLE) | equity_sector | **7%▲** | 99.6% | 14.02 |
| 11 | DBMF | managed_futures | **12%▲** | -1.7% | -1.58 |

commodity·에너지 동시점에서도 최상위. **DBMF 12%는 동시점 11위(-1.58)** — forward 11.5%였던 게
동시점에선 음(-). 추세추종은 라벨 시점엔 아직 못 번다.

### Slowdown (942일) — ✅ 가장 깨끗
| 순위 | 티커 | 자산군 | 비중 | CAGR | Martin |
|--:|---|---|--:|--:|--:|
| 2 | 305080(IEF) | bond_krw | **27%▲** | 14.6% | 4.49 |
| 4 | 411060(GLD) | gold | 14% | 20.5% | 1.55 |
| 8~11 | equity/commodity | | 15%/5% | -8~-22% | <0 |

채권 2위·금 4위 상위, 주식·원자재 전부 음(-). 방어 배분이 동시점과 정확히 일치.

### Stagflation (163일) — ⚠️ **forward 착시 붕괴: gold만 살고 commodity/에너지 무너짐**
| 순위 | 티커 | 자산군 | 비중 | CAGR | Martin | (이전 fwd21연) |
|--:|---|---|--:|--:|--:|--:|
| 5 | 411060(GLD) | gold | **18%▲** | 11.9% | **0.92** | (25.4%) |
| 7 | 218420(XLE) | equity_sector | **5%▲** | -69.9% | **-1.85** | (60.7%) |
| 12 | DBC | commodity | **18%▲** | -48.6% | **-2.88** | (34.2%) |

**핵심 교정**: forward로는 commodity +34%·에너지 +61%로 최상위였지만, *그 레짐을 실제 들고 있는
동안엔* DBC -48.6%(12위)·XLE -69.9%(7위). 상위는 채권·gold뿐. → **Stagflation의 commodity 18%·
에너지 5% 비중은 동시점으로 정당화되지 않는다**(forward가 다음 회복을 빌려와 과대평가했던 것).
gold 18%만 동시점으로 타당.

### Crisis (179일) — ⚠️ 채권 견고, bond_tips 약점 + cash는 분모왜곡
| 순위 | 티커 | 자산군 | 비중 | CAGR | Martin |
|--:|---|---|--:|--:|--:|
| 2 | 305080(IEF) | bond_krw | **20%▲** | 13.4% | 5.32 |
| 4 | 379810(QQQ) | equity_etf | 10% | **9.5%** | 0.49 |
| 11 | 468370(TIP) | bond_tips | **10%▲** | -3.1% | -1.67 |
| 15 | 469830(BIL) | cash | **28%▲** | 0.6% | -76.14 |

채권 2위로 견고. **equity 10%는 동시점에서도 CAGR +9.5%·Martin 0.49로 양(+)** — V자 보상이
forward 전유물이 아니라 Crisis 라벨이 저점 근처라 동시점에도 반등을 일부 잡는다. **bond_tips 10%는
동시점 -3.1%·11위로 약점.** cash 15위(-76.14)는 변동성 ~0%의 Martin 분모 폭발일 뿐 실제론 안정앵커
(CAGR +0.6%·MaxDD -0.1%) — 순위에서 cash는 무시. ▲ 중앙순위 11위 "재검토"는 이 cash 왜곡 탓이 큼.

## 발견 요약

1. **Goldilocks·Reflation·Slowdown = 동시점에서도 견고.** 비중확대 자산이 상위권.
2. **Stagflation = forward 착시 교정으로 부분 붕괴.** gold(18%)만 동시점 타당, **commodity 18%·
   에너지 5%는 동시점 하위** — 이전 "forward로 타당"은 예측 렌즈 오류였다(사용자 지적이 옳았음).
3. **Crisis = 채권 견고, equity 10% 동시점에도 양(+), bond_tips 10% 약점.** cash 꼴찌는 분모 artifact.
4. **약점 3종**: (a) Goldilocks gold 10%(동시점·forward 양쪽 죽은 비중), (b) Crisis bond_tips 10%
   (동시점 -1.67), (c) DBMF 전 레짐 동시점 일관 약세(단 2019 상장 단기샘플).

## 권고 (4지표·동시점 기준)

| 항목 | 권고 | 근거 |
|---|---|---|
| Goldilocks gold 10% | **축소** (강화) | 동시점 Martin -0.14·CAGR -1.4%·10위 + forward도 미지근. 양쪽 일관 |
| Stagflation commodity 18% | **재검토** | 동시점 Martin -2.88·12위. forward 착시였음. gold/채권으로 무게이동 검토 |
| Stagflation 에너지 5% | 재검토 | 동시점 Martin -1.85·7위 |
| Crisis bond_tips 10% | 관찰 | 동시점 -1.67. 단 분산헤지·소표본(2020·2022 집중)이라 즉시 제거 아님 |
| Crisis equity 10% | **유지** | 동시점에도 +9.5%/Martin 0.49 — V자가 forward 전유물 아님 |
| DBMF | 보류 | 동시점 일관 약세지만 2019 상장 단기. 별도 장기검증 |

* 모든 권고는 **레짐 조건부 단독 성과**이지 포트폴리오 결합(상관·분산)이 아님 → 다음 단계(#3)에서
  "데이터 최적 구성 vs 현재 비중" 대조 후, 비중 변경은 별도 4지표 스윕으로 포트폴리오 영향 확인 필요.
  특히 [[feedback-regime-targets-no-tuning]](per-regime 미세튜닝은 노이즈) 교훈상, 본 결과는
  *큰 방향성 오류(Stagflation commodity·Goldilocks gold)*만 다루고 미세조정은 지양한다.

## 한계

- **비연속일 이어붙이기**: 레짐 경계의 점프(평균회귀 꼬리)가 실현경로에 포함된다. 하지만 이것이
  "그 레짐 든 동안의 체감"이라 동시점 척도로는 정당. 연환산은 레짐일을 연속거래일로 간주.
- **Martin 분모왜곡**: cash·SHY 등 초저변동 자산은 Martin이 폭발/붕괴 → 순위는 CAGR·MaxDD와 함께 해석.
- **소표본**: Stagflation 163·Crisis 179일은 2020·2022 집중. DBMF·AVUV 2019 상장(샘플 94~777일).
- **프록시·in-sample**: KRW→US ETF, 환율 무시. 레짐 라벨은 rule 기반(detect_regime 변경 시 달라짐).
