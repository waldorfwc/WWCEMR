# LARC Device Tracking — Workflow + Patient Portal — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add ownership-aware LARC workflow rules, a two-track status model with per-step patient notifications and auto-allocation, and a patient portal (Stripe payments, in-app BoldSign signing, status tracking).

**Architecture:** Backend services stay small and isolated — a track projector, an auto-allocation service, a notification helper, a portal-auth module, a LarcPayment model + Stripe branch. The patient portal is cloned from the existing pellet portal. Frontend has no JS test runner — verify with `npm run build` + manual checklists; backend is full pytest TDD (`client`/`db` fixtures, super-admin client, `/api/...`).

**Tech Stack:** FastAPI + SQLAlchemy + pytest; React + Vite + react-query; Stripe; BoldSign; Twilio/SMTP.

**Spec:** `docs/superpowers/specs/2026-06-20-larc-device-portal-workflow-design.md`

**Conventions:** `now_utc_naive()` (never `datetime.utcnow`); routes under `/larc` & new `/larc-portal` mounted at `/api`; backend tests seed via models on the `db` fixture; run backend tests from `backend/` with `source venv/bin/activate`; MM/DD/YYYY user-facing dates; commit after each task.

**Execution note:** Groups are ordered by dependency. Group A (data model) and B (tracks) are foundational; do them first. Each task is TDD: write failing test → run (fail) → implement → run (pass) → commit.

---

## File Structure

**Backend — new**
- `app/models/larc_payment.py` — `LarcPayment` (Stripe payment rows for patient responsibility).
- `app/services/larc/patient_track.py` — `patient_track(a)` projector → the 5 patient-visible steps per track.
- `app/services/larc/allocation.py` — `try_auto_allocate(db, a)` (+ `maybe_satisfy_zero_responsibility`).
- `app/services/larc/notifications.py` — `notify_larc_step(db, a, step, …)`.
- `app/services/larc/portal_auth.py` — clone of `pellet/portal_auth.py`.
- `app/routers/patient_larc.py` — `/api/larc-portal` router.
- `backend/scripts/seed_larc_portal_templates.py` — LARC email/SMS template seeds.

**Backend — modified**
- `app/models/larc.py` — new `LarcAssignment` columns.
- `app/database.py` — lightweight migrations.
- `app/services/larc/workflow.py` — catalog edits, buckets, spawn (patient-owned billed n/a).
- `app/routers/larc.py` — record_payment → auto-allocate + notify; benefits → notify; no-stock alert in dashboard.
- `app/services/stripe_payments.py` + `app/routers/stripe_payments.py` — LARC checkout + webhook branch.
- `app/main.py` — register `patient_larc` router.

**Frontend — new:** `src/lib/larc-portal-api.js`, `src/pages/larc-portal/*` (LarcPortalLogin, LarcPortalVerify, LarcPortalShell, LarcStatus, LarcPortalPayments, LarcPortalEnrollment, LarcPortalDocuments).
**Frontend — modified:** `src/pages/Larc.jsx` (dashboard checkout card), `src/pages/LarcAssignment.jsx` (remove checkout, gate insurance, billing rules), `src/App.jsx` (portal routes).

---

# GROUP A — Data model & migrations

## Task 1: New `LarcAssignment` columns

**Files:** Modify `backend/app/models/larc.py` (LarcAssignment, after `reason_icd10` ~line 250); Modify `backend/app/database.py` (`needed` list); Test `backend/tests/test_larc_portal_workflow_model.py`

- [ ] **Step 1: Failing test** — create `backend/tests/test_larc_portal_workflow_model.py`:

```python
from app.models.larc import LarcAssignment, LarcDeviceType


def _dt(db):
    dt = LarcDeviceType(name="Mirena", category="larc", default_flow="pharmacy_order", is_active=True)
    db.add(dt); db.commit(); db.refresh(dt)
    return dt


def test_assignment_has_portal_workflow_columns(db):
    dt = _dt(db)
    a = LarcAssignment(chart_number="M1", patient_name="Doe, J", device_type_id=dt.id,
                       source_flow="in_stock", status="new",
                       sms_consent=True, sms_consented_by="patient:self",
                       portal_token_version=0, needs_allocation_no_stock=False)
    db.add(a); db.commit(); db.refresh(a)
    assert a.sms_consent is True
    assert a.portal_token_version == 0
    assert a.needs_allocation_no_stock is False
    assert a.sms_consented_at is None
```

- [ ] **Step 2: Run, expect FAIL** — `cd backend && source venv/bin/activate && pytest tests/test_larc_portal_workflow_model.py -q` → TypeError invalid kwarg.

- [ ] **Step 3: Add columns** in `LarcAssignment` (after the `reason_icd10` column from the prior feature):

```python
    sms_consent          = Column(Boolean, default=False, nullable=False)
    sms_consented_at     = Column(DateTime, nullable=True)
    sms_consented_by     = Column(String(200), nullable=True)
    portal_token_version = Column(Integer, default=0, nullable=False)
    needs_allocation_no_stock = Column(Boolean, default=False, nullable=False)
```

Confirm `Boolean`, `Integer`, `DateTime`, `String` are imported at the top of `larc.py` (they are — used elsewhere).

- [ ] **Step 4: Migrations** — in `backend/app/database.py` `needed` list (after the `reason_icd10` entry):

```python
        ("larc_assignments", "sms_consent", "BOOLEAN DEFAULT FALSE"),
        ("larc_assignments", "sms_consented_at", "DATETIME"),
        ("larc_assignments", "sms_consented_by", "VARCHAR(200)"),
        ("larc_assignments", "portal_token_version", "INTEGER DEFAULT 0"),
        ("larc_assignments", "needs_allocation_no_stock", "BOOLEAN DEFAULT FALSE"),
```

