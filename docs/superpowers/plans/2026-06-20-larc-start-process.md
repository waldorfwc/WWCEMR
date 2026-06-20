# Start LARC Process — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the two LARC dashboard entry buttons with a single "Start LARC Process" two-step intake that collects patient/request details (incl. Requested By + Reason w/ ICD-10), suggests stock-vs-pharmacy fulfillment (advisory, overridable), then hands off to the existing assignment workflow.

**Architecture:** A shared backend `pick_source_flow` service (extracted from the surgery path) powers a new advisory `suggest-flow` endpoint. The intake drawer creates the `LarcAssignment` once, after the path is confirmed, reusing the existing `create_assignment` endpoint (extended with reason + provider fields). Reasons are a new configurable list in the existing `larc_config` key-value store; providers reuse the existing clinician list.

**Tech Stack:** Backend FastAPI + SQLAlchemy + pytest (in-memory SQLite, `client`/`db` fixtures). Frontend React + Vite + react-query (no JS test runner — verify via `npm run build` + manual steps).

**Spec:** `docs/superpowers/specs/2026-06-20-larc-start-process-design.md`

**Conventions (must follow):**
- Dates: `now_utc_naive()` from `app.utils.dt`, never `datetime.utcnow()`.
- API routes are under `/larc` (mounted at `/api`), so tests call `/api/larc/...`.
- The `client` fixture is super-admin (passes every tier gate). Seed data by creating model rows via the `db` fixture.
- Backend is TDD: write the failing test first, watch it fail, implement, watch it pass, commit.
- Run backend tests from `backend/` with the venv active: `source venv/bin/activate`.

---

## File Structure

**Backend**
- `backend/app/services/larc/source_flow.py` — **new.** `pick_source_flow(db, dt)` (moved from surgery) + `suggest_flow(db, dt)` returning the advisory suggestion dict. Single source of truth for the stock/pharmacy/office decision.
- `backend/app/services/surgery/device_requests.py` — **modify.** Import `pick_source_flow` from the new module; delete the private copy.
- `backend/app/models/larc.py` — **modify.** Add `reason_for_request` + `reason_icd10` columns to `LarcAssignment`.
- `backend/app/database.py` — **modify.** Add the two columns to the lightweight-migration `needed` list.
- `backend/app/services/larc/settings.py` — **modify.** Add `reason_for_request_options` to `LARC_SETTINGS_DEFAULTS`.
- `backend/app/routers/larc.py` — **modify.** `LarcConfigPayload` gets the reasons list + validator; new `SuggestFlowIn` + `POST /assignments/suggest-flow`; `AssignmentIn` gets reason + provider fields; `create_assignment` persists them and accepts `office_procedure`; `_assignment_dict` echoes the reason fields.

**Frontend**
- `frontend/src/pages/Larc.jsx` — **modify.** Replace the two entry buttons with one "Start LARC Process"; add `StartLarcProcessDrawer` (two-step intake) — replaces `NewRequestDrawer`.
- `frontend/src/pages/LarcSettings.jsx` — **modify.** Add a "Reasons" tab with a label+ICD-10 pairs editor backed by `/larc/config`.

**Tests (new files)**
- `backend/tests/test_larc_source_flow.py`
- `backend/tests/test_larc_suggest_flow_api.py`
- `backend/tests/test_larc_reason_config.py`
- `backend/tests/test_larc_start_process_create.py`

---

## Task 1: Shared source-flow service + surgery refactor

**Files:**
- Create: `backend/app/services/larc/source_flow.py`
- Modify: `backend/app/services/surgery/device_requests.py` (lines 33-43 define `_pick_source_flow`; line 86 calls it)
- Test: `backend/tests/test_larc_source_flow.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_larc_source_flow.py
"""Unit tests for the shared LARC source-flow decision + suggestion."""
from app.models.larc import LarcDeviceType, LarcDevice
from app.services.larc.source_flow import pick_source_flow, suggest_flow


def _dt(db, name, default_flow):
    dt = LarcDeviceType(name=name, category=("office_procedure"
                        if default_flow == "office_procedure" else "larc"),
                        default_flow=default_flow, is_active=True)
    db.add(dt); db.commit(); db.refresh(dt)
    return dt


def _stock(db, dt, our_id):
    d = LarcDevice(our_id=our_id, device_type_id=dt.id, status="unassigned")
    db.add(d); db.commit()
    return d


def test_pick_in_stock_when_unassigned_device_exists(db):
    dt = _dt(db, "Mirena", "pharmacy_order")
    _stock(db, dt, "WWC-1")
    assert pick_source_flow(db, dt) == "in_stock"


def test_pick_pharmacy_when_no_stock_and_default_pharmacy(db):
    dt = _dt(db, "Kyleena", "pharmacy_order")
    assert pick_source_flow(db, dt) == "pharmacy_order"


def test_pick_office_when_default_office_and_no_stock(db):
    dt = _dt(db, "NovaSure", "office_procedure")
    assert pick_source_flow(db, dt) == "office_procedure"


def test_suggest_normal_device_offers_stock_and_pharmacy(db):
    dt = _dt(db, "Skyla", "pharmacy_order")
    _stock(db, dt, "WWC-2")
    s = suggest_flow(db, dt)
    assert s["suggested_flow"] == "in_stock"
    assert s["in_stock_count"] == 1
    assert set(s["allowed_flows"]) == {"in_stock", "pharmacy_order"}


def test_suggest_pharmacy_when_no_stock(db):
    dt = _dt(db, "Paragard", "pharmacy_order")
    s = suggest_flow(db, dt)
    assert s["suggested_flow"] == "pharmacy_order"
    assert s["in_stock_count"] == 0
    assert s["allowed_flows"] == ["pharmacy_order"]


def test_suggest_consumable_never_offers_pharmacy(db):
    dt = _dt(db, "Bensta", "office_procedure")
    s = suggest_flow(db, dt)
    assert s["suggested_flow"] == "office_procedure"
    assert "pharmacy_order" not in s["allowed_flows"]
    assert s["allowed_flows"] == ["office_procedure"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && source venv/bin/activate && pytest tests/test_larc_source_flow.py -q`
