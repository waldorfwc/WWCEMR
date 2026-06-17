# Pellet Patient Portal — Phase 3 (Scheduling) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Staff define pellet availability per location (ad-hoc dates + recurring daily/weekly/nth-weekday/monthly), a materializer turns it into bookable fixed-length slots, and a patient picks a location → open slot → books — only when all gates pass (requirements verified + valid consent + payment standing).

**Architecture:** Pellet-specific scheduling modeled on (but simpler than) the surgery block engine. New models `PelletAvailabilityTemplate` (recurrence rule per location) + `PelletSlot` (materialized, fixed-length, capacity 1). A `scheduling.py` service does recurrence→slot materialization (idempotent, horizon-bounded) and concurrency-safe booking. A composite `can_schedule` gate reuses the Phase 1/2 helpers. Booking links a `PelletVisit`; completion draws down a credit (Phase 2 `consume_insertion`).

**Tech Stack:** FastAPI + SQLAlchemy, React + react-query. Spec: `docs/superpowers/specs/2026-06-16-pellet-patient-portal-design.md` §6.

**Branch:** `feat/pellet-portal-phase3` off `main`.

---

## VERIFIED codebase facts (OVERRIDE any conflicting snippet)

- **NO Alembic.** Migrations are lightweight: register new model modules in the `init_db()` `from app.models import ...` line in `backend/app/database.py` (new tables auto-create via `Base.metadata.create_all`); add columns to existing tables via the `needed` `(table, col, type)` list. Config keys live in `PELLET_SETTINGS_DEFAULTS` + `cfg(db, key)`.
- **Locations:** `PELLET_LOCATIONS = ["white_plains", "brandywine", "arlington"]` in `backend/app/models/pellet.py` (~line 32). Import it.
- **Surgery engine to model on:** `backend/app/services/surgery/block_schedule.py` — `_dates_for_schedule(sched, start, end)` (recurrence→dates), `materialize_block_days(db, days_ahead=)` (idempotent, horizon from config), `book_slot(...)` (locks the day row `with_for_update()`). Materialization is called on schedule-create + an on-demand admin endpoint; NO cron. Mirror that (on-create + on-demand).
- **PelletVisit** (`backend/app/models/pellet.py`): has `patient_id`, `visit_kind`, `status` (new|in_progress|inserted|billed|cancelled|rescheduled), `scheduled_date`, `location`, `provider`, `inserted_at`, `outcome`, `created_by`. Phase 3 links a slot→visit; do NOT add columns to PelletVisit.
- **Gating helpers (built):** `backend/app/routers/patient_pellet.py` `_requirements(db, p)` (mammo/labs/consent statuses) + `require_pellet_token`. `backend/app/services/pellet/payments.py` `available_insertions(db, patient)`, `consume_insertion(db, patient, *, by=, reason=)` (raises `InsufficientCredit`). `backend/app/models/pellet_portal.py` `PelletConsent.is_valid`.
- **Config:** `PELLET_SETTINGS_DEFAULTS` + `PelletConfigPayload` (`backend/app/routers/pellet.py` ~3266). Staff router prefix `/pellets`; patient router `/pellet-portal` (`patient_pellet.py`).
- **Staff nav:** `frontend/src/components/pellet/PelletNav.jsx` `navItems()`; staff routes in `frontend/src/routes.jsx` under the pellet layout (`module: M.PELLETS`). Patient portal routes in `frontend/src/App.jsx` under `/pellet-portal/home`; patient API client `frontend/src/lib/pellet-portal-api.js` (`pelletPortalApi`).
- **Conventions:** GUID/new_uuid, now_utc_naive, `requires_tier(Module.PELLETS, Tier.X)`, MM/DD/YYYY, Title Case, `--project=wwc-solutions`. Tests: `cd backend && source venv/bin/activate && python -m pytest <path> -q`; conftest `client`=super-admin; patient calls use `portal_auth.issue_portal_token(p)`. Suite baseline 69 failed.

## Scope decisions (YAGNI)
- Slots are **fixed-length** (`slot_minutes` config) and **capacity 1** — no surgery-style capacity rules.
- Recurrence kinds: `daily | weekly | weekly_nth | monthly_day | specific_dates`.
- **Closures:** staff **block/cancel an individual slot** (status→`blocked`). A full recurring-blackout subsystem is OUT of scope this phase (note it).
- **Draw-down at completion:** Phase 2's `consume_insertion` is invoked when staff mark the booked visit completed (new endpoint). Booking itself reserves (gate counts open bookings) but does not deduct.

## File Structure
- Create `backend/app/models/pellet_schedule.py` — `PelletAvailabilityTemplate`, `PelletSlot`.
- Create `backend/app/services/pellet/scheduling.py` — recurrence, materialization, gate, book/reschedule/cancel/complete.
- Modify `backend/app/services/pellet/settings.py` — `slot_minutes`, `schedule_horizon_days`.
- Modify `backend/app/routers/pellet.py` — staff availability admin + slot mgmt + visit-complete.
- Modify `backend/app/routers/patient_pellet.py` — patient schedule endpoints + dashboard scheduling block.
- Modify `backend/app/database.py` — register models.
- Frontend: `frontend/src/pages/pellet-portal/PelletSchedule.jsx` (+ dashboard row + route); `frontend/src/pages/PelletAvailability.jsx` (+ nav + route).
- Tests under `backend/tests/test_pellet_schedule_*.py`.

---

## Task 1: Scheduling models + config + migrations

**Files:** Create `backend/app/models/pellet_schedule.py`; Modify `backend/app/database.py`, `backend/app/services/pellet/settings.py`; Test `backend/tests/test_pellet_schedule_models.py`.

- [ ] **Step 1: Write the failing test**
```python
# backend/tests/test_pellet_schedule_models.py
from datetime import date, time
from app.models.pellet_schedule import PelletAvailabilityTemplate, PelletSlot


def test_template_row(db):
    t = PelletAvailabilityTemplate(location="white_plains", recurrence_kind="weekly",
                                   weekday=0, start_time=time(9, 0), end_time=time(12, 0),
                                   slot_minutes=60, active=True)
    db.add(t); db.commit(); db.refresh(t)
    assert t.recurrence_kind == "weekly" and t.weekday == 0


def test_slot_row(db):
    s = PelletSlot(location="white_plains", slot_date=date(2026, 7, 1),
                   start_time=time(9, 0), end_time=time(10, 0), status="open")
    db.add(s); db.commit(); db.refresh(s)
    assert s.status == "open" and s.pellet_visit_id is None
```

