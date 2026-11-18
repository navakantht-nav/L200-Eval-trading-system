"""ADKv2 Agent Harness: BasePlugin, Model Armor, PII Scrubbing, Guardrails & OpenTelemetry."""

from __future__ import annotations

import json
import logging
import re
import time
from typing import TYPE_CHECKING, Any

from .config import DEFAULT_CONFIG
from .pii_scrubber import (
    PiiRedactingLogFilter,
    pseudonymize_identifier,
    sanitize_session_for_memory,
    scrub_pii_payload,
    scrub_pii_text,
)

if TYPE_CHECKING:
    from google.adk.agents.callback_context import CallbackContext
    from google.adk.agents.invocation_context import InvocationContext
    from google.adk.models.llm_request import LlmRequest
    from google.adk.models.llm_response import LlmResponse
    from google.adk.tools.base_tool import BaseTool
    from google.adk.tools.tool_context import ToolContext

try:
    from google.adk.plugins.base_plugin import BasePlugin as _AdkBasePlugin
except ImportError:  # Allows unit testing harness logic before `uv sync`
    class _AdkBasePlugin:  # type: ignore[no-redef]
        def __init__(self, name: str) -> None:
            self.name = name

_pii_log_filter = PiiRedactingLogFilter()
logging.getLogger("alphapulse").addFilter(_pii_log_filter)
logger = logging.getLogger("alphapulse.harness")
logger.addFilter(_pii_log_filter)

_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"ignore\s+(all\s+)?(previous|prior)\s+instructions", re.IGNORECASE),
    re.compile(r"bypass\s+(all\s+)?risk\s+(limits|guardrails|checks)", re.IGNORECASE),
    re.compile(r"disable\s+paper\s+trading", re.IGNORECASE),
    re.compile(r"execute\s+real\s+money\s+wire", re.IGNORECASE),
    re.compile(r"override\s+ap2\s+mandate", re.IGNORECASE),
)

#Module-level reference to the in-memory span exporter for programmatic audit/testing
_IN_MEMORY_SPAN_EXPORTER: Any = None


def get_in_memory_span_exporter() -> Any:
    """Returns the active OpenTelemetry `InMemorySpanExporter`, if configured."""
    return _IN_MEMORY_SPAN_EXPORTER


def _scrub_span_attributes_in_place(span: Any) -> None:
    """Scrubs PII/SPII and secrets from OpenTelemetry span attributes prior to export."""
    attrs = getattr(span, "_attributes", None)
    if attrs is None:
        return
    try:
        items = list(attrs.items())
    except Exception:
        return
    for key, val in items:
        clean_val, changed = scrub_pii_payload(val, pseudonymize_ids=True)
        if changed > 0 or clean_val != val:
            try:
                attrs[key] = clean_val
            except Exception:
                pass


def configure_otel_tracing() -> Any:
    """Configures OpenTelemetry TracerProvider with PII-scrubbing SpanProcessor."""
    global _IN_MEMORY_SPAN_EXPORTER
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

        class PiiScrubbingSpanProcessor(SpanProcessor):
            """Intercepts spans on_end to scrub PII/secrets before any exporter runs."""

            def on_start(self, span: Any, parent_context: Any = None) -> None:
                _scrub_span_attributes_in_place(span)

            def on_end(self, span: Any) -> None:
                _scrub_span_attributes_in_place(span)

            def shutdown(self) -> None:
                return None

            def force_flush(self, timeout_millis: int = 30000) -> bool:
                return True

        current = trace.get_tracer_provider()
        if isinstance(current, TracerProvider):
            return trace.get_tracer("alphapulse.adkv2")

        resource = Resource.create(
            {
                "service.name": DEFAULT_CONFIG.otel_service_name,
                "service.version": "0.3.0",
                "gcp.project_id": DEFAULT_CONFIG.gcp_project or "local-dev",
            }
        )
        provider = TracerProvider(resource=resource)
        # Register PII-scrubbing processor FIRST before any span exporter sees the span
        provider.add_span_processor(PiiScrubbingSpanProcessor())

        memory_exporter = InMemorySpanExporter()
        _IN_MEMORY_SPAN_EXPORTER = memory_exporter
        provider.add_span_processor(SimpleSpanProcessor(memory_exporter))

        if DEFAULT_CONFIG.enable_cloud_trace and DEFAULT_CONFIG.gcp_project:
            try:
                from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter

                cloud_exporter = CloudTraceSpanExporter(project_id=DEFAULT_CONFIG.gcp_project)
                provider.add_span_processor(BatchSpanProcessor(cloud_exporter))
            except Exception as exc:
                logger.warning("CloudTraceSpanExporter unavailable, continuing locally: %s", exc)

        trace.set_tracer_provider(provider)
        return trace.get_tracer("alphapulse.adkv2")
    except ImportError:
        return None


