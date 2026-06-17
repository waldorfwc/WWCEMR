# Surgery Type Catalog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the hardcoded surgery-procedure dropdown with a DB-backed, fully-editable Surgery Type catalog where each type maps name → CPT(s) → consent template(s), carries a minor/major/office classification and eligible locations, and auto-fills the surgery on intake.

**Architecture:** A new `SurgeryType` SQLAlchemy model (lightweight migration via `init_db()` registration, no Alembic), seeded once from the existing `picklists.PROCEDURES`. A thin service module holds list/CRUD/validation. The existing `/surgery/picklists` endpoint gains a `surgery_types` array (keeping a flattened `procedures` for back-compat), and new `Tier.MANAGE` CRUD endpoints live under `/surgery/admin/surgery-types`. The frontend intake dropdown reads `surgery_types` and auto-fills procedures/locations/classification/consent on select; a new "Surgery Types" section in Surgery Settings manages the catalog.

**Tech Stack:** FastAPI, SQLAlchemy, Pydantic, pytest (backend); React + react-query + Vite (frontend).

**Spec:** `docs/superpowers/specs/2026-06-17-surgery-type-catalog-design.md`

**Conventions (apply to every task):** MM/DD/YYYY dates, Title Case for UI titles/headers/buttons, money `$X.XX`; no secrets in source; `now_utc_naive()` never `datetime.utcnow()`; run backend commands from `backend/`, frontend from `frontend/`.

---

## File Structure

**Backend:**
- Create `backend/app/models/surgery_type.py` — the `SurgeryType` model.
- Modify `backend/app/database.py:39` — register the model in `init_db()`; add the seed call in the existing surgery-seed `try` block (lines 51-67).
- Create `backend/app/services/surgery/surgery_type_seed.py` — `seed_surgery_types(db)` (idempotent).
- Create `backend/app/services/surgery/surgery_types.py` — list/CRUD/validation + `as_picklist`.
- Modify `backend/app/services/surgery/picklists.py:157` — `all_picklists()` no longer the source of truth for the live dropdown; add a helper used by the router (the router merges the catalog in).
- Modify `backend/app/routers/surgery.py` — `GET /picklists` (line 1544) returns `surgery_types` + flattened `procedures`; add CRUD endpoints + `SurgeryTypePayload`; add `procedure_classification` to `ManualSurgeryIn` (line ~1228) and have `create_manual` (line ~1281-1292) honor an explicit classification.
- Modify `backend/tests/conftest.py` (~line 18) — register the new model so `create_all` builds its table in tests.
- Create `backend/tests/test_surgery_type_model.py`, `backend/tests/test_surgery_type_seed.py`, `backend/tests/test_surgery_types_service.py`, `backend/tests/test_surgery_type_router.py`, `backend/tests/test_surgery_manual_classification.py`, `backend/tests/test_surgery_type_catalog_walkthrough.py`.

**Frontend:**
- Modify `frontend/src/components/surgery/SurgeryIntakeForm.jsx` — dropdown reads `surgery_types`; `pickSurgery` auto-fills.
- Modify `frontend/src/pages/SurgerySettings.jsx` — add a "Surgery Types" manager.

---

### Task 1: `SurgeryType` model + table registration

**Files:**
- Create: `backend/app/models/surgery_type.py`
- Modify: `backend/app/database.py:39`
- Test: `backend/tests/test_surgery_type_model.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_surgery_type_model.py
"""The SurgeryType model: columns, defaults, and that init_db creates the table."""
from app.models.surgery_type import SurgeryType


def test_surgery_type_defaults(db):
    t = SurgeryType(name="Diagnostic hysteroscopy",
                    cpts=[{"cpt": "58555", "description": "Diagnostic hysteroscopy"}])
    db.add(t); db.commit(); db.refresh(t)
    assert t.id is not None
    assert t.classification == "minor"          # default
    assert t.eligible_facilities == []          # default
    assert t.consent_template_ids == []         # default
    assert t.active is True                      # default
    assert t.sort_order == 0                     # default
    assert t.created_at is not None and t.updated_at is not None
    assert t.cpts == [{"cpt": "58555", "description": "Diagnostic hysteroscopy"}]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_surgery_type_model.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.models.surgery_type'`.

- [ ] **Step 3: Create the model**

```python
# backend/app/models/surgery_type.py
"""A staff-managed surgery type: the "Surgery Name" dropdown is built from these
rows. Each type maps a name to one or more CPTs, a minor/major/office
classification, optional eligible locations, and the consent template(s) that
apply. Selecting a type during intake auto-fills the surgery.
"""
from __future__ import annotations

from sqlalchemy import Boolean, Column, DateTime, Integer, JSON, String

from app.database import Base
from app.models.guid import GUID, new_uuid
from app.utils.dt import now_utc_naive


class SurgeryType(Base):
    __tablename__ = "surgery_types"

    id = Column(GUID(), primary_key=True, default=new_uuid)
    name = Column(String(200), nullable=False)
    # [{"cpt": "58558", "description": "Hysteroscopy with D&C +/- polypectomy"}, ...]
    cpts = Column(JSON, nullable=False, default=list)
    # minor | major | office
    classification = Column(String(20), nullable=False, default="minor")
    # subset of SURGERY_FACILITY_VALUES; [] = all locations
    eligible_facilities = Column(JSON, nullable=False, default=list)
    # explicit ConsentTemplate IDs that apply to this type
    consent_template_ids = Column(JSON, nullable=False, default=list)
    active = Column(Boolean, nullable=False, default=True)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=now_utc_naive, nullable=False)
    updated_at = Column(DateTime, default=now_utc_naive,
                        onupdate=now_utc_naive, nullable=False)
```

- [ ] **Step 4: Register the model in `init_db()` so the table is created**

In `backend/app/database.py:39`, add `surgery_type` to the import list. Change:

```python
    from app.models import patient, claim, payment, denial, appeal, audit, document, patient_directory, clinical, payment_analysis, fax_log, practice_config, user, adjustment_code_reference, import_audit, groups, checklist, recall, training, google_sync, surgery, surgery_activity, larc, larc_config, billing_document, missing_charge, pellet, pellet_config, recall_config, state_transition, idempotency, personal_task, code_helper, patient_portal, module_tier, bai2, bai2_exclusion, pellet_portal, pellet_payment, pellet_schedule, cron_run  # noqa
```