- [ ] **Step 5: Run, expect PASS.**
- [ ] **Step 6: Commit** — `git add -A && git commit -m "feat(larc): assignment columns for portal + sms consent + no-stock flag"`

## Task 2: `LarcPayment` model

**Files:** Create `backend/app/models/larc_payment.py`; Test `backend/tests/test_larc_payment_model.py`

- [ ] **Step 1: Failing test** — `backend/tests/test_larc_payment_model.py`:

```python
from app.models.larc_payment import LarcPayment


def test_larc_payment_roundtrip(db):
    p = LarcPayment(assignment_id="a-1", kind="larc_patient_responsibility",
                    status="requested", amount_requested=120.00,
                    stripe_checkout_session_id="cs_test_1", checkout_url="https://x")
    db.add(p); db.commit(); db.refresh(p)
    assert p.id and p.status == "requested" and float(p.amount_requested) == 120.00
```

- [ ] **Step 2: Run, expect FAIL** (module missing).

- [ ] **Step 3: Create the model** (mirror `app/models/stripe_payment.py::PelletPayment`):

```python
"""Stripe payment rows for a LARC patient-responsibility charge."""
from __future__ import annotations
from datetime import datetime
from app.utils.dt import now_utc_naive
from sqlalchemy import Column, String, Numeric, DateTime, JSON
from app.database import Base
from app.utils.ids import gen_uuid   # match the id helper used by other larc models


class LarcPayment(Base):
    __tablename__ = "larc_payments"

    id          = Column(String(36), primary_key=True, default=gen_uuid)
    assignment_id = Column(String(36), index=True, nullable=False)
    kind        = Column(String(40), default="larc_patient_responsibility", nullable=False)
    status      = Column(String(20), default="requested", nullable=False)  # requested|paid|failed|expired|refunded
    amount_requested = Column(Numeric(10, 2), nullable=True)
    amount_paid      = Column(Numeric(10, 2), nullable=True)
    stripe_checkout_session_id = Column(String(255), index=True, nullable=True)
    stripe_payment_intent_id   = Column(String(255), index=True, nullable=True)
    checkout_url     = Column(String(600), nullable=True)
    last_event_payload = Column(JSON, nullable=True)
    requested_at = Column(DateTime, default=now_utc_naive, nullable=False)
    paid_at      = Column(DateTime, nullable=True)
```

Before writing, open `app/models/larc.py` and copy its exact id-default pattern (column type + default callable) — use the SAME pattern for `id` here (the snippet above assumes `gen_uuid` from `app.utils.ids`; if larc models use a different default, match it). Add `from app.models import larc_payment` to wherever models are imported for table creation if needed (check `app/database.py` import block / `Base.metadata`).

- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(larc): LarcPayment model"`

---

# GROUP B — Milestone tracks

## Task 3: Remove `appt_scheduled` from catalogs + buckets

**Files:** Modify `backend/app/services/larc/workflow.py`; Test `backend/tests/test_larc_track_changes.py`

- [ ] **Step 1: Failing test** — `backend/tests/test_larc_track_changes.py`:

```python
from app.services.larc.workflow import (
    IN_STOCK_MILESTONES, PHARMACY_ORDER_MILESTONES, ALL_BUCKETS)


def test_no_appt_scheduled_milestone():
    kinds_in = [k for k, *_ in IN_STOCK_MILESTONES]
    kinds_ph = [k for k, *_ in PHARMACY_ORDER_MILESTONES]
    assert "appt_scheduled" not in kinds_in
    assert "appt_scheduled" not in kinds_ph
    assert "appt_scheduled" not in ALL_BUCKETS
```

- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Edit** `workflow.py`: delete the `("appt_scheduled", …)` tuple from `IN_STOCK_MILESTONES` and `PHARMACY_ORDER_MILESTONES`; delete `"appt_scheduled"` from `ALL_BUCKETS`; in `assignment_buckets()` delete the block:
```python
    if done("appt_scheduled") and not done("device_checked_out"):
        out.add("appt_scheduled")
```
and ensure the checkout bucket triggers off the prior step instead — change the checked-out gate so a device that is allocated/received but not checked out still surfaces (replace the deleted block with):
```python
    # appt step removed — a benefits-verified, device-ready assignment goes
    # straight to "ready to check out".
    if done("benefits_verified") and not done("device_checked_out") \
            and (a.device_id or done("device_received")):
        out.add("checked_out")  # "ready to check out" lane
```
(Read the existing `checked_out` bucket logic first and merge rather than duplicate.)

- [ ] **Step 4: Run, expect PASS;** also run `pytest tests/ -q -k larc` (no regressions).
- [ ] **Step 5: Commit** — `git commit -am "feat(larc): drop appt_scheduled milestone + bucket"`

## Task 4: Patient-owned drops the `billed` milestone

**Files:** Modify `backend/app/services/larc/workflow.py` (`spawn_milestones`); Test append to `test_larc_track_changes.py`

- [ ] **Step 1: Failing test** (append):

```python
from app.models.larc import LarcAssignment, LarcDevice, LarcDeviceType
from app.models.larc import LarcMilestone


def _ph_assignment_with_patient_device(db):
    dt = LarcDeviceType(name="Kyleena", category="larc", default_flow="pharmacy_order", is_active=True)
    db.add(dt); db.commit(); db.refresh(dt)
    dev = LarcDevice(our_id="P-1", device_type_id=dt.id, status="assigned", ownership="patient_owned")
    db.add(dev); db.commit(); db.refresh(dev)
    a = LarcAssignment(chart_number="M9", patient_name="Roe, P", device_type_id=dt.id,
                       device_id=dev.id, source_flow="pharmacy_order", status="new")
    db.add(a); db.commit(); db.refresh(a)
    return a


def test_patient_owned_billed_not_applicable(db):
    from app.services.larc.workflow import spawn_milestones
    a = _ph_assignment_with_patient_device(db)
    spawn_milestones(db, a)
    billed = db.query(LarcMilestone).filter(
        LarcMilestone.assignment_id == a.id, LarcMilestone.kind == "billed").first()
    # For a patient-owned device, billed is spawned not_applicable (or absent).
    assert billed is None or billed.status == "not_applicable"
```

- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Edit `spawn_milestones`** — after creating milestone rows, when the bound device (or, if no device yet, a pharmacy_order flow which is patient-owned by definition) is patient-owned, set the `billed` milestone's status to `"not_applicable"`. Read the current function; add, before commit:
```python
    # Patient-owned devices are never billed by WWC — mark the billing step N/A.
    dev = assignment.device
    is_patient_owned = (dev.ownership == "patient_owned") if dev else (assignment.source_flow == "pharmacy_order")
    if is_patient_owned:
        for m in assignment.milestones:
            if m.kind == "billed":
                m.status = "not_applicable"
```
(Use the same session/flush pattern the function already uses.)

- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** — `git commit -am "feat(larc): billed milestone not-applicable for patient-owned"`

## Task 5: `patient_track` projector

**Files:** Create `backend/app/services/larc/patient_track.py`; Test `backend/tests/test_larc_patient_track.py`

- [ ] **Step 1: Failing test** — `backend/tests/test_larc_patient_track.py`:

```python
from app.models.larc import LarcAssignment, LarcDeviceType, LarcMilestone
from app.services.larc.workflow import spawn_milestones
from app.services.larc.patient_track import patient_track


def _mk(db, flow):
    dt = LarcDeviceType(name="Mirena", category="larc", default_flow="pharmacy_order", is_active=True)
    db.add(dt); db.commit(); db.refresh(dt)
    a = LarcAssignment(chart_number="T1", patient_name="Doe, J", device_type_id=dt.id,
                       source_flow=flow, status="new")
    db.add(a); db.commit(); db.refresh(a)
    spawn_milestones(db, a); db.commit()
    return a


def test_pharmacy_track_shape(db):
    a = _mk(db, "pharmacy_order")
    t = patient_track(a)
    assert t["track"] == "pharmacy"
    assert [s["key"] for s in t["steps"]] == [
        "request_received", "enrollment_completed", "enrollment_faxed",
        "device_received", "patient_notified"]
    assert t["steps"][0]["status"] == "done"        # request received on create
    assert t["steps"][1]["status"] == "current"


def test_practice_track_shape(db):
    a = _mk(db, "in_stock")
    t = patient_track(a)
    assert t["track"] == "practice_owned"
    assert [s["key"] for s in t["steps"]] == [
        "request_received", "responsibility_determined", "responsibility_satisfied",
        "device_allocated", "patient_notified"]
```

- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** `backend/app/services/larc/patient_track.py`:

```python
"""Project a LarcAssignment's internal milestones onto the 5 patient-visible
status steps. Used by the patient portal tracker and per-step notifications."""
from __future__ import annotations
from app.models.larc import LarcAssignment

_PHARMACY = [
    ("request_received",     "Provider Request Received", None),
    ("enrollment_completed", "Enrollment Form Completed", "enrollment_signed"),
    ("enrollment_faxed",     "Enrollment Form Faxed",     "request_faxed"),
    ("device_received",      "Device Received",           "device_received"),
    ("patient_notified",     "Patient Notified",          "patient_notified"),
]
_PRACTICE = [
    ("request_received",         "Provider Request Received",       None),
    ("responsibility_determined","Patient Responsibility Determined","benefits_verified"),
    ("responsibility_satisfied", "Patient Responsibility Satisfied", "__paid__"),
    ("device_allocated",         "Device Allocated",                "__allocated__"),
    ("patient_notified",         "Patient Notified",                "patient_notified"),
]


def _done(a: LarcAssignment, kind: str) -> bool:
    if kind is None:
        return True                       # request_received: true once the row exists
    if kind == "__paid__":
        return a.patient_paid_at is not None
    if kind == "__allocated__":
        return a.device_id is not None
    for m in (a.milestones or []):
        if m.kind == kind:
            return m.status in ("done", "skipped", "not_applicable")
    return False


def patient_track(a: LarcAssignment) -> dict:
    track = "pharmacy" if a.source_flow == "pharmacy_order" else "practice_owned"
    spec = _PHARMACY if track == "pharmacy" else _PRACTICE
    steps, first_unfinished_marked = [], False
    for key, label, kind in spec:
        done = _done(a, kind)
        if done:
            status = "done"
        elif not first_unfinished_marked:
            status = "current"; first_unfinished_marked = True
        else:
            status = "upcoming"
        steps.append({"key": key, "label": label, "status": status})
    return {"track": track, "steps": steps}
```

- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(larc): patient_track status projector"`

---

# GROUP C — Auto-allocation

## Task 6: `try_auto_allocate` service

**Files:** Create `backend/app/services/larc/allocation.py`; Test `backend/tests/test_larc_auto_allocate.py`

- [ ] **Step 1: Failing test** — `backend/tests/test_larc_auto_allocate.py`:

