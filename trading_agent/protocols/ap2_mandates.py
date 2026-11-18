"""AP2 v0.2 (Agent Payments Protocol) cryptographic mandates for verifiable trade settlement.

Implements the AP2 double-lock authorization pattern:
1. `IntentMandate`: Declares bounded trading constraints (symbol, max notional, slippage, expiry).
2. `CartMandate`: Broker-signed price-locked quote (`merchant_authorization` over `cart_digest`).
3. `CheckoutMandate`: User/Desk-signed approval over the broker's `cart_digest`.
4. `PaymentMandate`: User/Desk-signed settlement authorization releasing paper capital.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from ..config import DEFAULT_CONFIG
from ..tools.execution import execute_paper_trade
from ..tools.risk_engine import evaluate_pretrade_risk

if TYPE_CHECKING:
    from google.adk.tools import ToolContext

AP2_EXTENSION_URI = "https://ap2-protocol.org/specification/v0.2"


def _canonical_digest(payload: dict[str, Any]) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def _hmac_sign(message: str, role_salt: str) -> str:
    key = f"{DEFAULT_CONFIG.dev_jwt_secret}::{role_salt}".encode()
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).hexdigest()


class IntentMandate(BaseModel):
    """AP2 IntentMandate capturing trader constraints for human-present or autonomous orders."""

    symbol: str
    side: str
    max_notional_usd: float
    ap2_version: str = "0.2"
    mandate_id: str = Field(default_factory=lambda: f"intent-{uuid.uuid4().hex[:10]}")
    max_slippage_bps: float = 5.0
    human_present: bool = True
    issued_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    expires_at: str = Field(
        default_factory=lambda: (datetime.now(UTC) + timedelta(minutes=15)).isoformat()
    )


class CartMandate(BaseModel):
    """AP2 CartMandate locking execution terms with a broker cryptographic signature."""

    cart_id: str
    intent_mandate_id: str
    symbol: str
    side: str
    quantity: int
    locked_limit_price: float
    total_notional_usd: float
    expires_at: str
    cart_digest: str
    merchant_authorization: str
    ap2_version: str = "0.2"


class CheckoutMandate(BaseModel):
    """AP2 CheckoutMandate signed by the trader attesting to the exact broker CartMandate."""

    checkout_id: str
    cart_id: str
    cart_digest: str
    trader_principal: str
    signed_at: str
    user_signature: str
    ap2_version: str = "0.2"


class PaymentMandate(BaseModel):
    """AP2 PaymentMandate authorizing settlement funds against the verified `cart_digest`."""

    payment_mandate_id: str
    cart_id: str
    cart_digest: str
    authorized_amount_usd: float
    trader_principal: str
    signed_at: str
    user_signature: str
    currency: str = "USD"
    payment_rail: str = "PAPER_LEDGER_DPAN"
    ap2_version: str = "0.2"


def create_broker_cart_mandate(
    symbol: str,
    side: str,
    quantity: int,
    locked_limit_price: float,
    intent_mandate_id: str | None = None,
    ttl_seconds: int = 300,
) -> CartMandate:
    """Creates a broker-signed AP2 `CartMandate` locking price and notional terms."""
    clean_symbol = symbol.strip().upper()
    clean_side = side.strip().upper()
    qty = int(quantity)
    locked_px = round(float(locked_limit_price), 4)
    total_notional = round(qty * locked_px, 2)
    cart_id = f"cart-{uuid.uuid4().hex[:10]}"
    intent_id = intent_mandate_id or f"intent-{uuid.uuid4().hex[:8]}"
    expires_at = (datetime.now(UTC) + timedelta(seconds=ttl_seconds)).isoformat()

    core_fields: dict[str, Any] = {
        "ap2_version": "0.2",
        "cart_id": cart_id,
        "intent_mandate_id": intent_id,
        "symbol": clean_symbol,
        "side": clean_side,
        "quantity": qty,
        "locked_limit_price": locked_px,
        "total_notional_usd": total_notional,
        "expires_at": expires_at,
    }
    digest = _canonical_digest(core_fields)
    merchant_sig = _hmac_sign(digest, role_salt="broker_execution_venue")
    return CartMandate(
        ap2_version="0.2",
        cart_id=cart_id,
        intent_mandate_id=intent_id,
        symbol=clean_symbol,
        side=clean_side,
        quantity=qty,
        locked_limit_price=locked_px,
        total_notional_usd=total_notional,
        expires_at=expires_at,
        cart_digest=digest,
        merchant_authorization=merchant_sig,
    )


def sign_user_ap2_mandates(
    cart: CartMandate,
    trader_principal: str = "spiffe://alphapulse.local/user/trader-01",
) -> tuple[CheckoutMandate, PaymentMandate]:
    """Produces user-signed `CheckoutMandate` and `PaymentMandate` bound to `cart.cart_digest`."""
    now_iso = datetime.now(UTC).isoformat()
    checkout_id = f"chk-{uuid.uuid4().hex[:10]}"
    payment_id = f"pay-{uuid.uuid4().hex[:10]}"

    checkout_msg = f"{checkout_id}:{cart.cart_id}:{cart.cart_digest}:{trader_principal}"
    checkout_sig = _hmac_sign(checkout_msg, role_salt=f"user::{trader_principal}")
    checkout = CheckoutMandate(
        checkout_id=checkout_id,
        cart_id=cart.cart_id,
        cart_digest=cart.cart_digest,
        trader_principal=trader_principal,
        signed_at=now_iso,
        user_signature=checkout_sig,
    )

    payment_msg = (
        f"{payment_id}:{cart.cart_id}:{cart.cart_digest}:"
        f"{cart.total_notional_usd:.2f}:USD:{trader_principal}"
    )
    payment_sig = _hmac_sign(payment_msg, role_salt=f"user::{trader_principal}")
    payment = PaymentMandate(
        payment_mandate_id=payment_id,
        cart_id=cart.cart_id,
        cart_digest=cart.cart_digest,
        authorized_amount_usd=cart.total_notional_usd,
        trader_principal=trader_principal,
        signed_at=now_iso,
        user_signature=payment_sig,
    )
    return checkout, payment


def verify_ap2_double_lock(
    cart: CartMandate,
    checkout: CheckoutMandate,
    payment: PaymentMandate,
) -> tuple[bool, str]:
    """Cryptographically verifies broker price-lock AND trader authorization signatures."""
    recomputed_digest = _canonical_digest(
        {
            "ap2_version": cart.ap2_version,
            "cart_id": cart.cart_id,
            "intent_mandate_id": cart.intent_mandate_id,
            "symbol": cart.symbol,
            "side": cart.side,
            "quantity": cart.quantity,
            "locked_limit_price": round(cart.locked_limit_price, 4),
            "total_notional_usd": cart.total_notional_usd,
            "expires_at": cart.expires_at,
        }
    )
    if not hmac.compare_digest(recomputed_digest, cart.cart_digest):
        return False, "CartMandate fields were tampered after digest computation."

    expected_merchant_sig = _hmac_sign(cart.cart_digest, role_salt="broker_execution_venue")
    if not hmac.compare_digest(expected_merchant_sig, cart.merchant_authorization):
        return False, "Invalid broker merchant_authorization signature on CartMandate."

    expiry = datetime.fromisoformat(cart.expires_at)
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=UTC)
    if datetime.now(UTC) > expiry:
        return False, "CartMandate price lock has expired."

    if checkout.cart_digest != cart.cart_digest or payment.cart_digest != cart.cart_digest:
        return False, "Mandate digest mismatch between CartMandate and user mandates."

    expected_checkout_sig = _hmac_sign(
        f"{checkout.checkout_id}:{cart.cart_id}:{cart.cart_digest}:{checkout.trader_principal}",
        role_salt=f"user::{checkout.trader_principal}",
    )
    if not hmac.compare_digest(expected_checkout_sig, checkout.user_signature):
        return False, "Invalid user_signature on CheckoutMandate."

    expected_payment_sig = _hmac_sign(
        f"{payment.payment_mandate_id}:{cart.cart_id}:{cart.cart_digest}:"
        f"{payment.authorized_amount_usd:.2f}:{payment.currency}:{payment.trader_principal}",
        role_salt=f"user::{payment.trader_principal}",
    )
    if not hmac.compare_digest(expected_payment_sig, payment.user_signature):
        return False, "Invalid user_signature on PaymentMandate."

    if payment.authorized_amount_usd < cart.total_notional_usd:
        return (
            False,
            f"PaymentMandate authorized amount (${payment.authorized_amount_usd:,.2f}) "
            f"is less than CartMandate total (${cart.total_notional_usd:,.2f}).",
        )

    return True, "AP2 double-lock verification succeeded."


def issue_ap2_cart_mandate(
    symbol: str,
    side: str,
    quantity: int,
    limit_price: float,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Issues a risk-checked, broker-signed AP2 `CartMandate` locking quote terms.

    Args:
        symbol: Ticker symbol ('GOOGL', 'NVDA', 'MSFT', 'SPY', etc.).
        side: Trade side ('BUY' or 'SELL').
        quantity: Share quantity (> 0).
        limit_price: Locked execution limit price in USD.
        tool_context: Optional ADK ToolContext used to store `ap2_cart_mandate` in state.

    Returns:
        Dictionary containing the signed `CartMandate` or pre-trade risk rejection.
    """
    risk = evaluate_pretrade_risk(
        symbol=symbol,
        side=side,
        quantity=quantity,
        limit_price=limit_price,
        tool_context=tool_context,
    )
    if risk.get("verdict") != "APPROVED":
        return {
            "status": "rejected_by_risk_engine",
            "risk_assessment": risk,
        }

    intent = IntentMandate(
        symbol=symbol.strip().upper(),
        side=side.strip().upper(),
        max_notional_usd=float(risk["order_notional_usd"]),
    )
    cart = create_broker_cart_mandate(
        symbol=symbol,
        side=side,
        quantity=quantity,
        locked_limit_price=limit_price,
        intent_mandate_id=intent.mandate_id,
    )
    cart_dict = cart.model_dump()
    if tool_context is not None:
        tool_context.state["ap2_intent_mandate"] = intent.model_dump()
        tool_context.state["ap2_cart_mandate"] = cart_dict

    return {
        "status": "success",
        "protocol": "AP2/v0.2",
        "intent_mandate": intent.model_dump(),
        "cart_mandate": cart_dict,
    }