to append `, surgery_type` before the `# noqa`:

```python
    from app.models import patient, claim, payment, denial, appeal, audit, document, patient_directory, clinical, payment_analysis, fax_log, practice_config, user, adjustment_code_reference, import_audit, groups, checklist, recall, training, google_sync, surgery, surgery_activity, larc, larc_config, billing_document, missing_charge, pellet, pellet_config, recall_config, state_transition, idempotency, personal_task, code_helper, patient_portal, module_tier, bai2, bai2_exclusion, pellet_portal, pellet_payment, pellet_schedule, cron_run, surgery_type  # noqa
```

- [ ] **Step 5: Register the model for the test harness**

`backend/tests/conftest.py` builds the test DB with `Base.metadata.create_all` (not `init_db`), so a model is only given a table when it's imported into `Base.metadata` at collection time. conftest already does this for lazily-imported models (see the `from app.models import state_transition as _state_transition  # noqa: F401` line ~18). Add an analogous line right after it:

```python
from app.models import surgery_type as _surgery_type  # noqa: F401
```

This guarantees `surgery_types` exists in every test's fresh DB regardless of test-file collection order.

- [ ] **Step 6: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_surgery_type_model.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
cd backend && git add app/models/surgery_type.py app/database.py tests/conftest.py tests/test_surgery_type_model.py
git commit -m "feat(surgery-types): add SurgeryType model + table registration"
```

---

### Task 2: Seed the catalog from the hardcoded `PROCEDURES`

**Files:**
- Create: `backend/app/services/surgery/surgery_type_seed.py`
- Modify: `backend/app/database.py:51-67` (the surgery-seed `try` block)
- Test: `backend/tests/test_surgery_type_seed.py`

Note: `MAJOR_CPTS` lives in `app/services/surgery/smartsheet_seed.py` (`{"49320", "58146", "58660", "58662", "58550", "58552", "58553", "58554"}`). There is no office-CPT set, so the seed assigns only `major`/`minor`; staff set `office` manually afterward.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_surgery_type_seed.py
"""seed_surgery_types: one-time idempotent seed from picklists.PROCEDURES,
classifying each entry via MAJOR_CPTS."""
from app.models.surgery_type import SurgeryType
from app.services.surgery.picklists import PROCEDURES
from app.services.surgery.surgery_type_seed import seed_surgery_types


def test_seed_populates_once_and_is_idempotent(db):
    n = seed_surgery_types(db)
    assert n == len(PROCEDURES)
    assert db.query(SurgeryType).count() == len(PROCEDURES)
    # Second call seeds nothing (table non-empty).
    assert seed_surgery_types(db) == 0
    assert db.query(SurgeryType).count() == len(PROCEDURES)


def test_seed_classification_and_shape(db):
    seed_surgery_types(db)
    # 49320 Diagnostic laparoscopy is in MAJOR_CPTS → major.
    major = db.query(SurgeryType).filter(SurgeryType.cpts.isnot(None)).all()
    by_cpt = {t.cpts[0]["cpt"]: t for t in major}
    assert by_cpt["49320"].classification == "major"
    # 58558 is not in MAJOR_CPTS → minor.
    assert by_cpt["58558"].classification == "minor"
    # Each seeded type has a single-CPT row mirroring the source entry.
    t = by_cpt["58558"]
    assert t.cpts == [{"cpt": "58558", "description": t.name}]
    assert t.eligible_facilities == [] and t.consent_template_ids == [] and t.active is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_surgery_type_seed.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.surgery.surgery_type_seed'`.

- [ ] **Step 3: Write the seed**

```python
# backend/app/services/surgery/surgery_type_seed.py
"""One-time, idempotent seed of the SurgeryType catalog from the legacy
hardcoded PROCEDURES list. After this runs, the catalog is the source of truth
and is fully editable; PROCEDURES is retained only as the seed source.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.surgery_type import SurgeryType
from app.services.surgery.picklists import PROCEDURES
from app.services.surgery.smartsheet_seed import MAJOR_CPTS


def seed_surgery_types(db: Session) -> int:
    """Insert one SurgeryType per PROCEDURES entry, but only when the table is
    empty. Returns the number of rows inserted (0 if already seeded)."""
    if db.query(SurgeryType).count() > 0:
        return 0
    inserted = 0
    for i, proc in enumerate(PROCEDURES):
        cpt = (proc.get("cpt") or "").strip()
        desc = (proc.get("description") or "").strip()
        db.add(SurgeryType(
            name=desc,
            cpts=[{"cpt": cpt, "description": desc}],
            classification="major" if cpt in MAJOR_CPTS else "minor",
            eligible_facilities=[],
            consent_template_ids=[],
            active=True,
            sort_order=i,
        ))
        inserted += 1
    db.commit()
    return inserted
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_surgery_type_seed.py -v`
Expected: PASS.

- [ ] **Step 5: Wire the seed into startup**

In `backend/app/database.py`, inside the existing surgery-seed `try` block, import and call the seed alongside the others. Change lines 53-62 from:

```python
        from app.services.surgery.config_seed import (
            seed_default_facilities, seed_default_templates,
            seed_default_email_templates, seed_default_sms_templates,
        )
        db = SessionLocal()
        try:
            seed_default_facilities(db)
            seed_default_templates(db)
            seed_default_email_templates(db)
            seed_default_sms_templates(db)
        finally:
            db.close()
```

to:

```python
        from app.services.surgery.config_seed import (
            seed_default_facilities, seed_default_templates,
            seed_default_email_templates, seed_default_sms_templates,
        )
        from app.services.surgery.surgery_type_seed import seed_surgery_types
        db = SessionLocal()
        try:
            seed_default_facilities(db)
            seed_default_templates(db)
            seed_default_email_templates(db)
            seed_default_sms_templates(db)
            seed_surgery_types(db)
        finally:
            db.close()
```

