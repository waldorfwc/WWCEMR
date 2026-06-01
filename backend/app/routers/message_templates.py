"""Message template CRUD + render endpoint. Staff-managed canned replies."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.surgery import Surgery
from app.models.surgery_message import MessageTemplate
from app.routers.auth import get_current_user

router = APIRouter(prefix="/api/staff/message-templates",
                    tags=["staff-messages"])


class TemplatePayload(BaseModel):
    name: str
    body: str


def _to_dict(t: MessageTemplate) -> dict:
    return {"id": str(t.id), "name": t.name, "body": t.body}


def _render(body: str, surgery: Surgery) -> str:
    sd = surgery.scheduled_date
    date_str = sd.strftime("%B %-d, %Y") if sd else ""
    return (body
              .replace("{{patient_name}}", surgery.patient_name or "")
              .replace("{{surgery_date}}", date_str))


@router.get("")
def list_templates(db: Session = Depends(get_db),
                      user: dict = Depends(get_current_user)):
    rows = (db.query(MessageTemplate)
              .order_by(MessageTemplate.name.asc()).all())
    return {"templates": [_to_dict(t) for t in rows]}


@router.post("")
def create_template(payload: TemplatePayload,
                       db: Session = Depends(get_db),
                       user: dict = Depends(get_current_user)):
    t = MessageTemplate(name=payload.name.strip(),
                              body=payload.body)
    db.add(t); db.commit(); db.refresh(t)
    return _to_dict(t)


@router.put("/{tid}")
def update_template(tid: str, payload: TemplatePayload,
                       db: Session = Depends(get_db),
                       user: dict = Depends(get_current_user)):
    t = db.query(MessageTemplate).filter(MessageTemplate.id == tid).first()
    if t is None:
        raise HTTPException(status_code=404, detail="template not found")
    t.name = payload.name.strip()
    t.body = payload.body
    db.commit(); db.refresh(t)
    return _to_dict(t)


@router.delete("/{tid}")
def delete_template(tid: str, db: Session = Depends(get_db),
                       user: dict = Depends(get_current_user)):
    t = db.query(MessageTemplate).filter(MessageTemplate.id == tid).first()
    if t is None:
        raise HTTPException(status_code=404, detail="template not found")
    db.delete(t); db.commit()
    return {"ok": True}


@router.get("/{tid}/render")
def render_template(tid: str, surgery_id: str,
                       db: Session = Depends(get_db),
                       user: dict = Depends(get_current_user)):
    t = db.query(MessageTemplate).filter(MessageTemplate.id == tid).first()
    if t is None:
        raise HTTPException(status_code=404, detail="template not found")
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="surgery not found")
    return {"body": _render(t.body, s)}
