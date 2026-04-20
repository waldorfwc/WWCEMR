# Charts Page with Inline Fax Log Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rework `/documents` (Charts) to show patient list + recent fax log side-by-side with practice-wide totals up top, so staff can instantly see who's been faxed to EMA. Wire `FaxLog.sent_by` via session auth. Reorder TopNav and drop the standalone Fax log route.

**Architecture:** Four backend changes (sent_by wiring, `/fax/chart-summary` new endpoint, `/fax/recent` extended with DOB/doc_types/filters, DOB search on `/documents/patients`) followed by three frontend changes (TopNav reorder, new `useChartFaxSummary` hook, full rewrite of `Documents.jsx`). `pages/FaxLog.jsx` is deleted.

**Tech Stack:** FastAPI + SQLAlchemy, pytest, React 18 + Vite + Tailwind + React Query v5.

**Reference spec:** `docs/superpowers/specs/2026-04-20-charts-page-with-fax-log-design.md`

---

## Pre-flight notes

- HEAD before this plan: `git log -1 --oneline` shows the last PatientChart accordion tweak from today.
- `/documents/patients` **already returns `dob`** (verified in `backend/app/routers/documents.py:259`); we only need to add DOB to the search predicate.
- `auth.get_current_user` **already exists** (`backend/app/routers/auth.py:40-50`) and reads a Bearer token from headers. We'll use it as a FastAPI dependency.
- Backend tests in `backend/tests/conftest.py` use a `client` fixture that doesn't send an auth header. Adding `get_current_user` as a hard dependency on existing endpoints will break those tests — we MUST add an override.
- `send_batch` is called both as an HTTP endpoint AND directly as a Python function from `fax.py`'s legacy `/send` route (Phase 1 Task 6). The refactor must preserve both paths.

---

## File structure

**Backend — modified:**
- `backend/app/routers/fax_batch.py` — extract `_send_batch_core(payload, db, sent_by)` from existing `send_batch`; add route-level `get_current_user` dep; extend `/recent` with DOB/doc_types/filters; add `/chart-summary` endpoint.
- `backend/app/routers/fax.py` — add `get_current_user` dep to `/send`; pass `sent_by` through.
- `backend/app/routers/documents.py` — extend `list_patients` search to include DOB.
- `backend/tests/conftest.py` — override `get_current_user` to yield a test user.
- `backend/tests/test_fax_send_batch.py` — assert `sent_by` populated on rows.
- `backend/tests/test_fax_recent.py` — assert new fields + filters work.
- `backend/tests/test_fax_send_compat.py` — assert legacy `/send` populates `sent_by`.

**Backend — created:**
- `backend/tests/test_fax_chart_summary.py`
- `backend/tests/test_patients_search_dob.py`

**Frontend — modified:**
- `frontend/src/components/layout/TopNav.jsx` — reorder + drop Fax log
- `frontend/src/App.jsx` — drop `/fax-log` route + its import
- `frontend/src/pages/Documents.jsx` — full rewrite

**Frontend — created:**
- `frontend/src/hooks/useChartFaxSummary.js`
- `frontend/src/pages/documents/FaxLogPane.jsx`

**Frontend — deleted:**
- `frontend/src/pages/FaxLog.jsx`

---

## Task 1: Conftest auth override + extract `_send_batch_core`

This is a refactor task with no feature change. It unblocks every subsequent backend task that depends on `get_current_user`.

**Files:**
- Modify: `backend/tests/conftest.py`
- Modify: `backend/app/routers/fax_batch.py`

- [ ] **Step 1: Add `get_current_user` override to conftest**

Open `/Users/wwcclaudecode/Documents/wwc-era-project/backend/tests/conftest.py`. At the top of the file, replace the existing imports block and `override_get_db` section so the whole file reads:

```python
"""Shared pytest fixtures: in-memory SQLite + FastAPI TestClient."""
import os
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from app.database import Base, get_db
from app.main import app
from app.routers.auth import get_current_user


TEST_USER = {"email": "tester@waldorfwomenscare.com", "name": "Test User"}


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSession()
    yield session
    session.close()
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client(db):
    def override_get_db():
        try:
            yield db
        finally:
            pass
    def override_get_current_user():
        return TEST_USER

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
```

- [ ] **Step 2: Extract `_send_batch_core` and add auth dep to `send_batch` route**

Open `/Users/wwcclaudecode/Documents/wwc-era-project/backend/app/routers/fax_batch.py`.

Near the existing imports, add:
```python
from app.routers.auth import get_current_user
```

Find the current `send_batch` function definition. Rename it to `_send_batch_core` and make it pure (no `Depends`). Add a NEW `send_batch` route wrapper that depends on `get_current_user` and forwards.

Target shape (replace the entire existing `send_batch` function and add the new route function):

