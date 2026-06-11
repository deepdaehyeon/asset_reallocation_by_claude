# 분석: USD 종목 → 국내상장(KRW) ETF 대체 가능성 지도

- 날짜: 2026-06-12
- 목적: 합성 순환매를 줄이기 위해 USD 종목을 KRW-native로 대체할 수 있는지 자산군별 확인.
  채권은 이미 완료(IEF/SHY→305080, [[experiment-2026-06-11-bond-krw-consolidation]]).
- 검증: 웹(KRX·운용사) — 종목코드·존재 확인. **유동성·헤지여부·보수는 매수 전 재확인 필수.**

## 대체 원칙

1. **환노출형(unhedged) 우선.** 305080이 환노출형이라 IEF와 경제적 동일했듯, KRW로 거래되되
   USD/인플레 노출은 보존해야 위기 시 달러강세 방어가 유지된다. 헤지형(H)은 이 방어를 끊는다.
2. **경제적 동일성 우선.** 같은 기초지수를 추종해야 백테스트 동등성이 성립. 다른 자산으로
   바꾸는 건 '대체'가 아니라 '전략 변경'.

## 자산군별 지도

| 자산군 | 현재 USD | KRW 대체 후보 | 판정 |
|---|---|---|---|
| equity_etf | (이미 KRW) | 379800/379810 | ✅ 이미 KRW |
| bond_usd | IEF/SHY | 305080 | ✅ 완료(통합) |
| gold | (이미 KRW) | 411060 | ✅ 이미 KRW |
| **bond_tips** | VTIP | **468370** KODEX iShares 미국인플레이션국채액티브 | ✅ **깨끗한 대체** |
| **equity_sector** | XLE | **218420** KODEX 미국S&P500에너지(합성) | ⚠️ 가능(합성형) |
| equity_emerging | VWO | KODEX MSCI EM선물(H) 등 | ⚠️ 대부분 헤지형(H) |
| equity_individual | NVDA/LLY/PLTR/TSLA | 381180(반도체) 등 테마 | ⚠️ 전략 변경(알파→베타) |
| equity_developed | VEA | KODEX 선진국MSCI World 등 | ❌ ex-US 없음(US 중복) |
| commodity | DBC | (소형 원자재 ETF) | ❌ 광범위·유동성 부족 |
| managed_futures | DBMF | 없음 | ❌ 국내 CTA ETF 부재 |
| equity_factor | VTV/AVUV | 없음 | ❌ 미국 가치·소형가치 부재 |

## 핵심 통찰 — 깨끗한 대체는 현행 Goldilocks 레짐을 거의 못 줄인다

현재 라이브 레짐 Goldilocks의 USD 수요 0.40 구성:
- equity_individual **0.20** ← USD 수요의 **절반**
- commodity 0.05 + managed_futures 0.05 + factor 0.05 + developed 0.03 + emerging 0.02

깨끗한 대체(TIPS 468370, 에너지 218420)는 **Goldilocks에서 둘 다 비중 0**이라(에너지는
Goldilocks 0%, TIPS도 0%) **지금 합성을 못 줄인다.** 그것들은 방어/Reflation 레짐에서만
의미가 있다. 현행 레짐에서 USD 수요를 실제로 줄이려면 **equity_individual(USD 20%)**를
건드려야 하는데, 이건 1:1 KRW 대체가 없다:
- 381180(필라델피아 반도체)로 NVDA 부분 근사 가능하나 종목집중·테마 변질.
- KODEX S&P500/나스닥(이미 보유)으로 흡수하면 **개별주 알파를 지수 베타로 희석** = 전략 변경.

즉 "KRW를 더 산다"는 두 갈래로 갈린다:
- **(A) 배관 교체(전략 불변):** VTIP→468370, XLE→218420. 합성 제거에 기여하지만 *방어 레짐
  한정* 효과. 지금 당장 체감은 작다. 백테스트 동등성만 확인하면 안전하게 채택 가능.
- **(B) 통화 비중 이동(전략 변경):** equity_individual·commodity·MF 같은 USD-only 자산군을
  줄이고 KRW-native(equity_etf·gold·bond_krw·cash)로 재배분. Goldilocks 합성을 크게 줄이지만
  대가는 ① 개별주 알파 포기 ② 인플레·위기 헤지(DBC/DBMF/TIPS) 축소 = 분산 약화.
  [[feedback-regime-targets-no-tuning]](성능 미세튜닝은 노이즈)와는 결이 다른 *통화* 목적이나,
  분산을 깎는 건 사실이므로 백테스트로 비용을 계량해야 함.

## 대체 불가 자산군이 남기는 USD 바닥

DBMF(CTA)·VTV/AVUV(미국 팩터)·VEA(선진 ex-US)·DBC(광범위 원자재)는 KRW 대체가 없다.
이들이 활성인 레짐(특히 Reflation: commodity 0.16+MF 0.12+factor 0.08)에서는 USD 수요가
구조적으로 높게 남는다 → 합성/환전을 0으로 만들 수는 없고, **USD 계좌를 그 레짐 수요만큼
선충전**해 두는 운영 대응이 합성보다 깨끗하다([[project-live-turnover-vs-backtest-gap]] 연계).

## 권고

1. **(A) 깨끗한 대체 먼저 — 백테스트 동등성 확인 후 채택:** 468370(TIPS), 218420(에너지).
   305080 통합과 동일한 패턴(data.py에 KRW티커→기초지수 매핑 후 baseline 대비 검증).
   단 218420은 합성형이라 유동성·괴리율 점검 필수.
2. **(B) equity_individual의 통화 이동은 별도 전략 결정**으로 분리. core+satellite(core50)에서
   개별주를 satellite 알파로 유지할지(USD 유지), core 쪽 KRW 지수로 흡수할지(알파 포기)를
   먼저 정한 뒤 backtest로 알파 비용 계량.
3. **대체 불가 자산군은 USD 선충전으로 대응** — 합성을 늘리지 말 것.

## 검증한 종목코드 (매수 전 재확인 필수)

- 468370 — KODEX iShares 미국인플레이션국채액티브 (기초: ICE U.S. Treasury Inflation Linked)
- 218420 — KODEX 미국S&P500에너지(합성)
- 381180 — TIGER 미국필라델피아반도체나스닥 (SOX)
- (참고) KODEX 선진국MSCI World = US 포함이라 VEA(ex-US) 대체 부적합

## 한계

- 헤지여부·유동성(AUM)·괴리율·보수는 웹 1차 확인만 — 실제 매수 전 KRX/운용사 페이지 재확인.
- 합성형(218420)은 스왑 카운터파티·괴리 리스크가 실물형과 다름.
- 대체의 수익 동등성은 백테스트(data 매핑) 검증 후에만 채택.