- [ ] **Step 6: Run the full seed-related suite to confirm nothing broke**

Run: `cd backend && python -m pytest tests/test_surgery_type_seed.py tests/test_surgery_type_model.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
cd backend && git add app/services/surgery/surgery_type_seed.py app/database.py tests/test_surgery_type_seed.py
git commit -m "feat(surgery-types): seed catalog from legacy PROCEDURES on startup"
```

---

### Task 3: Service layer — list, CRUD, validation, `as_picklist`

**Files:**
- Create: `backend/app/services/surgery/surgery_types.py`
- Test: `backend/tests/test_surgery_types_service.py`

This module owns all catalog logic. `consent_template_ids` are validated against real `ConsentTemplate` rows (unknown IDs are dropped). `classification` must be one of `minor`/`major`/`office`. `eligible_facilities` must be a subset of `SURGERY_FACILITY_VALUES` (imported from `app.models.surgery`).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_surgery_types_service.py
"""SurgeryType service: list/create/update/soft-delete/reorder + validation."""
import pytest
from fastapi import HTTPException

from app.models.surgery import ConsentTemplate
from app.models.surgery_type import SurgeryType
from app.services.surgery import surgery_types as svc


def _tmpl(db, name="Hysteroscopy Consent"):
    t = ConsentTemplate(name=name, cpt_codes=["58558"], procedure_match=[],
                        facility_match=[], insurance_match=[])
    db.add(t); db.commit(); db.refresh(t)
    return t


def test_create_validates_and_persists(db):
    tmpl = _tmpl(db)
    row = svc.create_type(db, {
        "name": "Hysteroscopy with D&C",
        "cpts": [{"cpt": "58558", "description": "Hysteroscopy with D&C"}],
        "classification": "minor",
        "eligible_facilities": ["medstar", "office"],
        "consent_template_ids": [str(tmpl.id), "not-a-real-id"],
    })
    assert row.id is not None
    assert row.consent_template_ids == [str(tmpl.id)]          # unknown id dropped
    assert row.eligible_facilities == ["medstar", "office"]


def test_create_rejects_bad_input(db):
    with pytest.raises(HTTPException) as e1:
        svc.create_type(db, {"name": "", "cpts": [{"cpt": "1", "description": "x"}]})
    assert e1.value.status_code == 422
    with pytest.raises(HTTPException):                          # empty cpts
        svc.create_type(db, {"name": "X", "cpts": []})
    with pytest.raises(HTTPException):                          # bad classification
        svc.create_type(db, {"name": "X", "cpts": [{"cpt": "1", "description": "x"}],
                             "classification": "huge"})
    with pytest.raises(HTTPException):                          # bad facility
        svc.create_type(db, {"name": "X", "cpts": [{"cpt": "1", "description": "x"}],
                             "eligible_facilities": ["mars"]})


def test_list_excludes_inactive_by_default(db):
    a = svc.create_type(db, {"name": "A", "cpts": [{"cpt": "1", "description": "a"}]})
    svc.create_type(db, {"name": "B", "cpts": [{"cpt": "2", "description": "b"}]})
    svc.set_active(db, str(a.id), False)
    assert [t.name for t in svc.list_types(db)] == ["B"]
    assert {t.name for t in svc.list_types(db, include_inactive=True)} == {"A", "B"}


def test_update_and_reorder(db):
    a = svc.create_type(db, {"name": "A", "cpts": [{"cpt": "1", "description": "a"}]})
    b = svc.create_type(db, {"name": "B", "cpts": [{"cpt": "2", "description": "b"}]})
    svc.update_type(db, str(a.id), {"name": "A2",
                                    "cpts": [{"cpt": "1", "description": "a"}],
                                    "classification": "major"})
    assert db.get(SurgeryType, a.id).name == "A2"
    assert db.get(SurgeryType, a.id).classification == "major"
    svc.reorder(db, [str(b.id), str(a.id)])
    assert [t.name for t in svc.list_types(db)] == ["B", "A2"]


def test_as_picklist_shape(db):
    svc.create_type(db, {"name": "A", "cpts": [{"cpt": "1", "description": "a"}],
                         "classification": "office", "eligible_facilities": ["office"]})
    pl = svc.as_picklist(svc.list_types(db))
    assert pl[0].keys() >= {"id", "name", "cpts", "classification",
                            "eligible_facilities", "consent_template_ids"}
    assert pl[0]["classification"] == "office"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_surgery_types_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.surgery.surgery_types'`.

- [ ] **Step 3: Write the service**

