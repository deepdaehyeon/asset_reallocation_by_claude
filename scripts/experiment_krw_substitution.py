"""
실험: USD→KRW ETF 대체의 수익 영향 측정.

대체(2026-06-12 사용자 결정):
  - VTIP  → 468370 (KODEX iShares 미국인플레이션국채액티브) — 프록시 TIP (broad TIPS)
  - XLE   → 218420 (KODEX 미국S&P500에너지 합성)          — 프록시 XLE (동일 지수)
  - NVDA  → 381180 (TIGER 미국필라델피아반도체나스닥 SOX) — 프록시 SOXX (개별주→바스켓)

백테스트는 자산군 단위 + USD 단일통화라, derive_account_weights의 통화 라우팅(자산군
하드코딩)은 *수익*에 영향을 주지 않는다. 대체의 수익 차이는 오직 기초자산(프록시) 변화에서
온다:
  - 에너지: 218420은 XLE와 같은 S&P500 에너지 지수 → 프록시 동일 → 수익 영향 0(순수 배관).
  - TIPS:  468370(broad)은 VTIP(short)보다 듀레이션 김 → TIP 프록시로 듀레이션 차이만.
  - NVDA:  단일주 NVDA → 반도체 30종 바스켓(SOXX) → **전략 변경**(알파→섹터 베타). 비용 측정.

변형:
  1) baseline           : 현행(VTIP/XLE/NVDA)
  2) sub_clean          : 에너지+TIPS만 대체(배관) — 동등성 확인
  3) sub_all            : 에너지+TIPS+NVDA 대체 — NVDA 비용 포함

코드 변경 없음(config 복사 + PROXY_MAP 임시 확장). 결과는 docs/에 저장.
"""
from __future__ import annotations

import copy
import sys
import warnings
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "trading"))
sys.path.insert(0, str(ROOT / "backtest"))
sys.path.insert(0, str(ROOT / "scripts"))

warnings.filterwarnings("ignore", message="Model is not converging.*")

import data as bt_data  # noqa: E402
from data import load_all_prices  # noqa: E402
from engine import BacktestEngine  # noqa: E402
from fetcher import fetch_fred_history  # noqa: E402
from metrics import compute_metrics, rolling_cagr  # noqa: E402
from prototype_forward_regime_predictability import run_engine  # noqa: E402
from compare_rule_timing_ab import START, END  # noqa: E402

# 새 KRW 티커 → 백테스트 프록시(기초지수 추종 미국 ETF)
NEW_PROXIES = {
    "468370": "TIP",   # broad US TIPS (VTIP=short과 듀레이션 다름)
    "218420": "XLE",   # S&P500 에너지 — XLE와 동일 지수
    "381180": "SOXX",  # 필라델피아 반도체(SOX) — NVDA 단일주 대체
}


def sub_config(base, swaps):
    """swaps: {old_ticker: (new_ticker, asset_class)} — universe·asset_routing 치환."""
    cfg = copy.deepcopy(base)
    for old, (new, acls) in swaps.items():
        # universe: old 제거, new 추가(KRW)
        meta = cfg["universe"].pop(old)
        cfg["universe"][new] = {"name": new, "currency": "KRW", "asset_class": acls}
        # asset_routing: 같은 split 비중으로 티커만 교체
        rt = cfg["asset_routing"].get(acls, {})
        if old in rt:
            rt[new] = rt.pop(old)
    return cfg


def row(label, res):
    r = res["returns"].dropna()
    m = compute_metrics(r)
    rc3 = rolling_cagr(r, years=3.0)
    return {
        "전략": label,
        "CAGR": m.get("cagr", 0.0), "MaxDD": m.get("max_drawdown", 0.0),
        "Sharpe": m.get("sharpe", 0.0), "Ulcer": m.get("ulcer", 0.0),
        "Martin": m.get("martin", 0.0), "r3w": rc3["worst"], "r3m": rc3["median"],
    }


def main():
    # PROXY_MAP 임시 확장 (실험 한정)
    bt_data.PROXY_MAP.update(NEW_PROXIES)

    with open(ROOT / "trading" / "config.yaml") as f:
        base = yaml.safe_load(f)

    clean_swaps = {"VTIP": ("468370", "bond_tips"), "XLE": ("218420", "equity_sector")}
    all_swaps = dict(clean_swaps, **{"NVDA": ("381180", "equity_individual")})

    cfg_clean = sub_config(base, clean_swaps)
    cfg_all = sub_config(base, all_swaps)

    print(f"데이터 로딩 [{START} ~ {END}]...")
    # 모든 변형이 쓰는 티커를 한 번에 받기 위해 union universe로 로드
    union = copy.deepcopy(base)
    for t, m in {**{"468370": ("bond_tips",), "218420": ("equity_sector",),
                    "381180": ("equity_individual",)}}.items():
        union["universe"][t] = {"name": t, "currency": "KRW", "asset_class": m[0]}
    universe_px, signal_px = load_all_prices(config=union, start=START, end=END, use_cache=True)
    fred_history = fetch_fred_history(START, END)

    rows = []
    print("baseline...")
    rows.append(row("baseline(현행)", run_engine(BacktestEngine, universe_px, signal_px, fred_history, base)))
    print("sub_clean(에너지+TIPS)...")
    rows.append(row("sub_clean(E+TIPS)", run_engine(BacktestEngine, universe_px, signal_px, fred_history, cfg_clean)))
    print("sub_all(+NVDA→SOX)...")
    rows.append(row("sub_all(+NVDA)", run_engine(BacktestEngine, universe_px, signal_px, fred_history, cfg_all)))

    print(f"\n{'='*92}\n  USD→KRW 대체의 수익 영향 ({START}~{END})\n{'='*92}")
    h = (f"  {'전략':>18}{'CAGR':>8}{'MaxDD':>9}{'Sharpe':>8}{'Ulcer':>8}{'Martin':>8}{'3y최악':>8}{'3y중앙':>8}")
    print(h)
    print("  " + "─" * (len(h) + 6))
    for r in rows:
        print(f"  {r['전략']:>18}{r['CAGR']:>8.1%}{r['MaxDD']:>8.1%}{r['Sharpe']:>8.2f}"
              f"{r['Ulcer']:>8.2f}{r['Martin']:>8.2f}{r['r3w']:>8.1%}{r['r3m']:>8.1%}")

    print("\n  에너지(218420)는 XLE와 동일 지수 → sub_clean의 차이는 사실상 TIPS 듀레이션뿐.")
    print("  sub_all - sub_clean 차이 = NVDA→반도체바스켓(SOX) 전략 변경 비용.")


if __name__ == "__main__":
    main()
