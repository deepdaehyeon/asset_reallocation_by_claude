# #3 데이터 최적 *방향* vs 현재 regime_targets 대조 (동시점, 자산군 단위)

> **요약**: ① #2의 동시점 재순위를 regime_targets가 실제 제어하는 단위(자산군 class)로 올려, asset_routing대로 결합한 각 자산군의 레짐 조건부 실현수익(Martin 1차)을 현재 비중과 대조하고, 큰 방향성 어긋남만 플래그했다(미세 비중 처방은 [[feedback-regime-targets-no-tuning]]에 따라 의도적으로 배제 — 방향성 진단만). ② **9개 플래그 중 bond_usd 3건(Slowdown·Stagflation·Crisis "추가검토")은 가짜 신호**다 — bond_usd(IEF/SHY)는 bond_krw(305080→IEF 프록시)와 같은 기초자산이고 2026-06-11 config가 둘을 bond_krw로 통합했으므로 이미 20~27%로 보유 중(중복). 이를 빼면 **진짜 방향성 어긋남은 6건이며, 일관되게 "방어 레짐의 위험자산 과대 + 죽은 헤지"로 수렴**한다: 가장 큰 건 **Stagflation commodity 18%(동시점 Martin -2.88·CAGR -48.6%·9/11위)**와 **Goldilocks gold 10%(Martin -0.14·CAGR -1.4%·8/11위, 전 분석 일관)**, 그 다음 **Crisis bond_tips 10%(-1.67·8위)·Reflation managed_futures 12%(-1.58·9위, 단 2019 단기)**. ③ 반대로 **방어 레짐의 채권·gold, 위험 레짐의 equity·commodity 등 핵심 축은 모두 ALIGNED**로, 시스템의 큰 골격(레짐 스위칭 방향)은 데이터로 견고하다. 결론: config 변경은 하지 않고(규칙3·검증 우선), **Stagflation commodity·Goldilocks gold·Crisis bond_tips 3건을 4지표 포트폴리오 스윕 후보로 등록** 권고 — 단 상관·분산 효과 미반영이라 단독성과 약함이 곧 제거는 아님.

- 날짜: 2026-06-13
- 코드: `scripts/contrast_regime_targets_contemp.py` (진단 — 라이브/엔진/config 변경 없음)
- 기간: 2010-01-01 ~ 2025-04-30, 일별 rule acting regime, 자산군 단위(asset_routing 결합), forward 없음
- 선행: #1 [[experiment_2026-06-13_regime_detection_lag]] · #2 [[experiment_2026-06-13_regime_asset_rerank_contemp]]

## 방법

#2는 개별 ETF 순위였다. regime_targets는 *자산군* 비중을 제어하므로, asset_routing의 within-class
비중으로 각 자산군 종목을 결합한 실현수익(레짐 라벨된 날만)에 규칙4 Martin/CAGR/MaxDD를 적용,
자산군을 Martin 내림차순 정렬해 현재 비중과 대조했다. cash는 변동성~0의 Martin 분모왜곡이라
순위 제외(앵커로만 표기). 판정 규칙: **OVER_WEAK**=비중≥10%인데 Martin 하위절반(축소검토),
**UNDER_STRONG**=비중≈0인데 Martin 상위3위(추가검토), 나머지 ALIGNED.

## 플래그 9건 → 가짜 3건 제거 → 진짜 6건

| 레짐 | 자산군 | 현재 | 동시점순위 | Martin | CAGR | 판정 | 비고 |
|---|---|--:|--:|--:|--:|---|---|
| Stagflation | commodity | 18% | 9/11 | -2.88 | -48.6% | **축소검토** | **최대 어긋남(forward 착시 교정)** |
| Goldilocks | gold | 10% | 8/11 | -0.14 | -1.4% | **축소검토** | 전 분석 일관 죽은 비중 |
| Crisis | bond_tips | 10% | 8/11 | -1.67 | -3.1% | **축소검토** | 분산헤지·소표본이라 관찰 |
| Reflation | managed_futures | 12% | 9/11 | -1.58 | -1.7% | **축소검토** | 2019 상장 단기 → 보류 |
| Slowdown | equity_etf | 15% | 7/11 | -0.40 | -8.6% | 축소검토(약) | 방어 레짐 위험자산 = 예상된 약세 |
| Reflation | equity_etf | 22% | 6/11 | +2.79 | +20.4% | 축소검토(약) | CAGR 양(+), 중위권일뿐 — 약한 신호 |
| ~~Slowdown~~ | ~~bond_usd~~ | ~~0%~~ | ~~2~~ | ~~3.30~~ | | ~~추가검토~~ | **가짜: bond_krw와 동일기초(IEF), 이미 보유** |
| ~~Stagflation~~ | ~~bond_usd~~ | ~~0%~~ | ~~2~~ | ~~8.14~~ | | ~~추가검토~~ | **가짜: 위와 동일** |
| ~~Crisis~~ | ~~bond_usd~~ | ~~0%~~ | ~~2~~ | ~~5.15~~ | | ~~추가검토~~ | **가짜: 위와 동일** |

