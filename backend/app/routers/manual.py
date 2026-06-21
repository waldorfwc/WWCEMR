"""Unified operating-manual router.

Single endpoint set for all modules' in-app manual/SOP content.
Module is specified as a query-param (GET) or body field (POST).
Per-module tier gating is enforced at runtime — VIEW to read, MANAGE to write.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.manual import ManualSection
from app.routers.auth import get_current_user
from app.permissions.catalog import Module, Tier, MODULE_REGISTRY
from app.permissions.resolver import effective_tier
from app.services.audit_service import log_action

router = APIRouter(prefix="/manual", tags=["manual"])

# Map module string value → Module enum member (e.g. "surgery" → Module.SURGERY)
MODULE_BY_KEY: dict[str, Module] = {m.value: m for m in Module}


def _resolve_module(module: str) -> Module:
    m = MODULE_BY_KEY.get(module)
    if not m:
        raise HTTPException(status_code=400, detail=f"unknown module '{module}'")
    return m


def _assert_tier(db: Session, current_user: dict, module: Module, min_tier: Tier):
    email = (current_user.get("email") or "").lower().strip()
    actual = effective_tier(db, email, module)
    if actual < min_tier:
        label = MODULE_REGISTRY[module].label
        raise HTTPException(
            status_code=403,
            detail=f"forbidden — needs {min_tier.name.title()} on {label}",
        )


def _section_dict(s: ManualSection) -> dict:
    return {
        "id":         str(s.id),
        "slug":       s.slug,
        "title":      s.title,
        "body_md":    s.body_md,
        "sort_order": s.sort_order,
        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
        "updated_by": s.updated_by,
    }


# ──────────────────────────────────────────────────────────────────────────────
# GET /api/manual?module=<value>
# ──────────────────────────────────────────────────────────────────────────────

@router.get("")
def list_sections(
    module: str = Query(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    m = _resolve_module(module)
    _assert_tier(db, current_user, m, Tier.VIEW)
    rows = (
        db.query(ManualSection)
          .filter_by(module=module)
          .order_by(ManualSection.sort_order, ManualSection.title)
          .all()
    )
    return [_section_dict(s) for s in rows]


# ──────────────────────────────────────────────────────────────────────────────
# POST /api/manual
# ──────────────────────────────────────────────────────────────────────────────

class SectionIn(BaseModel):
    module: str
    slug: str
    title: str
    body_md: str = ""
    sort_order: int = 0


@router.post("", status_code=201)
def create_section(
    payload: SectionIn,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    m = _resolve_module(payload.module)
    _assert_tier(db, current_user, m, Tier.MANAGE)
    slug = payload.slug.strip().lower().replace(" ", "-")
    if not slug or not payload.title.strip():
        raise HTTPException(status_code=422, detail="slug and title are required")
    if db.query(ManualSection).filter_by(module=payload.module, slug=slug).first():
        raise HTTPException(status_code=409, detail=f"section '{slug}' already exists")
    row = ManualSection(
        module=payload.module,
        slug=slug,
        title=payload.title.strip(),
        body_md=payload.body_md,
        sort_order=payload.sort_order,
        updated_by=current_user.get("email") or "system",
    )
    db.add(row)
    db.flush()  # populate row.id before logging
    log_action(
        db, "manual_section_created", "manual_section",
        actor=current_user,
        resource_id=str(row.id),
        description=f"Created {payload.module} manual section {row.slug!r}",
        new_values={"module": payload.module, "slug": row.slug, "title": row.title},
        defer_commit=True,
    )
    db.commit()
    db.refresh(row)
    return {"id": str(row.id), "slug": row.slug}


# ──────────────────────────────────────────────────────────────────────────────
# PATCH /api/manual/{section_id}
# ──────────────────────────────────────────────────────────────────────────────

class SectionPatch(BaseModel):
    title: Optional[str] = None
    body_md: Optional[str] = None
    sort_order: Optional[int] = None


@router.patch("/{section_id}")
def patch_section(
    section_id: str,
    payload: SectionPatch,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    s = db.query(ManualSection).filter_by(id=section_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="section not found")
    _assert_tier(db, current_user, _resolve_module(s.module), Tier.MANAGE)
    old = {"title": s.title, "sort_order": s.sort_order}
    changes = payload.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(s, k, v)
    s.updated_by = current_user.get("email") or "system"
    log_action(
        db, "manual_section_updated", "manual_section",
        actor=current_user,
        resource_id=str(s.id),
        description=f"Edited {s.module} manual section {s.slug!r}",
        old_values=old,
        new_values=changes,
        defer_commit=True,
    )
    db.commit()
    db.refresh(s)
    return {"id": str(s.id)}


# ──────────────────────────────────────────────────────────────────────────────
# DELETE /api/manual/{section_id}
# ──────────────────────────────────────────────────────────────────────────────

@router.delete("/{section_id}", status_code=204)
def delete_section(
    section_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    s = db.query(ManualSection).filter_by(id=section_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="section not found")
    _assert_tier(db, current_user, _resolve_module(s.module), Tier.MANAGE)
    log_action(
        db, "manual_section_deleted", "manual_section",
        actor=current_user,
        resource_id=str(s.id),
        description=f"Deleted {s.module} manual section {s.slug!r}",
        old_values={
            "module": s.module,
            "slug": s.slug,
            "title": s.title,
            "body_excerpt": (s.body_md or "")[:240],
        },
        defer_commit=True,
    )
    db.delete(s)
    db.commit()