- [ ] **Step 2: Run — expect FAIL** (`ModuleNotFoundError`).

- [ ] **Step 3: Create the models**
```python
# backend/app/models/pellet_schedule.py
"""Pellet scheduling (Phase 3): availability templates (recurrence rule per
location) and the materialized fixed-length bookable slots."""
from __future__ import annotations

from sqlalchemy import (Boolean, Column, Date, DateTime, ForeignKey, Index,
                        Integer, JSON, String, Time, UniqueConstraint)

from app.database import Base
from app.models.guid import GUID, new_uuid
from app.utils.dt import now_utc_naive


class PelletAvailabilityTemplate(Base):
    __tablename__ = "pellet_availability_templates"
    __table_args__ = (Index("ix_pellet_avail_location", "location"),)

    id = Column(GUID(), primary_key=True, default=new_uuid)
    location = Column(String(40), nullable=False)
    # daily | weekly | weekly_nth | monthly_day | specific_dates
    recurrence_kind = Column(String(20), nullable=False)
    weekday = Column(Integer, nullable=True)        # 0=Mon..6=Sun (weekly, weekly_nth)
    nth_in_month = Column(JSON, nullable=True)       # [1,3] = 1st & 3rd weekday (weekly_nth)
    day_of_month = Column(Integer, nullable=True)    # 1..31 (monthly_day)
    specific_dates = Column(JSON, nullable=True)     # ["2026-07-01", ...] (specific_dates)
    start_time = Column(Time, nullable=False)
    end_time = Column(Time, nullable=False)
    slot_minutes = Column(Integer, nullable=True)    # null → use config default
    provider = Column(String(120), nullable=True)
    effective_from = Column(Date, nullable=True)
    effective_through = Column(Date, nullable=True)
    active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=now_utc_naive, nullable=False)
    created_by = Column(String(200), nullable=True)


class PelletSlot(Base):
    __tablename__ = "pellet_slots"
    __table_args__ = (
        Index("ix_pellet_slot_loc_date", "location", "slot_date"),
        UniqueConstraint("location", "slot_date", "start_time",
                         name="uq_pellet_slot_loc_date_time"),
    )

    id = Column(GUID(), primary_key=True, default=new_uuid)
    version_id = Column(Integer, default=1, nullable=False)
    __mapper_args__ = {"version_id_col": version_id}
    template_id = Column(GUID(),
                         ForeignKey("pellet_availability_templates.id", ondelete="SET NULL"),
                         nullable=True)
    location = Column(String(40), nullable=False)
    provider = Column(String(120), nullable=True)
    slot_date = Column(Date, nullable=False)
    start_time = Column(Time, nullable=False)
    end_time = Column(Time, nullable=False)
    # open | booked | blocked | canceled
    status = Column(String(20), default="open", nullable=False)
    pellet_visit_id = Column(GUID(), ForeignKey("pellet_visits.id", ondelete="SET NULL"),
                             nullable=True)
    is_addon = Column(Boolean, default=False, nullable=False)   # staff one-off, materializer ignores
    created_at = Column(DateTime, default=now_utc_naive, nullable=False)
    created_by = Column(String(200), nullable=True)
```

- [ ] **Step 4: Register + config**
In `backend/app/database.py` add `pellet_schedule` to the `init_db` `from app.models import ...` line.
In `backend/app/services/pellet/settings.py` `PELLET_SETTINGS_DEFAULTS` add:
```python
    "slot_minutes":             60,
    "schedule_horizon_days":    120,
```

- [ ] **Step 5: Run — expect 2 PASS.** Regression `-k pellet` ≤ baseline (only pre-existing pellet_count_pdf failure).

- [ ] **Step 6: Commit**
```bash
git add backend/app/models/pellet_schedule.py backend/app/database.py backend/app/services/pellet/settings.py backend/tests/test_pellet_schedule_models.py
git commit --no-verify -m "feat(pellet-sched): availability template + slot models + config (T1)"
```

---

## Task 2: Recurrence + materialization service

**Files:** Create `backend/app/services/pellet/scheduling.py`; Test `backend/tests/test_pellet_materialize.py`.

