"""S&P500(SPY) buy-and-hold 벤치마크 대비 알파 추적.

"같은 돈을 SPY에 넣었더라면" 방식(money-weighted): 벤치마크가 SPY 주식을 들고 있다고 보고,
입금하면 그 돈으로 SPY를 더 사고 출금하면 SPY를 판 것으로 처리(입출금 net_flow 반영) → 입금 많은
포트폴리오가 유리하게 왜곡되는 걸 막는다. 매 실행마다 알파 = 실제 총자산 − 벤치마크 가치.

앵커: 최초 실행 시 그날 총자산으로 시작(알파 0), 이후 벌어지는 차이를 추적(2026-07-10 사용자 A안).
2026-07-10 신설. 매매 로직 미영향(순수 리포트).
"""
from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Optional

_CSV = Path(__file__).parent.parent / "docs" / "_benchmark_history.csv"


def update_benchmark(
    state: dict,
    total_all_krw: float,
    spy_krw: float,
    net_flow_krw: float = 0.0,
) -> Optional[dict]:
    """벤치마크 갱신 후 알파 정보 반환. spy_krw<=0(가격 조회 실패)이면 None(스킵)."""
    if not spy_krw or spy_krw <= 0 or not total_all_krw or total_all_krw <= 0:
        return None

    shares = state.get("bench_spy_shares")
    if not shares:
        # 최초: 오늘 총자산으로 앵커
        shares = total_all_krw / spy_krw
        state["bench_spy_shares"] = shares
        state["bench_inception_at"] = datetime.now().isoformat()
        state["bench_start_value"] = float(total_all_krw)
        info = {"total": float(total_all_krw), "bench_value": float(total_all_krw),
                "alpha": 0.0, "alpha_pct": 0.0, "inception": True}
    else:
        # 입출금 반영: 입금하면 SPY 더 사고, 출금하면 SPY 판 것으로
        if net_flow_krw and abs(net_flow_krw) > 0:
            shares = float(shares) + net_flow_krw / spy_krw
            state["bench_spy_shares"] = shares
        bench_value = float(shares) * spy_krw
        alpha = total_all_krw - bench_value
        alpha_pct = alpha / bench_value if bench_value > 0 else 0.0
        info = {"total": float(total_all_krw), "bench_value": bench_value,
                "alpha": alpha, "alpha_pct": alpha_pct, "inception": False}

    _append_csv(info)
    return info


def _append_csv(info: dict) -> None:
    try:
        new = not _CSV.exists()
        with open(_CSV, "a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["datetime", "total_krw", "benchmark_krw", "alpha_krw", "alpha_pct"])
            w.writerow([datetime.now().isoformat(), f"{info['total']:.0f}",
                        f"{info['bench_value']:.0f}", f"{info['alpha']:.0f}",
                        f"{info['alpha_pct']:.4f}"])
    except Exception:
        pass


def format_alpha_line(info: Optional[dict]) -> str:
    """리포트용 한 줄(plain). info 없으면 빈 문자열."""
    if not info:
        return ""
    if info.get("inception"):
        return "S&P500 벤치마크: 오늘부터 추적 시작 (알파 0)"
    sign = "+" if info["alpha"] >= 0 else ""
    return (f"S&P500였다면 {info['bench_value']:,.0f}원 · 실제 {info['total']:,.0f}원 · "
            f"알파 {sign}{info['alpha']:,.0f}원 ({sign}{info['alpha_pct']:.1%})")