### bond_usd 가짜 신호 설명
bond_usd(IEF 0.58 + SHY 0.42)와 bond_krw(305080 → 백테스트 PROXY_MAP상 IEF)는 같은 기초자산이다.
config는 2026-06-11 "채권 KRW 통합 — 합성 잔차 제거"로 bond_usd를 bond_krw로 합쳤다(bond_usd
비중 0%). 따라서 "bond_usd가 동시점 2위인데 0%다"는 *이미 bond_krw 20~27%로 그 노출을 들고 있어*
가짜 신호다. 채권 노출은 충분하다.

## 핵심 패턴: 어긋남은 "방어 레짐 위험자산 + 죽은 헤지"로 수렴

진짜 6건은 무작위가 아니라 두 부류다:
1. **방어 레짐의 위험자산 잔여**: Stagflation commodity 18%·equity, Slowdown equity 15%, Crisis
   bond_tips. → 방어 국면인데 위험/저효율 자산이 비중을 차지. (단 일부는 core30 앵커·분산 목적)
2. **죽은/약한 헤지**: Goldilocks gold(호황엔 무용), Reflation MF(추세추종이 라벨 시점엔 못 범).

반대로 **시스템의 큰 골격은 ALIGNED**: 방어 레짐의 채권(bond_krw)·gold가 1~4위, 위험 레짐의
equity(Goldilocks 1·2·3위)·commodity(Reflation 1위)가 상위 — **레짐 스위칭의 방향 자체는 데이터로
정당**하다. 어긋남은 *방향*이 아니라 *일부 자산군의 잔여 비중*에 있다.

## 권고 (config 변경 없음 — 후보 등록만)

| 우선순위 | 항목 | 권고 | 근거 |
|--:|---|---|---|
| 1 | Stagflation commodity 18% | **스윕 후보 1순위** | 동시점 -2.88·-48.6%, forward 착시. gold/채권으로 무게이동 검토 |
| 2 | Goldilocks gold 10% | **스윕 후보** | 3개 분석(forward·#2·#3) 모두 죽은 비중 일관 |
| 3 | Crisis bond_tips 10% | 관찰 | 동시점 약하나 분산헤지·소표본(2020·2022) → 즉시변경 금지 |
| 4 | Reflation MF 12% | 보류 | 2019 상장 단기. DBMF 별도 장기검증 |
| — | bond_usd 플래그 3건 | **무시** | bond_krw와 동일기초·이미 보유(가짜 신호) |
| — | 나머지 골격 | **유지** | 레짐 스위칭 방향은 데이터로 견고 |

* 어느 것도 본 진단만으로 비중을 바꾸지 않는다. 자산군 단독 동시점은 **상관·분산 효과 미반영**
  ([[project-voltarget-blend-defense-engine]]에서 보듯 단독 약한 자산도 분산 가치 가능) →
  Stagflation commodity·Goldilocks gold만 별도 4지표 *포트폴리오* 스윕으로 결합효과 확인 후 결정.

## 한계

- **자산군 단독 동시점**: 포트폴리오 결합 시 상관·분산 미반영. gold·commodity는 단독 약해도 위기
  분산 헤지 가치 가능 → "축소검토"이지 "제거"가 아님.
- **bond_usd/bond_krw 프록시 중복**: PROXY_MAP상 305080→IEF라 두 자산군이 백테스트에서 사실상
  동일. 라이브에선 KRW 상품(305080)이 실제 보유분.
- **Martin 분모왜곡**: cash 순위 제외. 초저변동 자산은 CAGR·MaxDD 병행 해석.
- **소표본·프록시·in-sample**: Stagflation 163·Crisis 179일(2020·2022 집중), DBMF 2019 상장.