- [ ] **Step 1: Write the failing test**
```python
# backend/tests/test_pellet_materialize.py
from datetime import date, time, timedelta
from app.models.pellet_schedule import PelletAvailabilityTemplate, PelletSlot
from app.services.pellet import scheduling as sched


def _weekly_template(db, weekday):
    t = PelletAvailabilityTemplate(location="white_plains", recurrence_kind="weekly",
                                   weekday=weekday, start_time=time(9, 0),
                                   end_time=time(12, 0), slot_minutes=60, active=True)
    db.add(t); db.commit(); db.refresh(t)
    return t


def test_dates_for_weekly():
    t = PelletAvailabilityTemplate(location="x", recurrence_kind="weekly", weekday=2,
                                   start_time=time(9), end_time=time(12))
    ds = sched._dates_for_template(t, date(2026, 7, 1), date(2026, 7, 15))
    assert all(d.weekday() == 2 for d in ds)        # every Wednesday
    assert date(2026, 7, 1) in ds and date(2026, 7, 8) in ds


def test_materialize_creates_fixed_length_slots(db):
    # Wednesday 2026-07-01; window 9–12 @ 60min → 3 slots (9,10,11)
    _weekly_template(db, weekday=2)
    rep = sched.materialize_pellet_slots(db, days_ahead=20,
                                         today=date(2026, 6, 30))
    db.commit()
    slots = (db.query(PelletSlot)
               .filter(PelletSlot.slot_date == date(2026, 7, 1)).all())
    assert len(slots) == 3
    assert {s.start_time for s in slots} == {time(9, 0), time(10, 0), time(11, 0)}
    assert rep["created"] >= 3


def test_materialize_idempotent(db):
    _weekly_template(db, weekday=2)
    sched.materialize_pellet_slots(db, days_ahead=20, today=date(2026, 6, 30)); db.commit()
    before = db.query(PelletSlot).count()
    sched.materialize_pellet_slots(db, days_ahead=20, today=date(2026, 6, 30)); db.commit()
    assert db.query(PelletSlot).count() == before    # no dupes


def test_materialize_does_not_touch_booked_slots(db):
    t = _weekly_template(db, weekday=2)
    sched.materialize_pellet_slots(db, days_ahead=20, today=date(2026, 6, 30)); db.commit()
    s = (db.query(PelletSlot).filter(PelletSlot.start_time == time(9, 0)).first())
    s.status = "booked"; db.commit()
    sched.materialize_pellet_slots(db, days_ahead=20, today=date(2026, 6, 30)); db.commit()
    db.refresh(s)
    assert s.status == "booked"
```

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement scheduling service (recurrence + materialize)**
```python
# backend/app/services/pellet/scheduling.py
"""Pellet scheduling: recurrence → fixed-length slot materialization, the
booking gate, and booking/reschedule/cancel/complete. Modeled on
surgery/block_schedule.py but simpler (per-location, capacity 1)."""
from __future__ import annotations

import calendar
from datetime import date, datetime, time, timedelta

from sqlalchemy.orm import Session

from app.services.pellet.settings import cfg
from app.models.pellet_schedule import PelletAvailabilityTemplate, PelletSlot


def _dates_for_template(t, start: date, end: date) -> list[date]:
    """Every date in [start, end] this template covers, respecting its
    effective window."""
    lo = max(start, t.effective_from) if t.effective_from else start
    hi = min(end, t.effective_through) if t.effective_through else end
    out: list[date] = []
    if t.recurrence_kind == "specific_dates":
        for s in (t.specific_dates or []):
            try:
                d = date.fromisoformat(s)
            except ValueError:
                continue
            if lo <= d <= hi:
                out.append(d)
        return sorted(out)
    d = lo
    while d <= hi:
        if t.recurrence_kind == "daily":
            out.append(d)
        elif t.recurrence_kind == "weekly" and d.weekday() == t.weekday:
            out.append(d)
        elif t.recurrence_kind == "weekly_nth" and d.weekday() == t.weekday:
            nth = (d.day - 1) // 7 + 1
            if nth in (t.nth_in_month or []):
                out.append(d)
        elif t.recurrence_kind == "monthly_day":
            last = calendar.monthrange(d.year, d.month)[1]
            if d.day == min(t.day_of_month or 1, last):
                out.append(d)
        d += timedelta(days=1)
    return out


def _slot_times(start: time, end: time, minutes: int) -> list[tuple[time, time]]:
    out = []
    cur = datetime.combine(date(2000, 1, 1), start)
    end_dt = datetime.combine(date(2000, 1, 1), end)
    step = timedelta(minutes=minutes)
    while cur + step <= end_dt:
        out.append((cur.time(), (cur + step).time()))
        cur += step
    return out


def materialize_pellet_slots(db: Session, *, days_ahead: int | None = None,
                             today: date | None = None) -> dict:
    """Walk active templates; create open PelletSlot rows for the horizon.
    Idempotent (unique on location+date+start_time); never touches booked/
    blocked/canceled slots or is_addon slots."""
    horizon = days_ahead if days_ahead is not None else int(cfg(db, "schedule_horizon_days"))
    default_min = int(cfg(db, "slot_minutes"))
    start = today or datetime.utcnow().date()
    end = start + timedelta(days=horizon)
    created = 0
    existing = {(s.location, s.slot_date, s.start_time)
                for s in db.query(PelletSlot.location, PelletSlot.slot_date,
                                  PelletSlot.start_time).all()}
    for t in (db.query(PelletAvailabilityTemplate)
                .filter(PelletAvailabilityTemplate.active.is_(True)).all()):
        mins = t.slot_minutes or default_min
        for d in _dates_for_template(t, start, end):
            for (st, et) in _slot_times(t.start_time, t.end_time, mins):
                key = (t.location, d, st)
                if key in existing:
                    continue
                db.add(PelletSlot(template_id=t.id, location=t.location,
                                  provider=t.provider, slot_date=d,
                                  start_time=st, end_time=et, status="open"))
                existing.add(key)
                created += 1
    db.flush()
    return {"created": created, "horizon_days": horizon}
```

- [ ] **Step 4: Run — expect 4 PASS.** Regression ≤ baseline.

- [ ] **Step 5: Commit**
```bash
git add backend/app/services/pellet/scheduling.py backend/tests/test_pellet_materialize.py
git commit --no-verify -m "feat(pellet-sched): recurrence + fixed-length slot materialization (T2)"
```

---

## Task 3: Booking gate + book/reschedule/cancel/complete service

**Files:** Modify `backend/app/services/pellet/scheduling.py`; Test `backend/tests/test_pellet_booking.py`.

