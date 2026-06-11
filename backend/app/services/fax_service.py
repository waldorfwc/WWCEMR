"""
RingCentral fax service — sends documents via fax using the RC API.

Auth: reuses the shared RingCentralClient (services/ringcentral_client.py)
which exchanges a long-lived JWT for a 1-hour access token, caches it in
process memory, and refreshes ~5 min before expiry. Credentials live in
env vars sourced from Secret Manager:

    RC_CLIENT_ID
    RC_CLIENT_SECRET
    RC_JWT_TOKEN
    RC_SERVER_URL (defaults to https://platform.ringcentral.com)

Previously this module loaded credentials from a local
`~/Documents/rc-credentials.json` file, which worked on the Mac dev
workstation but always failed on Cloud Run (no such file) — every fax
attempt came back "Failed to authenticate with RingCentral".
"""

import logging
import os
import json
import httpx
from typing import Optional

from app.services.ringcentral_client import client as _rc_client

log = logging.getLogger(__name__)


def _get_access_token() -> Optional[str]:
    """Return a fresh RC access token (cached + auto-refreshed) or None
    if credentials aren't configured."""
    try:
        return _rc_client()._ensure_token()
    except Exception as e:
        log.error("RingCentral auth failed: %s", e)
        return None


def _rc_base_url() -> str:
    # .strip() before .rstrip("/") — the rc-server-url secret in Secret
    # Manager has trailing whitespace that survives the env var, and
    # httpx IDNA-encodes the spaces into the hostname label → UnicodeError
    # "label too long". Same defensive strip ringcentral_client.py does.
    raw = os.environ.get("RC_SERVER_URL", "https://platform.ringcentral.com").strip()
    return raw.rstrip("/") or "https://platform.ringcentral.com"


def send_fax(
    to_number: str,
    file_path: str,
    cover_page_text: Optional[str] = None,
    patient_name: Optional[str] = None,
) -> dict:
    """
    Send a fax via RingCentral API.

    Args:
        to_number: Fax number (e.g., "+12405551234" or "2405551234")
        file_path: Path to the file to fax (PDF, JPG, etc.)
        cover_page_text: Optional cover page message
        patient_name: Optional patient name for the cover page

    Returns:
        dict with status, message_id, etc.
    """
    token = _get_access_token()
    if not token:
        return {"error": "Failed to authenticate with RingCentral"}

    # file_path may be either a local filesystem path (legacy / dev) or a
    # storage key (Cloud Run on GCS). Load the bytes either way, then send
    # those bytes to RingCentral. The pre-fix code did
    # os.path.isfile(file_path) and short-circuited with "File not found"
    # on every prod fax because GCS keys aren't local files.
    file_bytes: Optional[bytes] = None
    if os.path.isfile(file_path):
        with open(file_path, "rb") as f:
            file_bytes = f.read()
    else:
        try:
            from app.services.storage import read_blob
            file_bytes = read_blob(file_path)
        except FileNotFoundError:
            return {"error": f"File not found: {file_path}"}
        except Exception as e:
            return {"error": f"Could not load file '{file_path}': {e}"}

    # Normalize phone number and reject anything that doesn't end up as a
    # full +1NXXNXXXXXX. The old code would silently pass 9-digit or
    # 12-digit garbage to RingCentral as-is — a transposed digit would
    # fax PHI to the wrong recipient with no warning. (Fable recalls
    # audit H2.)
    import re as _re
    clean_num = to_number.strip().replace("-", "").replace("(", "").replace(")", "").replace(" ", "").replace(".", "")
    if not clean_num.startswith("+"):
        if len(clean_num) == 10:
            clean_num = "+1" + clean_num
        elif len(clean_num) == 11 and clean_num.startswith("1"):
            clean_num = "+" + clean_num
    if not _re.fullmatch(r"\+1\d{10}", clean_num):
        return {"error": f"Invalid fax number: {to_number}"}

    # Determine MIME type
    ext = os.path.splitext(file_path)[1].lower()
    mime_map = {
        ".pdf": "application/pdf",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".tiff": "image/tiff",
        ".tif": "image/tiff",
        ".doc": "application/msword",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
    mime_type = mime_map.get(ext, "application/octet-stream")
    filename = os.path.basename(file_path)

    # Build cover page text
    cover = cover_page_text or ""
    if patient_name:
        cover = f"Patient: {patient_name}\n{cover}"
    cover = cover.strip() or "Document attached"

    # Build multipart request — file_bytes was resolved above (local or GCS).
    file_content = file_bytes

    # RingCentral fax API uses multipart/mixed
    json_part = json.dumps({
        "to": [{"phoneNumber": clean_num}],
        "coverPageText": cover,
    })

    r = httpx.post(
        f"{_rc_base_url()}/restapi/v1.0/account/~/extension/~/fax",
        headers={"Authorization": f"Bearer {token}"},
        files=[
            ("json", (None, json_part, "application/json")),
            ("attachment", (filename, file_content, mime_type)),
        ],
        timeout=30,
    )

    if r.status_code in (200, 201, 202):
        data = r.json()
        return {
            "success": True,
            "message_id": data.get("id"),
            "status": data.get("messageStatus", "Queued"),
            "to": clean_num,
            "pages": data.get("pgCnt"),
        }
    else:
        # Surface RC's error detail to Cloud Run logs so we can see *why*
        # a non-2xx came back (missing scope, blocked recipient, etc.).
        # The same detail is also stored on the FaxLog row.
        log.error("RingCentral fax failed HTTP %s to %s: %s",
                  r.status_code, clean_num, r.text[:500])
        return {
            "error": f"Fax failed: {r.status_code}",
            "detail": r.text[:300],
        }


def check_fax_status(message_id: str) -> dict:
    """Check the delivery status of a sent fax."""
    token = _get_access_token()
    if not token:
        return {"error": "Auth failed"}

    r = httpx.get(
        f"{_rc_base_url()}/restapi/v1.0/account/~/extension/~/message-store/{message_id}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    if r.status_code == 200:
        data = r.json()
        return {
            "message_id": data.get("id"),
            "status": data.get("messageStatus"),
            "to": data.get("to", [{}])[0].get("phoneNumber"),
            "created": data.get("creationTime"),
        }
    return {"error": f"Status check failed: {r.status_code}"}
