"""
자산 배분 시스템 웹 컨트롤 패널.

실행: python server.py
접속: http://<PC_IP>:8080
"""
import asyncio
import json
import sys
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from prometheus_client import Gauge, generate_latest, CONTENT_TYPE_LATEST

BASE_DIR = Path(__file__).parent
STATE_FILE = BASE_DIR / "state.json"
CONFIG_FILE = BASE_DIR / "config.yaml"

app = FastAPI(title="자산 배분 시스템")

# ── Prometheus 메트릭 ───────────────────────────────
_REGIME_MAP = {"Goldilocks": 0, "Reflation": 1, "Slowdown": 2, "Stagflation": 3, "Crisis": 4}

_g_regime          = Gauge("asset_regime_index",         "Confirmed regime (0=Goldilocks 1=Reflation 2=Slowdown 3=Stagflation 4=Crisis)")
_g_confidence      = Gauge("asset_regime_confidence",    "Regime confidence score [0,1]")
_g_drawdown        = Gauge("asset_portfolio_drawdown",   "Portfolio drawdown ratio")
_g_peak            = Gauge("asset_portfolio_peak_krw",   "Portfolio peak value KRW")
_g_total           = Gauge("asset_portfolio_total_krw",  "Portfolio current total KRW")
_g_pending_sells   = Gauge("asset_pending_sells_count",  "Pending sell settlements count")
_g_deferred_buys   = Gauge("asset_deferred_buys_count",  "Deferred buy orders count")
_g_last_run        = Gauge("asset_last_run_timestamp",   "Unix timestamp of last pipeline run")
_g_candidate_count = Gauge("asset_regime_candidate_count", "Consecutive confirmation count for candidate regime")
_g_target_weight   = Gauge("asset_target_weight",        "Target portfolio weight", ["ticker", "name", "asset_class"])


def _update_metrics(state: dict, cfg: dict) -> None:
    confirmed = state.get("confirmed_regime", "Slowdown")
    _g_regime.set(_REGIME_MAP.get(confirmed, 2))
    _g_confidence.set(state.get("last_run_confidence", 0.0))
    _g_drawdown.set(state.get("last_drawdown", 0.0))
    _g_peak.set(state.get("peak_krw", 0.0))
    _g_total.set(state.get("last_total_krw", 0.0))
    _g_pending_sells.set(len(state.get("pending_sells", [])))
    _g_deferred_buys.set(len(state.get("deferred_buys", [])))
    _g_candidate_count.set(state.get("candidate_count", 0))

    last_run = state.get("last_run_at")
    if last_run:
        from datetime import datetime
        _g_last_run.set(datetime.fromisoformat(last_run).timestamp())

    regime = state.get("confirmed_regime", "Slowdown")
    weights = cfg.get("regime_weights", {}).get(regime, {})
    universe = cfg.get("universe", {})
    for ticker, w in weights.items():
        info = universe.get(ticker, {})
        _g_target_weight.labels(
            ticker=ticker,
            name=info.get("name", ticker),
            asset_class=info.get("asset_class", ""),
        ).set(w)

# ── 내부 헬퍼 ──────────────────────────────────────

def _load_config() -> dict:
    with open(CONFIG_FILE) as f:
        return yaml.safe_load(f)


def _load_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}


# ── API ───────────────────────────────────────────

@app.get("/api/state")
async def get_state():
    return _load_state()


@app.get("/api/config")
async def get_config():
    cfg = _load_config()
    return {
        "universe": cfg["universe"],
        "regime_weights": cfg["regime_weights"],
        "rebalancing": cfg["rebalancing"],
        "risk": cfg["risk"],
        "settlement": cfg.get("settlement", {}),
    }


@app.get("/api/regime")
async def get_regime():
    """현재 레짐을 실시간으로 계산해 반환한다 (약 10초 소요).

    필터 카운터는 진행시키지 않고 state.json의 현재 상태만 읽는다.
    카운터 갱신은 run.py 실행 시에만 이루어진다.
    """
    try:
        cfg = _load_config()
        state = _load_state()

        from fetcher import fetch_signal_prices
        from features import compute_features
        from regime import detect_regime
        from datetime import date

        prices = fetch_signal_prices(
            tickers=cfg["signal"]["tickers"],
            lookback_days=cfg["signal"]["lookback_days"],
        )
        feats = compute_features(prices)
        raw_regime = detect_regime(feats)

        # 현재 필터 상태 (읽기 전용)
        fcfg = cfg.get("regime_filter", {})
        confirm_n = fcfg.get("confirmation_count", 3)
        cooldown_days = fcfg.get("cooldown_days", 5)

        confirmed = state.get("confirmed_regime", raw_regime)
        candidate = state.get("candidate_regime", raw_regime)
        candidate_count = state.get("candidate_count", 0)
        last_switch = state.get("last_switch_date")

        cooldown_remaining = 0
        if last_switch:
            elapsed = (date.today() - date.fromisoformat(last_switch)).days
            cooldown_remaining = max(0, cooldown_days - elapsed)

        return {
            "raw_regime": raw_regime,
            "regime": confirmed,
            "features": feats,
            "filter": {
                "confirmed": confirmed,
                "candidate": candidate,
                "candidate_count": candidate_count,
                "confirm_n": confirm_n,
                "is_transitioning": candidate != confirmed,
                "cooldown_remaining": cooldown_remaining,
                "last_switch_date": last_switch,
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/metrics")
async def metrics():
    """Prometheus 스크레이프 엔드포인트."""
    state = _load_state()
    cfg = _load_config()
    _update_metrics(state, cfg)
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ── WebSocket 파이프라인 스트리밍 ──────────────────

_run_lock = asyncio.Lock()


@app.websocket("/ws/run")
async def ws_run(ws: WebSocket, dry_run: bool = False):
    await ws.accept()

    if _run_lock.locked():
        await ws.send_text("[ERROR] 이미 실행 중인 파이프라인이 있습니다.\n")
        await ws.close()
        return

    proc = None
    async with _run_lock:
        cmd = [sys.executable, str(BASE_DIR / "run.py")]
        if dry_run:
            cmd.append("--dry-run")

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(BASE_DIR),
            )
            async for line in proc.stdout:
                await ws.send_text(line.decode("utf-8", errors="replace"))

            await proc.wait()
            await ws.send_text(f"\n[DONE] 종료 코드: {proc.returncode}\n")

        except WebSocketDisconnect:
            if proc and proc.returncode is None:
                proc.terminate()
        except Exception as e:
            try:
                await ws.send_text(f"[ERROR] {e}\n")
            except Exception:
                pass
        finally:
            try:
                await ws.close()
            except Exception:
                pass


# ── 정적 파일 + 루트 ──────────────────────────────

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.get("/")
async def root():
    html = (BASE_DIR / "static" / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(html)


# ── 진입점 ───────────────────────────────────────

if __name__ == "__main__":
    import socket
    import uvicorn

    hostname = socket.gethostname()
    try:
        local_ip = socket.gethostbyname(hostname)
    except Exception:
        local_ip = "127.0.0.1"

    print("━" * 50)
    print("자산 배분 시스템 웹 서버")
    print(f"  PC 접속:   http://localhost:8080")
    print(f"  폰 접속:   http://{local_ip}:8080")
    print("━" * 50)

    uvicorn.run(app, host="0.0.0.0", port=8080, reload=False)
