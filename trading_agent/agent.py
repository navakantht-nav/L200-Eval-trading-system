"""Root ADKv2 `App` & Coordinator wiring MCP, Skills, A2A, A2UI, AP2, Memory, and Harness."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from google.adk.agents.context_cache_config import ContextCacheConfig
from google.adk.agents.llm_agent import LlmAgent
from google.adk.apps.app import App, EventsCompactionConfig, ResumabilityConfig
from google.adk.skills import load_skills_from_dir
from google.adk.tools.preload_memory_tool import PreloadMemoryTool
from google.adk.tools.skill_toolset import SkillToolset

from .config import DEFAULT_CONFIG, TradingDeskConfig
from .harness import TradingHarnessPlugin
from .memory import recall_trading_memory, toggle_desk_kill_switch, update_trader_mandate
from .prompts import COORDINATOR_INSTRUCTION
from .protocols.a2ui_surfaces import emit_a2ui_trading_surface
from .protocols.ap2_mandates import issue_ap2_cart_mandate, settle_ap2_paper_trade
from .protocols.oauth_config import agent_identity_header_provider
from .tools import (
    compute_technical_signals,
    evaluate_pretrade_risk,
    execute_paper_trade,
    fetch_macro_and_news_sentiment,
    fetch_orderbook_microstructure,
    get_portfolio_state,
    stream_realtime_market_ticks,
)
from .workflow_graph import build_institutional_trade_workflow

_SKILLS_DIR = Path(__file__).resolve().parent / "skills"


def _build_mcp_toolset_if_remote(config: TradingDeskConfig) -> list[Any]:
    """Attaches `McpToolset` over Streamable HTTP / SSE with Agent Identity OAuth headers."""
    if config.mcp_transport == "streamable_http":
        from google.adk.tools.mcp_tool import McpToolset, StreamableHTTPConnectionParams

        return [
            McpToolset(
                connection_params=StreamableHTTPConnectionParams(url=config.mcp_server_url),
                tool_name_prefix="remote_mcp",
                header_provider=agent_identity_header_provider,
                use_mcp_resources=True,
                tool_list_cache_ttl_seconds=60.0,
            )
        ]
    if config.mcp_transport == "sse":
        from google.adk.tools.mcp_tool import McpToolset, SseConnectionParams

        return [
            McpToolset(
                connection_params=SseConnectionParams(url=config.mcp_server_url),
                tool_name_prefix="remote_mcp",
                header_provider=agent_identity_header_provider,
                use_mcp_resources=True,
            )
        ]
    return []


def _build_skill_toolset() -> list[Any]:
    """Loads modular ADKv2 `SKILL.md` playbooks via `SkillToolset`."""
    if not _SKILLS_DIR.is_dir():
        return []
    skills = load_skills_from_dir(_SKILLS_DIR)
    if not skills:
        return []
    return [SkillToolset(skills=skills)]


def _build_remote_a2a_subagents(config: TradingDeskConfig) -> list[Any]:
    """Optionally wires federated `RemoteA2aAgent` specialist when external A2A is enabled."""
    try:
        from google.adk.a2a.agent._remote_a2a_agent import RemoteA2aAgent

        macro_a2a = RemoteA2aAgent(
            name="macro_catalyst_a2a_specialist",
            description=(
                "Federated A2A specialist agent providing macro rates, FOMC event windows, "
                "and implied volatility rank analysis."
            ),
            agent_card=f"{config.macro_a2a_url.rstrip('/')}/.well-known/agent.json",
        )
        return [macro_a2a]
    except Exception:
        return []


def create_trading_agent_and_app(
    config: TradingDeskConfig = DEFAULT_CONFIG,
) -> tuple[LlmAgent, App, TradingHarnessPlugin]:
    """Constructs the Coordinator `LlmAgent`, ADKv2 `Workflow`, `TradingHarnessPlugin`, & `App`."""
    harness_plugin = TradingHarnessPlugin()
    trade_workflow = build_institutional_trade_workflow()

    core_tools: list[Any] = [
        PreloadMemoryTool(),
        stream_realtime_market_ticks,
        fetch_orderbook_microstructure,
        fetch_macro_and_news_sentiment,
        compute_technical_signals,
        evaluate_pretrade_risk,
        issue_ap2_cart_mandate,
        settle_ap2_paper_trade,
        execute_paper_trade,
        get_portfolio_state,
        emit_a2ui_trading_surface,
        update_trader_mandate,
        toggle_desk_kill_switch,
        recall_trading_memory,
        trade_workflow,  # ADKv2 Workflow exposed as a structured NodeTool on the Coordinator
        *_build_skill_toolset(),
        *_build_mcp_toolset_if_remote(config),
    ]

    coordinator = LlmAgent(
        name="alphapulse_desk_coordinator",
        model=config.coordinator_model,
        description=(
            "Lead institutional multi-agent trading & risk coordinator supporting "
            "ADKv2 Graph Workflows, MCP, A2A, A2UI v0.9, and AP2 v0.2 mandates."
        ),
        instruction=COORDINATOR_INSTRUCTION,
        tools=core_tools,
        sub_agents=_build_remote_a2a_subagents(config),
    )

    adk_app = App(
        name=config.app_name,
        root_agent=coordinator,
        plugins=[harness_plugin],
        events_compaction_config=EventsCompactionConfig(
            token_threshold=config.compaction_token_threshold,
            event_retention_size=config.compaction_event_retention_size,
        ),
        context_cache_config=ContextCacheConfig(
            cache_intervals=10,
            ttl_seconds=config.context_cache_ttl_seconds,
            min_tokens=4096,
        ),
        resumability_config=ResumabilityConfig(is_resumable=True),
    )
    return coordinator, adk_app, harness_plugin


root_agent, app, harness_plugin = create_trading_agent_and_app()