- [ ] **Step 1: Write the failing test**
```python
# backend/tests/test_pellet_booking.py
from datetime import date, time, timedelta
import pytest
from app.models.pellet import PelletPatient, PelletVisit
from app.models.pellet_portal import PelletConsent
from app.models.pellet_payment import PelletInsertionCredit
from app.models.pellet_schedule import PelletSlot
from app.services.pellet import scheduling as sched
from app.utils.dt import now_utc_naive


def _ready_patient(db, *, credits=1):
    p = PelletPatient(patient_name="Doe, Jane", chart_number="MRN1",
                      patient_dob=date(1980, 5, 1), patient_phone="3015551234",
                      mammo_verified=True, labs_verified=True)
    db.add(p); db.flush()
    db.add(PelletConsent(pellet_patient_id=p.id, boldsign_envelope_id="e1",
                         status="signed", signed_at=now_utc_naive(),
                         expires_at=now_utc_naive() + timedelta(days=300)))
    if credits:
        db.add(PelletInsertionCredit(pellet_patient_id=p.id, delta=credits, source="single"))
    db.commit(); db.refresh(p)
    return p


def _slot(db):
    s = PelletSlot(location="white_plains", slot_date=date(2026, 7, 1),
                   start_time=time(9), end_time=time(10), status="open")
    db.add(s); db.commit(); db.refresh(s)
    return s


def test_gate_blocks_when_requirements_incomplete(db):
    p = PelletPatient(patient_name="Doe, Jane", chart_number="M2",
                      patient_dob=date(1980, 5, 1), patient_phone="3015550000")
    db.add(p); db.commit(); db.refresh(p)
    ok, reason = sched.can_schedule(db, p)
    assert ok is False and "Mammogram" in reason or "consent" in reason.lower()


def test_gate_passes_when_ready(db):
    p = _ready_patient(db)
    ok, reason = sched.can_schedule(db, p)
    assert ok is True, reason


def test_book_links_visit_and_marks_slot(db):
    p = _ready_patient(db)
    s = _slot(db)
    visit = sched.book_slot(db, slot_id=str(s.id), patient=p, by="patient")
    db.commit(); db.refresh(s)
    assert s.status == "booked" and str(s.pellet_visit_id) == str(visit.id)
    assert visit.location == "white_plains" and visit.scheduled_date == date(2026, 7, 1)


def test_book_rejects_when_no_credit(db):
    p = _ready_patient(db, credits=0)
    s = _slot(db)
    with pytest.raises(sched.NotEligible):
        sched.book_slot(db, slot_id=str(s.id), patient=p, by="patient")


def test_book_rejects_taken_slot(db):
    p = _ready_patient(db)
    s = _slot(db); s.status = "booked"; db.commit()
    with pytest.raises(sched.SlotUnavailable):
        sched.book_slot(db, slot_id=str(s.id), patient=p, by="patient")


def test_open_bookings_count_blocks_second_when_one_credit(db):
    p = _ready_patient(db, credits=1)
    s1 = _slot(db)
    sched.book_slot(db, slot_id=str(s1.id), patient=p, by="patient"); db.commit()
    s2 = PelletSlot(location="white_plains", slot_date=date(2026, 7, 2),
                    start_time=time(9), end_time=time(10), status="open")
    db.add(s2); db.commit()
    # 1 credit, 1 open booking → available (1) NOT > open (1) → blocked
    with pytest.raises(sched.NotEligible):
        sched.book_slot(db, slot_id=str(s2.id), patient=p, by="patient")


def test_cancel_frees_slot(db):
    p = _ready_patient(db)
    s = _slot(db)
    visit = sched.book_slot(db, slot_id=str(s.id), patient=p, by="patient"); db.commit()
    sched.cancel_booking(db, slot_id=str(s.id), by="patient"); db.commit()
    db.refresh(s); db.refresh(visit)
    assert s.status == "open" and s.pellet_visit_id is None
    assert visit.status == "cancelled"


def test_complete_draws_down_credit(db):
    from app.services.pellet import payments as pay
    p = _ready_patient(db, credits=1)
    s = _slot(db)
    visit = sched.book_slot(db, slot_id=str(s.id), patient=p, by="patient"); db.commit()
    assert pay.credit_balance(db, p) == 1            # not drawn at booking
    sched.complete_booking(db, slot_id=str(s.id), by="staff@x"); db.commit()
    assert pay.credit_balance(db, p) == 0            # drawn at completion
    db.refresh(visit)
    assert visit.status == "inserted"
```

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement gate + booking in `scheduling.py`** (append)
```python
from app.models.pellet import PelletVisit
from app.services.pellet import payments as pay


class SlotUnavailable(Exception): pass
class NotEligible(Exception): pass


def _open_bookings(db: Session, patient) -> int:
    """Slots this patient has booked whose visit isn't completed/cancelled."""
    return (db.query(PelletSlot)
              .join(PelletVisit, PelletVisit.id == PelletSlot.pellet_visit_id)
              .filter(PelletSlot.status == "booked",
                      PelletVisit.patient_id == patient.id,
                      PelletVisit.status.in_(("new", "in_progress")))
              .count())


def can_schedule(db: Session, patient) -> tuple[bool, str]:
    """All gates: requirements verified + valid consent + payment standing
    (available_insertions > current open bookings)."""
    from app.routers.patient_pellet import _requirements
    for r in _requirements(db, patient):
        if r["status"] != "done":
            return False, f"{r['label']} not complete"
    if pay.available_insertions(db, patient) <= _open_bookings(db, patient):
        return False, "No insertion credit available — purchase to schedule"
    return True, ""


def book_slot(db: Session, *, slot_id: str, patient, by: str) -> PelletVisit:
    slot = (db.query(PelletSlot).filter(PelletSlot.id == slot_id)
              .with_for_update().first())
    if slot is None or slot.status != "open":
        raise SlotUnavailable("slot is not available")
    ok, reason = can_schedule(db, patient)
    if not ok:
        raise NotEligible(reason)
    visit = PelletVisit(patient_id=patient.id, visit_kind="repeat", status="new",
                        scheduled_date=slot.slot_date, location=slot.location,
                        provider=slot.provider, created_by=by)
    db.add(visit); db.flush()
    slot.status = "booked"
    slot.pellet_visit_id = visit.id
    return visit


def cancel_booking(db: Session, *, slot_id: str, by: str) -> None:
    slot = (db.query(PelletSlot).filter(PelletSlot.id == slot_id)
              .with_for_update().first())
    if slot is None or slot.status != "booked":
        raise SlotUnavailable("no active booking on this slot")
    if slot.pellet_visit_id:
        v = db.query(PelletVisit).filter(PelletVisit.id == slot.pellet_visit_id).first()
        if v:
            v.status = "cancelled"
    slot.status = "open"
    slot.pellet_visit_id = None


def reschedule_booking(db: Session, *, from_slot_id: str, to_slot_id: str,
                       patient, by: str) -> PelletVisit:
    """Free the old slot, book the new one, carrying the same visit."""
    new = (db.query(PelletSlot).filter(PelletSlot.id == to_slot_id)
             .with_for_update().first())
    if new is None or new.status != "open":
        raise SlotUnavailable("target slot is not available")
    old = (db.query(PelletSlot).filter(PelletSlot.id == from_slot_id)
             .with_for_update().first())
    if old is None or old.status != "booked":
        raise SlotUnavailable("no active booking to move")
    visit = db.query(PelletVisit).filter(PelletVisit.id == old.pellet_visit_id).first()
    old.status = "open"; old.pellet_visit_id = None
    new.status = "booked"; new.pellet_visit_id = visit.id if visit else None
    if visit:
        visit.scheduled_date = new.slot_date
        visit.location = new.location
        visit.provider = new.provider
    return visit


def complete_booking(db: Session, *, slot_id: str, by: str) -> PelletVisit:
    """Staff marks the booked insertion done → draw down a credit."""
    slot = (db.query(PelletSlot).filter(PelletSlot.id == slot_id)
              .with_for_update().first())
    if slot is None or slot.status != "booked" or not slot.pellet_visit_id:
        raise SlotUnavailable("no active booking on this slot")
    visit = db.query(PelletVisit).filter(PelletVisit.id == slot.pellet_visit_id).first()
    patient = db.query(__import__("app.models.pellet", fromlist=["PelletPatient"]).PelletPatient).filter_by(id=visit.patient_id).first()
    pay.consume_insertion(db, patient, by=by, reason="pellet insertion completed")
    visit.status = "inserted"
    visit.inserted_at = now_utc_naive()
    visit.inserted_by = by
    return visit
```
(Replace the inline `__import__` with a clean top-of-file `from app.models.pellet import PelletPatient, PelletVisit`.)

