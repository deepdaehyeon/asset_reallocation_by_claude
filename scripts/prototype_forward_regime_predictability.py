"""
프로토타입: 미래 레짐(forward-return ground truth) 예측 가능성 — 엄격한 walk-forward.

질문: 오라클 천장(experiment_2026-06-10_oracle_regime_ceiling.md)이 보여준 알파가
    "현재 분류" 대신 "미래 레짐 예측"으로 실제 실현 가능한가? 아니면 과적합인가?

설계 (누수 차단이 핵심):
  - ground-truth 라벨 y_t = argmax_regime (해당 레짐 타겟 포트폴리오의 t→t+H forward 수익률).
    = 오라클 라벨. look-ahead라 t+H 이후에야 확정.
  - 피처 X_t = compute_feature_matrix (전부 trailing·causal, FRED는 publication-lag 적용).
  - walk-forward: 매월 RF 재학습. 학습셋은 **라벨이 완전히 실현된 표본만**(s+H < 월시작).
    그 달의 각 영업일을 OOS 예측. 미래 정보 일절 사용 안 함.

결정적 비교:
  1) forward-RF가 "현재 룰 분류(라이브가 하는 것)"보다 오라클 라벨을 OOS로 더 잘 맞히나?
  2) 그 OOS 예측으로 매매하면 baseline(10.4%·Sharpe 0.84)을 이기나?
둘 다 아니면 → 천장 갭은 더 나은 예측기로 못 닫는 노이즈(과적합).

코드 변경 없음(시뮬레이션). 결과는 docs/에 저장.
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "trading"))
sys.path.insert(0, str(ROOT / "backtest"))
sys.path.insert(0, str(ROOT / "scripts"))

warnings.filterwarnings("ignore", message="Model is not converging.*")

from sklearn.ensemble import RandomForestClassifier  # noqa: E402
from sklearn.metrics import accuracy_score, f1_score, matthews_corrcoef  # noqa: E402

from data import load_all_prices  # noqa: E402
from features import compute_feature_matrix  # noqa: E402
from regime import REGIMES, detect_regime  # noqa: E402
from engine import BacktestEngine  # noqa: E402
from metrics import compute_metrics  # noqa: E402
from compare_rule_timing_ab import START, END, REBAL_FREQ, TX_COST, crisis_maxdd  # noqa: E402

CRISIS_WINDOWS = {"COVID 2020": ("2020-02-19", "2020-04-30"),
                  "Bear 2022": ("2022-01-01", "2022-12-31")}
H = 21                       # 라벨 forward horizon (오라클 sweet spot)
TRAIN_MIN = 252 * 3          # 최소 학습 표본(라벨 실현분)
RF_KW = dict(n_estimators=300, max_depth=6, min_samples_leaf=20,
             class_weight="balanced", random_state=0, n_jobs=-1)


def regime_ticker_weights(config):
    rt, routing = config["regime_targets"], config["asset_routing"]
    out = {}
    for reg in REGIMES:
        tw = {}
        for ac, w in rt[reg].items():
            for tk, sub in routing.get(ac, {}).items():
                tw[tk] = tw.get(tk, 0.0) + w * sub
        out[reg] = tw
    return out


def oracle_labels(universe_px, reg_tw, index):
    """y_t = argmax_regime 가중 forward 수익률(H). 미실현(끝쪽 H일)은 NaN."""
    fwd = universe_px.shift(-H) / universe_px - 1.0
    fwd = fwd.reindex(index).copy()
    fwd.iloc[-H:] = np.nan  # forward 윈도우 미완성 구간 라벨 무효
    y = pd.Series(index=index, dtype=object)
    for t in index:
        row = fwd.loc[t]
        best, best_r = None, -1e18
        for reg, tw in reg_tw.items():
            num = wsum = 0.0
            for tk, w in tw.items():
                v = row.get(tk)
                if v is not None and v == v:
                    num += w * v
                    wsum += w
            if wsum > 0:
                r = num / wsum
                if r > best_r:
                    best_r, best = r, reg
        y.loc[t] = best
    return y


def rule_regime_series(X):
    """각 시점 피처행으로 detect_regime — 라이브가 하는 '현재 분류' 베이스라인."""
    return pd.Series({t: detect_regime(X.loc[t].to_dict()) for t in X.index})


def walk_forward_predict(X, y):
    """매월 재학습, 라벨 실현분만 학습, 각 영업일 OOS 예측."""
    pos = {d: i for i, d in enumerate(X.index)}
    months = pd.Series(X.index, index=X.index).dt.to_period("M")
    preds = {}
    eval_months = sorted(set(months[months.index >= X.index[TRAIN_MIN]]))
    feat_cols = list(X.columns)
    for m in eval_months:
        test_dates = X.index[months == m]
        cutoff_pos = pos[test_dates[0]]
        train_mask = [d for d in X.index
                      if pos[d] + H < cutoff_pos and y.loc[d] is not None and y.loc[d] == y.loc[d]]
        if len(train_mask) < TRAIN_MIN:
            continue
        Xtr = X.loc[train_mask, feat_cols]
        ytr = y.loc[train_mask].astype(str)
        clf = RandomForestClassifier(**RF_KW).fit(Xtr.values, ytr.values)
        yhat = clf.predict(X.loc[test_dates, feat_cols].values)
        for d, p in zip(test_dates, yhat):
            preds[d] = p
    return pd.Series(preds)


def cls_metrics(name, y_true, y_pred):
    return {
        "예측기": name,
        "정확도": accuracy_score(y_true, y_pred),
        "MacroF1": f1_score(y_true, y_pred, average="macro", zero_division=0),
        "MCC": matthews_corrcoef(y_true, y_pred),
    }


class SeriesRegimeEngine(BacktestEngine):
    """_get_regime을 미리 계산한 레짐 시리즈로 대체(one-hot). HMM 학습 없음."""
    def set_regime_series(self, s):
        self._reg_series = s.sort_index()
    def _get_regime(self, as_of):
        idx = self._reg_series.index
        sub = self._reg_series[idx <= as_of]
        reg = sub.iloc[-1] if len(sub) else "Slowdown"
        if reg not in REGIMES:
            reg = "Slowdown"
        blend = {r: 0.0 for r in REGIMES}
        blend[reg] = 1.0
        return reg, blend, reg, 1.0, 1.0, 1.0


def run_engine(cls, universe_px, signal_px, fred_history, config, setup=None):
    rb = config.get("rebalancing", {})
    eng = cls(config=config, universe_px=universe_px, signal_px=signal_px,
              start=START, end=END, rebal_freq=REBAL_FREQ, tx_cost=TX_COST,
              drift_threshold=float(rb.get("drift_threshold", 0.015)),
              cooldown_days=int(rb.get("min_rebalance_interval_days", 0)),
              fred_history=fred_history)
    if setup:
        setup(eng)
    return eng.run()


def bt_row(label, res):
    m = compute_metrics(res["returns"])
    return {"전략": label, "CAGR": m.get("cagr", 0.0), "Sharpe": m.get("sharpe", 0.0),
            "MaxDD": m.get("max_drawdown", 0.0), "Calmar": m.get("calmar", 0.0),
            "리밸": int(res["rebalanced"].sum()), "tx누적": float(res["tx_cost"].sum()),
            "COVID": crisis_maxdd(res["returns"], *CRISIS_WINDOWS["COVID 2020"]),
            "Bear22": crisis_maxdd(res["returns"], *CRISIS_WINDOWS["Bear 2022"])}


def main():
    from fetcher import fetch_fred_history
    with open(ROOT / "trading" / "config.yaml") as f:
        base = yaml.safe_load(f)
    print(f"데이터 로딩 [{START} ~ {END}]... (H={H}, RF={RF_KW})")
    universe_px, signal_px = load_all_prices(config=base, start=START, end=END, use_cache=True)
    fred_history = fetch_fred_history(START, END)

    print("피처 행렬·오라클 라벨 생성 중...")
    X = compute_feature_matrix(signal_px, fred_history).loc[START:END]
    reg_tw = regime_ticker_weights(base)
    y = oracle_labels(universe_px, reg_tw, X.index)
    rule = rule_regime_series(X)

    print("walk-forward OOS 예측 중(매월 재학습)...")
    pred = walk_forward_predict(X, y)

    # 평가: 라벨 실현 + 예측 존재하는 시점만
    ev = [d for d in pred.index if y.loc[d] is not None and y.loc[d] == y.loc[d]]
    yt = y.loc[ev].astype(str)
    rows = [
        cls_metrics("forward-RF (OOS)", yt, pred.loc[ev].astype(str)),
        cls_metrics("현재 룰 분류", yt, rule.loc[ev].astype(str)),
        cls_metrics("majority(최빈)", yt, pd.Series(yt.mode()[0], index=yt.index)),
    ]
    agree = (pred.loc[ev].astype(str).values == rule.loc[ev].astype(str).values).mean()
    print(f"\n{'='*72}\n  [1] OOS 분류 스킬 — 오라클 라벨(H={H}) 예측  (평가 {len(ev)}일)\n{'='*72}")
    print(f"  {'예측기':>18}{'정확도':>9}{'MacroF1':>9}{'MCC':>8}")
    print("  " + "─" * 46)
    for r in rows:
        print(f"  {r['예측기']:>18}{r['정확도']:>9.1%}{r['MacroF1']:>9.3f}{r['MCC']:>8.3f}")
    print(f"\n  forward-RF ↔ 현재룰 일치율: {agree:.1%}")
    print(f"  오라클 라벨 분포: "
          + ", ".join(f"{k}={v:.0%}" for k, v in yt.value_counts(normalize=True).items()))

    # [2] 실현 가능 백테스트: OOS 예측(예측 없으면 룰 폴백)을 레짐으로 사용
    reg_series = pd.Series(index=X.index, dtype=object)
    reg_series.loc[rule.index] = rule.values
    reg_series.loc[pred.index] = pred.values
    print("\nbaseline(현행) 백테스트...")
    bt = [bt_row("baseline(현행)", run_engine(BacktestEngine, universe_px, signal_px, fred_history, base))]
    print("forward-RF(OOS) 백테스트...")
    bt.append(bt_row("forward-RF(OOS)",
                     run_engine(SeriesRegimeEngine, universe_px, signal_px, fred_history, base,
                                setup=lambda e: e.set_regime_series(reg_series))))
    print(f"\n{'='*94}\n  [2] 실현 가능 백테스트 — OOS forward 예측 vs 현행\n{'='*94}")
    hdr = (f"  {'전략':>16}{'CAGR':>8}{'Sharpe':>8}{'MaxDD':>9}{'Calmar':>8}"
           f"{'리밸':>7}{'tx누적':>9}{'COVID':>9}{'Bear22':>9}")
    print(hdr); print("  " + "─" * (len(hdr) + 4))
    for r in bt:
        print(f"  {r['전략']:>16}{r['CAGR']:>8.1%}{r['Sharpe']:>8.2f}{r['MaxDD']:>8.1%}"
              f"{r['Calmar']:>8.2f}{int(r['리밸']):>7}{r['tx누적']:>8.2%}"
              f"{r['COVID']:>8.1%}{r['Bear22']:>8.1%}")


if __name__ == "__main__":
    main()
