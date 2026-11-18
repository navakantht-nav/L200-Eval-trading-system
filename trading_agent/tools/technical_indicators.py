"""Quantitative technical indicator and market regime detection tools."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

from .market_data import generate_price_series

if TYPE_CHECKING:
    from google.adk.tools import ToolContext


def _ema_series(values: list[float], span: int) -> list[float]:
    if not values:
        return []
    alpha = 2.0 / (span + 1.0)
    ema = [values[0]]
    for price in values[1:]:
        ema.append(alpha * price + (1.0 - alpha) * ema[-1])
    return ema


def _compute_rsi(values: list[float], period: int = 14) -> float:
    if len(values) < period + 1:
        return 50.0
    gains: list[float] = []
    losses: list[float] = []
    for idx in range(1, len(values)):
        delta = values[idx] - values[idx - 1]
        gains.append(max(0.0, delta))
        losses.append(max(0.0, -delta))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for gain, loss in zip(gains[period:], losses[period:], strict=False):
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 2)


def _compute_macd(values: list[float]) -> dict[str, float]:
    ema_fast = _ema_series(values, span=12)
    ema_slow = _ema_series(values, span=26)
    macd_line = [f - s for f, s in zip(ema_fast, ema_slow, strict=False)]
    signal_line = _ema_series(macd_line, span=9)
    hist = macd_line[-1] - signal_line[-1]
    return {
        "macd": round(macd_line[-1], 4),
        "signal": round(signal_line[-1], 4),
        "histogram": round(hist, 4),
    }


def _compute_bollinger(
    values: list[float], period: int = 20, num_std: float = 2.0
) -> dict[str, float]:
    window = values[-period:] if len(values) >= period else values
    sma = sum(window) / max(1, len(window))
    variance = sum((x - sma) ** 2 for x in window) / max(1, len(window))
    std_dev = math.sqrt(variance)
    upper = sma + num_std * std_dev
    lower = sma - num_std * std_dev
    band_width = upper - lower
    percent_b = (values[-1] - lower) / band_width if band_width > 0 else 0.5
    return {
        "middle_sma20": round(sma, 2),
        "upper_band": round(upper, 2),
        "lower_band": round(lower, 2),
        "percent_b": round(percent_b, 4),
        "bandwidth_pct": round((band_width / max(sma, 1e-6)) * 100.0, 3),
    }


def compute_technical_signals(
    symbol: str,
    period_bars: int = 90,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Computes RSI(14), MACD(12,26,9), Bollinger Bands(20,2), EMA trend, and alpha signal.

    Args:
        symbol: Ticker symbol to analyze (for example, 'GOOGL', 'NVDA', 'SPY').
        period_bars: Number of historical intraday bars to evaluate (35 to 240).
        tool_context: Optional ADK ToolContext to persist computed technical signals.

    Returns:
        Structured dictionary with RSI, MACD, Bollinger metrics, regime classification,
        suggested stop-loss anchor, and composite technical alpha score in [-1.0, 1.0].
    """
    clean_symbol = symbol.strip().upper()
    if not clean_symbol:
        return {"status": "error", "error": "Ticker symbol is required."}

    bars = max(35, min(240, int(period_bars)))
    prices, volumes, _ = generate_price_series(clean_symbol, bars=bars)

    last_price = prices[-1]
    rsi_14 = _compute_rsi(prices, period=14)
    macd = _compute_macd(prices)
    bollinger = _compute_bollinger(prices, period=20, num_std=2.0)
    ema_9 = round(_ema_series(prices, span=9)[-1], 2)
    ema_21 = round(_ema_series(prices, span=21)[-1], 2)
    vwap = round(
        sum(p * v for p, v in zip(prices, volumes, strict=False)) / max(1, sum(volumes)), 2
    )

    score = 0.0
    score += 0.35 if ema_9 > ema_21 else -0.35
    score += 0.25 if macd["histogram"] > 0 else -0.25
    if rsi_14 < 32.0:
        score += 0.30
    elif rsi_14 > 72.0:
        score -= 0.30
    else:
        score += 0.15 if rsi_14 >= 52.0 else -0.15
    score += 0.10 if last_price >= vwap else -0.10
    alpha_score = round(max(-1.0, min(1.0, score)), 3)

    if bollinger["bandwidth_pct"] > 1.8 and abs(alpha_score) >= 0.45:
        regime = "HIGH_VOLATILITY_BREAKOUT"
    elif ema_9 > ema_21 and macd["histogram"] > 0:
        regime = "TRENDING_BULLISH"
    elif ema_9 < ema_21 and macd["histogram"] < 0:
        regime = "TRENDING_BEARISH"
    else:
        regime = "MEAN_REVERTING_RANGE"

    atr_proxy = max(0.25, round((bollinger["upper_band"] - bollinger["lower_band"]) / 4.0, 2))
    suggested_long_stop = round(last_price - 1.8 * atr_proxy, 2)
    suggested_short_stop = round(last_price + 1.8 * atr_proxy, 2)

    payload = {
        "status": "success",
        "symbol": clean_symbol,
        "last_price": last_price,
        "vwap": vwap,
        "ema_9": ema_9,
        "ema_21": ema_21,
        "rsi_14": rsi_14,
        "macd": macd,
        "bollinger": bollinger,
        "market_regime": regime,
        "alpha_signal_score": alpha_score,
        "directional_conviction": (
            "LONG_BIAS"
            if alpha_score >= 0.25
            else "SHORT_BIAS"
            if alpha_score <= -0.25
            else "NEUTRAL_HOLD"
        ),
        "atr_proxy": atr_proxy,
        "suggested_long_stop_loss": suggested_long_stop,
        "suggested_short_stop_loss": suggested_short_stop,
    }

    if tool_context is not None:
        tool_context.state["technical_signals"] = payload

    return payload
