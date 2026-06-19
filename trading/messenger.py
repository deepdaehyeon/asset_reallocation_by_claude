"""Slack 알림 모듈."""
import os
import traceback
from typing import Dict, List, Optional

import slack_sdk

_CHANNEL = "C02SGLQV529"
_MENTION = "김대현"


def _format_probs(probs: Optional[Dict[str, float]], threshold: float = 0.05) -> str:
    """레짐 확률 dict를 '`레짐` NN%' 내림차순 문자열로 포맷. 비면 '—'."""
    if not probs:
        return "—"
    parts = [
        f"`{r}` {p:.0%}"
        for r, p in sorted(probs.items(), key=lambda x: -x[1])
        if p >= threshold
    ]
    return "  ".join(parts) if parts else "—"


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

    @staticmethod
    def _regime_prob_lines(
        hmm_probs: Optional[Dict[str, float]],
        rf_probs: Optional[Dict[str, float]],
        blend_probs: Dict[str, float],
    ) -> str:
        """HMM·RF·가중합 레짐 확률을 각각 인용 줄로 반환.

        HMM이 비활성/데이터 부족이면 hmm_probs·rf_probs가 비어 가중합 한 줄만 표시한다.
        """
        lines = []
        if hmm_probs:
            lines.append(f"> HMM: {_format_probs(hmm_probs)}")
        if rf_probs:
            lines.append(f"> RF: {_format_probs(rf_probs)}")
        lines.append(f"> 가중합: {_format_probs(blend_probs)}")
        return "\n".join(lines)

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
        confidence: float = 0.0,
        universe: Optional[Dict[str, dict]] = None,
    ) -> None:
        def _label(ticker: str) -> str:
            if not universe:
                return ticker
            info = universe.get(ticker)
            name = info.get("name") if info else None
            return f"{ticker}({name})" if name and ticker.isdigit() else ticker

        weight_lines = "\n".join(
            f">   {_label(ticker):<24} {current_weights.get(ticker, 0):.1%} → {w:.1%}"
            for ticker, w in sorted(target_weights.items(), key=lambda x: -x[1])
            if w > 0
        )
        orders = "\n".join(f">   {line}" for line in order_log) if order_log else ">   변경 없음"

        deferred_section = ""
        if deferred_buys:
            lines = "\n".join(
                f">   :hourglass: {_label(d['ticker'])} {d['amount_krw']:,.0f}원 ({d['currency']}) — T+2 대기"
                for d in deferred_buys
            )
            deferred_section = f"\n*지연 매수 (합성 노출로 대체):*\n{lines}"

        conf_str = f" | 신뢰도 `{confidence:.0%}`" if confidence > 0 else ""
        text = (
            f":white_check_mark: *리밸런싱 완료*\n"
            f"> 레짐: `{regime}`{conf_str} | 자산: {total_krw:,.0f}원 | DD: {drawdown:+.1%}\n"
            f"*비중 변화:*\n{weight_lines}\n"
            f"*주문 내역:*\n{orders}"
            f"{deferred_section}"
        )
        self._send(text, mention=True)

    def send_monitor(
        self,
        regime: str,
        candidate: Optional[str],
        candidate_count: int,
        confirm_n: int,
        cooldown_remaining: int,
        features: Dict[str, float],
        confidence: float,
        blend_probs: Dict[str, float],
        total_krw: float,
        drawdown: float,
        drift_krw: float,
        drift_usd: float,
        trigger_krw: bool,
        trigger_usd: bool,
        reason_krw: str,
        reason_usd: str,
        hmm_probs: Optional[Dict[str, float]] = None,
        rf_probs: Optional[Dict[str, float]] = None,
    ) -> None:
        conf_str = f"`{confidence:.0%}`" if confidence > 0 else "—"

        if candidate and candidate != regime:
            cd_str = f", 쿨다운 {cooldown_remaining}일" if cooldown_remaining > 0 else ""
            filter_str = f"전환 대기 `{candidate}` ({candidate_count}/{confirm_n}회{cd_str}) → 확정 `{regime}`"
        else:
            filter_str = f"확정 `{regime}`"

        prob_lines = self._regime_prob_lines(hmm_probs, rf_probs, blend_probs)

        krw_icon = ":bell:" if trigger_krw else ":white_circle:"
        usd_icon = ":bell:" if trigger_usd else ":white_circle:"

        hy_str = f" | HY {features['hy_spread']:.2f}%" if "hy_spread" in features else ""
        curve_str = f" | 10Y-2Y {features['curve_10y2y']:+.2f}%" if "curve_10y2y" in features else ""

        text = (
            f":bar_chart: *모니터링 완료*\n"
            f"> 레짐: {filter_str} | 신뢰도 {conf_str}\n"
            f"{prob_lines}\n"
            f"> VIX `{features.get('vix', 0):.1f}` | "
            f"모멘텀1M `{features.get('momentum_1m', 0):+.1%}` | "
            f"모멘텀3M `{features.get('momentum_3m', 0):+.1%}` | "
            f"실현변동성 `{features.get('realized_vol', 0):.1%}`"
            f"{hy_str}{curve_str}\n"
            f"> 자산 `{total_krw:,.0f}원` | DD `{drawdown:+.1%}`\n"
            f"{krw_icon} KRW drift `{drift_krw:.1%}` → {reason_krw}\n"
            f"{usd_icon} USD drift `{drift_usd:.1%}` → {reason_usd}"
        )
        self._send(text)

    def send_dry_run(
        self,
        regime: str,
        candidate: Optional[str],
        candidate_count: int,
        confirm_n: int,
        cooldown_remaining: int,
        features: Dict[str, float],
        confidence: float,
        blend_probs: Dict[str, float],
        hmm_probs: Optional[Dict[str, float]] = None,
        rf_probs: Optional[Dict[str, float]] = None,
    ) -> None:
        conf_str = f"`{confidence:.0%}`" if confidence > 0 else "—"

        # 레짐 필터 상태
        if candidate and candidate != regime:
            cd_str = f", 쿨다운 {cooldown_remaining}일" if cooldown_remaining > 0 else ""
            filter_str = f"전환 대기 `{candidate}` ({candidate_count}/{confirm_n}회{cd_str})"
        else:
            filter_str = f"확정 `{regime}`"

        prob_lines = self._regime_prob_lines(hmm_probs, rf_probs, blend_probs)

        hy_str = f" | HY {features['hy_spread']:.2f}%" if "hy_spread" in features else ""
        curve_str = f" | 10Y-2Y {features['curve_10y2y']:+.2f}%" if "curve_10y2y" in features else ""

        text = (
            f":mag: *[Dry-Run] 레짐 분석 결과*\n"
            f"> 레짐: {filter_str} | 신뢰도 {conf_str}\n"
            f"{prob_lines}\n"
            f"> VIX `{features.get('vix', 0):.1f}` | "
            f"모멘텀1M `{features.get('momentum_1m', 0):+.1%}` | "
            f"모멘텀3M `{features.get('momentum_3m', 0):+.1%}` | "
            f"실현변동성 `{features.get('realized_vol', 0):.1%}`"
            f"{hy_str}{curve_str}"
        )
        self._send(text)

    def send_turnover_report(
        self,
        today: str,
        nav: float,
        daily_traded: float,
        daily_turnover: float,
        n_buy: int,
        n_sell: int,
        monthly_ym: str,
        monthly_traded: float,
        monthly_turnover: float,
    ) -> None:
        text = (
            f":repeat: *회전율 모니터링* ({today})\n"
            f"> 자산 `{nav:,.0f}원`\n"
            f"> 오늘 거래액 `{daily_traded:,.0f}원` (매수 {n_buy} / 매도 {n_sell}건) "
            f"→ 일일 회전율 `{daily_turnover:.1%}`\n"
            f"> {monthly_ym} 누적 거래액 `{monthly_traded:,.0f}원` "
            f"→ 월누적 회전율 `{monthly_turnover:.1%}`"
        )
        self._send(text)

    def send_order_error(self, ticker: str, error: Exception) -> None:
        self._send(f":warning: 주문 오류 `{ticker}`: {error}", mention=True)

    def send_system_error(self, error: Exception) -> None:
        tb = traceback.format_exc()
        self._send(f":x: *시스템 오류*\n```{tb[-1500:]}```", mention=True)
