"""Deterministic PII, SPII & Secret Scrubbing Pipeline for Logs, Traces & Memory.

Enforces defense-in-depth data minimization across:
1. Structured JSON logs & Python `logging.Filter` (`PiiRedactingLogFilter`)
2. OpenTelemetry distributed trace span attributes (`PiiScrubbingSpanProcessor`)
3. ADKv2 `Session` state & `Event` payloads prior to SQLite / Vertex AI persistence
4. ADKv2 `MemoryService` (`VertexAiMemoryBankService` / `InMemoryMemoryService`) ingestion & recall

Includes an optional Google Cloud Sensitive Data Protection (Cloud SDP / DLP)
`content.deidentify` pass layered above the zero-dependency deterministic floor.
"""

from __future__ import annotations

import copy
import hashlib
import logging
import re
from collections.abc import Mapping
from typing import Any

from .config import DEFAULT_CONFIG

logger = logging.getLogger("alphapulse.pii")

# Dictionary keys whose values must never be persisted or logged in cleartext
SENSITIVE_FIELD_NAMES: frozenset[str] = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "api_key",
        "apikey",
        "access_token",
        "refresh_token",
        "id_token",
        "authorization",
        "cookie",
        "set_cookie",
        "ssn",
        "tax_id",
        "social_security",
        "credit_card",
        "card_number",
        "cvv",
        "cvc",
        "iban",
        "routing_number",
        "bank_account",
        "account_number",
        "private_key",
        "client_secret",
        "email",
        "user_email",
        "phone",
        "phone_number",
        "street_address",
        "physical_address",
        "dob",
        "date_of_birth",
    }
)

# User-associated pseudonymous identifier keys hashed before logging/tracing
PSEUDONYMOUS_ID_KEYS: frozenset[str] = frozenset(
    {
        "user_id",
        "trader_id",
        "end_user_id",
        "gaia_id",
    }
)

_BEARER_TOKEN_RE = re.compile(r"\b(Bearer)\s+[A-Za-z0-9._~+/=-]{12,}", re.IGNORECASE)
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
_API_KEY_RE = re.compile(
    r"\b(?:AIza[0-9A-Za-z_-]{20,}|ya29\.[0-9A-Za-z._-]+|sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,})\b"
)
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_SSN_RE = re.compile(r"\b(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b")
_GROUPED_CARD_RE = re.compile(r"\b(?:\d{4}[-\s]){3}\d{4}\b")
_CONTIGUOUS_CARD_RE = re.compile(r"\b\d{15,16}\b")
_ACCOUNT_ID_RE = re.compile(r"\b(?:ACCT|ACC|BROKER)-[A-Z0-9-]{4,24}\b|\b[A-Z]{2}\d{2}[A-Z0-9]{12,30}\b")
_PHONE_RE = re.compile(
    r"(?<!\w)(?:\+1[-.\s]?)?(?:\(\d{3}\)\s*|\b\d{3}[-.\s])\d{3}[-.\s]\d{4}\b"
)
_IPV4_RE = re.compile(
    r"\b(?!127\.0\.0\.1\b)(?!0\.0\.0\.0\b)"
    r"(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}"
    r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\b"
)


def _passes_luhn(digits: str) -> bool:
    """Validates 15/16-digit strings via Luhn checksum to avoid false positives."""
    if not digits.isdigit() or len(digits) not in {15, 16}:
        return False
    total = 0
    reverse_digits = digits[::-1]
    for idx, ch in enumerate(reverse_digits):
        n = int(ch)
        if idx % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def pseudonymize_identifier(raw_id: str, salt: str | None = None) -> str:
    """One-way salted SHA-256 pseudonymization for user/trader identifiers in telemetry."""
    cleaned = str(raw_id or "").strip()
    if not cleaned or cleaned == "unknown" or cleaned.startswith("uid_"):
        return cleaned or "unknown"
    effective_salt = salt if salt is not None else DEFAULT_CONFIG.pii_pseudonym_salt
    digest = hashlib.sha256(f"{effective_salt}:{cleaned}".encode()).hexdigest()[:12]
    return f"uid_{digest}"


