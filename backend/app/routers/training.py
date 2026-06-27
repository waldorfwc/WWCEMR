"""Training & certification API."""
from __future__ import annotations

from datetime import date as _date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.utils.http import content_disposition
from app.models.checklist import TaskTemplate
from app.models.training import TrainerAuthorization, TrainingCertification
from app.models.user import User
from app.routers.auth import get_current_user
from app.permissions.catalog import Module, Tier
from app.permissions.dependencies import requires_tier
from app.services import training_service


router = APIRouter(prefix="/training", tags=["training"])


# ─── Pydantic ────────────────────────────────────────────────────────

class AuthorizeTrainerPayload(BaseModel):
    user_email: str
    template_id: str
    notes: Optional[str] = None


class RevokeTrainerPayload(BaseModel):
    user_email: str
    template_id: str
    reason: Optional[str] = None


class CertifyPayload(BaseModel):
    trainee_email: str
    template_id: str
    notes: Optional[str] = None


class AcknowledgePayload(BaseModel):
    confirm: bool                       # True = "I was trained", False = dispute
    dispute_reason: Optional[str] = None


class RevokeCertPayload(BaseModel):
    reason: Optional[str] = None


class CertifyGroupPayload(BaseModel):
    template_id: str
    group_id: str
    notes: Optional[str] = None


# ─── Serializers ─────────────────────────────────────────────────────

def _trainer_dict(a: TrainerAuthorization) -> dict:
    return {
        "id": str(a.id),
        "user_email": a.user_email,
        "template_id": str(a.template_id),
        "authorized_by": a.authorized_by,
        "authorized_at": str(a.authorized_at),
        "revoked_at": str(a.revoked_at) if a.revoked_at else None,
        "revoked_by": a.revoked_by,
        "revoked_reason": a.revoked_reason,
        "notes": a.notes,
    }


def _cert_dict(c: TrainingCertification, today: Optional[_date] = None) -> dict:
    today = today or _date.today()
    expired = bool(c.expires_on and c.expires_on < today)
    is_active = (c.status == "active" and c.revoked_at is None and not expired)
    return {
        "id": str(c.id),
        "user_email": c.user_email,
        "template_id": str(c.template_id),
        "trainer_email": c.trainer_email,
        "trainer_signed_at": str(c.trainer_signed_at) if c.trainer_signed_at else None,
        "trainee_signed_at": str(c.trainee_signed_at) if c.trainee_signed_at else None,
        "status": c.status,
        "is_active": is_active,
        "expires_on": str(c.expires_on) if c.expires_on else None,
        "expired": expired,
        "revoked_at": str(c.revoked_at) if c.revoked_at else None,
        "revoked_by": c.revoked_by,
        "revoked_reason": c.revoked_reason,
        "notes": c.notes,
    }


# ─── Trainer authorization (manager-only) ────────────────────────────

@router.post("/trainers", status_code=201)
def authorize_trainer(payload: AuthorizeTrainerPayload,
                       db: Session = Depends(get_db),
                       current_user: dict = Depends(requires_tier(Module.TRAINING, Tier.MANAGE))):
    tmpl = db.query(TaskTemplate).filter(TaskTemplate.id == payload.template_id).first()
    if not tmpl:
        raise HTTPException(status_code=404, detail="template not found")
    user = db.query(User).filter(User.email == payload.user_email.lower().strip()).first()
    if not user:
        raise HTTPException(status_code=422, detail="user not found")
    row = training_service.authorize_trainer(
        db,
        user_email=payload.user_email,
        template_id=payload.template_id,
        authorized_by=current_user.get("email"),
        notes=payload.notes,
    )
    return _trainer_dict(row)