```python
# backend/app/services/surgery/surgery_types.py
"""Surgery Type catalog service: validation + CRUD + picklist serialization.

The catalog backs the surgery-intake "Surgery Name" dropdown. Each type maps a
name to one or more CPTs, a classification, optional eligible locations, and the
consent template(s) that apply.
"""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.surgery import ConsentTemplate, SURGERY_FACILITY_VALUES
from app.models.surgery_type import SurgeryType

VALID_CLASSIFICATIONS = ("minor", "major", "office")


def _clean_cpts(raw) -> list[dict]:
    out = []
    for item in (raw or []):
        cpt = str((item or {}).get("cpt", "")).strip()
        desc = str((item or {}).get("description", "")).strip()
        if cpt or desc:
            out.append({"cpt": cpt, "description": desc})
    return out


def _validate(db: Session, payload: dict) -> dict:
    name = str(payload.get("name", "")).strip()
    if not name:
        raise HTTPException(422, "name is required")
    cpts = _clean_cpts(payload.get("cpts"))
    if not cpts or any(not c["cpt"] for c in cpts):
        raise HTTPException(422, "at least one CPT (with a code) is required")
    classification = payload.get("classification") or "minor"
    if classification not in VALID_CLASSIFICATIONS:
        raise HTTPException(422, f"classification must be one of {VALID_CLASSIFICATIONS}")
    facilities = [f for f in (payload.get("eligible_facilities") or []) if f]
    bad = [f for f in facilities if f not in SURGERY_FACILITY_VALUES]
    if bad:
        raise HTTPException(422, f"unknown facility code(s): {bad}")
    # Drop consent-template ids that don't reference a real template.
    wanted = [str(x) for x in (payload.get("consent_template_ids") or []) if x]
    known = set()
    if wanted:
        rows = db.query(ConsentTemplate.id).filter(ConsentTemplate.id.in_(wanted)).all()
        known = {str(r[0]) for r in rows}
    consent_ids = [x for x in wanted if x in known]
    return {
        "name": name, "cpts": cpts, "classification": classification,
        "eligible_facilities": facilities, "consent_template_ids": consent_ids,
    }


def list_types(db: Session, *, include_inactive: bool = False) -> list[SurgeryType]:
    q = db.query(SurgeryType)
    if not include_inactive:
        q = q.filter(SurgeryType.active.is_(True))
    return q.order_by(SurgeryType.sort_order, SurgeryType.name).all()


def _get(db: Session, type_id: str) -> SurgeryType:
    row = db.get(SurgeryType, type_id)
    if row is None:
        raise HTTPException(404, "surgery type not found")
    return row


def create_type(db: Session, payload: dict) -> SurgeryType:
    data = _validate(db, payload)
    nxt = (db.query(SurgeryType).count())
    row = SurgeryType(sort_order=payload.get("sort_order", nxt), **data)
    db.add(row); db.commit(); db.refresh(row)
    return row


def update_type(db: Session, type_id: str, payload: dict) -> SurgeryType:
    row = _get(db, type_id)
    data = _validate(db, payload)
    for k, v in data.items():
        setattr(row, k, v)
    if "sort_order" in payload and payload["sort_order"] is not None:
        row.sort_order = payload["sort_order"]
    if "active" in payload and payload["active"] is not None:
        row.active = bool(payload["active"])
    db.commit(); db.refresh(row)
    return row


def set_active(db: Session, type_id: str, active: bool) -> SurgeryType:
    row = _get(db, type_id)
    row.active = bool(active)
    db.commit(); db.refresh(row)
    return row


def reorder(db: Session, ordered_ids: list[str]) -> None:
    for i, tid in enumerate(ordered_ids):
        row = db.get(SurgeryType, tid)
        if row is not None:
            row.sort_order = i
    db.commit()


def as_picklist(types: list[SurgeryType]) -> list[dict]:
    return [{
        "id": str(t.id),
        "name": t.name,
        "cpts": t.cpts or [],
        "classification": t.classification,
        "eligible_facilities": t.eligible_facilities or [],
        "consent_template_ids": t.consent_template_ids or [],
    } for t in types]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_surgery_types_service.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/services/surgery/surgery_types.py tests/test_surgery_types_service.py
git commit -m "feat(surgery-types): catalog service with validation + CRUD"
```

---

### Task 4: Router — picklists integration + CRUD endpoints

**Files:**
- Modify: `backend/app/routers/surgery.py` (the `GET /picklists` handler at line 1544; add CRUD endpoints + a Pydantic payload near the other request models)
- Test: `backend/tests/test_surgery_type_router.py`

The existing handler is:

```python
@router.get("/picklists")
def get_picklists(current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.VIEW))):
    from app.services.surgery.picklists import all_picklists
    return all_picklists()
```

`all_picklists()` returns a dict that includes `"procedures": PROCEDURES`. We add `surgery_types` from the catalog and rebuild `procedures` as a flattened, de-duplicated list of the active catalog's CPT rows (back-compat).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_surgery_type_router.py
"""Surgery Type catalog endpoints: picklists exposure + MANAGE-gated CRUD.

`client` is the super-admin fixture (passes all tier checks)."""
from app.models.surgery import ConsentTemplate


def _consent(db):
    t = ConsentTemplate(name="Hyst Consent", cpt_codes=["58558"], procedure_match=[],
                        facility_match=[], insurance_match=[])
    db.add(t); db.commit(); db.refresh(t)
    return str(t.id)


def test_crud_and_picklists(client, db):
    cid = _consent(db)

    # Create
    r = client.post("/api/surgery/admin/surgery-types", json={
        "name": "Hysteroscopy with D&C",
        "cpts": [{"cpt": "58558", "description": "Hysteroscopy with D&C"},
                 {"cpt": "58120", "description": "D&C"}],
        "classification": "minor",
        "eligible_facilities": ["medstar"],
        "consent_template_ids": [cid],
    })
    assert r.status_code in (200, 201), r.text
    tid = r.json()["id"]

    # Picklists now carries surgery_types AND a flattened procedures list.
    pk = client.get("/api/surgery/picklists").json()
    names = [t["name"] for t in pk["surgery_types"]]
    assert "Hysteroscopy with D&C" in names
    mine = next(t for t in pk["surgery_types"] if t["id"] == tid)
    assert mine["consent_template_ids"] == [cid]
    assert {"cpt": "58120", "description": "D&C"} in pk["procedures"]

    # Update
    r = client.put(f"/api/surgery/admin/surgery-types/{tid}", json={
        "name": "Hysteroscopy with D&C (updated)",
        "cpts": [{"cpt": "58558", "description": "Hysteroscopy with D&C"}],
        "classification": "major",
    })
    assert r.status_code == 200, r.text
    assert r.json()["classification"] == "major"

    # Soft-delete → drops out of picklists, listed with include_inactive
    assert client.delete(f"/api/surgery/admin/surgery-types/{tid}").status_code == 200
    pk2 = client.get("/api/surgery/picklists").json()
    assert tid not in [t["id"] for t in pk2["surgery_types"]]
    allr = client.get("/api/surgery/admin/surgery-types?include_inactive=true").json()
    assert tid in [t["id"] for t in allr]


