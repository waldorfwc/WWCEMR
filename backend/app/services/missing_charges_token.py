"""Signed-token utilities for the provider self-service portal.

A provider clicks a link from their weekly email and lands on the
portal — no login. The token carries the provider's display name; we
verify the signature on every request.

Tokens are signed JWTs with a 60-day expiry (so a single token covers
~8 weekly emails before needing reissue). The signing secret comes from
MISSING_CHARGES_TOKEN_SECRET (or falls back to APP_SECRET / dev-only).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from app.utils.dt import now_utc_naive
from typing import Optional

import jwt   # pyjwt

log = logging.getLogger(__name__)

TOKEN_TTL_DAYS = 60
ALGORITHM = "HS256"
ISSUER = "wwc-billing"
KIND = "missing_charges_provider"


def _secret() -> str:
    s = (os.environ.get("MISSING_CHARGES_TOKEN_SECRET")
         or os.environ.get("APP_SECRET")
         or "dev-only-do-not-use-in-production")
    return s


def mint_token(provider: str, *, ttl_days: int = TOKEN_TTL_DAYS) -> str:
    """Mint a self-service token for the named provider."""
    if not provider or not provider.strip():
        raise ValueError("provider is required")
    now = now_utc_naive()
    payload = {
        "provider": provider.strip(),
        "iss": ISSUER,
        "kind": KIND,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=ttl_days)).timestamp()),
    }
    return jwt.encode(payload, _secret(), algorithm=ALGORITHM)


def decode_token(token: str) -> Optional[dict]:
    """Validate + decode a provider token. Returns the payload dict on
    success, or None on any failure (expired, bad signature, wrong kind)."""
    if not token:
        return None
    try:
        payload = jwt.decode(token, _secret(), algorithms=[ALGORITHM],
                              issuer=ISSUER,
                              leeway=10,   # tolerate small clock skew
                              options={"verify_iat": False})
    except jwt.InvalidTokenError as e:
        log.info("missing-charges token decode failed: %s", e)
        return None
    if payload.get("kind") != KIND:
        return None
    if not payload.get("provider"):
        return None
    return payload