```python
def _send_batch_core(payload: SendBatchPayload, db: Session, sent_by: Optional[str] = None):
    """Pure implementation — no Depends. Callable from other routes.

    Writes FaxLog.sent_by = sent_by on every new row.
    """
    if not payload.doc_ids:
        raise HTTPException(status_code=400, detail="doc_ids must not be empty")
    if payload.grouping_mode not in {m.value for m in GroupingMode}:
        raise HTTPException(status_code=400, detail=f"Invalid grouping_mode: {payload.grouping_mode}")

    patient_name = _patient_name(db, payload.chart_number)
    mode = payload.grouping_mode

    log_action(db, "FAX_BATCH_SENT", "fax",
               user_name=sent_by,
               description=f"Batch fax chart={payload.chart_number} docs={len(payload.doc_ids)} mode={mode} to {payload.dest_fax}")

    faxes = []
    # ... ENTIRE existing body (separate / combined / by_type branches) goes here,
    # unchanged except that _send_one_and_log now receives sent_by.
```

Then update `_send_one_and_log` to accept and write `sent_by`:

```python
def _send_one_and_log(
    db: Session,
    chart_number: str,
    dest_fax: str,
    doc_ids: list[str],
    file_path: Optional[str],
    cover_text: Optional[str],
    patient_name: str,
    grouping_mode: str,
    sent_by: Optional[str] = None,
    not_found_error: Optional[str] = None,
) -> dict:
    log = FaxLog(
        chart_number=chart_number,
        doc_ids=doc_ids,
        grouping_mode=grouping_mode,
        dest_fax=dest_fax,
        sent_by=sent_by,
    )
    db.add(log)
    db.flush()
    # ... rest of the existing body unchanged.
```

Every call site in the three grouping branches must now pass `sent_by=sent_by`. Example for the `separate` path:
```python
faxes.append(_send_one_and_log(
    db, payload.chart_number, payload.dest_fax, [doc_id],
    file_path=doc.file_path, cover_text=payload.cover_text,
    patient_name=patient_name, grouping_mode=mode,
    sent_by=sent_by,
))
```
Do the same in `combined` and `by_type` branches.

Add the thin route wrapper ABOVE `_send_batch_core`:

```python
@router.post("/send-batch")
def send_batch(
    payload: SendBatchPayload,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    return _send_batch_core(payload, db, sent_by=current_user.get("email"))
```

ALSO update the retry endpoint to populate sent_by:

```python
@router.post("/retry/{fax_log_id}")
def fax_retry(
    fax_log_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    original = db.query(FaxLog).filter(FaxLog.id == fax_log_id).first()
    if not original:
        raise HTTPException(status_code=404, detail="Fax log not found")
    mode = original.grouping_mode.value if hasattr(original.grouping_mode, "value") else original.grouping_mode
    batch = _send_batch_core(
        SendBatchPayload(
            chart_number=original.chart_number,
            doc_ids=list(original.doc_ids or []),
            dest_fax=original.dest_fax,
            grouping_mode=mode,
            cover_text=None,
        ),
        db=db,
        sent_by=current_user.get("email"),
    )
    for fax in batch["faxes"]:
        new_id = fax.get("fax_log_id")
        if new_id:
            new_log = db.query(FaxLog).filter(FaxLog.id == new_id).first()
            if new_log:
                new_log.retry_of = original.id
    db.commit()
    return batch
```

- [ ] **Step 3: Update `fax.py` legacy `/send` to pass `sent_by`**

Open `/Users/wwcclaudecode/Documents/wwc-era-project/backend/app/routers/fax.py`. Add near the other imports:
```python
from app.routers.auth import get_current_user
```

Change the old `from app.routers.fax_batch import send_batch, SendBatchPayload` line (inside `fax_document`) so that instead of calling `send_batch`, we call `_send_batch_core`. Update the `fax_document` signature and call:

```python
@router.post("/send")
def fax_document(
    payload: dict,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    # ... existing validation + intake branch unchanged ...

    # Patient-doc path:
    doc = db.query(PatientDocument).filter(PatientDocument.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    from app.routers.fax_batch import _send_batch_core, SendBatchPayload
    batch_result = _send_batch_core(
        SendBatchPayload(
            chart_number=doc.chart_number,
            doc_ids=[str(doc.id)],
            dest_fax=fax_number,
            grouping_mode="separate",
            cover_text=cover_text or None,
        ),
        db=db,
        sent_by=current_user.get("email"),
    )
    # ... rest unchanged ...
```

The intake branch also gains `current_user` availability for its audit log (optional; include for consistency):

```python
if doc_type == "intake":
    # ... existing logic ...
    log_action(db, "FAX_SENT", "fax",
               user_name=current_user.get("email"),
               description=f"Faxed intake to {fax_number} for {intake_doc.patient_name_raw} — msg {result.get('message_id')}")
    return result
```

- [ ] **Step 4: Run tests**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/ -v 2>&1 | tail -20
```
Expected: all 27 prior tests PASS. `sent_by` is now populated on new rows, but no existing test asserts that — so no change in pass/fail counts.

- [ ] **Step 5: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/tests/conftest.py backend/app/routers/fax_batch.py backend/app/routers/fax.py
git commit -m "refactor(backend): extract _send_batch_core, wire sent_by via get_current_user"
```

---

## Task 2: Assert `sent_by` in existing tests

**Files:**
- Modify: `backend/tests/test_fax_send_batch.py`
- Modify: `backend/tests/test_fax_send_compat.py`

- [ ] **Step 1: Extend first send_batch test to assert sent_by**

Open `/Users/wwcclaudecode/Documents/wwc-era-project/backend/tests/test_fax_send_batch.py`. Find `test_send_batch_separate_one_doc`. Add one line at the end of its assertions block (after the existing assertions):

