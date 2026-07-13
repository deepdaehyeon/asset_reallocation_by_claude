# 계좌별 직접 배분기 — 역합성↔상계 교착의 근본 해결

> **요약**: ① 역합성(KRW과포화→USD 이동)과 상계(통화왕복 취소)가 교착(매 실행 ~12%p 이동 시도를 ~3천만원씩 상계로 취소 → 아무것도 안 옮겨지고 현금부족·미체결 반복)을 일으켰다. 원인은 역합성이 "이상적 KRW/USD 분할"을 매번 백지에서 계산해 기존 KRW 보유를 팔아 USD로 되사는 왕복을 만들고, 상계가 그걸(옳게) 취소하는 것. ② 사용자 지적대로 **목표·현재보유·계좌용량을 알면 계좌별 매수/매도는 직접 계산**된다 — `route_accounts`(신설)로 등가자산(379800↔SPY·411060↔GLD 등)을 하나로 보고 **기존 보유는 유지, 델타만 여유계좌로**(부족분 KRW우선→없으면 USD, 초과분 매도, 진짜 과포화만 최소 relocate) 배분. `derive_account_weights`를 이 방식으로 재작성해 역합성 블록을 대체 → 통화 왕복이 원천 소멸. ③ 시나리오 5종(금 부족→USD·초과→매도·KRW과포화→최소relocate·USD과포화 등) + 라이브 케이스(금 왕복 GLD 0, 주식 델타만 USD, relocate는 수렴) 통과. 백테스트 4지표 회귀 없음(Martin 1.269→1.259·MaxDD −17.33→−17.0·tx 8.63→8.46%, 사실상 동일 — 백테스트는 usd_ratio 고정·현재보유 None이라 라이브 이득 미반영).

## 문제 (교착)

- 개별주 접기로 KRW-native(주식+금+현금...)가 KRW 계좌보다 ~16% 큼(구성 변화로 KRW/USD 과포화는 필연).
- 역합성: "KRW 꽉 참 → 금·현금·채권을 USD로" 매 실행 ~12%p 이동 지시(기존 KRW 보유 매도→USD 매수).
- 상계: "같은 자산 통화 왕복" ~3천만원/회 취소(옳음). → **아무것도 안 옮겨지고**, 초과분 매도가 취소돼
  KRW 현금이 안 생겨 주식 매수가 15만원으로 쪼그라듦(현금부족·미체결 누적).
- 근본: 역합성(용량 인식)과 상계(현재보유 인식)가 **서로의 의도를 몰라** 충돌.

## 해결 — 직접 배분 (route_accounts)

목표·현재보유·용량으로 계좌별 배정을 직접 계산(won 단위):
1. 현재보유 유지(목표 한도 내, 초과분은 감축).
2. 부족분(델타>0): KRW 여유 있으면 KRW, 없으면 USD에서 추가 매수.
3. KRW 진짜 과포화: 필요한 만큼만 KRW→USD 이동(진짜 relocate, USD 여유 한도 내).
4. 양쪽 초과: 비례 축소(진짜 계좌 한계).

→ 기존 보유를 팔아 되사는 **왕복이 안 생김** → 상계는 안전망으로만 남음. relocate는 진짜 과포화분뿐이라
다음 실행 현재보유에 반영돼 **반복 안 됨(수렴)**.

## 구현 (portfolio.py)

- `route_accounts(econ, cur_k, cur_u, krw_room, usd_room)` 신설(모듈 함수, 단위테스트).
- `derive_account_weights` 재작성: 등가 그룹(config `reverse_synthetic.map` 기반: equity_etf↔SPY/QQQ·
  gold↔GLD·bond_krw↔IEF/SHY·cash↔SGOV)을 route_accounts로 배분. USD전용(commodity·MF·factor·
  developed·emerging)은 USD 채움(equity 부족분 equity_etf KRW 근사대체). KRW전용(sector·tips) KRW.
  잔여 USD→SGOV. `current_weights` 인자 추가(run.py `_compute_targets`→호출부 배선). 없으면(백테스트)
  현재보유 0으로 KRW우선 채움(기존 유사).
- 역합성 overflow 블록·bond_shortfall 흡수 로직 제거(route_accounts가 통합 처리). 상계(_net_equivalent_orders)·
  그룹drift는 안전망으로 유지.

## 검증

- route_accounts 단위 5케이스 통과(금 부족→KRW유지+USD증분·KRW여유시 KRW·초과→매도·KRW과포화→최소
  relocate·USD과포화→감축).
- 라이브 시나리오(USD45/KRW55·현재보유): 금 411060 유지(GLD 0, 왕복소멸), 주식 델타 SPY로, relocate 최소.
- 백테스트 2010~2025 drift: 4지표 회귀 없음.

## 후속

- 라이브에서 실제 교착 해소(현금부족·미체결·상계액 감소) 확인 — 다음 며칠 로그.
- reverse_synthetic.enabled 플래그는 이제 미사용(map만 그룹정의에 사용) — 정리 가능.
