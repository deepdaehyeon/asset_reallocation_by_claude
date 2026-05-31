"""
층 1 Step 1 — C안(Phase 1 수동조정) 엔드투엔드 A/B 재검증.

배경: regime_targets는 커밋 6d86b79에서 Phase 1 진단(historical Sharpe 랭킹)을
반영해 4개 레짐(Reflation/Slowdown/Stagflation/Crisis)을 수동 조정했다.
당시 커밋은 A/B(Sharpe 0.733→0.751)를 기록했으나, 그 이후 파이프라인이 크게
바뀌었다(drawdown scaling 제거 b923dd2, drift 5%→1.5%, regime_change_trigger
제거 38170e5, vol floor 0.50 등). → **현재 엔진 기준으로 재측정**한다.

방법: regime_targets만 교체(pre-C vs 현재)하고 나머지 config·엔진 파라미터는
현재 라이브 설정으로 동일하게 두어, Phase 1 조정의 순수 기여를 격리한다.
  - current : 현재 config.yaml (C안 적용 상태)
  - pre-C   : git show 6d86b79^:trading/config.yaml 의 regime_targets만 주입

층 0 발견과의 연결: Crisis의 equity_etf 0→10% 변경은 "분류 후행 V-shape 보상"
근거인데, 층 0은 그게 "잘못된 시점 베타 추가"일 수 있다고 경고했다. 따라서
집계 Sharpe뿐 아니라 **위기구간 낙폭**(COVID/Bear22)을 함께 본다.

코드 변경 없음 (시뮬레이션). 결과는 docs/에 저장.
"""
from __future__ import annotations

import subprocess
import sys
import warnings
from copy import deepcopy
from pathlib import Path
from typing import Dict

import pandas as pd
import yaml

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "trading"))
sys.path.insert(0, str(ROOT / "backtest"))

warnings.filterwarnings("ignore", message="Model is not converging.*")

from fetcher import fetch_fred_history  # noqa: E402
from data import load_all_prices  # noqa: E402
from engine import BacktestEngine  # noqa: E402
from metrics import compute_metrics  # noqa: E402

CONFIG_PATH = ROOT / "trading" / "config.yaml"
PRE_C_REF = "6d86b79^:trading/config.yaml"
START = "2010-01-01"
END = "2025-04-30"
REBAL_FREQ = "W-FRI"
TX_COST = 0.001

CRISIS_WINDOWS = {
    "COVID 2020": ("2020-02-19", "2020-04-30"),
    "Bear 2022": ("2022-01-01", "2022-12-31"),
}


def load_pre_c_regime_targets() -> dict:
    """커밋 6d86b79 직전 config의 regime_targets를 추출."""
    blob = subprocess.check_output(["git", "show", PRE_C_REF], cwd=ROOT, text=True)
    return yaml.safe_load(blob)["regime_targets"]


def _drift_threshold(config: dict) -> float:
    return float(config.get("rebalancing", {}).get("drift_threshold", 0.015))


def _cooldown(config: dict) -> int:
    return int(config.get("rebalancing", {}).get("min_rebalance_interval_days", 0))


def make_engine(config, universe_px, signal_px, fred_history) -> BacktestEngine:
    return BacktestEngine(
        config=config,
        universe_px=universe_px,
        signal_px=signal_px,
        start=START,
        end=END,
        rebal_freq=REBAL_FREQ,
        tx_cost=TX_COST,
        drift_threshold=_drift_threshold(config),
        cooldown_days=_cooldown(config),
        fred_history=fred_history,
    )


def crisis_maxdd(returns: pd.Series, start: str, end: str) -> float:
    r = returns[start:end]
    if r.empty:
        return float("nan")
    eq = (1.0 + r).cumprod()
    return float((eq / eq.cummax() - 1.0).min())


def risk_regime_defense(result: pd.DataFrame) -> Dict[str, float]:
    """위험레짐(Crisis/Stagflation/Slowdown) 체류일의 일평균 수익률 — 방어 성과."""
    out = {}
    for r in ("Crisis", "Stagflation", "Slowdown"):
        mask = result["regime"] == r
        out[r] = float(result.loc[mask, "returns"].mean()) if mask.any() else float("nan")
    return out


