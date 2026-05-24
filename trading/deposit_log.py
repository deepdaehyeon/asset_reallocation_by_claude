"""
입출금(deposit/withdrawal) 이벤트 조회 — peak_krw 보정용.

기존 휴리스틱(|Δ|>10% AND age<30h → 입출금 추정)의 결함:
  1. 10% 미만 출금 미감지 → 잘못된 drawdown
  2. 1일 시장 폭락(예: −10%)이 입출금으로 오인 → defensive mode 미작동

이 모듈은 explicit 입출금 이벤트를 세 가지 소스에서 조회한다.

  1. CSV 로그 (primary, deterministic) — trading/logs/deposits.csv
     사용자가 입출금 발생 시 한 줄 추가. 자동화는 cron / shortcut 등으로 가능.
     형식: ts,acc_name,amount_krw,kind,note,id

  2. KIS profits 역산 (auto, 실거래 한정) — pykis account().profits() 사용
     공식: 입출금 = Δ(매입금액 + 예수금) − 실현손익
     장점: explicit CSV 기록 없이 자동
     한계: 모의투자 미지원 / 매수 수수료 ±수천원 오차 / 같은 날 중복 실행 시 정밀도↓

  3. 휴리스틱 fallback — |Δ|>10% AND age<30h → 입출금 추정 (기존 동작)

조회 우선순위: CSV → KIS profits → 휴리스틱

CSV 파일이 **존재하면** (비어 있어도) 사용자가 의도적으로 로그 시스템을 사용 중인 것으로
간주하고 "입출금 없음"으로 해석한다. 파일이 없을 때만 다음 단계로.
"""
from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Optional


# 기본 CSV 로그 경로 — env로 오버라이드 가능 (테스트용)
DEFAULT_LOG_PATH = Path(__file__).parent / "logs" / "deposits.csv"


@dataclass(frozen=True)
class IoEvent:
    """단일 입출금 이벤트."""
    id: str               # 고유 식별자 (중복 방지용)
    ts: datetime          # 발생 시각
    acc_name: str         # 계좌 식별자 (정보용)
    amount_krw: float     # 항상 양수
    kind: str             # 'deposit' or 'withdrawal'
    note: str = ""

    @property
    def signed_amount_krw(self) -> float:
        """입금은 +, 출금은 −. peak 보정에 그대로 더한다."""
        return self.amount_krw if self.kind == "deposit" else -self.amount_krw


# ──────────────────────────────────────────────────────────────────────────────
# 내부 유틸
# ──────────────────────────────────────────────────────────────────────────────


def _parse_ts(s: str) -> Optional[datetime]:
    """ISO8601 timestamp 파싱. 실패 시 None."""
    if not s:
        return None
    try:
        # 'Z' suffix 지원
        s = s.replace("Z", "+00:00") if s.endswith("Z") else s
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def _make_event_id(row: dict) -> str:
    """CSV 행에 명시 id가 없으면 ts+kind+amount로 결정적 ID 생성."""
    explicit = (row.get("id") or "").strip()
    if explicit:
        return explicit
    return f"{row.get('ts','').strip()}|{row.get('kind','').strip()}|{row.get('amount_krw','').strip()}"


def _parse_row(row: dict) -> Optional[IoEvent]:
    """CSV 행 → IoEvent. 검증 실패 시 None."""
    ts = _parse_ts((row.get("ts") or "").strip())
    if ts is None:
        return None

    kind = (row.get("kind") or "").strip().lower()
    if kind not in ("deposit", "withdrawal"):
        return None

    try:
        amount = float((row.get("amount_krw") or "").strip())
    except ValueError:
        return None
    if amount <= 0:
        return None  # 부호 혼동 방지 — 항상 양수

    return IoEvent(
        id=_make_event_id(row),
        ts=ts,
        acc_name=(row.get("acc_name") or "").strip(),
        amount_krw=amount,
        kind=kind,
        note=(row.get("note") or "").strip(),
    )


def _aware(dt: datetime) -> datetime:
    """naive → local tz, aware는 그대로. CSV ts와 since를 동일 기준으로 맞추기 위함."""
    if dt.tzinfo is None:
        return dt.astimezone()
    return dt


# ──────────────────────────────────────────────────────────────────────────────
# CSV 백엔드
# ──────────────────────────────────────────────────────────────────────────────


