"""Centralized, typed configuration for the AlphaPulse ADKv2 & GEAP Trading System."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_csv_set(value: str | None, default: tuple[str, ...]) -> frozenset[str]:
    if not value:
        return frozenset(default)
    return frozenset(item.strip().upper() for item in value.split(",") if item.strip())


@dataclass(frozen=True)
class TradingDeskConfig:
    """Immutable runtime, protocol, identity, and risk-governance configuration."""

    app_name: str = "alphapulse_trading_desk"
    coordinator_model: str = "gemini-3.8-flash"
    analyst_model: str = "gemini-3.8-flash"
    risk_critic_model: str = "gemini-3.8-flash"
    live_model: str = "gemini-3.8-live"

    # Session & Memory Persistence ("in_memory", "sqlite", or "vertex_ai")
    session_backend: str = "in_memory"
    memory_backend: str = "in_memory"
    session_db_url: str = "sqlite+aiosqlite:///./.adk/alphapulse_sessions.db"
    gcp_project: str = ""
    gcp_location: str = "global"
    gcp_infra_region: str = "us-central1"
    agent_engine_id: str = ""

    # MCP Toolkit & OAuth 2.1 / Agent Identity Configuration
    mcp_server_url: str = "http://127.0.0.1:8080/mcp"
    mcp_transport: str = "in_process"  # "in_process", "streamable_http", or "sse"
    oauth_issuer: str = "https://auth.alphapulse.local"
    oauth_audience: str = "alphapulse-mcp-toolkit"
    oauth_client_id: str = "alphapulse-trader-web"
    dev_jwt_secret: str = "alphapulse-local-dev-hmac-secret-change-in-prod"

    # Federated A2A & GEAP Model Armor
    macro_a2a_url: str = "http://127.0.0.1:8080/a2a/macro"
    model_armor_template: str = ""

    # Deterministic Risk & Settlement Governance
    trading_mode: str = "PAPER"
    default_starting_cash_usd: float = 250_000.0
    max_single_order_usd: float = 50_000.0
    max_position_weight_pct: float = 25.0
    max_portfolio_var_95_pct: float = 3.5
    restricted_tickers: frozenset[str] = field(
        default_factory=lambda: frozenset({"MEME", "PUMP", "FRAUD"})
    )

    # Observability, Context Compaction & Loop Caps
    enable_cloud_trace: bool = False
    otel_service_name: str = "alphapulse-trading-agent"
    compaction_token_threshold: int = 32_000
    compaction_event_retention_size: int = 10
    context_cache_ttl_seconds: int = 1800
    max_risk_loop_iterations: int = 3

    @classmethod
    def from_env(cls) -> TradingDeskConfig:
        """Loads configuration from environment variables with safe defaults."""
        return cls(
            app_name=os.getenv("ALPHAPULSE_APP_NAME", "alphapulse_trading_desk"),
            coordinator_model=os.getenv("ALPHAPULSE_COORDINATOR_MODEL", "gemini-3.8-flash"),
            analyst_model=os.getenv("ALPHAPULSE_ANALYST_MODEL", "gemini-3.8-flash"),
            risk_critic_model=os.getenv("ALPHAPULSE_RISK_CRITIC_MODEL", "gemini-3.8-flash"),
            live_model=os.getenv("ALPHAPULSE_LIVE_MODEL", "gemini-3.8-live"),
            session_backend=os.getenv("ALPHAPULSE_SESSION_BACKEND", "in_memory").lower(),
            memory_backend=os.getenv("ALPHAPULSE_MEMORY_BACKEND", "in_memory").lower(),
            session_db_url=os.getenv(
                "ALPHAPULSE_SESSION_DB_URL",
                "sqlite+aiosqlite:///./.adk/alphapulse_sessions.db",
            ),
            gcp_project=os.getenv("GOOGLE_CLOUD_PROJECT", ""),
            gcp_location=os.getenv("GOOGLE_CLOUD_LOCATION", "global"),
            gcp_infra_region=os.getenv("ALPHAPULSE_INFRA_REGION", "us-central1"),
            agent_engine_id=os.getenv("VERTEX_AI_AGENT_ENGINE_ID", ""),
            mcp_server_url=os.getenv("ALPHAPULSE_MCP_SERVER_URL", "http://127.0.0.1:8080/mcp"),
            mcp_transport=os.getenv("ALPHAPULSE_MCP_TRANSPORT", "in_process").lower(),
            oauth_issuer=os.getenv("ALPHAPULSE_OAUTH_ISSUER", "https://auth.alphapulse.local"),
            oauth_audience=os.getenv("ALPHAPULSE_OAUTH_AUDIENCE", "alphapulse-mcp-toolkit"),
            oauth_client_id=os.getenv("ALPHAPULSE_OAUTH_CLIENT_ID", "alphapulse-trader-web"),
            dev_jwt_secret=os.getenv(
                "ALPHAPULSE_DEV_JWT_SECRET",
                "alphapulse-local-dev-hmac-secret-change-in-prod",
            ),
            macro_a2a_url=os.getenv(
                "ALPHAPULSE_MACRO_A2A_URL",
                "http://127.0.0.1:8080/a2a/macro",
            ),
            model_armor_template=os.getenv("ALPHAPULSE_MODEL_ARMOR_TEMPLATE", ""),
            trading_mode=os.getenv("ALPHAPULSE_TRADING_MODE", "PAPER").upper(),
            default_starting_cash_usd=float(
                os.getenv("ALPHAPULSE_DEFAULT_STARTING_CASH_USD", "250000.0")
            ),
            max_single_order_usd=float(
                os.getenv("ALPHAPULSE_MAX_SINGLE_ORDER_USD", "50000.0")
            ),
            max_position_weight_pct=float(
                os.getenv("ALPHAPULSE_MAX_POSITION_WEIGHT_PCT", "25.0")
            ),
            max_portfolio_var_95_pct=float(
                os.getenv("ALPHAPULSE_MAX_PORTFOLIO_VAR_95_PCT", "3.5")
            ),
            restricted_tickers=_parse_csv_set(
                os.getenv("ALPHAPULSE_RESTRICTED_TICKERS"),
                ("MEME", "PUMP", "FRAUD"),
            ),
            enable_cloud_trace=_parse_bool(
                os.getenv("ALPHAPULSE_ENABLE_CLOUD_TRACE"), default=False
            ),
            otel_service_name=os.getenv("OTEL_SERVICE_NAME", "alphapulse-trading-agent"),
        )


DEFAULT_CONFIG = TradingDeskConfig.from_env()
