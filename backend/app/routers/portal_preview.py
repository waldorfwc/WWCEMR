"""Staff endpoint: issue a short-lived patient-portal JWT for previewing.

Coordinators click "View as patient" on the surgery admin page; this
returns an impersonation JWT bearing a viewer="staff:<email>" claim so
the patient_portal middleware can enforce read-only access.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.surgery import Surgery
from app.routers.auth import get_current_user
from app.services.patient_portal_auth import issue_portal_token

router = APIRouter(prefix="/api/admin/surgeries", tags=["admin"])


@router.post("/{surgery_id}/portal-preview-token")
def portal_preview_token(
    surgery_id: str,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="surgery not found")
    token = issue_portal_token(
        s,
        viewer=f"staff:{user['email']}",
        ttl_minutes=60,
    )
    return {"token": token, "surgery_id": str(s.id)}
