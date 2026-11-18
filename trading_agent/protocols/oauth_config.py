"""OAuth 2.0 (3LO & 2LO) + GCP Agent Identity (SPIFFE / OIDC) helpers for ADKv2 & MCP."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import TYPE_CHECKING, Any

from ..config import DEFAULT_CONFIG

if TYPE_CHECKING:
    from google.adk.agents.readonly_context import ReadonlyContext

REQUIRED_SCOPES_BY_OPERATION: dict[str, str] = {
    "stream_realtime_market_ticks": "trading:read",
    "fetch_orderbook_microstructure": "trading:read",
    "fetch_macro_and_news_sentiment": "trading:read",
    "compute_technical_signals": "trading:read",
    "get_portfolio_state": "trading:read",
    "emit_a2ui_trading_surface": "trading:read",
    "evaluate_pretrade_risk": "trading:simulate",
    "issue_ap2_cart_mandate": "trading:simulate",
    "execute_paper_trade": "trading:execute",
    "settle_ap2_paper_trade": "trading:execute",
}


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode((segment + padding).encode("ascii"))


def mint_oauth_jwt(
    subject: str = "spiffe://gcp.agent.identity/ns/alphapulse/agent/trading-coordinator",
    scopes: list[str] | None = None,
    delegated_user: str | None = None,
    ttl_seconds: int = 900,
    audience: str | None = None,
) -> str:
    """Mints a compact HS256 OAuth 2.1 / Agent Identity token for local & CI flows.

    In GCP Agent Runtime deployments with `IdentityType.AGENT_IDENTITY`, workload-to-MCP
    calls use certificate-bound Google OIDC tokens or Agent Identity Auth Manager 3LO
    access tokens; this helper provides a deterministic RFC 7519 token for standalone
    MCP conformance tests and local development.
    """
    now = int(time.time())
    effective_scopes = scopes or ["trading:read", "trading:simulate", "trading:execute"]
    header = {"alg": "HS256", "typ": "at+jwt"}
    claims: dict[str, Any] = {
        "iss": DEFAULT_CONFIG.oauth_issuer,
        "sub": subject,
        "aud": audience or DEFAULT_CONFIG.oauth_audience,
        "iat": now,
        "exp": now + ttl_seconds,
        "scope": " ".join(effective_scopes),
        "identity_type": "AGENT_IDENTITY",
    }
    if delegated_user:
        claims["act"] = {"sub": delegated_user, "grant_type": "authorization_code_3lo"}

    signing_input = (
        f"{_b64url_encode(json.dumps(header, separators=(',', ':')).encode('utf-8'))}."
        f"{_b64url_encode(json.dumps(claims, separators=(',', ':')).encode('utf-8'))}"
    )
    sig = hmac.new(
        DEFAULT_CONFIG.dev_jwt_secret.encode("utf-8"),
        signing_input.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{signing_input}.{_b64url_encode(sig)}"


def verify_oauth_jwt(
    token: str,
    required_scope: str | None = None,
    expected_audience: str | None = None,
) -> tuple[bool, dict[str, Any] | str]:
    """Verifies signature, expiration, audience, and scope on an OAuth 2.1 / OIDC token."""
    parts = token.strip().split(".")
    if len(parts) != 3:
        return False, "Malformed JWT: expected 3 dot-separated segments."

    header_b64, payload_b64, sig_b64 = parts
    signing_input = f"{header_b64}.{payload_b64}"
    expected_sig = hmac.new(
        DEFAULT_CONFIG.dev_jwt_secret.encode("utf-8"),
        signing_input.encode("ascii"),
        hashlib.sha256,
    ).digest()

    try:
        provided_sig = _b64url_decode(sig_b64)
        claims = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
    except Exception as exc:
        return False, f"Failed to decode JWT payload: {exc}"

    if not hmac.compare_digest(expected_sig, provided_sig):
        return False, "Invalid JWT signature."

    now = int(time.time())
    if int(claims.get("exp", 0)) < now:
        return False, "Token has expired."

    target_aud = expected_audience or DEFAULT_CONFIG.oauth_audience
    if claims.get("aud") != target_aud:
        return False, f"Invalid token audience '{claims.get('aud')}'; expected '{target_aud}'."

    if required_scope:
        granted = set(str(claims.get("scope", "")).split())
        if required_scope not in granted:
            return (
                False,
                f"Insufficient scope: required '{required_scope}', granted {sorted(granted)}.",
            )

    return True, claims


def get_oauth_protected_resource_metadata(resource_url: str) -> dict[str, Any]:
    """Returns RFC 9728 OAuth 2.0 Protected Resource Metadata for the MCP server."""
    return {
        "resource": resource_url,
        "authorization_servers": [DEFAULT_CONFIG.oauth_issuer],
        "bearer_methods_supported": ["header"],
        "scopes_supported": ["trading:read", "trading:simulate", "trading:execute"],
        "agent_identity_supported": True,
        "mtls_endpoint_aliases": {"mcp": f"{resource_url}/mcp"},
    }


def agent_identity_header_provider(
    readonly_context: ReadonlyContext | None = None,
) -> dict[str, str]:
    """ADKv2 `McpToolset` `header_provider` injecting Agent Identity + 3LO user context."""
    user_id = getattr(readonly_context, "user_id", None) or "anonymous-trader"
    session_id = getattr(readonly_context, "session_id", None) or "stateless"
    token = mint_oauth_jwt(
        subject=(
            f"spiffe://gcp.agent.identity/projects/"
            f"{DEFAULT_CONFIG.gcp_project or 'local-dev'}/agents/{DEFAULT_CONFIG.app_name}"
        ),
        scopes=["trading:read", "trading:simulate", "trading:execute"],
        delegated_user=f"user:{user_id}",
    )
    return {
        "Authorization": f"Bearer {token}",
        "X-Agent-Identity-Principal": DEFAULT_CONFIG.app_name,
        "X-ADK-Session-Id": str(session_id),
    }


def build_adk_oauth2_auth_config() -> Any:
    """Constructs an ADKv2 `AuthConfig` (`OAuth2Auth`) for 3-legged OAuth trade consent."""
    from fastapi.openapi.models import OAuth2, OAuthFlowAuthorizationCode
    from google.adk.auth.auth_credential import AuthCredential, AuthCredentialTypes, OAuth2Auth
    from google.adk.auth.auth_schemes import OAuthFlows
    from google.adk.auth.auth_tool import AuthConfig

    scheme = OAuth2(
        flows=OAuthFlows(
            authorizationCode=OAuthFlowAuthorizationCode(
                authorizationUrl=f"{DEFAULT_CONFIG.oauth_issuer}/oauth2/authorize",
                tokenUrl=f"{DEFAULT_CONFIG.oauth_issuer}/oauth2/token",
                scopes={
                    "trading:read": "Read realtime quotes, L2 orderbooks, and portfolio VaR",
                    "trading:simulate": "Run pre-trade VaR checks and lock AP2 CartMandates",
                    "trading:execute": "Sign AP2 PaymentMandates and settle paper trades",
                },
            )
        )
    )
    credential = AuthCredential(
        auth_type=AuthCredentialTypes.OAUTH2,
        oauth2=OAuth2Auth(
            client_id=DEFAULT_CONFIG.oauth_client_id,
            auth_uri=f"{DEFAULT_CONFIG.oauth_issuer}/oauth2/authorize",
        ),
    )
    return AuthConfig(
        auth_scheme=scheme,
        raw_auth_credential=credential,
        credential_key="alphapulse_3lo_oauth",
    )
