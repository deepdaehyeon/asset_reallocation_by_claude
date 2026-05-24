"""
입출금 기반 peak 보정 + 휴리스틱 fallback 테스트.

실제 KIS API 호출 없이 모킹으로 검증한다.

실행:
  python scripts/test_broker_io_peak.py
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "trading"))

from deposit_log import (
    DEFAULT_LOG_PATH,
    IoEvent,
    compute_net_flow,
    fetch_deposit_withdrawal_events,
    read_events_from_csv,
)


PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def _record(name: str, ok: bool, msg: str = "") -> None:
    if ok:
        PASSED.append(name)
        print(f"  ✓ {name}")
    else:
        FAILED.append((name, msg))
        print(f"  ✗ {name}\n      {msg}")


def _write_csv(path: Path, rows: list[dict]) -> None:
    import csv
    header = ["ts", "acc_name", "amount_krw", "kind", "note", "id"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=header)
        w.writeheader()
        for r in rows:
            w.writerow({h: r.get(h, "") for h in header})


# ──────────────────────────────────────────────────────────────────────────────
# 단위 테스트 — deposit_log 모듈
# ──────────────────────────────────────────────────────────────────────────────


def test_csv_read_basic():
    """CSV 1건씩 입금/출금 읽기."""
    with tempfile.TemporaryDirectory() as td:
        log = Path(td) / "deposits.csv"
        _write_csv(log, [
            {"ts": "2026-05-01T10:00:00", "acc_name": "KRW_1",
             "amount_krw": "1000000", "kind": "deposit", "note": "월급 입금"},
            {"ts": "2026-05-15T14:30:00", "acc_name": "KRW_1",
             "amount_krw": "300000", "kind": "withdrawal", "note": "생활비"},
        ])
        evs = read_events_from_csv(log)
        _record("test_csv_read_basic / 2건 파싱", len(evs) == 2,
                f"expected 2, got {len(evs)}")
        _record("test_csv_read_basic / 정렬", evs[0].ts < evs[1].ts)
        _record("test_csv_read_basic / net_flow",
                compute_net_flow(evs) == 700000.0,
                f"expected +700000, got {compute_net_flow(evs)}")


def test_csv_since_filter():
    """since 이후만 필터링."""
    with tempfile.TemporaryDirectory() as td:
        log = Path(td) / "deposits.csv"
        _write_csv(log, [
            {"ts": "2026-04-01T09:00:00", "amount_krw": "500000", "kind": "deposit"},
            {"ts": "2026-05-10T10:00:00", "amount_krw": "200000", "kind": "withdrawal"},
            {"ts": "2026-05-20T11:00:00", "amount_krw": "1000000", "kind": "deposit"},
        ])
        since = datetime.fromisoformat("2026-05-01T00:00:00")
        evs = read_events_from_csv(log, since=since)
        _record("test_csv_since_filter / 2건만 남음", len(evs) == 2)
        _record("test_csv_since_filter / 4월 제외",
                all(e.ts >= since.astimezone(e.ts.tzinfo or None) if e.ts.tzinfo else e.ts > since for e in evs))


def test_csv_bad_rows_skipped():
    """잘못된 행은 스킵하고 정상 행만 반환."""
    with tempfile.TemporaryDirectory() as td:
        log = Path(td) / "deposits.csv"
        _write_csv(log, [
            {"ts": "2026-05-01T10:00:00", "amount_krw": "1000000", "kind": "deposit"},
            {"ts": "BAD_DATE", "amount_krw": "500000", "kind": "deposit"},  # ts 오류
            {"ts": "2026-05-02T10:00:00", "amount_krw": "abc", "kind": "deposit"},  # 금액 오류
            {"ts": "2026-05-03T10:00:00", "amount_krw": "-100", "kind": "deposit"},  # 음수
            {"ts": "2026-05-04T10:00:00", "amount_krw": "100", "kind": "unknown"},  # kind 오류
            {"ts": "2026-05-05T10:00:00", "amount_krw": "200000", "kind": "withdrawal"},
        ])
        # capture print output to avoid noise
        buf = io.StringIO()
        with redirect_stdout(buf):
            evs = read_events_from_csv(log)
        _record("test_csv_bad_rows_skipped / 정상 2건",
                len(evs) == 2, f"got {len(evs)}")
        _record("test_csv_bad_rows_skipped / 경고 출력",
                "파싱 실패" in buf.getvalue())


def test_csv_missing_file():
    """CSV 파일 없으면 빈 리스트."""
    evs = read_events_from_csv(Path("/nonexistent/deposits.csv"))
    _record("test_csv_missing_file / 빈 리스트", evs == [])


def test_fetch_routes_to_csv():
    """CSV 파일 존재 시 fetch는 source=csv 반환."""
    with tempfile.TemporaryDirectory() as td:
        log = Path(td) / "deposits.csv"
        _write_csv(log, [
            {"ts": "2026-05-01T10:00:00", "amount_krw": "500000", "kind": "deposit"},
        ])
        evs, src = fetch_deposit_withdrawal_events(since=None, log_path=log)
        _record("test_fetch_routes_to_csv / source=csv", src == "csv")
        _record("test_fetch_routes_to_csv / 1건", len(evs) == 1)


def test_fetch_empty_csv_is_zero_events():
    """헤더만 있는 CSV → 빈 리스트 (휴리스틱 fallback 안 함)."""
    with tempfile.TemporaryDirectory() as td:
        log = Path(td) / "deposits.csv"
        _write_csv(log, [])  # 헤더만
        evs, src = fetch_deposit_withdrawal_events(since=None, log_path=log)
        _record("test_fetch_empty_csv_is_zero_events / source=csv (fallback 안 함)",
                src == "csv" and evs == [])


def test_fetch_no_csv_falls_to_none():
    """CSV 없고 KIS stub은 None → source=none."""
    evs, src = fetch_deposit_withdrawal_events(
        since=None,
        log_path=Path("/nonexistent/deposits.csv"),
    )
    _record("test_fetch_no_csv_falls_to_none / source=none",
            src == "none" and evs is None)


# ──────────────────────────────────────────────────────────────────────────────
# 통합 시나리오 — _correct_peak_for_io 로직
# ──────────────────────────────────────────────────────────────────────────────
# 직접 KisRebalancer를 띄우지 않고, 같은 로직을 가벼운 헬퍼로 재현 (의존성 회피)
# 실제 메서드와 입력/출력 시그니처가 동일하도록 작성한다.


class _PeakCorrectionHarness:
    """KisRebalancer._correct_peak_for_io를 의존성 없이 호출하기 위한 어댑터."""

    def __init__(self):
        self._clients = {}  # stub

    # bound method 사용을 위해 KisRebalancer 클래스의 _correct_peak_for_io를 import
    # 하지만 import 시점에 pykis가 로드되어야 하므로 우회: 모듈 함수로 추출하지 않고,
    # 동일한 로직을 직접 호출하도록 KisRebalancer를 monkey-patch 없이 그대로 부른다.


def _harness_correct_peak(
    peak: float,
    prev_total: float,
    prev_total_at: str,
    total_all_krw: float,
    log_path: Path,
    now: datetime,
) -> float:
    """
    KisRebalancer._correct_peak_for_io를 격리 호출하기 위한 래퍼.
    DEPOSIT_LOG_PATH env로 log_path 전달, datetime.now()는 monkey-patch.
    """
    from executor import KisRebalancer
    obj = KisRebalancer.__new__(KisRebalancer)  # __init__ 우회 (pykis 클라이언트 불필요)
    obj._clients = {}

    # datetime.now()를 patch — 휴리스틱의 age_h 계산을 결정적으로 만들기 위함
    import executor as _exec
    real_dt = _exec.datetime

    class _FakeDt(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

        @classmethod
        def fromisoformat(cls, s):
            return real_dt.fromisoformat(s)

    os.environ["DEPOSIT_LOG_PATH"] = str(log_path)
    try:
        with patch.object(_exec, "datetime", _FakeDt):
            new_peak, _ = obj._correct_peak_for_io(
                peak=peak,
                prev_total=prev_total,
                prev_total_at=prev_total_at,
                total_all_krw=total_all_krw,
            )
            return new_peak
    finally:
        os.environ.pop("DEPOSIT_LOG_PATH", None)


def test_scenario_small_withdrawal():
    """시나리오 1: 5% 출금 → peak가 5% 감소."""
    with tempfile.TemporaryDirectory() as td:
        log = Path(td) / "deposits.csv"
        _write_csv(log, [
            {"ts": "2026-05-23T15:00:00", "acc_name": "KRW_1",
             "amount_krw": "500000", "kind": "withdrawal", "note": "생활비"},
        ])
        # 직전 자산 10,000,000원, 현재 9,500,000원 (5% 출금)
        # 휴리스틱은 미감지(<10%)지만 CSV로 명시적 보정
        buf = io.StringIO()
        with redirect_stdout(buf):
            new_peak = _harness_correct_peak(
                peak=10_000_000,
                prev_total=10_000_000,
                prev_total_at="2026-05-23T09:00:00",
                total_all_krw=9_500_000,
                log_path=log,
                now=datetime.fromisoformat("2026-05-24T09:00:00"),
            )
        _record("test_scenario_small_withdrawal / peak 5% 감소",
                new_peak == 9_500_000.0,
                f"expected 9,500,000, got {new_peak:,.0f}")
        _record("test_scenario_small_withdrawal / source=csv 로그",
                "[peak 보정/csv]" in buf.getvalue())


def test_scenario_market_crash():
    """
    시나리오 2: −10% 시장 폭락 (입출금 없음, CSV 존재).
    CSV는 비어 있으므로 net_flow=0 → peak 그대로 → drawdown −10% 정상 보고.
    휴리스틱이라면 입출금으로 오인해 peak를 끌어내릴 위험이 있음.
    """
    with tempfile.TemporaryDirectory() as td:
        log = Path(td) / "deposits.csv"
        _write_csv(log, [])  # 헤더만 — "입출금 없음"을 명시적으로 표시
        buf = io.StringIO()
        with redirect_stdout(buf):
            new_peak = _harness_correct_peak(
                peak=10_000_000,
                prev_total=10_000_000,
                prev_total_at="2026-05-23T09:00:00",
                total_all_krw=9_000_000,  # −10% 폭락
                log_path=log,
                now=datetime.fromisoformat("2026-05-24T09:00:00"),
            )
        _record("test_scenario_market_crash / peak 그대로",
                new_peak == 10_000_000.0,
                f"expected 10,000,000, got {new_peak:,.0f}")
        # 휴리스틱 fallback 메시지가 안 떠야 함
        _record("test_scenario_market_crash / 휴리스틱 미가동",
                "[peak 보정/휴리스틱]" not in buf.getvalue())


def test_scenario_csv_missing_fallback_to_heuristic():
    """시나리오 3: CSV 없음 + 10% 이상 변화 → 휴리스틱 fallback 작동."""
    with tempfile.TemporaryDirectory() as td:
        log = Path(td) / "deposits.csv"  # 일부러 만들지 않음
        buf = io.StringIO()
        with redirect_stdout(buf):
            new_peak = _harness_correct_peak(
                peak=10_000_000,
                prev_total=10_000_000,
                prev_total_at="2026-05-23T09:00:00",
                total_all_krw=11_500_000,  # +15% 변화
                log_path=log,
                now=datetime.fromisoformat("2026-05-24T09:00:00"),  # 24h 전
            )
        # 휴리스틱: peak *= (1 + 0.15) = 11,500,000
        _record("test_scenario_csv_missing_fallback_to_heuristic / 휴리스틱 작동",
                new_peak == 11_500_000.0,
                f"expected 11,500,000, got {new_peak:,.0f}")
        _record("test_scenario_csv_missing_fallback_to_heuristic / 휴리스틱 메시지",
                "[peak 보정/휴리스틱]" in buf.getvalue())


def test_scenario_dedup():
    """시나리오 4: 동일 ID 이벤트 중복 시 user 책임 (csv는 한 번만 기록되도록 운영).

    since로 처리됨: prev_total_at 이후 이벤트만 가져오므로,
    한 번 처리한 이벤트는 다음 실행에서 prev_total_at이 갱신되어 제외된다.
    """
    with tempfile.TemporaryDirectory() as td:
        log = Path(td) / "deposits.csv"
        _write_csv(log, [
            {"ts": "2026-05-20T10:00:00", "amount_krw": "500000", "kind": "deposit"},
            {"ts": "2026-05-23T10:00:00", "amount_krw": "1000000", "kind": "deposit"},
        ])
        # 1차 호출: prev_total_at = 2026-05-19 → 두 이벤트 모두 적용 (+1,500,000)
        peak_after_1st = _harness_correct_peak(
            peak=10_000_000,
            prev_total=10_000_000,
            prev_total_at="2026-05-19T09:00:00",
            total_all_krw=11_500_000,
            log_path=log,
            now=datetime.fromisoformat("2026-05-24T09:00:00"),
        )
        _record("test_scenario_dedup / 1차 +1,500,000 적용",
                peak_after_1st == 11_500_000.0,
                f"got {peak_after_1st:,.0f}")

        # 2차 호출: prev_total_at = 2026-05-21 → 5/20 이벤트만 제외, 5/23 이벤트는 신규
        # since는 strict > 이므로 5/21 9시 이후만 → 5/23 입금만 적용
        peak_after_2nd = _harness_correct_peak(
            peak=peak_after_1st,
            prev_total=11_500_000,
            prev_total_at="2026-05-21T09:00:00",
            total_all_krw=12_500_000,
            log_path=log,
            now=datetime.fromisoformat("2026-05-24T09:00:00"),
        )
        _record("test_scenario_dedup / 2차에 1차 이벤트 중복 안 됨",
                peak_after_2nd == 12_500_000.0,  # 11,500,000 + 1,000,000 (5/23만)
                f"got {peak_after_2nd:,.0f}")


def test_scenario_withdrawal_below_zero_protection():
    """과도한 출금으로 peak가 음수가 되는 비현실적 케이스 → total_all_krw로 리셋."""
    with tempfile.TemporaryDirectory() as td:
        log = Path(td) / "deposits.csv"
        _write_csv(log, [
            {"ts": "2026-05-23T15:00:00", "amount_krw": "20000000",
             "kind": "withdrawal", "note": "오기재 또는 비상 출금"},
        ])
        buf = io.StringIO()
        with redirect_stdout(buf):
            new_peak = _harness_correct_peak(
                peak=10_000_000,
                prev_total=10_000_000,
                prev_total_at="2026-05-23T09:00:00",
                total_all_krw=5_000_000,
                log_path=log,
                now=datetime.fromisoformat("2026-05-24T09:00:00"),
            )
        _record("test_scenario_withdrawal_below_zero_protection / total_all_krw로 리셋",
                new_peak == 5_000_000.0,
                f"expected 5,000,000, got {new_peak:,.0f}")


# ──────────────────────────────────────────────────────────────────────────────
# KIS profits 역산 백엔드
# ──────────────────────────────────────────────────────────────────────────────


def _make_mock_kis_clients(realized_profit_krw: float, fees_krw: float = 0.0):
    """
    KIS profits 모킹.
    - account().profits(country='KR'): KR 손익 (= 인자 그대로)
    - account().profits(country='US'): USD profits는 0으로 설정 (테스트 단순화)
    """
    from unittest.mock import MagicMock

    kr_profits = MagicMock()
    kr_profits.profit = realized_profit_krw + fees_krw  # gross
    kr_profits.fees = fees_krw

    us_profits = MagicMock()
    us_profits.profit = 0
    us_profits.fees = 0

    def profits_dispatcher(start, end=None, country=None):
        return kr_profits if country == "KR" else us_profits

    account_obj = MagicMock()
    account_obj.profits = MagicMock(side_effect=profits_dispatcher)
    # _account_usd_krw_rate가 호출하는 balance(country='US')도 막아둠 (xr 1380 fallback 강제)
    account_obj.balance = MagicMock(side_effect=Exception("mock: no balance"))

    client = MagicMock()
    client.account = MagicMock(return_value=account_obj)

    return {"KRW_1": client}


def test_kis_profits_pure_deposit():
    """원금 1000만 → 1100만, 실현손익 0 → 입금 100만 검출."""
    from deposit_log import fetch_deposit_withdrawal_events

    clients = _make_mock_kis_clients(realized_profit_krw=0.0)
    events, src = fetch_deposit_withdrawal_events(
        since=datetime.fromisoformat("2026-05-20T09:00:00"),
        log_path=Path("/nonexistent/deposits.csv"),
        pykis_clients=clients,
        state_snapshot={"last_principal_krw": 10_000_000},
        current_principal_krw=11_000_000,
    )
    _record("test_kis_profits_pure_deposit / source=kis_profits", src == "kis_profits")
    _record("test_kis_profits_pure_deposit / 1건 추출", events is not None and len(events) == 1)
    _record(
        "test_kis_profits_pure_deposit / 입금 +1,000,000원",
        events[0].kind == "deposit" and events[0].amount_krw == 1_000_000.0,
        f"got kind={events[0].kind} amount={events[0].amount_krw}",
    )


def test_kis_profits_pure_withdrawal():
    """원금 1000만 → 800만, 실현손익 0 → 출금 200만."""
    from deposit_log import fetch_deposit_withdrawal_events
    clients = _make_mock_kis_clients(realized_profit_krw=0.0)
    events, src = fetch_deposit_withdrawal_events(
        since=datetime.fromisoformat("2026-05-20T09:00:00"),
        log_path=Path("/nonexistent/deposits.csv"),
        pykis_clients=clients,
        state_snapshot={"last_principal_krw": 10_000_000},
        current_principal_krw=8_000_000,
    )
    _record("test_kis_profits_pure_withdrawal / source", src == "kis_profits")
    _record(
        "test_kis_profits_pure_withdrawal / 출금 2,000,000",
        events and events[0].kind == "withdrawal" and events[0].amount_krw == 2_000_000.0,
    )


def test_kis_profits_realized_gain_no_io():
    """원금 1000만 → 1050만, 실현손익 50만 → 입출금 0건 (50만은 매도 손익)."""
    from deposit_log import fetch_deposit_withdrawal_events
    clients = _make_mock_kis_clients(realized_profit_krw=500_000)
    events, src = fetch_deposit_withdrawal_events(
        since=datetime.fromisoformat("2026-05-20T09:00:00"),
        log_path=Path("/nonexistent/deposits.csv"),
        pykis_clients=clients,
        state_snapshot={"last_principal_krw": 10_000_000},
        current_principal_krw=10_500_000,
    )
    _record("test_kis_profits_realized_gain_no_io / source", src == "kis_profits")
    _record(
        "test_kis_profits_realized_gain_no_io / 0건 (= 매도 손익, 입출금 아님)",
        events == [],
        f"got {events}",
    )


def test_kis_profits_realized_plus_deposit():
    """원금 1000만 → 1300만, 실현손익 50만 → 입금 250만."""
    from deposit_log import fetch_deposit_withdrawal_events
    clients = _make_mock_kis_clients(realized_profit_krw=500_000)
    events, src = fetch_deposit_withdrawal_events(
        since=datetime.fromisoformat("2026-05-20T09:00:00"),
        log_path=Path("/nonexistent/deposits.csv"),
        pykis_clients=clients,
        state_snapshot={"last_principal_krw": 10_000_000},
        current_principal_krw=13_000_000,
    )
    _record(
        "test_kis_profits_realized_plus_deposit / 입금 2,500,000",
        events and events[0].kind == "deposit" and events[0].amount_krw == 2_500_000.0,
        f"got {events[0] if events else None}",
    )


def test_kis_profits_initial_run_no_principal():
    """state에 last_principal_krw 없으면 None 반환 → 휴리스틱 fallback."""
    from deposit_log import fetch_deposit_withdrawal_events
    clients = _make_mock_kis_clients(realized_profit_krw=0.0)
    events, src = fetch_deposit_withdrawal_events(
        since=datetime.fromisoformat("2026-05-20T09:00:00"),
        log_path=Path("/nonexistent/deposits.csv"),
        pykis_clients=clients,
        state_snapshot={},  # 초기 실행
        current_principal_krw=10_000_000,
    )
    _record("test_kis_profits_initial_run_no_principal / None", events is None and src == "none")


def test_kis_profits_already_processed_today():
    """오늘 이미 처리된 적 있으면(processed_through=오늘) 0건 반환 (중복 방지)."""
    from deposit_log import fetch_deposit_withdrawal_events
    clients = _make_mock_kis_clients(realized_profit_krw=500_000)
    today = date.today().isoformat()
    events, src = fetch_deposit_withdrawal_events(
        since=datetime.fromisoformat("2026-05-20T09:00:00"),
        log_path=Path("/nonexistent/deposits.csv"),
        pykis_clients=clients,
        state_snapshot={
            "last_principal_krw": 10_000_000,
            "kis_profits_processed_through": today,
        },
        current_principal_krw=10_500_000,
    )
    _record(
        "test_kis_profits_already_processed_today / 빈 결과",
        events == [],
        f"got {events}",
    )


def test_kis_profits_csv_takes_precedence():
    """CSV가 존재하면 KIS profits 백엔드는 호출 안 됨."""
    from deposit_log import fetch_deposit_withdrawal_events
    with tempfile.TemporaryDirectory() as td:
        log = Path(td) / "deposits.csv"
        _write_csv(log, [])
        clients = _make_mock_kis_clients(realized_profit_krw=999_999_999)
        events, src = fetch_deposit_withdrawal_events(
            since=datetime.fromisoformat("2026-05-20T09:00:00"),
            log_path=log,
            pykis_clients=clients,
            state_snapshot={"last_principal_krw": 1},
            current_principal_krw=1_000_000_000,
        )
        _record("test_kis_profits_csv_takes_precedence / source=csv", src == "csv")
        # profits().profit이 호출되지 않았는지 — 모킹의 호출 카운트 확인
        _record(
            "test_kis_profits_csv_takes_precedence / profits() 미호출",
            clients["KRW_1"].account().profits.call_count == 0,
        )


def test_kis_profits_small_noise_below_threshold():
    """1만원 미만 net_flow는 노이즈(수수료 등)로 보고 0건."""
    from deposit_log import fetch_deposit_withdrawal_events
    clients = _make_mock_kis_clients(realized_profit_krw=0.0)
    events, src = fetch_deposit_withdrawal_events(
        since=datetime.fromisoformat("2026-05-20T09:00:00"),
        log_path=Path("/nonexistent/deposits.csv"),
        pykis_clients=clients,
        state_snapshot={"last_principal_krw": 10_000_000},
        current_principal_krw=10_005_000,  # +5,000원 (수수료 노이즈)
    )
    _record(
        "test_kis_profits_small_noise_below_threshold / 0건",
        events == [],
        f"got {events}",
    )


def main():
    print("=" * 60)
    print("  Broker I/O peak 보정 테스트")
    print("=" * 60)

    print("\n[단위] deposit_log 모듈")
    test_csv_read_basic()
    test_csv_since_filter()
    test_csv_bad_rows_skipped()
    test_csv_missing_file()
    test_fetch_routes_to_csv()
    test_fetch_empty_csv_is_zero_events()
    test_fetch_no_csv_falls_to_none()

    print("\n[시나리오] _correct_peak_for_io")
    test_scenario_small_withdrawal()
    test_scenario_market_crash()
    test_scenario_csv_missing_fallback_to_heuristic()
    test_scenario_dedup()
    test_scenario_withdrawal_below_zero_protection()

    print("\n[KIS profits 역산]")
    test_kis_profits_pure_deposit()
    test_kis_profits_pure_withdrawal()
    test_kis_profits_realized_gain_no_io()
    test_kis_profits_realized_plus_deposit()
    test_kis_profits_initial_run_no_principal()
    test_kis_profits_already_processed_today()
    test_kis_profits_csv_takes_precedence()
    test_kis_profits_small_noise_below_threshold()

    print("\n" + "=" * 60)
    print(f"  결과: {len(PASSED)} passed, {len(FAILED)} failed")
    print("=" * 60)
    if FAILED:
        print("\n실패:")
        for name, msg in FAILED:
            print(f"  - {name}: {msg}")
        sys.exit(1)


if __name__ == "__main__":
    main()
