"""일일 회전율(turnover) Slack 보고.

2026-06-18 executor 잔고부족 재시도 수정 후 엔진을 고정하고, 한 달간(~2026-07-19)
예상대로 회전(거래 빈도/규모)되는지 확인하기 위한 한시적 모니터링.
"""
import csv
import json
import sqlite3
from datetime import date
from pathlib import Path

from messenger import Messenger

BASE = Path(__file__).parent
STATE_FILE = BASE / "state.json"
STATE_DB = BASE / "state.db"
ORDERS_LOG = BASE / "logs" / "orders.csv"

REPORT_END = date(2026, 7, 19)  # 1개월 모니터링 종료일 — 이후 조용히 스킵


def _load_state() -> dict:
    if STATE_DB.exists():
        con = sqlite3.connect(STATE_DB)
        try:
            rows = con.execute("SELECT key, value FROM state_current").fetchall()
        finally:
            con.close()
        flat = {k: json.loads(v) for k, v in rows}
        state = flat.get("__root__", {})
        if isinstance(state, dict):
            return state
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}


def main() -> None:
    today = date.today()
    if today > REPORT_END:
        return

    state = _load_state()
    nav = float(state.get("last_total_all_krw") or state.get("last_total_krw") or 0.0)
    monthly_traded = float(state.get("monthly_traded_krw", 0.0))
    monthly_ym = state.get("monthly_ym", "")

    daily_traded = 0.0
    n_buy = n_sell = 0
    if ORDERS_LOG.exists():
        with open(ORDERS_LOG) as f:
            for row in csv.DictReader(f):
                if row.get("status") != "ok":
                    continue
                if not row.get("datetime", "").startswith(today.isoformat()):
                    continue
                daily_traded += abs(float(row.get("amount_krw", 0) or 0))
                if row.get("action") == "buy":
                    n_buy += 1
                elif row.get("action") == "sell":
                    n_sell += 1

    daily_turnover = daily_traded / nav if nav else 0.0
    monthly_turnover = monthly_traded / nav if nav else 0.0

    Messenger().send_turnover_report(
        today=today.isoformat(),
        nav=nav,
        daily_traded=daily_traded,
        daily_turnover=daily_turnover,
        n_buy=n_buy,
        n_sell=n_sell,
        monthly_ym=monthly_ym,
        monthly_traded=monthly_traded,
        monthly_turnover=monthly_turnover,
    )


if __name__ == "__main__":
    main()
