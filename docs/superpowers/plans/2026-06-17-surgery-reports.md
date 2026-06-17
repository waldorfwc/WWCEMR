# Surgery Reports Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Surgery → Reports page: a filter bar (date range + facility + surgeon) over 6 operational/financial tiles, each clickable to a drill-down surgery list with CSV export.

**Architecture:** A pure aggregation service (`app/services/surgery/reports.py`, one function per tile) feeds a dedicated router (`app/routers/surgery_reports.py`, prefix `/surgery/reports`, `Tier.VIEW`) exposing `/summary`, `/{tile}/rows`, and `?format=csv`. A new React page (`SurgeryReports.jsx`) is wired into the surgery section's routes + nav, mirroring `SurgeryPaymentPosting`.

**Tech Stack:** FastAPI, SQLAlchemy, Pydantic, pytest (backend); React + react-query + Vite (frontend).

**Spec:** `docs/superpowers/specs/2026-06-17-surgery-reports-design.md`

**Conventions (every task):** MM/DD/YYYY dates, Title Case tab/headers/buttons, money `$X.XX`; `now_utc_naive()` never `datetime.utcnow()`; run backend pytest with `./venv/bin/python -m pytest ...` from `backend/`; frontend `npm run build` from `frontend/`.

**Grounding facts (verified):**
- `Surgery` columns used: `status`, `procedure_classification`, `selected_facility`, `surgeon_primary`, `scheduled_date` (Date), `created_at` (DateTime), `completed_at` (DateTime, nullable), `reschedule_count` (Int, nullable). `SURGERY_FACILITY_VALUES = ("medstar","crmc","office","wwc_office_white_plains")` in `app/models/surgery.py`.
- Status values: `incomplete,new,in_progress,confirmed,completed,cancelled,hold,unresponsive`.
- Per-step completion: `_state(s, key)` in `app/services/surgery/step_engine.py` → `"done"|"todo"|"in_progress"|"n/a"`.
- Stripe-payment posting: `SurgeryPayment` (`app/models/stripe_payment.py`) with `kind`, `status`, `amount_paid`, `paid_at`, `posted_to_modmed_at`, `stripe_payment_intent_id`, `stripe_checkout_session_id`, `surgery_id`.
- Capacity: `capacity_rules(db)` in `app/services/surgery/block_schedule.py` → per-facility dict; `office` has `slot_times` (list), `medstar`/`crmc` have `options:[{case_kind,max}]`. `BlockDay(facility, block_date, slots[])` + `SurgerySlot` model in `app/models/surgery.py`.
- Frontend status labels: `STATUS_LABEL` exported from `frontend/src/pages/Surgery.jsx`. Surgery routes in `frontend/src/routes.jsx`; section nav in `frontend/src/components/surgery/SurgeryNav.jsx`. The `api` client + `fmt` util are used by `SurgeryPaymentPosting.jsx`.
- Tests: function-scoped `db` fixture (empty DB), super-admin `client` fixture. Creating `Surgery(...)` rows directly via `db` does NOT hit the Postgres surgery-number sequence (that only fires in the `/manual` endpoint), so no `_no_pg_sequence` fixture is needed here.

---

## File Structure
- Create `backend/app/services/surgery/reports.py` — aggregations (one fn per tile) + drill-down row builders + CSV helper.
- Create `backend/app/routers/surgery_reports.py` — endpoints; register in `app/main.py`.
- Create `frontend/src/pages/SurgeryReports.jsx`; add a route in `frontend/src/routes.jsx` + a nav link in `frontend/src/components/surgery/SurgeryNav.jsx`.
- Tests: `backend/tests/test_surgery_reports_service.py`, `test_surgery_reports_router.py`, `test_surgery_reports_walkthrough.py`.

---

### Task 1: Reports service — base query, period helper, status funnel, completed, cycle time