@router.delete("/trainers", status_code=200)
def revoke_trainer(payload: RevokeTrainerPayload,
                    db: Session = Depends(get_db),
                    current_user: dict = Depends(requires_tier(Module.TRAINING, Tier.MANAGE))):
    try:
        row = training_service.revoke_trainer(
            db,
            user_email=payload.user_email,
            template_id=payload.template_id,
            revoked_by=current_user.get("email"),
            reason=payload.reason,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return _trainer_dict(row)


@router.get("/trainers")
def list_trainers(template_id: Optional[str] = None,
                   user_email: Optional[str] = None,
                   include_revoked: bool = False,
                   db: Session = Depends(get_db),
                   current_user: dict = Depends(requires_tier(Module.TRAINING, Tier.VIEW))):
    """List trainer authorizations. Filter by template or user."""
    q = db.query(TrainerAuthorization)
    if template_id:
        q = q.filter(TrainerAuthorization.template_id == template_id)
    if user_email:
        q = q.filter(TrainerAuthorization.user_email == user_email.lower().strip())
    if not include_revoked:
        q = q.filter(TrainerAuthorization.revoked_at.is_(None))
    return {"trainers": [_trainer_dict(r) for r in q.all()]}


# ─── Certifications ──────────────────────────────────────────────────

@router.post("/certifications", status_code=201)
def certify(payload: CertifyPayload, db: Session = Depends(get_db),
             current_user: dict = Depends(get_current_user)):
    """Trainer signs that they trained the trainee.

    Per-template TrainerAuthorization is required, EXCEPT when the caller
    has Training:Manage tier (super users / office managers) — they can
    directly mark anyone trained without authorizing themselves first.
    """
    from app.permissions.resolver import effective_tier
    trainer = (current_user.get("email") or "").lower().strip()
    tmpl = db.query(TaskTemplate).filter(TaskTemplate.id == payload.template_id).first()
    if not tmpl:
        raise HTTPException(status_code=404, detail="template not found")
    trainee = db.query(User).filter(User.email == payload.trainee_email.lower().strip()).first()
    if not trainee:
        raise HTTPException(status_code=422, detail="trainee user not found")

    is_authorizer = effective_tier(db, trainer, Module.TRAINING) >= Tier.MANAGE

    try:
        row = training_service.certify(
            db,
            trainer_email=trainer,
            trainee_email=payload.trainee_email,
            template_id=payload.template_id,
            notes=payload.notes,
            bypass_trainer_check=is_authorizer,
        )
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    return _cert_dict(row)


@router.post("/certify-group", status_code=201)
def certify_group(payload: CertifyGroupPayload,
                    db: Session = Depends(get_db),
                    current_user: dict = Depends(requires_tier(Module.TRAINING, Tier.MANAGE))):
    """Bulk-certify every member of a group on one template. Skips users
    already certified (idempotent). Always bypasses the trainer check
    since this endpoint is gated on training:authorize."""
    from app.models.groups import Group
    tmpl = db.query(TaskTemplate).filter(TaskTemplate.id == payload.template_id).first()
    if not tmpl:
        raise HTTPException(status_code=404, detail="template not found")
    grp = db.query(Group).filter(Group.id == payload.group_id).first()
    if not grp:
        raise HTTPException(status_code=404, detail="group not found")

    trainer = (current_user.get("email") or "").lower().strip()
    issued: list = []
    skipped: list = []
    for u in (grp.members or []):
        em = (u.email or "").lower().strip()
        if not em or em == trainer:
            skipped.append({"email": em, "reason": "self / blank"})
            continue
        if training_service.is_certified(db, em, payload.template_id):
            skipped.append({"email": em, "reason": "already active"})
            continue
        try:
            training_service.certify(
                db, trainer_email=trainer, trainee_email=em,
                template_id=payload.template_id,
                notes=payload.notes or f"Bulk-issued to group {grp.name}",
                bypass_trainer_check=True,
            )
            issued.append(em)
        except ValueError as e:
            skipped.append({"email": em, "reason": str(e)})
    db.commit()
    return {"template_id": str(tmpl.id), "group_id": str(grp.id),
            "group_name": grp.name, "issued": issued, "skipped": skipped}


class RevokeGroupPayload(BaseModel):
    template_id: str
    group_id: str
    reason: Optional[str] = None


@router.post("/revoke-group")
def revoke_group(payload: RevokeGroupPayload,
                   db: Session = Depends(get_db),
                   current_user: dict = Depends(requires_tier(Module.TRAINING, Tier.MANAGE))):
    """Bulk-revoke certifications for every member of a group on one
    template. Used when an SOP changes and everyone needs to re-train."""
    from app.models.groups import Group
    grp = db.query(Group).filter(Group.id == payload.group_id).first()
    if not grp:
        raise HTTPException(status_code=404, detail="group not found")

    by = (current_user.get("email") or "").lower().strip()
    revoked: list = []
    skipped: list = []
    for u in (grp.members or []):
        em = (u.email or "").lower().strip()
        if not em:
            continue
        cert = (db.query(TrainingCertification)
                  .filter(TrainingCertification.user_email == em,
                          TrainingCertification.template_id == payload.template_id,
                          TrainingCertification.revoked_at.is_(None))
                  .first())
        if not cert:
            skipped.append({"email": em, "reason": "no active cert"})
            continue
        try:
            training_service.revoke_cert(
                db, cert_id=cert.id, revoked_by=by,
                reason=payload.reason or f"Bulk revoke for group {grp.name}",
            )
            revoked.append(em)
        except ValueError as e:
            skipped.append({"email": em, "reason": str(e)})
    db.commit()
    return {"template_id": payload.template_id, "group_id": str(grp.id),
            "group_name": grp.name, "revoked": revoked, "skipped": skipped}


@router.patch("/certifications/{cert_id}/acknowledge")
def acknowledge(cert_id: str, payload: AcknowledgePayload,
                 db: Session = Depends(get_db),
                 current_user: dict = Depends(get_current_user)):
    trainee = (current_user.get("email") or "").lower().strip()
    try:
        row = training_service.acknowledge(
            db,
            cert_id=cert_id,
            trainee_email=trainee,
            confirm=payload.confirm,
            dispute_reason=payload.dispute_reason,
        )
    except ValueError as e:
        msg = str(e)
        code = 403 if "only acknowledge your own" in msg else 422
        if "not found" in msg:
            code = 404
        raise HTTPException(status_code=code, detail=msg)
    return _cert_dict(row)


@router.post("/certifications/{cert_id}/force-acknowledge")
def force_acknowledge(cert_id: str,
                       db: Session = Depends(get_db),
                       current_user: dict = Depends(requires_tier(Module.TRAINING, Tier.MANAGE))):
    """Admin override: mark a pending_trainee certification as active on
    the trainee's behalf. Used when the trainee hasn't logged in / can't
    log in to acknowledge themselves. Audited via training_service."""
    from datetime import datetime as _dt
    from app.models.training import TrainingCertification
    from app.services.training_service import compute_expires_on
    row = db.query(TrainingCertification).filter(TrainingCertification.id == cert_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="certification not found")
    if row.status != "pending_trainee":
        raise HTTPException(status_code=422,
                            detail=f"cert is {row.status}, not pending_trainee")
    by = (current_user.get("email") or "system").lower().strip()
    row.status = "active"
    row.trainee_signed_at = _dt.utcnow()
    tmpl = db.query(TaskTemplate).filter(TaskTemplate.id == row.template_id).first()
    if tmpl:
        row.expires_on = compute_expires_on(tmpl, row.trainee_signed_at)
    row.notes = ((row.notes or "") + f"\n[force-acknowledged by {by}]").strip()
    db.commit(); db.refresh(row)
    return _cert_dict(row)


@router.delete("/certifications/{cert_id}", status_code=200)
def revoke_cert(cert_id: str, payload: RevokeCertPayload = RevokeCertPayload(),
                 db: Session = Depends(get_db),
                 current_user: dict = Depends(requires_tier(Module.TRAINING, Tier.MANAGE))):
    try:
        row = training_service.revoke_cert(
            db,
            cert_id=cert_id,
            revoked_by=current_user.get("email"),
            reason=payload.reason,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return _cert_dict(row)


@router.get("/certifications")
def list_certifications(template_id: Optional[str] = None,
                         user_email: Optional[str] = None,
                         status: Optional[str] = None,
                         db: Session = Depends(get_db),
                         current_user: dict = Depends(requires_tier(Module.TRAINING, Tier.VIEW))):
    q = db.query(TrainingCertification)
    if template_id:
        q = q.filter(TrainingCertification.template_id == template_id)
    if user_email:
        q = q.filter(TrainingCertification.user_email == user_email.lower().strip())
    if status:
        q = q.filter(TrainingCertification.status == status)
    return {"certifications": [_cert_dict(r) for r in q.all()]}


# ─── My view (the trainee's perspective) ─────────────────────────────

@router.get("/mine")
def my_training(db: Session = Depends(get_db),
                 current_user: dict = Depends(get_current_user)):
    """What's pending for me (acks) + what I'm certified on + what I'm
    authorized to train others on."""
    me = (current_user.get("email") or "").lower().strip()

    pending = (db.query(TrainingCertification, TaskTemplate)
                 .join(TaskTemplate, TrainingCertification.template_id == TaskTemplate.id)
                 .filter(TrainingCertification.user_email == me,
                         TrainingCertification.status == "pending_trainee")
                 .all())
    certified = (db.query(TrainingCertification, TaskTemplate)
                   .join(TaskTemplate, TrainingCertification.template_id == TaskTemplate.id)
                   .filter(TrainingCertification.user_email == me,
                           TrainingCertification.status.in_(["active", "disputed", "revoked"]))
                   .all())
    trainer_for = (db.query(TrainerAuthorization, TaskTemplate)
                     .join(TaskTemplate, TrainerAuthorization.template_id == TaskTemplate.id)
                     .filter(TrainerAuthorization.user_email == me,
                             TrainerAuthorization.revoked_at.is_(None))
                     .all())

    def with_tmpl(cert, tmpl):
        d = _cert_dict(cert)
        d["template"] = {
            "id": str(tmpl.id), "title": tmpl.title,
            "training_material_url": tmpl.training_material_url,
            "category": tmpl.category,
        }
        return d

    return {
        "pending_acknowledgments": [with_tmpl(c, t) for c, t in pending],
        "my_certifications": [with_tmpl(c, t) for c, t in certified],
        "trainer_for": [
            {
                **_trainer_dict(a),
                "template": {"id": str(t.id), "title": t.title,
                             "training_material_url": t.training_material_url,
                             "category": t.category},
            } for a, t in trainer_for
        ],
    }


# ─── Matrix view (admin / manager) ───────────────────────────────────

@router.get("/matrix")
def training_matrix(db: Session = Depends(get_db),
                     current_user: dict = Depends(requires_tier(Module.TRAINING, Tier.VIEW))):
    """Full grid: every active template × every user, with certification
    status per cell. Used by /admin/training. Trims templates without
    requires_training and users not in any group."""
    today = _date.today()

    templates = (db.query(TaskTemplate)
                   .filter(TaskTemplate.active.is_(True),
                           TaskTemplate.requires_training.is_(True))
                   .order_by(TaskTemplate.category, TaskTemplate.title)
                   .all())
    users = (db.query(User)
                .filter(User.groups.any())
                .order_by(User.email)
                .all())

    template_ids = [t.id for t in templates]
    certs = (db.query(TrainingCertification)
                .filter(TrainingCertification.template_id.in_(template_ids))
                .all())
    auths = (db.query(TrainerAuthorization)
                .filter(TrainerAuthorization.template_id.in_(template_ids),
                        TrainerAuthorization.revoked_at.is_(None))
                .all())

    cert_by_key = {(c.user_email, str(c.template_id)): c for c in certs}
    trainer_by_key = {(a.user_email, str(a.template_id)) for a in auths}

    return {
        "templates": [
            {
                "id": str(t.id),
                "title": t.title,
                "category": t.category,
                "training_material_url": t.training_material_url,
                "expires_kind": t.expires_kind,
                "expires_value": t.expires_value,
                "expires_on_date": str(t.expires_on_date) if t.expires_on_date else None,
            } for t in templates
        ],
        "users": [
            {"email": u.email, "display_name": u.display_name} for u in users
        ],
        "cells": [
            {
                "user_email": u.email,
                "template_id": str(t.id),
                "is_trainer": (u.email, str(t.id)) in trainer_by_key,
                "cert": _cert_dict(cert_by_key[(u.email, str(t.id))], today)
                        if (u.email, str(t.id)) in cert_by_key else None,
            }
            for u in users for t in templates
        ],
    }


# ─── My Job Responsibilities ────────────────────────────────────────

@router.get("/mine/responsibilities")
def my_responsibilities(db: Session = Depends(get_db),
                         current_user: dict = Depends(get_current_user)):
    """Every template the current user is assigned to (via groups, direct
    users list, or permission), with their certification status on each.
    Powers the 'My Job Responsibilities' view."""
    from app.services.checklist_service import _assignees_for_template
    me = (current_user.get("email") or "").lower().strip()
    user_row = db.query(User).filter(User.email == me).first()

    # Walk every active template; include those whose assignee set contains me.
    templates = db.query(TaskTemplate).filter(TaskTemplate.active.is_(True)).all()
    my_templates = []
    for t in templates:
        try:
            assignees = _assignees_for_template(db, t)
        except Exception:
            assignees = []
        if any((u.email or "").lower().strip() == me for u in assignees):
            my_templates.append(t)
        elif not assignees and t.role and user_row and t.role == (user_row.practice_role or ""):
            # Legacy role-only template with no resolvable assignees but the
            # user matches the legacy role string.
            my_templates.append(t)

    # Pull cert rows for the user, indexed by template
    certs = (db.query(TrainingCertification)
               .filter(TrainingCertification.user_email == me).all())
    cert_by_template = {str(c.template_id): c for c in certs}

    today = _date.today()
    items = []
    for t in my_templates:
        c = cert_by_template.get(str(t.id))
        if c:
            status = c.status
            trained = (status == "active"
                       and c.revoked_at is None
                       and not (c.expires_on and c.expires_on < today))
        else:
            status = "none"
            trained = False
        items.append({
            "template_id":          str(t.id),
            "title":                t.title,
            "question_text":        t.question_text,
            "category":             t.category,
            "priority":             t.priority,
            "requires_training":    bool(t.requires_training),
            "trained":              trained,
            "status":               status,
            "trainer_email":        c.trainer_email      if c else None,
            "trainer_signed_at":    (c.trainer_signed_at.isoformat()
                                       if (c and c.trainer_signed_at) else None),
            "trainee_signed_at":    (c.trainee_signed_at.isoformat()
                                       if (c and c.trainee_signed_at) else None),
            "expires_on":           str(c.expires_on) if (c and c.expires_on) else None,
            "training_material_url": t.training_material_url,
        })
    # Sort: untrained first (sorted by category then title), then trained.
    items.sort(key=lambda x: (1 if x["trained"] else 0, x["category"] or "", x["title"] or ""))

    return {
        "user": {
            "email": me,
            "display_name": user_row.display_name if user_row else None,
            "practice_role": user_row.practice_role if user_row else None,
        },
        "items": items,
        "summary": {
            "total":   len(items),
            "trained": sum(1 for x in items if x["trained"]),
            "untrained": sum(1 for x in items if not x["trained"]),
        },
    }


@router.get("/mine/responsibilities.pdf")
def my_responsibilities_pdf(db: Session = Depends(get_db),
                              current_user: dict = Depends(get_current_user)):
    """Print-friendly PDF of the 'My Job Responsibilities' table."""
    from fastapi.responses import Response
    from app.services.responsibilities_pdf import build_responsibilities_pdf
    payload = my_responsibilities(db=db, current_user=current_user)
    pdf = build_responsibilities_pdf(payload)
    name = (payload["user"].get("display_name")
              or payload["user"]["email"].split("@")[0]
              or "user").replace(" ", "_")
    fname = f"my-job-responsibilities-{name}-{_date.today().isoformat()}.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": content_disposition(fname, "inline")},
    )