- [ ] **Step 4: Run — expect 8 PASS.** Regression ≤ baseline.

- [ ] **Step 5: Commit**
```bash
git add backend/app/services/pellet/scheduling.py backend/tests/test_pellet_booking.py
git commit --no-verify -m "feat(pellet-sched): booking gate + book/reschedule/cancel/complete (T3)"
```

---

## Task 4: Staff availability admin endpoints

**Files:** Modify `backend/app/routers/pellet.py`; Test `backend/tests/test_pellet_availability_admin.py`.

- [ ] **Step 1: Write the failing test**
```python
# backend/tests/test_pellet_availability_admin.py
from datetime import date, time
from app.models.pellet_schedule import PelletAvailabilityTemplate, PelletSlot


def test_create_template_materializes(client, db):
    r = client.post("/api/pellets/availability/templates", json={
        "location": "white_plains", "recurrence_kind": "weekly", "weekday": 2,
        "start_time": "09:00", "end_time": "12:00", "slot_minutes": 60})
    assert r.status_code == 201, r.text
    assert db.query(PelletAvailabilityTemplate).count() == 1
    # auto-materialize created some slots
    assert db.query(PelletSlot).count() > 0


def test_list_and_delete_template(client, db):
    tid = client.post("/api/pellets/availability/templates", json={
        "location": "brandywine", "recurrence_kind": "daily",
        "start_time": "09:00", "end_time": "11:00", "slot_minutes": 60}).json()["id"]
    assert any(t["id"] == tid for t in client.get("/api/pellets/availability/templates").json()["items"])
    assert client.delete(f"/api/pellets/availability/templates/{tid}").status_code == 204


def test_add_adhoc_slot(client, db):
    r = client.post("/api/pellets/availability/slots", json={
        "location": "arlington", "slot_date": "2026-07-04",
        "start_time": "10:00", "end_time": "11:00"})
    assert r.status_code == 201, r.text
    s = db.query(PelletSlot).filter(PelletSlot.is_addon.is_(True)).first()
    assert s and s.location == "arlington"


def test_block_slot(client, db):
    s = PelletSlot(location="white_plains", slot_date=date(2026, 7, 1),
                   start_time=time(9), end_time=time(10), status="open")
    db.add(s); db.commit()
    r = client.post(f"/api/pellets/availability/slots/{s.id}/block")
    assert r.status_code == 200
    db.refresh(s); assert s.status == "blocked"


def test_materialize_endpoint(client, db):
    db.add(PelletAvailabilityTemplate(location="white_plains", recurrence_kind="daily",
                                      start_time=time(9), end_time=time(11),
                                      slot_minutes=60, active=True))
    db.commit()
    r = client.post("/api/pellets/availability/materialize")
    assert r.status_code == 200 and r.json()["created"] > 0
```

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement admin endpoints in `pellet.py`**
Add imports `from app.models.pellet_schedule import PelletAvailabilityTemplate, PelletSlot`, `from app.services.pellet import scheduling as pelletsched`, `from datetime import date as _date, time as _time`. Helper `_parse_time("HH:MM") -> time`. Then:
```python
class AvailTemplateIn(BaseModel):
    location: str
    recurrence_kind: str           # daily|weekly|weekly_nth|monthly_day|specific_dates
    weekday: Optional[int] = None
    nth_in_month: Optional[list] = None
    day_of_month: Optional[int] = None
    specific_dates: Optional[list] = None
    start_time: str
    end_time: str
    slot_minutes: Optional[int] = None
    provider: Optional[str] = None
    effective_from: Optional[str] = None
    effective_through: Optional[str] = None


@router.get("/availability/templates")
def list_avail_templates(db: Session = Depends(get_db),
                         current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.VIEW))):
    rows = db.query(PelletAvailabilityTemplate).order_by(
        PelletAvailabilityTemplate.location).all()
    return {"items": [{
        "id": str(t.id), "location": t.location, "recurrence_kind": t.recurrence_kind,
        "weekday": t.weekday, "nth_in_month": t.nth_in_month, "day_of_month": t.day_of_month,
        "specific_dates": t.specific_dates,
        "start_time": t.start_time.strftime("%H:%M"), "end_time": t.end_time.strftime("%H:%M"),
        "slot_minutes": t.slot_minutes, "provider": t.provider, "active": t.active,
    } for t in rows]}


@router.post("/availability/templates", status_code=201)
def create_avail_template(payload: AvailTemplateIn, db: Session = Depends(get_db),
                          current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.MANAGE))):
    t = PelletAvailabilityTemplate(
        location=payload.location, recurrence_kind=payload.recurrence_kind,
        weekday=payload.weekday, nth_in_month=payload.nth_in_month,
        day_of_month=payload.day_of_month, specific_dates=payload.specific_dates,
        start_time=_parse_time(payload.start_time), end_time=_parse_time(payload.end_time),
        slot_minutes=payload.slot_minutes, provider=payload.provider,
        effective_from=_date.fromisoformat(payload.effective_from) if payload.effective_from else None,
        effective_through=_date.fromisoformat(payload.effective_through) if payload.effective_through else None,
        active=True, created_by=current_user.get("email"))
    db.add(t); db.commit()
    rep = pelletsched.materialize_pellet_slots(db); db.commit()
    return {"id": str(t.id), "materialized": rep}


@router.delete("/availability/templates/{template_id}", status_code=204)
def delete_avail_template(template_id: str, db: Session = Depends(get_db),
                          current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.MANAGE))):
    t = db.query(PelletAvailabilityTemplate).filter_by(id=template_id).first()
    if t is None:
        raise HTTPException(status_code=404, detail="template not found")
    t.active = False     # soft-disable; future materialize won't add its slots
    db.commit()


class AdhocSlotIn(BaseModel):
    location: str
    slot_date: str
    start_time: str
    end_time: str
    provider: Optional[str] = None


@router.post("/availability/slots", status_code=201)
def add_adhoc_slot(payload: AdhocSlotIn, db: Session = Depends(get_db),
                   current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.MANAGE))):
    db.add(PelletSlot(location=payload.location,
                      slot_date=_date.fromisoformat(payload.slot_date),
                      start_time=_parse_time(payload.start_time),
                      end_time=_parse_time(payload.end_time), provider=payload.provider,
                      status="open", is_addon=True, created_by=current_user.get("email")))
    db.commit()
    return {"ok": True}


@router.post("/availability/slots/{slot_id}/block")
def block_slot(slot_id: str, db: Session = Depends(get_db),
               current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.MANAGE))):
    s = db.query(PelletSlot).filter_by(id=slot_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="slot not found")
    if s.status == "booked":
        raise HTTPException(status_code=409, detail="cancel the booking first")
    s.status = "blocked"; db.commit()
    return {"ok": True, "status": s.status}


@router.post("/availability/materialize")
def materialize_endpoint(db: Session = Depends(get_db),
                         current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.MANAGE))):
    rep = pelletsched.materialize_pellet_slots(db); db.commit()
    return rep
```