**Files:**
- Create: `backend/app/services/surgery/reports.py`
- Test: `backend/tests/test_surgery_reports_service.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_surgery_reports_service.py
"""Surgery Reports aggregation service."""
from datetime import date, datetime, timedelta

from app.models.surgery import Surgery
from app.services.surgery import reports as rpt
from app.utils.dt import now_utc_naive


def _surg(db, **kw):
    base = dict(chart_number="M1", patient_name="Doe, J", status="new",
                surgeon_primary="Cooke, Aryian, MD", selected_facility="medstar")
    base.update(kw)
    s = Surgery(**base)
    db.add(s); db.commit(); db.refresh(s)
    return s


def test_status_funnel_counts_and_filters(db):
    _surg(db, status="new")
    _surg(db, status="confirmed")
    _surg(db, status="confirmed", surgeon_primary="Other, MD")
    out = rpt.status_funnel(db, facility=None, surgeon=None)
    assert out["by_status"]["confirmed"] == 2
    assert out["by_status"]["new"] == 1
    # surgeon filter narrows
    out2 = rpt.status_funnel(db, facility=None, surgeon="Other, MD")
    assert out2["by_status"]["confirmed"] == 1 and out2["by_status"].get("new", 0) == 0


def test_completed_in_range_with_prior_period(db):
    df, dt = date(2026, 6, 1), date(2026, 6, 30)
    _surg(db, status="completed", procedure_classification="major",
          completed_at=datetime(2026, 6, 10, 9, 0))
    _surg(db, status="completed", procedure_classification="minor",
          completed_at=datetime(2026, 6, 20, 9, 0))
    _surg(db, status="completed", procedure_classification="major",
          completed_at=datetime(2026, 5, 15, 9, 0))   # prior month
    out = rpt.completed(db, date_from=df, date_to=dt, facility=None, surgeon=None)
    assert out["total"] == 2
    assert out["by_classification"] == {"major": 1, "minor": 1}
    assert out["prior_total"] == 1
    assert out["delta"] == 1
    assert out["prior_from"] == date(2026, 5, 2) and out["prior_to"] == date(2026, 5, 31)


def test_cycle_time_lead_and_reschedule(db):
    df, dt = date(2026, 6, 1), date(2026, 6, 30)
    s1 = _surg(db, status="completed", completed_at=datetime(2026, 6, 10),
               scheduled_date=date(2026, 6, 10), reschedule_count=2)
    s1.created_at = datetime(2026, 6, 1); db.commit()       # 9-day lead
    s2 = _surg(db, status="completed", completed_at=datetime(2026, 6, 20),
               scheduled_date=date(2026, 6, 20), reschedule_count=0)
    s2.created_at = datetime(2026, 6, 9); db.commit()        # 11-day lead
    out = rpt.cycle_time(db, date_from=df, date_to=dt, facility=None, surgeon=None)
    assert out["n"] == 2
    assert out["avg_lead_days"] == 10.0
    assert out["reschedule_rate"] == 0.5
    assert out["avg_reschedules"] == 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_surgery_reports_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.surgery.reports'`.

- [ ] **Step 3: Create the service with these functions**

```python
# backend/app/services/surgery/reports.py
"""Surgery Reports aggregations. Each tile is a pure function over the Surgery
data, parameterized by an optional facility + surgeon filter and (for period
tiles) a date range. No persistence; computed on request."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.surgery import Surgery


def _base_query(db: Session, facility: Optional[str], surgeon: Optional[str]):
    q = db.query(Surgery)
    if facility:
        q = q.filter(Surgery.selected_facility == facility)
    if surgeon:
        q = q.filter(Surgery.surgeon_primary == surgeon)
    return q


def _dt_floor(d: date) -> datetime:
    return datetime.combine(d, time.min)


def _completed_in_range_q(db, date_from, date_to, facility, surgeon):
    """Surgeries with completed_at within [date_from, date_to] (inclusive)."""
    return (_base_query(db, facility, surgeon)
            .filter(Surgery.completed_at.isnot(None),
                    Surgery.completed_at >= _dt_floor(date_from),
                    Surgery.completed_at < _dt_floor(date_to + timedelta(days=1))))


def status_funnel(db: Session, *, facility: Optional[str] = None,
                  surgeon: Optional[str] = None) -> dict:
    """Snapshot: count of surgeries by internal status (frontend maps labels)."""
    rows = (_base_query(db, facility, surgeon)
            .with_entities(Surgery.status, func.count(Surgery.id))
            .group_by(Surgery.status).all())
    return {"by_status": {status: int(n) for status, n in rows}}


def completed(db: Session, *, date_from: date, date_to: date,
              facility: Optional[str] = None, surgeon: Optional[str] = None) -> dict:
    """Period: surgeries completed in range, split by classification, vs the
    immediately-preceding equal-length period."""
    rows = (_completed_in_range_q(db, date_from, date_to, facility, surgeon)
            .with_entities(Surgery.procedure_classification, func.count(Surgery.id))
            .group_by(Surgery.procedure_classification).all())
    by_cls = {(cls or "unspecified"): int(n) for cls, n in rows}
    total = sum(by_cls.values())
    length = (date_to - date_from).days + 1
    prior_to = date_from - timedelta(days=1)
    prior_from = prior_to - timedelta(days=length - 1)
    prior_total = _completed_in_range_q(db, prior_from, prior_to, facility, surgeon).count()
    return {"total": total, "by_classification": by_cls,
            "prior_total": prior_total, "prior_from": prior_from,
            "prior_to": prior_to, "delta": total - prior_total}


def cycle_time(db: Session, *, date_from: date, date_to: date,
               facility: Optional[str] = None, surgeon: Optional[str] = None) -> dict:
    """Period: avg lead days (scheduled_date - created_at) and reschedule stats
    over surgeries completed in range."""
    rows = _completed_in_range_q(db, date_from, date_to, facility, surgeon).all()
    n = len(rows)
    leads = [(s.scheduled_date - s.created_at.date()).days
             for s in rows if s.scheduled_date and s.created_at]
    resch = [int(s.reschedule_count or 0) for s in rows]
    avg_lead = round(sum(leads) / len(leads), 1) if leads else None
    rate = round(sum(1 for r in resch if r > 0) / n, 2) if n else 0.0
    avg_resch = round(sum(resch) / n, 2) if n else 0.0
    return {"n": n, "avg_lead_days": avg_lead,
            "reschedule_rate": rate, "avg_reschedules": avg_resch}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_surgery_reports_service.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/services/surgery/reports.py tests/test_surgery_reports_service.py
git commit -m "feat(surgery-reports): service base + status funnel, completed, cycle time"
```

