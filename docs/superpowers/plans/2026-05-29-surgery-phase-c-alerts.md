# Surgery Phase C — Alerts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect surgeries that fall on a newly-blocked day (PTO / closure) and surface them as a To-do row on the Surgery dashboard with a "Mark hospital notified" resolve action. (The configurable office release alert is already wired up in Phase B.)

**Architecture:** A small detection service + a new dashboard payload key + a new ToDoPanel section + a resolve endpoint + a `Surgery.blocked_conflict_notified_at` column for idempotency.

**Tech Stack:** FastAPI + SQLAlchemy, pytest with sqlite-in-memory, React + TanStack Query.

**Depends on:** None hard, but Phase B is recommended first (the config tab UI gives the user a place to manage things if needed).

---

## File Structure

- **Create:** `backend/app/services/surgery_blackout_conflict.py` — detection logic.
- **Create:** `backend/tests/test_surgery_blackout_conflict.py` — coverage for all three scopes + resolve.
- **Modify:** `backend/app/models/surgery.py` — add `blocked_conflict_notified_at` to `Surgery`.
- **Modify:** `backend/app/routers/surgery.py` — extend the dashboard payload; new resolve endpoint.
- **Modify:** `frontend/src/pages/Surgery.jsx` — add the new ToDoPanel section.

---

## Section 1 — Backend: detection service

### Task C1: Add `blocked_conflict_notified_at` column

**Files:**
- Modify: `backend/app/models/surgery.py`

- [ ] **Step 1: Add the column**

In the `Surgery` model (near other timestamp columns like `last_rescheduled_at`), add:

```python
    # Set when the user clicks "Mark hospital notified" on the blocked-day
    # conflict To-do (Phase C). When set, the conflict drops off the list.
    blocked_conflict_notified_at = Column(DateTime, nullable=True)
    blocked_conflict_notified_by = Column(String(120), nullable=True)
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/models/surgery.py
git commit -m "feat(surgery): add blocked_conflict_notified_at + notified_by"
```

---

### Task C2: Detection service with tests

**Files:**
- Create: `backend/app/services/surgery_blackout_conflict.py`
- Create: `backend/tests/test_surgery_blackout_conflict.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Phase C — blackout/surgery conflict detection."""
from datetime import date, timedelta

from app.models.surgery import Surgery, SurgeryBlackoutDay
from app.services.surgery_blackout_conflict import find_blocked_conflicts


def _surgery(db, days_out: int, facility="medstar", status="confirmed"):
    s = Surgery(
        chart_number="1",
        patient_name="Pat",
        scheduled_date=date.today() + timedelta(days=days_out),
        selected_facility=facility,
        status=status,
        eligible_facilities=[facility],
    )
    db.add(s); db.flush()
    return s


def _blackout(db, days_out: int, scope: str, facility=None, reason="pto",
              label="Dr. Cooke PTO", owner_email=None):
    b = SurgeryBlackoutDay(
        blackout_date=date.today() + timedelta(days=days_out),
        scope=scope, reason=reason, label=label,
        facility=facility, owner_email=owner_email,
    )
    db.add(b); db.flush()
    return b


def test_office_scope_flags_all_surgeries_on_date(db):
    s1 = _surgery(db, 3, facility="office")
    s2 = _surgery(db, 3, facility="medstar")
    _blackout(db, 3, scope="office", reason="holiday", label="Memorial Day")
    db.commit()

    out = find_blocked_conflicts(db)
    ids = {c["surgery_id"] for c in out}
    assert str(s1.id) in ids
    assert str(s2.id) in ids


def test_facility_scope_flags_matching_facility_only(db):
    s_medstar = _surgery(db, 3, facility="medstar")
    s_crmc    = _surgery(db, 3, facility="crmc")
    _blackout(db, 3, scope="facility", facility="medstar", reason="facility_closed",
               label="MedStar closed for maintenance")
    db.commit()

    out = find_blocked_conflicts(db)
    ids = {c["surgery_id"] for c in out}
    assert str(s_medstar.id) in ids
    assert str(s_crmc.id) not in ids


def test_provider_scope_flags_all_surgeries_on_date(db):
    # Single-surgeon practice; provider PTO grounds the whole day.
    s = _surgery(db, 3)
    _blackout(db, 3, scope="provider", reason="pto",
               label="Aryian Cooke PTO",
               owner_email="acooke@waldorfwomenscare.com")
    db.commit()

    out = find_blocked_conflicts(db)
    assert any(c["surgery_id"] == str(s.id) for c in out)


def test_resolved_conflicts_are_excluded(db):
    from datetime import datetime
    s = _surgery(db, 3, facility="office")
    _blackout(db, 3, scope="office", reason="holiday", label="Holiday")
    s.blocked_conflict_notified_at = datetime.utcnow()
    db.commit()

    assert find_blocked_conflicts(db) == []


def test_cancelled_surgeries_excluded(db):
    _surgery(db, 3, facility="office", status="cancelled")
    _blackout(db, 3, scope="office", reason="holiday", label="Holiday")
    db.commit()

    assert find_blocked_conflicts(db) == []
```