```python
    assert logs[0].sent_by == "tester@waldorfwomenscare.com"
```

- [ ] **Step 2: Extend compat test similarly**

Open `/Users/wwcclaudecode/Documents/wwc-era-project/backend/tests/test_fax_send_compat.py`. Find `test_legacy_fax_send_creates_fax_log`. Add at the end of its assertions block:

```python
    assert logs[0].sent_by == "tester@waldorfwomenscare.com"
```

- [ ] **Step 3: Run tests**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_fax_send_batch.py tests/test_fax_send_compat.py -v 2>&1 | tail -15
```
Expected: all still PASS with the new assertions.

- [ ] **Step 4: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/tests/test_fax_send_batch.py backend/tests/test_fax_send_compat.py
git commit -m "test(backend): assert FaxLog.sent_by populated on send-batch + legacy /send"
```

---

## Task 3: `GET /api/fax/chart-summary`

**Files:**
- Modify: `backend/app/routers/fax_batch.py`
- Create: `backend/tests/test_fax_chart_summary.py`

- [ ] **Step 1: Write failing tests**

Create `/Users/wwcclaudecode/Documents/wwc-era-project/backend/tests/test_fax_chart_summary.py`:

```python
"""GET /api/fax/chart-summary — per-chart fax-count and last-sent-date map."""
from datetime import datetime, timedelta
from app.models.fax_log import FaxLog, FaxLogStatus, GroupingMode


def test_chart_summary_groups_by_chart(client, db):
    now = datetime.utcnow()
    db.add_all([
        FaxLog(chart_number="AAA", doc_ids=["d1"], grouping_mode=GroupingMode.SEPARATE,
               dest_fax="1", status=FaxLogStatus.SENT, sent_at=now - timedelta(hours=1)),
        FaxLog(chart_number="AAA", doc_ids=["d2"], grouping_mode=GroupingMode.SEPARATE,
               dest_fax="1", status=FaxLogStatus.DELIVERED, sent_at=now - timedelta(minutes=5)),
        FaxLog(chart_number="BBB", doc_ids=["d3"], grouping_mode=GroupingMode.SEPARATE,
               dest_fax="1", status=FaxLogStatus.SENT, sent_at=now - timedelta(days=2)),
    ])
    db.commit()

    r = client.get("/api/fax/chart-summary")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    # 2 charts
    assert len(body) == 2

    by_chart = {row["chart_number"]: row for row in body}
    assert by_chart["AAA"]["fax_count"] == 2
    assert by_chart["BBB"]["fax_count"] == 1
    # last_sent_at is the max sent_at for the chart — AAA's newer row
    assert by_chart["AAA"]["last_sent_at"] > by_chart["BBB"]["last_sent_at"]


def test_chart_summary_empty_db_returns_empty_list(client, db):
    r = client.get("/api/fax/chart-summary")
    assert r.status_code == 200
    assert r.json() == []
```

- [ ] **Step 2: Run to verify fail**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_fax_chart_summary.py -v 2>&1 | tail -10
```
Expected: 404.

- [ ] **Step 3: Add endpoint to `fax_batch.py`**

Append this endpoint to `/Users/wwcclaudecode/Documents/wwc-era-project/backend/app/routers/fax_batch.py` (on `router`, not `log_router`):

```python
@router.get("/chart-summary")
def fax_chart_summary(db: Session = Depends(get_db)):
    """Per-chart fax aggregates for the patient-list fax indicator.
    Returns one row per chart_number that has any FaxLog activity."""
    from sqlalchemy import func as sql_func
    rows = (
        db.query(
            FaxLog.chart_number,
            sql_func.count(FaxLog.id).label("fax_count"),
            sql_func.max(FaxLog.sent_at).label("last_sent_at"),
        )
        .group_by(FaxLog.chart_number)
        .all()
    )
    return [
        {
            "chart_number": r.chart_number,
            "fax_count": int(r.fax_count),
            "last_sent_at": r.last_sent_at.isoformat() + "Z" if r.last_sent_at else None,
        }
        for r in rows
    ]
```

- [ ] **Step 4: Run tests**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_fax_chart_summary.py -v 2>&1 | tail -8
```
Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/routers/fax_batch.py backend/tests/test_fax_chart_summary.py
git commit -m "feat(backend): GET /fax/chart-summary for patient-list fax indicator"
```

---

## Task 4: Extend `GET /api/fax/recent` with DOB, doc_types, sent_by, and window/status filters

**Files:**
- Modify: `backend/app/routers/fax_batch.py`
- Modify: `backend/tests/test_fax_recent.py`

- [ ] **Step 1: Add failing tests**

Append to `/Users/wwcclaudecode/Documents/wwc-era-project/backend/tests/test_fax_recent.py`:

```python
from app.models.document import PatientDocument
from datetime import date


