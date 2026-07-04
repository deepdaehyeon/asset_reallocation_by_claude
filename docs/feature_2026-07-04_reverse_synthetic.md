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

## 동반 보완: equivalence-group drift (쓸데없는 통화 왕복 회전 방지)

역합성이 만든 KRW↔USD 쌍(QQQ↔379810, SPY↔379800, GLD↔411060, IEF/SHY↔305080, SGOV↔469830)은
통화(계좌)만 다르고 노출이 같다. drift를 종목별 `Σ|현재−목표|`로 세면 같은 나스닥을 QQQ↔379810로
옮기는 것이 회전으로 잡혀(계좌 여력·overflow 경계 흔들림에 반응) **의미 없는 통화 왕복 매매**를 유발한다.

- **조치**: `compute_drift(current, target, groups)`가 `config.equivalence_groups`를 받아 같은 그룹을
  **합산한 뒤** 차이를 계산. 그룹 총노출 변화만 drift로 센다. `run._compute_side_drifts`(트리거 판정)에
  배선. 그룹 내 라우팅 비율이 고정이라 그룹 합산이 실제 노출 변화만 정확히 포착.
- **검증**: 나스닥 통화 왕복(총노출 동일) → 종목별 drift 0.10 vs **그룹 drift 0.00** ✓ / 실제 노출
  0.12→0.08 감소 → 그룹 drift 0.04 ✓ / 그룹 밖 종목 변화는 그대로 계상 ✓.
- **범위**: 이건 리밸 **트리거**를 그룹 기준으로 만든다(일별 wobble이 리밸을 안 부름).

### 주문 단계 상계 (order netting) — 트리거 발동일의 wash 제거

트리거가 다른 이유로 발동한 날 force-full이 그룹 내 통화 배분을 갱신하는 잔여 wash를 제거한다
(트리거가 요즘 매일 걸려 이 잔여도 매일 생김).

- **조치**: `executor._net_equivalent_orders(orders, groups, min_order_krw)`를 `rebalance()`의
  `_build_orders` 직후·**side 필터 전**에 호출. 같은 그룹의 순매수·순매도 중 겹치는 부분(min)을
  양쪽 비례 축소 → 그룹 순노출은 보존, 상쇄분만 제거. **매매를 줄이기만** 하므로 안전.
- **2단계 집행 정합**: USD run(03:00)이 상계 후 QQQ만 일부 매수 → KRW run(10:00)은 QQQ 매수분이
  반영된 current를 다시 읽어 379810 매도를 재차 상계(무매매). 각 run이 독립적으로 상계해도 일관.
- **검증**: 순수 wash(QQQ+379810 동량) → 둘 다 제거 / 실제 +노출 + 통화이동 → 순증분만 매수, 매도
  제거 / 그룹 밖 종목 통과 / 2단계 집행 정합. 로그 `[동일자산 상계] ... 제거 (wash netting)`.

## forward 합성(synthetic_pairs)과의 분리 원칙 (2026-07-04 정리)

**불변식: 한 자산은 "다른 자산 프록시"(synthetic_pairs)이거나 "같은 자산 등가"(equivalence_groups)이지
둘 다일 수 없다.** 두 집합은 서로소여야 한다(검증: 교집합 0).

- **synthetic_pairs (forward T+2 브리지)** = *다른* 자산 프록시. 개별주→지수(NVDA→379810), 팩터·지역
  →S&P500(VTV·VEA·VWO→379800), 원자재→금(DBC·DBMF→411060). USD 매수 지연 시 상관 높은 KRW로
  임시 노출 유지 — 의도된 헤지지 wash 아님. **equivalence_groups에 넣으면 안 됨**(잘못 상계됨).
- **equivalence_groups (같은 자산)** = 통화만 다른 동일 노출. drift 합산 + 주문 상계로 통화 왕복 방지.
- **제거된 브리지 (2026-07-04)**: SPY/QQQ/GLD(이번 추가)·IEF/SHY(기존, bond_usd=0이라 forward 미매수).
  이들은 **오직 역합성으로만 USD 매수**되는데, 역합성은 KRW가 꽉 차서 USD로 간 상황 → KRW 프록시로
  브리지하면 자리가 없어 다른 KRW 보유를 밀어내는 churn. 지연 시 프록시 없이 다음 실행 재시도가 옳음.
  (노파심 점검에서 발견 — 같은 자산의 통화 왕복은 forward 브리지가 아니라 equivalence 상계로 처리.)

## 상호작용·한계 (규칙5)

- **06-11 채권 통합과 대칭 완성**: 그때 bond_usd→bond_krw 통합으로 forward 순환매를 끊었는데,
  역합성은 IEF를 되살린다 — 모순이 아니라 대칭(forward:USD채권부족→KRW / reverse:KRW채권초과→USD).
  둘이 합쳐 채권이 통화 무관 fungible. 단 IEF 매매가 다시 생길 수 있음(초과 토글 시).
- **회전율**: 초과가 켜졌다 꺼졌다 하면 USD 종목 매매 발생. 단 방어+USD과잉일 때만 → 평소 미발동.
- **백테스트 검증 불가**: usd_ratio=0.30 고정이라 KRW/USD 불균형이 안 생김. 계좌비율 표류 백테스트가
  있어야 4지표 검증 가능([[project-live-turnover-vs-backtest-gap]]).
- **부분 커버**: equity_sector(에너지 XLE 없음)·bond_tips(VTIP 없음)는 여전히 축소.
- **현재 미발동**: USD37%/KRW63%·Goldilocks라 KRW 여력 충분 → 코드만 인입, 매매 변화 없음.
