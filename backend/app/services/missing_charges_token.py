"""Signed-token utilities for the provider self-service portal.

A provider clicks a link from their weekly email and lands on the
portal — no login. The token carries the provider's display name; we
verify the signature on every request.

Tokens are signed JWTs with a 14-day expiry (so a single token covers
~2 weekly emails before needing reissue). The signing secret comes from
MISSING_CHARGES_TOKEN_SECRET, else the app's main SECRET_KEY (settings.secret_key)
— the SAME secret the rest of the app signs JWTs with. There is no hardcoded
fallback: a token signed with a guessable secret would let anyone forge a
provider link and read patient PHI on the public portal. In prod (Cloud Run,
K_SERVICE set) we warn loudly when the dedicated secret is unset and we fall
back to the shared SECRET_KEY — but we do NOT hard-fail, because the secret
is mounted AFTER this code deploys (per the rollout order) and a hard fail
would break minting in the interim.

Revocation: tokens embed a per-provider `ptv` (provider token version) read
from ProviderUserMapping.token_version. verify_provider_token() rejects a
token whose ptv is below the provider's current stored version, so bumping
the version (manual revoke, or auto-bump on offboarding) kills outstanding
links. A pre-change token has no `ptv` → treated as 0 → valid until expiry.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from app.utils.dt import now_utc_naive
from typing import Optional

import jwt   # pyjwt

from app.config import settings

log = logging.getLogger(__name__)

TOKEN_TTL_DAYS = 14
ALGORITHM = "HS256"
ISSUER = "wwc-billing"
KIND = "missing_charges_provider"


def _secret() -> str:
    env_secret = os.environ.get("MISSING_CHARGES_TOKEN_SECRET")
    if env_secret:
        return env_secret
    # Soft guard: in prod (Cloud Run sets K_SERVICE) warn that we're signing
    # provider tokens with the shared SECRET_KEY. Do NOT hard-fail — the
    # dedicated secret is mounted AFTER this code deploys (rollout order), so
    # a hard fail would break minting in the interim.
    if os.environ.get("K_SERVICE"):
        log.warning(
            "MISSING_CHARGES_TOKEN_SECRET is unset in prod; falling back to "
            "the shared SECRET_KEY for provider-token signing. Mount the "
            "dedicated secret to restore key separation.")
    return settings.secret_key


def mint_token(provider: str, *, ttl_days: int = TOKEN_TTL_DAYS,
               token_version: int = 0) -> str:
    """Mint a self-service token for the named provider.

    Pure / DB-free: callers that want the provider's current token_version
    embedded should use mint_token_for_provider(). `token_version` lands in
    the JWT as `ptv` so verify_provider_token() can reject stale links.
    """
    if not provider or not provider.strip():
        raise ValueError("provider is required")
    now = now_utc_naive()
    payload = {
        "provider": provider.strip(),
        "iss": ISSUER,
        "kind": KIND,
        "ptv": int(token_version),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=ttl_days)).timestamp()),
    }
    return jwt.encode(payload, _secret(), algorithm=ALGORITHM)


def _provider_token_version(db, provider: str) -> int:
    """Current token_version for a provider (0 if no mapping row exists).

    Deliberately does NOT auto-create a mapping — creating an empty
    (no-email) row would make _provider_user() treat the provider as a
    'no match' and silently drop them from the weekly email.
    """
    from app.models.missing_charge import ProviderUserMapping
    m = (db.query(ProviderUserMapping)
           .filter(ProviderUserMapping.provider_name == provider)
           .first())
    return int(m.token_version) if m and m.token_version is not None else 0


def mint_token_for_provider(db, provider: str, *,
                            ttl_days: int = TOKEN_TTL_DAYS) -> str:
    """Mint a token embedding the provider's current stored token_version."""
    ver = _provider_token_version(db, provider)
    return mint_token(provider, ttl_days=ttl_days, token_version=ver)


def verify_provider_token(db, token: str) -> Optional[dict]:
    """Decode + revocation-check a provider token.

    Returns the payload on success, None if the signature/kind/exp is bad
    OR the token's `ptv` is below the provider's current stored
    token_version (revoked/stale). A pre-change token has no `ptv` → 0 →
    valid until it expires.
    """
    payload = decode_token(token)
    if payload is None:
        return None
    stored = _provider_token_version(db, payload["provider"])
    if int(payload.get("ptv", 0)) < stored:
        return None
    return payload


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
