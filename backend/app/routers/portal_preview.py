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
from app.services.audit_service import log_action
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
    # HIPAA: record the impersonation so we always know which staff
    # member viewed the patient portal as that patient and when. The
    # read-only enforcement happens elsewhere; this is the audit trail.
    email = (user.get("email") or "").lower().strip() or None
    log_action(
        db,
        action="IMPERSONATE",
        resource_type="surgery",
        resource_id=str(s.id),
        patient_id=s.chart_number or None,
        user_id=email,
        user_name=user.get("name") or email,
        description=(f"Staff issued a portal-preview JWT for "
                     f"{s.patient_name or s.chart_number} (read-only)"),
    )
    return {"token": token, "surgery_id": str(s.id)}
