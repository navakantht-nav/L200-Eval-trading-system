"""Multi-Protocol FastAPI Gateway: ADKv2 HTTP/SSE/WS Bidi, MCP `/mcp`, A2A, A2UI & AP2."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from mcp_toolkit.server import dispatch_mcp_jsonrpc
from trading_agent.config import DEFAULT_CONFIG
from trading_agent.protocols.a2a_integration import (
    build_agent_card_dict,
    build_macro_specialist_card_dict,
    handle_macro_a2a_rpc,
)
from trading_agent.protocols.a2ui_surfaces import (
    build_ap2_approval_a2ui_messages,
    emit_a2ui_trading_surface,
)
from trading_agent.protocols.ap2_mandates import (
    CartMandate,
    issue_ap2_cart_mandate,
    settle_ap2_paper_trade,
    sign_user_ap2_mandates,
)
from trading_agent.protocols.oauth_config import (
    get_oauth_protected_resource_metadata,
    mint_oauth_jwt,
)
from trading_agent.tools import (
    get_portfolio_state,
    stream_realtime_market_ticks,
)

from .console_html import CONSOLE_HTML

_REPO_ROOT = Path(__file__).resolve().parent.parent


class TokenMintRequest(BaseModel):
    subject: str = "spiffe://gcp.agent.identity/ns/alphapulse/user/demo-trader"
    scopes: list[str] = Field(
        default_factory=lambda: ["trading:read", "trading:simulate", "trading:execute"]
    )


class StageAndSettleRequest(BaseModel):
    symbol: str = "GOOGL"
    side: str = "BUY"
    quantity: int = 25
    limit_price: float | None = None
    order_type: str = "TWAP"
    trader_principal: str = "spiffe://alphapulse.local/user/demo-trader"


def create_app(*, include_adk_runtime_routes: bool = True) -> FastAPI:
    """Creates the unified FastAPI application mounting ADKv2, MCP, A2A, A2UI, & AP2 routes."""
    base_app: FastAPI | None = None
    if include_adk_runtime_routes and os.getenv("ALPHAPULSE_LIGHT_APP") != "1":
        try:
            from google.adk.cli.fast_api import get_fast_api_app

            base_app = get_fast_api_app(
                agents_dir=str(_REPO_ROOT),
                web=True,
                a2a=True,
                trace_to_cloud=DEFAULT_CONFIG.enable_cloud_trace,
                otel_to_cloud=DEFAULT_CONFIG.enable_cloud_trace,
            )
        except Exception:
            base_app = None

    api = base_app or FastAPI(
        title="AlphaPulse Realtime Multi-Agent Trading System",
        version="0.2.0",
    )

    @api.get("/healthz")
    async def healthcheck() -> dict[str, Any]:
        return {
            "status": "healthy",
            "app": DEFAULT_CONFIG.app_name,
            "trading_mode": DEFAULT_CONFIG.trading_mode,
            "protocols": [
                "ADKv2-Workflow",
                "MCP-Streamable-HTTP",
                "A2A-0.2.6",
                "A2UI-v0.9",
                "AP2-v0.2",
                "OAuth-2.1",
                "SSE",
                "WebSocket-Bidi",
            ],
        }

    @api.get("/console", response_class=HTMLResponse)
    async def interactive_console() -> HTMLResponse:
        return HTMLResponse(content=CONSOLE_HTML)

    @api.get("/.well-known/oauth-protected-resource")
    async def oauth_protected_resource_metadata(request: Request) -> dict[str, Any]:
        base = str(request.base_url).rstrip("/")
        return get_oauth_protected_resource_metadata(base)

    @api.post("/api/v1/oauth/token")
    async def issue_demo_oauth_token(req: TokenMintRequest) -> dict[str, Any]:
        token = mint_oauth_jwt(subject=req.subject, scopes=req.scopes)
        return {
            "access_token": token,
            "token_type": "Bearer",
            "expires_in": 900,
            "scope": " ".join(req.scopes),
        }

    @api.get("/.well-known/agent.json")
    async def root_a2a_agent_card(request: Request) -> dict[str, Any]:
        return build_agent_card_dict(str(request.base_url))

    @api.get("/a2a/macro/.well-known/agent.json")
    async def macro_a2a_agent_card(request: Request) -> dict[str, Any]:
        return build_macro_specialist_card_dict(str(request.base_url))

    @api.post("/a2a/macro")
    async def macro_a2a_rpc_endpoint(request: Request) -> JSONResponse:
        body = await request.json()
        return JSONResponse(content=handle_macro_a2a_rpc(body))

    @api.post("/mcp")
    async def mcp_streamable_http_endpoint(
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> JSONResponse:
        payload = await request.json()
        method = payload.get("method", "")
        enforce = method == "tools/call"
        response = dispatch_mcp_jsonrpc(
            payload,
            authorization_header=authorization,
            enforce_oauth=enforce,
        )
        status_code = 401 if response.get("error", {}).get("code") == -32001 else 200
        return JSONResponse(content=response, status_code=status_code)

    @api.get("/api/v1/portfolio")
    async def rest_portfolio_snapshot() -> dict[str, Any]:
        return get_portfolio_state()

    @api.get("/api/v1/a2ui/{symbol}")
    async def rest_a2ui_surface(symbol: str) -> dict[str, Any]:
        return emit_a2ui_trading_surface(symbol=symbol, surface_kind="MARKET_PULSE")

    @api.get("/api/v1/stream/ticks/{symbol}")
    async def sse_market_tick_stream(
        symbol: str,
        max_events: int = Query(default=10, ge=1, le=120),
        interval_ms: int = Query(default=350, ge=50, le=5000),
    ) -> StreamingResponse:
        async def _event_generator() -> Any:
            clean_sym = symbol.strip().upper()
            for seq in range(1, max_events + 1):
                snap = stream_realtime_market_ticks(clean_sym, window_ticks=30 + seq)
                event_payload = {
                    "sequence": seq,
                    "symbol": clean_sym,
                    "last_price": snap["last_price"],
                    "bid": snap["bid"],
                    "ask": snap["ask"],
                    "vwap": snap["vwap"],
                    "a2ui_delta": {
                        "version": "v0.9",
                        "updateDataModel": {
                            "surfaceId": f"market-pulse-{clean_sym.lower()}",
                            "path": "/metrics/rawPrice",
                            "value": snap["last_price"],
                        },
                    },
                }
                yield f"data: {json.dumps(event_payload)}\n\n"
                if seq < max_events:
                    await asyncio.sleep(interval_ms / 1000.0)

        return StreamingResponse(_event_generator(), media_type="text/event-stream")

    @api.websocket("/api/v1/ws/live-desk")
    async def websocket_bidirectional_desk(ws: WebSocket) -> None:
        """Bidirectional WebSocket streaming endpoint compatible with LiveRequestQueue."""
        await ws.accept()
        try:
            while True:
                raw = await ws.receive_text()
                msg = json.loads(raw)
                action = str(msg.get("action", "subscribe")).lower()
                symbol = str(msg.get("symbol", "GOOGL")).upper()
                if action == "ping":
                    await ws.send_json({"type": "pong", "symbol": symbol})
                else:
                    snap = stream_realtime_market_ticks(symbol, window_ticks=45)
                    await ws.send_json(
                        {
                            "type": "live_tick_frame",
                            "symbol": symbol,
                            "last_price": snap["last_price"],
                            "vwap": snap["vwap"],
                            "spread_bps": snap["spread_bps"],
                        }
                    )
        except WebSocketDisconnect:
            return

    @api.post("/api/v1/ap2/stage-and-settle")
    async def rest_ap2_stage_and_settle(req: StageAndSettleRequest) -> JSONResponse:
        quote = stream_realtime_market_ticks(req.symbol, window_ticks=30)
        if quote.get("status") != "success":
            return JSONResponse(content=quote, status_code=400)

        px = float(req.limit_price) if req.limit_price is not None else float(quote["ask"])
        mandate_bundle = issue_ap2_cart_mandate(
            symbol=req.symbol,
            side=req.side,
            quantity=req.quantity,
            limit_price=px,
        )
        if mandate_bundle.get("status") != "success":
            return JSONResponse(content=mandate_bundle, status_code=422)

        cart = CartMandate.model_validate(mandate_bundle["cart_mandate"])
        checkout, payment = sign_user_ap2_mandates(
            cart, trader_principal=req.trader_principal
        )
        settlement = settle_ap2_paper_trade(
            cart_mandate=cart.model_dump(),
            checkout_mandate=checkout.model_dump(),
            payment_mandate=payment.model_dump(),
            order_type=req.order_type,
        )
        settlement["a2ui_approval_surface"] = build_ap2_approval_a2ui_messages(
            cart.model_dump(),
            {"verdict": "APPROVED", "post_trade_concentration_pct": 4.2, "post_trade_var_95_pct": 1.1},
        )
        return JSONResponse(content=settlement)

    return api


app = create_app()
