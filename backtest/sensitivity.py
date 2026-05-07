"""
파라미터 민감도 분석.

목적: 결과가 특정 파라미터에 과도하게 민감하지 않음을 확인.
     비중 최적화(과적합)가 아닌 로버스트니스 검증 도구.

검증 원칙:
  - 파라미터를 ±20~40% 범위에서 변화
  - 각 변화에서 CAGR / Sharpe / MaxDD를 기록
  - 결과 범위(max - min)가 작을수록 로버스트한 파라미터
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

import pandas as pd

from engine import BacktestEngine
from metrics import compute_metrics


# 민감도 테스트 파라미터 정의 (config 경로 → 테스트값 목록)
SENSITIVITY_PARAMS: Dict[str, dict] = {
    "hmm_predict_lookback": {
        "description": "HMM 추론 시퀀스 길이 — 거래일 (기본 60일)",
        "path":   ["hmm", "predict_lookback"],
        "values": [10, 20, 40, 60, 90, 120],
        "base":   60,
    },
    "vol_target": {
        "description": "변동성 타겟팅 목표 변동성 (기본 10%)",
        "path":   ["vol_targeting", "target_vol"],
        "values": [0.08, 0.09, 0.10, 0.11, 0.12],
        "base":   0.10,
    },
    "hmm_override_threshold": {
        "description": "HMM 앙상블 override 임계값 (기본 60%)",
        "path":   ["hmm", "override_threshold"],
        "values": [0.50, 0.55, 0.60, 0.65, 0.70],
        "base":   0.60,
    },
    "mild_drawdown": {
        "description": "Mild 드로우다운 임계값 (기본 -10%)",
        "path":   ["risk", "drawdown_thresholds", "mild"],
        "values": [-0.08, -0.09, -0.10, -0.11, -0.12],
        "base":   -0.10,
    },
    "moderate_drawdown": {
        "description": "Moderate 드로우다운 임계값 (기본 -20%)",
        "path":   ["risk", "drawdown_thresholds", "moderate"],
        "values": [-0.15, -0.18, -0.20, -0.22, -0.25],
        "base":   -0.20,
    },
    "class_max_managed_futures": {
        "description": "Managed Futures 최대 비중 상한 (기본 12%)",
        "path":   ["class_max_weight", "managed_futures"],
        "values": [0.09, 0.10, 0.11, 0.12, 0.13, 0.14],
        "base":   0.12,
    },
}


def _set_nested(d: dict, path: list, value: Any) -> dict:
    """중첩 dict의 특정 경로에 값을 설정한다 (깊은 복사)."""
    d = deepcopy(d)
    obj = d
    for key in path[:-1]:
        if key not in obj:
            obj[key] = {}
        obj = obj[key]
    obj[path[-1]] = value
    return d


def run_sensitivity(
    base_config: dict,
    universe_px: pd.DataFrame,
    signal_px: pd.DataFrame,
    start: str,
    end: str,
    param_name: str,
    **engine_kwargs,
) -> pd.DataFrame:
    """
    단일 파라미터를 변화시키며 백테스트를 반복하고 성과 지표를 비교한다.

    Returns: 파라미터 값별 성과 지표 DataFrame
    """
    if param_name not in SENSITIVITY_PARAMS:
        raise ValueError(
            f"알 수 없는 파라미터: {param_name}\n"
            f"가능한 파라미터: {list(SENSITIVITY_PARAMS)}"
        )

    spec = SENSITIVITY_PARAMS[param_name]
    rows: List[dict] = []

    print(f"\n[민감도] {spec['description']}")
    print("-" * 52)

    for val in spec["values"]:
        config = _set_nested(base_config, spec["path"], val)
        engine = BacktestEngine(
            config=config,
            universe_px=universe_px,
            signal_px=signal_px,
            start=start,
            end=end,
            **engine_kwargs,
        )
        result = engine.run()
        m = compute_metrics(result["returns"])
        m[param_name] = val
        m["is_base"] = (val == spec["base"])
        rows.append(m)

        marker = " ← 기본값" if val == spec["base"] else ""
        print(
            f"  {param_name}={val:7.4f}{marker:<8}  "
            f"CAGR={m.get('cagr', 0):+.1%}  "
            f"Sharpe={m.get('sharpe', 0):.2f}  "
            f"MaxDD={m.get('max_drawdown', 0):.1%}"
        )

    df = pd.DataFrame(rows).set_index(param_name)
    return df


def run_all_sensitivity(
    base_config: dict,
    universe_px: pd.DataFrame,
    signal_px: pd.DataFrame,
    start: str,
    end: str,
    **engine_kwargs,
) -> Dict[str, pd.DataFrame]:
    """모든 정의된 파라미터에 대해 민감도 분석을 실행한다."""
    results: Dict[str, pd.DataFrame] = {}
    for param in SENSITIVITY_PARAMS:
        try:
            results[param] = run_sensitivity(
                base_config, universe_px, signal_px,
                start, end, param, **engine_kwargs,
            )
        except Exception as e:
            print(f"  [오류] {param}: {e}")
    return results
