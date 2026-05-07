"""Slack 알림 모듈."""
import os
import traceback
from typing import Dict, List, Optional

import slack_sdk

_CHANNEL = "C02SGLQV529"
_MENTION = "김대현"


class Messenger:
    def __init__(self) -> None:
        token = os.getenv("SLACK_TOKEN")
        self._client = slack_sdk.WebClient(token=token) if token else None

    def _send(self, text: str, mention: bool = False) -> None:
        if not self._client:
            return
        body = f"<@{_MENTION}> {text}" if mention else text
        try:
            self._client.chat_postMessage(channel=_CHANNEL, text=body)
        except Exception as e:
            print(f"[Slack 오류] {e}")

    def send_start(
        self,
        regime: str,
        features: Dict[str, float],
        confidence: float = 0.0,
    ) -> None:
        conf_str = f" | 신뢰도 `{confidence:.0%}`" if confidence > 0 else ""
        hy_str = (
            f" | HY {features['hy_spread']:.2f}%"
            if "hy_spread" in features else ""
        )
        text = (
            f":rocket: *리밸런싱 시작*\n"
            f"> 레짐: `{regime}`{conf_str}\n"
            f"> VIX {features.get('vix', 0):.1f} | "
            f"모멘텀1M {features.get('momentum_1m', 0):+.1%} | "
            f"실현변동성 {features.get('realized_vol', 0):.1%}{hy_str}"
        )
        self._send(text)

    def send_complete(
        self,
        regime: str,
        total_krw: float,
        drawdown: float,
        target_weights: Dict[str, float],
        current_weights: Dict[str, float],
        order_log: List[str],
        deferred_buys: Optional[List[dict]] = None,
        pending_sells: Optional[List[str]] = None,
        confidence: float = 0.0,
    ) -> None:
        weight_lines = "\n".join(
            f">   {ticker:<8} {current_weights.get(ticker, 0):.1%} → {w:.1%}"
            for ticker, w in sorted(target_weights.items(), key=lambda x: -x[1])
            if w > 0
        )
        orders = "\n".join(f">   {line}" for line in order_log) if order_log else ">   변경 없음"

        deferred_section = ""
        if deferred_buys:
            lines = "\n".join(
                f">   :hourglass: {d['ticker']} {d['amount_krw']:,.0f}원 ({d['currency']}) — T+2 대기"
                for d in deferred_buys
            )
            deferred_section = f"\n*지연 매수 (합성 노출로 대체):*\n{lines}"

        pending_section = ""
        if pending_sells:
            lines = "\n".join(f">   {s}" for s in pending_sells)
            pending_section = f"\n*미결제 매도 (결제 대기):*\n{lines}"

        conf_str = f" | 신뢰도 `{confidence:.0%}`" if confidence > 0 else ""
        text = (
            f":white_check_mark: *리밸런싱 완료*\n"
            f"> 레짐: `{regime}`{conf_str} | 자산: {total_krw:,.0f}원 | DD: {drawdown:+.1%}\n"
            f"*비중 변화:*\n{weight_lines}\n"
            f"*주문 내역:*\n{orders}"
            f"{deferred_section}"
            f"{pending_section}"
        )
        self._send(text, mention=True)

    def send_order_error(self, ticker: str, error: Exception) -> None:
        self._send(f":warning: 주문 오류 `{ticker}`: {error}", mention=True)

    def send_system_error(self, error: Exception) -> None:
        tb = traceback.format_exc()
        self._send(f":x: *시스템 오류*\n```{tb[-1500:]}```", mention=True)
