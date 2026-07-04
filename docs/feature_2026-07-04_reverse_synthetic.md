# 역합성 (Reverse-synthetic) — KRW 계좌 초과분을 USD 등가 종목으로 이동

> **요약**: ① 기존 엔진은 USD 부족 시 부족분을 KRW(equity_etf·bond_krw)로 흡수하는 forward 경로만 있고 그 반대(KRW-native 목표가 KRW 계좌 여력을 초과)는 **비례 축소로 방어 노출을 잃는** 비대칭이 있어(방어 국면+USD 과잉 시 305080 등이 반토막), 그 거울인 역합성을 설계·구현했다. ② KRW-native 목표 합이 KRW 계좌 여력을 넘으면 경제적 등가 USD 클래스가 있는 클래스(bond_krw→IEF/SHY, cash→SGOV, gold→GLD, equity_etf→SPY/QQQ)부터 USD 잔여 예산 한도 내에서 USD 계좌로 옮겨 **노출 총량을 보존**하고, 매핑 없는 equity_sector·bond_tips만 축소한다 — `usd_remaining>0`(USD 과잉)일 때만 발동하므로 bond_shortfall(USD 부족)과 상호 배타라 순환이 없다. ③ SPY·QQQ·GLD를 universe에 추가하고 `reverse_synthetic.enabled: true`(기본 ON)로 라이브 반영했으며, 모의 3케이스(방어+USD과잉 발동·Goldilocks 균형 미발동·OFF시 기존 축소) 통과. 단 백테스트는 usd_ratio=0.30 고정이라 이 경로가 거의 안 돌아 4지표 검증 불가한 라이브 전용 구조 안전망이다.

## 배경 — 비대칭 (docs 이전 진단)

계좌 분리(KRW/USD) 구조에서 흡수(absorber)가 **USD→KRW 단방향**만 있었다:
- USD 주식 부족 → KRW `equity_etf`(잔여 흡수).
- USD 채권 부족(`bond_shortfall`) → `bond_krw`.
- USD 매수 지연(T+2) → `synthetic_pairs`로 KRW 프록시.

반대로 **KRW 계좌가 KRW-native 목표(gold·bond_krw·cash·tips·sector)를 다 못 담으면** `portfolio.py`의
`krw_total > krw_investable` 정규화가 **전 종목 비례 축소** → 방어 국면에서 USD 계좌가 과대하면
KRW 방어자산(한국채·금·현금)이 깎여 방어가 약해지고, 대체 USD 매수도 없었다.

## 설계

- **발동 조건**: `reverse_synthetic.enabled` AND `krw_ratio>0` AND `usd_remaining>0`(USD 우선순위
  배정 후 USD 잔여 존재 = USD 과잉) AND KRW 초과분 `overflow>0.1%`.
- **매핑 (config `reverse_synthetic.map`)**: `bond_krw→bond_usd`, `cash→cash_usd`, `gold→gold_usd`,
  `equity_etf→equity_etf_usd`. 방어 듀레이션 우선순위(채권→현금→금→주식)로 이동.
- **알고리즘** (`derive_account_weights`, monitoring 시점): USD 우선순위 배정 후 KRW-native 유효목표
  `krw_eff` 집계 → `overflow = Σkrw_eff − krw_ratio×(1−krw_cash_min)` → 매핑 있는 클래스부터
  `movable=min(class목표, overflow, usd_remaining/total)`만큼 `usd_pool[usd_cls]`로 이동하고 `krw_eff`
  감액 → 잔여 overflow(매핑 없는 클래스·USD 소진분)는 기존 비례 축소.
- **순환 없음**: `bond_shortfall`(USD 부족)은 `usd_remaining=0`일 때만, 역합성은 `usd_remaining>0`일
  때만 발동 → 상호 배타.

## 신규 종목·라우팅 (config.yaml)

- universe: `SPY`(equity_etf_usd)·`QQQ`(equity_etf_usd)·`GLD`(gold_usd), 전부 USD·exec_account USD.
- asset_routing: `equity_etf_usd: {SPY:0.64, QQQ:0.36}`(KRW 379800/379810 대칭)·`gold_usd: {GLD:1.0}`.
  `bond_usd`(IEF/SHY)·`cash_usd`(SGOV)는 기존 재사용.
- settlement.synthetic_pairs: `SPY→379800·QQQ→379810·GLD→411060`(역합성 매수 T+2 지연 시 KRW 프록시).
- regime_targets에는 미포함(cash_usd/SGOV처럼 동적 라우팅 전용).

## 검증 (모의, `derive_account_weights` 직접 호출)

| 시나리오 | 결과 |
|---|---|
| (A) Crisis 유사 + USD60/KRW40 | bond_krw 20%→IEF/SHY, cash 19.4%→SGOV. 채권 노출 0.20 보존(축소 안 됨). gold는 USD 소진 후 KRW 잔류. ✓ |
| (B) Goldilocks + USD37/KRW63 | 미발동(KRW 여력 충분). ✓ |
| (C) (A)와 동일 + enabled=false | IEF/SHY 없음, 305080이 0.20→0.099 반토막(기존 축소). ✓ |

→ ON일 때 방어 노출 보존, OFF/균형일 때 무변화 확인.

## 상호작용·한계 (규칙5)

- **06-11 채권 통합과 대칭 완성**: 그때 bond_usd→bond_krw 통합으로 forward 순환매를 끊었는데,
  역합성은 IEF를 되살린다 — 모순이 아니라 대칭(forward:USD채권부족→KRW / reverse:KRW채권초과→USD).
  둘이 합쳐 채권이 통화 무관 fungible. 단 IEF 매매가 다시 생길 수 있음(초과 토글 시).
- **회전율**: 초과가 켜졌다 꺼졌다 하면 USD 종목 매매 발생. 단 방어+USD과잉일 때만 → 평소 미발동.
- **백테스트 검증 불가**: usd_ratio=0.30 고정이라 KRW/USD 불균형이 안 생김. 계좌비율 표류 백테스트가
  있어야 4지표 검증 가능([[project-live-turnover-vs-backtest-gap]]).
- **부분 커버**: equity_sector(에너지 XLE 없음)·bond_tips(VTIP 없음)는 여전히 축소.
- **현재 미발동**: USD37%/KRW63%·Goldilocks라 KRW 여력 충분 → 코드만 인입, 매매 변화 없음.