def test_reorder(client, db):
    a = client.post("/api/surgery/admin/surgery-types",
                    json={"name": "A", "cpts": [{"cpt": "1", "description": "a"}]}).json()["id"]
    b = client.post("/api/surgery/admin/surgery-types",
                    json={"name": "B", "cpts": [{"cpt": "2", "description": "b"}]}).json()["id"]
    assert client.post("/api/surgery/admin/surgery-types/reorder",
                       json={"ordered_ids": [b, a]}).status_code == 200
    listed = client.get("/api/surgery/admin/surgery-types").json()
    order = [t["id"] for t in listed if t["id"] in (a, b)]
    assert order == [b, a]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_surgery_type_router.py -v`
Expected: FAIL (`surgery_types` KeyError on picklists / 404 on the admin routes).

- [ ] **Step 3: Replace the `get_picklists` handler**

In `backend/app/routers/surgery.py`, replace the existing handler (line ~1544) with:

```python
@router.get("/picklists")
def get_picklists(db: Session = Depends(get_db),
                  current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.VIEW))):
    from app.services.surgery.picklists import all_picklists
    from app.services.surgery import surgery_types as st_svc
    base = all_picklists()
    types = st_svc.list_types(db)
    base["surgery_types"] = st_svc.as_picklist(types)
    # Back-compat: flatten the catalog's CPT rows into the legacy `procedures`
    # shape, de-duplicated by (cpt, description), preserving catalog order.
    seen = set()
    flat = []
    for t in base["surgery_types"]:
        for c in t["cpts"]:
            key = (c.get("cpt", ""), c.get("description", ""))
            if key not in seen:
                seen.add(key)
                flat.append({"cpt": key[0], "description": key[1]})
    base["procedures"] = flat
    return base
```

(Confirm `get_db` and `Session` are already imported in this file — they are used by other handlers. If not, add `from sqlalchemy.orm import Session` and `from app.database import get_db`.)

- [ ] **Step 4: Add the payload model + CRUD endpoints**

Add near the other Pydantic request models in `backend/app/routers/surgery.py`:

```python
class SurgeryTypePayload(BaseModel):
    name: str
    cpts: list[dict] = []
    classification: str = "minor"
    eligible_facilities: list[str] = []
    consent_template_ids: list[str] = []
    active: Optional[bool] = None
    sort_order: Optional[int] = None


class SurgeryTypeReorderPayload(BaseModel):
    ordered_ids: list[str] = []
```

(Confirm `BaseModel` and `Optional` are imported — `from pydantic import BaseModel` and `from typing import Optional`. Add them if missing.)

Add these endpoints (place them after the `get_picklists` handler):

```python
@router.get("/admin/surgery-types")
def list_surgery_types(include_inactive: bool = False,
                       db: Session = Depends(get_db),
                       current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.MANAGE))):
    from app.services.surgery import surgery_types as st_svc
    return st_svc.as_picklist(st_svc.list_types(db, include_inactive=include_inactive))


@router.post("/admin/surgery-types", status_code=201)
def create_surgery_type(payload: SurgeryTypePayload,
                        db: Session = Depends(get_db),
                        current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.MANAGE))):
    from app.services.surgery import surgery_types as st_svc
    row = st_svc.create_type(db, payload.model_dump())
    return st_svc.as_picklist([row])[0]


@router.put("/admin/surgery-types/{type_id}")
def update_surgery_type(type_id: str, payload: SurgeryTypePayload,
                        db: Session = Depends(get_db),
                        current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.MANAGE))):
    from app.services.surgery import surgery_types as st_svc
    row = st_svc.update_type(db, type_id, payload.model_dump())
    return st_svc.as_picklist([row])[0]


@router.delete("/admin/surgery-types/{type_id}")
def delete_surgery_type(type_id: str,
                        db: Session = Depends(get_db),
                        current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.MANAGE))):
    from app.services.surgery import surgery_types as st_svc
    st_svc.set_active(db, type_id, False)
    return {"ok": True}


@router.post("/admin/surgery-types/reorder")
def reorder_surgery_types(payload: SurgeryTypeReorderPayload,
                          db: Session = Depends(get_db),
                          current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.MANAGE))):
    from app.services.surgery import surgery_types as st_svc
    st_svc.reorder(db, payload.ordered_ids)
    return {"ok": True}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_surgery_type_router.py -v`
Expected: PASS.

- [ ] **Step 6: Confirm no regression in the existing picklists/intake tests**

Run: `cd backend && python -m pytest tests/ -k "picklist or surgery_type" -v`
Expected: PASS (no pre-existing picklist test breaks on the new `db` dependency / response shape).

- [ ] **Step 7: Commit**

```bash
cd backend && git add app/routers/surgery.py tests/test_surgery_type_router.py
git commit -m "feat(surgery-types): picklists exposure + MANAGE CRUD endpoints"
```

---

### Task 5: Backend — let intake honor an explicit classification

**Files:**
- Modify: `backend/app/routers/surgery.py` (`ManualSurgeryIn` at line ~1228; the classification block in `create_manual` at lines ~1281-1292)
- Test: `backend/tests/test_surgery_manual_classification.py`

Today `create_manual` always derives `procedure_classification` from CPTs/robotic/facility and ignores any submitted value. To let a catalog type's classification (e.g. a manual `office`) flow through intake, accept an optional `procedure_classification` and prefer it when present; otherwise fall back to the existing derivation (unchanged).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_surgery_manual_classification.py
"""create_manual honors an explicit procedure_classification when supplied,
and still derives it from CPTs when omitted. `client` is the super-admin fixture."""

_BASE = dict(
    chart_number="MRN-CLS-1", patient_name="Test, Pat", dob="1980-01-01",
    phone="3015551212", email="pat@example.com",
    address_street="1 A St", address_city="Town", address_state="MD", address_zip="20601",
    primary_insurance="Aetna", primary_member_id="X1", surgeon_primary="Cooke, Aryian, MD",
    diagnoses=[{"icd": "N93.9", "description": "AUB"}],
    eligible_facilities=["office"], estimated_minutes=30, preop_date="2026-07-01",
)


def test_explicit_classification_wins(client):
    body = dict(_BASE, surgery_name="Diagnostic hysteroscopy",
                procedures=[{"cpt": "58555", "description": "Diagnostic hysteroscopy"}],
                procedure_classification="office")
    r = client.post("/api/surgery/manual", json=body)
    assert r.status_code == 201, r.text
    # Fetch it back and confirm the explicit classification stuck (would be
    # "minor" under the legacy CPT-derived path for 58555 at a non-office facility).
    sid = r.json()["id"]
    got = client.get(f"/api/surgery/{sid}").json()
    assert got["procedure_classification"] == "office"


def test_omitted_classification_is_derived(client):
    body = dict(_BASE, chart_number="MRN-CLS-2",
                surgery_name="Diagnostic laparoscopy",
                procedures=[{"cpt": "49320", "description": "Diagnostic laparoscopy"}])
    r = client.post("/api/surgery/manual", json=body)
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    got = client.get(f"/api/surgery/{sid}").json()
    assert got["procedure_classification"] == "major"   # 49320 ∈ MAJOR → derived
```

