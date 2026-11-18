"""Unit tests for market ticks, L2 microstructure, technical indicators, VaR, & paper ledger."""

from __future__ import annotations

import unittest

from trading_agent.tools import (
    approve_and_escalate_risk_loop,
    compute_technical_signals,
    evaluate_pretrade_risk,
    execute_paper_trade,
    fetch_macro_and_news_sentiment,
    fetch_orderbook_microstructure,
    get_portfolio_state,
    reset_process_portfolio,
    stream_realtime_market_ticks,
)


class _DummyActions:
    def __init__(self) -> None:
        self.escalate = False


class _DummyContext:
    def __init__(self) -> None:
        self.state: dict[str, object] = {}
        self.actions = _DummyActions()


class TestQuantToolsAndRisk(unittest.TestCase):
    def setUp(self) -> None:
        reset_process_portfolio()

    def test_market_ticks_and_orderbook_state_persistence(self) -> None:
        ctx = _DummyContext()
        ticks = stream_realtime_market_ticks("GOOGL", window_ticks=45, tool_context=ctx)  # type: ignore[arg-type]
        self.assertEqual(ticks["status"], "success")
        self.assertEqual(ticks["symbol"], "GOOGL")
        self.assertGreater(ticks["last_price"], 0)
        self.assertLessEqual(ticks["bid"], ticks["ask"])
        self.assertEqual(ctx.state["active_symbol"], "GOOGL")

        book = fetch_orderbook_microstructure("GOOGL", depth_levels=5, tool_context=ctx)  # type: ignore[arg-type]
        self.assertEqual(book["status"], "success")
        self.assertEqual(len(book["bids"]), 5)
        self.assertEqual(len(book["asks"]), 5)
        self.assertIn("temp:l2_raw_ladder", ctx.state)

        macro = fetch_macro_and_news_sentiment("NVDA", tool_context=ctx)  # type: ignore[arg-type]
        self.assertEqual(macro["status"], "success")
        self.assertIn(macro["sentiment_regime"], {"BULLISH", "BEARISH", "NEUTRAL"})

    def test_technical_indicators_regime_and_stops(self) -> None:
        ctx = _DummyContext()
        sig = compute_technical_signals("NVDA", period_bars=90, tool_context=ctx)  # type: ignore[arg-type]
        self.assertEqual(sig["status"], "success")
        self.assertGreaterEqual(sig["rsi_14"], 0.0)
        self.assertLessEqual(sig["rsi_14"], 100.0)
        self.assertIn(
            sig["market_regime"],
            {
                "HIGH_VOLATILITY_BREAKOUT",
                "TRENDING_BULLISH",
                "TRENDING_BEARISH",
                "MEAN_REVERTING_RANGE",
            },
        )
        self.assertLess(sig["suggested_long_stop_loss"], sig["last_price"])
        self.assertGreater(sig["suggested_short_stop_loss"], sig["last_price"])

    def test_pretrade_risk_rejects_oversized_and_restricted_orders(self) -> None:
        ctx = _DummyContext()
        oversized = evaluate_pretrade_risk(
            symbol="GOOGL",
            side="BUY",
            quantity=600,
            limit_price=195.0,
            tool_context=ctx,  # type: ignore[arg-type]
        )
        self.assertEqual(oversized["verdict"], "REJECTED")
        self.assertGreater(len(oversized["breaches"]), 0)
        self.assertGreater(oversized["recommended_max_quantity"], 0)
        self.assertLess(oversized["recommended_max_quantity"], 600)

        restricted = evaluate_pretrade_risk(
            symbol="MEME",
            side="BUY",
            quantity=10,
            limit_price=10.0,
            tool_context=ctx,  # type: ignore[arg-type]
        )
        self.assertEqual(restricted["verdict"], "REJECTED")
        self.assertEqual(restricted["recommended_max_quantity"], 0)

    def test_app_kill_switch_blocks_pretrade_approval(self) -> None:
        ctx = _DummyContext()
        ctx.state["app:desk_halt"] = True
        res = evaluate_pretrade_risk(
            symbol="AAPL",
            side="BUY",
            quantity=10,
            limit_price=220.0,
            tool_context=ctx,  # type: ignore[arg-type]
        )
        self.assertEqual(res["verdict"], "REJECTED")
        self.assertTrue(any("app:desk_halt" in b for b in res["breaches"]))

    def test_idempotent_paper_execution_and_loop_escalation(self) -> None:
        ctx = _DummyContext()
        first = execute_paper_trade(
            symbol="MSFT",
            side="BUY",
            quantity=20,
            order_type="TWAP",
            idempotency_key="idem-msft-001",
            tool_context=ctx,  # type: ignore[arg-type]
        )
        self.assertEqual(first["status"], "success")
        self.assertFalse(first["idempotent_replay"])

        replay = execute_paper_trade(
            symbol="MSFT",
            side="BUY",
            quantity=20,
            order_type="TWAP",
            idempotency_key="idem-msft-001",
            tool_context=ctx,  # type: ignore[arg-type]
        )
        self.assertEqual(replay["status"], "success")
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(first["fill"]["order_id"], replay["fill"]["order_id"])

        portfolio = get_portfolio_state(tool_context=ctx)  # type: ignore[arg-type]
        self.assertEqual(portfolio["positions"]["MSFT"]["quantity"], 20)

        esc = approve_and_escalate_risk_loop(
            symbol="MSFT",
            side="BUY",
            approved_quantity=20,
            limit_price=425.0,
            approval_rationale="Compliant with 95% VaR.",
            tool_context=ctx,  # type: ignore[arg-type]
        )
        self.assertEqual(esc["status"], "approved")
        self.assertTrue(ctx.actions.escalate)


if __name__ == "__main__":
    unittest.main()