def extract_latest_user_text(llm_request: Any) -> str:
    """Extracts the latest user prompt text from an ADKv2 `LlmRequest`."""
    contents = getattr(llm_request, "contents", None) or []
    for content in reversed(contents):
        if getattr(content, "role", None) == "user":
            parts = getattr(content, "parts", None) or []
            texts = [getattr(p, "text", "") for p in parts if getattr(p, "text", None)]
            if texts:
                return "\n".join(texts)
    return ""


def screen_with_model_armor(text: str) -> str | None:
    """Screens text via Google Cloud Model Armor template (when configured) + local floor."""
    if not text:
        return None

    for pattern in _INJECTION_PATTERNS:
        if pattern.search(text):
            return (
                "Blocked by AlphaPulse Model Armor & Compliance Floor: "
                "Prompt attempts to bypass risk limits, AP2 mandates, or paper-trading isolation."
            )

    if DEFAULT_CONFIG.model_armor_template and DEFAULT_CONFIG.gcp_project:
        try:
            import google.auth
            import httpx
            from google.auth.transport.requests import Request

            creds, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            creds.refresh(Request())
            location = DEFAULT_CONFIG.gcp_infra_region
            endpoint = (
                f"https://modelarmor.{location}.rep.googleapis.com/v1/"
                f"{DEFAULT_CONFIG.model_armor_template}:sanitizeUserPrompt"
            )
            sanitized_text, _ = scrub_pii_text(text)
            resp = httpx.post(
                endpoint,
                headers={"Authorization": f"Bearer {creds.token}"},
                json={"userPromptData": {"text": sanitized_text}},
                timeout=3.0,
            )
            if resp.status_code == 200:
                body = resp.json()
                match_state = (
                    body.get("sanitizationResult", {}).get("filterMatchState", "")
                )
                if match_state == "MATCH_FOUND":
                    return "Blocked by GCP Model Armor Policy Template (MATCH_FOUND)."
        except Exception as exc:
            clean_err, _ = scrub_pii_text(str(exc))
            logger.debug("Model Armor API fallback to local floor: %s", clean_err)

    return None


def validate_tool_invocation(tool_name: str, args: dict[str, Any]) -> dict[str, Any] | None:
    """Deterministic pre-tool circuit breaker for restricted tickers and fat-finger orders."""
    symbol = str(args.get("symbol", "")).strip().upper()
    if symbol and symbol in DEFAULT_CONFIG.restricted_tickers:
        return {
            "status": "blocked_by_guardrail",
            "reason": (
                f"Ticker '{symbol}' is restricted by compliance policy "
                f"{sorted(DEFAULT_CONFIG.restricted_tickers)}."
            ),
        }

    if tool_name in {"execute_paper_trade", "settle_ap2_paper_trade", "mcp_settle_ap2_paper_trade"}:
        if DEFAULT_CONFIG.trading_mode != "PAPER":
            return {
                "status": "blocked_by_guardrail",
                "reason": "Live brokerage routing is disabled; PAPER mode is mandatory.",
            }

    if tool_name == "execute_paper_trade":
        qty = int(args.get("quantity", 0) or 0)
        limit_px = args.get("limit_price")
        if qty <= 0:
            return {
                "status": "blocked_by_guardrail",
                "reason": "Order quantity must be a positive integer.",
            }
        if limit_px is not None and float(limit_px) * qty > DEFAULT_CONFIG.max_single_order_usd * 2.0:
            return {
                "status": "blocked_by_guardrail",
                "reason": (
                    f"Fat-finger circuit breaker tripped: notional "
                    f"${float(limit_px) * qty:,.2f} exceeds 2x desk order cap "
                    f"(${DEFAULT_CONFIG.max_single_order_usd * 2.0:,.2f})."
                ),
            }
    return None


