"""Google Workspace sync admin API.

All endpoints require user:manage. Off the /admin namespace because
this is admin-only ops surface.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.google_sync import GoogleSyncExclusion, GoogleSyncRun
from app.routers.auth import require_permission
from app.services import google_sync as svc

router = APIRouter(prefix="/admin/google-sync", tags=["admin-google-sync"])


# ─── Pydantic ────────────────────────────────────────────────────────

class ExclusionPayload(BaseModel):
    email: str
    reason: Optional[str] = None


# ─── Status + run-now ────────────────────────────────────────────────

@router.get("/status")
def status(db: Session = Depends(get_db),
           current_user: dict = Depends(require_permission("user:manage"))):
    """Last-run summary + whether the sync is configured."""
    last = (db.query(GoogleSyncRun)
              .order_by(GoogleSyncRun.started_at.desc())
              .first())
    return {
        "configured": svc.is_configured(),
        "last_run": svc._run_dict(last) if last else None,
    }


@router.post("/run")
def run_now(db: Session = Depends(get_db),
            current_user: dict = Depends(require_permission("user:manage"))):
    """Trigger an immediate sync. Returns the run summary."""
    return svc.run_sync(db, triggered_by=current_user.get("email") or "manual")


@router.get("/preview")
def preview(db: Session = Depends(get_db),
            current_user: dict = Depends(require_permission("user:manage"))):
    """Google emails that would be created on the next sync — useful for
    pre-excluding service accounts before they get auto-provisioned."""
    if not svc.is_configured():
        return {"configured": False, "would_create": []}
    try:
        emails = svc.preview_new_users(db)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Directory API error: {exc}")
    return {"configured": True, "would_create": emails}


@router.get("/runs")
def list_runs(limit: int = 20, db: Session = Depends(get_db),
              current_user: dict = Depends(require_permission("user:manage"))):
    rows = (db.query(GoogleSyncRun)
              .order_by(GoogleSyncRun.started_at.desc())
              .limit(limit).all())
    return {"runs": [svc._run_dict(r) for r in rows]}


# ─── Exclusions ──────────────────────────────────────────────────────

@router.get("/exclusions")
def list_exclusions(db: Session = Depends(get_db),
                    current_user: dict = Depends(require_permission("user:manage"))):
    rows = db.query(GoogleSyncExclusion).order_by(GoogleSyncExclusion.email).all()
    return {
        "exclusions": [
            {
                "email": r.email,
                "reason": r.reason,
                "added_by": r.added_by,
                "added_at": str(r.added_at),
            }
            for r in rows
        ]
    }


@router.post("/exclusions", status_code=201)
def add_exclusion(payload: ExclusionPayload, db: Session = Depends(get_db),
                  current_user: dict = Depends(require_permission("user:manage"))):
    email = (payload.email or "").lower().strip()
    if not email or "@" not in email:
        raise HTTPException(status_code=422, detail="email required (must include @)")
    existing = db.query(GoogleSyncExclusion).filter(GoogleSyncExclusion.email == email).first()
    if existing:
        # Update reason in place
        existing.reason = payload.reason
        existing.added_by = current_user.get("email")
        db.commit()
        return {"email": existing.email, "reason": existing.reason,
                "added_by": existing.added_by, "added_at": str(existing.added_at)}
    row = GoogleSyncExclusion(
        email=email,
        reason=payload.reason,
        added_by=current_user.get("email"),
    )
    db.add(row); db.commit(); db.refresh(row)
    return {"email": row.email, "reason": row.reason,
            "added_by": row.added_by, "added_at": str(row.added_at)}


@router.delete("/exclusions/{email}", status_code=204)
def remove_exclusion(email: str, db: Session = Depends(get_db),
                     current_user: dict = Depends(require_permission("user:manage"))):
    row = (db.query(GoogleSyncExclusion)
             .filter(GoogleSyncExclusion.email == email.lower().strip())
             .first())
    if not row:
        raise HTTPException(status_code=404, detail="exclusion not found")
    db.delete(row); db.commit()
    return None