def _deidentify_with_cloud_sdp(text: str) -> str:
    """Optional Google Cloud Sensitive Data Protection (DLP) de-identification pass."""
    if not (DEFAULT_CONFIG.enable_cloud_sdp and DEFAULT_CONFIG.gcp_project and text):
        return text
    try:
        import google.auth
        import httpx
        from google.auth.transport.requests import Request

        creds, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        creds.refresh(Request())
        endpoint = (
            f"https://dlp.googleapis.com/v2/projects/{DEFAULT_CONFIG.gcp_project}"
            "/content:deidentify"
        )
        info_types = [
            {"name": "EMAIL_ADDRESS"},
            {"name": "PHONE_NUMBER"},
            {"name": "US_SOCIAL_SECURITY_NUMBER"},
            {"name": "CREDIT_CARD_NUMBER"},
            {"name": "IBAN_CODE"},
            {"name": "AUTH_TOKEN"},
        ]
        resp = httpx.post(
            endpoint,
            headers={"Authorization": f"Bearer {creds.token}"},
            json={
                "item": {"value": text},
                "inspectConfig": {"infoTypes": info_types},
                "deidentifyConfig": {
                    "infoTypeTransformations": {
                        "transformations": [
                            {"primitiveTransformation": {"replaceWithInfoTypeConfig": {}}}
                        ]
                    }
                },
            },
            timeout=2.5,
        )
        if resp.status_code == 200:
            val = resp.json().get("item", {}).get("value")
            if isinstance(val, str):
                return val
    except Exception as exc:
        logger.debug("Cloud SDP API pass skipped (local floor active): %s", exc)
    return text


def scrub_pii_text(text: str, *, allow_cloud_sdp: bool = False) -> tuple[str, int]:
    """Scrubs PII, SPII, and credentials from free text.

    Returns:
        Tuple of `(redacted_text, redaction_count)`.
    """
    if not text or not DEFAULT_CONFIG.enable_pii_scrubbing:
        return text, 0

    redactions = 0
    out = text

    out, n = _BEARER_TOKEN_RE.subn(r"\1 [REDACTED_SECRET]", out)
    redactions += n

    out, n = _JWT_RE.subn("[REDACTED_SECRET]", out)
    redactions += n

    out, n = _API_KEY_RE.subn("[REDACTED_SECRET]", out)
    redactions += n

    out, n = _SSN_RE.subn("[REDACTED_SSN]", out)
    redactions += n

    out, n = _GROUPED_CARD_RE.subn("[REDACTED_CARD]", out)
    redactions += n

    def _replace_luhn_card(match: re.Match[str]) -> str:
        nonlocal redactions
        candidate = match.group(0)
        if _passes_luhn(candidate):
            redactions += 1
            return "[REDACTED_CARD]"
        return candidate

    out = _CONTIGUOUS_CARD_RE.sub(_replace_luhn_card, out)

    out, n = _ACCOUNT_ID_RE.subn("[REDACTED_ACCOUNT_ID]", out)
    redactions += n

    out, n = _EMAIL_RE.subn("[REDACTED_EMAIL]", out)
    redactions += n

    out, n = _PHONE_RE.subn("[REDACTED_PHONE]", out)
    redactions += n

    out, n = _IPV4_RE.subn("[REDACTED_IP]", out)
    redactions += n

    if allow_cloud_sdp and DEFAULT_CONFIG.enable_cloud_sdp:
        sdp_out = _deidentify_with_cloud_sdp(out)
        if sdp_out != out:
            redactions += 1
            out = sdp_out

    return out, redactions


def _normalize_key(key: str) -> str:
    bare = key.split(":", 1)[-1]
    return re.sub(r"[^a-z0-9]+", "_", bare.strip().lower()).strip("_")


def scrub_pii_payload(
    payload: Any,
    *,
    pseudonymize_ids: bool = False,
    allow_cloud_sdp: bool = False,
) -> tuple[Any, int]:
    """Recursively scrubs PII/SPII and sensitive keys across dicts, lists, and strings."""
    if not DEFAULT_CONFIG.enable_pii_scrubbing or payload is None:
        return payload, 0

    if isinstance(payload, (bool, int, float)):
        return payload, 0

    if isinstance(payload, str):
        return scrub_pii_text(payload, allow_cloud_sdp=allow_cloud_sdp)

    if isinstance(payload, Mapping):
        cleaned_dict: dict[str, Any] = {}
        total_redactions = 0
        for raw_k, raw_v in payload.items():
            key_str = str(raw_k)
            norm_k = _normalize_key(key_str)
            if norm_k in SENSITIVE_FIELD_NAMES:
                if raw_v not in (None, "", "[REDACTED]"):
                    cleaned_dict[key_str] = f"[REDACTED_{norm_k.upper()}]"
                    total_redactions += 1
                else:
                    cleaned_dict[key_str] = raw_v
                continue
            if pseudonymize_ids and norm_k in PSEUDONYMOUS_ID_KEYS and isinstance(raw_v, str):
                cleaned_dict[key_str] = pseudonymize_identifier(raw_v)
                continue
            sub_val, sub_count = scrub_pii_payload(
                raw_v,
                pseudonymize_ids=pseudonymize_ids,
                allow_cloud_sdp=allow_cloud_sdp,
            )
            cleaned_dict[key_str] = sub_val
            total_redactions += sub_count
        return cleaned_dict, total_redactions

    if isinstance(payload, list):
        cleaned_list: list[Any] = []
        total_redactions = 0
        for item in payload:
            sub_val, sub_count = scrub_pii_payload(
                item,
                pseudonymize_ids=pseudonymize_ids,
                allow_cloud_sdp=allow_cloud_sdp,
            )
            cleaned_list.append(sub_val)
            total_redactions += sub_count
        return cleaned_list, total_redactions

    if isinstance(payload, tuple):
        cleaned_items: list[Any] = []
        total_redactions = 0
        for item in payload:
            sub_val, sub_count = scrub_pii_payload(
                item,
                pseudonymize_ids=pseudonymize_ids,
                allow_cloud_sdp=allow_cloud_sdp,
            )
            cleaned_items.append(sub_val)
            total_redactions += sub_count
        return tuple(cleaned_items), total_redactions

    return payload, 0