Expected: FAIL — `ModuleNotFoundError: app.services.larc.source_flow`.

- [ ] **Step 3: Create the shared service**

```python
# backend/app/services/larc/source_flow.py
"""Shared LARC fulfillment-path decision.

Single source of truth for whether a device request should be filled from
in-house stock, via a pharmacy enrollment form, or as an in-office
consumable. Used by both the surgery device-request sync and the manual
"Start LARC Process" intake so the two never drift.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.larc import LarcDevice, LarcDeviceType


def pick_source_flow(db: Session, dt: LarcDeviceType) -> str:
    """Decide the fulfillment path for a device type:
      - a matching device in stock          -> "in_stock"
      - else the type's default office flow  -> "office_procedure"
      - else                                 -> "pharmacy_order"
    """
    in_stock = (db.query(LarcDevice)
                  .filter(LarcDevice.device_type_id == dt.id,
                          LarcDevice.status == "unassigned")
                  .count())
    if in_stock > 0:
        return "in_stock"
    if dt.default_flow == "office_procedure":
        return "office_procedure"
    return "pharmacy_order"


def suggest_flow(db: Session, dt: LarcDeviceType) -> dict:
    """Advisory suggestion for the intake drawer.

    Returns the recommended flow plus the override set ``allowed_flows``:
      - always include the suggested flow
      - include "in_stock" when there is on-hand stock
      - include "pharmacy_order" only for non-consumable types
        (default_flow != office_procedure)
      - include "office_procedure" only when it is the suggestion
    Order is stable for display: in_stock, pharmacy_order, office_procedure.
    """
    in_stock_count = (db.query(LarcDevice)
                        .filter(LarcDevice.device_type_id == dt.id,
                                LarcDevice.status == "unassigned")
                        .count())
    suggested = pick_source_flow(db, dt)

    allowed: list[str] = []
    if in_stock_count > 0:
        allowed.append("in_stock")
    if dt.default_flow != "office_procedure":
        allowed.append("pharmacy_order")
    if suggested == "office_procedure":
        allowed.append("office_procedure")
    if suggested not in allowed:                      # safety net
        allowed.insert(0, suggested)

    order = ["in_stock", "pharmacy_order", "office_procedure"]
    allowed = [f for f in order if f in allowed]

    return {
        "suggested_flow": suggested,
        "in_stock_count": in_stock_count,
        "default_flow": dt.default_flow,
        "allowed_flows": allowed,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && source venv/bin/activate && pytest tests/test_larc_source_flow.py -q`
Expected: PASS (6 passed).

- [ ] **Step 5: Refactor surgery to use the shared function (no behavior change)**

In `backend/app/services/surgery/device_requests.py`, delete the private `def _pick_source_flow(db, dt)` (lines ~33-43) and add an import near the top:

```python
from app.services.larc.source_flow import pick_source_flow
```

Then change the call site (line ~86) from:

```python
            source_flow = _pick_source_flow(db, dt)
```
to:
```python
            source_flow = pick_source_flow(db, dt)
```

- [ ] **Step 6: Run the surgery device-request tests to confirm no regression**

Run: `cd backend && source venv/bin/activate && pytest tests/ -q -k "device_request or surgery_device"`
Expected: PASS (no failures). If no such tests exist, run `pytest tests/test_larc_source_flow.py -q` and `python -c "import app.services.surgery.device_requests"` (must import cleanly).

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/larc/source_flow.py backend/app/services/surgery/device_requests.py backend/tests/test_larc_source_flow.py
git commit -m "feat(larc): shared pick_source_flow + suggest_flow service"
```

---

## Task 2: `suggest-flow` endpoint

**Files:**
- Modify: `backend/app/routers/larc.py` (add `SuggestFlowIn` near `AssignmentIn` at line ~1149; add route near `create_assignment` at line ~1218)
- Test: `backend/tests/test_larc_suggest_flow_api.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_larc_suggest_flow_api.py
"""Authenticated tests for POST /api/larc/assignments/suggest-flow."""
from app.models.larc import LarcDeviceType, LarcDevice


def _dt(db, name, default_flow):
    dt = LarcDeviceType(name=name, category=("office_procedure"
                        if default_flow == "office_procedure" else "larc"),
                        default_flow=default_flow, is_active=True)
    db.add(dt); db.commit(); db.refresh(dt)
    return dt


