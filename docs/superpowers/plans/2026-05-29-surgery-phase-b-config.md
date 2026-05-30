# Surgery Phase B — Configuration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace hardcoded surgery values (release thresholds, recipient lists, facility identifiers, procedure durations) with admin-managed configuration. Wire `SurgeryRules` (`/surgery/rules`) as a tabbed admin page.

**Architecture:** Four small tables + four admin endpoints. Each table follows the existing audit pattern (created_by/at, updated_by/at). The frontend swaps hardcoded `FACILITY_LABEL` dicts for a query-cached picklist.

**Tech Stack:** FastAPI + SQLAlchemy, pytest with sqlite-in-memory, React + TanStack Query, lucide-react icons.

**Depends on:** Phase A (no hard dep, but ship A first so this phase lands cleanly on a working calendar/waitlist).

---

## File Structure

- **Create:** `backend/app/models/surgery_config.py` — new module for the four config models.
- **Create:** `backend/app/routers/surgery_config.py` — new router for all admin/picklist endpoints.
- **Create:** `backend/tests/test_surgery_config.py` — coverage for the four endpoints.
- **Modify:** `backend/app/main.py` — register the router + import the model module so `init_db` picks up the tables.
- **Modify:** `backend/app/services/surgery_release_alerts.py` — read thresholds + recipients from config, fall back to current behavior if empty.
- **Modify:** `frontend/src/pages/SurgeryRules.jsx` — add four tabs.
- **Create:** `frontend/src/hooks/useFacilities.js` — TanStack-Query-backed hook that returns the facility list (used everywhere the hardcoded `FACILITY_LABEL` map appears).
- **Modify:** all frontend files that import `FACILITY_LABEL` — swap to `useFacilities()`. This is a wide change; we do it as a separate task with a grep audit.

---

## Section 1 — Models

### Task B1: Define the four config models

**Files:**
- Create: `backend/app/models/surgery_config.py`

- [ ] **Step 1: Create the file**

`backend/app/models/surgery_config.py`:

```python
"""Surgery module configuration tables (Phase B).

Four small tables back the admin UI on /surgery/rules:

  surgery_config              — key/value store for thresholds
                                (office_full_threshold, office_lookahead_days,
                                hospital_lookahead_days, etc.)
  surgery_alert_recipients    — per-alert email lists
                                (office_release, hospital_release)
  facilities                  — replaces hardcoded FACILITY_LABEL dicts
                                across the codebase
  surgery_procedure_templates — default durations + CPT for each procedure
                                kind, used by the coordinator override flow
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean, Column, DateTime, Integer, JSON, String, Text, UniqueConstraint,
)

from app.database import Base
from app.models.guid import GUID, new_uuid


class SurgeryConfig(Base):
    __tablename__ = "surgery_config"

    key        = Column(String(60), primary_key=True)
    value      = Column(JSON, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow,
                            onupdate=datetime.utcnow, nullable=False)
    updated_by = Column(String(120), nullable=True)


class SurgeryAlertRecipient(Base):
    __tablename__ = "surgery_alert_recipients"
    __table_args__ = (
        UniqueConstraint("alert_kind", "email", name="uq_alert_recip_kind_email"),
    )

    id         = Column(GUID(), primary_key=True, default=new_uuid)
    alert_kind = Column(String(40), nullable=False)
    # values: office_release | hospital_release
    email      = Column(String(200), nullable=False)
    added_by   = Column(String(120), nullable=True)
    added_at   = Column(DateTime, default=datetime.utcnow, nullable=False)


class Facility(Base):
    __tablename__ = "facilities"
    __table_args__ = (
        UniqueConstraint("code", name="uq_facility_code"),
    )

    id         = Column(GUID(), primary_key=True, default=new_uuid)
    code       = Column(String(20), nullable=False)
    label      = Column(String(120), nullable=False)
    address    = Column(Text, nullable=True)
    is_active  = Column(Boolean, default=True, nullable=False)
    sort_order = Column(Integer, default=100, nullable=False)
    created_by = Column(String(120), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_by = Column(String(120), nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow,
                            onupdate=datetime.utcnow, nullable=False)


class SurgeryProcedureTemplate(Base):
    __tablename__ = "surgery_procedure_templates"
    __table_args__ = (
        UniqueConstraint("code", name="uq_proc_template_code"),
    )

    id                       = Column(GUID(), primary_key=True, default=new_uuid)
    code                     = Column(String(40), nullable=False)
    name                     = Column(String(200), nullable=False)
    procedure_kind           = Column(String(20), nullable=False)
    # values: minor | major | office | robotic_180 | robotic_240
    default_duration_minutes = Column(Integer, nullable=False)
    default_cpt_code         = Column(String(20), nullable=True)
    is_active                = Column(Boolean, default=True, nullable=False)
    created_by               = Column(String(120), nullable=True)
    created_at               = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_by               = Column(String(120), nullable=True)
    updated_at               = Column(DateTime, default=datetime.utcnow,
                                          onupdate=datetime.utcnow, nullable=False)
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/models/surgery_config.py
git commit -m "feat(surgery): config models — SurgeryConfig, AlertRecipient, Facility, ProcedureTemplate"
```

---

## Section 2 — Router

### Task B2: Pydantic schemas + router skeleton

**Files:**
- Create: `backend/app/routers/surgery_config.py`

- [ ] **Step 1: Create the file with schemas + router prefix**