- [ ] **Step 4: Run — expect 5 PASS.** Regression ≤ baseline. `python -c "import app.main"`.

- [ ] **Step 5: Commit**
```bash
git add backend/app/routers/pellet.py backend/tests/test_pellet_availability_admin.py
git commit --no-verify -m "feat(pellet-sched): staff availability admin endpoints (T4)"
```

---

## Task 5: Patient schedule endpoints + dashboard scheduling block

**Files:** Modify `backend/app/routers/patient_pellet.py`; Test `backend/tests/test_pellet_patient_schedule.py`.

- [ ] **Step 1: Write the failing test**
```python
# backend/tests/test_pellet_patient_schedule.py
from datetime import date, time, timedelta
import pytest
from app.models.pellet import PelletPatient
from app.models.pellet_portal import PelletConsent
from app.models.pellet_payment import PelletInsertionCredit
from app.models.pellet_schedule import PelletSlot
from app.services.pellet import portal_auth
from app.utils.dt import now_utc_naive


@pytest.fixture
def ready(db):
    p = PelletPatient(patient_name="Doe, Jane", chart_number="MRN1",
                      patient_dob=date(1980, 5, 1), patient_phone="3015551234",
                      mammo_verified=True, labs_verified=True)
    db.add(p); db.flush()
    db.add(PelletConsent(pellet_patient_id=p.id, boldsign_envelope_id="e1", status="signed",
                         signed_at=now_utc_naive(), expires_at=now_utc_naive() + timedelta(days=300)))
    db.add(PelletInsertionCredit(pellet_patient_id=p.id, delta=1, source="single"))
    db.add(PelletSlot(location="white_plains", slot_date=date(2099, 7, 1),
                      start_time=time(9), end_time=time(10), status="open"))
    db.commit(); db.refresh(p)
    return p, {"Authorization": f"Bearer {portal_auth.issue_portal_token(p)}"}


def test_locations_and_open_slots(client, db, ready):
    _p, h = ready
    locs = client.get("/api/pellet-portal/schedule/locations", headers=h).json()
    assert "white_plains" in locs["locations"]
    slots = client.get("/api/pellet-portal/schedule/slots?location=white_plains", headers=h).json()
    assert len(slots["items"]) == 1 and slots["items"][0]["location"] == "white_plains"


def test_book_and_my_bookings(client, db, ready):
    p, h = ready
    sid = client.get("/api/pellet-portal/schedule/slots?location=white_plains",
                     headers=h).json()["items"][0]["id"]
    r = client.post(f"/api/pellet-portal/schedule/slots/{sid}/book", headers=h)
    assert r.status_code == 200, r.text
    mine = client.get("/api/pellet-portal/schedule/my", headers=h).json()
    assert len(mine["items"]) == 1


def test_book_blocked_when_gate_fails(client, db):
    p = PelletPatient(patient_name="No, Reqs", chart_number="M9",
                      patient_dob=date(1980, 5, 1), patient_phone="3015559999")
    db.add(p); db.flush()
    s = PelletSlot(location="white_plains", slot_date=date(2099, 7, 2),
                   start_time=time(9), end_time=time(10), status="open")
    db.add(s); db.commit()
    h = {"Authorization": f"Bearer {portal_auth.issue_portal_token(p)}"}
    r = client.post(f"/api/pellet-portal/schedule/slots/{s.id}/book", headers=h)
    assert r.status_code == 409


def test_cancel_booking(client, db, ready):
    p, h = ready
    sid = client.get("/api/pellet-portal/schedule/slots?location=white_plains",
                     headers=h).json()["items"][0]["id"]
    client.post(f"/api/pellet-portal/schedule/slots/{sid}/book", headers=h)
    r = client.post(f"/api/pellet-portal/schedule/slots/{sid}/cancel", headers=h)
    assert r.status_code == 200
    from app.models.pellet_schedule import PelletSlot as PS
    assert db.query(PS).filter(PS.id == sid).first().status == "open"
```

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement patient endpoints in `patient_pellet.py`**
Add imports `from app.models.pellet_schedule import PelletSlot`, `from app.models.pellet import PELLET_LOCATIONS, PelletVisit`, `from app.services.pellet import scheduling as pelletsched`, `from datetime import date as _date`.
```python
@router.get("/schedule/locations")
def schedule_locations(p: PelletPatient = Depends(require_pellet_token)):
    return {"locations": PELLET_LOCATIONS}


@router.get("/schedule/slots")
def open_slots(location: str, p: PelletPatient = Depends(require_pellet_token),
               db: Session = Depends(get_db)):
    today = _date.today()
    rows = (db.query(PelletSlot)
              .filter(PelletSlot.location == location, PelletSlot.status == "open",
                      PelletSlot.slot_date >= today)
              .order_by(PelletSlot.slot_date, PelletSlot.start_time).limit(200).all())
    ok, reason = pelletsched.can_schedule(db, p)
    return {"can_schedule": ok, "reason": reason, "items": [{
        "id": str(s.id), "location": s.location, "provider": s.provider,
        "slot_date": s.slot_date.isoformat(),
        "start_time": s.start_time.strftime("%H:%M"),
        "end_time": s.end_time.strftime("%H:%M"),
    } for s in rows]}


@router.post("/schedule/slots/{slot_id}/book")
def book(slot_id: str, p: PelletPatient = Depends(require_pellet_token),
         db: Session = Depends(get_db)):
    try:
        visit = pelletsched.book_slot(db, slot_id=slot_id, patient=p, by="patient")
    except pelletsched.SlotUnavailable as e:
        raise HTTPException(status_code=409, detail=str(e))
    except pelletsched.NotEligible as e:
        raise HTTPException(status_code=409, detail=str(e))
    db.commit()
    from app.services.pellet.activity import record_pellet_activity
    record_pellet_activity(db, p, "booked",
                           f"Booked {visit.location} on {visit.scheduled_date.strftime('%m/%d/%Y')}")
    db.commit()
    return {"ok": True, "visit_id": str(visit.id),
            "scheduled_date": visit.scheduled_date.isoformat(), "location": visit.location}


@router.post("/schedule/slots/{slot_id}/cancel")
def cancel(slot_id: str, p: PelletPatient = Depends(require_pellet_token),
           db: Session = Depends(get_db)):
    # Only the patient's own booked slot.
    s = db.query(PelletSlot).filter(PelletSlot.id == slot_id).first()
    if s is None or s.status != "booked":
        raise HTTPException(status_code=409, detail="no active booking")
    v = db.query(PelletVisit).filter(PelletVisit.id == s.pellet_visit_id).first()
    if not v or str(v.patient_id) != str(p.id):
        raise HTTPException(status_code=403, detail="not your booking")
    pelletsched.cancel_booking(db, slot_id=slot_id, by="patient"); db.commit()
    return {"ok": True}


@router.get("/schedule/my")
def my_bookings(p: PelletPatient = Depends(require_pellet_token),
                db: Session = Depends(get_db)):
    rows = (db.query(PelletSlot).join(PelletVisit, PelletVisit.id == PelletSlot.pellet_visit_id)
              .filter(PelletVisit.patient_id == p.id, PelletSlot.status == "booked")
              .order_by(PelletSlot.slot_date).all())
    return {"items": [{"slot_id": str(s.id), "location": s.location,
                       "slot_date": s.slot_date.isoformat(),
                       "start_time": s.start_time.strftime("%H:%M")} for s in rows]}
```
Also extend `GET /dashboard`: add `"scheduling": {"can_schedule": ok, "reason": reason, "booked": [...]}` using `can_schedule` + the my-bookings query, so the dashboard "Scheduling" row goes live.