def test_recent_includes_dob_doc_types_sent_by(client, db):
    db.merge(PatientDirectory(chart_number="DOBTEST", patient_name="Last, First", dob=date(1980, 5, 15)))
    # seed docs so doc_types can resolve
    doc1 = PatientDocument(chart_number="DOBTEST", doc_type="Insurance Card",
                           doc_id="I1", filename="a.pdf", file_path="/tmp/a.pdf")
    doc2 = PatientDocument(chart_number="DOBTEST", doc_type="Progress Note",
                           doc_id="P1", filename="b.pdf", file_path="/tmp/b.pdf")
    db.add_all([doc1, doc2])
    db.commit()
    db.refresh(doc1); db.refresh(doc2)

    db.add(FaxLog(
        chart_number="DOBTEST",
        doc_ids=[str(doc1.id), str(doc2.id)],
        grouping_mode=GroupingMode.COMBINED,
        dest_fax="2402522141",
        status=FaxLogStatus.SENT,
        sent_at=datetime.utcnow(),
        sent_by="tester@waldorfwomenscare.com",
    ))
    db.commit()

    r = client.get("/api/fax/recent")
    assert r.status_code == 200
    row = r.json()[0]
    assert row["dob"] == "1980-05-15"
    assert set(row["doc_types"]) == {"Insurance Card", "Progress Note"}
    assert row["sent_by"] == "tester@waldorfwomenscare.com"


def test_recent_window_filter(client, db):
    now = datetime.utcnow()
    db.add_all([
        FaxLog(chart_number="W1", doc_ids=["x"], grouping_mode=GroupingMode.SEPARATE,
               dest_fax="1", status=FaxLogStatus.SENT, sent_at=now - timedelta(days=3)),
        FaxLog(chart_number="W2", doc_ids=["y"], grouping_mode=GroupingMode.SEPARATE,
               dest_fax="1", status=FaxLogStatus.SENT, sent_at=now - timedelta(days=20)),
    ])
    db.commit()

    r_week = client.get("/api/fax/recent?window=7&limit=50")
    charts = {row["chart_number"] for row in r_week.json()}
    assert "W1" in charts
    assert "W2" not in charts

    r_month = client.get("/api/fax/recent?window=30&limit=50")
    charts = {row["chart_number"] for row in r_month.json()}
    assert charts == {"W1", "W2"}


def test_recent_status_filter(client, db):
    now = datetime.utcnow()
    db.add_all([
        FaxLog(chart_number="S1", doc_ids=["x"], grouping_mode=GroupingMode.SEPARATE,
               dest_fax="1", status=FaxLogStatus.SENT, sent_at=now),
        FaxLog(chart_number="S2", doc_ids=["y"], grouping_mode=GroupingMode.SEPARATE,
               dest_fax="1", status=FaxLogStatus.FAILED, sent_at=now),
    ])
    db.commit()

    r = client.get("/api/fax/recent?status=failed&limit=50")
    charts = {row["chart_number"] for row in r.json()}
    assert charts == {"S2"}
```

- [ ] **Step 2: Run to verify failures**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_fax_recent.py -v 2>&1 | tail -15
```
Expected: the 3 new tests FAIL — `dob`, `doc_types`, `sent_by` not in response; query params ignored.

- [ ] **Step 3: Replace the `fax_recent` handler in `fax_batch.py`**

Find the existing `@router.get("/recent")` + `fax_recent` function in `/Users/wwcclaudecode/Documents/wwc-era-project/backend/app/routers/fax_batch.py` and replace it with:

```python
@router.get("/recent")
def fax_recent(
    limit: int = 5,
    window: Optional[int] = None,  # days; None = no window
    status: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Recent fax activity for Dashboard card AND Charts-page fax-log pane."""
    q = db.query(FaxLog)
    if window:
        from datetime import datetime, timedelta
        q = q.filter(FaxLog.sent_at >= datetime.utcnow() - timedelta(days=window))
    if status:
        q = q.filter(FaxLog.status == status)

    rows = q.order_by(FaxLog.sent_at.desc()).limit(max(1, min(limit, 200))).all()
    if not rows:
        return []

    # Bulk patient lookup
    charts = {r.chart_number for r in rows}
    dir_rows = db.query(PatientDirectory).filter(PatientDirectory.chart_number.in_(charts)).all()
    patient_map = {p.chart_number: p for p in dir_rows}

    # Bulk doc_type lookup — gather every doc_id across all rows, one query
    all_doc_ids = {d for r in rows for d in (r.doc_ids or [])}
    from app.models.document import PatientDocument
    doc_types_by_id: dict[str, str] = {}
    if all_doc_ids:
        doc_rows = db.query(PatientDocument.id, PatientDocument.doc_type).filter(
            PatientDocument.id.in_(all_doc_ids)
        ).all()
        doc_types_by_id = {str(d.id): d.doc_type for d in doc_rows}

    def serialize(r: FaxLog) -> dict:
        p = patient_map.get(r.chart_number)
        types = sorted({doc_types_by_id[d] for d in (r.doc_ids or []) if d in doc_types_by_id})
        return {
            "id": str(r.id),
            "chart_number": r.chart_number,
            "patient_name": p.patient_name if p else r.chart_number,
            "dob": str(p.dob) if p and p.dob else None,
            "status": r.status.value if hasattr(r.status, "value") else r.status,
            "sent_at": r.sent_at.isoformat() + "Z" if r.sent_at else None,
            "dest_fax": r.dest_fax,
            "doc_count": len(r.doc_ids or []),
            "doc_types": types,
            "sent_by": r.sent_by,
        }

    return [serialize(r) for r in rows]
```

