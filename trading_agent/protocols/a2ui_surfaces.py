"""A2UI v0.9 (Agent-to-User Interface) declarative UI surface builders & MCP Apps widgets.

Generates safe, framework-agnostic declarative JSON UI payloads (`createSurface`,
`updateComponents`, `updateDataModel`) conforming to A2UI v0.9 alongside ADKv2
`UiWidget(provider='mcp', ...)` metadata so web, mobile, and canvas renderers
can display interactive trading blotters and pre-trade AP2 approval cards
without executing untrusted LLM-generated code.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..tools.execution import get_portfolio_state
from ..tools.market_data import fetch_orderbook_microstructure, stream_realtime_market_ticks
from ..tools.technical_indicators import compute_technical_signals

if TYPE_CHECKING:
    from google.adk.tools import ToolContext

A2UI_EXTENSION_URI_V09 = "https://a2ui.org/specification/v0.9"
A2UI_MIME_TYPE = "application/a2ui+json"
BASIC_CATALOG_URI = "https://a2ui.org/catalogs/basic/v0.9"


def build_market_pulse_a2ui_messages(
    quote: dict[str, Any],
    signals: dict[str, Any],
    orderbook: dict[str, Any],
) -> list[dict[str, Any]]:
    """Builds a flat-component A2UI v0.9 message sequence for a live ticker dashboard."""
    symbol = quote["symbol"]
    surface_id = f"market-pulse-{symbol.lower()}"

    return [
        {
            "version": "v0.9",
            "createSurface": {
                "surfaceId": surface_id,
                "catalogId": BASIC_CATALOG_URI,
            },
        },
        {
            "version": "v0.9",
            "updateComponents": {
                "surfaceId": surface_id,
                "components": [
                    {
                        "id": "root",
                        "component": "Card",
                        "title": {"path": "/header/title"},
                        "subtitle": {"path": "/header/subtitle"},
                        "child": "body_col",
                    },
                    {
                        "id": "body_col",
                        "component": "Column",
                        "children": ["kpi_row", "signal_row", "action_row"],
                    },
                    {
                        "id": "kpi_row",
                        "component": "Row",
                        "children": ["metric_price", "metric_vwap", "metric_imbalance"],
                    },
                    {
                        "id": "metric_price",
                        "component": "Text",
                        "text": {"path": "/metrics/lastPriceLabel"},
                        "variant": "headline",
                    },
                    {
                        "id": "metric_vwap",
                        "component": "Text",
                        "text": {"path": "/metrics/vwapLabel"},
                        "variant": "body",
                    },
                    {
                        "id": "metric_imbalance",
                        "component": "Text",
                        "text": {"path": "/metrics/imbalanceLabel"},
                        "variant": "body",
                    },
                    {
                        "id": "signal_row",
                        "component": "Row",
                        "children": ["txt_regime", "txt_indicators"],
                    },
                    {
                        "id": "txt_regime",
                        "component": "Text",
                        "text": {"path": "/signals/regimeBadge"},
                        "variant": "subtitle",
                    },
                    {
                        "id": "txt_indicators",
                        "component": "Text",
                        "text": {"path": "/signals/indicatorSummary"},
                        "variant": "caption",
                    },
                    {
                        "id": "action_row",
                        "component": "Row",
                        "children": ["btn_stage_trade"],
                    },
                    {
                        "id": "btn_stage_trade",
                        "component": "Button",
                        "label": f"Stage Risk-Checked {symbol} Order",
                        "action": {
                            "name": "stage_ap2_order",
                            "context": {
                                "symbol": {"path": "/header/symbol"},
                                "referencePrice": {"path": "/metrics/rawPrice"},
                            },
                        },
                    },
                ],
            },
        },
        {
            "version": "v0.9",
            "updateDataModel": {
                "surfaceId": surface_id,
                "path": "/",
                "value": {
                    "header": {
                        "symbol": symbol,
                        "title": f"{symbol} Realtime Quantitative Pulse",
                        "subtitle": f"Sector: {quote['sector']} | Spread: {quote['spread_bps']} bps",
                    },
                    "metrics": {
                        "rawPrice": quote["last_price"],
                        "lastPriceLabel": f"Last: ${quote['last_price']:,.2f} ({quote['window_change_pct']:+.2f}%)",
                        "vwapLabel": f"VWAP: ${quote['vwap']:,.2f}",
                        "imbalanceLabel": (
                            f"L2 Imbalance: {orderbook['orderbook_imbalance']:+.2f} "
                            f"({orderbook['pressure_signal']})"
                        ),
                    },
                    "signals": {
                        "regimeBadge": (
                            f"Regime: {signals['market_regime']} | "
                            f"Conviction: {signals['directional_conviction']}"
                        ),
                        "indicatorSummary": (
                            f"RSI(14): {signals['rsi_14']} | "
                            f"MACD Hist: {signals['macd']['histogram']:+.4f} | "
                            f"Alpha: {signals['alpha_signal_score']:+.2f}"
                        ),
                    },
                },
            },
        },
    ]


def build_ap2_approval_a2ui_messages(
    cart_mandate: dict[str, Any],
    risk_verdict: dict[str, Any],
) -> list[dict[str, Any]]:
    """Builds an interactive A2UI v0.9 pre-trade VaR & AP2 Mandate signing surface."""
    cart_id = cart_mandate["cart_id"]
    surface_id = f"ap2-approval-{cart_id}"

    return [
        {
            "version": "v0.9",
            "createSurface": {
                "surfaceId": surface_id,
                "catalogId": BASIC_CATALOG_URI,
            },
        },
        {
            "version": "v0.9",
            "updateComponents": {
                "surfaceId": surface_id,
                "components": [
                    {
                        "id": "approval_card",
                        "component": "Card",
                        "title": {"path": "/title"},
                        "subtitle": {"path": "/subtitle"},
                        "child": "approval_col",
                    },
                    {
                        "id": "approval_col",
                        "component": "Column",
                        "children": ["order_terms", "risk_metrics", "digest_txt", "buttons_row"],
                    },
                    {
                        "id": "order_terms",
                        "component": "Text",
                        "text": {"path": "/orderTerms"},
                        "variant": "headline",
                    },
                    {
                        "id": "risk_metrics",
                        "component": "Text",
                        "text": {"path": "/riskSummary"},
                        "variant": "body",
                    },
                    {
                        "id": "digest_txt",
                        "component": "Text",
                        "text": {"path": "/digestLine"},
                        "variant": "caption",
                    },
                    {
                        "id": "buttons_row",
                        "component": "Row",
                        "children": ["btn_sign_settle", "btn_cancel"],
                    },
                    {
                        "id": "btn_sign_settle",
                        "component": "Button",
                        "label": "Sign AP2 Mandate & Settle Paper Trade",
                        "action": {
                            "name": "sign_and_settle_ap2",
                            "context": {
                                "cartId": {"path": "/cartId"},
                                "cartDigest": {"path": "/cartDigest"},
                            },
                        },
                    },
                    {
                        "id": "btn_cancel",
                        "component": "Button",
                        "label": "Reject Order",
                        "action": {
                            "name": "reject_ap2_order",
                            "context": {"cartId": {"path": "/cartId"}},
                        },
                    },
                ],
            },
        },
        {
            "version": "v0.9",
            "updateDataModel": {
                "surfaceId": surface_id,
                "path": "/",
                "value": {
                    "cartId": cart_id,
                    "cartDigest": cart_mandate["cart_digest"],
                    "title": f"AP2 Double-Lock Trade Mandate ({cart_mandate['symbol']})",
                    "subtitle": f"Pre-Trade Risk Verdict: {risk_verdict.get('verdict', 'APPROVED')}",
                    "orderTerms": (
                        f"{cart_mandate['side']} {cart_mandate['quantity']} {cart_mandate['symbol']} "
                        f"@ ${cart_mandate['locked_limit_price']:,.2f} "
                        f"(${cart_mandate['total_notional_usd']:,.2f})"
                    ),
                    "riskSummary": (
                        f"Post-Trade Concentration: {risk_verdict.get('post_trade_concentration_pct', 0):.2f}% | "
                        f"Post-Trade 95% 1d VaR: {risk_verdict.get('post_trade_var_95_pct', 0):.2f}% of NAV"
                    ),
                    "digestLine": f"SHA-256 Price-Lock Digest: {cart_mandate['cart_digest'][:24]}...",
                },
            },
        },
    ]


def emit_a2ui_trading_surface(
    symbol: str = "GOOGL",
    surface_kind: str = "MARKET_PULSE",
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Generates a validated A2UI v0.9 declarative UI surface and MCP App widget descriptor.

    Args:
        symbol: Ticker symbol to visualize (for example, 'GOOGL', 'NVDA').
        surface_kind: Either 'MARKET_PULSE' or 'PORTFOLIO_BLOTTER'.
        tool_context: Optional ADK ToolContext used to attach `UiWidget` & store surface state.

    Returns:
        Dictionary containing A2UI v0.9 messages (`createSurface`, `updateComponents`,
        `updateDataModel`) and the MCP Apps `ui://alphapulse/trading-blotter` descriptor.
    """
    clean_symbol = symbol.strip().upper()
    kind = surface_kind.strip().upper()

    quote = stream_realtime_market_ticks(clean_symbol, window_ticks=60, tool_context=tool_context)
    signals = compute_technical_signals(clean_symbol, period_bars=90, tool_context=tool_context)
    orderbook = fetch_orderbook_microstructure(clean_symbol, depth_levels=5, tool_context=tool_context)
    portfolio = get_portfolio_state(tool_context=tool_context)

    messages = build_market_pulse_a2ui_messages(quote, signals, orderbook)
    mcp_ui_widget = {
        "id": f"blotter-{clean_symbol.lower()}",
        "provider": "mcp",
        "payload": {
            "resource_uri": "ui://alphapulse/trading-blotter",
            "tool": {"name": "emit_a2ui_trading_surface", "surface_kind": kind},
            "tool_args": {"symbol": clean_symbol, "nav_usd": portfolio["nav_usd"]},
        },
    }

    envelope = {
        "status": "success",
        "protocol": "A2UI/v0.9",
        "mime_type": A2UI_MIME_TYPE,
        "surface_kind": kind,
        "symbol": clean_symbol,
        "a2ui_messages": messages,
        "mcp_ui_widget": mcp_ui_widget,
    }
    if tool_context is not None:
        tool_context.state["last_a2ui_surface"] = envelope
    return envelope