---

### Task 2: Reports service — not-ready (≤14 days) tile

**Files:**
- Modify: `backend/app/services/surgery/reports.py`
- Test: `backend/tests/test_surgery_reports_service.py` (append)

Reuse `_state(s, key)` from `step_engine.py`. A step is a blocker when its state is `"todo"` or `"in_progress"` (`"done"`/`"n/a"` are not). Blocker keys: `benefits`, `consents`, `prior_auth`, `clearance`, `device`, `labs`.

- [ ] **Step 1: Write the failing test (append)**

```python
def test_not_ready_blockers(db):
    today = date(2026, 6, 15)
    # Inside window, benefits not verified -> blocker on "benefits".
    _surg(db, status="confirmed", scheduled_date=date(2026, 6, 20),
          benefits_verified_at=None)
    # Inside window but fully ready -> excluded. benefits verified, consents
    # signed/not_required, no clearance/device required, labs sent, auth n/a.
    _surg(db, status="confirmed", scheduled_date=date(2026, 6, 18),
          benefits_verified_at=datetime(2026, 6, 1), consent_status="not_required",
          auth_status="not_required", clearance_required=False, device_required=False,
          labs_sent_to_hospital=True)
    # Outside window (>14 days) -> excluded.
    _surg(db, status="confirmed", scheduled_date=date(2026, 7, 30))
    # Completed -> excluded.
    _surg(db, status="completed", scheduled_date=date(2026, 6, 19))
    out = rpt.not_ready(db, facility=None, surgeon=None, today=today)
    assert out["total"] == 1
    assert out["by_blocker"]["benefits"] == 1
    assert out["by_blocker"].get("labs", 0) == 1   # first surg also missing labs etc.
```