```python
from app.models.larc import LarcAssignment, LarcDevice, LarcDeviceType
from app.services.larc.workflow import spawn_milestones
from app.services.larc.allocation import try_auto_allocate
from app.utils.dt import now_utc_naive
from datetime import date


def _setup(db, with_stock):
    dt = LarcDeviceType(name="Liletta", category="larc", default_flow="pharmacy_order", is_active=True)
    db.add(dt); db.commit(); db.refresh(dt)
    if with_stock:
        db.add(LarcDevice(our_id="S-1", device_type_id=dt.id, status="unassigned", ownership="wwc_owned"))
    a = LarcAssignment(chart_number="A1", patient_name="Doe, J", device_type_id=dt.id,
                       source_flow="in_stock", status="in_progress",
                       benefits_verified_at=date.today(), patient_paid_at=now_utc_naive())
    db.add(a); db.commit(); db.refresh(a); spawn_milestones(db, a); db.commit()
    return a


def test_auto_allocate_binds_device(db):
    a = _setup(db, with_stock=True)
    res = try_auto_allocate(db, a); db.refresh(a)
    assert res["allocated"] is True
    assert a.device_id is not None
    assert a.needs_allocation_no_stock is False


def test_auto_allocate_no_stock_flags(db):
    a = _setup(db, with_stock=False)
    res = try_auto_allocate(db, a); db.refresh(a)
    assert res["allocated"] is False and res["reason"] == "no_stock"
    assert a.needs_allocation_no_stock is True


def test_auto_allocate_requires_gates(db):
    a = _setup(db, with_stock=True)
    a.patient_paid_at = None; db.commit()
    res = try_auto_allocate(db, a)
    assert res["allocated"] is False and res["reason"] == "gates_unmet"
```

- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** `allocation.py` — read `allocate_device()` in `routers/larc.py` (~2033-2117) and lift its atomic-claim logic here:

```python
"""Auto-allocate an in-stock device once benefits + payment are satisfied."""
from __future__ import annotations
from sqlalchemy.orm import Session
from sqlalchemy import update
from app.models.larc import LarcAssignment, LarcDevice
from app.services.larc.workflow import log_audit


def try_auto_allocate(db: Session, a: LarcAssignment) -> dict:
    if a.source_flow != "in_stock" or a.device_id:
        return {"allocated": False, "reason": "not_applicable"}
    if not (a.benefits_verified_at and a.patient_paid_at):
        return {"allocated": False, "reason": "gates_unmet"}

    # Atomic claim of one unassigned device of the right type (mirror allocate_device).
    dev = (db.query(LarcDevice)
             .filter(LarcDevice.device_type_id == a.device_type_id,
                     LarcDevice.status == "unassigned")
             .order_by(LarcDevice.expiration_date.asc().nullslast())
             .first())
    if not dev:
        a.needs_allocation_no_stock = True
        db.commit()
        return {"allocated": False, "reason": "no_stock"}

    claimed = db.execute(
        update(LarcDevice)
        .where(LarcDevice.id == dev.id, LarcDevice.status == "unassigned")
        .values(status="assigned")).rowcount
    if not claimed:
        return {"allocated": False, "reason": "race_lost"}

    a.device_id = dev.id
    a.needs_allocation_no_stock = False
    log_audit(db, actor="system:auto_allocate", action="device_assigned",
              assignment_id=a.id, device_id=dev.id,
              summary=f"Auto-allocated device {dev.our_id} on payment")
    db.commit()
    return {"allocated": True, "device_id": str(dev.id)}
```

Check `log_audit`'s real signature in `workflow.py` and match it exactly.

- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(larc): try_auto_allocate service"`

## Task 7: Wire payment → auto-allocate (+ $0 auto-satisfy)

**Files:** Modify `backend/app/routers/larc.py` (`record_payment` ~2005-2026, `verify_benefits` ~1506-1571); Test `backend/tests/test_larc_payment_autoallocate.py`

- [ ] **Step 1: Failing test** — `backend/tests/test_larc_payment_autoallocate.py`:

```python
from app.models.larc import LarcAssignment, LarcDevice, LarcDeviceType
from datetime import date


def _ready_in_stock(db):
    dt = LarcDeviceType(name="Liletta", category="larc", default_flow="pharmacy_order", is_active=True)
    db.add(dt); db.commit(); db.refresh(dt)
    db.add(LarcDevice(our_id="S-9", device_type_id=dt.id, status="unassigned", ownership="wwc_owned"))
    a = LarcAssignment(chart_number="B1", patient_name="Doe, J", device_type_id=dt.id,
                       source_flow="in_stock", status="in_progress", benefits_verified_at=date.today())
    db.add(a); db.commit(); db.refresh(a)
    return a


def test_record_payment_triggers_auto_allocate(client, db):
    a = _ready_in_stock(db)
    r = client.post(f"/api/larc/assignments/{a.id}/payment-received", json={"amount": 100})
    assert r.status_code == 200, r.text
    db.refresh(a)
    assert a.patient_paid_at is not None
    assert a.device_id is not None     # auto-allocated
```

- [ ] **Step 2: Run, expect FAIL** (payment recorded but no device bound).
- [ ] **Step 3: Implement** — in `record_payment`, after setting `patient_paid_at/by/amount` and before the response, add:
```python
    from app.services.larc.allocation import try_auto_allocate
    try_auto_allocate(db, a)
    db.refresh(a)
```
In `verify_benefits`, after marking benefits done, add the $0-responsibility auto-satisfy:
```python
    from app.services.larc.allocation import try_auto_allocate
    if a.source_flow == "in_stock" and (a.patient_responsibility in (None, 0)) and not a.patient_paid_at:
        a.patient_paid_at = now_utc_naive()
        a.patient_paid_by = "system:zero_responsibility"
        try_auto_allocate(db, a)
    db.refresh(a)
```
(Place imports at module top if preferred; ensure `now_utc_naive` is imported — it is.)

- [ ] **Step 4: Run, expect PASS;** `pytest tests/ -q -k larc`.
- [ ] **Step 5: Commit** — `git commit -am "feat(larc): payment + zero-responsibility trigger auto-allocation"`

---

# GROUP D — Notifications

## Task 8: `notify_larc_step` helper

**Files:** Create `backend/app/services/larc/notifications.py`; Test `backend/tests/test_larc_notifications.py`

- [ ] **Step 1: Failing test** — `backend/tests/test_larc_notifications.py`:

