"""DocuSign JWT auth client.

Signs a JWT with our private RSA key, exchanges it at
https://{auth_uri}/oauth/token for a short-lived access token, and caches
the token until ~5 minutes before expiry.

The user being impersonated (settings.docusign_user_id) must have granted
consent to the integration once via the consent URL — see
`build_consent_url()` and the /docusign/consent walkthrough.
"""
from __future__ import annotations

import time
from threading import Lock
from typing import Optional

import httpx
import jwt

from app.config import settings


_TOKEN_CACHE: dict = {"access_token": None, "expires_at": 0}
_LOCK = Lock()

# DocuSign JWT lifetime: 1 hour. Refresh ~5 min early.
_TOKEN_LIFETIME_SECONDS = 3600
_REFRESH_BUFFER_SECONDS = 300

# eSignature scopes: "signature impersonation" is required for JWT grant.
_SCOPES = "signature impersonation"


class DocuSignAuthError(Exception):
    """Raised when JWT exchange fails (most often: missing user consent)."""


def _need_token() -> bool:
    return (
        not _TOKEN_CACHE["access_token"]
        or time.time() >= _TOKEN_CACHE["expires_at"] - _REFRESH_BUFFER_SECONDS
    )


def _build_jwt() -> str:
    now = int(time.time())
    claims = {
        "iss": settings.docusign_integration_key,   # client_id
        "sub": settings.docusign_user_id,           # user being impersonated
        "aud": settings.docusign_auth_uri,          # account-d.docusign.com
        "iat": now,
        "exp": now + _TOKEN_LIFETIME_SECONDS,
        "scope": _SCOPES,
    }
    return jwt.encode(claims, settings.docusign_private_key, algorithm="RS256")


def _exchange(assertion: str) -> dict:
    url = f"https://{settings.docusign_auth_uri}/oauth/token"
    data = {
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": assertion,
    }
    with httpx.Client(timeout=30) as client:
        r = client.post(url, data=data)
    if r.status_code != 200:
        body = r.text
        # consent_required => user has not granted consent yet
        if "consent_required" in body:
            raise DocuSignAuthError(
                "DocuSign user has not granted consent. Visit the consent URL "
                "(see build_consent_url()) once in your browser to authorize."
            )
        raise DocuSignAuthError(f"DocuSign token exchange failed: {r.status_code} {body}")
    return r.json()


def get_access_token() -> str:
    """Return a valid access token, fetching/refreshing as needed."""
    if not settings.docusign_integration_key or not settings.docusign_private_key:
        raise DocuSignAuthError("DocuSign credentials not configured")
    with _LOCK:
        if _need_token():
            assertion = _build_jwt()
            payload = _exchange(assertion)
            _TOKEN_CACHE["access_token"] = payload["access_token"]
            _TOKEN_CACHE["expires_at"] = time.time() + int(payload.get("expires_in", _TOKEN_LIFETIME_SECONDS))
        return _TOKEN_CACHE["access_token"]


def build_consent_url(redirect_uri: str = "http://localhost:5173/docusign/callback") -> str:
    """One-time consent URL.

    Open this once in a browser logged in as the DocuSign user being
    impersonated. Click "Accept". After that, JWT grants will succeed.
    """
    return (
        f"https://{settings.docusign_auth_uri}/oauth/auth"
        f"?response_type=code"
        f"&scope=signature%20impersonation"
        f"&client_id={settings.docusign_integration_key}"
        f"&redirect_uri={redirect_uri}"
    )


def reset_cache() -> None:
    """Force the next call to re-fetch a token. Test helper."""
    with _LOCK:
        _TOKEN_CACHE["access_token"] = None
        _TOKEN_CACHE["expires_at"] = 0


def envelopes_base_url() -> str:
    """Base URL for envelope API calls — uses the account-scoped REST base."""
    return f"{settings.docusign_base_uri}/restapi/v2.1/accounts/{settings.docusign_account_id}"


def auth_headers() -> dict:
    return {
        "Authorization": f"Bearer {get_access_token()}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