```python
"""Surgery module config + admin endpoints (Phase B).

Permissions:
  GET picklist endpoints                          claim:read
  All admin endpoints (POST/PUT/PATCH/DELETE)     user:manage
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.surgery_config import (
    SurgeryConfig, SurgeryAlertRecipient, Facility, SurgeryProcedureTemplate,
)
from app.routers.auth import require_permission


router = APIRouter(prefix="/surgery", tags=["surgery-config"])


# ─── Defaults (used when a config key has no row yet) ───────────────

CONFIG_DEFAULTS = {
    "office_full_threshold":     6,
    "office_lookahead_days":     6,
    "hospital_lookahead_days":  14,
}

ALERT_KINDS = ("office_release", "hospital_release")
PROCEDURE_KINDS = ("minor", "major", "office", "robotic_180", "robotic_240")


# ─── Pydantic shapes ────────────────────────────────────────────────

class ConfigPayload(BaseModel):
    office_full_threshold:     Optional[int] = None
    office_lookahead_days:     Optional[int] = None
    hospital_lookahead_days:   Optional[int] = None


class RecipientIn(BaseModel):
    alert_kind: str
    email: str


class FacilityIn(BaseModel):
    code: str
    label: str
    address: Optional[str] = None
    is_active: bool = True
    sort_order: int = 100


class FacilityPatch(BaseModel):
    code: Optional[str] = None
    label: Optional[str] = None
    address: Optional[str] = None
    is_active: Optional[bool] = None
    sort_order: Optional[int] = None


class TemplateIn(BaseModel):
    code: str
    name: str
    procedure_kind: str
    default_duration_minutes: int
    default_cpt_code: Optional[str] = None
    is_active: bool = True


class TemplatePatch(BaseModel):
    code: Optional[str] = None
    name: Optional[str] = None
    procedure_kind: Optional[str] = None
    default_duration_minutes: Optional[int] = None
    default_cpt_code: Optional[str] = None
    is_active: Optional[bool] = None
```

- [ ] **Step 2: Commit (scaffold only)**

```bash
git add backend/app/routers/surgery_config.py
git commit -m "feat(surgery): scaffold surgery-config router (schemas + defaults)"
```

---

### Task B3: Config GET/PUT endpoints + test

**Files:**
- Modify: `backend/app/routers/surgery_config.py`
- Create: `backend/tests/test_surgery_config.py`

- [ ] **Step 1: Write the failing test**

`backend/tests/test_surgery_config.py`:

```python
"""Phase B config endpoints — coverage for the four admin areas."""

def test_get_config_returns_defaults_when_empty(client):
    resp = client.get("/api/surgery/config")
    assert resp.status_code == 200
    body = resp.json()
    assert body["office_full_threshold"] == 6
    assert body["office_lookahead_days"] == 6
    assert body["hospital_lookahead_days"] == 14


def test_put_config_persists_values(client):
    resp = client.put("/api/surgery/config", json={
        "office_full_threshold": 8,
        "hospital_lookahead_days": 21,
    })
    assert resp.status_code == 200
    body = client.get("/api/surgery/config").json()
    assert body["office_full_threshold"] == 8
    assert body["office_lookahead_days"] == 6      # untouched, falls back to default
    assert body["hospital_lookahead_days"] == 21


def test_put_config_rejects_unknown_key(client):
    # Unknown keys silently ignored — Pydantic discards them.
    resp = client.put("/api/surgery/config", json={"bogus_key": 9000})
    assert resp.status_code == 200
    body = client.get("/api/surgery/config").json()
    assert "bogus_key" not in body
```

Run it:

```bash
cd backend && ./venv/bin/pytest tests/test_surgery_config.py::test_get_config_returns_defaults_when_empty -v
```

