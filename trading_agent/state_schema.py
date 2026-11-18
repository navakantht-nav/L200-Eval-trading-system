"""Typed ADKv2 `state_schema` contract for session-scoped workflow state.

ADKv2 validates every unprefixed `ctx.state[key] = value` write against this
Pydantic model, raising `StateSchemaError` on undeclared keys or mistyped values.
Prefixed keys (`user:*`, `app:*`, `temp:*`) automatically bypass this schema and
are managed across their respective ADKv2 lifetimes.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class TradingWorkflowState(BaseModel):
    """Session-scoped state contract enforced across ADKv2 Workflow nodes."""

    active_symbol: str = "GOOGL"
    target_notional_usd: float = 15_000.0
    requested_side: str = "BUY"

    latest_quotes: dict[str, dict[str, Any]] = Field(default_factory=dict)
    orderbook_microstructure: dict[str, Any] = Field(default_factory=dict)
    macro_news_sentiment: dict[str, Any] = Field(default_factory=dict)
    technical_signals: dict[str, Any] = Field(default_factory=dict)

    market_ticks_report: str = ""
    technical_signals_report: str = ""
    macro_sentiment_report: str = ""

    trade_proposal_draft: Any = ""
    proposed_order: dict[str, Any] = Field(default_factory=dict)
    latest_risk_verdict: dict[str, Any] = Field(default_factory=dict)
    risk_review_summary: str = ""
    risk_loop_iteration: int = 0
    risk_approved_trade: dict[str, Any] = Field(default_factory=dict)

    ap2_intent_mandate: dict[str, Any] = Field(default_factory=dict)
    ap2_cart_mandate: dict[str, Any] = Field(default_factory=dict)
    ap2_checkout_mandate: dict[str, Any] = Field(default_factory=dict)
    ap2_payment_mandate: dict[str, Any] = Field(default_factory=dict)

    portfolio_snapshot: dict[str, Any] = Field(default_factory=dict)
    portfolio_metrics_view: dict[str, Any] = Field(default_factory=dict)
    last_execution_fill: dict[str, Any] = Field(default_factory=dict)
    execution_receipt_summary: str = ""

    mandate_notes: str = ""
    last_a2ui_surface: dict[str, Any] = Field(default_factory=dict)
    last_guardrail_block: str = ""
