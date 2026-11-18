"""Agent2Agent (A2A) AgentCard metadata, protocol extensions (A2UI + AP2), and sub-agents."""

from __future__ import annotations

from typing import Any

from ..config import DEFAULT_CONFIG
from ..tools.market_data import fetch_macro_and_news_sentiment
from .a2ui_surfaces import A2UI_EXTENSION_URI_V09
from .ap2_mandates import AP2_EXTENSION_URI


def build_agent_card_dict(base_url: str = "http://localhost:8080") -> dict[str, Any]:
    """Builds a standards-compliant A2A `AgentCard` advertising A2UI v0.9, AP2 v0.2, & OAuth2."""
    clean_base = base_url.rstrip("/")
    return {
        "name": "AlphaPulse Realtime Trading & Risk Desk",
        "description": (
            "GCP-Native ADKv2 Multi-Agent Quantitative Trading System featuring "
            "parallel market intelligence, deterministic VaR circuit breakers, "
            "MCP Streamable-HTTP tools, A2UI v0.9 declarative UI surfaces, and "
            "AP2 v0.2 cryptographic settlement mandates."
        ),
        "url": f"{clean_base}/a2a",
        "version": "0.2.0",
        "protocolVersion": "0.2.6",
        "defaultInputModes": ["text/plain", "application/json"],
        "defaultOutputModes": [
            "text/plain",
            "application/json",
            "application/a2ui+json",
        ],
        "capabilities": {
            "streaming": True,
            "pushNotifications": True,
            "stateTransitionHistory": True,
            "extensions": [
                {
                    "uri": A2UI_EXTENSION_URI_V09,
                    "description": "Declarative A2UI v0.9 generative surfaces for market & risk cards.",
                    "required": False,
                },
                {
                    "uri": AP2_EXTENSION_URI,
                    "description": "AP2 v0.2 double-lock cryptographic mandates for trade settlement.",
                    "required": False,
                },
            ],
        },
        "securitySchemes": {
            "alphapulse_oauth2": {
                "type": "oauth2",
                "flows": {
                    "authorizationCode": {
                        "authorizationUrl": f"{DEFAULT_CONFIG.oauth_issuer}/oauth2/authorize",
                        "tokenUrl": f"{DEFAULT_CONFIG.oauth_issuer}/oauth2/token",
                        "scopes": {
                            "trading:read": "Read realtime ticks, orderbooks, and portfolio risk",
                            "trading:simulate": "Simulate pre-trade VaR and issue AP2 CartMandates",
                            "trading:execute": "Settle paper trades with signed AP2 mandates",
                        },
                    }
                },
            },
            "gcp_agent_identity_oidc": {
                "type": "openIdConnect",
                "openIdConnectUrl": "https://accounts.google.com/.well-known/openid-configuration",
            },
        },
        "security": [{"alphapulse_oauth2": ["trading:read"]}],
        "skills": [
            {
                "id": "realtime_market_intelligence",
                "name": "Realtime Market & L2 Microstructure Intelligence",
                "description": (
                    "Streams intraday ticks, VWAP, Level-2 orderbook imbalance, RSI/MACD/Bollinger "
                    "regimes, and macro catalysts in parallel."
                ),
                "tags": ["market-data", "orderbook", "technical-analysis", "streaming"],
                "examples": [
                    "Give me a realtime market pulse and L2 orderbook check on NVDA.",
                    "What is the RSI, MACD, and volatility regime for GOOGL?",
                ],
            },
            {
                "id": "pretrade_var_and_ap2_settlement",
                "name": "Pre-Trade VaR Risk Gate & AP2 Mandate Settlement",
                "description": (
                    "Evaluates 1-day 95% portfolio VaR and concentration caps, locks quotes via "
                    "AP2 CartMandates, renders A2UI v0.9 approval cards, and settles paper trades."
                ),
                "tags": ["risk", "var", "ap2", "a2ui", "paper-trading"],
                "examples": [
                    "Propose a $20,000 BUY on GOOGL, verify 95% VaR, and stage an AP2 CartMandate.",
                    "Buy 500 shares of NVDA and resize automatically if it breaches desk limits.",
                ],
            },
        ],
    }


def build_macro_specialist_card_dict(base_url: str = "http://localhost:8080") -> dict[str, Any]:
    """AgentCard for the federated Macro & Catalyst A2A sub-agent."""
    clean_base = base_url.rstrip("/")
    return {
        "name": "AlphaPulse Macro & Catalyst A2A Specialist",
        "description": "Remote A2A specialist agent providing macro backdrop, IV percentile, and catalyst analysis.",
        "url": f"{clean_base}/a2a/macro",
        "version": "0.2.0",
        "protocolVersion": "0.2.6",
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["application/json", "text/plain"],
        "capabilities": {"streaming": True, "pushNotifications": False},
        "skills": [
            {
                "id": "macro_catalyst_scan",
                "name": "Macro & Catalyst Scan",
                "description": "Returns FOMC/rates backdrop, implied vol rank, and sector catalysts.",
                "tags": ["macro", "sentiment", "a2a"],
            }
        ],
    }


def handle_macro_a2a_rpc(rpc_request: dict[str, Any]) -> dict[str, Any]:
    """Lightweight JSON-RPC 2.0 A2A task handler for the Macro Specialist endpoint."""
    req_id = rpc_request.get("id", "1")
    method = rpc_request.get("method", "message/send")
    params = rpc_request.get("params", {}) or {}

    # Extract ticker symbol from A2A message text parts (default GOOGL)
    symbol = "GOOGL"
    message = params.get("message", {}) or {}
    for part in message.get("parts", []) or []:
        text = str(part.get("text", "")).upper()
        for candidate in ("GOOGL", "NVDA", "MSFT", "AAPL", "SPY", "BTC-USD"):
            if candidate in text:
                symbol = candidate
                break

    intel = fetch_macro_and_news_sentiment(symbol)
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "result": {
            "kind": "task",
            "id": f"macro-task-{req_id}",
            "status": {"state": "completed"},
            "artifacts": [
                {
                    "artifactId": f"macro-{symbol.lower()}",
                    "name": f"{symbol} Macro Intelligence",
                    "parts": [
                        {"kind": "data", "data": intel},
                        {
                            "kind": "text",
                            "text": (
                                f"[A2A:{method}] {symbol} sentiment={intel['sentiment_regime']} "
                                f"({intel['composite_sentiment_score']:+.2f}), "
                                f"IV_pct={intel['implied_vol_percentile']}%"
                            ),
                        },
                    ],
                }
            ],
        },
    }
