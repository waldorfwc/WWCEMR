"""RingCentral REST API client.

Uses JWT-bearer auth flow:
  - Long-lived JWT (set in env once) is exchanged for a 1-hour access token
  - Access token is cached in-process and refreshed on demand
  - Endpoints used: /oauth/token, /account/~/extension, /account/~/extension/{id}/ring-out

Configuration (env):
  RC_CLIENT_ID
  RC_CLIENT_SECRET
  RC_JWT_TOKEN
  RC_SERVER_URL (defaults to https://platform.ringcentral.com)
  RC_CALLER_ID  (E.164 number to display on patient caller ID)
"""
from __future__ import annotations

import base64
import logging
import os
import threading
import time
from typing import Optional

import httpx

log = logging.getLogger(__name__)


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


class RingCentralClient:
    """Singleton-ish client. Caches the access token in memory until ~5 min
    before expiry, then re-exchanges the JWT."""

    def __init__(self):
        self._access_token: Optional[str] = None
        self._expires_at: float = 0.0
        self._lock = threading.Lock()

    # ─── Auth ─────────────────────────────────────────────────────────

    def _ensure_token(self) -> str:
        with self._lock:
            now = time.time()
            if self._access_token and self._expires_at - now > 300:
                return self._access_token
            self._refresh()
            return self._access_token

    def _refresh(self) -> None:
        cid = _env("RC_CLIENT_ID")
        csec = _env("RC_CLIENT_SECRET")
        jwt = _env("RC_JWT_TOKEN")
        url = _env("RC_SERVER_URL", "https://platform.ringcentral.com")
        if not (cid and csec and jwt):
            raise RuntimeError("RingCentral credentials not configured in env")

        basic = base64.b64encode(f"{cid}:{csec}".encode()).decode()
        r = httpx.post(
            f"{url}/restapi/oauth/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": jwt,
            },
            headers={
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            timeout=15,
        )
        if r.status_code != 200:
            raise RuntimeError(f"RC token exchange failed: HTTP {r.status_code} — {r.text[:200]}")
        body = r.json()
        self._access_token = body["access_token"]
        self._expires_at = time.time() + int(body.get("expires_in", 3600))
        log.info("RC token refreshed; expires in %s sec", body.get("expires_in"))

    # ─── Helpers ──────────────────────────────────────────────────────

    @property
    def base_url(self) -> str:
        return _env("RC_SERVER_URL", "https://platform.ringcentral.com")

    @property
    def caller_id(self) -> str:
        return _env("RC_CALLER_ID")

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._ensure_token()}",
            "Accept": "application/json",
        }

    # ─── Endpoints ────────────────────────────────────────────────────

    def list_extensions(self) -> list[dict]:
        """Return all User-type extensions on the account.
        Used to auto-populate User.ringcentral_user_id by email."""
        r = httpx.get(
            f"{self.base_url}/restapi/v1.0/account/~/extension",
            params={"perPage": 200, "type": "User"},
            headers=self._headers(),
            timeout=20,
        )
        r.raise_for_status()
        return r.json().get("records", [])

    def ring_out(self, *, from_ext_id: str, from_phone: str,
                 to_phone: str, caller_id: Optional[str] = None) -> dict:
        """Initiate a RingOut call.

        from_ext_id     : the calling user's RC extension ID (path param, numeric)
        from_ext_number : the calling user's extension NUMBER (e.g. "600") —
                          RC rings whatever device is registered to this extension
        to_phone        : E.164 patient phone number
        caller_id       : E.164 number patient sees on their caller ID
                          (defaults to RC_CALLER_ID env var — usually the
                          practice main number for branding/safety)

        Flow: RC platform rings the from-extension's registered devices
        (desk phone, RC mobile app, RC desktop). Once the user answers,
        RC dials the patient and bridges. Patient sees caller_id on their
        screen, never the staff's personal number.
        """
        if not from_ext_id:
            raise ValueError("from_ext_id required")
        if not from_phone:
            raise ValueError("from_phone required (the PSTN number RC will call first)")
        if not to_phone:
            raise ValueError("to_phone required")
        if from_phone == to_phone:
            raise ValueError("from_phone and to_phone cannot be the same number")
        cid = caller_id or self.caller_id
        if not cid:
            raise RuntimeError("RC_CALLER_ID not configured and no caller_id passed")

        body = {
            # RC calls from_phone first; once it picks up, dials to_phone.
            "from": {"phoneNumber": from_phone},
            "to": {"phoneNumber": to_phone},
            "callerId": {"phoneNumber": cid},
            "playPrompt": False,
            "country": {"id": "1"},
        }
        r = httpx.post(
            f"{self.base_url}/restapi/v1.0/account/~/extension/{from_ext_id}/ring-out",
            json=body,
            headers={**self._headers(), "Content-Type": "application/json"},
            timeout=15,
        )
        if r.status_code not in (200, 201):
            raise RuntimeError(f"RC ring-out failed: HTTP {r.status_code} — {r.text[:300]}")
        return r.json()

    def get_call_log(self, call_log_id: str) -> dict:
        """Fetch a call log entry by ID — used to retrieve duration after a
        RingOut call completes."""
        r = httpx.get(
            f"{self.base_url}/restapi/v1.0/account/~/call-log/{call_log_id}",
            headers=self._headers(),
            timeout=15,
        )
        r.raise_for_status()
        return r.json()


# Module-level singleton. Imports stay cheap; lazy-creates the token on
# first use.
_client: Optional[RingCentralClient] = None


def client() -> RingCentralClient:
    global _client
    if _client is None:
        _client = RingCentralClient()
    return _client