Run:

```bash
cd backend && ./venv/bin/pytest tests/test_surgery_blackout_conflict.py -v
```

Expected: 5 ImportErrors (service doesn't exist).

- [ ] **Step 2: Implement the service**

`backend/app/services/surgery_blackout_conflict.py`:

```python
"""Detect surgeries booked on dates that are now blacked-out.

A conflict is one Surgery whose scheduled_date matches one
SurgeryBlackoutDay row with an applicable scope. Resolved conflicts
(blocked_conflict_notified_at IS NOT NULL) are excluded, as are
cancelled / completed surgeries.

Scope rules:
  office    — applies to any surgery on that date
  facility  — applies to surgeries whose selected_facility == blackout.facility
  provider  — applies to any surgery on that date (single-surgeon practice;
              when we add a second surgeon, swap to email-match)
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.surgery import Surgery, SurgeryBlackoutDay


ACTIVE_STATUSES = ("new", "in_progress", "confirmed", "hold")


def find_blocked_conflicts(db: Session) -> list[dict]:
    """Return one dict per (surgery, blackout) pair."""
    blackouts = db.query(SurgeryBlackoutDay).all()
    if not blackouts:
        return []

    by_date: dict[str, list[SurgeryBlackoutDay]] = {}
    for b in blackouts:
        by_date.setdefault(b.blackout_date, []).append(b)

    surgeries = (db.query(Surgery)
                   .filter(Surgery.scheduled_date.in_(list(by_date.keys())))
                   .filter(Surgery.status.in_(ACTIVE_STATUSES))
                   .filter(Surgery.blocked_conflict_notified_at.is_(None))
                   .all())

    out = []
    for s in surgeries:
        for b in by_date[s.scheduled_date]:
            if not _scope_matches(s, b):
                continue
            out.append({
                "surgery_id":       str(s.id),
                "patient_name":     s.patient_name,
                "scheduled_date":   s.scheduled_date.isoformat(),
                "facility":         s.selected_facility,
                "blackout_scope":   b.scope,
                "blackout_reason":  b.reason,
                "blackout_label":   b.label,
            })
            break  # one conflict per surgery is enough
    return out


def _scope_matches(s: Surgery, b: SurgeryBlackoutDay) -> bool:
    if b.scope == "office":
        return True
    if b.scope == "facility":
        return s.selected_facility == b.facility
    if b.scope == "provider":
        # Single-surgeon practice: provider PTO grounds the day for all
        # surgeries. If/when there's >1 operating surgeon, refine this.
        return True
    return False
```

- [ ] **Step 3: Re-run tests**

```bash
cd backend && ./venv/bin/pytest tests/test_surgery_blackout_conflict.py -v
```

Expected: 5 passes.

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/surgery_blackout_conflict.py backend/tests/test_surgery_blackout_conflict.py
git commit -m "feat(surgery): blackout-conflict detection service"
```

---

## Section 2 — Backend: dashboard + resolve endpoint

### Task C3: Extend `/surgery/dashboard` to include `blocked_conflicts`

**Files:**
- Modify: `backend/app/routers/surgery.py`
- Modify: `backend/tests/test_surgery_blackout_conflict.py`

- [ ] **Step 1: Add a test for the dashboard integration**

Append to `test_surgery_blackout_conflict.py`:

```python
def test_dashboard_includes_blocked_conflicts(client, db):
    from datetime import date as _d, timedelta
    s = Surgery(
        chart_number="1", patient_name="Pat",
        scheduled_date=_d.today() + timedelta(days=2),
        selected_facility="office",
        status="confirmed",
        eligible_facilities=["office"],
    )
    db.add(s)
    db.add(SurgeryBlackoutDay(
        blackout_date=_d.today() + timedelta(days=2),
        scope="office", reason="holiday", label="Memorial Day",
    ))
    db.commit()

    resp = client.get("/api/surgery/dashboard")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "blocked_conflicts" in body
    assert len(body["blocked_conflicts"]) == 1
    assert body["blocked_conflicts"][0]["patient_name"] == "Pat"
    assert body["blocked_conflicts"][0]["blackout_reason"] == "holiday"
```

- [ ] **Step 2: Wire the detection into the dashboard**

In `backend/app/routers/surgery.py`, find the `/dashboard` endpoint handler. Near the end (before the final return), call the new service and append its output to the response dict:

```python
    from app.services.surgery_blackout_conflict import find_blocked_conflicts
    response["blocked_conflicts"] = find_blocked_conflicts(db)
    return response
```

- [ ] **Step 3: Re-run the test**

```bash
cd backend && ./venv/bin/pytest tests/test_surgery_blackout_conflict.py::test_dashboard_includes_blocked_conflicts -v
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/app/routers/surgery.py backend/tests/test_surgery_blackout_conflict.py
git commit -m "feat(surgery): dashboard payload includes blocked_conflicts"
```

---

### Task C4: Resolve endpoint

**Files:**
- Modify: `backend/app/routers/surgery.py`
- Modify: `backend/tests/test_surgery_blackout_conflict.py`

- [ ] **Step 1: Append failing test**

```python
def test_resolve_endpoint_marks_notified(client, db):
    from datetime import date as _d, timedelta
    s = Surgery(
        chart_number="1", patient_name="Pat",
        scheduled_date=_d.today() + timedelta(days=2),
        selected_facility="office",
        status="confirmed",
        eligible_facilities=["office"],
    )
    db.add(s)
    db.add(SurgeryBlackoutDay(
        blackout_date=_d.today() + timedelta(days=2),
        scope="office", reason="holiday", label="Holiday",
    ))
    db.commit()

    resp = client.post(f"/api/surgery/{s.id}/blocked-conflict/resolve")
    assert resp.status_code == 200, resp.text
    db.refresh(s)
    assert s.blocked_conflict_notified_at is not None
    assert s.blocked_conflict_notified_by  # filled with TEST_USER email

    # Subsequent dashboard call no longer includes it.
    body = client.get("/api/surgery/dashboard").json()
    assert body["blocked_conflicts"] == []
```

- [ ] **Step 2: Implement the endpoint**

Add to `backend/app/routers/surgery.py`:

```python
@router.post("/{surgery_id}/blocked-conflict/resolve")
def resolve_blocked_conflict(
    surgery_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_permission("claim:edit")),
):
    from datetime import datetime
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="surgery not found")
    actor = current_user.get("email") or "system"
    s.blocked_conflict_notified_at = datetime.utcnow()
    s.blocked_conflict_notified_by = actor

    # Audit trail
    from app.models.surgery import SurgeryNote
    db.add(SurgeryNote(
        surgery_id=s.id,
        created_by=actor,
        kind="blocked_conflict_resolved",
        body=f"Marked hospital notified of conflict on {s.scheduled_date}.",
    ))
    db.commit()
    return {"ok": True, "notified_at": s.blocked_conflict_notified_at.isoformat()}