def test_suggest_flow_in_stock(client, db):
    dt = _dt(db, "Mirena", "pharmacy_order")
    db.add(LarcDevice(our_id="WWC-10", device_type_id=dt.id, status="unassigned"))
    db.commit()
    r = client.post("/api/larc/assignments/suggest-flow",
                    json={"device_type_id": str(dt.id)})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["suggested_flow"] == "in_stock"
    assert body["in_stock_count"] == 1
    assert set(body["allowed_flows"]) == {"in_stock", "pharmacy_order"}


def test_suggest_flow_pharmacy_when_empty(client, db):
    dt = _dt(db, "Kyleena", "pharmacy_order")
    r = client.post("/api/larc/assignments/suggest-flow",
                    json={"device_type_id": str(dt.id)})
    assert r.status_code == 200, r.text
    assert r.json()["suggested_flow"] == "pharmacy_order"
    assert r.json()["allowed_flows"] == ["pharmacy_order"]


def test_suggest_flow_unknown_device_type_404(client, db):
    r = client.post("/api/larc/assignments/suggest-flow",
                    json={"device_type_id": "00000000-0000-0000-0000-000000000000"})
    assert r.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && source venv/bin/activate && pytest tests/test_larc_suggest_flow_api.py -q`
Expected: FAIL — 404/405 (route not defined) or 422.

- [ ] **Step 3: Add the import, request model, and route**

In `backend/app/routers/larc.py`, add to the imports near the other service imports (around line 53):

```python
from app.services.larc.source_flow import suggest_flow
```

Add the request model just above `class AssignmentIn(BaseModel):` (line ~1149):

```python
class SuggestFlowIn(BaseModel):
    device_type_id: str
```

Add the route just above `@router.post("/assignments", status_code=201)` (line ~1218):

```python
@router.post("/assignments/suggest-flow")
def suggest_assignment_flow(
    payload: SuggestFlowIn,
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.LARC, Tier.WORK)),
):
    """Advisory: given a device type, recommend stock vs pharmacy vs office
    and the set of override options. Does not create anything."""
    dt = (db.query(LarcDeviceType)
            .filter(LarcDeviceType.id == payload.device_type_id,
                    LarcDeviceType.is_active.is_(True))
            .first())
    if not dt:
        raise HTTPException(status_code=404, detail="device type not found")
    return suggest_flow(db, dt)
```

Note: this route must be registered **before** any `/assignments/{assignment_id}` catch-all so "suggest-flow" isn't parsed as an id. Placing it immediately above `create_assignment` (a POST `/assignments`) satisfies this — verify no earlier `@router.post("/assignments/{...}")` shadows it.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && source venv/bin/activate && pytest tests/test_larc_suggest_flow_api.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/larc.py backend/tests/test_larc_suggest_flow_api.py
git commit -m "feat(larc): advisory suggest-flow endpoint"
```

---

## Task 3: Reason fields on `LarcAssignment` + migration

**Files:**
- Modify: `backend/app/models/larc.py` (after `requested_by_provider` at line 248)
- Modify: `backend/app/database.py` (the `needed` list at line ~153)
- Test: `backend/tests/test_larc_start_process_create.py` (first test only)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_larc_start_process_create.py
"""Start-LARC-Process create-assignment behavior: reason + provider capture."""
from app.models.larc import LarcAssignment, LarcDeviceType


def _dt(db, name="Mirena", default_flow="pharmacy_order"):
    dt = LarcDeviceType(name=name, category="larc",
                        default_flow=default_flow, is_active=True)
    db.add(dt); db.commit(); db.refresh(dt)
    return dt


def test_assignment_model_has_reason_columns(db):
    dt = _dt(db)
    a = LarcAssignment(chart_number="MRN1", patient_name="Doe, Jane",
                       device_type_id=dt.id, source_flow="pharmacy_order",
                       status="new", reason_for_request="Contraception",
                       reason_icd10="Z30.430")
    db.add(a); db.commit(); db.refresh(a)
    assert a.reason_for_request == "Contraception"
    assert a.reason_icd10 == "Z30.430"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && source venv/bin/activate && pytest tests/test_larc_start_process_create.py::test_assignment_model_has_reason_columns -q`
Expected: FAIL — `TypeError: 'reason_for_request' is an invalid keyword argument for LarcAssignment`.

- [ ] **Step 3: Add the columns**

In `backend/app/models/larc.py`, immediately after the `requested_by_provider` column (line 248):

```python
    requested_by_provider = Column(String(200), nullable=True)
    reason_for_request = Column(String(120), nullable=True)
    reason_icd10       = Column(String(20),  nullable=True)
