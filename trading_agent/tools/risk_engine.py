"""Pre-trade risk compliance, parametric VaR, position sizing, and workflow sign-off."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

from ..config import DEFAULT_CONFIG
from .market_data import generate_price_series

if TYPE_CHECKING:
    from google.adk.tools import ToolContext


# Process-level default portfolio store used by standalone MCP calls when no ADK session is passed
_PROCESS_PORTFOLIO_STORE: dict[str, Any] = {
    "cash_usd": DEFAULT_CONFIG.default_starting_cash_usd,
    "realized_pnl_usd": 0.0,
    "positions": {
        "SPY": {"quantity": 100, "avg_cost": 590.00},
    },
    "execution_ledger": [],
}


def reset_process_portfolio() -> None:
    """Resets the standalone MCP process portfolio for deterministic tests."""
    _PROCESS_PORTFOLIO_STORE.clear()
    _PROCESS_PORTFOLIO_STORE.update(
        {
            "cash_usd": DEFAULT_CONFIG.default_starting_cash_usd,
            "realized_pnl_usd": 0.0,
            "positions": {"SPY": {"quantity": 100, "avg_cost": 590.00}},
            "execution_ledger": [],
        }
    )


def _ensure_portfolio(tool_context: ToolContext | None) -> dict[str, Any]:
    if tool_context is None:
        return _PROCESS_PORTFOLIO_STORE
    existing = tool_context.state.get("portfolio_snapshot")
    if not isinstance(existing, dict) or "cash_usd" not in existing:
        fresh = {
            "cash_usd": DEFAULT_CONFIG.default_starting_cash_usd,
            "realized_pnl_usd": 0.0,
            "positions": {"SPY": {"quantity": 100, "avg_cost": 590.00}},
            "execution_ledger": [],
        }
        tool_context.state["portfolio_snapshot"] = fresh
        return fresh
    return existing


def _compute_portfolio_metrics(
    portfolio: dict[str, Any],
    override_prices: dict[str, float] | None = None,
) -> dict[str, Any]:
    override_prices = override_prices or {}
    positions: dict[str, dict[str, Any]] = portfolio.get("positions", {})
    cash_usd = float(portfolio.get("cash_usd", DEFAULT_CONFIG.default_starting_cash_usd))

    gross_exposure = 0.0
    unrealized_pnl = 0.0
    daily_variance_usd2 = 0.0
    enriched_positions: dict[str, dict[str, Any]] = {}

    for sym, pos in positions.items():
        qty = int(pos.get("quantity", 0))
        if qty == 0:
            continue
        avg_cost = float(pos.get("avg_cost", 0.0))
        if sym in override_prices:
            mark = override_prices[sym]
            _, _, meta = generate_price_series(sym, bars=15)
        else:
            prices, _, meta = generate_price_series(sym, bars=15)
            mark = prices[-1]

        market_value = qty * mark
        gross_exposure += abs(market_value)
        unrealized_pnl += qty * (mark - avg_cost)
        daily_vol = float(meta["annual_vol"]) / math.sqrt(252.0)
        daily_variance_usd2 += (abs(market_value) * daily_vol) ** 2
        enriched_positions[sym] = {
            "quantity": qty,
            "avg_cost": round(avg_cost, 2),
            "mark_price": round(mark, 2),
            "market_value_usd": round(market_value, 2),
            "unrealized_pnl_usd": round(qty * (mark - avg_cost), 2),
            "annual_vol": float(meta["annual_vol"]),
        }

    nav_usd = max(
        1.0, cash_usd + sum(p["market_value_usd"] for p in enriched_positions.values())
    )
    portfolio_daily_std_usd = math.sqrt(daily_variance_usd2 * 1.35)
    var_95_usd = 1.64485 * portfolio_daily_std_usd
    var_99_usd = 2.32635 * portfolio_daily_std_usd
    var_95_pct = (var_95_usd / nav_usd) * 100.0

    return {
        "cash_usd": round(cash_usd, 2),
        "nav_usd": round(nav_usd, 2),
        "gross_exposure_usd": round(gross_exposure, 2),
        "unrealized_pnl_usd": round(unrealized_pnl, 2),
        "realized_pnl_usd": round(float(portfolio.get("realized_pnl_usd", 0.0)), 2),
        "var_95_daily_usd": round(var_95_usd, 2),
        "var_99_daily_usd": round(var_99_usd, 2),
        "var_95_pct_of_nav": round(var_95_pct, 3),
        "positions": enriched_positions,
    }


def evaluate_pretrade_risk(
    symbol: str,
    side: str,
    quantity: int,
    limit_price: float,
    stop_loss_price: float | None = None,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Evaluates pre-trade risk limits: notional cap, concentration, 95% VaR, and stop-loss.

    Args:
        symbol: Ticker symbol for the proposed trade (for example, 'GOOGL').
        side: Order direction, either 'BUY' or 'SELL'.
        quantity: Positive integer share/unit quantity proposed.
        limit_price: Proposed execution or limit price in USD.
        stop_loss_price: Optional protective stop-loss price in USD.
        tool_context: Optional ADK ToolContext containing current session portfolio state.

    Returns:
        Structured pre-trade risk assessment with 'verdict' ('APPROVED' or 'REJECTED'),
        list of policy breaches, post-trade 95% VaR, and 'recommended_max_quantity'.
    """
    clean_symbol = symbol.strip().upper()
    clean_side = side.strip().upper()

    if clean_side not in {"BUY", "SELL"}:
        return {"status": "error", "error": f"Invalid side '{side}'. Must be 'BUY' or 'SELL'."}
    if quantity <= 0 or limit_price <= 0:
        return {"status": "error", "error": "Quantity and limit_price must both be positive."}

    breaches: list[str] = []
    if tool_context is not None and tool_context.state.get("app:desk_halt"):
        breaches.append("Desk-wide kill switch (app:desk_halt) is currently active.")

    if clean_symbol in DEFAULT_CONFIG.restricted_tickers:
        breaches.append(
            f"Ticker '{clean_symbol}' is on the restricted compliance list "
            f"{sorted(DEFAULT_CONFIG.restricted_tickers)}."
        )

    portfolio = _ensure_portfolio(tool_context)
    pre_metrics = _compute_portfolio_metrics(
        portfolio, override_prices={clean_symbol: limit_price}
    )
    nav_usd = float(pre_metrics["nav_usd"])
    order_notional = round(quantity * limit_price, 2)

    if order_notional > DEFAULT_CONFIG.max_single_order_usd:
        breaches.append(
            f"Order notional ${order_notional:,.2f} exceeds single-order limit "
            f"${DEFAULT_CONFIG.max_single_order_usd:,.2f}."
        )

    if clean_side == "BUY" and order_notional > float(pre_metrics["cash_usd"]):
        breaches.append(
            f"Insufficient buying power: order costs ${order_notional:,.2f} vs "
            f"available cash ${pre_metrics['cash_usd']:,.2f}."
        )

    sim_positions = {k: dict(v) for k, v in portfolio.get("positions", {}).items()}
    current_qty = int(sim_positions.get(clean_symbol, {}).get("quantity", 0))
    signed_delta = quantity if clean_side == "BUY" else -quantity
    new_qty = current_qty + signed_delta
    sim_positions[clean_symbol] = {
        "quantity": new_qty,
        "avg_cost": (
            limit_price
            if current_qty == 0
            else sim_positions[clean_symbol].get("avg_cost", limit_price)
        ),
    }
    sim_portfolio = {
        "cash_usd": float(portfolio.get("cash_usd", DEFAULT_CONFIG.default_starting_cash_usd))
        - signed_delta * limit_price,
        "realized_pnl_usd": portfolio.get("realized_pnl_usd", 0.0),
        "positions": sim_positions,
    }
    post_metrics = _compute_portfolio_metrics(
        sim_portfolio, override_prices={clean_symbol: limit_price}
    )

    post_symbol_val = abs(new_qty * limit_price)
    concentration_pct = round(
        (post_symbol_val / max(1.0, post_metrics["nav_usd"])) * 100.0, 2
    )
    if concentration_pct > DEFAULT_CONFIG.max_position_weight_pct:
        breaches.append(
            f"Post-trade concentration {concentration_pct:.2f}% exceeds maximum position "
            f"weight {DEFAULT_CONFIG.max_position_weight_pct:.2f}%."
        )

    var_ceiling = DEFAULT_CONFIG.max_portfolio_var_95_pct
    if tool_context is not None and tool_context.state.get("app:var_95_ceiling_pct"):
        var_ceiling = float(tool_context.state["app:var_95_ceiling_pct"])

    if post_metrics["var_95_pct_of_nav"] > var_ceiling:
        breaches.append(
            f"Post-trade 1-day 95% VaR ({post_metrics['var_95_pct_of_nav']:.2f}% of NAV) "
            f"exceeds desk ceiling {var_ceiling:.2f}%."
        )

    if stop_loss_price is not None:
        if clean_side == "BUY" and stop_loss_price >= limit_price:
            breaches.append(
                f"Invalid BUY stop_loss_price ({stop_loss_price}) >= limit_price ({limit_price})."
            )
        elif clean_side == "SELL" and stop_loss_price <= limit_price:
            breaches.append(
                f"Invalid SELL stop_loss_price ({stop_loss_price}) <= limit_price ({limit_price})."
            )

    max_by_order_cap = int(DEFAULT_CONFIG.max_single_order_usd // limit_price)
    max_by_conc_cap = max(
        0,
        int(
            (
                (DEFAULT_CONFIG.max_position_weight_pct / 100.0) * nav_usd
                - abs(current_qty * limit_price)
            )
            // limit_price
        ),
    )
    max_by_cash = (
        int(float(pre_metrics["cash_usd"]) // limit_price)
        if clean_side == "BUY"
        else max_by_order_cap
    )
    recommended_max_qty = max(
        0,
        min(
            max_by_order_cap,
            max_by_conc_cap if max_by_conc_cap > 0 else max_by_order_cap,
            max_by_cash,
        ),
    )
    if clean_symbol in DEFAULT_CONFIG.restricted_tickers:
        recommended_max_qty = 0

    verdict = "APPROVED" if not breaches else "REJECTED"
    assessment = {
        "status": "success",
        "verdict": verdict,
        "symbol": clean_symbol,
        "side": clean_side,
        "proposed_quantity": quantity,
        "limit_price": limit_price,
        "order_notional_usd": order_notional,
        "post_trade_concentration_pct": concentration_pct,
        "pre_trade_var_95_pct": pre_metrics["var_95_pct_of_nav"],
        "post_trade_var_95_pct": post_metrics["var_95_pct_of_nav"],
        "breaches": breaches,
        "recommended_max_quantity": recommended_max_qty,
    }

    if tool_context is not None:
        tool_context.state["latest_risk_verdict"] = assessment

    return assessment


def approve_and_escalate_risk_loop(
    symbol: str,
    side: str,
    approved_quantity: int,
    limit_price: float,
    approval_rationale: str,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Records formal risk sign-off and sets `escalate = True` if inside a refinement loop.

    Args:
        symbol: Approved ticker symbol.
        side: Approved side ('BUY' or 'SELL').
        approved_quantity: Approved share quantity (> 0) or 0 if halting trade cleanly.
        limit_price: Approved execution limit price in USD.
        approval_rationale: Concise risk officer sign-off summary.
        tool_context: Optional ADK ToolContext used to store approval and escalate.

    Returns:
        Confirmation payload with risk sign-off metadata.
    """
    approval_record = {
        "status": "approved",
        "symbol": symbol.strip().upper(),
        "side": side.strip().upper(),
        "approved_quantity": int(approved_quantity),
        "limit_price": float(limit_price),
        "approval_rationale": approval_rationale.strip(),
    }
    if tool_context is not None:
        tool_context.state["risk_approved_trade"] = approval_record
        if getattr(tool_context, "actions", None) is not None:
            tool_context.actions.escalate = True
    return approval_record
