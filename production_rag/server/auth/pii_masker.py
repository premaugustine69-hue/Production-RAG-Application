"""PII masking utilities for logs and API responses.

Masks common PII patterns before they reach log output or are echoed back.
Patterns covered:
  - Email addresses
  - Phone numbers (international and local formats)
  - Credit card numbers (basic Luhn-shaped patterns)
  - SSN / National ID patterns
  - IPv4 addresses in logs (partial mask)
  - JWT tokens in logs (show prefix only)
"""

from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(
    r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+",
    re.IGNORECASE,
)
_PHONE_RE = re.compile(
    r"(\+?\d[\d\s\-().]{7,}\d)",
)
_CREDIT_CARD_RE = re.compile(
    r"\b(?:\d[ -]?){13,16}\b",
)
_SSN_RE = re.compile(
    r"\b\d{3}[-\s]?\d{2}[-\s]?\d{4}\b",
)
_JWT_RE = re.compile(
    r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\b",
)
_IPV4_RE = re.compile(
    r"\b(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})\b",
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def mask_text(text: str) -> str:
    """Replace PII patterns in ``text`` with masked placeholders."""
    text = _JWT_RE.sub(_mask_jwt, text)
    text = _EMAIL_RE.sub(_mask_email, text)
    text = _SSN_RE.sub("***-**-****", text)
    text = _CREDIT_CARD_RE.sub(_mask_credit_card, text)
    text = _IPV4_RE.sub(_mask_ip, text)
    # Phone last to avoid false-positives colliding with card patterns
    text = _PHONE_RE.sub(_mask_phone, text)
    return text


def mask_dict(data: dict[str, Any], keys_to_mask: set[str] | None = None) -> dict[str, Any]:
    """Recursively mask sensitive values in a dict by key name or by content scan."""
    _sensitive_keys = keys_to_mask or {
        "password",
        "hashed_password",
        "secret",
        "token",
        "api_key",
        "access_token",
        "refresh_token",
        "authorization",
        "credit_card",
        "ssn",
        "national_id",
    }
    result: dict[str, Any] = {}
    for k, v in data.items():
        if k.lower() in _sensitive_keys:
            result[k] = "***REDACTED***"
        elif isinstance(v, dict):
            result[k] = mask_dict(v, _sensitive_keys)
        elif isinstance(v, str):
            result[k] = mask_text(v)
        elif isinstance(v, list):
            result[k] = [
                mask_dict(item, _sensitive_keys) if isinstance(item, dict)
                else (mask_text(item) if isinstance(item, str) else item)
                for item in v
            ]
        else:
            result[k] = v
    return result


# ---------------------------------------------------------------------------
# Internal maskers
# ---------------------------------------------------------------------------


def _mask_email(m: re.Match) -> str:  # type: ignore[type-arg]
    parts = m.group().split("@")
    local = parts[0]
    domain = parts[1] if len(parts) > 1 else ""
    masked_local = local[0] + "***" if len(local) > 1 else "***"
    return f"{masked_local}@{domain}"


def _mask_phone(m: re.Match) -> str:  # type: ignore[type-arg]
    raw = m.group()
    digits_only = re.sub(r"\D", "", raw)
    if len(digits_only) < 7:
        return raw
    return raw[:2] + "***" + raw[-2:]


def _mask_credit_card(m: re.Match) -> str:  # type: ignore[type-arg]
    raw = m.group()
    digits = re.sub(r"\D", "", raw)
    if len(digits) < 13:
        return raw
    return "****-****-****-" + digits[-4:]


def _mask_ip(m: re.Match) -> str:  # type: ignore[type-arg]
    return f"{m.group(1)}.{m.group(2)}.***.*"


def _mask_jwt(m: re.Match) -> str:  # type: ignore[type-arg]
    token = m.group()
    parts = token.split(".")
    return parts[0] + ".***REDACTED***"
