"""OAuth 2.1 / GCP Agent Identity Resource Server Guard for the MCP Toolkit."""

from __future__ import annotations

from typing import Any

from trading_agent.protocols.oauth_config import (
    REQUIRED_SCOPES_BY_OPERATION,
    verify_oauth_jwt,
)


class McpAuthorizationError(PermissionError):
    """Raised when an MCP tool call lacks a valid Bearer token or required scope."""


def authorize_mcp_tool_call(
    tool_name: str,
    authorization_header: str | None,
    *,
    enforce_auth: bool = True,
) -> dict[str, Any]:
    """Validates `Authorization: Bearer <jwt>` and tool-specific OAuth 2.1 scopes."""
    required_scope = REQUIRED_SCOPES_BY_OPERATION.get(tool_name, "trading:read")
    if not enforce_auth:
        return {"sub": "local-bypass", "scope": required_scope}

    if not authorization_header or not authorization_header.lower().startswith("bearer "):
        raise McpAuthorizationError(
            f"Missing Bearer token for MCP tool '{tool_name}' (requires scope '{required_scope}')."
        )

    raw_token = authorization_header.split(" ", 1)[1].strip()
    valid, claims_or_err = verify_oauth_jwt(raw_token, required_scope=required_scope)
    if not valid:
        raise McpAuthorizationError(str(claims_or_err))
    assert isinstance(claims_or_err, dict)
    return claims_or_err