- [ ] **Step 4: Run tests**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_fax_recent.py -v 2>&1 | tail -15
```
Expected: 6 PASS (3 original + 3 new).

- [ ] **Step 5: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/routers/fax_batch.py backend/tests/test_fax_recent.py
git commit -m "feat(backend): /fax/recent — add dob, doc_types, sent_by, window+status filters"
```

---

## Task 5: DOB search on `/api/documents/patients`

**Files:**
- Modify: `backend/app/routers/documents.py`
- Create: `backend/tests/test_patients_search_dob.py`

- [ ] **Step 1: Write failing test**

Create `/Users/wwcclaudecode/Documents/wwc-era-project/backend/tests/test_patients_search_dob.py`:

```python
"""Search by DOB on /api/documents/patients."""
from datetime import date
from app.models.patient_directory import PatientDirectory
from app.models.document import PatientDocument


def test_search_by_dob_iso(client, db):
    # Two patients with different DOBs. Seed a doc for each so list_patients shows them.
    db.merge(PatientDirectory(chart_number="CA", patient_name="Alpha, A", dob=date(1985, 2, 14)))
    db.merge(PatientDirectory(chart_number="CB", patient_name="Beta, B", dob=date(1990, 8, 1)))
    db.add_all([
        PatientDocument(chart_number="CA", doc_type="x", doc_id="1",
                        filename="a.pdf", file_path="/tmp/a.pdf"),
        PatientDocument(chart_number="CB", doc_type="x", doc_id="2",
                        filename="b.pdf", file_path="/tmp/b.pdf"),
    ])
    db.commit()

    r = client.get("/api/documents/patients?search=1985-02-14")
    assert r.status_code == 200
    body = r.json()
    charts = {p["chart_number"] for p in body["patients"]}
    assert charts == {"CA"}


def test_search_by_partial_dob(client, db):
    db.merge(PatientDirectory(chart_number="Y1", patient_name="Y, A", dob=date(1985, 2, 14)))
    db.merge(PatientDirectory(chart_number="Y2", patient_name="Y, B", dob=date(1985, 7, 22)))
    db.merge(PatientDirectory(chart_number="Y3", patient_name="Y, C", dob=date(1992, 1, 1)))
    db.add_all([
        PatientDocument(chart_number=c, doc_type="x", doc_id="1",
                        filename=f"{c}.pdf", file_path=f"/tmp/{c}.pdf")
        for c in ("Y1", "Y2", "Y3")
    ])
    db.commit()

    # Partial year-only match
    r = client.get("/api/documents/patients?search=1985")
    charts = {p["chart_number"] for p in r.json()["patients"]}
    assert charts == {"Y1", "Y2"}
```

- [ ] **Step 2: Run to verify failure**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_patients_search_dob.py -v 2>&1 | tail -10
```
Expected: both FAIL — current search doesn't match DOB.

- [ ] **Step 3: Extend search in `list_patients`**

In `/Users/wwcclaudecode/Documents/wwc-era-project/backend/app/routers/documents.py`, find the `list_patients` function (around line 221) and replace the `if search:` block with:

```python
    if search:
        from sqlalchemy import cast, String
        # Search matches patient_name, chart_number, or DOB (substring).
        # DOB stored as Date — cast to string for substring matching.
        matching_charts = db.query(PatientDirectory.chart_number).filter(
            PatientDirectory.patient_name.ilike(f"%{search}%")
            | cast(PatientDirectory.dob, String).ilike(f"%{search}%")
        ).subquery()
        q = q.filter(
            PatientDocument.chart_number.ilike(f"%{search}%")
            | PatientDocument.chart_number.in_(matching_charts)
        )
```

- [ ] **Step 4: Run tests**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_patients_search_dob.py tests/ -v 2>&1 | tail -15
```
Expected: 2 new + all prior tests PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/routers/documents.py backend/tests/test_patients_search_dob.py
git commit -m "feat(backend): /documents/patients search matches DOB (YYYY-MM-DD substring)"
```

---

## Task 6: Frontend — TopNav reorder + drop `/fax-log`

**Files:**
- Modify: `frontend/src/components/layout/TopNav.jsx`
- Modify: `frontend/src/App.jsx`
- Delete: `frontend/src/pages/FaxLog.jsx`

- [ ] **Step 1: Update `nav` array in `TopNav.jsx`**

Open `/Users/wwcclaudecode/Documents/wwc-era-project/frontend/src/components/layout/TopNav.jsx`. Replace the existing `const nav = [...]` array with:

```jsx
const nav = [
  { to: '/',          label: 'Dashboard' },
  { to: '/documents', label: 'Charts' },
  { to: '/ar',        label: 'A/R' },
  { to: '/claims',    label: 'Claims' },
  { to: '/denials',   label: 'Denials' },
  { to: '/appeals',   label: 'Appeals' },
  { to: '/import',    label: 'Import' },
  { to: '/audit',     label: 'Audit' },
]
```

- [ ] **Step 2: Drop the `/fax-log` route from `App.jsx`**

Open `/Users/wwcclaudecode/Documents/wwc-era-project/frontend/src/App.jsx`. Remove the `import FaxLogPage from './pages/FaxLog'` line and remove the `<Route path="/fax-log" element={<FaxLogPage />} />` line.

- [ ] **Step 3: Delete `FaxLog.jsx`**

```bash
rm /Users/wwcclaudecode/Documents/wwc-era-project/frontend/src/pages/FaxLog.jsx
```

- [ ] **Step 4: Build**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/frontend && npx vite build 2>&1 | tail -5
```
Expected: success.