(If `GET /api/surgery/{id}` is not the detail route or doesn't return `procedure_classification`, confirm the actual detail endpoint with `grep -n "@router.get(\"/{" app/routers/surgery.py` and adjust the read-back accordingly — the model field is `procedure_classification`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_surgery_manual_classification.py -v`
Expected: `test_explicit_classification_wins` FAILS (got "minor", expected "office"); the derived test passes.

- [ ] **Step 3: Add the field to `ManualSurgeryIn`**

In `backend/app/routers/surgery.py`, add to `ManualSurgeryIn` (after `is_robotic: bool = False` at line ~1263):

```python
    procedure_classification: Optional[str] = None   # explicit override from a catalog type
```

- [ ] **Step 4: Prefer the explicit value in `create_manual`**

Replace the classification block (lines ~1281-1292):

```python
    # Procedure classification
    cpts = {(p.get("cpt") or "").strip() for p in payload.procedures if p.get("cpt")}
    ROBOTIC = {"58545", "58571", "58572", "58573", "58574", "58575"}
    MAJOR   = {"49320", "58146", "58660", "58662", "58550", "58552", "58553", "58554"}
    if payload.is_robotic or (cpts & ROBOTIC):
        classification = "robotic_240" if (payload.estimated_minutes or 0) >= 240 else "robotic_180"
    elif cpts & MAJOR:
        classification = "major"
    elif selected == "office":
        classification = "office"
    else:
        classification = "minor"
```

with (prefer an explicit, valid classification; otherwise derive as before):

```python
    # Procedure classification — prefer an explicit value (from a catalog type),
    # otherwise derive from CPTs / robotic / facility as before.
    cpts = {(p.get("cpt") or "").strip() for p in payload.procedures if p.get("cpt")}
    ROBOTIC = {"58545", "58571", "58572", "58573", "58574", "58575"}
    MAJOR   = {"49320", "58146", "58660", "58662", "58550", "58552", "58553", "58554"}
    explicit = (payload.procedure_classification or "").strip()
    if explicit in ("minor", "major", "office", "robotic_180", "robotic_240"):
        classification = explicit
    elif payload.is_robotic or (cpts & ROBOTIC):
        classification = "robotic_240" if (payload.estimated_minutes or 0) >= 240 else "robotic_180"
    elif cpts & MAJOR:
        classification = "major"
    elif selected == "office":
        classification = "office"
    else:
        classification = "minor"
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_surgery_manual_classification.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd backend && git add app/routers/surgery.py tests/test_surgery_manual_classification.py
git commit -m "feat(surgery-types): intake honors an explicit procedure_classification"
```

---

### Task 6: Frontend intake — dropdown reads `surgery_types` + auto-fill

**Files:**
- Modify: `frontend/src/components/surgery/SurgeryIntakeForm.jsx`

Current relevant code:
- `const procedureOpts = picks?.procedures || []` (line ~92).
- `pickSurgery(label)` (lines ~240-252) matches by description and fills one procedure row.
- An auto-match `useEffect` (lines ~105-148) recomputes `consent_template_ids` from `procedures`/facility/insurance via `/surgery/consent/match-preview`, honoring `consent_overrides.added`/`removed`.

We make the dropdown list catalog types and, on select, fill procedures (all CPTs), eligible locations, classification, and force-include the type's consent templates via `consent_overrides.added` (so the existing match-preview effect keeps them selected rather than clobbering them).

- [ ] **Step 1: Read the two spots to confirm line context**

Run: `cd frontend && grep -n "procedureOpts\|surgery_types\|function pickSurgery\|surgery_name" src/components/surgery/SurgeryIntakeForm.jsx`

- [ ] **Step 2: Add a `surgery_types` accessor next to `procedureOpts`**

Find (line ~92):

```javascript
  const procedureOpts = picks?.procedures || []
```

Replace with:

```javascript
  const procedureOpts = picks?.procedures || []
  const surgeryTypeOpts = picks?.surgery_types || []
```

- [ ] **Step 3: Rewrite `pickSurgery` to take a type id and auto-fill**

Find the `pickSurgery` function (lines ~240-252):

```javascript
  function pickSurgery(label) {
    // The dropdown is keyed by description; auto-fill the first procedure row
    // with the matching CPT + description so coordinators don't double-enter.
    const match = procedureOpts.find(p => p.description === label)
    setForm(f => ({
      ...f,
      surgery_name: label,
      procedures: match
        ? [{ cpt: match.cpt, description: match.description },
           ...f.procedures.slice(1)]
        : f.procedures,
    }))
  }
```

Replace with:

```javascript
  function pickSurgery(typeId) {
    // The dropdown is keyed by the surgery type id. Selecting a type auto-fills
    // the procedures (all its CPTs), eligible locations, classification, and
    // force-includes the type's consent templates (via consent_overrides.added
    // so the match-preview effect keeps them selected).
    const type = surgeryTypeOpts.find(t => t.id === typeId)
    if (!type) {
      setForm(f => ({ ...f, surgery_name: '' }))
      return
    }
    const cptRows = (type.cpts || []).map(c => ({ cpt: c.cpt, description: c.description }))
    setForm(f => {
      const added = [...new Set([...(f.consent_overrides?.added || []),
                                 ...(type.consent_template_ids || [])])]
      const removed = (f.consent_overrides?.removed || [])
        .filter(id => !(type.consent_template_ids || []).includes(id))
      const consent = [...new Set([...(f.consent_template_ids || []),
                                   ...(type.consent_template_ids || [])])]
      return {
        ...f,
        surgery_name: type.name,
        procedures: cptRows.length ? cptRows : f.procedures,
        eligible_facilities: (type.eligible_facilities && type.eligible_facilities.length)
          ? type.eligible_facilities
          : f.eligible_facilities,
        procedure_classification: type.classification || f.procedure_classification,
        consent_template_ids: consent,
        consent_overrides: { added, removed },
      }
    })
  }
```

- [ ] **Step 4: Add `procedure_classification` to the initial form state and the submit payload**

The initial-state object literal (line ~29-38) has `surgery_name: ''`, `procedures: [...]`, etc. Add a classification field so `pickSurgery` has somewhere to write and the submit reads it. After `surgery_name: '',` (line ~29) add:

```javascript
  procedure_classification: '',
```

Then in `buildFields()` (the returned payload object, after `surgery_name: form.surgery_name,` at line ~321) add:

```javascript
      procedure_classification: form.procedure_classification || null,
```

(The backend `ManualSurgeryIn.procedure_classification` from Task 5 receives it; an empty value falls back to CPT-derived classification.)

- [ ] **Step 5: Point the dropdown at `surgeryTypeOpts`**

Run: `cd frontend && grep -n "pickSurgery\|surgery_name" src/components/surgery/SurgeryIntakeForm.jsx` to find the `<select>`/options block that renders the "Surgery Name" picker.

Update the options to map over `surgeryTypeOpts` with `value={t.id}` and label `{t.name}`, and the current value to match the selected type's id. The select's `onChange` already calls `pickSurgery(e.target.value)` (now passing the id). Concretely, the options become:

```jsx
<option value="">Select a surgery…</option>
{surgeryTypeOpts.map(t => (
  <option key={t.id} value={t.id}>{t.name}</option>
))}
```

and the select's `value` should resolve the current `form.surgery_name` back to an id:

```jsx
value={surgeryTypeOpts.find(t => t.name === form.surgery_name)?.id || ''}
```

(If the existing markup binds to `form.surgery_name` directly as the option value, switch it to the id-based binding above so multi-CPT types resolve correctly.)

- [ ] **Step 6: Build the frontend to confirm it compiles**

Run: `cd frontend && npm run build`
Expected: build succeeds with no errors referencing `SurgeryIntakeForm.jsx`.

- [ ] **Step 7: Commit**

```bash
cd frontend && git add src/components/surgery/SurgeryIntakeForm.jsx
git commit -m "feat(surgery-types): intake dropdown reads catalog + auto-fills on select"
```

---

### Task 7: Frontend — "Surgery Types" manager in Surgery Settings

**Files:**
- Modify: `frontend/src/pages/SurgerySettings.jsx`

Add a "Surgery Types" section: a table of types (Name, CPTs, Classification, Locations, Active) with Add / Edit / Remove, plus an editor (modal or inline form) with: name; CPT rows (add/remove `{cpt, description}`); classification select (Minor / Major / Office); eligible-locations checkboxes (MedStar / CRMC / Office / WWC Office White Plains); consent-template multi-select (fetched from the consent-templates list endpoint); active toggle. Saving invalidates `['surgery-picklists']` so intake updates immediately.

- [ ] **Step 1: Inspect the settings page structure + how other sections fetch/mutate**

Run: `cd frontend && grep -n "useQuery\|useMutation\|invalidateQueries\|api.get\|api.post\|Section\|Title Case\|<h2\|tab" src/pages/SurgerySettings.jsx | head -40`
Read the file to match its existing section pattern, query client usage, and styling.

- [ ] **Step 2: Add the data hooks**

Inside the `SurgerySettings` component, add (use the file's existing `api` import and `useQueryClient`):

```javascript
  const qc = useQueryClient()
  const typesQ = useQuery({
    queryKey: ['surgery-types-admin'],
    queryFn: () => api.get('/surgery/admin/surgery-types?include_inactive=true').then(r => r.data),
  })
  const consentQ = useQuery({
    queryKey: ['consent-templates'],
    queryFn: () => api.get('/consent-templates').then(r => r.data),
  })
  const saveType = useMutation({
    mutationFn: ({ id, body }) =>
      (id ? api.put(`/surgery/admin/surgery-types/${id}`, body)
          : api.post('/surgery/admin/surgery-types', body)).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['surgery-types-admin'] })
      qc.invalidateQueries({ queryKey: ['surgery-picklists'] })
    },
  })
  const removeType = useMutation({
    mutationFn: (id) => api.delete(`/surgery/admin/surgery-types/${id}`).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['surgery-types-admin'] })
      qc.invalidateQueries({ queryKey: ['surgery-picklists'] })
    },
  })
```

(Confirm `useQuery`, `useMutation`, `useQueryClient` are imported from `@tanstack/react-query`; add to the existing import if needed.)

- [ ] **Step 3: Render the section**

Add a "Surgery Types" section following the page's existing section markup/styling. Table columns: Name, CPTs (join `c.cpt` values), Classification (Title Case), Locations (the eligible list or "All"), Active. Each row has Edit and Remove (Remove calls `removeType.mutate(t.id)` with a confirm). An "Add Surgery Type" button opens the editor with empty state.

Editor fields (controlled local state, default `{ name:'', cpts:[{cpt:'',description:''}], classification:'minor', eligible_facilities:[], consent_template_ids:[], active:true }`):
- Name — text input.
- CPTs — repeatable rows of `{cpt, description}` with an "Add CPT" button and per-row remove.
- Classification — `<select>` of Minor / Major / Office (values `minor`/`major`/`office`).
- Eligible Locations — checkboxes for `medstar`/`crmc`/`office`/`wwc_office_white_plains` (labels "MedStar", "CRMC", "Office", "WWC Office White Plains"); none checked = all.
- Consent Templates — multi-select / checkbox list from `consentQ.data` (`{id, name}`), storing ids in `consent_template_ids`.
- Active — checkbox.

Save calls `saveType.mutate({ id: editing?.id, body: editorState })` and closes the editor on success.

Use Title Case for the section title ("Surgery Types"), column headers, and the primary button ("Add Surgery Type" / "Save Surgery Type").

- [ ] **Step 4: Build the frontend**

Run: `cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 5: Commit**

```bash
cd frontend && git add src/pages/SurgerySettings.jsx
git commit -m "feat(surgery-types): Surgery Types catalog manager in Surgery Settings"
```

---

### Task 8: Authenticated walk-through

**Files:**
- Create: `backend/tests/test_surgery_type_catalog_walkthrough.py`

End-to-end through the test client (super-admin `client` fixture): create a type, see it in picklists, edit a seeded built-in, soft-delete, confirm picklist reflects it.

- [ ] **Step 1: Write the walk-through test**

```python
# backend/tests/test_surgery_type_catalog_walkthrough.py
"""Authenticated walk-through of the Surgery Type catalog: staff create a new
type (name + 2 CPTs + major + MedStar + a consent template), see it in the
intake picklist, edit a seeded built-in type, and soft-delete a type — the
picklist reflects each change. `client` is the super-admin fixture."""
from app.models.surgery import ConsentTemplate
from app.services.surgery.surgery_type_seed import seed_surgery_types


def test_catalog_walkthrough(client, db, capsys):
    log = []

    # 0. Seed the catalog from the legacy PROCEDURES list (as startup would).
    seeded = seed_surgery_types(db)
    assert seeded > 0
    log.append(f"0. catalog seeded from legacy list → {seeded} surgery types")

    tmpl = ConsentTemplate(name="Hysteroscopy Consent", cpt_codes=["58558"],
                           procedure_match=[], facility_match=[], insurance_match=[])
    db.add(tmpl); db.commit(); db.refresh(tmpl)

    # 1. Staff add a new multi-CPT type with classification, location, consent.
    r = client.post("/api/surgery/admin/surgery-types", json={
        "name": "Hysteroscopy with D&C + Polypectomy",
        "cpts": [{"cpt": "58558", "description": "Hysteroscopy with D&C"},
                 {"cpt": "58120", "description": "D&C"}],
        "classification": "major",
        "eligible_facilities": ["medstar"],
        "consent_template_ids": [str(tmpl.id)],
    })
    assert r.status_code == 201, r.text
    new_id = r.json()["id"]
    log.append("1. created 'Hysteroscopy with D&C + Polypectomy' (2 CPTs, major, MedStar, 1 consent)")

    # 2. It appears in the intake picklist with its full mapping.
    pk = client.get("/api/surgery/picklists").json()
    mine = next(t for t in pk["surgery_types"] if t["id"] == new_id)
    assert mine["classification"] == "major"
    assert mine["eligible_facilities"] == ["medstar"]
    assert mine["consent_template_ids"] == [str(tmpl.id)]
    assert len(mine["cpts"]) == 2
    log.append("2. /surgery/picklists → new type present with CPTs, classification, location, consent")

    # 3. Edit a seeded built-in type (rename + reclassify).
    built_in = next(t for t in pk["surgery_types"]
                    if t["cpts"] and t["cpts"][0]["cpt"] == "58555")  # Diagnostic hysteroscopy
    r = client.put(f"/api/surgery/admin/surgery-types/{built_in['id']}", json={
        "name": "Diagnostic Hysteroscopy (Office)",
        "cpts": built_in["cpts"],
        "classification": "office",
    })
    assert r.status_code == 200 and r.json()["classification"] == "office"
    log.append("3. edited a seeded built-in → renamed + reclassified to office")

    # 4. Soft-delete the new type → drops from the picklist, stays in admin list.
    assert client.delete(f"/api/surgery/admin/surgery-types/{new_id}").status_code == 200
    pk2 = client.get("/api/surgery/picklists").json()
    assert new_id not in [t["id"] for t in pk2["surgery_types"]]
    admin = client.get("/api/surgery/admin/surgery-types?include_inactive=true").json()
    assert new_id in [t["id"] for t in admin]
    log.append("4. soft-deleted the new type → gone from picklist, retained (inactive) in admin list")

    with capsys.disabled():
        print("\n  -- Surgery Type catalog walk-through (authenticated) --")
        for line in log:
            print("   " + line)
```

- [ ] **Step 2: Run it**

Run: `cd backend && python -m pytest tests/test_surgery_type_catalog_walkthrough.py -v -s`
Expected: PASS, and the walk-through log prints.

- [ ] **Step 3: Commit**

```bash
cd backend && git add tests/test_surgery_type_catalog_walkthrough.py
git commit -m "test(surgery-types): authenticated catalog walk-through"
```

---

## Final Verification (after all tasks)

- [ ] Run the full backend surgery + new suites:

Run: `cd backend && python -m pytest tests/ -k "surgery_type or picklist" -v`
Expected: all PASS.

- [ ] Build the frontend clean:

Run: `cd frontend && npm run build`
Expected: success.

- [ ] Confirm no regression vs the pre-change baseline for surgery tests. The catalog only adds; the one behavioral change to an existing endpoint is `GET /surgery/picklists` gaining `surgery_types` and rebuilding `procedures` from the catalog. No existing test asserts the main `/picklists` `procedures` content (current consumers read `insurance_companies`, `/picklists/facilities`, `/picklists/procedure-templates`), and in unseeded tests the flattened `procedures` is simply empty — acceptable. In production the catalog is seeded, so `procedures` reproduces the same CPT/description pairs as today. Run the broader surgery suite to confirm:

Run: `cd backend && python -m pytest tests/ -k "surgery" -q`
Expected: no NEW failures versus the documented baseline (90 failed / 555 passed pre-existing).

---

## Notes for the implementer
- **No Alembic.** Tables are created by `Base.metadata.create_all` via `init_db()` model registration (Task 1, Step 4). The `conftest` test DB uses the same `init_db` path, so the table exists in tests.
- **Seed is idempotent and empty-guarded** — safe to run on every boot. In production it seeds once; thereafter the catalog is authoritative.
- **`procedures` back-compat:** the flattened list is rebuilt from the active catalog, so any other consumer of `picks.procedures` keeps working. Don't delete `picklists.PROCEDURES` — it's the seed source.
- **Consent precedence in intake:** the type's consent templates are force-included via `consent_overrides.added`; the existing match-preview effect then unions matched templates on top. This keeps the explicit map authoritative without disabling auto-match.
