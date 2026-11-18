"""Protocol integration exports (AP2, A2UI, A2A, OAuth 2.1 & Agent Identity)."""

from .a2a_integration import (
    build_agent_card_dict,
    build_macro_specialist_card_dict,
    handle_macro_a2a_rpc,
)
from .a2ui_surfaces import (
    A2UI_EXTENSION_URI_V09,
    A2UI_MIME_TYPE,
    build_ap2_approval_a2ui_messages,
    build_market_pulse_a2ui_messages,
    emit_a2ui_trading_surface,
)
from .ap2_mandates import (
    AP2_EXTENSION_URI,
    CartMandate,
    CheckoutMandate,
    IntentMandate,
    PaymentMandate,
    create_broker_cart_mandate,
    issue_ap2_cart_mandate,
    settle_ap2_paper_trade,
    sign_user_ap2_mandates,
    verify_ap2_double_lock,
)
from .oauth_config import (
    REQUIRED_SCOPES_BY_OPERATION,
    agent_identity_header_provider,
    build_adk_oauth2_auth_config,
    get_oauth_protected_resource_metadata,
    mint_oauth_jwt,
    verify_oauth_jwt,
)

__all__ = [
    "A2UI_EXTENSION_URI_V09",
    "A2UI_MIME_TYPE",
    "AP2_EXTENSION_URI",
    "CartMandate",
    "CheckoutMandate",
    "IntentMandate",
    "PaymentMandate",
    "REQUIRED_SCOPES_BY_OPERATION",
    "agent_identity_header_provider",
    "build_adk_oauth2_auth_config",
    "build_agent_card_dict",
    "build_ap2_approval_a2ui_messages",
    "build_macro_specialist_card_dict",
    "build_market_pulse_a2ui_messages",
    "create_broker_cart_mandate",
    "emit_a2ui_trading_surface",
    "get_oauth_protected_resource_metadata",
    "handle_macro_a2a_rpc",
    "issue_ap2_cart_mandate",
    "mint_oauth_jwt",
    "settle_ap2_paper_trade",
    "sign_user_ap2_mandates",
    "verify_ap2_double_lock",
    "verify_oauth_jwt",
]
