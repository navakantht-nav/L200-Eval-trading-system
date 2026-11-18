"""Idempotent paper-trading order execution engine and stateful portfolio ledger."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from ..config import DEFAULT_CONFIG
from .market_data import stream_realtime_market_ticks
from .risk_engine import _compute_portfolio_metrics, _ensure_portfolio, evaluate_pretrade_risk

if TYPE_CHECKING:
    from google.adk.tools import ToolContext


def get_portfolio_state(tool_context: ToolContext | None = None) -> dict[str, Any]:
    """Returns current paper-trading portfolio positions, cash, NAV, PnL, and 95% daily VaR.

    Args:
        tool_context: Optional ADK ToolContext holding session portfolio state.

    Returns:
        Portfolio summary dictionary including mark-to-market positions and risk metrics.
    """
    portfolio = _ensure_portfolio(tool_context)
    metrics = _compute_portfolio_metrics(portfolio)
    metrics["status"] = "success"
    metrics["trading_mode"] = DEFAULT_CONFIG.trading_mode
    metrics["recent_fills"] = list(portfolio.get("execution_ledger", []))[-5:]
    if tool_context is not None:
        tool_context.state["portfolio_metrics_view"] = metrics
    return metrics


def execute_paper_trade(
    symbol: str,
    side: str,
    quantity: int,
    order_type: str = "LIMIT",
    limit_price: float | None = None,
    strategy_tag: str = "quant_adkv2_workflow",
    idempotency_key: str | None = None,
    ap2_payment_mandate_id: str | None = None,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Executes an idempotent PAPER trade after enforcing deterministic pre-trade risk checks.

    Args:
        symbol: Ticker symbol to trade (for example, 'GOOGL', 'NVDA').
        side: Trade direction ('BUY' or 'SELL').
        quantity: Number of shares/units to trade (> 0).
        order_type: Order execution type ('LIMIT', 'MARKET', or 'TWAP').
        limit_price: Optional limit price in USD; defaults to current market ask/bid.
        strategy_tag: Attribution label recorded in the execution audit ledger.
        idempotency_key: Optional client order key preventing duplicate fills on resume.
        ap2_payment_mandate_id: Optional verified AP2 PaymentMandate identifier.
        tool_context: Optional ADK ToolContext used to mutate session portfolio state.

    Returns:
        Execution fill receipt or structured rejection details if risk checks fail.
    """
    if DEFAULT_CONFIG.trading_mode != "PAPER":
        return {
            "status": "blocked",
            "error": (
                f"Execution halted: trading_mode='{DEFAULT_CONFIG.trading_mode}'. "
                "Only PAPER execution is permitted by desk governance."
            ),
        }

    clean_symbol = symbol.strip().upper()
    clean_side = side.strip().upper()
    clean_order_type = order_type.strip().upper()
    if clean_order_type not in {"LIMIT", "MARKET", "TWAP"}:
        return {
            "status": "error",
            "error": f"Unsupported order_type '{order_type}'. Use LIMIT, MARKET, or TWAP.",
        }

    portfolio = _ensure_portfolio(tool_context)
    ledger: list[dict[str, Any]] = list(portfolio.get("execution_ledger", []))

    if idempotency_key:
        for prior_fill in ledger:
            if prior_fill.get("idempotency_key") == idempotency_key:
                return {
                    "status": "success",
                    "idempotent_replay": True,
                    "fill": prior_fill,
                }

    quote = stream_realtime_market_ticks(
        clean_symbol, window_ticks=30, tool_context=tool_context
    )
    if quote.get("status") != "success":
        return quote

    reference_px = float(quote["ask"] if clean_side == "BUY" else quote["bid"])
    effective_limit = float(limit_price) if limit_price is not None else reference_px

    risk_check = evaluate_pretrade_risk(
        symbol=clean_symbol,
        side=clean_side,
        quantity=quantity,
        limit_price=effective_limit,
        tool_context=tool_context,
    )
    if risk_check.get("verdict") != "APPROVED":
        return {
            "status": "rejected_by_risk_engine",
            "risk_assessment": risk_check,
        }

    slippage_bps = (
        1.2 if clean_order_type == "TWAP" else 2.5 if clean_order_type == "MARKET" else 0.5
    )
    slip_factor = (slippage_bps / 10_000.0) * (1.0 if clean_side == "BUY" else -1.0)
    fill_price = round(reference_px * (1.0 + slip_factor), 2)
    if clean_order_type == "LIMIT":
        if clean_side == "BUY" and fill_price > effective_limit:
            fill_price = round(effective_limit, 2)
        elif clean_side == "SELL" and fill_price < effective_limit:
            fill_price = round(effective_limit, 2)

    notional_usd = round(fill_price * quantity, 2)
    positions: dict[str, dict[str, Any]] = {
        k: dict(v) for k, v in portfolio.get("positions", {}).items()
    }
    pos = positions.get(clean_symbol, {"quantity": 0, "avg_cost": 0.0})
    old_qty = int(pos.get("quantity", 0))
    old_avg = float(pos.get("avg_cost", 0.0))
    signed_qty = quantity if clean_side == "BUY" else -quantity
    new_qty = old_qty + signed_qty
    realized_delta = 0.0

    if old_qty == 0 or (old_qty > 0 and signed_qty > 0) or (old_qty < 0 and signed_qty < 0):
        total_cost = abs(old_qty) * old_avg + abs(signed_qty) * fill_price
        new_avg = total_cost / max(1, abs(new_qty))
    else:
        closed_units = min(abs(old_qty), abs(signed_qty))
        pnl_per_share = (fill_price - old_avg) if old_qty > 0 else (old_avg - fill_price)
        realized_delta = round(closed_units * pnl_per_share, 2)
        if new_qty == 0:
            new_avg = 0.0
        elif (old_qty > 0 > new_qty) or (old_qty < 0 < new_qty):
            new_avg = fill_price
        else:
            new_avg = old_avg

    if new_qty == 0:
        positions.pop(clean_symbol, None)
    else:
        positions[clean_symbol] = {
            "quantity": new_qty,
            "avg_cost": round(new_avg, 4),
        }

    new_cash = round(float(portfolio["cash_usd"]) - signed_qty * fill_price, 2)
    new_realized = round(float(portfolio.get("realized_pnl_usd", 0.0)) + realized_delta, 2)

    order_hash = hashlib.sha1(
        f"{clean_symbol}:{clean_side}:{quantity}:{fill_price}:{len(ledger)}".encode()
    ).hexdigest()[:10]
    fill_record = {
        "order_id": f"PAPER-{order_hash.upper()}",
        "idempotency_key": idempotency_key or f"auto-{order_hash}",
        "ap2_payment_mandate_id": ap2_payment_mandate_id or "direct-paper-auth",
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "symbol": clean_symbol,
        "side": clean_side,
        "order_type": clean_order_type,
        "quantity": quantity,
        "fill_price": fill_price,
        "notional_usd": notional_usd,
        "slippage_bps": slippage_bps,
        "realized_pnl_delta_usd": realized_delta,
        "strategy_tag": strategy_tag,
    }
    ledger.append(fill_record)

    updated_portfolio = {
        "cash_usd": new_cash,
        "realized_pnl_usd": new_realized,
        "positions": positions,
        "execution_ledger": ledger,
    }
    if tool_context is not None:
        tool_context.state["portfolio_snapshot"] = updated_portfolio
        tool_context.state["last_execution_fill"] = fill_record
    else:
        portfolio.clear()
        portfolio.update(updated_portfolio)

    post_summary = _compute_portfolio_metrics(
        updated_portfolio, override_prices={clean_symbol: fill_price}
    )
    return {
        "status": "success",
        "idempotent_replay": False,
        "fill": fill_record,
        "updated_portfolio_summary": {
            "cash_usd": post_summary["cash_usd"],
            "nav_usd": post_summary["nav_usd"],
            "realized_pnl_usd": post_summary["realized_pnl_usd"],
            "unrealized_pnl_usd": post_summary["unrealized_pnl_usd"],
            "var_95_pct_of_nav": post_summary["var_95_pct_of_nav"],
        },
    }