```python
from app.models.larc import LarcAssignment, LarcDeviceType
from app.models.patient_email import PatientEmail
from app.models.patient_sms import PatientSms
from app.services.larc.notifications import notify_larc_step


def _a(db, sms=False, cell="240-555-0001", email="p@example.com"):
    dt = LarcDeviceType(name="Mirena", category="larc", default_flow="pharmacy_order", is_active=True)
    db.add(dt); db.commit(); db.refresh(dt)
    a = LarcAssignment(chart_number="N1", patient_name="Doe, J", device_type_id=dt.id,
                       source_flow="pharmacy_order", status="new",
                       patient_email=email, patient_cell=cell, sms_consent=sms)
    db.add(a); db.commit(); db.refresh(a)
    return a


def test_notify_emails_always(db):
    a = _a(db, sms=False)
    notify_larc_step(db, a, "enrollment_completed")
    assert db.query(PatientEmail).count() >= 1
    assert db.query(PatientSms).count() == 0          # not opted in


def test_notify_texts_when_opted_in(db):
    a = _a(db, sms=True)
    notify_larc_step(db, a, "enrollment_completed")
    assert db.query(PatientSms).count() >= 1


def test_notify_idempotent_per_step(db):
    a = _a(db, sms=False)
    notify_larc_step(db, a, "enrollment_completed")
    notify_larc_step(db, a, "enrollment_completed")
    assert db.query(PatientEmail).count() == 1
```

- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** — read `send_patient_email`/`send_patient_sms` signatures (`app/services/patient_email.py`, `patient_sms.py`) and the `kind`→template mapping. Then:

```python
"""Fire per-step patient notifications for a LARC assignment (email always,
SMS only if the patient opted in). Idempotent per (assignment, step)."""
from __future__ import annotations
import os
from sqlalchemy.orm import Session
from app.models.patient_email import PatientEmail
from app.services.patient_email import send_patient_email
from app.services.patient_sms import send_patient_sms

# step → notification kind (must match seeded EmailTemplate/SmsTemplate kinds)
STEP_KIND = {
    "responsibility_determined": "larc_responsibility_due",
    "responsibility_satisfied":  "larc_payment_receipt",
    "device_allocated":          "larc_device_allocated",
    "enrollment_completed":      "larc_enrollment_ready",
    "enrollment_faxed":          "larc_enrollment_faxed",
    "device_received":           "larc_device_received",
    "patient_notified":          "larc_ready",
}


def _portal_url() -> str:
    base = (os.environ.get("APP_BASE_URL") or "https://gw.waldorfwomenscare.com").rstrip("/")
    return f"{base}/larc-portal/login"


def _already_sent(db: Session, assignment_id: str, kind: str) -> bool:
    return (db.query(PatientEmail)
              .filter(PatientEmail.chart_number == assignment_id,   # we key context on assignment id
                      PatientEmail.kind == kind).first() is not None)


def notify_larc_step(db: Session, a, step: str, *, sent_by: str = "system") -> None:
    kind = STEP_KIND.get(step)
    if not kind:
        return
    if _already_sent(db, a.id, kind):
        return
    ctx = {
        "patient_name": (a.patient_first_name or a.patient_name or "").split(",")[0].strip(),
        "portal_url": _portal_url(),
        "amount": f"{a.patient_responsibility:.2f}" if a.patient_responsibility else "",
    }
    if a.patient_email:
        send_patient_email(db, kind=kind, to_email=a.patient_email, context=ctx,
                           sent_by=sent_by, chart_number=a.id)
    if a.sms_consent and a.patient_cell:
        send_patient_sms(db, kind=kind, to_phone=a.patient_cell, context=ctx,
                         sent_by=sent_by, chart_number=a.id, consent_override=True)
```

