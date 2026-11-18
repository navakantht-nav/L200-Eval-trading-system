"""End-to-end tests for PII/SPII & secret scrubbing across Logs, OTel Spans, Sessions & Memory."""

from __future__ import annotations

import io
import json
import logging
import unittest

from trading_agent.harness import TradingHarnessPlugin, get_in_memory_span_exporter
from trading_agent.memory import (
    build_memory_service,
    build_session_service,
    recall_trading_memory,
    toggle_desk_kill_switch,
    update_trader_mandate,
)
from trading_agent.pii_scrubber import (
    PiiRedactingLogFilter,
    pseudonymize_identifier,
    scrub_pii_payload,
    scrub_pii_text,
)


class _DummyCtx:
    def __init__(self) -> None:
        self.state: dict[str, object] = {}


class _DummyTool:
    def __init__(self, name: str = "update_trader_mandate") -> None:
        self.name = name


class TestPiiScrubbingAcrossLogsTracesAndMemory(unittest.IsolatedAsyncioTestCase):
    def test_text_and_structured_payload_redaction_preserves_quant_numbers(self) -> None:
        raw = (
            "Trader jane.doe@hedgefund.io (SSN 123-45-6789, phone (415) 555-0199, "
            "card 4532-0151-1283-0366, acct ACCT-99887766, IP 203.0.113.42, "
            "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxIn0.sig12345678) "
            "wants BUY 250 GOOGL @ $194.35 (VaR 0.348%)."
        )
        scrubbed, count = scrub_pii_text(raw)
        self.assertGreaterEqual(count, 7)
        self.assertNotIn("jane.doe@hedgefund.io", scrubbed)
        self.assertNotIn("123-45-6789", scrubbed)
        self.assertNotIn("555-0199", scrubbed)
        self.assertNotIn("4532-0151-1283-0366", scrubbed)
        self.assertNotIn("ACCT-99887766", scrubbed)
        self.assertNotIn("203.0.113.42", scrubbed)
        self.assertNotIn("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9", scrubbed)
        # Ensure normal quant prices, tickers, and percentages remain untouched
        self.assertIn("BUY 250 GOOGL @ $194.35 (VaR 0.348%)", scrubbed)

        nested = {
            "user_id": "jane.doe@hedgefund.io",
            "api_key": "sk-secret-live-token-1234567890",
            "ssn": "123-45-6789",
            "symbol": "NVDA",
            "limit_price": 138.25,
        }
        clean_dict, dict_redactions = scrub_pii_payload(nested, pseudonymize_ids=True)
        self.assertEqual(dict_redactions, 2)
        self.assertTrue(str(clean_dict["user_id"]).startswith("uid_"))
        self.assertNotIn("jane.doe", str(clean_dict["user_id"]))
        self.assertEqual(clean_dict["api_key"], "[REDACTED_API_KEY]")
        self.assertEqual(clean_dict["ssn"], "[REDACTED_SSN]")
        self.assertEqual(clean_dict["symbol"], "NVDA")
        self.assertEqual(clean_dict["limit_price"], 138.25)

    def test_pseudonymize_identifier_is_deterministic_and_irreversible(self) -> None:
        p1 = pseudonymize_identifier("trader_alice@firm.com")
        p2 = pseudonymize_identifier("trader_alice@firm.com")
        self.assertEqual(p1, p2)
        self.assertTrue(p1.startswith("uid_"))
        self.assertNotIn("alice", p1)

    async def test_harness_scrubs_structured_logs_and_otel_spans(self) -> None:
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.addFilter(PiiRedactingLogFilter())
        harness_logger = logging.getLogger("alphapulse.harness")
        harness_logger.setLevel(logging.INFO)
        harness_logger.addHandler(handler)

        try:
            harness = TradingHarnessPlugin()
            exporter = get_in_memory_span_exporter()
            if exporter is not None:
                exporter.clear()

            ctx = _DummyCtx()
            tool = _DummyTool("update_trader_mandate")
            synthetic_oauth_tok = "ya29" + ".syntheticTestOAuthTokenValue12345"
            sensitive_args = {
                "symbol": "GOOGL",
                "notes": "Contact trader@desk.io or 415-555-0144; acct ACCT-77665544",
                "access_token": synthetic_oauth_tok,
            }
            await harness.before_tool_callback(
                tool=tool,  # type: ignore[arg-type]
                tool_args=sensitive_args,
                tool_context=ctx,  # type: ignore[arg-type]
            )
            await harness.after_tool_callback(
                tool=tool,  # type: ignore[arg-type]
                tool_args=sensitive_args,
                tool_context=ctx,  # type: ignore[arg-type]
                result={
                    "status": "success",
                    "symbol": "GOOGL",
                    "reason": "Approved for trader@desk.io (SSN 123-45-6789)",
                },
            )
            err_resp = await harness.on_tool_error_callback(
                tool=tool,  # type: ignore[arg-type]
                tool_args=sensitive_args,
                tool_context=ctx,  # type: ignore[arg-type]
                error=RuntimeError("Upstream auth failed for Bearer secret_jwt_token_9876543210"),
            )
            assert err_resp is not None
            self.assertNotIn("secret_jwt_token_9876543210", err_resp["recoverable_error"])
            self.assertIn("[REDACTED_SECRET]", err_resp["recoverable_error"])
            self.assertGreater(harness.metrics["pii_redactions"], 0)

            log_output = stream.getvalue()
            self.assertNotIn("trader@desk.io", log_output)
            self.assertNotIn("415-555-0144", log_output)
            self.assertNotIn("ACCT-77665544", log_output)
            self.assertNotIn("123-45-6789", log_output)
            self.assertNotIn("syntheticTestOAuthTokenValue12345", log_output)
            self.assertIn("[REDACTED_EMAIL]", log_output)

            if exporter is not None:
                spans = exporter.get_finished_spans()
                self.assertGreaterEqual(len(spans), 1)
                span_attrs_json = json.dumps(dict(spans[-1].attributes or {}))
                self.assertNotIn("trader@desk.io", span_attrs_json)
                self.assertNotIn("123-45-6789", span_attrs_json)
                self.assertNotIn("syntheticTestOAuthTokenValue12345", span_attrs_json)
                self.assertIn("[REDACTED_EMAIL]", span_attrs_json)
        finally:
            harness_logger.removeHandler(handler)

    async def test_session_and_memory_pipelines_scrub_pii_before_storage(self) -> None:
        from google.adk.events.event import Event
        from google.genai import types

        ctx = _DummyCtx()
        mandate_res = update_trader_mandate(
            risk_tolerance="MODERATE",
            max_drawdown_pct=5.0,
            preferred_watchlist=["GOOGL"],
            notes="Send fills to portfolio.mgr@alpha.com, SSN 123-45-6789, avoid FOMC.",
            tool_context=ctx,  # type: ignore[arg-type]
        )
        self.assertNotIn("portfolio.mgr@alpha.com", str(ctx.state["mandate_notes"]))
        self.assertNotIn("123-45-6789", str(ctx.state["mandate_notes"]))
        self.assertGreaterEqual(mandate_res["updated_user_mandate"]["pii_redactions_applied"], 2)

        halt_res = toggle_desk_kill_switch(
            True,
            reason="Manual halt by risk.lead@alpha.com from 198.51.100.77",
            tool_context=ctx,  # type: ignore[arg-type]
        )
        self.assertNotIn("risk.lead@alpha.com", halt_res["reason"])
        self.assertNotIn("198.51.100.77", halt_res["reason"])

        session_svc = build_session_service()
        memory_svc = build_memory_service()

        sess = await session_svc.create_session(
            app_name="alphapulse_pii_test",
            user_id="trader_01",
            state={
                "email": "raw.trader@alpha.com",
                "mandate_notes": "Account ACCT-11223344 prefers GOOGL semiconductor momentum.",
            },
        )
        self.assertEqual(sess.state["email"], "[REDACTED_EMAIL]")
        self.assertNotIn("ACCT-11223344", sess.state["mandate_notes"])
        self.assertIn("[REDACTED_ACCOUNT_ID]", sess.state["mandate_notes"])

        ev = Event(
            author="user",
            content=types.Content(
                role="user",
                parts=[
                    types.Part.from_text(
                        text=(
                            "My SSN is 123-45-6789 and email is raw.trader@alpha.com. "
                            "Remember our semiconductor thesis on NVDA and GOOGL."
                        )
                    )
                ],
            ),
        )
        await session_svc.append_event(sess, ev)
        stored_sess = await session_svc.get_session(
            app_name="alphapulse_pii_test",
            user_id="trader_01",
            session_id=sess.id,
        )
        assert stored_sess is not None
        stored_text = stored_sess.events[0].content.parts[0].text  # type: ignore[union-attr,index]
        assert stored_text is not None
        self.assertNotIn("123-45-6789", stored_text)
        self.assertNotIn("raw.trader@alpha.com", stored_text)
        self.assertIn("semiconductor thesis on NVDA and GOOGL", stored_text)

        # Ingest into MemoryService and verify search_memory returns only scrubbed text
        await memory_svc.add_session_to_memory(stored_sess)
        mem_resp = await memory_svc.search_memory(
            app_name="alphapulse_pii_test",
            user_id="trader_01",
            query="semiconductor thesis raw.trader@alpha.com",
        )
        recalled_blob = json.dumps([m.model_dump() for m in mem_resp.memories], default=str)
        self.assertNotIn("123-45-6789", recalled_blob)
        self.assertNotIn("raw.trader@alpha.com", recalled_blob)
        self.assertIn("semiconductor thesis", recalled_blob)

        recall_tool_res = await recall_trading_memory(
            query="semiconductor raw.trader@alpha.com",
            tool_context=None,
        )
        self.assertNotIn("raw.trader@alpha.com", recall_tool_res["query"])


if __name__ == "__main__":
    unittest.main()