- [ ] **Step 4: Run — expect 4 PASS.** Regression ≤ baseline.

- [ ] **Step 5: Commit**
```bash
git add backend/app/routers/patient_pellet.py backend/tests/test_pellet_patient_schedule.py
git commit --no-verify -m "feat(pellet-sched): patient schedule endpoints + dashboard scheduling (T5)"
```

---

## Task 6: Staff visit-complete endpoint (draw-down) + reschedule

**Files:** Modify `backend/app/routers/pellet.py`; Test `backend/tests/test_pellet_complete.py`.

- [ ] **Step 1: Write the failing test**
```python
# backend/tests/test_pellet_complete.py
from datetime import date, time, timedelta
from app.models.pellet import PelletPatient
from app.models.pellet_portal import PelletConsent
from app.models.pellet_payment import PelletInsertionCredit
from app.models.pellet_schedule import PelletSlot
from app.services.pellet import scheduling as sched, payments as pay
from app.utils.dt import now_utc_naive


def _booked(db):
    p = PelletPatient(patient_name="Doe, Jane", chart_number="MRN1",
                      patient_dob=date(1980, 5, 1), patient_phone="3015551234",
                      mammo_verified=True, labs_verified=True)
    db.add(p); db.flush()
    db.add(PelletConsent(pellet_patient_id=p.id, boldsign_envelope_id="e1", status="signed",
                         signed_at=now_utc_naive(), expires_at=now_utc_naive() + timedelta(days=300)))
    db.add(PelletInsertionCredit(pellet_patient_id=p.id, delta=1, source="single"))
    s = PelletSlot(location="white_plains", slot_date=date(2099, 7, 1),
                   start_time=time(9), end_time=time(10), status="open")
    db.add(s); db.commit(); db.refresh(p); db.refresh(s)
    sched.book_slot(db, slot_id=str(s.id), patient=p, by="patient"); db.commit()
    return p, s


def test_staff_complete_draws_down(client, db):
    p, s = _booked(db)
    r = client.post(f"/api/pellets/slots/{s.id}/complete")
    assert r.status_code == 200, r.text
    assert pay.credit_balance(db, p) == 0


def test_complete_409_when_no_booking(client, db):
    s = PelletSlot(location="white_plains", slot_date=date(2099, 7, 2),
                   start_time=time(9), end_time=time(10), status="open")
    db.add(s); db.commit()
    r = client.post(f"/api/pellets/slots/{s.id}/complete")
    assert r.status_code == 409
```

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement in `pellet.py`**
```python
@router.post("/slots/{slot_id}/complete")
def complete_slot(slot_id: str, db: Session = Depends(get_db),
                  current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.WORK))):
    from app.services.pellet import payments as pelletpay
    try:
        visit = pelletsched.complete_booking(db, slot_id=slot_id,
                                             by=(current_user.get("email") or "staff"))
    except pelletsched.SlotUnavailable as e:
        raise HTTPException(status_code=409, detail=str(e))
    except pelletpay.InsufficientCredit:
        raise HTTPException(status_code=409, detail="no insertion credit to draw down")
    db.commit()
    return {"ok": True, "visit_id": str(visit.id), "status": visit.status}
```
(`pelletsched` imported in T4.)

