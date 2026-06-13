# synthetic_pairs 금(411060) 자동 가중 분석 — 큐 항목 D 검증 (2026-05-30)

> **요약**: ① USD 매수 지연 시 XLE·VTIP·DBC·DBMF가 모두 411060(금)으로 합성 노출이 흘러 최악 시나리오(Stagflation + USD 전면 부족)에서 gold가 35%까지 부풀 수 있다는 우려를 분석했다. ② 실제 라이브는 매 실행 지연 1건 단위로 411060 비중이 10-11%에 안정적으로 유지됐으며, 261220은 버그가 아닌 DBC 합성 브리지 전용 의도된 설계임을 확인했다. ③ 라이브 코드 변경 없이 문서화만 — 최악 35%는 드문 confluence, 주요 트리거(USD 부족)는 USD 입금으로 완화, 구조적 강제 때문에 분산이 무조건 낫지도 않다.

## 배경

2026-05-29 트레이딩 코드 검토에서 식별한 D 항목:

> **D — synthetic_pairs에서 금(411060)이 자동 가중**
> XLE/DBC/VTIP/DBMF가 합성 노출에서 모두 411060로 흘러감. USD 매수 실패 누적 시
> 금이 의도보다 부풀어. 261220(WTI)은 universe엔 있지만 regime_targets엔 없는 좀비 종목.

## 합성 메커니즘 (라이브 전용)

T+2 결제 지연으로 USD 매수가 당일 체결 못 하면 `deferred_buys`에 저장되고, 다음 실행에서
`apply_synthetic_reallocation`이 KRW 동등 자산 비중을 임시 증가시킨다 (`trading/portfolio.py:476`).
**백테스트 엔진은 이 메커니즘을 모델링하지 않는다** (즉시 체결 가정) → buffer_floor처럼 백테스트 불가.

### gold로 흘러드는 USD 매핑 (`config.yaml` synthetic_pairs)

| USD 티커 | asset_class | → 411060 비중 |
|---|---|---|
| XLE  | equity_sector | 50% (나머지 50% 379800) |
| VTIP | bond_tips | **100%** |
| DBC  | commodity | 50% (나머지 50% 261220) |
| DBMF | managed_futures | **100%** |

gold가 USD 인플레/분산 자산의 KRW 프록시 catch-all이 된 이유: KRW universe에 유동성 있는
인플레 헷지 commodity-ish 종목이 411060(금)·261220(WTI) 둘뿐이라 구조적으로 강제됨.

## 최악 시나리오 (Stagflation + USD 전면 부족)

Stagflation 기본 비중: equity_sector 0.05, commodity 0.18, managed_futures 0.07, bond_tips 0.08.
4개 USD 매수가 모두 지연되면 합성 gold 추가분:

```
XLE  0.05 × 0.5 = 0.025
VTIP 0.08 × 1.0 = 0.080
DBC  0.18 × 0.5 = 0.090
DBMF 0.07 × 1.0 = 0.070
합계 = 0.265
```

기본 gold 0.18 + 0.265 = raw 0.445. `apply_synthetic_reallocation`이 original_sum으로 재정규화
(portfolio.py:516-519): 전체합 1.255 → scale 0.99/1.255 = 0.789 → **gold 최종 ≈ 35%**,
나머지 자산 일괄 ~21% 희석.

## 라이브 현실 (logs/krw.log, 2026-05-15~29)

- 합성 발동은 매 실행 **"지연 매수 1건"** 단위로 소량.
- 411060 비중: 계좌 14-14.8% / **전체 ~10-11%** — Goldilocks 목표(gold 0.10) 근처에 안정.
- 최악(35%)은 *Stagflation 레짐 AND commodity/MF/tips/sector USD 매수 동시 지연*이라는 드문
  confluence에서만 발생. 그 트리거가 곧 **USD 예산 부족(항목 A)**.

## 261220 좀비 우려 재평가

`config.yaml:128-132` 261220 universe 정의에 이미 주석:
> `# Synthetic Bridge (KRW) — T+2 합성 노출 전용, regime_targets 미포함`

**의도된 설계**로, DBC 합성(50%)의 WTI 다리 역할 전용. regime_targets 부재는 버그가 아니라 설계.
좀비 종목 우려는 이미 해소된 상태.

## 결정 (2026-05-30, 사용자: 코드 변경 없이 문서화만)

**라이브 코드 변경 없음. baseline 유지.** 근거:

1. **조건부 tail**: 최악 35%는 Stagflation + USD 전면부족의 드문 confluence에서만. 라이브 현실은 ~10-11%.
2. **A로 직접 완화**: 트리거(USD 부족)가 2026-06-01 월요일 USD 입금(항목 A)으로 줄어듦. 지연 빈도 ↓ → 합성 gold 부하 ↓.
3. **구조적 강제**: gold는 KRW universe의 유일한 유동성 인플레 헷지 프록시. 매핑 다양화(VTIP/DBMF → 305080)는
   프록시 의미(인플레채·트렌드추종 → 듀레이션채)를 훼손해 트레이드오프가 명확하지 않음.
4. **261220은 이미 의도된 설계** — 별도 조치 불필요.
5. **재정규화가 sum은 보존** — 합성이 폭증해도 전체 노출 합(0.99)은 유지되고, 임시(T+2, 실제 USD 체결 시 해소)다.

C·F와 동일하게 "분석 → 문서화 → baseline 유지" 패턴. 라이브 monitor.log에서 Stagflation 진입 +
USD 부족이 겹치는 구간이 실제로 관측되면 그때 합성 cap(옵트인) 재검토.
