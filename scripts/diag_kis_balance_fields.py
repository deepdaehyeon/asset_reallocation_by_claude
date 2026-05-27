"""
KIS 잔고 조회 raw output2 모든 필드를 출력 — T+2 결제 반영 필드 식별용.

실행:
  python scripts/diag_kis_balance_fields.py

목적: pykis가 deposit.amount = dnca_tot_amt로만 노출하는데,
이 필드는 당일 매도대금이 결제 전이라 잔고에 안 들어감.
MTS에서 보이는 실제 총자산과 일치하는 필드를 찾는다.

KIS Open API 국내주식 잔고조회(TR_ID: TTTC8434R) output2 주요 필드:
  - dnca_tot_amt       : 예수금총금액 (T+0 — 매도대금 미반영)  ← pykis가 노출
  - nxdy_excc_amt      : 익영업일정산금액 (T+1)
  - prvs_rcdl_excc_amt : 가수도정산금액 (T+2 — 매도대금 포함)
  - thdt_buy_amt       : 금일매수금액
  - thdt_sll_amt       : 금일매도금액
  - tot_evlu_amt       : 총평가금액 (주식 + 예수금 — MTS의 '총자산')
  - scts_evlu_amt      : 유가증권평가금액
  - nass_amt           : 순자산금액
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "trading"))

import pykis  # noqa: E402

AUTH_PATH = ROOT / "trading" / "auth.yaml"
CONFIG_PATH = ROOT / "trading" / "config.yaml"


def main():
    with open(AUTH_PATH) as f:
        auth = yaml.safe_load(f)
    with open(CONFIG_PATH) as f:
        config = yaml.safe_load(f)

    seen = set()
    for acc_name, acc_cfg in config["accounts"].items():
        acc_no = acc_cfg["acc_no"]
        currency = acc_cfg["currency"]
        key = (acc_no, currency)
        if key in seen:
            continue
        seen.add(key)

        creds = auth[acc_no]
        client = pykis.PyKis(
            id=creds["id"],
            appkey=creds["appkey"],
            secretkey=creds["secretkey"],
            account=acc_no,
            keep_token=True,
        )

        country = "KR" if currency == "KRW" else "US"
        print(f"\n{'='*70}")
        print(f"  계좌 {acc_name} ({acc_no}, country={country})")
        print(f"{'='*70}")

        try:
            bal = client.account().balance(country=country)
        except Exception as e:
            print(f"  [실패] {type(e).__name__}: {e}")
            continue

        raw = bal.raw()
        if raw is None:
            print("  [경고] raw response 없음")
            continue

        # output1은 종목 리스트, output2가 잔고 합계 필드
        out2 = raw.get("output2")
        if isinstance(out2, list) and out2:
            out2 = out2[0]  # 보통 list[0]에 잔고 합계
        if not isinstance(out2, dict):
            print("  [경고] output2 dict 아님:", type(out2))
            continue

        # 금액 관련 필드만 추출 (이름에 amt/balance 등 포함)
        keys_money = []
        for k, v in sorted(out2.items()):
            try:
                fv = float(v) if v not in ("", None) else 0.0
            except (ValueError, TypeError):
                continue
            keys_money.append((k, fv, v))

        print(f"  output2 필드 ({len(keys_money)}개 숫자형):")
        print(f"  {'-'*68}")
        for k, fv, raw_v in keys_money:
            marker = ""
            if k in ("dnca_tot_amt", "nxdy_excc_amt", "prvs_rcdl_excc_amt",
                     "tot_evlu_amt", "scts_evlu_amt", "nass_amt",
                     "thdt_buy_amt", "thdt_sll_amt"):
                marker = "  ⭐"
            print(f"    {k:<28} {fv:>20,.2f}  ({raw_v}){marker}")

        # pykis가 보는 값과 비교
        print(f"\n  pykis가 노출하는 값:")
        try:
            for cur, dep in bal.deposits.items():
                print(f"    deposit[{cur}].amount             = {float(dep.amount):>20,.2f}")
                print(f"    deposit[{cur}].withdrawable_amount = {float(dep.withdrawable_amount):>20,.2f}")
                print(f"    deposit[{cur}].exchange_rate       = {float(dep.exchange_rate):>20,.4f}")
        except Exception as e:
            print(f"    [경고] deposit 추출 실패: {e}")

        # 보유종목 합 계산
        try:
            current_sum = float(bal.current_amount)
            purchase_sum = float(bal.purchase_amount)
            total_amount = float(bal.amount)
            print(f"\n  pykis가 계산하는 합:")
            print(f"    balance.current_amount (보유종목 평가합 KRW) = {current_sum:>20,.2f}")
            print(f"    balance.purchase_amount (보유종목 매입합 KRW) = {purchase_sum:>20,.2f}")
            print(f"    balance.amount (= current + 예수금)            = {total_amount:>20,.2f}")
        except Exception as e:
            print(f"    [경고] balance.amount 추출 실패: {e}")

        # 종목별 평가금액 — 이중계산 여부 확인
        out1 = raw.get("output1")
        if isinstance(out1, list) and out1:
            print(f"\n  보유종목 (output1) {len(out1)}건:")
            total_evlu = 0.0
            total_pchs = 0.0
            for stock in out1:
                # 국내/해외 필드명 다름
                sym = stock.get("pdno") or stock.get("ovrs_pdno") or "?"
                qty = stock.get("hldg_qty") or stock.get("ovrs_cblc_qty") or 0
                evlu = stock.get("evlu_amt") or stock.get("ovrs_stck_evlu_amt") or stock.get("frcr_evlu_amt2") or 0
                pchs = stock.get("pchs_amt") or stock.get("frcr_pchs_amt1") or stock.get("frcr_pchs_amt") or 0
                try:
                    evlu_f = float(evlu) if evlu else 0.0
                    pchs_f = float(pchs) if pchs else 0.0
                except (ValueError, TypeError):
                    evlu_f = pchs_f = 0.0
                total_evlu += evlu_f
                total_pchs += pchs_f
                print(f"    {sym:<12} qty={qty:<8} 평가={evlu_f:>18,.2f}  매입={pchs_f:>18,.2f}")
            print(f"  {'-'*68}")
            print(f"    {'합계':<12} {'':<13} 평가={total_evlu:>18,.2f}  매입={total_pchs:>18,.2f}")


if __name__ == "__main__":
    main()
