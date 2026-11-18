"""ADKv2 Graph `Workflow` orchestrating parallel market fan-out, JoinNode, risk loop, & AP2 settlement."""

from __future__ import annotations

import json
from typing import Any

from .config import DEFAULT_CONFIG
from .prompts import TRADE_PROPOSER_INSTRUCTION
from .protocols.a2ui_surfaces import build_ap2_approval_a2ui_messages
from .protocols.ap2_mandates import (
    CartMandate,
    issue_ap2_cart_mandate,
    settle_ap2_paper_trade,
    sign_user_ap2_mandates,
)
from .state_schema import TradingWorkflowState
from .tools.market_data import (
    fetch_macro_and_news_sentiment,
    fetch_orderbook_microstructure,
    stream_realtime_market_ticks,
)
from .tools.risk_engine import evaluate_pretrade_risk
from .tools.technical_indicators import compute_technical_signals


def build_institutional_trade_workflow(proposer_model: Any | None = None) -> Any:
    """Builds the ADKv2 `Workflow` graph with typed `TradingWorkflowState` validation.

    Graph Topology:
      START -> (tick_microstructure_node, technical_regime_node, macro_catalyst_node)
            -> JoinNode("market_intel_join")
            -> trade_proposer_agent (LlmAgent)
            -> risk_and_ap2_gate_node (conditional routing):
                 - "RETRY_PROPOSAL"       -> trade_proposer_agent (bounded cycle)
                 - "READY_FOR_SETTLEMENT" -> ap2_settlement_node
                 - "HALT_REJECTED"        -> risk_halt_summary_node
    """
    from google.adk.agents import LlmAgent
    from google.adk.agents.context import Context
    from google.adk.workflow import START, Edge, JoinNode, RetryConfig, Workflow, node

    @node(
        name="tick_microstructure_node",
        retry_config=RetryConfig(max_attempts=2),
        timeout=15.0,
    )
    def run_tick_and_l2_scan(ctx: Context) -> str:
        symbol = str(ctx.state.get("active_symbol", "GOOGL")).upper()
        ticks = stream_realtime_market_ticks(symbol, window_ticks=60, tool_context=ctx)
        book = fetch_orderbook_microstructure(symbol, depth_levels=5, tool_context=ctx)
        report = (
            f"{symbol} Last=${ticks['last_price']} VWAP=${ticks['vwap']} "
            f"Spread={ticks['spread_bps']}bps L2_Imbalance={book['orderbook_imbalance']:+.2f} "
            f"({book['pressure_signal']})"
        )
        ctx.state["market_ticks_report"] = report
        return report

    @node(
        name="technical_regime_node",
        retry_config=RetryConfig(max_attempts=2),
        timeout=15.0,
    )
    def run_technical_regime_scan(ctx: Context) -> str:
        symbol = str(ctx.state.get("active_symbol", "GOOGL")).upper()
        signals = compute_technical_signals(symbol, period_bars=90, tool_context=ctx)
        report = (
            f"{symbol} Regime={signals['market_regime']} RSI14={signals['rsi_14']} "
            f"MACD_Hist={signals['macd']['histogram']:+.4f} "
            f"Alpha={signals['alpha_signal_score']:+.2f} ({signals['directional_conviction']})"
        )
        ctx.state["technical_signals_report"] = report
        return report

    @node(
        name="macro_catalyst_node",
        retry_config=RetryConfig(max_attempts=2),
        timeout=15.0,
    )
    def run_macro_catalyst_scan(ctx: Context) -> str:
        symbol = str(ctx.state.get("active_symbol", "GOOGL")).upper()
        macro = fetch_macro_and_news_sentiment(symbol, tool_context=ctx)
        report = (
            f"{symbol} Sentiment={macro['sentiment_regime']} "
            f"({macro['composite_sentiment_score']:+.2f}) "
            f"IV_Rank={macro['implied_vol_percentile']}%"
        )
        ctx.state["macro_sentiment_report"] = report
        return report

    market_join = JoinNode(name="market_intel_join")

    trade_proposer_agent = LlmAgent(
        name="trade_strategy_proposer",
        model=proposer_model or DEFAULT_CONFIG.analyst_model,
        description="Drafts regime-aware trade rationale from parallel market intelligence.",
        instruction=TRADE_PROPOSER_INSTRUCTION,
        output_key="trade_proposal_draft",
    )

    @node(name="risk_and_ap2_gate_node")
    def evaluate_and_route_risk(ctx: Context) -> str:
        symbol = str(ctx.state.get("active_symbol", "GOOGL")).upper()
        side = str(ctx.state.get("requested_side", "BUY")).upper()
        target_usd = float(ctx.state.get("target_notional_usd", 15_000.0))
        iteration = int(ctx.state.get("risk_loop_iteration", 0)) + 1
        ctx.state["risk_loop_iteration"] = iteration

        quotes = ctx.state.get("latest_quotes", {})
        last_px = float(quotes.get(symbol, {}).get("ask", 192.50))

        # On retry iterations, adopt the risk engine's recommended_max_quantity automatically
        prior_verdict = ctx.state.get("latest_risk_verdict", {})
        if iteration > 1 and prior_verdict.get("recommended_max_quantity", 0) > 0:
            proposed_qty = int(prior_verdict["recommended_max_quantity"])
        else:
            proposed_qty = max(1, int(target_usd // max(last_px, 0.01)))

        verdict = evaluate_pretrade_risk(
            symbol=symbol,
            side=side,
            quantity=proposed_qty,
            limit_price=last_px,
            tool_context=ctx,
        )
        ctx.state["proposed_order"] = {
            "symbol": symbol,
            "side": side,
            "quantity": proposed_qty,
            "limit_price": last_px,
        }

        if verdict["verdict"] == "APPROVED":
            mandate_res = issue_ap2_cart_mandate(
                symbol=symbol,
                side=side,
                quantity=proposed_qty,
                limit_price=last_px,
                tool_context=ctx,
            )
            cart_dict = mandate_res["cart_mandate"]
            a2ui_msgs = build_ap2_approval_a2ui_messages(cart_dict, verdict)
            ctx.state["last_a2ui_surface"] = {
                "protocol": "A2UI/v0.9",
                "a2ui_messages": a2ui_msgs,
            }
            ctx.route = "READY_FOR_SETTLEMENT"
            return json.dumps({"route": "READY_FOR_SETTLEMENT", "cart_id": cart_dict["cart_id"]})

        if (
            iteration < DEFAULT_CONFIG.max_risk_loop_iterations
            and verdict.get("recommended_max_quantity", 0) > 0
        ):
            ctx.route = "RETRY_PROPOSAL"
            return json.dumps(
                {
                    "route": "RETRY_PROPOSAL",
                    "breaches": verdict["breaches"],
                    "recommended_max_quantity": verdict["recommended_max_quantity"],
                }
            )

        ctx.route = "HALT_REJECTED"
        return json.dumps({"route": "HALT_REJECTED", "breaches": verdict["breaches"]})

    def _needs_human_confirmation(ctx: Context) -> bool:
        """Requests interactive HITL confirmation only for large orders (> $35k)."""
        cart = ctx.state.get("ap2_cart_mandate", {}) or {}
        return float(cart.get("total_notional_usd", 0.0)) > 35_000.0

    @node(
        name="ap2_settlement_node",
        require_confirmation=_needs_human_confirmation,
        rerun_on_resume=True,
    )
    def settle_approved_ap2_order(ctx: Context) -> str:
        cart_dict = ctx.state.get("ap2_cart_mandate")
        if not isinstance(cart_dict, dict) or "cart_id" not in cart_dict:
            return "No approved AP2 CartMandate present for settlement."

        cart = CartMandate.model_validate(cart_dict)
        trader_id = f"spiffe://alphapulse.local/user/{ctx.user_id or 'desk-trader'}"
        checkout, payment = sign_user_ap2_mandates(cart, trader_principal=trader_id)
        order_style = str(ctx.state.get("user:exec_style", "TWAP"))

        receipt = settle_ap2_paper_trade(
            cart_mandate=cart.model_dump(),
            checkout_mandate=checkout.model_dump(),
            payment_mandate=payment.model_dump(),
            order_type=order_style,
            idempotency_key=payment.payment_mandate_id,
            tool_context=ctx,
        )
        fill = receipt.get("fill", {})
        summary = (
            f"AP2 Double-Lock Verified & Settled: {fill.get('order_id')} | "
            f"{fill.get('side')} {fill.get('quantity')} {fill.get('symbol')} @ "
            f"${fill.get('fill_price')} ({fill.get('order_type')}) | "
            f"Mandate={payment.payment_mandate_id}"
        )
        ctx.state["execution_receipt_summary"] = summary
        return summary

    @node(name="risk_halt_summary_node")
    def summarize_risk_halt(ctx: Context) -> str:
        verdict = ctx.state.get("latest_risk_verdict", {})
        summary = (
            f"Trade halted by AlphaPulse Pre-Trade Risk Gate for {verdict.get('symbol')}: "
            f"{'; '.join(verdict.get('breaches', ['Policy breach']))}"
        )
        ctx.state["execution_receipt_summary"] = summary
        return summary

    return Workflow(
        name="institutional_trade_workflow",
        description=(
            "ADKv2 Graph Workflow executing parallel market/L2/technical/macro scans, "
            "JoinNode synchronization, iterative VaR risk sizing, AP2 CartMandate locking, "
            "and idempotent paper settlement."
        ),
        state_schema=TradingWorkflowState,
        edges=[
            (
                START,
                (
                    run_tick_and_l2_scan,
                    run_technical_regime_scan,
                    run_macro_catalyst_scan,
                ),
                market_join,
                trade_proposer_agent,
                evaluate_and_route_risk,
            ),
            Edge(
                from_node=evaluate_and_route_risk,
                to_node=trade_proposer_agent,
                route="RETRY_PROPOSAL",
            ),
            Edge(
                from_node=evaluate_and_route_risk,
                to_node=settle_approved_ap2_order,
                route="READY_FOR_SETTLEMENT",
            ),
            Edge(
                from_node=evaluate_and_route_risk,
                to_node=summarize_risk_halt,
                route="HALT_REJECTED",
            ),
        ],
    )