- [ ] **Step 5: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add -A frontend/src/App.jsx frontend/src/components/layout/TopNav.jsx frontend/src/pages/
git commit -m "feat(frontend): reorder TopNav, drop standalone Fax log route"
```

---

## Task 7: Frontend — `useChartFaxSummary` hook

**Files:**
- Create: `frontend/src/hooks/useChartFaxSummary.js`

- [ ] **Step 1: Create the hook**

Write `/Users/wwcclaudecode/Documents/wwc-era-project/frontend/src/hooks/useChartFaxSummary.js`:

```jsx
import { useQuery } from '@tanstack/react-query'
import api from '../utils/api'

// Returns { [chart_number]: { fax_count, last_sent_at } } for every chart
// that has any FaxLog activity. Stale-time 2 minutes — plenty for a
// migration workflow where ops sees updates on next hover/nav.
export function useChartFaxSummary() {
  return useQuery({
    queryKey: ['fax-chart-summary'],
    queryFn: async () => {
      const rows = await api.get('/fax/chart-summary').then(r => r.data)
      const map = {}
      for (const r of rows) {
        map[r.chart_number] = { fax_count: r.fax_count, last_sent_at: r.last_sent_at }
      }
      return map
    },
    staleTime: 2 * 60 * 1000,
  })
}
```

- [ ] **Step 2: Build**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/frontend && npx vite build 2>&1 | tail -5
```
Expected: success.

- [ ] **Step 3: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add frontend/src/hooks/useChartFaxSummary.js
git commit -m "feat(frontend): useChartFaxSummary hook — chart→fax-count+last-sent-at map"
```

---

## Task 8: Frontend — FaxLogPane component

**Files:**
- Create: `frontend/src/pages/documents/FaxLogPane.jsx`

- [ ] **Step 1: Create the component**

Make sure the directory exists:
```bash
mkdir -p /Users/wwcclaudecode/Documents/wwc-era-project/frontend/src/pages/documents
```

Write `/Users/wwcclaudecode/Documents/wwc-era-project/frontend/src/pages/documents/FaxLogPane.jsx`:

```jsx
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { format } from 'date-fns'
import api from '../../utils/api'
import FaxStatusChip from '../../components/FaxStatusChip'

const STATUSES = [
  { value: '',          label: 'All status' },
  { value: 'queued',    label: 'Queued' },
  { value: 'sent',      label: 'Sent' },
  { value: 'delivered', label: 'Delivered' },
  { value: 'failed',    label: 'Failed' },
]
const WINDOWS = [
  { value: 7,  label: 'Last 7 days' },
  { value: 30, label: 'Last 30 days' },
  { value: 90, label: 'Last 90 days' },
]