(Note: the first surgery is missing several gates, so multiple `by_blocker` keys will be ≥1; the test asserts `benefits` and `labs` are counted and that `total` counts the surgery once.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_surgery_reports_service.py::test_not_ready_blockers -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'not_ready'`.

- [ ] **Step 3: Add `not_ready` to `reports.py`**

Add the import near the top (with the other imports):

```python
from app.services.surgery.step_engine import _state
```

Add the function:

```python
_BLOCKER_KEYS = ("benefits", "consents", "prior_auth", "clearance", "device", "labs")


def not_ready(db: Session, *, facility: Optional[str] = None,
              surgeon: Optional[str] = None, today: Optional[date] = None) -> dict:
    """Snapshot: surgeries scheduled in the next 14 days that are not fully
    ready, broken down by blocking step. A step blocks when its step-engine
    state is 'todo' or 'in_progress'."""
    from app.utils.dt import now_utc_naive
    today = today or now_utc_naive().date()
    horizon = today + timedelta(days=14)
    rows = (_base_query(db, facility, surgeon)
            .filter(Surgery.scheduled_date.isnot(None),
                    Surgery.scheduled_date >= today,
                    Surgery.scheduled_date <= horizon,
                    Surgery.status.notin_(("cancelled", "completed")))
            .all())
    by_blocker = {k: 0 for k in _BLOCKER_KEYS}
    total = 0
    for s in rows:
        blocked = [k for k in _BLOCKER_KEYS if _state(s, k) in ("todo", "in_progress")]
        if blocked:
            total += 1
            for k in blocked:
                by_blocker[k] += 1
    return {"total": total, "by_blocker": by_blocker}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_surgery_reports_service.py -v`
Expected: PASS (all service tests).

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/services/surgery/reports.py tests/test_surgery_reports_service.py
git commit -m "feat(surgery-reports): not-ready ≤14-day blocker tile"
```

---

### Task 3: Reports service — payment-posting backlog + utilization tiles

**Files:**
- Modify: `backend/app/services/surgery/reports.py`
- Test: `backend/tests/test_surgery_reports_service.py` (append)

`posting_backlog` mirrors the Stripe-only predicate from the Payment Posting tab (manual offsets excluded). `utilization` uses `BlockDay`/`SurgerySlot` + `capacity_rules`.

- [ ] **Step 1: Write the failing test (append)**

```python
from decimal import Decimal
from app.models.stripe_payment import SurgeryPayment
from app.models.surgery import BlockDay, SurgerySlot


def test_posting_backlog(db):
    s = _surg(db, status="confirmed")
    db.add(SurgeryPayment(surgery_id=s.id, kind="deposit", status="paid",
                          amount_paid=Decimal("400.00"), stripe_payment_intent_id="pi_1",
                          paid_at=datetime(2026, 6, 1), posted_to_modmed_at=None))
    db.add(SurgeryPayment(surgery_id=s.id, kind="manual_offset", status="paid",
                          amount_paid=Decimal("999.00"), paid_at=datetime(2026, 6, 2),
                          posted_to_modmed_at=None))   # excluded (manual offset)
    db.add(SurgeryPayment(surgery_id=s.id, kind="deposit", status="paid",
                          amount_paid=Decimal("100.00"), stripe_payment_intent_id="pi_2",
                          paid_at=datetime(2026, 6, 3), posted_to_modmed_at=datetime(2026, 6, 4)))  # already posted
    db.commit()
    out = rpt.posting_backlog(db, facility=None, surgeon=None)
    assert out["count"] == 1
    assert out["total_amount"] == 400.0


def test_utilization_booked_vs_capacity(db):
    df, dt = date(2026, 6, 1), date(2026, 6, 30)
    s = _surg(db, status="confirmed", selected_facility="office")
    bd = BlockDay(facility="office", block_date=date(2026, 6, 10),
                  block_kind="office", start_time=time(7, 30), end_time=time(16, 0))
    db.add(bd); db.flush()
    db.add(SurgerySlot(block_day_id=bd.id, surgery_id=s.id,
                       start_time=time(7, 30), end_time=time(8, 30)))
    db.commit()
    out = rpt.utilization(db, date_from=df, date_to=dt, facility=None)
    # office capacity = 7 fixed slots; 1 booked.
    assert out["by_facility"]["office"]["capacity"] == 7
    assert out["by_facility"]["office"]["booked"] == 1
    assert out["overall_pct"] == round(1 / 7 * 100, 1)
```

(If `SurgerySlot` requires more non-null columns than `block_day_id/surgery_id/start_time/end_time`, inspect the model in `app/models/surgery.py` and add the minimal required fields to the test row — keep the assertion intent identical.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_surgery_reports_service.py -k "posting_backlog or utilization" -v`
Expected: FAIL — `AttributeError: ... 'posting_backlog'`.

- [ ] **Step 3: Add both functions to `reports.py`**

```python
def _stripe_only_predicates():
    """Restrict to genuine Stripe payments — mirrors surgery.py _stripe_only_filter.
    Manual offsets (kind='manual_offset') originate in ModMed and carry no Stripe
    id, so they're never in the posting backlog."""
    from sqlalchemy import or_
    from app.models.stripe_payment import SurgeryPayment
    return (
        SurgeryPayment.kind != "manual_offset",
        or_(SurgeryPayment.stripe_payment_intent_id.isnot(None),
            SurgeryPayment.stripe_checkout_session_id.isnot(None)),
    )


def posting_backlog(db: Session, *, facility: Optional[str] = None,
                    surgeon: Optional[str] = None) -> dict:
    """Snapshot: paid Stripe payments not yet posted to ModMed."""
    from app.models.stripe_payment import SurgeryPayment
    from app.utils.dt import now_utc_naive
    q = (db.query(SurgeryPayment)
         .outerjoin(Surgery, Surgery.id == SurgeryPayment.surgery_id)
         .filter(SurgeryPayment.status == "paid",
                 SurgeryPayment.posted_to_modmed_at.is_(None),
                 *_stripe_only_predicates()))
    if facility:
        q = q.filter(Surgery.selected_facility == facility)
    if surgeon:
        q = q.filter(Surgery.surgeon_primary == surgeon)
    rows = q.all()
    total = round(sum(float(p.amount_paid or 0) for p in rows), 2)
    paids = [p.paid_at for p in rows if p.paid_at]
    oldest_age = ((now_utc_naive() - min(paids)).days if paids else None)
    return {"count": len(rows), "total_amount": total, "oldest_age_days": oldest_age}


def _block_day_capacity(rule: dict) -> int:
    """Max cases a block day can hold: fixed-slot facilities use the slot count,
    option-based facilities use the largest option max."""
    if rule.get("slot_times"):
        return len(rule["slot_times"])
    opts = rule.get("options") or []
    return max((int(o.get("max", 0)) for o in opts), default=0)


def utilization(db: Session, *, date_from: date, date_to: date,
                facility: Optional[str] = None) -> dict:
    """Period: booked slots vs capacity across block days in range, per facility."""
    from app.models.surgery import BlockDay, SurgerySlot
    from app.services.surgery.block_schedule import capacity_rules
    rules = capacity_rules(db)
    q = db.query(BlockDay).filter(BlockDay.block_date >= date_from,
                                  BlockDay.block_date <= date_to)
    if facility:
        q = q.filter(BlockDay.facility == facility)
    by_fac: dict = {}
    for bd in q.all():
        booked = (db.query(func.count(SurgerySlot.id))
                  .filter(SurgerySlot.block_day_id == bd.id).scalar() or 0)
        cap = _block_day_capacity(rules.get(bd.facility) or {})
        agg = by_fac.setdefault(bd.facility, {"booked": 0, "capacity": 0})
        agg["booked"] += int(booked)
        agg["capacity"] += cap
    for fac, agg in by_fac.items():
        agg["pct"] = (round(agg["booked"] / agg["capacity"] * 100, 1)
                      if agg["capacity"] else 0.0)
    tot_b = sum(a["booked"] for a in by_fac.values())
    tot_c = sum(a["capacity"] for a in by_fac.values())
    return {"booked": tot_b, "capacity": tot_c,
            "overall_pct": round(tot_b / tot_c * 100, 1) if tot_c else 0.0,
            "by_facility": by_fac}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_surgery_reports_service.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/services/surgery/reports.py tests/test_surgery_reports_service.py
git commit -m "feat(surgery-reports): posting backlog + block utilization tiles"
```

---

### Task 4: Reports service — drill-down rows + CSV

**Files:**
- Modify: `backend/app/services/surgery/reports.py`
- Test: `backend/tests/test_surgery_reports_service.py` (append)

`rows_for(db, tile, ...)` returns the underlying rows for a clicked tile (optionally narrowed by `bucket`). `rows_to_csv(rows)` serializes a list of flat dicts to CSV text.

- [ ] **Step 1: Write the failing test (append)**

```python
def test_rows_for_status_funnel_bucket(db):
    _surg(db, status="hold")
    _surg(db, status="hold")
    _surg(db, status="new")
    rows = rpt.rows_for(db, "status_funnel", date_from=date(2026, 6, 1),
                        date_to=date(2026, 6, 30), facility=None, surgeon=None,
                        bucket="hold", today=date(2026, 6, 15))
    assert len(rows) == 2 and all(r["status"] == "hold" for r in rows)
    assert {"surgery_id", "chart_number", "patient_name", "status"} <= set(rows[0])


def test_rows_to_csv_has_header_and_rows():
    csv_text = rpt.rows_to_csv([{"a": 1, "b": "x"}, {"a": 2, "b": "y"}])
    lines = [ln for ln in csv_text.splitlines() if ln.strip()]
    assert lines[0] == "a,b"
    assert len(lines) == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_surgery_reports_service.py -k "rows" -v`
Expected: FAIL — `AttributeError: ... 'rows_for'`.

- [ ] **Step 3: Add row builders + CSV to `reports.py`**

```python
import csv
import io

# Frontend renders labels via STATUS_LABEL; keep a backend copy for CSV/rows.
STATUS_LABEL = {
    "incomplete": "Incomplete", "new": "New", "in_progress": "Benefits Check",
    "confirmed": "Pre-Surgery", "completed": "Post-Surgery", "hold": "Hold",
    "cancelled": "Canceled", "unresponsive": "Unresponsive",
}


def _surg_row(s) -> dict:
    return {
        "surgery_id": str(s.id),
        "surgery_number": s.surgery_number,
        "chart_number": s.chart_number,
        "patient_name": s.patient_name,
        "surgeon_primary": s.surgeon_primary,
        "selected_facility": s.selected_facility,
        "scheduled_date": s.scheduled_date.strftime("%m/%d/%Y") if s.scheduled_date else None,
        "status": s.status,
        "status_label": STATUS_LABEL.get(s.status, s.status),
    }


def rows_for(db: Session, tile: str, *, date_from: date, date_to: date,
             facility: Optional[str] = None, surgeon: Optional[str] = None,
             bucket: Optional[str] = None, today: Optional[date] = None) -> list[dict]:
    """Underlying rows for a clicked tile; `bucket` narrows to a sub-segment."""
    from app.utils.dt import now_utc_naive
    if tile == "status_funnel":
        q = _base_query(db, facility, surgeon)
        if bucket:
            q = q.filter(Surgery.status == bucket)
        return [_surg_row(s) for s in q.order_by(Surgery.scheduled_date).all()]

    if tile == "completed":
        rows = _completed_in_range_q(db, date_from, date_to, facility, surgeon).all()
        if bucket:  # bucket = a classification value
            rows = [s for s in rows if (s.procedure_classification or "unspecified") == bucket]
        out = []
        for s in rows:
            r = _surg_row(s)
            r["classification"] = s.procedure_classification
            r["completed_at"] = s.completed_at.strftime("%m/%d/%Y") if s.completed_at else None
            out.append(r)
        return out

    if tile == "cycle_time":
        out = []
        for s in _completed_in_range_q(db, date_from, date_to, facility, surgeon).all():
            r = _surg_row(s)
            r["lead_days"] = ((s.scheduled_date - s.created_at.date()).days
                              if s.scheduled_date and s.created_at else None)
            r["reschedule_count"] = int(s.reschedule_count or 0)
            out.append(r)
        return out

    if tile == "not_ready":
        today = today or now_utc_naive().date()
        horizon = today + timedelta(days=14)
        q = (_base_query(db, facility, surgeon)
             .filter(Surgery.scheduled_date.isnot(None),
                     Surgery.scheduled_date >= today,
                     Surgery.scheduled_date <= horizon,
                     Surgery.status.notin_(("cancelled", "completed"))))
        out = []
        for s in q.order_by(Surgery.scheduled_date).all():
            blockers = [k for k in _BLOCKER_KEYS if _state(s, k) in ("todo", "in_progress")]
            if not blockers:
                continue
            if bucket and bucket not in blockers:
                continue
            r = _surg_row(s)
            r["blockers"] = blockers
            out.append(r)
        return out

    if tile == "posting_backlog":
        from app.models.stripe_payment import SurgeryPayment
        q = (db.query(SurgeryPayment, Surgery)
             .outerjoin(Surgery, Surgery.id == SurgeryPayment.surgery_id)
             .filter(SurgeryPayment.status == "paid",
                     SurgeryPayment.posted_to_modmed_at.is_(None),
                     *_stripe_only_predicates()))
        if facility:
            q = q.filter(Surgery.selected_facility == facility)
        if surgeon:
            q = q.filter(Surgery.surgeon_primary == surgeon)
        out = []
        for p, s in q.all():
            out.append({
                "payment_id": str(p.id),
                "chart_number": getattr(s, "chart_number", None),
                "patient_name": getattr(s, "patient_name", None),
                "amount_paid": float(p.amount_paid or 0),
                "paid_at": p.paid_at.strftime("%m/%d/%Y") if p.paid_at else None,
                "confirmation": p.stripe_payment_intent_id or p.stripe_checkout_session_id,
            })
        return out

    if tile == "utilization":
        from app.models.surgery import BlockDay, SurgerySlot
        from app.services.surgery.block_schedule import capacity_rules
        rules = capacity_rules(db)
        q = db.query(BlockDay).filter(BlockDay.block_date >= date_from,
                                      BlockDay.block_date <= date_to)
        if facility:
            q = q.filter(BlockDay.facility == facility)
        out = []
        for bd in q.order_by(BlockDay.block_date).all():
            booked = (db.query(func.count(SurgerySlot.id))
                      .filter(SurgerySlot.block_day_id == bd.id).scalar() or 0)
            out.append({
                "facility": bd.facility,
                "block_date": bd.block_date.strftime("%m/%d/%Y"),
                "block_kind": bd.block_kind,
                "booked": int(booked),
                "capacity": _block_day_capacity(rules.get(bd.facility) or {}),
            })
        return out

    raise ValueError(f"unknown tile: {tile}")


VALID_TILES = ("status_funnel", "not_ready", "completed", "cycle_time",
               "posting_backlog", "utilization")


def rows_to_csv(rows: list[dict]) -> str:
    """Serialize flat dict rows to CSV text. List-valued cells are joined with
    '; '. Empty input yields an empty string."""
    if not rows:
        return ""
    fields = list(rows[0].keys())
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({k: ("; ".join(map(str, v)) if isinstance(v, list) else v)
                    for k, v in r.items()})
    return buf.getvalue()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_surgery_reports_service.py -v`
Expected: PASS (all service tests).

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/services/surgery/reports.py tests/test_surgery_reports_service.py
git commit -m "feat(surgery-reports): drill-down rows + CSV serialization"
```

---

### Task 5: Reports router + registration

**Files:**
- Create: `backend/app/routers/surgery_reports.py`
- Modify: `backend/app/main.py` (import + include_router)
- Test: `backend/tests/test_surgery_reports_router.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_surgery_reports_router.py
"""Surgery Reports endpoints. `client` is the super-admin fixture."""
from datetime import date, datetime

from app.models.surgery import Surgery


def _surg(db, **kw):
    base = dict(chart_number="M1", patient_name="Doe, J", status="hold",
                surgeon_primary="Cooke, Aryian, MD", selected_facility="medstar")
    base.update(kw)
    s = Surgery(**base); db.add(s); db.commit(); db.refresh(s)
    return s


def test_summary_returns_all_tiles(client, db):
    _surg(db, status="hold")
    _surg(db, status="completed", procedure_classification="major",
          completed_at=datetime(2026, 6, 10))
    r = client.get("/api/surgery/reports/summary?from=2026-06-01&to=2026-06-30")
    assert r.status_code == 200, r.text
    body = r.json()
    for key in ("status_funnel", "not_ready", "completed", "cycle_time",
                "posting_backlog", "utilization", "period"):
        assert key in body
    assert body["status_funnel"]["by_status"]["hold"] == 1


def test_rows_json_and_csv(client, db):
    _surg(db, status="hold")
    _surg(db, status="hold")
    j = client.get("/api/surgery/reports/status_funnel/rows?bucket=hold")
    assert j.status_code == 200 and len(j.json()["items"]) == 2
    c = client.get("/api/surgery/reports/status_funnel/rows?bucket=hold&format=csv")
    assert c.status_code == 200
    assert c.headers["content-type"].startswith("text/csv")
    assert c.text.splitlines()[0].startswith("surgery_id")


def test_rows_unknown_tile_404(client, db):
    assert client.get("/api/surgery/reports/bogus/rows").status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_surgery_reports_router.py -v`
Expected: FAIL — 404 on `/api/surgery/reports/summary` (router not mounted).

- [ ] **Step 3: Create the router**

```python
# backend/app/routers/surgery_reports.py
"""Surgery Reports endpoints: a one-shot summary of all tiles, plus per-tile
drill-down rows (JSON or CSV). Read-only (Tier.VIEW)."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.permissions.catalog import Module, Tier
from app.permissions.dependencies import requires_tier
from app.services.surgery import reports as rpt
from app.utils.dt import now_utc_naive

router = APIRouter(prefix="/surgery/reports", tags=["surgery-reports"])


def _parse_range(from_: Optional[str], to_: Optional[str]) -> tuple[date, date]:
    """Default to the current month (1st → today) when omitted."""
    today = now_utc_naive().date()
    df = date.fromisoformat(from_) if from_ else today.replace(day=1)
    dt = date.fromisoformat(to_) if to_ else today
    return df, dt


@router.get("/summary")
def reports_summary(
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = None,
    facility: Optional[str] = None,
    surgeon: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.VIEW)),
):
    df, dt = _parse_range(from_, to)
    return {
        "period": {"from": df.isoformat(), "to": dt.isoformat()},
        "status_funnel": rpt.status_funnel(db, facility=facility, surgeon=surgeon),
        "not_ready": rpt.not_ready(db, facility=facility, surgeon=surgeon),
        "completed": _isoize(rpt.completed(db, date_from=df, date_to=dt,
                                           facility=facility, surgeon=surgeon)),
        "cycle_time": rpt.cycle_time(db, date_from=df, date_to=dt,
                                     facility=facility, surgeon=surgeon),
        "posting_backlog": rpt.posting_backlog(db, facility=facility, surgeon=surgeon),
        "utilization": rpt.utilization(db, date_from=df, date_to=dt, facility=facility),
    }


def _isoize(completed: dict) -> dict:
    """JSON-safe the two date fields in the completed tile."""
    out = dict(completed)
    out["prior_from"] = completed["prior_from"].isoformat()
    out["prior_to"] = completed["prior_to"].isoformat()
    return out


@router.get("/{tile}/rows")
def reports_rows(
    tile: str,
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = None,
    facility: Optional[str] = None,
    surgeon: Optional[str] = None,
    bucket: Optional[str] = None,
    format: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.VIEW)),
):
    if tile not in rpt.VALID_TILES:
        raise HTTPException(status_code=404, detail="unknown report tile")
    df, dt = _parse_range(from_, to)
    rows = rpt.rows_for(db, tile, date_from=df, date_to=dt, facility=facility,
                        surgeon=surgeon, bucket=bucket)
    if (format or "").lower() == "csv":
        csv_text = rpt.rows_to_csv(rows)
        filename = f"surgery-{tile}-{df.isoformat()}_{dt.isoformat()}.csv"
        return StreamingResponse(
            iter([csv_text]), media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'})
    return {"items": rows}
```

- [ ] **Step 4: Register the router in `main.py`**

In `backend/app/main.py`, add `surgery_reports` to the surgery router import (line ~20, the long `from app.routers import ...` line — append `, surgery_reports`). Then add an `include_router` next to where `surgery.router` is included (search for `include_router(surgery.router`). Mirror its form; the endpoints self-gate with `requires_tier`, so include with just the prefix:

```python
app.include_router(surgery_reports.router, prefix="/api")
```

(If `surgery.router` is included with explicit `dependencies=[...]`, match that exact form instead — read the surrounding lines and follow the established pattern.)

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_surgery_reports_router.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd backend && git add app/routers/surgery_reports.py app/main.py tests/test_surgery_reports_router.py
git commit -m "feat(surgery-reports): summary + drill-down rows/CSV endpoints"
```

---

### Task 6: Frontend — Reports page + route + nav

**Files:**
- Create: `frontend/src/pages/SurgeryReports.jsx`
- Modify: `frontend/src/routes.jsx`, `frontend/src/components/surgery/SurgeryNav.jsx`

- [ ] **Step 1: Inspect the patterns to mirror**

Run from `frontend/`:
`grep -n "SurgeryPaymentPosting\|payment-posting" src/routes.jsx src/components/surgery/SurgeryNav.jsx`
`grep -n "import\|api\.\|fmt\|useQuery\|export default\|STATUS_LABEL" src/pages/SurgeryPaymentPosting.jsx | head -30`
Read `SurgeryPaymentPosting.jsx` for the page shell, the `api` import path, the `fmt` util, and react-query usage. Read the top of `src/pages/Surgery.jsx` to import `STATUS_LABEL`.

- [ ] **Step 2: Add the route**

In `frontend/src/routes.jsx`: add the import alongside the other surgery page imports:

```javascript
import SurgeryReports from './pages/SurgeryReports'
```

and add a route entry next to the `payment-posting` entry:

```javascript
    { path: 'reports', element: <SurgeryReports />, module: M.SURGERY, tier: TIER.VIEW },
```

- [ ] **Step 3: Add the nav link**

In `frontend/src/components/surgery/SurgeryNav.jsx`, add next to the Payment Posting link:

```javascript
    { to: '/surgery/reports', label: 'Reports', tier: TIER.VIEW },
```

- [ ] **Step 4: Create the page**

Create `frontend/src/pages/SurgeryReports.jsx`. Match `SurgeryPaymentPosting.jsx` for the `api` import path, the `fmt` util, and styling classes. Requirements:
- A filter bar: date-range preset `<select>` (This Month / Last Month / Last 30 Days / Last 90 Days / Custom) that resolves to `from`/`to` ISO strings (compute client-side; "Custom" reveals two `<input type="date">`), a facility `<select>` (`''`=All, then `medstar`/`crmc`/`office`/`wwc_office_white_plains` with labels MedStar/CRMC/Office/WWC Office White Plains), and a surgeon `<select>` (`''`=All + options from `GET /surgery/picklists` `surgeons`).
- `useQuery(['surgery-report-summary', filters], () => api.get('/surgery/reports/summary', {params}).then(r=>r.data))`, refetching on filter change.
- A responsive grid of 6 tiles, each rendering the headline figure + breakdown:
  1. **Status funnel** — rows of `STATUS_LABEL[status]: count` (imported from `Surgery.jsx`), in label order, for the statuses present.
  2. **Not ready ≤14 days** — `not_ready.total` headline + per-blocker chips (benefits/consents/prior_auth/clearance/device/labs).
  3. **Completed** — `completed.total` with classification split + delta vs prior (`completed.delta`).
  4. **Cycle time** — `cycle_time.avg_lead_days` days + `reschedule_rate` (as %).
  5. **Posting backlog** — `posting_backlog.count` + `$total_amount` + `oldest_age_days`d.
  6. **Utilization** — `overall_pct`% + per-facility `pct` rows.
- Each tile (and, where natural, a segment such as a status row, a blocker chip, or a classification) is clickable → opens a drill-down panel/modal that calls `GET /surgery/reports/{tile}/rows` with the same filter params + `bucket` (the clicked segment), renders the returned `items` as a table, and shows a **Download CSV** button. The CSV button navigates/fetches the same endpoint with `format=csv` and triggers a download (e.g. build the URL with params + `format=csv` and `window.open`, or fetch as blob and save).
- Dates render MM/DD/YYYY via `fmt.date()`; money as `$X.XX`. Title Case headings/buttons.

Keep the page focused; if it grows large, factor the tile + drill-down into small local components within the file.

- [ ] **Step 5: Build**

Run: `cd frontend && npm run build`
Expected: build succeeds, no errors referencing the new files.

- [ ] **Step 6: Commit**

```bash
cd frontend && git add src/pages/SurgeryReports.jsx src/routes.jsx src/components/surgery/SurgeryNav.jsx
git commit -m "feat(surgery-reports): Reports page with filters, tiles, drill-down + CSV"
```

---

### Task 7: Authenticated walk-through

**Files:**
- Create: `backend/tests/test_surgery_reports_walkthrough.py`

- [ ] **Step 1: Write the walk-through test**

```python
# backend/tests/test_surgery_reports_walkthrough.py
"""Authenticated walk-through of Surgery Reports: seed a small set, read the
summary, drill into a tile, and export CSV. `client` is the super-admin fixture."""
from datetime import date, datetime

from app.models.surgery import Surgery


def _surg(db, **kw):
    base = dict(chart_number="M1", patient_name="Doe, J", status="confirmed",
                surgeon_primary="Cooke, Aryian, MD", selected_facility="medstar")
    base.update(kw); s = Surgery(**base); db.add(s); db.commit(); db.refresh(s)
    return s


def test_reports_walkthrough(client, db, capsys):
    log = []
    _surg(db, status="hold")
    _surg(db, status="confirmed")
    _surg(db, status="completed", procedure_classification="major",
          completed_at=datetime(2026, 6, 10))

    # 1. Summary returns every tile.
    body = client.get("/api/surgery/reports/summary?from=2026-06-01&to=2026-06-30").json()
    assert set(body) >= {"status_funnel", "not_ready", "completed", "cycle_time",
                         "posting_backlog", "utilization", "period"}
    log.append(f"1. /summary → funnel {body['status_funnel']['by_status']}, "
               f"completed {body['completed']['total']} (Δ {body['completed']['delta']})")

    # 2. Drill into the 'hold' bucket of the status funnel.
    items = client.get("/api/surgery/reports/status_funnel/rows?bucket=hold").json()["items"]
    assert len(items) == 1 and items[0]["status"] == "hold"
    log.append(f"2. drill status_funnel?bucket=hold → {len(items)} surgery")

    # 3. CSV export of the same bucket.
    csv_resp = client.get("/api/surgery/reports/status_funnel/rows?bucket=hold&format=csv")
    assert csv_resp.status_code == 200
    assert csv_resp.headers["content-type"].startswith("text/csv")
    assert csv_resp.text.splitlines()[0].startswith("surgery_id")
    log.append("3. CSV export → text/csv with header + 1 row")

    with capsys.disabled():
        print("\n  -- Surgery Reports walk-through (authenticated) --")
        for line in log:
            print("   " + line)
```

- [ ] **Step 2: Run it**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_surgery_reports_walkthrough.py -v -s`
Expected: PASS, log prints.

- [ ] **Step 3: Commit**

```bash
cd backend && git add tests/test_surgery_reports_walkthrough.py
git commit -m "test(surgery-reports): authenticated reports walk-through"
```

---

## Final Verification (after all tasks)
- [ ] Backend: `cd backend && ./venv/bin/python -m pytest tests/ -k "surgery_reports" -v` → all PASS.
- [ ] Frontend: `cd frontend && npm run build` → clean.
- [ ] No new failures vs the documented baseline (the pre-existing stale-import `ModuleNotFoundError` collection errors are unrelated). Run `cd backend && ./venv/bin/python -m pytest tests/ -k "surgery" -q` and confirm no NEW failures.

## Notes for the implementer
- **No new tables.** Reports compute on request; the data volume is small.
- **`completed_at` is the completion anchor**; `scheduled_date − created_at` is lead time. Surgeries missing either are skipped from the lead-time average (not counted as 0).
- **Snapshot vs period:** `status_funnel`, `not_ready`, `posting_backlog` ignore the date range (always "now"/next-14-days); `completed`, `cycle_time`, `utilization` use it. Facility + surgeon filters apply to all.
- **Reuse, don't re-derive:** `not_ready` blockers come from `step_engine._state`; the Stripe-only predicate mirrors `surgery.py`'s `_stripe_only_filter` (manual offsets excluded).
- **Money:** all dollar values here are deposits/payments already validated at write time; no clamping needed in read-only aggregation.
