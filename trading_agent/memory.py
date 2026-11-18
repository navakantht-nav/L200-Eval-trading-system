"""Session, scoped State (`user:`, `app:`, `temp:`), and Memory Bank integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .config import DEFAULT_CONFIG, TradingDeskConfig

if TYPE_CHECKING:
    from google.adk.memory.base_memory_service import BaseMemoryService
    from google.adk.sessions.base_session_service import BaseSessionService
    from google.adk.tools import ToolContext


def update_trader_mandate(
    risk_tolerance: str,
    max_drawdown_pct: float,
    preferred_watchlist: list[str],
    preferred_order_type: str = "TWAP",
    notes: str = "",
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Saves cross-session trader preferences into ADKv2 `user:*` scoped state.

    Args:
        risk_tolerance: Trader risk profile ('CONSERVATIVE', 'MODERATE', or 'AGGRESSIVE').
        max_drawdown_pct: Maximum tolerated portfolio drawdown percentage (for example, 5.0).
        preferred_watchlist: List of ticker symbols monitored by the trader.
        preferred_order_type: Default execution algorithm ('TWAP', 'LIMIT', or 'MARKET').
        notes: Optional desk mandate notes persisted for Memory Bank consolidation.
        tool_context: Optional ADK ToolContext used to persist `user:` scoped state.

    Returns:
        Confirmation of updated `user:` state keys.
    """
    clean_profile = risk_tolerance.strip().upper()
    if clean_profile not in {"CONSERVATIVE", "MODERATE", "AGGRESSIVE"}:
        return {
            "status": "error",
            "error": "risk_tolerance must be CONSERVATIVE, MODERATE, or AGGRESSIVE.",
        }

    watchlist = [s.strip().upper() for s in preferred_watchlist if s.strip()]
    order_style = preferred_order_type.strip().upper()
    mandate = {
        "risk_tolerance": clean_profile,
        "max_drawdown_pct": round(float(max_drawdown_pct), 2),
        "preferred_watchlist": watchlist,
        "preferred_order_type": order_style,
        "notes": notes.strip(),
    }

    if tool_context is not None:
        tool_context.state["user:risk_profile"] = clean_profile
        tool_context.state["user:max_drawdown_pct"] = mandate["max_drawdown_pct"]
        tool_context.state["user:watchlist"] = watchlist
        tool_context.state["user:exec_style"] = order_style
        if notes.strip():
            tool_context.state["mandate_notes"] = notes.strip()

    return {"status": "success", "updated_user_mandate": mandate}


def toggle_desk_kill_switch(
    halt_trading: bool,
    reason: str = "Manual risk officer override",
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Toggles the global `app:desk_halt` kill switch shared across all sessions.

    Args:
        halt_trading: True to halt all order approvals across the app; False to resume.
        reason: Audit explanation recorded alongside the kill-switch toggle.
        tool_context: Optional ADK ToolContext mutating `app:` scoped state.

    Returns:
        Updated global kill-switch state.
    """
    if tool_context is not None:
        tool_context.state["app:desk_halt"] = bool(halt_trading)
        tool_context.state["app:desk_halt_reason"] = reason.strip()
    return {
        "status": "success",
        "app_desk_halt": bool(halt_trading),
        "reason": reason.strip(),
    }


async def recall_trading_memory(
    query: str,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Queries long-term Memory Bank (`VertexAiMemoryBankService` / `InMemoryMemoryService`).

    Args:
        query: Semantic or keyword search query (for example, 'FOMC semiconductor thesis').
        tool_context: Optional ADK ToolContext connected to the active `MemoryService`.

    Returns:
        Matched historical memory entries merged with current `user:*` preferences.
    """
    matches: list[str] = []
    user_mandate: dict[str, Any] = {}

    if tool_context is not None:
        user_mandate = {
            "risk_profile": tool_context.state.get("user:risk_profile", "MODERATE"),
            "watchlist": tool_context.state.get("user:watchlist", []),
            "exec_style": tool_context.state.get("user:exec_style", "TWAP"),
        }
        search_fn = getattr(tool_context, "search_memory", None)
        if callable(search_fn):
            try:
                response = await search_fn(query)
                for mem in getattr(response, "memories", []) or []:
                    content = getattr(mem, "content", None)
                    parts = getattr(content, "parts", []) if content else []
                    for part in parts:
                        if getattr(part, "text", None):
                            matches.append(str(part.text))
            except ValueError:
                pass

    return {
        "status": "success",
        "query": query,
        "user_scoped_mandate": user_mandate,
        "recalled_memories": matches,
    }


def build_session_service(config: TradingDeskConfig = DEFAULT_CONFIG) -> BaseSessionService:
    """Instantiates the configured ADKv2 `SessionService` tier."""
    if config.session_backend == "vertex_ai" and config.gcp_project:
        from google.adk.sessions import VertexAiSessionService

        return VertexAiSessionService(
            project=config.gcp_project,
            location=config.gcp_location,
            agent_engine_id=config.agent_engine_id or None,
        )
    if config.session_backend == "sqlite":
        from google.adk.sessions import DatabaseSessionService

        return DatabaseSessionService(db_url=config.session_db_url)

    from google.adk.sessions import InMemorySessionService

    return InMemorySessionService()


def build_memory_service(config: TradingDeskConfig = DEFAULT_CONFIG) -> BaseMemoryService:
    """Instantiates the configured ADKv2 `MemoryService` tier."""
    if config.memory_backend == "vertex_ai" and config.gcp_project:
        from google.adk.memory import VertexAiMemoryBankService

        return VertexAiMemoryBankService(
            project=config.gcp_project,
            location=config.gcp_location,
            agent_engine_id=config.agent_engine_id or None,
        )

    from google.adk.memory import InMemoryMemoryService

    return InMemoryMemoryService()