```

- [ ] **Step 4: Add the lightweight migration entries**

In `backend/app/database.py`, inside the `needed = [` list (after the existing `("larc_assignments", "deleted_by", "VARCHAR(200)")` entry near line 201):

```python
        ("larc_assignments", "reason_for_request", "VARCHAR(120)"),
        ("larc_assignments", "reason_icd10", "VARCHAR(20)"),
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && source venv/bin/activate && pytest tests/test_larc_start_process_create.py::test_assignment_model_has_reason_columns -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/models/larc.py backend/app/database.py backend/tests/test_larc_start_process_create.py
git commit -m "feat(larc): reason_for_request + reason_icd10 on assignment"
```

---

## Task 4: Reason-for-request config (defaults + payload validation)

**Files:**
- Modify: `backend/app/services/larc/settings.py` (`LARC_SETTINGS_DEFAULTS` at line ~15)
- Modify: `backend/app/routers/larc.py` (`LarcConfigPayload` at line 400)
- Test: `backend/tests/test_larc_reason_config.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_larc_reason_config.py
"""Reason-for-request configurable list via /api/larc/config."""


def test_config_includes_reason_defaults(client, db):
    r = client.get("/api/larc/config")
    assert r.status_code == 200, r.text
    opts = r.json()["reason_for_request_options"]
    labels = {o["reason"] for o in opts}
    assert {"Contraception", "Menorrhagia"} <= labels
    by = {o["reason"]: o["icd10"] for o in opts}
    assert by["Contraception"] == "Z30.430"
    assert by["Menorrhagia"] == "N92.0"


def test_config_put_updates_reasons(client, db):
    new = [{"reason": "Dysmenorrhea", "icd10": "N94.6"}]
    r = client.put("/api/larc/config", json={"reason_for_request_options": new})
    assert r.status_code == 200, r.text
    assert r.json()["reason_for_request_options"] == new


def test_config_put_rejects_invalid_reason_item(client, db):
    r = client.put("/api/larc/config",
                   json={"reason_for_request_options": [{"reason": "No code"}]})
    assert r.status_code == 422


def test_config_put_rejects_blank_icd10(client, db):
    r = client.put("/api/larc/config",
                   json={"reason_for_request_options":
                         [{"reason": "X", "icd10": "  "}]})
    assert r.status_code == 422
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && source venv/bin/activate && pytest tests/test_larc_reason_config.py -q`
Expected: FAIL — `KeyError`/missing key on GET; PUT does not validate.

- [ ] **Step 3: Add the default**

In `backend/app/services/larc/settings.py`, add to `LARC_SETTINGS_DEFAULTS`:

```python
LARC_SETTINGS_DEFAULTS: dict[str, Any] = {
    "device_expiry_hold_days":          365,
    "assignment_reallocate_after_days": 180,
    "pharmacy_order_sla_days":           14,
    "checkout_ack_window_hours":         24,
    "reason_for_request_options": [
        {"reason": "Contraception", "icd10": "Z30.430"},
        {"reason": "Menorrhagia",   "icd10": "N92.0"},
    ],
}
```

- [ ] **Step 4: Add the payload field + validator**

In `backend/app/routers/larc.py`, ensure `field_validator` is imported from pydantic (check the existing pydantic import line near the top; add `field_validator` if absent):

```python
from pydantic import BaseModel, Field, field_validator
```

Extend `LarcConfigPayload` (line 400):

```python
class LarcConfigPayload(BaseModel):
    device_expiry_hold_days:          Optional[int] = Field(default=None, ge=1, le=3650)
    assignment_reallocate_after_days: Optional[int] = Field(default=None, ge=1, le=3650)
    pharmacy_order_sla_days:          Optional[int] = Field(default=None, ge=1, le=365)
    checkout_ack_window_hours:        Optional[int] = Field(default=None, ge=1, le=720)
    reason_for_request_options:       Optional[list[dict]] = None

    @field_validator("reason_for_request_options")
    @classmethod
    def _validate_reasons(cls, v):
        if v is None:
            return v
        cleaned = []
        for item in v:
            if not isinstance(item, dict):
                raise ValueError("each reason must be an object")
            reason = str(item.get("reason", "")).strip()
            icd10 = str(item.get("icd10", "")).strip()
            if not reason or not icd10:
                raise ValueError("each reason needs a non-empty reason and icd10")
            cleaned.append({"reason": reason, "icd10": icd10})
        return cleaned
```

The existing `put_larc_config` loop already persists any key present in `LARC_SETTINGS_DEFAULTS`, so no change is needed there.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && source venv/bin/activate && pytest tests/test_larc_reason_config.py -q`
Expected: PASS (4 passed).

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/larc/settings.py backend/app/routers/larc.py backend/tests/test_larc_reason_config.py
git commit -m "feat(larc): configurable reason-for-request list with ICD-10"
```

---

## Task 5: `create_assignment` — persist reason + provider, accept office_procedure

**Files:**
- Modify: `backend/app/routers/larc.py` (`AssignmentIn` line ~1149; `create_assignment` validation line ~1222 + the `LarcAssignment(...)` constructor line ~1270; `_assignment_dict` line ~183)
- Test: `backend/tests/test_larc_start_process_create.py` (add tests)

- [ ] **Step 1: Write the failing tests (append to the file)**

```python
def test_create_persists_reason_and_provider(client, db):
    dt = _dt(db)
    r = client.post("/api/larc/assignments", json={
        "chart_number": "MRN2",
        "patient_name": "Roe, Mary",
        "patient_first_name": "Mary", "patient_last_name": "Roe",
        "device_type_id": str(dt.id),
        "source_flow": "pharmacy_order",
        "reason_for_request": "Contraception",
        "reason_icd10": "Z30.430",
        "requested_by_provider": "Aryian Cooke, MD",
        "inserting_provider_email": "acooke@waldorfwomenscare.com",
        "inserting_provider_name": "Aryian Cooke, MD",
        "inserting_provider_npi": "1234567890",
    })
    assert r.status_code == 201, r.text
    aid = r.json()["id"]
    a = db.query(LarcAssignment).filter(LarcAssignment.id == aid).first()
    assert a.reason_for_request == "Contraception"
    assert a.reason_icd10 == "Z30.430"
    assert a.requested_by_provider == "Aryian Cooke, MD"
    assert a.inserting_provider_email == "acooke@waldorfwomenscare.com"
    assert a.inserting_provider_npi == "1234567890"


def test_create_accepts_office_procedure(client, db):
    dt = _dt(db, name="NovaSure", default_flow="office_procedure")
    r = client.post("/api/larc/assignments", json={
        "chart_number": "MRN3",
        "patient_name": "Poe, Edna",
        "patient_first_name": "Edna", "patient_last_name": "Poe",
        "device_type_id": str(dt.id),
        "source_flow": "office_procedure",
        "reason_for_request": "Menorrhagia",
        "reason_icd10": "N92.0",
        "requested_by_provider": "Aryian Cooke, MD",
    })
    assert r.status_code == 201, r.text
    a = db.query(LarcAssignment).filter(
        LarcAssignment.id == r.json()["id"]).first()
    assert a.source_flow == "office_procedure"


def test_assignment_dict_echoes_reason(client, db):
    dt = _dt(db)
    aid = client.post("/api/larc/assignments", json={
        "chart_number": "MRN4", "patient_name": "Foe, Ann",
        "patient_first_name": "Ann", "patient_last_name": "Foe",
        "device_type_id": str(dt.id), "source_flow": "pharmacy_order",
        "reason_for_request": "Contraception", "reason_icd10": "Z30.430",
        "requested_by_provider": "Aryian Cooke, MD",
    }).json()["id"]
    got = client.get(f"/api/larc/assignments/{aid}").json()
    assert got["reason_for_request"] == "Contraception"
    assert got["reason_icd10"] == "Z30.430"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && source venv/bin/activate && pytest tests/test_larc_start_process_create.py -q`
Expected: FAIL — office_procedure rejected with 422; reason/provider fields not accepted/persisted; `_assignment_dict` lacks the keys.

- [ ] **Step 3: Extend `AssignmentIn`**

In `backend/app/routers/larc.py`, add these fields to `AssignmentIn` (after `notes: Optional[str] = None`, line ~1171):

```python
    reason_for_request: Optional[str] = None
    reason_icd10:       Optional[str] = None
    requested_by_provider:    Optional[str] = None
    inserting_provider_email: Optional[str] = None
    inserting_provider_name:  Optional[str] = None
    inserting_provider_npi:   Optional[str] = None
```

- [ ] **Step 4: Accept `office_procedure` in the source_flow guard**

Change the guard in `create_assignment` (line ~1222) from:

```python
    if payload.source_flow not in ("in_stock", "pharmacy_order"):
        raise HTTPException(status_code=422, detail="invalid source_flow")
```
to:
```python
    if payload.source_flow not in ("in_stock", "pharmacy_order", "office_procedure"):
        raise HTTPException(status_code=422, detail="invalid source_flow")
```

- [ ] **Step 5: Persist the new fields on the assignment**

The `a = LarcAssignment(...)` constructor (line ~1270) currently ends with these two contiguous lines:

```python
        notes=payload.notes,
        created_by=current_user.get("email"),
```

Replace **both** of those lines with this block (it re-includes `notes` and `created_by`):

```python
        notes=payload.notes,
        reason_for_request=(payload.reason_for_request or "").strip() or None,
        reason_icd10=(payload.reason_icd10 or "").strip() or None,
        requested_by_provider=(payload.requested_by_provider or "").strip() or None,
        inserting_provider_email=(payload.inserting_provider_email or "").strip() or None,
        inserting_provider_name=(payload.inserting_provider_name or "").strip() or None,
        inserting_provider_npi=(payload.inserting_provider_npi or "").strip() or None,
        created_by=current_user.get("email"),
```

- [ ] **Step 6: Echo the reason in `_assignment_dict`**

In `_assignment_dict` (near line 183, where `"requested_by_provider": a.requested_by_provider,` is returned), add:

```python
        "requested_by_provider": a.requested_by_provider,
        "reason_for_request": a.reason_for_request,
        "reason_icd10": a.reason_icd10,
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd backend && source venv/bin/activate && pytest tests/test_larc_start_process_create.py -q`
Expected: PASS (4 passed, incl. the Task-3 model test).

- [ ] **Step 8: Commit**

```bash
git add backend/app/routers/larc.py backend/tests/test_larc_start_process_create.py
git commit -m "feat(larc): create_assignment captures reason + provider; accepts office_procedure"
```

---

## Task 6: Frontend — single "Start LARC Process" button + two-step drawer

**Files:**
- Modify: `frontend/src/pages/Larc.jsx` (entry buttons at lines 131-139; drawer mount ~441-444; replace `NewRequestDrawer` ~452-740)

No JS test runner exists; verify with `npm run build` + the manual checklist in Step 5.

- [ ] **Step 1: Replace the two entry buttons with one**

In `frontend/src/pages/Larc.jsx`, replace the two buttons (the "Benefits for In-Stock Device" and "LARC Enrollment Form" buttons at lines ~131-139) with a single button, and replace the two drawer-trigger states with one:

```jsx
        <button className="btn-primary" onClick={() => setStartOpen(true)}>
          <Plus size={13} /> Start LARC Process
        </button>
```

Add `const [startOpen, setStartOpen] = useState(false)` with the other `useState` hooks, and remove the old `newRequest` / `reserveInventory` state. At the drawer mount (lines ~441-444) replace both `<NewRequestDrawer .../>` instances with:

```jsx
      {startOpen && <StartLarcProcessDrawer
        onClose={() => setStartOpen(false)}
        onCreated={(id) => { setStartOpen(false); navigate('/larc/assignments/' + id) }}
      />}
```

- [ ] **Step 2: Add the `StartLarcProcessDrawer` component**

Replace the `NewRequestDrawer` function (lines ~452-740) with `StartLarcProcessDrawer`. It mirrors the existing drawer chrome (`fixed inset-0 z-50 flex justify-end`, sticky header/footer, `grid grid-cols-6` body) and the existing field set, plus: two new dropdowns, a Continue→Suggestion→Confirm two-step flow. Full component:

```jsx
function StartLarcProcessDrawer({ onClose, onCreated }) {
  const qc = useQueryClient()
  const [step, setStep] = useState(1)            // 1 = intake, 2 = suggestion
  const [suggestion, setSuggestion] = useState(null)  // {suggested_flow, in_stock_count, allowed_flows}
  const [chosenFlow, setChosenFlow] = useState(null)
  const [form, setForm] = useState({
    chart_number: '', patient_first_name: '', patient_last_name: '',
    patient_dob: '', patient_email: '', patient_cell: '',
    device_type_id: '', requested_by_email: '',
    reason_for_request: '', reason_icd10: '',
  })
  const update = (k, v) => setForm(f => ({ ...f, [k]: v }))

  const { data: types } = useQuery({
    queryKey: ['larc-device-types'],
    queryFn: () => api.get('/larc/device-types').then(r => r.data),
    staleTime: 60_000,
  })
  const { data: clinicians } = useQuery({
    queryKey: ['clinicians'],
    queryFn: () => api.get('/admin/users/clinicians').then(r => r.data),
    staleTime: 60_000,
  })
  const { data: config } = useQuery({
    queryKey: ['larc-config'],
    queryFn: () => api.get('/larc/config').then(r => r.data),
    staleTime: 60_000,
  })
  const reasons = config?.reason_for_request_options || []

  const allFilled = form.chart_number.trim() && form.patient_first_name.trim()
    && form.patient_last_name.trim() && form.patient_dob && form.patient_email.trim()
    && form.patient_cell.trim() && form.device_type_id && form.requested_by_email
    && form.reason_for_request

  const suggest = useMutation({
    mutationFn: () => api.post('/larc/assignments/suggest-flow',
      { device_type_id: form.device_type_id }).then(r => r.data),
    onSuccess: (data) => { setSuggestion(data); setChosenFlow(data.suggested_flow); setStep(2) },
    onError: (e) => alert(e?.response?.data?.detail || 'Could not compute a suggestion'),
  })

  const create = useMutation({
    mutationFn: () => {
      const prov = (clinicians || []).find(c => c.email === form.requested_by_email)
      return api.post('/larc/assignments', {
        chart_number: form.chart_number.trim(),
        patient_name: `${form.patient_last_name.trim()}, ${form.patient_first_name.trim()}`,
        patient_first_name: form.patient_first_name.trim(),
        patient_last_name: form.patient_last_name.trim(),
        patient_dob: form.patient_dob,
        patient_email: form.patient_email.trim(),
        patient_cell: form.patient_cell.trim(),
        device_type_id: form.device_type_id,
        source_flow: chosenFlow,
        reason_for_request: form.reason_for_request,
        reason_icd10: form.reason_icd10,
        requested_by_provider: prov?.display_name || null,
        inserting_provider_email: prov?.email || null,
        inserting_provider_name: prov?.display_name || null,
        inserting_provider_npi: prov?.npi || null,
      }).then(r => r.data)
    },
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ['larc-dashboard'] })
      qc.invalidateQueries({ queryKey: ['larc-assignments'] })
      onCreated(data.id)
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Create failed'),
  })

  const FLOW_LABEL = {
    in_stock: 'Use an in-stock device',
    pharmacy_order: 'Pharmacy enrollment form',
    office_procedure: 'In-office procedure device',
  }

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/30" />
      <div className="relative w-full max-w-lg bg-white shadow-xl overflow-y-auto"
           onClick={e => e.stopPropagation()}>
        <div className="sticky top-0 bg-white border-b px-4 py-3 flex items-center justify-between">
          <h2 className="font-semibold text-plum-700">Start LARC Process</h2>
          <button onClick={onClose}><X size={18} /></button>
        </div>

        {step === 1 && (
          <div className="p-4 grid grid-cols-6 gap-2 text-sm">
            <label className="col-span-3">MRN
              <input className="input w-full" value={form.chart_number}
                     onChange={e => update('chart_number', e.target.value)} /></label>
            <label className="col-span-3">DOB
              <input type="date" className="input w-full" value={form.patient_dob}
                     onChange={e => update('patient_dob', e.target.value)} /></label>
            <label className="col-span-3">First Name
              <input className="input w-full" value={form.patient_first_name}
                     onChange={e => update('patient_first_name', e.target.value)} /></label>
            <label className="col-span-3">Last Name
              <input className="input w-full" value={form.patient_last_name}
                     onChange={e => update('patient_last_name', e.target.value)} /></label>
            <label className="col-span-3">Email
              <input className="input w-full" value={form.patient_email}
                     onChange={e => update('patient_email', e.target.value)} /></label>
            <label className="col-span-3">Cell Phone
              <input className="input w-full" value={form.patient_cell}
                     onChange={e => update('patient_cell', e.target.value)} /></label>
            <label className="col-span-6">Device Type
              <select className="input w-full" value={form.device_type_id}
                      onChange={e => update('device_type_id', e.target.value)}>
                <option value="">— select device —</option>
                {(types || []).filter(t => t.is_active).map(t => (
                  <option key={t.id} value={t.id}>{t.name}</option>))}
              </select></label>
            <label className="col-span-6">Requested By
              <select className="input w-full" value={form.requested_by_email}
                      onChange={e => update('requested_by_email', e.target.value)}>
                <option value="">— select provider —</option>
                {(clinicians || []).map(c => (
                  <option key={c.email} value={c.email}>
                    {c.display_name}{c.credential ? `, ${c.credential}` : ''}</option>))}
              </select>
              <span className="text-[11px] text-muted">Manage providers in Admin → Users.</span>
            </label>
            <label className="col-span-6">Reason for Request
              <select className="input w-full" value={form.reason_for_request}
                      onChange={e => {
                        const r = reasons.find(x => x.reason === e.target.value)
                        update('reason_for_request', e.target.value)
                        update('reason_icd10', r?.icd10 || '')
                      }}>
                <option value="">— select reason —</option>
                {reasons.map(r => (
                  <option key={r.reason} value={r.reason}>{r.reason} ({r.icd10})</option>))}
              </select></label>
          </div>
        )}

        {step === 2 && suggestion && (
          <div className="p-4 text-sm space-y-3">
            <div className="rounded border border-plum-200 bg-plum-50 p-3">
              <div className="font-medium text-plum-700">Recommended</div>
              <div>{FLOW_LABEL[suggestion.suggested_flow]}
                {suggestion.suggested_flow === 'in_stock'
                  && ` — ${suggestion.in_stock_count} available`}</div>
            </div>
            <div>
              <div className="text-[11px] text-muted mb-1">Choose how to fulfill:</div>
              {suggestion.allowed_flows.map(f => (
                <label key={f} className="flex items-center gap-2 py-1">
                  <input type="radio" name="flow" checked={chosenFlow === f}
                         onChange={() => setChosenFlow(f)} />
                  {FLOW_LABEL[f]}
                </label>))}
            </div>
          </div>
        )}

        <div className="sticky bottom-0 bg-white border-t px-4 py-3 flex justify-between">
          {step === 2
            ? <button className="btn-ghost" onClick={() => setStep(1)}>Back</button>
            : <span />}
          {step === 1
            ? <button className="btn-primary" disabled={!allFilled || suggest.isPending}
                      onClick={() => suggest.mutate()}>Continue</button>
            : <button className="btn-primary" disabled={!chosenFlow || create.isPending}
                      onClick={() => create.mutate()}>Confirm &amp; Create</button>}
        </div>
      </div>
    </div>
  )
}
```

Ensure the imports at the top of `Larc.jsx` include `useState`, `useQuery`, `useMutation`, `useQueryClient`, `api`, `useNavigate`/`navigate`, and the `X` and `Plus` icons (the file already imports most of these for the old drawer — add any that are now missing; remove the now-unused `NewRequestDrawer` references).

- [ ] **Step 2b: Confirm `navigate` is available**

`Larc.jsx` must have `const navigate = useNavigate()` in the page component (it already navigates on create today). If not present, add `import { useNavigate } from 'react-router-dom'` and `const navigate = useNavigate()` in the `Larc` component, and ensure the `onCreated` callback uses it.

- [ ] **Step 3: Build**

Run: `cd frontend && npm run build`
Expected: `✓ built` with no errors (no references to a removed `NewRequestDrawer`, no missing imports).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/Larc.jsx
git commit -m "feat(larc): single Start LARC Process entry with two-step suggestion drawer"
```

