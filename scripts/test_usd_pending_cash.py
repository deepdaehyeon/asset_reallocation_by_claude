"""USD 미결제 순매매 NAV 현금 계산 회귀 테스트.

실행:
  python scripts/test_usd_pending_cash.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "trading"))

from executor import _compute_usd_nav_cash  # noqa: E402


def _check(name: str, actual: float, expected: float) -> None:
    if not math.isclose(actual, expected, rel_tol=0.0, abs_tol=1e-9):
        raise AssertionError(f"{name}: expected {expected}, got {actual}")
    print(f"  ✓ {name}")


def main() -> None:
    cash, sell, buy = _compute_usd_nav_cash(
        100.0, 100.0, {"output2": [{"frcr_sll_amt_smtl": "1000", "frcr_buy_amt_smtl": "0"}]}
    )
    _check("매도만 있으면 gross 매도액 전부 반영", cash, 1100.0)
    _check("매도액 파싱", sell, 1000.0)
    _check("매수액 파싱", buy, 0.0)

    cash, _, _ = _compute_usd_nav_cash(
        100.0, 100.0, {"output2": {"frcr_sll_amt_smtl": "1000", "frcr_buy_amt_smtl": "900"}}
    )
    _check("매수·매도 동시 발생 시 순매도만 반영", cash, 200.0)

    cash, _, _ = _compute_usd_nav_cash(
        100.0, 100.0, {"output2": [{"frcr_sll_amt_smtl": "0", "frcr_buy_amt_smtl": "250"}]}
    )
    _check("매수만 있으면 미결제 매수 의무 차감", cash, -150.0)

    cash, _, _ = _compute_usd_nav_cash(100.0, 100.0, {"output2": [{}]})
    _check("미결제 필드가 없으면 기준 예수금 유지", cash, 100.0)

    # 2026-08-28 실계좌 회귀: 매도 $16,045.79와 매수 $15,289.49를 함께 처리해야 한다.
    cash, sell, buy = _compute_usd_nav_cash(
        586.94,
        586.94,
        {
            "output2": [{
                "frcr_sll_amt_smtl": "16045.790000",
                "frcr_buy_amt_smtl": "15289.490000",
            }]
        },
    )
    _check("2026-08-28 순매매 현금", cash, 1343.24)
    _check("2026-08-28 기존식 과대계상 제거", (586.94 + sell) - cash, buy)

    print("\n결과: 8 passed, 0 failed")


if __name__ == "__main__":
    main()
