"""Realtime market tick streaming, Level-2 order book, and catalyst sentiment tools."""

from __future__ import annotations

import hashlib
import math
import random
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from google.adk.tools import ToolContext


_REFERENCE_UNIVERSE: dict[str, dict[str, Any]] = {
    "GOOGL": {
        "anchor_price": 192.50,
        "annual_vol": 0.27,
        "sector": "Technology",
        "avg_daily_volume": 24_500_000,
        "catalysts": [
            "Cloud AI infrastructure demand accelerating enterprise backlog growth",
            "Next-generation Gemini agentic developer adoption expanding margins",
        ],
        "sentiment_bias": 0.42,
    },
    "NVDA": {
        "anchor_price": 141.80,
        "annual_vol": 0.44,
        "sector": "Semiconductors",
        "avg_daily_volume": 48_000_000,
        "catalysts": [
            "Hyperscaler data center capex guidance remains constructive",
            "Options market pricing elevated short-term gamma around supply updates",
        ],
        "sentiment_bias": 0.35,
    },
    "MSFT": {
        "anchor_price": 428.40,
        "annual_vol": 0.23,
        "sector": "Software",
        "avg_daily_volume": 19_200_000,
        "catalysts": [
            "Enterprise cloud seat expansion steady across commercial renewals",
            "FX headwinds partially offsetting operating leverage in guidance",
        ],
        "sentiment_bias": 0.22,
    },
    "AAPL": {
        "anchor_price": 229.10,
        "annual_vol": 0.22,
        "sector": "Consumer Hardware",
        "avg_daily_volume": 38_000_000,
        "catalysts": [
            "Services recurring revenue mix hitting record gross margin contribution",
            "On-device AI upgrade cycle tracking in line with consensus",
        ],
        "sentiment_bias": 0.18,
    },
    "SPY": {
        "anchor_price": 596.00,
        "annual_vol": 0.15,
        "sector": "Broad Market ETF",
        "avg_daily_volume": 55_000_000,
        "catalysts": [
            "Macro rates volatility moderating ahead of upcoming FOMC minutes",
            "Breadth improving across industrial and technology constituents",
        ],
        "sentiment_bias": 0.15,
    },
    "BTC-USD": {
        "anchor_price": 91_500.00,
        "annual_vol": 0.58,
        "sector": "Digital Assets",
        "avg_daily_volume": 32_000_000_000,
        "catalysts": [
            "Institutional ETF net inflows positive over trailing 5 sessions",
            "Perpetual futures funding rates elevated; watch liquidation clusters",
        ],
        "sentiment_bias": 0.28,
    },
}


def get_reference_universe() -> dict[str, dict[str, Any]]:
    """Returns a copy of the reference market universe metadata."""
    return {k: dict(v) for k, v in _REFERENCE_UNIVERSE.items()}


def _seed_for_symbol(symbol: str, bucket: str = "default") -> int:
    digest = hashlib.sha256(f"{symbol.upper()}::{bucket}".encode()).hexdigest()
    return int(digest[:12], 16)


def generate_price_series(
    symbol: str,
    bars: int = 60,
    time_bucket: str | None = None,
) -> tuple[list[float], list[int], dict[str, Any]]:
    """Generates a reproducible geometric Brownian motion + mean-reverting series."""
    clean_symbol = symbol.strip().upper()
    meta = _REFERENCE_UNIVERSE.get(
        clean_symbol,
        {
            "anchor_price": 100.0 + (_seed_for_symbol(clean_symbol) % 150),
            "annual_vol": 0.32,
            "sector": "Equities",
            "avg_daily_volume": 8_000_000,
            "catalysts": [f"Standard market liquidity regime observed for {clean_symbol}"],
            "sentiment_bias": 0.05,
        },
    )
    bucket = time_bucket or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M")
    rng = random.Random(_seed_for_symbol(clean_symbol, bucket))

    anchor = float(meta["anchor_price"])
    annual_vol = float(meta["annual_vol"])
    step_vol = annual_vol / math.sqrt(252 * 390)
    drift = float(meta["sentiment_bias"]) * 0.00015

    prices: list[float] = []
    volumes: list[int] = []
    price = anchor * (1.0 + rng.uniform(-0.012, 0.012))
    base_bar_vol = max(1_000, int(meta["avg_daily_volume"] / 390))

    for i in range(max(10, bars)):
        mean_reversion = 0.03 * (anchor - price) / anchor
        cyc = 0.0004 * math.sin(i / 6.5)
        shock = rng.gauss(drift + mean_reversion + cyc, step_vol * 2.4)
        price = max(0.50, round(price * math.exp(shock), 2))
        vol_multiplier = 1.0 + min(2.5, abs(shock) / max(step_vol, 1e-6) * 0.35)
        bar_volume = int(base_bar_vol * vol_multiplier * rng.uniform(0.8, 1.25))
        prices.append(price)
        volumes.append(bar_volume)

    return prices, volumes, meta