def _summarize_outcome_for_telemetry(result: Any) -> tuple[str, int]:
    """Produces a compact, PII-scrubbed outcome summary for logs and OTel spans."""
    if isinstance(result, dict):
        compact = {
            k: v
            for k, v in result.items()
            if k in {"status", "symbol", "approved", "order_id", "reason", "recoverable_error"}
        }
        scrubbed, count = scrub_pii_payload(compact, pseudonymize_ids=True)
        return json.dumps(scrubbed, default=str), count
    if isinstance(result, str):
        clean_str, count = scrub_pii_text(result[:256])
        return clean_str, count
    return "ok", 0


class TradingHarnessPlugin(_AdkBasePlugin):
    """Global ADKv2 runtime harness enforcing governance, PII redaction, telemetry & memory."""

    def __init__(self, name: str = "alphapulse_trading_harness") -> None:
        super().__init__(name=name)
        self._tracer = configure_otel_tracing()
        self.metrics: dict[str, Any] = {
            "runs_started": 0,
            "runs_completed": 0,
            "model_calls": 0,
            "prompt_tokens": 0,
            "candidate_tokens": 0,
            "tool_calls": 0,
            "tool_errors": 0,
            "guardrail_blocks": 0,
            "memory_ingests": 0,
            "pii_redactions": 0,
            "tool_latencies_ms": [],
        }

    def _emit_structured_log(self, event_type: str, **fields: Any) -> None:
        scrubbed_fields, redaction_count = scrub_pii_payload(fields, pseudonymize_ids=True)
        self.metrics["pii_redactions"] += redaction_count
        record = {
            "service": DEFAULT_CONFIG.otel_service_name,
            "event": event_type,
            "pii_scrubbed": True,
            **(scrubbed_fields if isinstance(scrubbed_fields, dict) else {}),
        }
        logger.info(json.dumps(record, default=str))

    async def before_run_callback(
        self, *, invocation_context: InvocationContext
    ) -> Any:
        self.metrics["runs_started"] += 1
        session = getattr(invocation_context, "session", None)
        raw_session_id = str(getattr(session, "id", "unknown"))
        clean_session_id, n = scrub_pii_text(raw_session_id)
        self.metrics["pii_redactions"] += n
        raw_user_id = str(getattr(session, "user_id", "unknown"))
        self._emit_structured_log(
            "invocation.start",
            session_id=clean_session_id,
            user_pseudo_id=pseudonymize_identifier(raw_user_id),
        )
        return None

    async def before_model_callback(
        self, *, callback_context: CallbackContext, llm_request: LlmRequest
    ) -> LlmResponse | None:
        self.metrics["model_calls"] += 1
        user_text = extract_latest_user_text(llm_request)
        _, pii_hits = scrub_pii_text(user_text)
        if pii_hits > 0:
            self.metrics["pii_redactions"] += pii_hits

        violation = screen_with_model_armor(user_text)
        if violation is None:
            return None

        self.metrics["guardrail_blocks"] += 1
        callback_context.state["last_guardrail_block"] = violation
        self._emit_structured_log(
            "guardrail.model_blocked",
            agent=getattr(callback_context, "agent_name", "unknown"),
            reason=violation,
        )

        from google.adk.models.llm_response import LlmResponse
        from google.genai import types

        return LlmResponse(
            content=types.Content(
                role="model",
                parts=[types.Part.from_text(text=violation)],
            )
        )

    async def after_model_callback(
        self, *, callback_context: CallbackContext, llm_response: LlmResponse
    ) -> LlmResponse | None:
        usage = getattr(llm_response, "usage_metadata", None)
        if usage is not None:
            self.metrics["prompt_tokens"] += int(getattr(usage, "prompt_token_count", 0) or 0)
            self.metrics["candidate_tokens"] += int(
                getattr(usage, "candidates_token_count", 0) or 0
            )
        return None

    async def before_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
    ) -> dict[str, Any] | None:
        self.metrics["tool_calls"] += 1
        tool_name = getattr(tool, "name", "unknown_tool")
        tool_context.state[f"temp:span_start_{tool_name}"] = time.perf_counter_ns()

        redacted_intent, redaction_count = scrub_pii_payload(tool_args, pseudonymize_ids=True)
        self.metrics["pii_redactions"] += redaction_count
        self._emit_structured_log(
            "tool.intent",
            tool=tool_name,
            intent_args=redacted_intent,
        )

        block = validate_tool_invocation(tool_name, tool_args)
        if block is not None:
            self.metrics["guardrail_blocks"] += 1
            tool_context.state["last_guardrail_block"] = block["reason"]
            self._emit_structured_log(
                "guardrail.tool_blocked", tool=tool_name, reason=block["reason"]
            )
            return block
        return None

    async def after_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
        result: Any,
    ) -> dict[str, Any] | None:
        tool_name = getattr(tool, "name", "unknown_tool")
        start_ns = tool_context.state.get(f"temp:span_start_{tool_name}")
        latency_ms = (
            round((time.perf_counter_ns() - int(start_ns)) / 1_000_000.0, 3)
            if start_ns
            else 0.0
        )
        self.metrics["tool_latencies_ms"].append({"tool": tool_name, "latency_ms": latency_ms})
        tool_status = str(result.get("status", "ok")) if isinstance(result, dict) else "ok"

        redacted_args, args_redactions = scrub_pii_payload(tool_args, pseudonymize_ids=True)
        outcome_summary, out_redactions = _summarize_outcome_for_telemetry(result)
        total_span_redactions = args_redactions + out_redactions
        self.metrics["pii_redactions"] += total_span_redactions

        if self._tracer is not None:
            with self._tracer.start_as_current_span(f"tool.{tool_name}") as span:
                span.set_attribute("adk.tool.name", tool_name)
                span.set_attribute("adk.tool.latency_ms", latency_ms)
                span.set_attribute("adk.tool.status", tool_status)
                span.set_attribute("adk.tool.intent_redacted", json.dumps(redacted_args, default=str))
                span.set_attribute("adk.tool.outcome_redacted", outcome_summary)
                span.set_attribute("adk.pii.redactions_count", total_span_redactions)

        # Attach MCP App UiWidget when tool emits an MCP UI widget descriptor
        if isinstance(result, dict) and "mcp_ui_widget" in result:
            try:
                from google.adk.events.ui_widget import UiWidget

                widget_spec = result["mcp_ui_widget"]
                render_fn = getattr(tool_context, "render_ui_widget", None)
                if callable(render_fn):
                    render_fn(
                        UiWidget(
                            id=widget_spec["id"],
                            provider=widget_spec["provider"],
                            payload=widget_spec["payload"],
                        )
                    )
            except Exception as exc:
                logger.debug("UiWidget attachment skipped: %s", exc)

        self._emit_structured_log(
            "tool.completed",
            tool=tool_name,
            latency_ms=latency_ms,
            status=tool_status,
            outcome=outcome_summary,
        )
        return None

    async def on_tool_error_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
        error: Exception,
    ) -> dict[str, Any] | None:
        self.metrics["tool_errors"] += 1
        tool_name = getattr(tool, "name", "unknown_tool")
        scrubbed_err, redaction_count = scrub_pii_text(str(error))
        self.metrics["pii_redactions"] += redaction_count
        self._emit_structured_log("tool.error", tool=tool_name, error=scrubbed_err)
        return {
            "status": "error",
            "tool": tool_name,
            "recoverable_error": scrubbed_err,
        }

    async def after_run_callback(
        self, *, invocation_context: InvocationContext
    ) -> None:
        self.metrics["runs_completed"] += 1
        memory_service = getattr(invocation_context, "memory_service", None)
        session = getattr(invocation_context, "session", None)
        if memory_service is not None and session is not None:
            state = getattr(session, "state", {}) or {}
            if state.get("last_execution_fill") or state.get("mandate_notes"):
                try:
                    sanitized_session, redactions = sanitize_session_for_memory(session)
                    self.metrics["pii_redactions"] += redactions
                    await memory_service.add_session_to_memory(sanitized_session)
                    self.metrics["memory_ingests"] += 1
                except Exception as exc:
                    clean_err, _ = scrub_pii_text(str(exc))
                    logger.debug("Automatic memory consolidation skipped: %s", clean_err)
