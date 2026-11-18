"""Session, scoped State (`user:`, `app:`, `temp:`), and PII-Scrubbed Memory Bank."""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from .config import DEFAULT_CONFIG, TradingDeskConfig
from .pii_scrubber import (
    sanitize_event_for_storage,
    sanitize_session_for_memory,
    scrub_pii_payload,
    scrub_pii_text,
)

if TYPE_CHECKING:
    from google.adk.events.event import Event
    from google.adk.memory.base_memory_service import BaseMemoryService, SearchMemoryResponse
    from google.adk.memory.memory_entry import MemoryEntry
    from google.adk.sessions.base_session_service import (
        BaseSessionService,
        GetSessionConfig,
        ListSessionsResponse,
    )
    from google.adk.sessions.session import Session
    from google.adk.tools import ToolContext


def update_trader_mandate(
    risk_tolerance: str,
    max_drawdown_pct: float,
    preferred_watchlist: list[str],
    preferred_order_type: str = "TWAP",
    notes: str = "",
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Saves PII-scrubbed cross-session trader preferences into ADKv2 `user:*` state.

    Args:
        risk_tolerance: Trader risk profile ('CONSERVATIVE', 'MODERATE', or 'AGGRESSIVE').
        max_drawdown_pct: Maximum tolerated portfolio drawdown percentage (for example, 5.0).
        preferred_watchlist: List of ticker symbols monitored by the trader.
        preferred_order_type: Default execution algorithm ('TWAP', 'LIMIT', or 'MARKET').
        notes: Optional desk mandate notes (automatically scrubbed for PII before storage).
        tool_context: Optional ADK ToolContext used to persist `user:` scoped state.

    Returns:
        Confirmation of updated `user:` state keys and count of redacted PII items.
    """
    clean_profile = risk_tolerance.strip().upper()
    if clean_profile not in {"CONSERVATIVE", "MODERATE", "AGGRESSIVE"}:
        return {
            "status": "error",
            "error": "risk_tolerance must be CONSERVATIVE, MODERATE, or AGGRESSIVE.",
        }

    watchlist = [s.strip().upper() for s in preferred_watchlist if s.strip()]
    order_style = preferred_order_type.strip().upper()
    sanitized_notes, redactions = scrub_pii_text(notes.strip(), allow_cloud_sdp=True)
    mandate = {
        "risk_tolerance": clean_profile,
        "max_drawdown_pct": round(float(max_drawdown_pct), 2),
        "preferred_watchlist": watchlist,
        "preferred_order_type": order_style,
        "notes": sanitized_notes,
        "pii_redactions_applied": redactions,
    }

    if tool_context is not None:
        tool_context.state["user:risk_profile"] = clean_profile
        tool_context.state["user:max_drawdown_pct"] = mandate["max_drawdown_pct"]
        tool_context.state["user:watchlist"] = watchlist
        tool_context.state["user:exec_style"] = order_style
        if sanitized_notes:
            tool_context.state["mandate_notes"] = sanitized_notes

    return {"status": "success", "updated_user_mandate": mandate}


def toggle_desk_kill_switch(
    halt_trading: bool,
    reason: str = "Manual risk officer override",
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Toggles the global `app:desk_halt` kill switch with PII-sanitized audit reason.

    Args:
        halt_trading: True to halt all order approvals across the app; False to resume.
        reason: Audit explanation recorded alongside the kill-switch toggle.
        tool_context: Optional ADK ToolContext mutating `app:` scoped state.

    Returns:
        Updated global kill-switch state.
    """
    sanitized_reason, redactions = scrub_pii_text(reason.strip(), allow_cloud_sdp=True)
    if tool_context is not None:
        tool_context.state["app:desk_halt"] = bool(halt_trading)
        tool_context.state["app:desk_halt_reason"] = sanitized_reason
    return {
        "status": "success",
        "app_desk_halt": bool(halt_trading),
        "reason": sanitized_reason,
        "pii_redactions_applied": redactions,
    }


async def recall_trading_memory(
    query: str,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Queries long-term Memory Bank with PII scrubbing on query and recalled entries.

    Args:
        query: Semantic or keyword search query (for example, 'FOMC semiconductor thesis').
        tool_context: Optional ADK ToolContext connected to the active `MemoryService`.

    Returns:
        Matched historical memory entries merged with current `user:*` preferences.
    """
    sanitized_query, query_redactions = scrub_pii_text(query, allow_cloud_sdp=True)
    matches: list[str] = []
    user_mandate: dict[str, Any] = {}
    egress_redactions = 0

    if tool_context is not None:
        user_mandate = {
            "risk_profile": tool_context.state.get("user:risk_profile", "MODERATE"),
            "watchlist": tool_context.state.get("user:watchlist", []),
            "exec_style": tool_context.state.get("user:exec_style", "TWAP"),
        }
        search_fn = getattr(tool_context, "search_memory", None)
        if callable(search_fn):
            try:
                response = await search_fn(sanitized_query)
                for mem in getattr(response, "memories", []) or []:
                    content = getattr(mem, "content", None)
                    parts = getattr(content, "parts", []) if content else []
                    for part in parts:
                        if getattr(part, "text", None):
                            clean_text, r = scrub_pii_text(str(part.text))
                            egress_redactions += r
                            matches.append(clean_text)
            except ValueError:
                pass

    return {
        "status": "success",
        "query": sanitized_query,
        "user_scoped_mandate": user_mandate,
        "recalled_memories": matches,
        "pii_redactions_applied": query_redactions + egress_redactions,
    }


def wrap_session_service_with_pii_scrubber(inner: BaseSessionService) -> BaseSessionService:
    """Wraps any ADKv2 `BaseSessionService` in a deterministic PII-scrubbing proxy."""
    from google.adk.sessions.base_session_service import BaseSessionService

    class PiiScrubbingSessionService(BaseSessionService):
        """Transparent `BaseSessionService` proxy scrubbing PII/secrets before storage."""

        def __init__(self, backend: BaseSessionService) -> None:
            self.inner = backend
            self.redactions_applied = 0

        async def create_session(
            self,
            *,
            app_name: str,
            user_id: str,
            state: dict[str, Any] | None = None,
            session_id: str | None = None,
        ) -> Session:
            clean_state = state
            if state:
                scrubbed, count = scrub_pii_payload(state, allow_cloud_sdp=True)
                self.redactions_applied += count
                if isinstance(scrubbed, dict):
                    clean_state = scrubbed
            return await self.inner.create_session(
                app_name=app_name,
                user_id=user_id,
                state=clean_state,
                session_id=session_id,
            )

        async def get_session(
            self,
            *,
            app_name: str,
            user_id: str,
            session_id: str,
            config: GetSessionConfig | None = None,
        ) -> Session | None:
            return await self.inner.get_session(
                app_name=app_name,
                user_id=user_id,
                session_id=session_id,
                config=config,
            )

        async def list_sessions(
            self, *, app_name: str, user_id: str | None = None
        ) -> ListSessionsResponse:
            return await self.inner.list_sessions(app_name=app_name, user_id=user_id)

        async def delete_session(
            self, *, app_name: str, user_id: str, session_id: str
        ) -> None:
            await self.inner.delete_session(
                app_name=app_name, user_id=user_id, session_id=session_id
            )

        async def get_user_state(
            self, *, app_name: str, user_id: str
        ) -> dict[str, Any]:
            return await self.inner.get_user_state(app_name=app_name, user_id=user_id)

        async def append_event(self, session: Session, event: Event) -> Event:
            clean_event, count = sanitize_event_for_storage(event)
            self.redactions_applied += count
            return await self.inner.append_event(session, clean_event)

        async def flush(self) -> None:
            flush_fn = getattr(self.inner, "flush", None)
            if callable(flush_fn):
                await flush_fn()

    return PiiScrubbingSessionService(inner)


def build_session_service(config: TradingDeskConfig = DEFAULT_CONFIG) -> BaseSessionService:
    """Instantiates the configured ADKv2 `SessionService` wrapped in PII scrubbing."""
    if config.session_backend == "vertex_ai" and config.gcp_project:
        from google.adk.sessions import VertexAiSessionService

        raw_session_svc: BaseSessionService = VertexAiSessionService(
            project=config.gcp_project,
            location=config.gcp_infra_region,
            agent_engine_id=config.agent_engine_id or None,
        )
    elif config.session_backend == "sqlite":
        from google.adk.sessions import DatabaseSessionService

        raw_session_svc = DatabaseSessionService(db_url=config.session_db_url)
    else:
        from google.adk.sessions import InMemorySessionService

        raw_session_svc = InMemorySessionService()

    return wrap_session_service_with_pii_scrubber(raw_session_svc)


def wrap_memory_service_with_pii_scrubber(inner: BaseMemoryService) -> BaseMemoryService:
    """Wraps any ADKv2 `BaseMemoryService` in a deterministic PII-scrubbing proxy."""
    from google.adk.memory.base_memory_service import BaseMemoryService

    class PiiScrubbingMemoryService(BaseMemoryService):
        """Transparent `BaseMemoryService` proxy scrubbing PII/SPII before Memory Bank storage."""

        def __init__(self, backend: BaseMemoryService) -> None:
            self.inner = backend
            self.redactions_applied = 0

        async def add_session_to_memory(self, session: Session) -> None:
            sanitized_session, count = sanitize_session_for_memory(session)
            self.redactions_applied += count
            await self.inner.add_session_to_memory(sanitized_session)

        async def add_events_to_memory(
            self,
            *,
            app_name: str,
            user_id: str,
            events: Sequence[Event],
            session_id: str | None = None,
            custom_metadata: Mapping[str, object] | None = None,
        ) -> None:
            cleaned_events: list[Event] = []
            for ev in events:
                clean_ev, count = sanitize_event_for_storage(ev)
                self.redactions_applied += count
                cleaned_events.append(clean_ev)
            clean_meta: Mapping[str, object] | None = custom_metadata
            if custom_metadata is not None:
                scrubbed_meta, m_count = scrub_pii_payload(custom_metadata, allow_cloud_sdp=True)
                self.redactions_applied += m_count
                if isinstance(scrubbed_meta, Mapping):
                    clean_meta = scrubbed_meta
            await self.inner.add_events_to_memory(
                app_name=app_name,
                user_id=user_id,
                events=cleaned_events,
                session_id=session_id,
                custom_metadata=clean_meta,
            )

        async def add_memory(
            self,
            *,
            app_name: str,
            user_id: str,
            memories: Sequence[MemoryEntry],
            custom_metadata: Mapping[str, object] | None = None,
        ) -> None:
            cleaned_memories: list[MemoryEntry] = []
            for entry in memories:
                clone_fn = getattr(entry, "model_copy", None)
                cloned = clone_fn(deep=True) if callable(clone_fn) else copy.deepcopy(entry)
                content = getattr(cloned, "content", None)
                for part in getattr(content, "parts", []) or []:
                    if getattr(part, "text", None):
                        clean_txt, count = scrub_pii_text(str(part.text), allow_cloud_sdp=True)
                        self.redactions_applied += count
                        part.text = clean_txt
                cleaned_memories.append(cloned)
            await self.inner.add_memory(
                app_name=app_name,
                user_id=user_id,
                memories=cleaned_memories,
                custom_metadata=custom_metadata,
            )

        async def search_memory(
            self,
            *,
            app_name: str,
            user_id: str,
            query: str,
        ) -> SearchMemoryResponse:
            clean_query, q_count = scrub_pii_text(query, allow_cloud_sdp=True)
            self.redactions_applied += q_count
            response = await self.inner.search_memory(
                app_name=app_name,
                user_id=user_id,
                query=clean_query,
            )
            for mem in getattr(response, "memories", []) or []:
                content = getattr(mem, "content", None)
                for part in getattr(content, "parts", []) or []:
                    if getattr(part, "text", None):
                        clean_txt, r_count = scrub_pii_text(str(part.text))
                        self.redactions_applied += r_count
                        part.text = clean_txt
            return response

        async def delete_memory(
            self,
            *,
            app_name: str,
            user_id: str,
            memory_id: str | None = None,
        ) -> None:
            await self.inner.delete_memory(
                app_name=app_name, user_id=user_id, memory_id=memory_id
            )

    return PiiScrubbingMemoryService(inner)


def build_memory_service(config: TradingDeskConfig = DEFAULT_CONFIG) -> BaseMemoryService:
    """Instantiates the configured ADKv2 `MemoryService` wrapped in PII scrubbing."""
    if config.memory_backend == "vertex_ai" and config.gcp_project:
        from google.adk.memory import VertexAiMemoryBankService

        raw_memory_svc: BaseMemoryService = VertexAiMemoryBankService(
            project=config.gcp_project,
            location=config.gcp_infra_region,
            agent_engine_id=config.agent_engine_id or None,
        )
    else:
        from google.adk.memory import InMemoryMemoryService

        raw_memory_svc = InMemoryMemoryService()

    return wrap_memory_service_with_pii_scrubber(raw_memory_svc)
