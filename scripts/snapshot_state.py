"""state.db + config.yaml → STATUS.md 스냅샷 생성.

바이너리 state.db는 GitHub에서 못 보므로, 사람이 읽는 markdown으로 덤프해
레포 루트 STATUS.md에 쓴다. config.yaml은 이미 트래킹되지만 523줄이라
자주 보는 핵심 설정만 요약한다. push_logs.sh가 이 파일을 커밋한다.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

import yaml

try:
    from zoneinfo import ZoneInfo
    _NOW = datetime.now(ZoneInfo("Asia/Seoul"))
except Exception:
    _NOW = datetime.now()

ROOT = Path(__file__).parent.parent
STATE_DB = ROOT / "trading" / "state.db"
CONFIG = ROOT / "trading" / "config.yaml"
OUT = ROOT / "STATUS.md"

FEATURE_LABELS = {
    "momentum_1m": "모멘텀 1M", "momentum_3m": "모멘텀 3M", "realized_vol": "실현변동성",
    "vix": "VIX", "vix_term_structure": "VIX 기간구조", "credit_signal": "크레딧 신호",
    "hy_spread": "HY 스프레드", "hy_spread_zscore": "HY z-score", "curve_10y2y": "10Y-2Y 커브",
    "cpi_yoy": "CPI YoY", "cpi_mom_zscore": "CPI MoM z", "unrate_chg_3m": "실업률 3M 변화",
    "breakeven_5y": "BEI 5Y", "m2_yoy": "M2 YoY", "fed_bs_yoy": "Fed BS YoY", "nfci": "NFCI",
    "dxy_mom_1m": "DXY 1M", "commodity_mom_1m": "원자재 1M",
}
PCT_FEATURES = {"momentum_1m", "momentum_3m", "realized_vol"}


def _load_state() -> dict:
    con = sqlite3.connect(str(STATE_DB))
    try:
        row = con.execute(
            "select value from state_current where key='__root__'"
        ).fetchone()
    finally:
        con.close()
    return json.loads(row[0]) if row else {}


def _fmt_won(v) -> str:
    return f"₩{v:,.0f}" if isinstance(v, (int, float)) else "—"


def _fmt_pct(v, digits=2) -> str:
    return f"{v*100:.{digits}f}%" if isinstance(v, (int, float)) else "—"


def _fmt_dt(s) -> str:
    if not s:
        return "—"
    try:
        return datetime.fromisoformat(s).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(s)


def build() -> str:
    st = _load_state()
    cfg = yaml.safe_load(CONFIG.read_text())
    L: list[str] = []
    a = L.append

    a(f"# 트레이딩 상태 스냅샷")
    a("")
    a(f"> 생성: **{_NOW.strftime('%Y-%m-%d %H:%M')} KST** · "
      f"마지막 실행: {_fmt_dt(st.get('last_run_at'))}")
    a("")
    a("> ⚠️ 자동 생성 파일. 수동 편집 금지 (`scripts/snapshot_state.py`).")
    a("")

    # ── 레짐 ──
    a("## 레짐")
    a("")
    a("| 항목 | 값 |")
    a("|---|---|")
    a(f"| 확정 레짐 | **{st.get('confirmed_regime', '—')}** |")
    cand = st.get("candidate_regime")
    if cand and cand != st.get("confirmed_regime"):
        a(f"| 전환 후보 | {cand} ({st.get('candidate_count', 0)}회) |")
    a(f"| 마지막 전환일 | {st.get('last_switch_date', '—')} |")
    a(f"| 신뢰도 | {_fmt_pct(st.get('last_run_confidence'))} |")
    a(f"| HMM 매핑 | {st.get('hmm_mapping_method', '—')} "
      f"(실행 {st.get('hmm_total_runs', 0)}회, legacy 폴백 {st.get('hmm_legacy_fallback_count', 0)}회) |")
    a("")

    probs = st.get("prev_blend_probs", {})
    if probs:
        a("**blend 확률 분포**")
        a("")
        a("| 레짐 | 확률 |")
        a("|---|---|")
        for r, p in sorted(probs.items(), key=lambda x: -x[1]):
            if p >= 0.001:
                a(f"| {r} | {_fmt_pct(p, 1)} |")
        a("")

    # ── 자산 ──
    total = st.get("last_total_all_krw")
    principal = st.get("last_principal_krw")
    a("## 자산")
    a("")
    a("| 항목 | 값 |")
    a("|---|---|")
    a(f"| 총자산 | {_fmt_won(total)} |")
    a(f"| 원금 | {_fmt_won(principal)} |")
    if isinstance(total, (int, float)) and isinstance(principal, (int, float)) and principal:
        pnl = total - principal
        a(f"| 누적 손익 | {_fmt_won(pnl)} ({pnl/principal*100:+.2f}%) |")
    a(f"| 고점 | {_fmt_won(st.get('peak_krw'))} |")
    a(f"| 드로우다운 | {_fmt_pct(st.get('last_drawdown'))} |")
    _alpha = st.get("last_alpha")
    if isinstance(_alpha, dict) and not _alpha.get("inception"):
        _av = _alpha.get("alpha", 0.0)
        _ap = _alpha.get("alpha_pct", 0.0)
        a(f"| S&P500였다면 | {_fmt_won(_alpha.get('bench_value'))} |")
        a(f"| **알파(vs S&P500)** | **{_fmt_won(_av)} ({_ap*100:+.2f}%)** |")
    a(f"| 이번 달 회전액 | {_fmt_won(st.get('monthly_traded_krw'))} ({st.get('monthly_ym', '—')}) |")
    a(f"| USD/KRW | {st.get('usd_krw_rate', '—'):,.1f} ({_fmt_dt(st.get('usd_krw_at'))}) |"
      if isinstance(st.get('usd_krw_rate'), (int, float)) else "| USD/KRW | — |")
    a("")

    # ── 트리거 ──
    a("## 리밸런싱 트리거")
    a("")
    a("| 계좌 | drift | 트리거 | 사유 | 마지막 리밸 |")
    a("|---|---|---|---|---|")
    a(f"| KRW | {_fmt_pct(st.get('last_drift_krw'))} | "
      f"{'🔔' if st.get('trigger_krw') else '⚪'} | {st.get('trigger_reason_krw') or '—'} | "
      f"{_fmt_dt(st.get('last_rebalanced_krw_at'))} |")
    a(f"| USD | {_fmt_pct(st.get('last_drift_usd'))} | "
      f"{'🔔' if st.get('trigger_usd') else '⚪'} | {st.get('trigger_reason_usd') or '—'} | "
      f"{_fmt_dt(st.get('last_rebalanced_usd_at'))} |")
    a("")
    deferred = st.get("deferred_buys") or []
    if deferred:
        a(f"**지연 매수 {len(deferred)}건 대기 중**")
        a("")
        for d in deferred:
            a(f"- {d.get('ticker')} {d.get('amount_krw', 0):,.0f}원 ({d.get('currency')})")
        a("")
    else:
        a("지연 매수: 없음")
        a("")

    # ── 목표 비중 ──
    targets = st.get("saved_blended_targets", {})
    if targets:
        a("## 목표 비중 (블렌딩 결과)")
        a("")
        a("| 자산군 | 비중 |")
        a("|---|---|")
        for k, w in sorted(targets.items(), key=lambda x: -x[1]):
            if w >= 0.0005:
                a(f"| {k} | {_fmt_pct(w, 1)} |")
        a("")

    # ── 매크로 피처 ──
    feats = st.get("saved_features", {})
    if feats:
        a("## 매크로 피처 (마지막 실행)")
        a("")
        a("| 지표 | 값 |")
        a("|---|---|")
        for k in FEATURE_LABELS:
            if k in feats:
                v = feats[k]
                label = FEATURE_LABELS[k]
                if k in PCT_FEATURES:
                    a(f"| {label} | {_fmt_pct(v, 1)} |")
                else:
                    a(f"| {label} | {v:.2f} |")
        a("")

    # ── 핵심 설정 ──
    a("## 핵심 설정값")
    a("> 전체 설정은 `trading/config.yaml` 참조.")
    a("")
    rb = cfg.get("rebalancing", {})
    rf = cfg.get("regime_filter", {})
    hmm = cfg.get("hmm", {})
    vt = cfg.get("vol_targeting", {})
    a("| 설정 | 값 |")
    a("|---|---|")
    a(f"| drift 임계 | {_fmt_pct(rb.get('drift_threshold'), 1)} |")
    a(f"| 리밸 쿨다운 | {rb.get('min_rebalance_interval_days', 0)}일 |")
    a(f"| 실행/월간 회전율 상한 | {rb.get('max_run_turnover', 0)} / {rb.get('max_monthly_turnover', 0)} (0=무제한) |")
    a(f"| 레짐 타이밍 소스 | {rf.get('regime_timing_source', '—')} |")
    a(f"| confirmation / cooldown | {rf.get('confirmation_count', '—')}회 / {rf.get('cooldown_days', '—')}일 |")
    a(f"| blend 평활 α | {rf.get('blend_smoothing_alpha', '—')} |")
    a(f"| 신뢰도 산식 / 임계 | {rf.get('confidence_method', '—')} / {rf.get('confidence_threshold', '—')} |")
    a(f"| HMM 안정화 / deadband | {hmm.get('stabilize_mapping', '—')} / {hmm.get('mapping_deadband', '—')} |")
    a(f"| HMM override / crisis 우선 | {hmm.get('override_threshold', '—')} / {hmm.get('crisis_priority_threshold', '—')} |")
    a(f"| vol target (기본) / floor | {vt.get('target_vol', '—')} / {vt.get('floor', '—')} |")
    rtv = vt.get("regime_target_vol", {})
    if rtv:
        a(f"| 레짐별 target vol | " + ", ".join(f"{k} {v}" for k, v in rtv.items()) + " |")
    a("")

    return "\n".join(L) + "\n"


def main() -> None:
    OUT.write_text(build())
    print(f"[snapshot] {OUT} 생성 완료")


if __name__ == "__main__":
    main()