```

- [ ] **Step 3: Run tests**

```bash
cd backend && ./venv/bin/pytest tests/test_surgery_blackout_conflict.py -v
```

Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add backend/app/routers/surgery.py backend/tests/test_surgery_blackout_conflict.py
git commit -m "feat(surgery): resolve endpoint for blocked-day conflict"
```

---

## Section 3 — Frontend: ToDoPanel section

### Task C5: Add "Blocked-day conflicts" section to `ToDoPanel`

**Files:**
- Modify: `frontend/src/pages/Surgery.jsx`

- [ ] **Step 1: Locate the `ToDoPanel` function**

In `frontend/src/pages/Surgery.jsx`, find `function ToDoPanel(...)` (around line 701 per earlier survey).

- [ ] **Step 2: Add a new section above the existing release-alert sections**

Modify the `ToDoPanel` signature to accept `blockedConflicts` and `onResolveConflict`:

```jsx
function ToDoPanel({ todos, hospitalUnbooked = [], officeUnderbooked = [],
                     blockedConflicts = [], onResolveConflict, onOpen }) {
  const totalItems = todos.length + hospitalUnbooked.length
                   + officeUnderbooked.length + blockedConflicts.length
```

Add this rendering block at the top of the panel's body (before existing sections), so the most urgent items surface first:

