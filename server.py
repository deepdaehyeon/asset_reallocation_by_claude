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
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).parent
STATE_FILE = BASE_DIR / "state.json"
CONFIG_FILE = BASE_DIR / "config.yaml"

app = FastAPI(title="자산 배분 시스템")

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
    """현재 레짐을 실시간으로 계산해 반환한다 (약 10초 소요)."""
    try:
        cfg = _load_config()
        from fetcher import fetch_signal_prices
        from features import compute_features
        from regime import detect_regime

        prices = fetch_signal_prices(
            tickers=cfg["signal"]["tickers"],
            lookback_days=cfg["signal"]["lookback_days"],
        )
        feats = compute_features(prices)
        regime = detect_regime(feats)
        return {"regime": regime, "features": feats}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