IMPORTANT: confirm `send_patient_email`/`send_patient_sms` parameter names against the real signatures (the reports show `send_patient_email(db, *, kind, to_email, context, sent_by, surgery_id=None, chart_number=None, ad_hoc_*)` and `send_patient_sms(db, *, kind, surgery=None, context, sent_by, to_phone=None, chart_number=None, ad_hoc_body=None, consent_override=False)`). Use `chart_number=a.id` as the idempotency/audit key and match `_already_sent` to whatever column actually stores it. If those helpers require a seeded template to exist, this test depends on Task 9 — if so, reorder: seed templates first, or have `_already_sent` + soft-fail make the email-count assertions hold (send_patient_email returns a row even when the template is missing? verify; if not, do Task 9 before Task 8's run).

- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(larc): notify_larc_step (email always, SMS opt-in, idempotent)"`

## Task 9: Seed LARC notification templates + wire to milestones

**Files:** Create `backend/scripts/seed_larc_portal_templates.py`; hook into the same init path other seeds use (find how `seed_email_templates`/`seed_sms_templates` run — `init_db()` or a startup hook); Modify `routers/larc.py` to call `notify_larc_step` at each milestone completion; Test `backend/tests/test_larc_notify_wiring.py`

- [ ] **Step 1: Failing test** — `backend/tests/test_larc_notify_wiring.py`:

```python
from app.models.larc import LarcAssignment, LarcDeviceType
from app.models.patient_email import PatientEmail


def _a(db):
    dt = LarcDeviceType(name="Mirena", category="larc", default_flow="pharmacy_order", is_active=True)
    db.add(dt); db.commit(); db.refresh(dt)
    a = LarcAssignment(chart_number="W1", patient_name="Doe, J", device_type_id=dt.id,
                       source_flow="in_stock", status="in_progress", patient_email="p@example.com")
    db.add(a); db.commit(); db.refresh(a)
    return a


def test_benefits_fires_responsibility_notice(client, db):
    a = _a(db)
    r = client.post(f"/api/larc/assignments/{a.id}/benefits", json={
        "allowed_amount": 900, "deductible": 0, "deductible_met": 0, "copay": 0,
        "coinsurance_pct": 0, "oop_max": 0, "oop_met": 0})
    assert r.status_code == 200, r.text
    assert db.query(PatientEmail).filter(
        PatientEmail.kind == "larc_responsibility_due").count() == 1
```

- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3a: Seed script** `seed_larc_portal_templates.py` — mirror `seed_email_templates.py`/`seed_sms_templates.py`, upserting `EmailTemplate` and `SmsTemplate` rows for the 7 kinds in `STEP_KIND` (subject/body with `{{patient_name}}`, `{{portal_url}}`, `{{amount}}`, `{{practice_phone}}`). Register it in the same place the existing template seeds are invoked at init so tests (which build the schema fresh) have them — read how `seed_email_templates` is wired and follow it.

- [ ] **Step 3b: Wire notifications** in `routers/larc.py`:
  - `verify_benefits`: after marking benefits done, `notify_larc_step(db, a, "responsibility_determined")`.
  - `record_payment` / zero-responsibility path / Stripe branch: `notify_larc_step(db, a, "responsibility_satisfied")`.
  - `try_auto_allocate` success path → caller fires `notify_larc_step(db, a, "device_allocated")` (do it in the router callers, not the service, to keep the service pure — OR have the service return allocated and let callers notify).
  - enrollment signed webhook / `request_faxed` / `device_received` / `notify` endpoint: fire `enrollment_completed` / `enrollment_faxed` / `device_received` / `patient_notified` respectively. (Find each milestone-completion site — `apply_webhook_event` in `enrollment_sender.py` for signed/faxed; `receive_device` for received; `/notify` for patient_notified.)

- [ ] **Step 4: Run, expect PASS;** `pytest tests/ -q -k "larc"`.
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(larc): seed notification templates + fire per-step notifications"`

---

# GROUP E — Stripe

## Task 10: LARC checkout session

**Files:** Modify `backend/app/services/stripe_payments.py` (add a LARC kind/wrapper); Test `backend/tests/test_larc_stripe_checkout.py`

- [ ] **Step 1: Failing test** — mock Stripe like the existing stripe tests do (read `tests/` for the existing Stripe checkout test + how Stripe is monkeypatched). Test that calling the LARC checkout creator persists a `LarcPayment(status="requested")` with a `checkout_url`. Write the test to match the existing mock pattern (e.g. `monkeypatch` `stripe.checkout.Session.create`). Assert a `LarcPayment` row exists after the call.

- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** `create_larc_checkout(db, assignment, amount, actor="patient") -> dict` in `stripe_payments.py` mirroring the surgery/pellet path: create a Stripe Checkout Session with metadata `{"larc_assignment_id": a.id, "kind": "larc_patient_responsibility"}`, persist `LarcPayment(status="requested", amount_requested=amount, stripe_checkout_session_id=…, checkout_url=…)`, return `{"checkout_url": …, "payment_id": …}`. Reuse the existing 15-min idempotency-window behavior if practical.

- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(larc): Stripe checkout for patient responsibility"`

## Task 11: Stripe webhook LARC branch

**Files:** Modify `backend/app/routers/stripe_payments.py` (webhook dispatch ~296-303 + a new `_handle_larc_session_completed`); Test `backend/tests/test_larc_stripe_webhook.py`

- [ ] **Step 1: Failing test** — build a fake `checkout.session.completed` event whose metadata has `larc_assignment_id`, with a pre-seeded `LarcPayment(status="requested")` + an in-stock assignment (benefits verified, stock available). POST to `/api/stripe/webhook` exactly like the existing surgery webhook test (copy its signature-bypass/monkeypatch). Assert: `LarcPayment.status == "paid"`, `assignment.patient_paid_at` set, `assignment.device_id` set (auto-allocated), and a `larc_payment_receipt` PatientEmail exists.

- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** — add a LARC discriminator + branch in the webhook dispatch:
```python
    elif event_type == "checkout.session.completed" and (obj.get("metadata") or {}).get("larc_assignment_id"):
        _handle_larc_session_completed(db, obj); db.commit()
```
placed BEFORE the generic surgery `checkout.session.completed` branch. Implement `_handle_larc_session_completed(db, obj)`: find `LarcPayment` by `stripe_checkout_session_id`; set `status="paid"`, `amount_paid`, `paid_at`, `stripe_payment_intent_id`; load the assignment, set `patient_paid_at/by/amount`; `notify_larc_step(db, a, "responsibility_satisfied")`; `try_auto_allocate(db, a)`; if allocated, `notify_larc_step(db, a, "device_allocated")`. Reuse `ProcessedStripeEvent` dedup already wrapping the dispatch.

- [ ] **Step 4: Run, expect PASS;** `pytest tests/ -q -k "stripe or larc"`.
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(larc): Stripe webhook branch → paid + auto-allocate + notify"`

---

# GROUP F — Portal backend

## Task 12: LARC portal auth

**Files:** Create `backend/app/services/larc/portal_auth.py`; Test `backend/tests/test_larc_portal_auth.py`

- [ ] **Step 1: Failing test** — `backend/tests/test_larc_portal_auth.py`: seed an active `LarcAssignment` with `patient_dob` + `patient_cell`; test `match_assignment(db, dob, last4)` returns it; test `issue_portal_token`/`decode_portal_token` round-trips with `scope == "larc_portal"` and `sub == assignment.id`; test a bumped `portal_token_version` invalidates an old token (decode still returns payload, but the router dependency rejects — assert via the `lpv` claim mismatch helper).

- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** — clone `backend/app/services/pellet/portal_auth.py` exactly, substituting: `PelletPatient`→`LarcAssignment`; claim `scope="larc_portal"`; version claim `ppv`→`lpv` reading `a.portal_token_version`; `match_patient`→`match_assignment(db, dob, last4)` querying active assignments (`is_active == True`) ordered by `created_at desc`, first match on DOB + last-4 of `patient_cell`. Keep the bcrypt OTP challenge/verify code identical. Reuse `_send_sms` (Twilio).

- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(larc): patient portal auth (SMS OTP, clone of pellet)"`

## Task 13: LARC portal router

**Files:** Create `backend/app/routers/patient_larc.py`; Modify `backend/app/main.py` (register router); Test `backend/tests/test_larc_portal_api.py`

- [ ] **Step 1: Failing test** — `backend/tests/test_larc_portal_api.py`: seed an assignment; mint a portal token via `portal_auth.issue_portal_token`; call `GET /api/larc-portal/dashboard` with `Authorization: Bearer <token>` → 200 with `track` + `steps` from `patient_track`; call without token → 401. Test `POST /api/larc-portal/payments/checkout` returns a `checkout_url` (mock Stripe). (Login/verify OTP can be a lighter test that `POST /login` returns a challenge token.)

- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** — clone the structure of `backend/app/routers/patient_pellet.py`, mounted `prefix="/larc-portal"` at `/api`. Endpoints: `POST /login`, `POST /verify` (on first successful verify set `a.sms_consent=True, sms_consented_at, sms_consented_by="patient:self-service"` ONLY if the verify payload includes an opt-in flag — accept `{"sms_opt_in": bool}`), `GET /dashboard` (→ `patient_track(a)` + payment summary {responsibility, paid} + enrollment status + documents list), `GET /payments` + `POST /payments/checkout` (→ `create_larc_checkout`), `GET /enrollment` + `GET /enrollment/sign-link/{envelope_id}` (→ `get_embedded_sign_link`) + `GET /enrollment/signed-pdf/{envelope_id}`, `GET /documents`. Use a `require_larc_portal_token` dependency mirroring `require_pellet_token` (validate Bearer, scope, `lpv` vs `a.portal_token_version`, non-GET allowed for patient token). Register in `main.py` next to the other portal routers.

- [ ] **Step 4: Run, expect PASS;** `pytest tests/ -q -k "larc_portal"`.
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(larc): patient portal API (/api/larc-portal)"`

---

# GROUP G — Frontend: staff-side changes

## Task 14: Gate insurance upload by fulfillment path

**Files:** Modify `frontend/src/pages/LarcAssignment.jsx` (InsuranceCardCard render ~1455). Verify via `npm run build` + manual.

- [ ] **Step 1:** Find where `InsuranceCardCard` (or the insurance section) is rendered. Wrap its render in a condition so it only shows when the assignment's `source_flow === 'pharmacy_order'`. (The assignment dict already exposes `source_flow`.)
- [ ] **Step 2:** `cd frontend && npm run build` → `✓ built`.
- [ ] **Step 3: Commit** — `git commit -am "feat(larc): insurance upload only for pharmacy flow"`
- [ ] **Step 4: Manual:** on an in-stock/office assignment, the insurance-card card is absent; on a pharmacy assignment, it shows.

## Task 15: Patient-owned billing UI (no claim / no billed step)

**Files:** Modify `frontend/src/pages/LarcAssignment.jsx` (`BilledBody` ~1175). Build + manual.

- [ ] **Step 1:** In `BilledBody`, when `device_ownership === 'patient_owned'`, render no claim-# entry and no "Insertion billed" step — show only an optional "Mark complete (no claim)" action calling the existing `/close-out`, or nothing if already complete. Ensure the milestone list rendering skips a `not_applicable` `billed` milestone (filter milestones with `status === 'not_applicable'` from the displayed steps).
- [ ] **Step 2:** `npm run build` → `✓ built`.
- [ ] **Step 3: Commit** — `git commit -am "feat(larc): hide billing/claim for patient-owned"`
- [ ] **Step 4: Manual:** patient-owned assignment shows no claim entry and no billed step; practice-owned unchanged.

## Task 16: Move MA checkout to dashboard; remove from detail

**Files:** Modify `frontend/src/pages/LarcAssignment.jsx` (remove `CheckoutPlaceholderBody` ~1045 + its case wiring); Modify `frontend/src/pages/Larc.jsx` (add a checkout card). Build + manual.

- [ ] **Step 1:** Delete `CheckoutPlaceholderBody` and the milestone-case branch that renders it on the detail page. Confirm nothing else references it. ALSO delete the now-dead `ApptBody` component and its `case 'appt_scheduled':` branch (the milestone no longer spawns after Task 3, so this card is dead code) — confirm no other reference.
- [ ] **Step 2:** On the LARC dashboard (`Larc.jsx`), add a "Devices Ready to Check Out" card that fetches `GET /larc/checkouts/ready` and posts `/checkout-direct` (mirror the `LarcCheckoutCard`/`LarcCheckoutRow` components in `MyChecklist.jsx` ~642-746 — import/extract or replicate the same logic).
- [ ] **Step 3:** `npm run build` → `✓ built`.
- [ ] **Step 4: Commit** — `git commit -am "feat(larc): MA checkout on dashboard, removed from detail page"`
- [ ] **Step 5: Manual:** detail page has no checkout card; dashboard lists ready devices and checkout-direct works; My Checklist card still works.

---

# GROUP H — Frontend: patient portal

> No JS test runner — each task ends with `npm run build` (must pass) + a manual checklist. Clone the pellet-portal files and adapt; do not hand-rewrite from scratch.

## Task 17: Portal API client + auth pages

**Files:** Create `frontend/src/lib/larc-portal-api.js`, `src/pages/larc-portal/LarcPortalLogin.jsx`, `LarcPortalVerify.jsx`. Build.

- [ ] **Step 1:** Clone `frontend/src/lib/pellet-portal-api.js` → `larc-portal-api.js`, changing base path to `/api/larc-portal` and localStorage keys to `wwc.larc-portal.token` / `wwc.larc-portal.aid`.
- [ ] **Step 2:** Clone `PelletPortalLogin.jsx` / `PelletPortalVerify.jsx` → `LarcPortalLogin.jsx` / `LarcPortalVerify.jsx`, pointing at the new api client. On verify, include the SMS opt-in checkbox value (`sms_opt_in`) in the verify call.
- [ ] **Step 3:** `npm run build` (will succeed once routes added in Task 20; if it errors only on unused imports, fix). 
- [ ] **Step 4: Commit** — `git add -A && git commit -m "feat(larc-portal): api client + auth pages"`

## Task 18: Portal shell + status tracker

**Files:** Create `src/pages/larc-portal/LarcPortalShell.jsx`, `LarcStatus.jsx`. Build.

- [ ] **Step 1:** Clone `PelletPortalShell.jsx` → `LarcPortalShell.jsx` (nav items: Status, Payments, Enrollment, Documents; use the larc api client; staff-preview `?staff_token=` supported as in the original).
- [ ] **Step 2:** Create `LarcStatus.jsx` — fetch `GET /larc-portal/dashboard`, render the `steps` as a vertical step tracker modeled on surgery `JourneyTimeline` (`frontend/src/pages/portal/Dashboard.jsx` ~264): done = filled check, current = ringed, upcoming = light. Show the track name.
- [ ] **Step 3:** `npm run build`.
- [ ] **Step 4: Commit** — `git add -A && git commit -m "feat(larc-portal): shell + status tracker"`

## Task 19: Payments, Enrollment, Documents pages

**Files:** Create `src/pages/larc-portal/LarcPortalPayments.jsx`, `LarcPortalEnrollment.jsx`, `LarcPortalDocuments.jsx`. Build.

- [ ] **Step 1:** `LarcPortalPayments.jsx` — clone `pellet-portal/PelletPayments.jsx` (or surgery `portal/Payments.jsx`): show balance from `GET /larc-portal/payments`; "Pay now" → `POST /larc-portal/payments/checkout` → redirect to `checkout_url`. Hide/disable when responsibility is 0.
- [ ] **Step 2:** `LarcPortalEnrollment.jsx` — clone surgery `portal/Consent.jsx`: list enrollment envelope(s) from `GET /larc-portal/enrollment`; "Sign now" → `GET /larc-portal/enrollment/sign-link/{id}` → redirect to embedded sign URL. Only shown for the pharmacy track.
- [ ] **Step 3:** `LarcPortalDocuments.jsx` — clone surgery `portal/Documents.jsx`: list signed enrollment PDF + payment receipts.
- [ ] **Step 4:** `npm run build`.
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(larc-portal): payments, enrollment signing, documents"`

## Task 20: Wire portal routes

**Files:** Modify `frontend/src/App.jsx` (imports + public routes). Build.

- [ ] **Step 1:** Add imports for the new pages and a public route block (mirroring `/pellet-portal/*`):
```jsx
        <Route path="/larc-portal" element={<Navigate to="/larc-portal/login" replace />} />
        <Route path="/larc-portal/login" element={<LarcPortalLogin />} />
        <Route path="/larc-portal/verify" element={<LarcPortalVerify />} />
        <Route path="/larc-portal/home" element={<LarcPortalShell />}>
          <Route index element={<LarcStatus />} />
          <Route path="payments" element={<LarcPortalPayments />} />
          <Route path="enrollment" element={<LarcPortalEnrollment />} />
          <Route path="documents" element={<LarcPortalDocuments />} />
        </Route>
```
- [ ] **Step 2:** `npm run build` → `✓ built`.
- [ ] **Step 3: Commit** — `git add -A && git commit -m "feat(larc-portal): routes"`
- [ ] **Step 4: Manual (post-deploy):** `/larc-portal/login` → OTP → status tracker renders the right track; pay redirects to Stripe; pharmacy enrollment "Sign now" opens BoldSign; documents list.

---

# GROUP I — Verification & deploy

## Task 21: Full suite, build, deploy

- [ ] **Step 1:** `cd backend && source venv/bin/activate && python -m pytest -q -p no:cacheprovider` → all pass (prior baseline 1257 + new tests; no regressions).
- [ ] **Step 2:** `cd frontend && npm run build` → `✓ built`.
- [ ] **Step 3:** Seed templates in prod path confirmed (the seed runs at init). 
- [ ] **Step 4 (deploy, only when the user asks):** backend first (portal API + webhook), then frontend:
```bash
SHA=$(git rev-parse --short HEAD)
gcloud builds submit backend/  --tag=us-east4-docker.pkg.dev/wwc-solutions/app/backend:$SHA  --project=wwc-solutions --region=us-east4
gcloud builds submit frontend/ --tag=us-east4-docker.pkg.dev/wwc-solutions/app/frontend:$SHA --project=wwc-solutions --region=us-east4
gcloud run services update backend  --region=us-east4 --project=wwc-solutions --image=...backend:$SHA
gcloud run services update frontend --region=us-east4 --project=wwc-solutions --image=...frontend:$SHA
```
New `larc_*` columns added on boot by `_apply_lightweight_migrations()`; `larc_payments` via `Base.metadata`. No new Stripe webhook events needed (reuses `checkout.session.completed`).

---

## Notes / risks
- **Notification idempotency key:** the plan keys on `chart_number=a.id` into PatientEmail/PatientSms. Verify that column is free for this use in LARC context; if surgery semantics conflict, add a dedicated `larc_assignment_id` column to the audit tables instead (small migration) — decide during Task 8/9.
- **send_patient_email/sms exact signatures** must be confirmed before Task 8 (the report gives them but verify in code).
- **Template seeding at test time:** if `send_patient_email` requires a template row, ensure the LARC template seed runs in the test init path (Task 9) — may require doing Task 9's seed before Task 8 passes; reorder if needed.
- **Portal multi-request:** login resolves to most-recent active assignment; listing/switching multiple is a dashboard nicety — keep MVP to most-recent.
- **`log_audit` / id-default / model import** patterns: confirm against existing larc code before writing new models/services.