def main() -> None:
    with open(CONFIG_PATH) as f:
        current_config = yaml.safe_load(f)

    pre_c_targets = load_pre_c_regime_targets()
    pre_c_config = deepcopy(current_config)
    pre_c_config["regime_targets"] = pre_c_targets

    print(f"데이터 로딩 중 [{START} ~ {END}]...")
    universe_px, signal_px = load_all_prices(
        config=current_config, start=START, end=END, use_cache=True
    )
    fred_history = fetch_fred_history(START, END)
    print(f"  유니버스 {len(universe_px.columns)}종목 / {len(universe_px)}거래일")

    variants = {"pre-C (Phase1 이전)": pre_c_config, "current (C안 적용)": current_config}
    results: Dict[str, pd.DataFrame] = {}

    print("\n전략 실행 중...")
    for label, cfg in variants.items():
        print(f"  [{label}]")
        results[label] = make_engine(cfg, universe_px, signal_px, fred_history).run()

    rows = []
    for label, res in results.items():
        m = compute_metrics(res["returns"])
        defense = risk_regime_defense(res)
        rows.append({
            "variant": label,
            "CAGR": m.get("cagr", 0.0),
            "Sharpe": m.get("sharpe", 0.0),
            "MaxDD": m.get("max_drawdown", 0.0),
            "Calmar": m.get("calmar", 0.0),
            "COVID_DD": crisis_maxdd(res["returns"], *CRISIS_WINDOWS["COVID 2020"]),
            "Bear22_DD": crisis_maxdd(res["returns"], *CRISIS_WINDOWS["Bear 2022"]),
            "Crisis_d": defense["Crisis"],
            "Stagfl_d": defense["Stagflation"],
            "Slowdn_d": defense["Slowdown"],
        })
    df = pd.DataFrame(rows).set_index("variant")

    print(f"\n{'='*100}")
    print("  C안 A/B — Phase 1 regime_targets 수동조정의 엔드투엔드 기여 (현재 엔진 기준)")
    print(f"{'='*100}")
    hdr = (
        f"  {'variant':<22} {'CAGR':>7} {'Sharpe':>7} {'MaxDD':>8} {'Calmar':>7} "
        f"{'COVID':>8} {'Bear22':>8}"
    )
    print(hdr)
    print("  " + "─" * (len(hdr) - 2))
    for label, row in df.iterrows():
        print(
            f"  {label:<22} {row['CAGR']:>6.1%} {row['Sharpe']:>7.2f} "
            f"{row['MaxDD']:>7.1%} {row['Calmar']:>7.2f} "
            f"{row['COVID_DD']:>7.1%} {row['Bear22_DD']:>7.1%}"
        )

    print("\n  위험레짐 체류일 일평균 수익률 (방어 성과, 높을수록 좋음):")
    print(f"  {'variant':<22} {'Crisis':>10} {'Stagflation':>12} {'Slowdown':>10}")
    print("  " + "─" * 56)
    for label, row in df.iterrows():
        print(
            f"  {label:<22} {row['Crisis_d']:>+9.3%} "
            f"{row['Stagfl_d']:>+11.3%} {row['Slowdn_d']:>+9.3%}"
        )

    # 델타
    pre = df.loc["pre-C (Phase1 이전)"]
    cur = df.loc["current (C안 적용)"]
    print(f"\n{'='*100}")
    print("  델타 (current − pre-C)")
    print(f"{'='*100}")
    print(f"  Sharpe   {cur['Sharpe'] - pre['Sharpe']:+.3f}")
    print(f"  MaxDD    {(cur['MaxDD'] - pre['MaxDD'])*100:+.2f}pp  (양수=개선)")
    print(f"  Calmar   {cur['Calmar'] - pre['Calmar']:+.3f}")
    print(f"  CAGR     {(cur['CAGR'] - pre['CAGR'])*100:+.2f}pp")
    print(f"  COVID DD {(cur['COVID_DD'] - pre['COVID_DD'])*100:+.2f}pp  (양수=얕아짐)")
    print(f"  Bear22DD {(cur['Bear22_DD'] - pre['Bear22_DD'])*100:+.2f}pp")

    return df


if __name__ == "__main__":
    main()