- [ ] **Step 4: Run — expect 2 PASS.** Regression ≤ baseline.

- [ ] **Step 5: Commit**
```bash
git add backend/app/routers/pellet.py backend/tests/test_pellet_complete.py
git commit --no-verify -m "feat(pellet-sched): staff visit-complete draws down credit (T6)"
```

---

## Task 7: Frontend — patient Schedule page + dashboard row + staff Availability editor

**Files:** Create `frontend/src/pages/pellet-portal/PelletSchedule.jsx`, `frontend/src/pages/PelletAvailability.jsx`; Modify `frontend/src/pages/pellet-portal/PelletDashboard.jsx`, `frontend/src/App.jsx`, `frontend/src/components/pellet/PelletNav.jsx`, `frontend/src/routes.jsx`.

- [ ] **Step 1: Patient Schedule page** — `PelletSchedule.jsx`: `GET /schedule/locations` → location buttons; on pick, `GET /schedule/slots?location=` → list open slots (date MM/DD/YYYY + time); if `can_schedule` false show a banner with `reason` + link to Payments/checklist and disable booking; "Book" → `POST /schedule/slots/{id}/book` → success → show in "My Booking" (`GET /schedule/my`) with a Cancel (`POST .../cancel`). Use `pelletPortalApi`; mirror PelletPayments.jsx styling.

- [ ] **Step 2: Dashboard** — in `PelletDashboard.jsx`, replace the locked "Scheduling" row with a live one from `dashboard.scheduling`: if booked show date/location; else if `can_schedule` show a "Schedule" CTA → `/pellet-portal/home/schedule`; else show the gate reason. Keep mammo/labs/consent/payment rows intact.

- [ ] **Step 3: Route** — `App.jsx`: add `<Route path="schedule" element={<PelletSchedule />} />` under `/pellet-portal/home` (+ import).

- [ ] **Step 4: Staff Availability editor** — `PelletAvailability.jsx`: list templates (`GET /pellets/availability/templates`), a create form (location dropdown from the 3 locations, recurrence_kind select with the fields it needs, start/end time, slot_minutes), delete (soft) per row, an "Add one-off slot" form (`POST /availability/slots`), and a "Re-materialize" button (`POST /availability/materialize`). Use staff `api` + `fmt`. Register in `PelletNav.jsx` (`{ to: '/pellets/schedule', label: 'Scheduling', tier: TIER.MANAGE }`) and `routes.jsx` (`{ path: 'schedule', element: <PelletAvailability />, module: M.PELLETS, tier: TIER.MANAGE }` + import).

- [ ] **Step 5: Build** — `cd frontend && npm run build` clean.

- [ ] **Step 6: Commit**
```bash
git add frontend/src/pages/pellet-portal/PelletSchedule.jsx frontend/src/pages/PelletAvailability.jsx frontend/src/pages/pellet-portal/PelletDashboard.jsx frontend/src/App.jsx frontend/src/components/pellet/PelletNav.jsx frontend/src/routes.jsx
git commit --no-verify -m "feat(pellet-sched): patient Schedule page + dashboard row + staff Availability editor (T7)"
```

---

## Task 8: Authenticated walk-through + deploy

**Files:** Create `backend/tests/test_pellet_schedule_walkthrough.py`.

- [ ] **Step 1: Walk-through test** — drive real endpoints: staff create a weekly template (materializes slots) → patient (requirements verified + consent + 1 credit) lists slots for a location → books one → staff marks it complete → credit drawn to 0 and visit status `inserted`. Assert + print a 5-line narrated log (mirror prior phases' walk-throughs; use `client` for staff, a portal token for the patient). Run `-s`; MUST pass. Then full suite ≤ baseline; `npm run build` clean.
- [ ] **Step 2: Commit, then controller deploys**
```bash
git add backend/tests/test_pellet_schedule_walkthrough.py
git commit --no-verify -m "test(pellet-sched): Phase-3 authenticated walk-through (T8)"
```
Then merge to main; build both images `--project=wwc-solutions`; deploy backend+frontend; smoke (`/api/pellet-portal/schedule/locations` 401 noauth; `/pellets/schedule` 200; `/pellet-portal/home/schedule` 200); push.
- [ ] **Step 3: Materialization cadence note** — like surgery, materialization runs on template-create + the on-demand `/availability/materialize` endpoint; there is no cron. FLAG to the user that a periodic re-materialize (to keep the rolling horizon filled) can be added later (surgery has the same limitation).

---

## Self-review notes (confirm during execution)
- Field names/prefixes per the VERIFIED block override snippets. Replace the inline `__import__` in `complete_booking` with a clean `from app.models.pellet import PelletPatient`.
- `book_slot`/`cancel`/`reschedule`/`complete` lock the slot row `with_for_update()` for concurrency (Postgres; SQLite no-ops it in tests — fine).
- Gate rule: `available_insertions > open_bookings` (so 1 credit + 1 open booking blocks a 2nd) — matches the Phase-2 design draw-down-at-completion model.
- Suite kept ≤ baseline (69); each task commits; deploy `--project=wwc-solutions`.
- Out of scope (note to user): recurring blackout subsystem (staff block individual slots instead); automatic horizon re-materialization cron.
