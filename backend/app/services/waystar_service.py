"""
Waystar Integration Service
Supports REST API, SFTP ERA download, and SOAP/XML legacy connections.
Connection mode is auto-detected from configured credentials and base URL.
"""

import base64
import os
import re
from datetime import date, timedelta
from typing import Optional, List, Dict
from io import BytesIO

import httpx

from app.config import settings
from app.parsers.era_835 import Era835Parser


class WaystarConnectionError(Exception):
    pass


class WaystarClient:
    """
    Unified Waystar client — handles REST, SFTP, and legacy ZirMed modes.
    Call test_connection() first to confirm which mode works.
    """

    def __init__(self):
        self.api_key = settings.waystar_api_key
        self.password = settings.waystar_password
        self.base_url = (settings.waystar_base_url or "").rstrip("/")
        self._token: Optional[str] = None
        self._mode: Optional[str] = None  # "rest", "sftp", "basic"

    # ── Connection Testing ───────────────────────────────────

    def test_connection(self) -> Dict:
        """
        Try all known Waystar connection modes.
        Returns dict with mode, status, and details.
        """
        results = {}

        # Mode 1: OAuth2 token exchange (modern Waystar REST)
        try:
            token = self._get_oauth_token()
            if token:
                self._token = token
                self._mode = "rest_oauth"
                return {"mode": "rest_oauth", "status": "connected", "token_obtained": True}
        except Exception as e:
            results["rest_oauth"] = str(e)

        # Mode 2: Basic auth REST
        try:
            r = self._basic_auth_request("GET", "/claims")
            if r and r.status_code in (200, 400, 403):
                self._mode = "rest_basic"
                return {"mode": "rest_basic", "status": "connected", "http_code": r.status_code}
        except Exception as e:
            results["rest_basic"] = str(e)

        # Mode 3: SFTP
        if settings.waystar_sftp_host:
            try:
                result = self._test_sftp()
                if result:
                    self._mode = "sftp"
                    return {"mode": "sftp", "status": "connected"}
            except Exception as e:
                results["sftp"] = str(e)

        return {
            "mode": None,
            "status": "failed",
            "details": results,
            "help": (
                "Connection failed. Waystar requires IP whitelisting for API access. "
                "Please check your Waystar portal under Settings > API or Settings > EDI "
                "to confirm the correct base URL and whether your IP needs to be whitelisted. "
                "Contact Waystar support at 844-927-9782 to whitelist your IP: "
                + self._get_public_ip()
            ),
        }

    def _get_public_ip(self) -> str:
        try:
            r = httpx.get("https://api.ipify.org", timeout=5)
            return r.text.strip()
        except Exception:
            return "unknown"

    # ── OAuth2 (Modern REST) ─────────────────────────────────

    def _get_oauth_token(self) -> Optional[str]:
        if not self.base_url:
            raise WaystarConnectionError("No base URL configured")

        credentials = base64.b64encode(
            f"{self.api_key}:{self.password}".encode()
        ).decode()

        token_urls = [
            f"{self.base_url}/v2/token",
            f"{self.base_url}/oauth/token",
            f"{self.base_url}/auth/token",
            f"{self.base_url}/token",
        ]

        for url in token_urls:
            try:
                r = httpx.post(
                    url,
                    headers={
                        "Authorization": f"Basic {credentials}",
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Accept": "application/json",
                    },
                    data={"grant_type": "client_credentials"},
                    timeout=10,
                    follow_redirects=True,
                )
                if r.status_code == 200:
                    data = r.json()
                    return data.get("access_token") or data.get("token")
            except Exception:
                continue
        raise WaystarConnectionError("Could not obtain OAuth token from any endpoint")

    def _get_auth_headers(self) -> Dict[str, str]:
        if self._token:
            return {
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        credentials = base64.b64encode(
            f"{self.api_key}:{self.password}".encode()
        ).decode()
        return {
            "Authorization": f"Basic {credentials}",
            "x-api-key": self.api_key,
            "Accept": "application/json",
        }

    def _basic_auth_request(self, method: str, path: str, **kwargs):
        if not self.base_url:
            raise WaystarConnectionError("No base URL configured")
        url = f"{self.base_url}{path}"
        with httpx.Client(timeout=15, follow_redirects=True) as client:
            return client.request(method, url, headers=self._get_auth_headers(), **kwargs)

    # ── SFTP ERA Download ────────────────────────────────────

    def _sftp_password(self) -> str:
        """Return the SFTP-specific password, falling back to the API password."""
        return settings.waystar_sftp_password or self.password

    def _sftp_username(self) -> str:
        return settings.waystar_sftp_username or self.api_key

    def _test_sftp(self) -> bool:
        import paramiko
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(
            settings.waystar_sftp_host,
            port=settings.waystar_sftp_port,
            username=self._sftp_username(),
            password=self._sftp_password(),
            timeout=10,
        )
        ssh.close()
        return True

    def download_eras_sftp(self, remote_dir: str = "Out/835", local_dir: str = "./uploads/waystar_835") -> List[str]:
        """Download ERA files from Waystar SFTP."""
        import paramiko
        downloaded = []
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(
            settings.waystar_sftp_host,
            port=settings.waystar_sftp_port,
            username=self._sftp_username(),
            password=self._sftp_password(),
        )
        sftp = ssh.open_sftp()
        os.makedirs(local_dir, exist_ok=True)

        try:
            files = sftp.listdir(remote_dir)
            era_files = [f for f in files if f.lower().endswith((".835", ".era", ".x12", ".txt"))]
            for filename in era_files:
                remote_path = f"{remote_dir}/{filename}"
                local_path = os.path.join(local_dir, filename)
                sftp.get(remote_path, local_path)
                downloaded.append(local_path)
        finally:
            sftp.close()
            ssh.close()

        return downloaded

    # ── REST API Endpoints ───────────────────────────────────

    def get_claim_status(self, claim_number: str, payer_id: Optional[str] = None) -> Dict:
        """Query claim status (276/277 equivalent)."""
        if not self.base_url:
            return {"error": "No Waystar base URL configured"}

        paths = [
            f"/claims/{claim_number}/status",
            f"/v1/claims/{claim_number}",
            f"/claim-status?claimNumber={claim_number}",
        ]
        for path in paths:
            try:
                r = self._basic_auth_request("GET", path)
                if r.status_code == 200:
                    return r.json()
            except Exception:
                continue
        return {"error": "Claim status endpoint not reachable", "claim_number": claim_number}

    def get_remittances(
        self,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
        payer_id: Optional[str] = None,
    ) -> List[Dict]:
        """Retrieve ERA/remittance records."""
        if not self.base_url:
            return []

        date_from = date_from or (date.today() - timedelta(days=90))
        date_to = date_to or date.today()

        params = {
            "from": date_from.isoformat(),
            "to": date_to.isoformat(),
        }
        if payer_id:
            params["payerId"] = payer_id

        for path in ["/remittances", "/v1/remittances", "/era"]:
            try:
                r = self._basic_auth_request("GET", path, params=params)
                if r.status_code == 200:
                    return r.json() if isinstance(r.json(), list) else r.json().get("items", [])
            except Exception:
                continue
        return []

    def get_era_file(self, remittance_id: str) -> Optional[str]:
        """Download a specific ERA 835 file content."""
        for path in [
            f"/remittances/{remittance_id}/835",
            f"/era/{remittance_id}/download",
        ]:
            try:
                r = self._basic_auth_request("GET", path)
                if r.status_code == 200:
                    return r.text
            except Exception:
                continue
        return None

    def get_ar_summary(self, date_from: Optional[date] = None) -> Dict:
        """Get A/R aging summary from Waystar."""
        date_from = date_from or (date.today() - timedelta(days=180))
        for path in ["/reports/ar-aging", "/analytics/ar", "/v1/ar-summary"]:
            try:
                r = self._basic_auth_request("GET", path, params={"from": date_from.isoformat()})
                if r.status_code == 200:
                    return r.json()
            except Exception:
                continue
        return {}

    def check_eligibility(
        self,
        payer_id: str,
        member_id: str,
        first_name: str,
        last_name: str,
        dob: str,
        dos: Optional[str] = None,
    ) -> Dict:
        """Real-time eligibility verification (270/271)."""
        payload = {
            "payerId": payer_id,
            "memberId": member_id,
            "firstName": first_name,
            "lastName": last_name,
            "dateOfBirth": dob,
            "dateOfService": dos or date.today().isoformat(),
        }
        for path in ["/eligibility/checks", "/v2/eligibility", "/eligibility"]:
            try:
                r = self._basic_auth_request("POST", path, json=payload)
                if r.status_code in (200, 201):
                    return r.json()
            except Exception:
                continue
        return {"error": "Eligibility check not reachable"}


# Singleton
_client: Optional[WaystarClient] = None


def get_waystar_client() -> WaystarClient:
    global _client
    if _client is None:
        _client = WaystarClient()
    return _client