- [ ] **Step 5: Manual verification (record results in the PR/commit notes)**

After deploy (or `npm run dev`), as a LARC-Work user:
1. `/larc` shows one **Start LARC Process** button (the two old buttons are gone).
2. Open it: all nine fields render; Requested By lists clinicians; Reason lists configured reasons with ICD-10; **Continue** is disabled until every field is filled.
3. Pick a device type with stock → Continue → suggestion reads "Use an in-stock device — N available", override shows in-stock + pharmacy.
4. Pick a pharmacy-only type with no stock → suggestion = pharmacy; only pharmacy offered.
5. Pick a consumable (e.g. NovaSure) → suggestion = in-office; pharmacy NOT offered.
6. Confirm → lands on `/larc/assignments/{id}`; the assignment shows the chosen flow and the reason; for a pharmacy flow, the enrollment step's inserting provider is pre-filled with the Requested-By provider.

---

## Task 7: Frontend — Reasons editor in LARC Settings

**Files:**
- Modify: `frontend/src/pages/LarcSettings.jsx` (`BASE_TABS` line 15; tab render lines 41-54; add a `ReasonsTab` component)

- [ ] **Step 1: Add the tab**

In `frontend/src/pages/LarcSettings.jsx`, add `{ id: 'reasons', label: 'Reasons' }` to `BASE_TABS` (line 15), and add a render branch with the others (line ~51):