def sanitize_event_for_storage(event: Any) -> tuple[Any, int]:
    """Deep-copies and scrubs an ADKv2 `Event` before session or memory persistence."""
    if event is None or not DEFAULT_CONFIG.enable_pii_scrubbing:
        return event, 0

    clone_fn = getattr(event, "model_copy", None)
    ev = clone_fn(deep=True) if callable(clone_fn) else copy.deepcopy(event)
    total_redactions = 0

    content = getattr(ev, "content", None)
    parts = getattr(content, "parts", None) if content is not None else None
    if parts:
        for part in parts:
            txt = getattr(part, "text", None)
            if isinstance(txt, str) and txt:
                clean_txt, c = scrub_pii_text(txt, allow_cloud_sdp=True)
                if c > 0:
                    part.text = clean_txt
                    total_redactions += c

            fn_call = getattr(part, "function_call", None)
            if fn_call is not None and getattr(fn_call, "args", None):
                clean_args, c = scrub_pii_payload(fn_call.args, allow_cloud_sdp=True)
                if c > 0:
                    fn_call.args = clean_args
                    total_redactions += c

            fn_resp = getattr(part, "function_response", None)
            if fn_resp is not None and getattr(fn_resp, "response", None):
                clean_resp, c = scrub_pii_payload(fn_resp.response, allow_cloud_sdp=True)
                if c > 0:
                    fn_resp.response = clean_resp
                    total_redactions += c

    actions = getattr(ev, "actions", None)
    if actions is not None:
        state_delta = getattr(actions, "state_delta", None)
        if isinstance(state_delta, dict) and state_delta:
            clean_delta, c = scrub_pii_payload(state_delta, allow_cloud_sdp=True)
            if c > 0:
                actions.state_delta = clean_delta
                total_redactions += c

    return ev, total_redactions


def sanitize_session_for_memory(session: Any) -> tuple[Any, int]:
    """Produces a PII-scrubbed copy of an ADKv2 `Session` before Memory Bank ingestion."""
    if session is None or not DEFAULT_CONFIG.enable_pii_scrubbing:
        return session, 0

    clone_fn = getattr(session, "model_copy", None)
    sanitized = clone_fn(deep=True) if callable(clone_fn) else copy.deepcopy(session)
    total_redactions = 0

    state = getattr(sanitized, "state", None)
    if isinstance(state, dict) and state:
        clean_state, c = scrub_pii_payload(state, allow_cloud_sdp=True)
        total_redactions += c
        sanitized.state = clean_state
        # Also scrub live mutable session state in place so PII never lingers in memory
        live_state = getattr(session, "state", None)
        if isinstance(live_state, dict) and c > 0:
            live_state.clear()
            live_state.update(clean_state)

    events = getattr(sanitized, "events", None)
    if isinstance(events, list) and events:
        cleaned_events = []
        for ev in events:
            clean_ev, c = sanitize_event_for_storage(ev)
            cleaned_events.append(clean_ev)
            total_redactions += c
        sanitized.events = cleaned_events

    return sanitized, total_redactions


class PiiRedactingLogFilter(logging.Filter):
    """Python `logging.Filter` scrubbing PII/SPII and secrets before log emission."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not DEFAULT_CONFIG.enable_pii_scrubbing:
            return True
        if isinstance(record.msg, str):
            record.msg, _ = scrub_pii_text(record.msg)
        if record.args:
            if isinstance(record.args, Mapping):
                scrubbed_args, _ = scrub_pii_payload(record.args, pseudonymize_ids=True)
                record.args = scrubbed_args
            elif isinstance(record.args, tuple):
                scrubbed_tuple, _ = scrub_pii_payload(record.args, pseudonymize_ids=True)
                record.args = scrubbed_tuple
        return True