def settle_ap2_paper_trade(
    cart_mandate: dict[str, Any],
    checkout_mandate: dict[str, Any],
    payment_mandate: dict[str, Any],
    order_type: str = "TWAP",
    idempotency_key: str | None = None,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Verifies AP2 double-lock cryptographic signatures and settles an idempotent paper trade.

    Args:
        cart_mandate: Broker-signed AP2 CartMandate dictionary.
        checkout_mandate: User-signed AP2 CheckoutMandate dictionary.
        payment_mandate: User-signed AP2 PaymentMandate dictionary.
        order_type: Execution algorithm ('LIMIT', 'MARKET', or 'TWAP').
        idempotency_key: Optional client deduplication key; defaults to `payment_mandate_id`.
        tool_context: Optional ADK ToolContext to persist mandates and portfolio fills.

    Returns:
        Verified settlement receipt or cryptographic verification failure.
    """
    try:
        cart = CartMandate.model_validate(cart_mandate)
        checkout = CheckoutMandate.model_validate(checkout_mandate)
        payment = PaymentMandate.model_validate(payment_mandate)
    except Exception as exc:
        return {"status": "error", "error": f"Malformed AP2 mandate payload: {exc}"}

    valid, message = verify_ap2_double_lock(cart, checkout, payment)
    if not valid:
        return {
            "status": "ap2_signature_rejected",
            "reason": message,
        }

    if tool_context is not None:
        tool_context.state["ap2_checkout_mandate"] = checkout.model_dump()
        tool_context.state["ap2_payment_mandate"] = payment.model_dump()

    dedup_key = idempotency_key or payment.payment_mandate_id
    execution_result = execute_paper_trade(
        symbol=cart.symbol,
        side=cart.side,
        quantity=cart.quantity,
        order_type=order_type,
        limit_price=cart.locked_limit_price,
        strategy_tag="ap2_verified_settlement",
        idempotency_key=dedup_key,
        ap2_payment_mandate_id=payment.payment_mandate_id,
        tool_context=tool_context,
    )
    execution_result["ap2_verification"] = {
        "verified": True,
        "message": message,
        "cart_digest": cart.cart_digest,
        "payment_mandate_id": payment.payment_mandate_id,
    }
    return execution_result