```jsx
      {tab === 'reasons'    && <ReasonsTab />}
```

- [ ] **Step 2: Add the `ReasonsTab` component**

Add at the bottom of the file (mirrors `ThresholdsTab`'s fetch/mutate pattern at lines 79-118):

```jsx
function ReasonsTab() {
  const qc = useQueryClient()
  const { data: config } = useQuery({
    queryKey: ['larc-config'],
    queryFn: () => api.get('/larc/config').then(r => r.data),
  })
  const [rows, setRows] = useState(null)
  const list = rows ?? config?.reason_for_request_options ?? []

  const save = useMutation({
    mutationFn: () => api.put('/larc/config',
      { reason_for_request_options: list.filter(r => r.reason.trim() && r.icd10.trim()) }
    ).then(r => r.data),
    onSuccess: (data) => {
      qc.setQueryData(['larc-config'], data)
      setRows(null)
      alert('Saved')
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Save failed'),
  })

  const set = (i, k, v) => setRows(list.map((r, j) => j === i ? { ...r, [k]: v } : r))
  const add = () => setRows([...list, { reason: '', icd10: '' }])
  const remove = (i) => setRows(list.filter((_, j) => j !== i))

  return (
    <div className="space-y-2 max-w-xl">
      <p className="text-sm text-muted">Reasons shown in the Start LARC Process form.
        Each needs an ICD-10 code.</p>
      {list.map((r, i) => (
        <div key={i} className="flex gap-2 items-center">
          <input className="input flex-1" placeholder="Reason" value={r.reason}
                 onChange={e => set(i, 'reason', e.target.value)} />
          <input className="input w-32" placeholder="ICD-10" value={r.icd10}
                 onChange={e => set(i, 'icd10', e.target.value)} />
          <button className="btn-ghost text-red-600" onClick={() => remove(i)}>Remove</button>
        </div>
      ))}
      <div className="flex gap-2 pt-2">
        <button className="btn-ghost" onClick={add}>+ Add Reason</button>
        <button className="btn-primary" disabled={save.isPending}
                onClick={() => save.mutate()}>Save Changes</button>
      </div>
    </div>
  )
}
```

Ensure `useState`, `useQuery`, `useMutation`, `useQueryClient`, and `api` are imported at the top of `LarcSettings.jsx` (most are already used by `ThresholdsTab`; add any missing).

- [ ] **Step 3: Build**

Run: `cd frontend && npm run build`
Expected: `✓ built` with no errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/LarcSettings.jsx
git commit -m "feat(larc): reasons-with-ICD-10 editor in LARC Settings"
```

- [ ] **Step 5: Manual verification**

On `/larc/settings`, the **Reasons** tab lists Contraception/Menorrhagia with codes; add a row, Save, reload — it persists; the new reason appears in the Start LARC Process form's Reason dropdown.

---

## Task 8: Full-suite verification + deploy

- [ ] **Step 1: Run the full backend suite**

Run: `cd backend && source venv/bin/activate && python -m pytest -q -p no:cacheprovider`
Expected: all pass (previous baseline 1240 passed; this adds ~17 new tests and changes no existing behavior).

- [ ] **Step 2: Build the frontend**

Run: `cd frontend && npm run build`
Expected: `✓ built`.

- [ ] **Step 3: Deploy (only when the user asks to deploy)**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
SHA=$(git rev-parse --short HEAD)
gcloud builds submit backend/  --tag=us-east4-docker.pkg.dev/wwc-solutions/app/backend:$SHA  --project=wwc-solutions --region=us-east4
gcloud builds submit frontend/ --tag=us-east4-docker.pkg.dev/wwc-solutions/app/frontend:$SHA --project=wwc-solutions --region=us-east4
gcloud run services update backend  --region=us-east4 --project=wwc-solutions --image=us-east4-docker.pkg.dev/wwc-solutions/app/backend:$SHA
gcloud run services update frontend --region=us-east4 --project=wwc-solutions --image=us-east4-docker.pkg.dev/wwc-solutions/app/frontend:$SHA
```

Backend deploys first (the frontend calls the new `suggest-flow` endpoint). The new columns are added on boot by `_apply_lightweight_migrations()`; no manual DB step.

---

## Notes / risks

- **ICD-10 defaults** (`Z30.430`, `N92.0`) are starting values — flag for billing to confirm; they're editable in settings.
- **Office-procedure in the manual flow:** allowed because a consumable device type's only sensible path is office-procedure. The override hides pharmacy for consumables and in-stock when none is on hand.
- **No behavior change to downstream** allocate-device / enrollment / fax flows — they run unchanged from the assignment detail page.
- **Surgery path** keeps working through the same `pick_source_flow` (now shared); Task 1 Step 6 guards against regression.
