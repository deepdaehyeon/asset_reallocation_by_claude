"""규칙 기반 시장 레짐 감지."""

REGIMES = ["Risk-On", "Neutral", "Risk-Off", "High-Vol"]


def detect_regime(features: dict) -> str:
    """
    피처 딕셔너리로부터 시장 레짐을 분류한다.

    우선순위:
      1. High-Vol  — 실현변동성 또는 VIX가 극단적으로 높을 때
      2. Risk-Off  — 베어리시 신호 2개 이상
      3. Risk-On   — 불리시 신호 2개 이상
      4. Neutral   — 혼재

    Returns:
        "Risk-On" | "Neutral" | "Risk-Off" | "High-Vol"
    """
    vix = features["vix"]
    mom1m = features["momentum_1m"]
    mom3m = features["momentum_3m"]
    rvol = features["realized_vol"]
    credit = features["credit_signal"]

    if rvol > 0.25 or vix > 35:
        return "High-Vol"

    bearish = sum([
        mom1m < -0.03,
        mom3m < -0.05,
        vix > 25,
        credit < -0.03,
    ])

    bullish = sum([
        mom1m > 0.02,
        mom3m > 0.04,
        vix < 18,
        credit > 0.02,
    ])

    if bearish >= 2:
        return "Risk-Off"
    if bullish >= 2:
        return "Risk-On"
    return "Neutral"
