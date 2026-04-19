"""
RingCentral fax service — sends documents via fax using the RC API.
"""

import os
import json
import httpx
from typing import Optional
from app.config import settings


_RC_CREDS = None

def _load_creds():
    global _RC_CREDS
    if _RC_CREDS:
        return _RC_CREDS
    creds_path = os.path.expanduser("~/Documents/rc-credentials.json")
    if os.path.isfile(creds_path):
        with open(creds_path) as f:
            _RC_CREDS = json.load(f)
        return _RC_CREDS
    return None


def _get_access_token() -> Optional[str]:
    creds = _load_creds()
    if not creds:
        return None

    jwt_creds = creds.get("jwt", {})
    jwt_token = list(jwt_creds.values())[0] if jwt_creds else None
    if not jwt_token:
        return None

    r = httpx.post(
        f"{creds['server']}/restapi/oauth/token",
        auth=(creds["clientId"], creds["clientSecret"]),
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": jwt_token,
        },
        timeout=10,
    )
    if r.status_code == 200:
        return r.json()["access_token"]
    return None


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

    if not os.path.isfile(file_path):
        return {"error": f"File not found: {file_path}"}

    # Normalize phone number
    clean_num = to_number.strip().replace("-", "").replace("(", "").replace(")", "").replace(" ", "")
    if not clean_num.startswith("+"):
        if len(clean_num) == 10:
            clean_num = "+1" + clean_num
        elif len(clean_num) == 11 and clean_num.startswith("1"):
            clean_num = "+" + clean_num

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

    # Build multipart request
    with open(file_path, "rb") as f:
        file_content = f.read()

    # RingCentral fax API uses multipart/mixed
    json_part = json.dumps({
        "to": [{"phoneNumber": clean_num}],
        "coverPageText": cover,
    })

    r = httpx.post(
        "https://platform.ringcentral.com/restapi/v1.0/account/~/extension/~/fax",
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
        f"https://platform.ringcentral.com/restapi/v1.0/account/~/extension/~/message-store/{message_id}",
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