def read_events_from_csv(
    log_path: Path,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
) -> list[IoEvent]:
    """
    CSV에서 since~until 구간의 이벤트를 읽어 정렬 반환.
    since/until은 naive(local tz로 해석) 또는 aware 모두 허용.
    형식 오류 행은 무시 (경고 출력).
    """
    if not log_path.exists():
        return []

    events: list[IoEvent] = []
    bad_rows = 0
    with open(log_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader, start=2):  # 2: header 다음 행
            ev = _parse_row(row)
            if ev is None:
                bad_rows += 1
                continue
            events.append(ev)
    if bad_rows > 0:
        print(f"  [deposit_log] {log_path.name}: {bad_rows}개 행 파싱 실패 (스킵)")

    if since is not None:
        since_a = _aware(since)
        events = [e for e in events if _aware(e.ts) > since_a]
    if until is not None:
        until_a = _aware(until)
        events = [e for e in events if _aware(e.ts) <= until_a]

    events.sort(key=lambda e: _aware(e.ts))
    return events


# ──────────────────────────────────────────────────────────────────────────────
# KIS profits 역산 백엔드
# ──────────────────────────────────────────────────────────────────────────────
#
# 공식 유도:
#   총자산 = 매입금액(P) + 평가손익 + 예수금(C)
#   시장 변동 무관한 부분: P + C
#
#   거래별 Δ(P + C):
#     매수: -fee
#     매도: +(매도가 - 매수가) - fee = +실현손익(net of fees)
#     입금: +deposit_amt
#     출금: -withdraw_amt
#
#   ⟹ Δ(P + C) = (실현손익 net) + 입출금net + (-매수수수료)
#   ⟹ 입출금net ≈ Δ(P + C) - 실현손익net  (매수수수료 ±수천원 오차)
#
# KIS profits API의 .profit이 net인지 gross인지에 따라 fees를 추가로 빼야 할 수 있다.
# 보수적으로 (profit - fees)를 net으로 사용.


def _try_kis_profits(
    pykis_clients: Optional[dict],
    since: Optional[datetime],
    state_snapshot: Optional[dict],
    current_principal_krw: Optional[float],
) -> Optional[list[IoEvent]]:
    """
    pykis account().profits()로 기간 실현손익을 조회하여 입출금을 역산한다.

    Parameters
    ----------
    pykis_clients     : {acc_name: pykis.PyKis}
    since             : 조회 시작 시각 (state의 last_total_all_krw_at)
    state_snapshot    : {'last_principal_krw': float, ...}  — 직전 실행 원금(P+C)
    current_principal_krw : 이번 실행 시점의 P+C

    Returns
    -------
    None  : 사용 불가 (필수 입력 누락, API 실패, 모의투자 등) → 다음 백엔드로
    list  : 역산 성공 (입출금 없으면 빈 리스트)
            단일 합성 이벤트로 반환: amount = |net_flow|, kind 결정.
    """
    if not pykis_clients or since is None or state_snapshot is None or current_principal_krw is None:
        return None

    last_principal_krw = state_snapshot.get("last_principal_krw")
    if last_principal_krw is None or last_principal_krw <= 0:
        return None  # 초기 실행 — 비교 불가

    start_date = since.date() if isinstance(since, datetime) else since
    if not isinstance(start_date, date):
        return None
    end_date = date.today()

    # 같은 날 두 번 이상 실행되면 profits()가 같은 매도 손익을 다시 반환한다.
    # state['kis_profits_processed_through']로 마지막 처리 종료일을 저장하고,
    # 이미 그 날짜까지 처리됐다면 start = 그 다음 날로 옮긴다 (보수적 중복 방지).
    processed_through = state_snapshot.get("kis_profits_processed_through")
    if processed_through:
        try:
            pt = date.fromisoformat(processed_through)
            if pt >= start_date:
                from datetime import timedelta
                start_date = pt + timedelta(days=1)
        except (ValueError, TypeError):
            pass

    if start_date > end_date:
        # 처리 미루기 — 오늘 안에 이미 다 봤음. net_flow=0으로 빈 결과 반환
        return []

    # 통합 실현손익 합산 — 계좌별로 KR / US 모두 조회
    total_realized_krw = 0.0
    seen_kis_clients: set[int] = set()  # 같은 PyKis 인스턴스 중복 호출 방지
    for acc_name, client in pykis_clients.items():
        cid = id(client)
        if cid in seen_kis_clients:
            continue
        seen_kis_clients.add(cid)

        for country in ("KR", "US"):
            try:
                profits = client.account().profits(
                    start=start_date,
                    end=end_date,
                    country=country,
                )
            except Exception as e:
                msg = str(e).lower()
                # 모의투자 등 미지원 — 조용히 스킵
                if "모의" in msg or "지원" in msg or "vts" in msg:
                    return None
                # 한 계좌/국가 실패는 스킵, 전체 실패는 None
                print(
                    f"  [deposit_log/kis_profits] {acc_name}({country}) 조회 실패 — 스킵: {type(e).__name__}: {e}"
                )
                continue

            try:
                profit = float(profits.profit)
                fees = float(profits.fees)
            except Exception:
                continue
            net = profit - fees  # 보수적 net 처리 (profit이 gross일 가능성)

            # USD 계좌면 KRW 환산
            if country == "US":
                xr = _account_usd_krw_rate(client)
                net *= xr

            total_realized_krw += net

    delta_principal = current_principal_krw - float(last_principal_krw)
    net_flow = delta_principal - total_realized_krw

    # 매수 수수료, 환율 변동 등으로 인한 작은 오차는 입출금 아님으로 간주.
    # 1만원 미만은 노이즈로 보고 0건 반환.
    if abs(net_flow) < 10_000:
        return []

    # 합성 이벤트 1건 (kind 결정)
    kind = "deposit" if net_flow > 0 else "withdrawal"
    return [
        IoEvent(
            id=f"kis_profits|{start_date.isoformat()}|{end_date.isoformat()}|{net_flow:.0f}",
            ts=datetime.combine(end_date, datetime.min.time()).astimezone(),
            acc_name="(kis_profits 역산)",
            amount_krw=abs(net_flow),
            kind=kind,
            note=(
                f"Δprincipal {delta_principal:+,.0f} - 실현손익 {total_realized_krw:+,.0f}"
                f" = {net_flow:+,.0f}원 (역산 추정, 매수 수수료 오차 ±수천원)"
            ),
        )
    ]


