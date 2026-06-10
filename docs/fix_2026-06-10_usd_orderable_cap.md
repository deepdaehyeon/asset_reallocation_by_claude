# 수정: USD 매수 "주문가능금액 초과" — orderable cap을 USD 경로에도 적용

- 날짜: 2026-06-10
- 파일: `trading/executor.py`
- 증상: USD 리밸런싱(23:30)에서 잔여 매수가 KIS에 거부.
  - 06/08 `buy VWO 21주 @ 58.47` → `(RT_CD:7, APBK0952) 주문가능금액을 초과 했습니다`
  - 06/09 `buy VWO 29주 @ 58.87` → 동일 거부 (이틀 연속, 둘 다 마지막 잔여 매수 VWO)
  - 결과: `[매수 실패] USD 1건 → 다음 실행 시 합성 노출로 대체` (한 종목 미집행)

## 원인

KRW 경로는 매도 직후 `_fetch_krw_orderable`로 KIS 주문가능금액을 조회해 매수를
스케일다운하는 cap이 있었으나(2026-06-08 수정), **USD 경로에는 동일 cap이 없었다.**
USD 매수는 `target_usd × total_usd_krw` 스냅샷으로 그대로 집행 →
매도 미체결·ask 슬리피지·환전 증거금이 누적되어 마지막 잔여 매수(VWO)가 한도 초과.

`rebalance()` Phase 3에서 USD 매수는 `scaled_buy_orders.append((t, c, a, acc))`로
cap 없이 직접 추가되고 있었음(KRW만 `_fetch_krw_orderable` 스케일 적용).

## 수정

KRW와 동일 패턴을 USD에 미러링:

1. `get_portfolio_state`에서 `self._usd_cash_krw`(USD 출금가능현금 원화환산) 저장 — 폴백용.
2. `_fetch_usd_orderable(acc_name, ref_ticker)` 추가:
   - `stock.orderable_amount(price=ask)` → 해외 응답의
     `max_ord_psbl_qty`(quantity) × ask, `ovrs_ord_psbl_amt`(amount) 중 작은 값.
   - 둘 다 KIS가 매수증거금·환전·수수료를 반영해 자체 계산한 권위값 = KIS가 수락/거부에
     쓰는 바로 그 기준이므로, 이 값으로 cap하면 초과 거부가 발생하지 않음.
   - `× usd_krw × 0.98`(정수주 반올림·슬리피지 여유). 조회 실패 시 `_usd_cash_krw × 0.98` 폴백.
3. `rebalance()` Phase 3에서 USD 매수도 acc별로 묶어 `_fetch_usd_orderable` cap 적용 후
   `total_buy > orderable`이면 비례 스케일다운(KRW 코드와 동일 구조).

## 검증·주의

- 코드 syntax/임포트 OK. pykis `KisForeignOrderableAmount.amount/quantity` 필드 존재 확인.
- 실주문 미실행(사용자 확인 규칙). 다음 정규 USD 실행(23:30)에서 자연 검증되며,
  cap이 과하게 작으면 일부 매수가 deferred → 기존 `failed_buys/합성 노출` 메커니즘이 흡수(안전 방향).
- API 조회 실패 시 폴백은 매도 전 현금만 반영(보수적, 작게 잡음) → 초과보다 미달 쪽으로 안전.