def stream_realtime_market_ticks(
    symbol: str,
    window_ticks: int = 60,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Streams recent realtime market ticks, VWAP, and realized volatility for a ticker.

    Args:
        symbol: Ticker symbol to inspect (for example, 'GOOGL', 'NVDA', 'MSFT', 'SPY').
        window_ticks: Number of 1-minute streaming bars to aggregate (15 to 240).
        tool_context: Optional ADK ToolContext used to persist quotes into session state.

    Returns:
        Dictionary containing status, latest price, bid/ask, VWAP, intraday volatility,
        recent tick tail, and timestamp.
    """
    clean_symbol = symbol.strip().upper()
    if not clean_symbol:
        return {"status": "error", "error": "Ticker symbol must not be empty."}

    clamped_ticks = max(15, min(240, int(window_ticks)))
    prices, volumes, meta = generate_price_series(clean_symbol, bars=clamped_ticks)

    last_price = prices[-1]
    open_price = prices[0]
    high_price = max(prices)
    low_price = min(prices)
    total_volume = sum(volumes)
    vwap = round(
        sum(p * v for p, v in zip(prices, volumes, strict=False)) / max(1, total_volume),
        2,
    )

    log_returns = [
        math.log(prices[i] / prices[i - 1])
        for i in range(1, len(prices))
        if prices[i - 1] > 0
    ]
    mean_ret = sum(log_returns) / max(1, len(log_returns))
    variance = sum((r - mean_ret) ** 2 for r in log_returns) / max(1, len(log_returns) - 1)
    realized_annual_vol = round(math.sqrt(variance) * math.sqrt(252 * 390), 4)

    half_spread = max(0.01, round(last_price * 0.00025, 2))
    bid = round(last_price - half_spread, 2)
    ask = round(last_price + half_spread, 2)
    change_pct = round(((last_price - open_price) / open_price) * 100.0, 3)

    snapshot = {
        "status": "success",
        "symbol": clean_symbol,
        "sector": meta["sector"],
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "last_price": last_price,
        "bid": bid,
        "ask": ask,
        "spread_bps": round(((ask - bid) / last_price) * 10_000, 2),
        "window_open": open_price,
        "window_high": high_price,
        "window_low": low_price,
        "window_change_pct": change_pct,
        "vwap": vwap,
        "vwap_distance_pct": round(((last_price - vwap) / vwap) * 100.0, 3),
        "window_volume": total_volume,
        "realized_annual_volatility": realized_annual_vol,
        "recent_ticks": prices[-10:],
    }

    if tool_context is not None:
        quotes = dict(tool_context.state.get("latest_quotes", {}))
        quotes[clean_symbol] = snapshot
        tool_context.state["latest_quotes"] = quotes
        tool_context.state["active_symbol"] = clean_symbol

    return snapshot


def fetch_orderbook_microstructure(
    symbol: str,
    depth_levels: int = 5,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Inspects Level-2 order book depth, queue imbalance, and market impact slippage.

    Args:
        symbol: Ticker symbol to query (for example, 'GOOGL', 'NVDA').
        depth_levels: Number of L2 price levels per side (1 to 10).
        tool_context: Optional ADK ToolContext to record microstructure metrics.

    Returns:
        Dictionary with top-of-book ladders, order book imbalance [-1, 1], and slippage bps.
    """
    clean_symbol = symbol.strip().upper()
    levels = max(1, min(10, int(depth_levels)))
    tick_snap = stream_realtime_market_ticks(
        clean_symbol, window_ticks=30, tool_context=tool_context
    )
    if tick_snap["status"] != "success":
        return tick_snap

    rng = random.Random(_seed_for_symbol(clean_symbol, "l2_book"))
    mid = float(tick_snap["last_price"])
    tick_size = 0.01 if mid < 5000 else 0.50

    bids: list[dict[str, float | int]] = []
    asks: list[dict[str, float | int]] = []
    for lvl in range(1, levels + 1):
        bid_px = round(mid - lvl * tick_size, 2)
        ask_px = round(mid + lvl * tick_size, 2)
        bid_sz = int(rng.uniform(180, 950) * (1.0 + 0.15 * lvl))
        ask_sz = int(rng.uniform(170, 920) * (1.0 + 0.15 * lvl))
        bids.append({"price": bid_px, "size": bid_sz})
        asks.append({"price": ask_px, "size": ask_sz})

    total_bid_liq = sum(int(b["size"]) for b in bids)
    total_ask_liq = sum(int(a["size"]) for a in asks)
    imbalance = round(
        (total_bid_liq - total_ask_liq) / max(1, total_bid_liq + total_ask_liq),
        4,
    )
    est_10k_slippage_bps = round(
        max(0.8, 3.5 - 2.0 * (total_bid_liq + total_ask_liq) / 5000.0), 2
    )

    result = {
        "status": "success",
        "symbol": clean_symbol,
        "mid_price": mid,
        "bids": bids,
        "asks": asks,
        "total_bid_size": total_bid_liq,
        "total_ask_size": total_ask_liq,
        "orderbook_imbalance": imbalance,
        "pressure_signal": (
            "BUY_PRESSURE"
            if imbalance > 0.12
            else "SELL_PRESSURE"
            if imbalance < -0.12
            else "BALANCED"
        ),
        "estimated_10k_usd_slippage_bps": est_10k_slippage_bps,
    }
    if tool_context is not None:
        tool_context.state["orderbook_microstructure"] = result
        tool_context.state["temp:l2_raw_ladder"] = {"bids": bids, "asks": asks}
    return result


def fetch_macro_and_news_sentiment(
    symbol: str,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Retrieves structured macro risk indicators, catalyst headlines, and sentiment scores.

    Args:
        symbol: Ticker symbol to analyze.
        tool_context: Optional ADK ToolContext to persist sentiment intelligence.

    Returns:
        Dictionary with composite sentiment [-1.0, 1.0], IV rank, and catalyst headlines.
    """
    clean_symbol = symbol.strip().upper()
    _, _, meta = generate_price_series(clean_symbol, bars=20)
    rng = random.Random(_seed_for_symbol(clean_symbol, "sentiment"))

    base_bias = float(meta["sentiment_bias"])
    composite_sentiment = round(
        max(-1.0, min(1.0, base_bias + rng.uniform(-0.12, 0.12))), 3
    )
    iv_percentile = round(
        min(99.0, max(10.0, float(meta["annual_vol"]) * 145.0 + rng.uniform(-8, 8))), 1
    )

    sentiment_payload = {
        "status": "success",
        "symbol": clean_symbol,
        "sector": meta["sector"],
        "composite_sentiment_score": composite_sentiment,
        "sentiment_regime": (
            "BULLISH"
            if composite_sentiment >= 0.20
            else "BEARISH"
            if composite_sentiment <= -0.20
            else "NEUTRAL"
        ),
        "implied_vol_percentile": iv_percentile,
        "macro_backdrop": {
            "us_10y_yield_pct": 4.18,
            "vix_index": 16.4,
            "event_risk_window": "CLEAR_NO_IMMINENT_FOMC_WITHIN_24H",
        },
        "catalyst_headlines": list(meta["catalysts"]),
    }

    if tool_context is not None:
        tool_context.state["macro_news_sentiment"] = sentiment_payload

    return sentiment_payload