def _account_usd_krw_rate(client) -> float:
    """pykis client에서 USD/KRW 환율 추출. 실패 시 1380 폴백."""
    try:
        bal = client.account().balance(country="US")
        for cur, dep in bal.deposits.items():
            if str(cur) == "USD":
                return float(dep.exchange_rate)
    except Exception:
        pass
    return 1380.0


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────


def fetch_deposit_withdrawal_events(
    since: Optional[datetime],
    log_path: Optional[Path] = None,
    pykis_clients: Optional[dict] = None,
    state_snapshot: Optional[dict] = None,
    current_principal_krw: Optional[float] = None,
) -> tuple[Optional[list[IoEvent]], str]:
    """
    since 이후 입출금 이벤트를 조회한다.

    조회 우선순위:
      1. CSV 로그 (파일이 존재하면 → 결과로 채택, 비어 있어도 '0건')
      2. KIS profits 역산 (state에 last_principal_krw가 있고 current_principal_krw 전달 시)
      3. 둘 다 불가 → None → 호출자가 휴리스틱 fallback 사용

    Parameters
    ----------
    since                  : 이 시각 *초과*인 이벤트만 반환. None이면 전체.
    log_path               : CSV 경로 (None → DEFAULT_LOG_PATH 또는 env DEPOSIT_LOG_PATH)
    pykis_clients          : KIS profits 백엔드용
    state_snapshot         : KIS profits 백엔드용 (직전 실행 원금)
    current_principal_krw  : KIS profits 백엔드용 (이번 실행 매입금액 + 예수금, KRW)

    Returns
    -------
    (events_or_None, source)
      events: list[IoEvent] 정상 조회 (0건 포함)
      None  : 조회 불가 → 휴리스틱 fallback 신호
      source: 'csv' | 'kis_profits' | 'none'
    """
    path = log_path or Path(os.environ.get("DEPOSIT_LOG_PATH", DEFAULT_LOG_PATH))

    if path.exists():
        return read_events_from_csv(path, since=since), "csv"

    # CSV 미존재 → KIS profits 역산 시도
    api_events = _try_kis_profits(
        pykis_clients=pykis_clients,
        since=since,
        state_snapshot=state_snapshot,
        current_principal_krw=current_principal_krw,
    )
    if api_events is not None:
        return api_events, "kis_profits"

    return None, "none"


def compute_net_flow(events: Iterable[IoEvent]) -> float:
    """입출금 이벤트 리스트의 순유입(net inflow) 합산. 입금 +, 출금 −."""
    return float(sum(e.signed_amount_krw for e in events))
