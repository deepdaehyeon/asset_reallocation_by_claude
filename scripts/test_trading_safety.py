"""데이터 품질·주문 timeout·실패매도 안전장치 회귀 테스트.

실제 시세나 KIS API를 호출하지 않는다.
실행: python scripts/test_trading_safety.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "trading"))

from executor import KisRebalancer  # noqa: E402
from features import compute_feature_matrix  # noqa: E402
from fetcher import _merge_partial_fred_result  # noqa: E402
from regime import HmmRegimeClassifier  # noqa: E402
from run import _compute_trigger  # noqa: E402
from settlement import SettlementTracker  # noqa: E402


def _prices(vix9d_full: bool) -> pd.DataFrame:
    idx = pd.bdate_range("2025-01-01", periods=240)
    x = np.arange(len(idx), dtype=float)
    data = {
        "SPY": 500.0 + x * 0.2 + np.sin(x / 7),
        "^VIX": 18.0 + np.sin(x / 10),
        "HYG": 75.0 + x * 0.01,
        "TLT": 95.0 - x * 0.005,
        "DX-Y.NYB": 100.0 + np.sin(x / 20),
        "DJP": 30.0 + np.cos(x / 15),
    }
    vix9d = np.full(len(idx), np.nan)
    if vix9d_full:
        vix9d[:] = 17.0 + np.sin(x / 9)
    else:
        vix9d[-1] = 17.5
    data["^VIX9D"] = vix9d
    return pd.DataFrame(data, index=idx)


def test_optional_feature_cannot_collapse_matrix() -> None:
    matrix = compute_feature_matrix(_prices(False))
    assert len(matrix) >= 150, len(matrix)
    assert "vix_term_structure" not in matrix.columns

    full = compute_feature_matrix(_prices(True))
    assert "vix_term_structure" in full.columns


def test_partial_fred_cache_merge() -> None:
    merged, restored = _merge_partial_fred_result(
        {"hy_spread": 1.6, "curve_10y2y": 0.4},
        {"hy_spread": 1.7, "cpi_yoy": 2.3, "nfci": -0.2},
    )
    assert merged == {
        "hy_spread": 1.6,
        "curve_10y2y": 0.4,
        "cpi_yoy": 2.3,
        "nfci": -0.2,
    }
    assert restored == ["cpi_yoy", "nfci"]


class _Pending:
    def __init__(self, executed: int, rejected: bool = False):
        self.executed_qty = executed
        self.rejected = rejected


class _Order:
    def __init__(self, states, cancel_fails: bool = False):
        self.states = list(states)
        self.cancel_fails = cancel_fails
        self.cancel_calls = 0

    @property
    def pending_order(self):
        if len(self.states) > 1:
            return self.states.pop(0)
        return self.states[0]

    def cancel(self):
        self.cancel_calls += 1
        if self.cancel_fails:
            raise RuntimeError("cancel failed")


def _wait_harness() -> KisRebalancer:
    obj = KisRebalancer.__new__(KisRebalancer)
    obj.usd_krw = 1400.0
    obj.order_max_retries = 2
    obj.order_retry_interval_s = 0.02
    obj.order_timeout_s = 0.20
    obj.order_poll_interval_s = 0.005
    return obj


def test_partial_fill_reorders_only_remainder() -> None:
    obj = _wait_harness()
    first = _Order([_Pending(2)])
    reordered_qty: list[int] = []

    def reorder(_price: float, qty: int):
        reordered_qty.append(qty)
        return _Order([None])

    filled, _price, closed = obj._wait_for_fill(
        first, reorder, "TEST", "sell", 10, 100.0, "KRW",
        max_retries=2, retry_interval=0.01, max_wait_s=0.10, poll_interval_s=0.002,
    )
    assert (filled, closed) == (10, True)
    assert reordered_qty == [8], reordered_qty


def test_cancel_failure_never_reorders() -> None:
    obj = _wait_harness()
    order = _Order([_Pending(0)], cancel_fails=True)
    reordered_qty: list[int] = []
    filled, _price, closed = obj._wait_for_fill(
        order,
        lambda _p, q: reordered_qty.append(q),
        "TEST", "sell", 10, 100.0, "KRW",
        max_retries=1, retry_interval=0.01, max_wait_s=0.04, poll_interval_s=0.002,
    )
    assert filled == 0
    assert closed is False
    assert reordered_qty == []


def test_anchor_metric_ignores_numeric_state_permutation() -> None:
    clf = HmmRegimeClassifier(stabilize_mapping=True, mapping_deadband=0.3, fit_seeds=[42])
    clf._feature_cols = ["momentum_1m"]
    clf._last_state_centroids = {
        0: {"momentum_1m": 4.0},
        1: {"momentum_1m": 3.0},
        2: {"momentum_1m": 2.0},
        3: {"momentum_1m": 1.0},
        4: {"momentum_1m": 0.0},
    }
    labels = ["Goldilocks", "Reflation", "Slowdown", "Stagflation", "Goldilocks"]
    clf._anchor = [
        {"regime": labels[i], "centroid": {"momentum_1m": float(i)}}
        for i in range(5)
    ]
    raw = {s: "Slowdown" for s in range(5)}
    aligned = clf._align_to_anchor(raw)
    stats = clf.alignment_stats
    assert stats["compared"] == 5
    assert stats["accepted"] == 5
    assert stats["semantic_changes"] == 0
    assert aligned[0] == labels[4] and aligned[4] == labels[0]


def test_failed_sell_persists_and_triggers() -> None:
    tracker = SettlementTracker({})
    tracker.add_failed_sell("218420", 115, 2_346_000, "KRW", "timeout")
    saved = tracker.to_dict()
    assert saved["failed_sells"][0]["qty"] == 115

    config = {
        "risk": {"drawdown_thresholds": {"moderate": -0.20}},
        "rebalancing": {"min_rebalance_interval_days": 0, "drift_threshold": 0.05},
    }
    triggered, reason = _compute_trigger(
        0.0, False, 0.0, None, config, has_failed_sell=True
    )
    assert triggered and reason == "failed_sells"


def main() -> None:
    tests = [
        test_optional_feature_cannot_collapse_matrix,
        test_partial_fred_cache_merge,
        test_partial_fill_reorders_only_remainder,
        test_cancel_failure_never_reorders,
        test_anchor_metric_ignores_numeric_state_permutation,
        test_failed_sell_persists_and_triggers,
    ]
    for test in tests:
        test()
        print(f"  ✓ {test.__name__}")
    print(f"\n결과: {len(tests)} passed, 0 failed")


if __name__ == "__main__":
    main()