Expected: FAIL with "404 Not Found" (endpoint doesn't exist yet).

- [ ] **Step 2: Implement the endpoints**

Append to `backend/app/routers/surgery_config.py`:

```python
# ─── Config (key/value) ─────────────────────────────────────────────

def _read_config(db: Session) -> dict:
    rows = db.query(SurgeryConfig).all()
    out = dict(CONFIG_DEFAULTS)
    for r in rows:
        out[r.key] = r.value
    return out


@router.get("/config")
def get_config(db: Session = Depends(get_db),
               current_user: dict = Depends(require_permission("claim:read"))):
    return _read_config(db)


@router.put("/config")
def put_config(payload: ConfigPayload,
               db: Session = Depends(get_db),
               current_user: dict = Depends(require_permission("user:manage"))):
    actor = current_user.get("email") or "system"
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        if k not in CONFIG_DEFAULTS:
            continue
        row = db.query(SurgeryConfig).filter(SurgeryConfig.key == k).first()
        if row is None:
            db.add(SurgeryConfig(key=k, value=v, updated_by=actor))
        else:
            row.value = v
            row.updated_by = actor
    db.commit()
    return _read_config(db)
```

- [ ] **Step 3: Register the router in `backend/app/main.py`**

Add to the imports near other router imports:

```python
from app.routers import surgery_config
```

Add to the include_router block (near the surgery router registration):

```python
app.include_router(surgery_config.router, prefix="/api")
```

- [ ] **Step 4: Re-run the three config tests**

```bash
cd backend && ./venv/bin/pytest tests/test_surgery_config.py -v -k config
```

Expected: 3 passes.

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/surgery_config.py backend/app/main.py backend/tests/test_surgery_config.py
git commit -m "feat(surgery): config GET/PUT endpoints with defaults"
```

---

### Task B4: Alert recipients endpoints + tests

**Files:**
- Modify: `backend/app/routers/surgery_config.py`
- Modify: `backend/tests/test_surgery_config.py`

- [ ] **Step 1: Append failing tests**

```python
def test_recipients_empty_by_default(client):
    resp = client.get("/api/surgery/admin/alert-recipients")
    assert resp.status_code == 200
    assert resp.json() == {"office_release": [], "hospital_release": []}


def test_add_recipient(client):
    resp = client.post("/api/surgery/admin/alert-recipients",
                       json={"alert_kind": "office_release",
                              "email": "manager@waldorfwomenscare.com"})
    assert resp.status_code == 201
    out = client.get("/api/surgery/admin/alert-recipients").json()
    assert "manager@waldorfwomenscare.com" in out["office_release"]


def test_dup_recipient_returns_409(client):
    client.post("/api/surgery/admin/alert-recipients",
                json={"alert_kind": "office_release", "email": "a@b.com"})
    resp = client.post("/api/surgery/admin/alert-recipients",
                       json={"alert_kind": "office_release", "email": "a@b.com"})
    assert resp.status_code == 409


def test_unknown_alert_kind_returns_422(client):
    resp = client.post("/api/surgery/admin/alert-recipients",
                       json={"alert_kind": "totally_made_up", "email": "x@y.com"})
    assert resp.status_code == 422


def test_delete_recipient(client):
    client.post("/api/surgery/admin/alert-recipients",
                json={"alert_kind": "office_release", "email": "x@y.com"})
    resp = client.delete("/api/surgery/admin/alert-recipients",
                          params={"alert_kind": "office_release", "email": "x@y.com"})
    assert resp.status_code == 204
    out = client.get("/api/surgery/admin/alert-recipients").json()
    assert out["office_release"] == []
```

- [ ] **Step 2: Append endpoint implementations**

```python
# ─── Alert recipients ───────────────────────────────────────────────

@router.get("/admin/alert-recipients")
def list_recipients(db: Session = Depends(get_db),
                    current_user: dict = Depends(require_permission("claim:read"))):
    rows = db.query(SurgeryAlertRecipient).all()
    out = {k: [] for k in ALERT_KINDS}
    for r in rows:
        out.setdefault(r.alert_kind, []).append(r.email)
    for k in out:
        out[k].sort()
    return out


@router.post("/admin/alert-recipients", status_code=201)
def add_recipient(payload: RecipientIn,
                  db: Session = Depends(get_db),
                  current_user: dict = Depends(require_permission("user:manage"))):
    if payload.alert_kind not in ALERT_KINDS:
        raise HTTPException(status_code=422,
                            detail=f"unknown alert_kind: {payload.alert_kind}")
    email = payload.email.strip().lower()
    if not email:
        raise HTTPException(status_code=422, detail="email required")
    actor = current_user.get("email") or "system"
    exists = (db.query(SurgeryAlertRecipient)
                .filter(SurgeryAlertRecipient.alert_kind == payload.alert_kind,
                         SurgeryAlertRecipient.email == email).first())
    if exists:
        raise HTTPException(status_code=409, detail="recipient already exists")
    row = SurgeryAlertRecipient(alert_kind=payload.alert_kind,
                                  email=email, added_by=actor)
    db.add(row)
    db.commit()
    return {"id": str(row.id), "alert_kind": row.alert_kind, "email": row.email}


@router.delete("/admin/alert-recipients", status_code=204)
def delete_recipient(alert_kind: str, email: str,
                     db: Session = Depends(get_db),
                     current_user: dict = Depends(require_permission("user:manage"))):
    row = (db.query(SurgeryAlertRecipient)
             .filter(SurgeryAlertRecipient.alert_kind == alert_kind,
                      SurgeryAlertRecipient.email == email.strip().lower())
             .first())
    if row:
        db.delete(row)
        db.commit()
    return None
```

- [ ] **Step 3: Run the 5 recipient tests**

```bash
cd backend && ./venv/bin/pytest tests/test_surgery_config.py -v -k recipient
```

Expected: 5 passes.

- [ ] **Step 4: Commit**

```bash
git add backend/app/routers/surgery_config.py backend/tests/test_surgery_config.py
git commit -m "feat(surgery): alert-recipients admin endpoints"
```

---

### Task B5: Facilities CRUD + picklist

**Files:**
- Modify: `backend/app/routers/surgery_config.py`
- Modify: `backend/tests/test_surgery_config.py`

- [ ] **Step 1: Append failing tests**

```python
def test_facility_crud_round_trip(client):
    # Create
    resp = client.post("/api/surgery/admin/facilities", json={
        "code": "medstar", "label": "MedStar Southern Maryland",
        "address": "7503 Surratts Rd, Clinton, MD",
        "sort_order": 1,
    })
    assert resp.status_code == 201
    fid = resp.json()["id"]

    # List
    out = client.get("/api/surgery/admin/facilities").json()
    assert any(f["code"] == "medstar" for f in out["facilities"])

    # Patch
    resp = client.patch(f"/api/surgery/admin/facilities/{fid}", json={"label": "MedStar SMH"})
    assert resp.status_code == 200
    assert resp.json()["label"] == "MedStar SMH"

    # Picklist (claim:read) returns only active facilities, sorted
    out = client.get("/api/surgery/picklists/facilities").json()
    codes = [f["code"] for f in out["facilities"]]
    assert "medstar" in codes

    # Deactivate
    client.patch(f"/api/surgery/admin/facilities/{fid}", json={"is_active": False})
    out = client.get("/api/surgery/picklists/facilities").json()
    assert "medstar" not in [f["code"] for f in out["facilities"]]


def test_facility_dup_code_returns_409(client):
    client.post("/api/surgery/admin/facilities", json={"code": "office", "label": "Office"})
    resp = client.post("/api/surgery/admin/facilities", json={"code": "office", "label": "Office 2"})
    assert resp.status_code == 409
```

- [ ] **Step 2: Append endpoints**

```python
# ─── Facilities ─────────────────────────────────────────────────────

def _facility_dict(f: Facility) -> dict:
    return {"id": str(f.id), "code": f.code, "label": f.label,
            "address": f.address, "is_active": f.is_active,
            "sort_order": f.sort_order}


@router.get("/admin/facilities")
def list_facilities_admin(db: Session = Depends(get_db),
                           current_user: dict = Depends(require_permission("claim:read"))):
    rows = (db.query(Facility)
              .order_by(Facility.sort_order.asc(), Facility.label.asc()).all())
    return {"facilities": [_facility_dict(f) for f in rows]}


@router.get("/picklists/facilities")
def list_facilities_picklist(db: Session = Depends(get_db),
                              current_user: dict = Depends(require_permission("claim:read"))):
    rows = (db.query(Facility)
              .filter(Facility.is_active.is_(True))
              .order_by(Facility.sort_order.asc(), Facility.label.asc()).all())
    return {"facilities": [_facility_dict(f) for f in rows]}


@router.post("/admin/facilities", status_code=201)
def create_facility(payload: FacilityIn,
                    db: Session = Depends(get_db),
                    current_user: dict = Depends(require_permission("user:manage"))):
    code = (payload.code or "").strip().lower()
    label = (payload.label or "").strip()
    if not code or not label:
        raise HTTPException(status_code=422, detail="code and label required")
    if db.query(Facility).filter(Facility.code == code).first():
        raise HTTPException(status_code=409, detail="code already exists")
    actor = current_user.get("email") or "system"
    f = Facility(code=code, label=label, address=payload.address,
                  is_active=payload.is_active, sort_order=payload.sort_order,
                  created_by=actor, updated_by=actor)
    db.add(f); db.commit(); db.refresh(f)
    return _facility_dict(f)


@router.patch("/admin/facilities/{facility_id}")
def patch_facility(facility_id: str, payload: FacilityPatch,
                   db: Session = Depends(get_db),
                   current_user: dict = Depends(require_permission("user:manage"))):
    f = db.query(Facility).filter(Facility.id == facility_id).first()
    if not f:
        raise HTTPException(status_code=404, detail="facility not found")
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(f, k, v)
    f.updated_by = current_user.get("email") or "system"
    db.commit(); db.refresh(f)
    return _facility_dict(f)


@router.delete("/admin/facilities/{facility_id}", status_code=204)
def delete_facility(facility_id: str,
                    db: Session = Depends(get_db),
                    current_user: dict = Depends(require_permission("user:manage"))):
    f = db.query(Facility).filter(Facility.id == facility_id).first()
    if f:
        db.delete(f); db.commit()
    return None
```

- [ ] **Step 3: Run the 2 facility tests**

```bash
cd backend && ./venv/bin/pytest tests/test_surgery_config.py -v -k facility
```

Expected: 2 passes.

- [ ] **Step 4: Commit**

```bash
git add backend/app/routers/surgery_config.py backend/tests/test_surgery_config.py
git commit -m "feat(surgery): facilities CRUD + picklist endpoints"
```

---

### Task B6: Procedure templates CRUD + picklist

**Files:**
- Modify: `backend/app/routers/surgery_config.py`
- Modify: `backend/tests/test_surgery_config.py`

- [ ] **Step 1: Append failing tests**

```python
def test_template_crud_round_trip(client):
    resp = client.post("/api/surgery/admin/procedure-templates", json={
        "code": "robotic_180", "name": "Robotic hysterectomy",
        "procedure_kind": "robotic_180",
        "default_duration_minutes": 180,
        "default_cpt_code": "58571",
    })
    assert resp.status_code == 201
    tid = resp.json()["id"]
    out = client.get("/api/surgery/picklists/procedure-templates").json()
    assert any(t["code"] == "robotic_180" for t in out["templates"])

    resp = client.patch(f"/api/surgery/admin/procedure-templates/{tid}",
                         json={"default_duration_minutes": 200})
    assert resp.json()["default_duration_minutes"] == 200


def test_template_unknown_kind_returns_422(client):
    resp = client.post("/api/surgery/admin/procedure-templates", json={
        "code": "bogus", "name": "Bogus", "procedure_kind": "not_a_kind",
        "default_duration_minutes": 60,
    })
    assert resp.status_code == 422
```

- [ ] **Step 2: Append endpoints** (mirror the facility endpoints; one new wrinkle is the `procedure_kind` allowlist check)

```python
# ─── Procedure templates ────────────────────────────────────────────

def _template_dict(t: SurgeryProcedureTemplate) -> dict:
    return {"id": str(t.id), "code": t.code, "name": t.name,
            "procedure_kind": t.procedure_kind,
            "default_duration_minutes": t.default_duration_minutes,
            "default_cpt_code": t.default_cpt_code,
            "is_active": t.is_active}


@router.get("/admin/procedure-templates")
def list_templates_admin(db: Session = Depends(get_db),
                          current_user: dict = Depends(require_permission("claim:read"))):
    rows = db.query(SurgeryProcedureTemplate).order_by(
        SurgeryProcedureTemplate.name.asc()).all()
    return {"templates": [_template_dict(t) for t in rows]}


@router.get("/picklists/procedure-templates")
def list_templates_picklist(db: Session = Depends(get_db),
                             current_user: dict = Depends(require_permission("claim:read"))):
    rows = (db.query(SurgeryProcedureTemplate)
              .filter(SurgeryProcedureTemplate.is_active.is_(True))
              .order_by(SurgeryProcedureTemplate.name.asc()).all())
    return {"templates": [_template_dict(t) for t in rows]}


@router.post("/admin/procedure-templates", status_code=201)
def create_template(payload: TemplateIn,
                    db: Session = Depends(get_db),
                    current_user: dict = Depends(require_permission("user:manage"))):
    if payload.procedure_kind not in PROCEDURE_KINDS:
        raise HTTPException(status_code=422,
                            detail=f"unknown procedure_kind: {payload.procedure_kind}")
    if payload.default_duration_minutes <= 0:
        raise HTTPException(status_code=422, detail="duration must be > 0")
    actor = current_user.get("email") or "system"
    if db.query(SurgeryProcedureTemplate).filter(
            SurgeryProcedureTemplate.code == payload.code).first():
        raise HTTPException(status_code=409, detail="code already exists")
    t = SurgeryProcedureTemplate(
        code=payload.code, name=payload.name,
        procedure_kind=payload.procedure_kind,
        default_duration_minutes=payload.default_duration_minutes,
        default_cpt_code=payload.default_cpt_code,
        is_active=payload.is_active, created_by=actor, updated_by=actor,
    )
    db.add(t); db.commit(); db.refresh(t)
    return _template_dict(t)


@router.patch("/admin/procedure-templates/{template_id}")
def patch_template(template_id: str, payload: TemplatePatch,
                   db: Session = Depends(get_db),
                   current_user: dict = Depends(require_permission("user:manage"))):
    t = db.query(SurgeryProcedureTemplate).filter(
            SurgeryProcedureTemplate.id == template_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="template not found")
    data = payload.model_dump(exclude_unset=True)
    if "procedure_kind" in data and data["procedure_kind"] not in PROCEDURE_KINDS:
        raise HTTPException(status_code=422,
                            detail=f"unknown procedure_kind: {data['procedure_kind']}")
    for k, v in data.items():
        setattr(t, k, v)
    t.updated_by = current_user.get("email") or "system"
    db.commit(); db.refresh(t)
    return _template_dict(t)


@router.delete("/admin/procedure-templates/{template_id}", status_code=204)
def delete_template(template_id: str,
                    db: Session = Depends(get_db),
                    current_user: dict = Depends(require_permission("user:manage"))):
    t = db.query(SurgeryProcedureTemplate).filter(
            SurgeryProcedureTemplate.id == template_id).first()
    if t:
        db.delete(t); db.commit()
    return None
```

- [ ] **Step 3: Run all config tests**

```bash
cd backend && ./venv/bin/pytest tests/test_surgery_config.py -v
```

Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add backend/app/routers/surgery_config.py backend/tests/test_surgery_config.py
git commit -m "feat(surgery): procedure-templates CRUD + picklist endpoints"
```

---

## Section 3 — Wire release alerts to config

### Task B7: `release_alerts.py` reads thresholds + recipients from config

**Files:**
- Modify: `backend/app/services/surgery_release_alerts.py`

- [ ] **Step 1: Replace the module-level constants with config reads**

In `backend/app/services/surgery_release_alerts.py`, near the top of the file, replace:

```python
HOSPITAL_LOOKAHEAD_DAYS = 14
OFFICE_LOOKAHEAD_DAYS   = 6
OFFICE_FULL_THRESHOLD   = 6
```

With:

```python
from app.models.surgery_config import (
    SurgeryConfig, SurgeryAlertRecipient,
)


_CONFIG_DEFAULTS = {
    "office_full_threshold":   6,
    "office_lookahead_days":   6,
    "hospital_lookahead_days": 14,
}


def _cfg(db, key: str):
    row = db.query(SurgeryConfig).filter(SurgeryConfig.key == key).first()
    return row.value if row else _CONFIG_DEFAULTS[key]


def _configured_recipients(db, alert_kind: str) -> list[str]:
    rows = (db.query(SurgeryAlertRecipient)
              .filter(SurgeryAlertRecipient.alert_kind == alert_kind).all())
    return [r.email for r in rows]
```

- [ ] **Step 2: Replace the constants with `_cfg()` calls everywhere they're used in this file**

Walk each `HOSPITAL_LOOKAHEAD_DAYS`, `OFFICE_LOOKAHEAD_DAYS`, `OFFICE_FULL_THRESHOLD` reference (there are 7 of them per the earlier grep). Each function that uses them now reads them through `_cfg(db, "...")` at function-entry.

For example, where the code was:

```python
end = today + timedelta(days=HOSPITAL_LOOKAHEAD_DAYS)
```

becomes:

```python
end = today + timedelta(days=_cfg(db, "hospital_lookahead_days"))
```

- [ ] **Step 3: Wire the recipient fallback**

In `_scheduler_recipients` and `_office_manager_recipients`, return whichever wins: configured override OR existing role-based query. Concretely, find the call site that builds the union of schedulers + office managers (the existing function `_combined_office_recipients` per code), and prepend the configured list. Add a new top-level helper:

```python
def _office_release_recipients(db) -> list:
    """Return the configured list if non-empty; otherwise fall back to the
    role-based query (schedulers + office managers). Falling back means we
    never silently lose alerts during rollout."""
    configured = _configured_recipients(db, "office_release")
    if configured:
        return [User(email=e, notify_email=True, display_name=e) for e in configured]
    schedulers = _scheduler_recipients(db)
    managers   = _office_manager_recipients(db)
    seen = {}
    for u in schedulers + managers:
        if u.email not in seen:
            seen[u.email] = u
    return list(seen.values())


def _hospital_release_recipients(db) -> list:
    configured = _configured_recipients(db, "hospital_release")
    if configured:
        return [User(email=e, notify_email=True, display_name=e) for e in configured]
    return _scheduler_recipients(db)
```

Then update the cron entry-point functions (`send_office_release_alerts`, `send_hospital_release_alerts`, or whatever the current names are) to use these helpers instead of calling `_scheduler_recipients` / `_office_manager_recipients` directly.

- [ ] **Step 4: Smoke run the existing test**

If a `tests/test_surgery_release_alerts.py` exists, run it. If not, just exercise the module via the import:

```bash
cd backend && ./venv/bin/python -c "from app.services.surgery_release_alerts import _cfg; print('ok')"
```

Expected: prints `ok` (no import errors).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/surgery_release_alerts.py
git commit -m "feat(surgery): release_alerts reads thresholds + recipients from config"
```

---

## Section 4 — Frontend admin UI

### Task B8: Tabbed `SurgeryRules` page

**Files:**
- Modify: `frontend/src/pages/SurgeryRules.jsx`

- [ ] **Step 1: Wrap the existing rules content in a tabs layout**

Open `frontend/src/pages/SurgeryRules.jsx`. Wrap the existing component's main render in a tab container with 5 tabs. The first tab keeps the existing content (Milestone rules); the other 4 hold the new admin areas:

```jsx
import { useState } from 'react'
import { Sliders, Mail, Building2, Stethoscope, ListChecks } from 'lucide-react'
// ... existing imports preserved ...

const TABS = [
  { k: 'milestones',  label: 'Milestone rules',  icon: ListChecks },
  { k: 'thresholds',  label: 'Thresholds',       icon: Sliders },
  { k: 'recipients',  label: 'Alert recipients', icon: Mail },
  { k: 'facilities',  label: 'Facilities',       icon: Building2 },
  { k: 'templates',   label: 'Procedure templates', icon: Stethoscope },
]

export default function SurgeryRules() {
  const [tab, setTab] = useState('milestones')
  return (
    <div>
      <h1 className="text-2xl font-bold text-gray-900 mb-4">Surgery rules</h1>
      <div className="flex gap-1 border-b border-border-subtle mb-4">
        {TABS.map(t => {
          const Icon = t.icon
          return (
            <button key={t.k}
                    onClick={() => setTab(t.k)}
                    className={`flex items-center gap-1.5 px-4 py-2 text-sm font-medium border-b-2 transition ${
                      tab === t.k
                        ? 'border-plum-600 text-plum-700'
                        : 'border-transparent text-gray-500 hover:text-plum-700 hover:border-plum-200'
                    }`}>
              <Icon size={14} /> {t.label}
            </button>
          )
        })}
      </div>
      {tab === 'milestones'  && <MilestoneRulesTab />}
      {tab === 'thresholds'  && <ThresholdsTab />}
      {tab === 'recipients'  && <RecipientsTab />}
      {tab === 'facilities'  && <FacilitiesTab />}
      {tab === 'templates'   && <TemplatesTab />}
    </div>
  )
}
```

(Extract the existing rules render into a new `MilestoneRulesTab()` component to preserve current behavior. No logic change.)

- [ ] **Step 2: Commit the skeleton**

```bash
git add frontend/src/pages/SurgeryRules.jsx
git commit -m "feat(surgery): SurgeryRules tabbed layout"
```

---

### Task B9: `ThresholdsTab` component

**Files:**
- Modify: `frontend/src/pages/SurgeryRules.jsx`

- [ ] **Step 1: Add the component**

Append to `SurgeryRules.jsx`:

```jsx
function ThresholdsTab() {
  const qc = useQueryClient()
  const { data } = useQuery({
    queryKey: ['surgery-config'],
    queryFn: () => api.get('/surgery/config').then(r => r.data),
  })
  const [draft, setDraft] = useState(null)
  const live = draft || data || {}

  const save = useMutation({
    mutationFn: (body) => api.put('/surgery/config', body).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['surgery-config'] })
      setDraft(null)
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Save failed'),
  })

  function field(key, label, hint) {
    return (
      <div className="flex items-center gap-3">
        <label className="text-[12px] text-gray-600 w-56">{label}</label>
        <input type="number" min="1" className="input text-sm w-24"
               value={live[key] ?? ''}
               onChange={e => setDraft({ ...live, [key]: Number(e.target.value) })} />
        {hint && <span className="text-[11px] text-gray-400">{hint}</span>}
      </div>
    )
  }

  return (
    <div className="bg-white rounded-lg border border-border-subtle p-5 max-w-2xl">
      <h2 className="text-base font-semibold mb-3">Release-alert thresholds</h2>
      <div className="space-y-3">
        {field('office_full_threshold',   'Office full threshold', '(<this = release the rest)')}
        {field('office_lookahead_days',   'Office lookahead days', '(fire alert this many days ahead)')}
        {field('hospital_lookahead_days', 'Hospital lookahead days', '(scan empty hospital days within this window)')}
      </div>
      <div className="mt-4 flex items-center gap-2">
        <button className="btn-primary text-sm" disabled={!draft || save.isPending}
                onClick={() => save.mutate(draft)}>
          {save.isPending ? 'Saving…' : 'Save'}
        </button>
        {draft && (
          <button className="btn-secondary text-sm" onClick={() => setDraft(null)}>Cancel</button>
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Verify build**

```bash
cd frontend && npm run build 2>&1 | tail -5
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/SurgeryRules.jsx
git commit -m "feat(surgery): rules — Thresholds tab"
```

---

### Task B10: `RecipientsTab` component

**Files:**
- Modify: `frontend/src/pages/SurgeryRules.jsx`

- [ ] **Step 1: Add the component**

```jsx
function RecipientsTab() {
  const qc = useQueryClient()
  const { data } = useQuery({
    queryKey: ['surgery-recipients'],
    queryFn: () => api.get('/surgery/admin/alert-recipients').then(r => r.data),
  })
  const add = useMutation({
    mutationFn: ({ alert_kind, email }) =>
      api.post('/surgery/admin/alert-recipients', { alert_kind, email }).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['surgery-recipients'] }),
    onError: (e) => alert(e?.response?.data?.detail || 'Add failed'),
  })
  const remove = useMutation({
    mutationFn: ({ alert_kind, email }) =>
      api.delete('/surgery/admin/alert-recipients', { params: { alert_kind, email } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['surgery-recipients'] }),
  })

  function ListEditor({ title, kind, hint }) {
    const [draft, setDraft] = useState('')
    const list = data?.[kind] || []
    return (
      <div className="bg-white rounded-lg border border-border-subtle p-5 max-w-xl mb-3">
        <h3 className="text-sm font-semibold mb-1">{title}</h3>
        <p className="text-[11px] text-gray-500 mb-3">{hint}</p>
        <div className="flex items-center gap-2 mb-3">
          <input className="input text-sm flex-1"
                 placeholder="someone@waldorfwomenscare.com"
                 value={draft} onChange={e => setDraft(e.target.value)} />
          <button className="btn-primary text-sm" disabled={!draft.trim()}
                  onClick={() => { add.mutate({ alert_kind: kind, email: draft.trim() }); setDraft('') }}>
            Add
          </button>
        </div>
        {list.length === 0 ? (
          <div className="text-[11px] text-gray-400 italic">
            No configured recipients — falling back to role-based query.
          </div>
        ) : (
          <ul className="space-y-1">
            {list.map(e => (
              <li key={e} className="flex items-center justify-between text-[12px]">
                <span>{e}</span>
                <button onClick={() => remove.mutate({ alert_kind: kind, email: e })}
                        className="text-red-600 text-[11px] hover:underline">Remove</button>
              </li>
            ))}
          </ul>
        )}
      </div>
    )
  }

  return (
    <div>
      <ListEditor title="Office release alert"   kind="office_release"
                   hint="Notified when an office procedure day is short on bookings." />
      <ListEditor title="Hospital release alert" kind="hospital_release"
                   hint="Notified when a hospital block day is fully empty." />
    </div>
  )
}
```

- [ ] **Step 2: Build + commit**

```bash
cd frontend && npm run build 2>&1 | tail -5
git add frontend/src/pages/SurgeryRules.jsx
git commit -m "feat(surgery): rules — Alert recipients tab"
```

---

### Task B11: `FacilitiesTab` component

**Files:**
- Modify: `frontend/src/pages/SurgeryRules.jsx`

- [ ] **Step 1: Add the component**

Use the same inline-edit pattern as the Insurance Contacts module (Phase A reference). Each row in the facilities table has: `code`, `label`, `address`, `is_active` toggle, `sort_order`. Add row button at the top.

Implement using the same pattern as `InsuranceContacts.jsx` — the user has already approved that UX. Reuse the layout: card → table → editable rows with Save/Cancel.

Endpoint mapping:
- List → `GET /api/surgery/admin/facilities`
- Create → `POST /api/surgery/admin/facilities`
- Edit → `PATCH /api/surgery/admin/facilities/{id}`
- Delete → `DELETE /api/surgery/admin/facilities/{id}`

- [ ] **Step 2: Seed the existing 3 facilities**

To preserve current behavior, the user (or this task) populates the table with the 3 existing facilities (`office`, `medstar`, `crmc`). Add a one-shot seed function called from `init_db()` or run via a script:

```python
# backend/app/services/surgery_config_seed.py
from sqlalchemy.orm import Session
from app.models.surgery_config import Facility

DEFAULT_FACILITIES = [
    {"code": "office",  "label": "WWC Office — White Plains",
     "address": "White Plains, MD", "sort_order": 1},
    {"code": "medstar", "label": "MedStar Southern Maryland Hospital",
     "address": "7503 Surratts Rd, Clinton, MD", "sort_order": 2},
    {"code": "crmc",    "label": "University of MD Charles Regional",
     "address": "5 Garrett Ave, La Plata, MD", "sort_order": 3},
]

def seed_default_facilities(db: Session) -> int:
    inserted = 0
    for f in DEFAULT_FACILITIES:
        exists = db.query(Facility).filter(Facility.code == f["code"]).first()
        if exists:
            continue
        db.add(Facility(**f, created_by="seed", updated_by="seed"))
        inserted += 1
    if inserted:
        db.commit()
    return inserted
```

Call this from `init_db()` in `backend/app/database.py` (after `create_all`):

```python
def init_db():
    Base.metadata.create_all(bind=engine)
    # Seed surgery facilities once (idempotent).
    try:
        from app.services.surgery_config_seed import seed_default_facilities
        with SessionLocal() as db:
            seed_default_facilities(db)
    except Exception:
        pass
```

- [ ] **Step 3: Build + commit**

```bash
cd frontend && npm run build 2>&1 | tail -5
git add frontend/src/pages/SurgeryRules.jsx backend/app/services/surgery_config_seed.py backend/app/database.py
git commit -m "feat(surgery): rules — Facilities tab + default seed (office/medstar/crmc)"
```

---

### Task B12: `TemplatesTab` component

**Files:**
- Modify: `frontend/src/pages/SurgeryRules.jsx`

- [ ] **Step 1: Add the component**

Same inline-edit pattern as `FacilitiesTab`. Columns: `name`, `procedure_kind` (dropdown), `default_duration_minutes` (number), `default_cpt_code`, `is_active`.

Endpoint mapping:
- List → `GET /api/surgery/admin/procedure-templates`
- Create → `POST /api/surgery/admin/procedure-templates`
- Edit → `PATCH /api/surgery/admin/procedure-templates/{id}`
- Delete → `DELETE /api/surgery/admin/procedure-templates/{id}`

- [ ] **Step 2: Seed defaults**

Add to `backend/app/services/surgery_config_seed.py`:

```python
from app.models.surgery_config import SurgeryProcedureTemplate

DEFAULT_TEMPLATES = [
    {"code": "office_30",     "name": "Office procedure (30 min)",
     "procedure_kind": "office", "default_duration_minutes": 30},
    {"code": "minor_60",      "name": "Minor procedure (60 min)",
     "procedure_kind": "minor",  "default_duration_minutes": 60},
    {"code": "major_120",     "name": "Major procedure (120 min)",
     "procedure_kind": "major",  "default_duration_minutes": 120},
    {"code": "robotic_180",   "name": "Robotic surgery (180 min)",
     "procedure_kind": "robotic_180", "default_duration_minutes": 180,
     "default_cpt_code": "58571"},
    {"code": "robotic_240",   "name": "Robotic surgery (240 min)",
     "procedure_kind": "robotic_240", "default_duration_minutes": 240,
     "default_cpt_code": "58572"},
]

def seed_default_templates(db: Session) -> int:
    inserted = 0
    for t in DEFAULT_TEMPLATES:
        if db.query(SurgeryProcedureTemplate).filter(
                SurgeryProcedureTemplate.code == t["code"]).first():
            continue
        db.add(SurgeryProcedureTemplate(**t, created_by="seed", updated_by="seed"))
        inserted += 1
    if inserted:
        db.commit()
    return inserted
```

Call from `init_db()` alongside `seed_default_facilities`.

- [ ] **Step 3: Build + commit**

```bash
cd frontend && npm run build 2>&1 | tail -5
git add frontend/src/pages/SurgeryRules.jsx backend/app/services/surgery_config_seed.py backend/app/database.py
git commit -m "feat(surgery): rules — Procedure templates tab + default seed"
```

---

## Section 5 — Replace hardcoded `FACILITY_LABEL`

### Task B13: Audit hardcoded `FACILITY_LABEL` and add the `useFacilities` hook

**Files:**
- Create: `frontend/src/hooks/useFacilities.js`
- Modify: every file that imports/declares `FACILITY_LABEL`

- [ ] **Step 1: Inventory the hardcoded sites**

Run:

```bash
cd frontend && grep -rn "FACILITY_LABEL" src
```

Expected hits: `Surgery.jsx`, `SurgeryWaitlist.jsx`, `SurgeryDetail.jsx`, `PatientSurgery.jsx`, possibly more. List every file path the grep returns — those are the files to convert in Step 3.

- [ ] **Step 2: Create the hook**

`frontend/src/hooks/useFacilities.js`:

```javascript
import { useQuery } from '@tanstack/react-query'
import api from '../utils/api'

/**
 * Returns the list of active facilities + a lookup helper.
 *   facilities → array of {code, label, address, sort_order}
 *   labelOf(code) → human label or the code if not found
 */
export function useFacilities() {
  const q = useQuery({
    queryKey: ['facilities-picklist'],
    queryFn: () => api.get('/surgery/picklists/facilities').then(r => r.data.facilities),
    staleTime: 60_000,
  })
  const facilities = q.data || []
  const map = Object.fromEntries(facilities.map(f => [f.code, f.label]))
  return {
    facilities,
    labelOf: (code) => map[code] || code,
    isLoading: q.isLoading,
  }
}
```

- [ ] **Step 3: Convert each consumer**

For every file the grep surfaced, replace the local `FACILITY_LABEL` dict with `const { labelOf } = useFacilities()` and call `labelOf(code)` everywhere the dict was used.

Example (one file at a time):

Before:
```jsx
const FACILITY_LABEL = { medstar: 'MedStar', crmc: 'CRMC', office: 'Office' }
// ...
<span>{FACILITY_LABEL[s.facility]}</span>
```

After:
```jsx
import { useFacilities } from '../hooks/useFacilities'
// ...
function Component(...) {
  const { labelOf } = useFacilities()
  // ...
  return <span>{labelOf(s.facility)}</span>
}
```

Do not delete `FACILITY_BADGE` / `FACILITY_TONE` dicts (they carry styling, not labels). Keep those local for now.

- [ ] **Step 4: Verify build + grep is clean**

```bash
cd frontend && npm run build 2>&1 | tail -5
grep -rn "FACILITY_LABEL" src
```

Expected: build succeeds, grep returns no hits.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/hooks/useFacilities.js frontend/src/pages
git commit -m "refactor(surgery): replace hardcoded FACILITY_LABEL with useFacilities hook"
```

---

## Section 6 — Verification

### Task B14: Deploy + smoke test

- [ ] **Step 1: Deploy backend (creates new tables on cold start via init_db + seeds defaults)**

```bash
cd backend && gcloud builds submit . --project=wwc-solutions --region=us-east4 \
  --tag=us-east4-docker.pkg.dev/wwc-solutions/app/backend:v24
gcloud run deploy backend --image=us-east4-docker.pkg.dev/wwc-solutions/app/backend:v24 \
  --region=us-east4 --project=wwc-solutions
```

- [ ] **Step 2: Deploy frontend**

```bash
cd frontend && gcloud builds submit . --project=wwc-solutions --region=us-east4 \
  --tag=us-east4-docker.pkg.dev/wwc-solutions/app/frontend:v22
gcloud run deploy frontend --image=us-east4-docker.pkg.dev/wwc-solutions/app/frontend:v22 \
  --region=us-east4 --project=wwc-solutions
```

- [ ] **Step 3: Verify in browser**

- `https://gw.waldorfwomenscare.com/surgery/rules` — five tabs visible.
- **Thresholds**: defaults show 6/6/14, save persists.
- **Alert recipients**: add `manager@waldorfwomenscare.com` to office_release, verify it appears, remove it.
- **Facilities**: 3 default rows (office/medstar/crmc). Edit one, verify other pages now show the new label (e.g. `/surgery/waitlist`).
- **Procedure templates**: 5 default rows. Add a new template, verify it appears in the picklist endpoint.

- [ ] **Step 4: Push**

```bash
git push origin main
```