export default function FaxLogPane() {
  const [status, setStatus] = useState('')
  const [window, setWindow] = useState(7)

  const q = useQuery({
    queryKey: ['fax-recent-pane', status, window],
    queryFn: () => api.get('/fax/recent', {
      params: { limit: 50, window, status: status || undefined },
    }).then(r => r.data),
    refetchInterval: (query) => {
      const data = query.state?.data
      return Array.isArray(data) && data.some(r => r.status === 'queued' || r.status === 'sent')
        ? 30_000
        : false
    },
  })

  async function retry(id) {
    await api.post(`/fax/retry/${id}`)
    q.refetch()
  }

  const rows = q.data || []

  return (
    <div className="bg-white border border-border-subtle rounded-lg overflow-hidden flex flex-col">
      <div className="px-4 py-2.5 border-b border-border-subtle flex justify-between items-center">
        <div className="font-serif text-ink text-[15px] font-semibold">Recent faxes</div>
        <div className="flex gap-2">
          <select className="input text-[11px] py-1 px-2 w-[130px]"
                  value={status} onChange={e => setStatus(e.target.value)}>
            {STATUSES.map(s => <option key={s.value} value={s.value}>{s.label}</option>)}
          </select>
          <select className="input text-[11px] py-1 px-2 w-[130px]"
                  value={window} onChange={e => setWindow(Number(e.target.value))}>
            {WINDOWS.map(w => <option key={w.value} value={w.value}>{w.label}</option>)}
          </select>
        </div>
      </div>
      <div className="overflow-auto flex-1">
        <table className="w-full text-[12px]">
          <thead className="bg-plum-50 sticky top-0">
            <tr>
              <th className="table-th">Sent</th>
              <th className="table-th">Patient</th>
              <th className="table-th">DOB</th>
              <th className="table-th">Chart</th>
              <th className="table-th">Docs</th>
              <th className="table-th">Doc types</th>
              <th className="table-th">Dest</th>
              <th className="table-th">Status</th>
              <th className="table-th">Sent by</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(r => (
              <tr key={r.id} className="table-row">
                <td className="table-td whitespace-nowrap">{r.sent_at ? format(new Date(r.sent_at), 'M/d h:mma') : '—'}</td>
                <td className="table-td">{r.patient_name}</td>
                <td className="table-td">{r.dob || '—'}</td>
                <td className="table-td">#{r.chart_number}</td>
                <td className="table-td">{r.doc_count}</td>
                <td className="table-td text-muted">{(r.doc_types || []).join(', ') || '—'}</td>
                <td className="table-td">{r.dest_fax}</td>
                <td className="table-td"><FaxStatusChip row={r} onRetry={() => retry(r.id)} /></td>
                <td className="table-td text-muted">{r.sent_by || '—'}</td>
              </tr>
            ))}
            {rows.length === 0 && !q.isLoading && (
              <tr><td colSpan={9} className="table-td text-center text-muted py-8">No faxes in this window.</td></tr>
            )}
            {q.isLoading && (
              <tr><td colSpan={9} className="table-td text-center text-muted py-8">Loading...</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Build**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/frontend && npx vite build 2>&1 | tail -5
```
Expected: success.

- [ ] **Step 3: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add frontend/src/pages/documents/FaxLogPane.jsx
git commit -m "feat(frontend): FaxLogPane component — recent fax log with filters"
```

---

## Task 9: Frontend — Rewrite `Documents.jsx`

**Files:**
- Modify: `frontend/src/pages/Documents.jsx` (full rewrite)

- [ ] **Step 1: Replace the file**

Overwrite `/Users/wwcclaudecode/Documents/wwc-era-project/frontend/src/pages/Documents.jsx` with:

```jsx
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Search } from 'lucide-react'
import api from '../utils/api'
import { useChartFaxSummary } from '../hooks/useChartFaxSummary'
import FaxLogPane from './documents/FaxLogPane'

const TODAY_ISO = () => {
  const d = new Date()
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const dd = String(d.getDate()).padStart(2, '0')
  return `${d.getFullYear()}-${mm}-${dd}`
}

function faxChip(summary) {
  if (!summary?.last_sent_at) {
    return <span className="text-[10px] text-muted opacity-45">—</span>
  }
  const sent = new Date(summary.last_sent_at)
  const sentIso = `${sent.getFullYear()}-${String(sent.getMonth() + 1).padStart(2, '0')}-${String(sent.getDate()).padStart(2, '0')}`
  const label = `✓ ${sent.getMonth() + 1}/${sent.getDate()}`
  const isToday = sentIso === TODAY_ISO()
  const cls = isToday
    ? 'bg-green-100 text-green-800'
    : 'bg-plum-100 text-plum-700'
  return (
    <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded whitespace-nowrap ${cls}`}>
      {label}
    </span>
  )
}

