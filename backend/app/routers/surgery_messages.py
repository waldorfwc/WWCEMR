"""Staff endpoints: per-surgery thread + global unread inbox.

The patient-facing endpoints live in patient_portal.py (gated by the
portal JWT). These endpoints are gated by the staff session via
get_current_user.
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.surgery import Surgery
from app.models.surgery_message import SurgeryMessage
from app.routers.auth import get_current_user
from app.services.checklist_notifications import send_sms

router = APIRouter(prefix="/api/staff", tags=["staff-messages"])

PORTAL_URL = "https://gw.waldorfwomenscare.com"


class MessagePayload(BaseModel):
    body: str


def _to_dict(m: SurgeryMessage) -> dict:
    return {
        "id":           str(m.id),
        "author_kind":  m.author_kind,
        "author_email": m.author_email,
        "body":         m.body,
        "sent_at":      m.sent_at.isoformat() if m.sent_at else None,
    }


@router.get("/surgeries/{surgery_id}/messages")
def staff_thread(
    surgery_id: str,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="surgery not found")
    msgs = (db.query(SurgeryMessage)
              .filter(SurgeryMessage.surgery_id == surgery_id)
              .order_by(SurgeryMessage.sent_at.asc())
              .all())
    for m in msgs:
        if m.author_kind == "patient" and m.read_by_staff_at is None:
            m.read_by_staff_at = datetime.utcnow()
    db.commit()
    return {"messages": [_to_dict(m) for m in msgs]}


@router.post("/surgeries/{surgery_id}/messages")
def staff_send(
    surgery_id: str,
    payload: MessagePayload,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    body = (payload.body or "").strip()
    if not body:
        raise HTTPException(status_code=422, detail="Message cannot be empty.")
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="surgery not found")
    m = SurgeryMessage(
        surgery_id=s.id,
        author_kind="staff",
        author_email=user["email"],
        body=body,
    )
    db.add(m); db.commit(); db.refresh(m)
    phone = (s.cell_phone or s.phone or "").strip()
    if phone:
        try:
            send_sms(phone,
                       f"WWC has a new message for you. Sign in at "
                       f"{PORTAL_URL} to read it.")
        except Exception:
            import logging
            logging.getLogger(__name__).exception("portal P6 SMS notify failed")
    return _to_dict(m)


@router.get("/messages/inbox")
def staff_inbox(
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """Surgeries with at least one unread patient message, newest first.
    Collapse to one row per surgery (most recent unread)."""
    rows = (db.query(SurgeryMessage, Surgery)
              .join(Surgery, Surgery.id == SurgeryMessage.surgery_id)
              .filter(SurgeryMessage.author_kind == "patient",
                       SurgeryMessage.read_by_staff_at.is_(None))
              .order_by(SurgeryMessage.sent_at.desc())
              .all())
    seen: set = set()
    out = []
    for m, s in rows:
        if s.id in seen: continue
        seen.add(s.id)
        out.append({
            "surgery_id":    str(s.id),
            "chart_number":  s.chart_number,
            "patient_name":  s.patient_name,
            "last_body":     m.body[:80],
            "last_sent_at":  m.sent_at.isoformat() if m.sent_at else None,
        })
    return {"rows": out, "count": len(out)}
