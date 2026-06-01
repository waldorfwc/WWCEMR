"""
Waystar API router — exposes Waystar integration endpoints to the frontend.
"""

import os

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional
from datetime import date

from app.database import get_db
from app.services.waystar_service import get_waystar_client, WaystarConnectionError
from app.services.audit_service import log_action
from app.config import settings
from app.services.storage import serve_blob

router = APIRouter(prefix="/waystar", tags=["waystar"])


@router.get("/status")
def waystar_status():
    """Return whether Waystar credentials are configured."""
    return {
        "configured": bool(settings.waystar_api_key and settings.waystar_password),
        "has_base_url": bool(settings.waystar_base_url),
        "has_sftp": bool(settings.waystar_sftp_host),
        "api_key_hint": (settings.waystar_api_key[:6] + "..." if settings.waystar_api_key else None),
    }


@router.post("/test-connection")
def test_connection(db: Session = Depends(get_db)):
    """Test all Waystar connection modes and return which works."""
    if not settings.waystar_api_key:
        raise HTTPException(status_code=400, detail="Waystar credentials not configured")
    try:
        client = get_waystar_client()
        result = client.test_connection()
        log_action(db, "WAYSTAR_TEST", "waystar", description=f"Connection test result: {result.get('status')}")
        return result
    except Exception as e:
        return {"status": "error", "detail": str(e)}


@router.get("/remittances")
def list_remittances(
    date_from: Optional[str] = Query(None, description="YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, description="YYYY-MM-DD"),
    payer_id: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Retrieve ERA/remittance records from Waystar."""
    if not settings.waystar_api_key:
        return {"error": "Waystar not configured", "items": []}

    df = None
    dt = None
    if date_from:
        try:
            df = date.fromisoformat(date_from)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date_from format")
    if date_to:
        try:
            dt = date.fromisoformat(date_to)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date_to format")

    client = get_waystar_client()
    items = client.get_remittances(date_from=df, date_to=dt, payer_id=payer_id)
    log_action(db, "VIEW", "waystar_remittances", description=f"Retrieved {len(items)} remittances")
    return {"items": items, "count": len(items)}


@router.get("/claim-status/{claim_number}")
def claim_status(claim_number: str, payer_id: Optional[str] = None, db: Session = Depends(get_db)):
    """Query real-time claim status from Waystar (276/277)."""
    if not settings.waystar_api_key:
        return {"error": "Waystar not configured"}
    client = get_waystar_client()
    result = client.get_claim_status(claim_number, payer_id)
    log_action(db, "WAYSTAR_CLAIM_STATUS", "claim", description=f"Claim status lookup: {claim_number}")
    return result


@router.post("/eligibility")
def check_eligibility(payload: dict, db: Session = Depends(get_db)):
    """
    Real-time eligibility verification (270/271).
    Body: { payer_id, member_id, first_name, last_name, dob, dos? }
    """
    required = ["payer_id", "member_id", "first_name", "last_name", "dob"]
    missing = [k for k in required if not payload.get(k)]
    if missing:
        raise HTTPException(status_code=400, detail=f"Missing fields: {', '.join(missing)}")
    if not settings.waystar_api_key:
        return {"error": "Waystar not configured"}

    client = get_waystar_client()
    result = client.check_eligibility(
        payer_id=payload["payer_id"],
        member_id=payload["member_id"],
        first_name=payload["first_name"],
        last_name=payload["last_name"],
        dob=payload["dob"],
        dos=payload.get("dos"),
    )
    log_action(db, "ELIGIBILITY_CHECK", "patient", description=f"Eligibility check payer: {payload['payer_id']}")
    return result


@router.get("/ar-summary")
def ar_summary(
    date_from: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """Get A/R aging summary from Waystar analytics."""
    if not settings.waystar_api_key:
        return {"error": "Waystar not configured", "data": {}}
    df = None
    if date_from:
        try:
            df = date.fromisoformat(date_from)
        except ValueError:
            pass
    client = get_waystar_client()
    result = client.get_ar_summary(date_from=df)
    return result


@router.post("/sync-eras")
def sync_eras_sftp(
    remote_dir: str = "/outbox/era",
    db: Session = Depends(get_db),
):
    """
    Download ERA files via SFTP and import them into the system.
    Requires WAYSTAR_SFTP_HOST to be configured.
    """
    if not settings.waystar_sftp_host:
        raise HTTPException(
            status_code=400,
            detail="SFTP not configured. Set WAYSTAR_SFTP_HOST in .env"
        )
    try:
        client = get_waystar_client()
        downloaded = client.download_eras_sftp(remote_dir=remote_dir)

        # Parse + post each downloaded file through the Phase 2c ERA pipeline.
        from app.services.era_poster import process_era_file

        results = []
        for fpath in downloaded:
            try:
                with open(fpath, "r") as f:
                    content = f.read()
                result = process_era_file(
                    db, content,
                    filename=os.path.basename(fpath),
                    user_email="waystar-sftp-sync",
                )
                results.append({
                    "file": os.path.basename(fpath),
                    "claims_posted": result.claims_posted,
                    "claims_unmatched": result.claims_unmatched,
                    "status": "imported" if result.claims_posted else "no_matches",
                })
            except Exception as e:
                results.append({
                    "file": os.path.basename(fpath),
                    "status": "error",
                    "error": str(e),
                })

        log_action(db, "WAYSTAR_SFTP_SYNC", "waystar",
                   description=f"SFTP sync: {len(downloaded)} files downloaded")
        return {"downloaded": len(downloaded), "results": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/eob-report/{filename}")
def download_eob_report(filename: str, db: Session = Depends(get_db)):
    """Download a Waystar EOB/remittance report file from gs://wwc-app-docs/waystar-reports/."""
    safe_name = os.path.basename(filename)
    log_action(db, "DOWNLOAD", "eob_report",
               description=f"Downloaded EOB report: {safe_name}")
    return serve_blob(
        local_path=None,
        gcs_object=f"waystar-reports/{safe_name}",
        media_type="text/plain",
        filename=safe_name,
        disposition="inline",
    )