export default function Documents() {
  const navigate = useNavigate()
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const PER_PAGE = 100

  const { data: indexStatus } = useQuery({
    queryKey: ['doc-index-status'],
    queryFn: () => api.get('/documents/index/status').then(r => r.data),
  })

  const { data: patients, isLoading } = useQuery({
    queryKey: ['doc-patients', search, page],
    queryFn: () => api.get('/documents/patients', {
      params: { search: search || undefined, page, per_page: PER_PAGE },
    }).then(r => r.data),
    enabled: (indexStatus?.indexed_documents || 0) > 0,
  })

  const { data: faxSummary } = useChartFaxSummary()

  const totalDocs = indexStatus?.indexed_documents || 0
  const totalPatients = indexStatus?.indexed_patients || 0

  return (
    <div>
      {/* Header */}
      <div className="flex items-baseline justify-between mb-4">
        <div>
          <h1 className="font-serif font-semibold text-ink text-[22px] tracking-tight m-0">Patient Charts</h1>
          <div className="text-muted text-[12px] mt-0.5">
            <span className="font-serif text-ink font-semibold text-[14px]">{totalDocs.toLocaleString()}</span> documents
            <span className="mx-1">·</span>
            <span className="font-serif text-ink font-semibold text-[14px]">{totalPatients.toLocaleString()}</span> patients
          </div>
        </div>
      </div>

      {/* Two-pane layout */}
      <div className="grid gap-3" style={{ gridTemplateColumns: '280px 1fr', minHeight: 'calc(100vh - 180px)' }}>
        {/* Patient list */}
        <div className="bg-white border border-border-subtle rounded-lg overflow-hidden flex flex-col">
          <div className="p-2 border-b border-border-subtle">
            <div className="relative">
              <Search size={12} className="absolute left-2 top-2 text-muted" />
              <input
                className="w-full pl-6 pr-2 py-1.5 border border-border-subtle rounded text-[11px] focus:outline-none focus:ring-1 focus:ring-plum-700"
                placeholder="Search name, chart #, or DOB..."
                value={search}
                onChange={e => { setSearch(e.target.value); setPage(1) }}
              />
            </div>
          </div>
          <div className="flex-1 overflow-y-auto">
            <div className="px-3 py-1.5 text-[11px] text-muted border-b border-border-subtle bg-plum-50">
              {patients?.total?.toLocaleString() || 0} patients
            </div>
            {isLoading ? (
              <div className="text-center text-muted text-[11px] py-8">Loading...</div>
            ) : (
              patients?.patients?.map(p => (
                <button
                  key={p.chart_number}
                  onClick={() => navigate(`/chart/${p.chart_number}`)}
                  className="w-full text-left px-3 py-2 text-[11px] border-b border-plum-100 hover:bg-plum-50 transition-colors flex justify-between items-start gap-2"
                >
                  <div className="min-w-0 flex-1">
                    <div className="font-medium text-ink truncate">
                      {p.patient_name || `Chart ${p.chart_number}`}
                    </div>
                    <div className="text-muted text-[10px] truncate">
                      #{p.chart_number}
                      {p.dob && <> · DOB {p.dob}</>}
                      {' · '}{p.document_count}d
                    </div>
                  </div>
                  <div className="shrink-0">{faxChip(faxSummary?.[p.chart_number])}</div>
                </button>
              ))
            )}
            {patients && patients.total > PER_PAGE && (
              <div className="flex items-center justify-center gap-2 py-3 text-[11px] text-muted">
                <button onClick={() => setPage(p => Math.max(1, p - 1))}
                        disabled={page === 1}
                        className="px-2 py-1 border border-border-subtle rounded disabled:opacity-40">Prev</button>
                <span>{page} / {Math.ceil(patients.total / PER_PAGE)}</span>
                <button onClick={() => setPage(p => p + 1)}
                        disabled={page >= Math.ceil(patients.total / PER_PAGE)}
                        className="px-2 py-1 border border-border-subtle rounded disabled:opacity-40">Next</button>
              </div>
            )}
          </div>
        </div>

        {/* Recent fax log */}
        <FaxLogPane />
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Build**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/frontend && npx vite build 2>&1 | tail -5
```
Expected: success.

- [ ] **Step 3: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add frontend/src/pages/Documents.jsx
git commit -m "feat(frontend): Charts page with totals + patient list (fax chips, DOB) + fax log pane"
```

---

## Task 10: Rename Dashboard's "Recent faxes to EMA" card

The Dashboard card title was written in Phase 0 when every fax went to ModMed EMA. Faxes may now go elsewhere on occasion, so generalize the label.

**Files:**
- Modify: `frontend/src/pages/Dashboard.jsx`

- [ ] **Step 1: Change the heading**

In `/Users/wwcclaudecode/Documents/wwc-era-project/frontend/src/pages/Dashboard.jsx`, find the `Recent faxes to EMA` string in the card header and replace it with `Recent faxes`. One occurrence.

- [ ] **Step 2: Build**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/frontend && npx vite build 2>&1 | tail -5
```
Expected: success.

- [ ] **Step 3: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add frontend/src/pages/Dashboard.jsx
git commit -m "style(frontend): rename Dashboard 'Recent faxes to EMA' → 'Recent faxes'"
```

---

## Task 11: Final verification

**Files:** none — verification only.

- [ ] **Step 1: Full backend test suite**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/ -v 2>&1 | tail -15
```
Expected: all prior + new tests PASS (32+ total).

- [ ] **Step 2: Frontend build**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/frontend && npx vite build 2>&1 | tail -5
```
Expected: success.

- [ ] **Step 3: Smoke check**

- Start stack: `./start.sh` from repo root.
- Navigate to `/documents`:
  - Header shows totals and "Patient Charts" title.
  - Patient sidebar shows DOB + fax chip per patient.
  - Searching "1985" narrows to patients born in 1985.
  - Right pane shows recent fax log table with 9 columns.
  - Status / window dropdowns filter the table.
- `/` (Dashboard): "Recent faxes" card still works (renamed in Task 10).
- `/fax-log` URL: now 404s or redirects (expected).
- TopNav: `Dashboard → Charts → A/R → …` — no Fax log entry.

- [ ] **Step 4: Final empty commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git commit --allow-empty -m "test: Charts-with-fax-log verified end-to-end"
```

---

## Self-review

- **Spec coverage:** ✓
  - TopNav reorder → T6
  - Totals header → T9
  - Patient list with DOB + last-sent chip → T9 (+ backend T3/T5)
  - Right-pane fax log → T8 + T9
  - `sent_by` wiring → T1/T2
  - `/fax/chart-summary` → T3
  - `/fax/recent` extended (DOB, doc_types, sent_by, window, status) → T4
  - DOB search on `/documents/patients` → T5
  - Drop standalone `/fax-log` route → T6
- **Placeholder scan:** ✓ No TBD / TODO / "appropriately handle". One "existing body unchanged" note in T1 is a deliberate reference to code the engineer is preserving, not a placeholder.
- **Type consistency:** ✓
  - `_send_batch_core(payload, db, sent_by)` signature consistent across T1's router, T1's retry handler, and fax.py legacy delegation.
  - `useChartFaxSummary` returns `{[chart]: {fax_count, last_sent_at}}` — consumed by `Documents.jsx` as `faxSummary?.[chart_number]`.
  - `/fax/chart-summary` rows shape `{chart_number, fax_count, last_sent_at}` matches what the hook reshapes.
  - `/fax/recent` new fields `dob, doc_types, sent_by` consumed verbatim in `FaxLogPane`.
- **Test isolation:** ✓ conftest override (T1) means every test's client skips real auth; implementations populate `sent_by` from the overridden user.
