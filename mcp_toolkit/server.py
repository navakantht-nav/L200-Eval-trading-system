"""Standalone AlphaPulse Quantitative MCP Toolkit Server (Streamable HTTP, SSE & Stdio).

Exposes all AlphaPulse market data, L2 orderbook, technical indicators, pre-trade VaR,
A2UI v0.9 surface generation, and AP2 v0.2 cryptographic settlement tools via the
Model Context Protocol (MCP), plus MCP Resources (including `ui://alphapulse/trading-blotter`
for MCP Apps) and MCP Prompts.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from trading_agent.config import DEFAULT_CONFIG
from trading_agent.protocols.a2ui_surfaces import emit_a2ui_trading_surface
from trading_agent.protocols.ap2_mandates import issue_ap2_cart_mandate, settle_ap2_paper_trade
from trading_agent.tools import (
    compute_technical_signals,
    evaluate_pretrade_risk,
    execute_paper_trade,
    fetch_macro_and_news_sentiment,
    fetch_orderbook_microstructure,
    get_portfolio_state,
    get_reference_universe,
    stream_realtime_market_ticks,
)

from .oauth_guard import McpAuthorizationError, authorize_mcp_tool_call

# Registry of all tools exposed by the MCP Toolkit for both FastMCP and JSON-RPC dispatch
MCP_TOOL_REGISTRY: dict[str, Any] = {
    "stream_realtime_market_ticks": stream_realtime_market_ticks,
    "fetch_orderbook_microstructure": fetch_orderbook_microstructure,
    "fetch_macro_and_news_sentiment": fetch_macro_and_news_sentiment,
    "compute_technical_signals": compute_technical_signals,
    "evaluate_pretrade_risk": evaluate_pretrade_risk,
    "issue_ap2_cart_mandate": issue_ap2_cart_mandate,
    "settle_ap2_paper_trade": settle_ap2_paper_trade,
    "execute_paper_trade": execute_paper_trade,
    "get_portfolio_state": get_portfolio_state,
    "emit_a2ui_trading_surface": emit_a2ui_trading_surface,
}


def read_mcp_resource(uri: str) -> dict[str, Any]:
    """Resolves MCP resource URIs (`market://`, `risk://`, and `ui://` MCP Apps)."""
    if uri == "market://universe/reference":
        return {
            "uri": uri,
            "mimeType": "application/json",
            "data": get_reference_universe(),
        }
    if uri == "risk://governance/policy-limits":
        return {
            "uri": uri,
            "mimeType": "application/json",
            "data": {
                "trading_mode": DEFAULT_CONFIG.trading_mode,
                "max_single_order_usd": DEFAULT_CONFIG.max_single_order_usd,
                "max_position_weight_pct": DEFAULT_CONFIG.max_position_weight_pct,
                "max_portfolio_var_95_pct": DEFAULT_CONFIG.max_portfolio_var_95_pct,
                "restricted_tickers": sorted(DEFAULT_CONFIG.restricted_tickers),
            },
        }
    if uri == "ui://alphapulse/trading-blotter":
        portfolio = get_portfolio_state()
        return {
            "uri": uri,
            "mimeType": "text/html",
            "html": (
                "<div data-mcp-app='alphapulse-blotter'>"
                f"<h3>AlphaPulse Paper Blotter (NAV: ${portfolio['nav_usd']:,.2f})</h3>"
                f"<p>1d 95% VaR: {portfolio['var_95_pct_of_nav']:.2f}% | "
                f"Cash: ${portfolio['cash_usd']:,.2f}</p></div>"
            ),
        }
    raise KeyError(f"Unknown MCP resource URI: {uri}")


def dispatch_mcp_jsonrpc(
    payload: dict[str, Any],
    authorization_header: str | None = None,
    *,
    enforce_oauth: bool = True,
) -> dict[str, Any]:
    """Standards-compliant MCP JSON-RPC 2.0 dispatcher over Streamable HTTP (`/mcp`)."""
    req_id = payload.get("id", 1)
    method = payload.get("method", "")
    params = payload.get("params", {}) or {}

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2025-03-26",
                "serverInfo": {"name": "alphapulse-quant-mcp-toolkit", "version": "0.2.0"},
                "capabilities": {
                    "tools": {"listChanged": False},
                    "resources": {"subscribe": False, "listChanged": False},
                    "prompts": {"listChanged": False},
                },
            },
        }

    if method == "tools/list":
        tools_meta = [
            {
                "name": name,
                "description": (fn.__doc__ or "").strip().splitlines()[0],
            }
            for name, fn in MCP_TOOL_REGISTRY.items()
        ]
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": tools_meta}}

    if method == "resources/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "resources": [
                    {
                        "uri": "market://universe/reference",
                        "name": "Reference Universe Metadata",
                        "mimeType": "application/json",
                    },
                    {
                        "uri": "risk://governance/policy-limits",
                        "name": "Desk Risk Governance Limits",
                        "mimeType": "application/json",
                    },
                    {
                        "uri": "ui://alphapulse/trading-blotter",
                        "name": "AlphaPulse MCP App Trading Blotter",
                        "mimeType": "text/html",
                    },
                ]
            },
        }

    if method == "resources/read":
        uri = str(params.get("uri", ""))
        try:
            resource_data = read_mcp_resource(uri)
        except KeyError as exc:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32602, "message": str(exc)},
            }
        return {"jsonrpc": "2.0", "id": req_id, "result": {"contents": [resource_data]}}

    if method == "tools/call":
        tool_name = str(params.get("name", ""))
        arguments = dict(params.get("arguments", {}) or {})
        fn = MCP_TOOL_REGISTRY.get(tool_name)
        if fn is None:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Unknown MCP tool '{tool_name}'."},
            }
        try:
            claims = authorize_mcp_tool_call(
                tool_name,
                authorization_header,
                enforce_auth=enforce_oauth,
            )
        except McpAuthorizationError as auth_err:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32001,
                    "message": f"OAuth 2.1 Unauthorized: {auth_err}",
                },
            }

        result = fn(**arguments)
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "content": [{"type": "text", "text": json.dumps(result)}],
                "structuredContent": result,
                "isError": result.get("status") == "error",
                "_meta": {"authenticatedPrincipal": claims.get("sub")},
            },
        }

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Unsupported MCP method '{method}'."},
    }


def create_fastmcp_server() -> Any:
    """Constructs the official MCP v2 `MCPServer` (or v1 `FastMCP`) exposing all tools & resources."""
    try:
        from mcp.server.mcpserver import MCPServer

        mcp: Any = MCPServer(
            name="alphapulse-quant-mcp-toolkit",
            version="0.2.0",
            instructions="AlphaPulse Quantitative Trading, VaR Risk, A2UI v0.9 & AP2 v0.2 MCP Toolkit",
        )
    except ImportError:
        from mcp.server.fastmcp import FastMCP  # type: ignore[attr-defined,no-redef]

        mcp = FastMCP(name="alphapulse-quant-mcp-toolkit", stateless_http=True)

    @mcp.tool()
    def mcp_stream_realtime_market_ticks(symbol: str, window_ticks: int = 60) -> dict[str, Any]:
        """Streams recent realtime market ticks, VWAP, spread, and realized volatility."""
        return stream_realtime_market_ticks(symbol=symbol, window_ticks=window_ticks)

    @mcp.tool()
    def mcp_fetch_orderbook_microstructure(symbol: str, depth_levels: int = 5) -> dict[str, Any]:
        """Inspects Level-2 order book ladders, queue imbalance, and market impact slippage."""
        return fetch_orderbook_microstructure(symbol=symbol, depth_levels=depth_levels)

    @mcp.tool()
    def mcp_fetch_macro_and_news_sentiment(symbol: str) -> dict[str, Any]:
        """Retrieves structured macro risk backdrop, implied vol rank, and catalyst headlines."""
        return fetch_macro_and_news_sentiment(symbol=symbol)

    @mcp.tool()
    def mcp_compute_technical_signals(symbol: str, period_bars: int = 90) -> dict[str, Any]:
        """Computes RSI(14), MACD(12,26,9), Bollinger Bands(20,2), EMA trend, and regime."""
        return compute_technical_signals(symbol=symbol, period_bars=period_bars)

    @mcp.tool()
    def mcp_evaluate_pretrade_risk(
        symbol: str,
        side: str,
        quantity: int,
        limit_price: float,
        stop_loss_price: float | None = None,
    ) -> dict[str, Any]:
        """Evaluates pre-trade risk limits: notional cap, concentration, and 95% 1d VaR."""
        return evaluate_pretrade_risk(
            symbol=symbol,
            side=side,
            quantity=quantity,
            limit_price=limit_price,
            stop_loss_price=stop_loss_price,
        )

    @mcp.tool()
    def mcp_issue_ap2_cart_mandate(
        symbol: str,
        side: str,
        quantity: int,
        limit_price: float,
    ) -> dict[str, Any]:
        """Issues a risk-checked, broker-signed AP2 v0.2 CartMandate price lock."""
        return issue_ap2_cart_mandate(
            symbol=symbol,
            side=side,
            quantity=quantity,
            limit_price=limit_price,
        )

    @mcp.tool()
    def mcp_settle_ap2_paper_trade(
        cart_mandate: dict[str, Any],
        checkout_mandate: dict[str, Any],
        payment_mandate: dict[str, Any],
        order_type: str = "TWAP",
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Verifies AP2 double-lock cryptographic signatures and settles an idempotent paper trade."""
        return settle_ap2_paper_trade(
            cart_mandate=cart_mandate,
            checkout_mandate=checkout_mandate,
            payment_mandate=payment_mandate,
            order_type=order_type,
            idempotency_key=idempotency_key,
        )

    @mcp.tool()
    def mcp_get_portfolio_state() -> dict[str, Any]:
        """Returns mark-to-market paper portfolio positions, cash, NAV, PnL, and 95% VaR."""
        return get_portfolio_state()

    @mcp.tool()
    def mcp_emit_a2ui_trading_surface(
        symbol: str = "GOOGL",
        surface_kind: str = "MARKET_PULSE",
    ) -> dict[str, Any]:
        """Generates an A2UI v0.9 declarative UI surface and MCP App blotter descriptor."""
        return emit_a2ui_trading_surface(symbol=symbol, surface_kind=surface_kind)

    @mcp.resource("market://universe/reference")
    def resource_market_universe() -> str:
        """Reference market universe anchor prices, volatilities, and sectors."""
        return json.dumps(read_mcp_resource("market://universe/reference")["data"])

    @mcp.resource("risk://governance/policy-limits")
    def resource_risk_limits() -> str:
        """House risk governance limits (notional, concentration, VaR, restricted list)."""
        return json.dumps(read_mcp_resource("risk://governance/policy-limits")["data"])

    @mcp.resource("ui://alphapulse/trading-blotter")
    def resource_ui_blotter() -> str:
        """Interactive HTML MCP App trading blotter widget."""
        return str(read_mcp_resource("ui://alphapulse/trading-blotter")["html"])

    @mcp.prompt()
    def pretrade_risk_memo(symbol: str, target_notional_usd: float = 20000.0) -> str:
        """Structured institutional prompt template for a pre-trade risk & AP2 memo."""
        return (
            f"Run a parallel market, L2 microstructure, and technical regime scan on {symbol}. "
            f"Propose a trade up to ${target_notional_usd:,.0f}, verify 95% 1d VaR, and issue "
            "a signed AP2 CartMandate plus an A2UI v0.9 approval surface."
        )

    return mcp


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the AlphaPulse MCP Toolkit Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default="streamable-http",
    )
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8081)
    args = parser.parse_args()

    mcp = create_fastmcp_server()
    if args.transport == "stdio":
        mcp.run(transport="stdio")
    else:
        import uvicorn

        asgi_app = (
            mcp.streamable_http_app()
            if args.transport == "streamable-http"
            else mcp.sse_app()
        )
        uvicorn.run(asgi_app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
