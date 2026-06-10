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

import os

from app.database import get_db
from app.models.surgery import Surgery
from app.models.surgery_message import SurgeryMessage
from app.permissions.catalog import Module, Tier
from app.permissions.dependencies import requires_tier
from app.routers.auth import get_current_user
from app.services.patient_sms import send_patient_sms

router = APIRouter(prefix="/api/staff", tags=["staff-messages"])

# Same env var that surgery_klara_drafter uses, so a domain move
# doesn't half-break SMS links. (Fable recalls audit L4.)
PORTAL_URL = os.environ.get("PATIENT_PORTAL_URL",
                              "https://gw.waldorfwomenscare.com")


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
    user: dict = Depends(requires_tier(Module.SURGERY, Tier.WORK)),
):
    """Read the thread without side effects. Marking patient messages
    read is now an explicit POST to /mark-read so prefetch / GET
    auto-reload can't silently clear the shared inbox. (Fable M3.)"""
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="surgery not found")
    msgs = (db.query(SurgeryMessage)
              .filter(SurgeryMessage.surgery_id == surgery_id)
              .order_by(SurgeryMessage.sent_at.asc())
              .all())
    return {"messages": [_to_dict(m) for m in msgs]}


@router.post("/surgeries/{surgery_id}/messages/mark-read")
def staff_mark_read(
    surgery_id: str,
    db: Session = Depends(get_db),
    user: dict = Depends(requires_tier(Module.SURGERY, Tier.WORK)),
):
    """Explicitly mark all patient messages on this thread as read."""
    if not db.query(Surgery).filter(Surgery.id == surgery_id).first():
        raise HTTPException(status_code=404, detail="surgery not found")
    msgs = (db.query(SurgeryMessage)
              .filter(SurgeryMessage.surgery_id == surgery_id,
                      SurgeryMessage.author_kind == "patient",
                      SurgeryMessage.read_by_staff_at.is_(None))
              .all())
    now = datetime.utcnow()
    for m in msgs:
        m.read_by_staff_at = now
    db.commit()
    return {"marked": len(msgs)}


@router.post("/surgeries/{surgery_id}/messages")
def staff_send(
    surgery_id: str,
    payload: MessagePayload,
    db: Session = Depends(get_db),
    user: dict = Depends(requires_tier(Module.SURGERY, Tier.WORK)),
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
    # Route the patient-notification SMS through send_patient_sms so it
    # honors the consent gate and writes the PatientSms audit row.
    # Previously this called send_sms() directly, bypassing both.
    # (Fable recalls audit H1.)
    try:
        send_patient_sms(
            db, kind=None, surgery=s, context={},
            ad_hoc_body=(f"WWC has a new message for you. Sign in at "
                          f"{PORTAL_URL} to read it."),
            sent_by=user["email"],
        )
    except Exception:
        import logging
        logging.getLogger(__name__).exception("portal P6 SMS notify failed")
    return _to_dict(m)


@router.get("/messages/inbox")
def staff_inbox(
    db: Session = Depends(get_db),
    user: dict = Depends(requires_tier(Module.SURGERY, Tier.WORK)),
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