```jsx
  {blockedConflicts.length > 0 && (
    <section className="mb-3">
      <h3 className="text-[11px] uppercase text-red-700 font-semibold mb-1.5 flex items-center gap-1">
        <AlertTriangle size={11} /> Surgery on blocked day ({blockedConflicts.length})
      </h3>
      <ul className="space-y-1">
        {blockedConflicts.map(c => (
          <li key={c.surgery_id}
              className="flex items-center justify-between border border-red-200 bg-red-50 rounded px-3 py-2">
            <button onClick={() => onOpen(c.surgery_id)}
                    className="text-left text-[12px] flex-1 hover:underline">
              <strong className="text-red-800">{c.patient_name}</strong>
              <span className="text-gray-600"> · {c.scheduled_date} · {c.facility || '—'}</span>
              <div className="text-[11px] text-gray-500">
                Blocked: {c.blackout_label || c.blackout_reason} ({c.blackout_scope})
              </div>
            </button>
            <button onClick={() => onResolveConflict(c.surgery_id)}
                    className="text-[11px] px-2 py-1 rounded border border-red-300 text-red-700 hover:bg-red-100">
              Mark hospital notified
            </button>
          </li>
        ))}
      </ul>
    </section>
  )}
```

- [ ] **Step 3: Wire the dashboard data into the panel**

Find where `ToDoPanel` is rendered in the parent `Surgery` component. Add the wiring:

```jsx
  const qc = useQueryClient()
  const resolveConflict = useMutation({
    mutationFn: (surgery_id) =>
      api.post(`/surgery/${surgery_id}/blocked-conflict/resolve`).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['surgery-dashboard'] }),
    onError: (e) => alert(e?.response?.data?.detail || 'Failed to resolve'),
  })

  <ToDoPanel todos={dash?.todo || []}
             hospitalUnbooked={dash?.hospital_unbooked || []}
             officeUnderbooked={dash?.office_underbooked || []}
             blockedConflicts={dash?.blocked_conflicts || []}
             onResolveConflict={(id) => resolveConflict.mutate(id)}
             onOpen={(id) => navigate(`/surgery/${id}`)} />
```

- [ ] **Step 4: Verify the build**

```bash
cd frontend && npm run build 2>&1 | tail -5
```

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/Surgery.jsx
git commit -m "feat(surgery): To-do panel — Blocked-day conflicts section + resolve action"
```

---

## Section 4 — Verification

### Task C6: Deploy + smoke test

- [ ] **Step 1: Apply the column in production**

```bash
# Same temporary public-IP pattern. Apply ALTER TABLE before backend deploy:
export PATH="/opt/homebrew/share/google-cloud-sdk/bin:$PATH"
CURRENT_IP=$(curl -s https://api.ipify.org)
gcloud sql instances patch app-db --project=wwc-solutions --assign-ip \
  --authorized-networks=$CURRENT_IP/32 --quiet
PUBIP=$(gcloud sql instances describe app-db --project=wwc-solutions --format=json | \
        python3 -c "import sys,json; d=json.load(sys.stdin); print(next(ip['ipAddress'] for ip in d['ipAddresses'] if ip['type']=='PRIMARY'))")
export PGPASSWORD=$(gcloud secrets versions access latest --secret=cloudsql-postgres-root-password --project=wwc-solutions)
psql "host=$PUBIP user=postgres dbname=wwc_app sslmode=require" -c \
  "ALTER TABLE surgeries
   ADD COLUMN IF NOT EXISTS blocked_conflict_notified_at TIMESTAMP,
   ADD COLUMN IF NOT EXISTS blocked_conflict_notified_by VARCHAR(120);"
unset PGPASSWORD
gcloud sql instances patch app-db --project=wwc-solutions --no-assign-ip \
  --clear-authorized-networks --quiet
```

- [ ] **Step 2: Deploy backend + frontend**

```bash
cd backend && gcloud builds submit . --project=wwc-solutions --region=us-east4 \
  --tag=us-east4-docker.pkg.dev/wwc-solutions/app/backend:v25
gcloud run deploy backend --image=us-east4-docker.pkg.dev/wwc-solutions/app/backend:v25 \
  --region=us-east4 --project=wwc-solutions
cd ../frontend && gcloud builds submit . --project=wwc-solutions --region=us-east4 \
  --tag=us-east4-docker.pkg.dev/wwc-solutions/app/frontend:v23
gcloud run deploy frontend --image=us-east4-docker.pkg.dev/wwc-solutions/app/frontend:v23 \
  --region=us-east4 --project=wwc-solutions
```

- [ ] **Step 3: Manually create a test conflict + verify**

In the UI:
1. Visit `/surgery/block-schedule` (or wherever blackouts are added) and add a SurgeryBlackoutDay for a date that has an upcoming surgery booked. (Use a known confirmed surgery from the dashboard list; pick its scheduled_date and add an `office`-scope blackout for that day.)
2. Refresh `/surgery`. The To-do panel should show the new "Surgery on blocked day" section with the affected surgery and a **Mark hospital notified** button.
3. Click the resolve button. The row should disappear and the dashboard's total count should decrease.

- [ ] **Step 4: Push**

```bash
git push origin main
```
