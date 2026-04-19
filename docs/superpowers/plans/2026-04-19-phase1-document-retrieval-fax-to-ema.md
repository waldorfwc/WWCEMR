# Phase 1 — Document Retrieval & Fax-to-EMA Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship persistent fax tracking, batched sending, and a retry/history UI for patient documents going to ModMed EMA. One chart at a time; user ticks docs, picks a grouping mode, sends, sees status chips that update from a background poller.

**Architecture:** New `FaxLog` table captures every attempt. New `fax_batch.py` router exposes `send-batch`, `recent`, `by-chart`, `retry`, and paginated `fax-log` endpoints; existing `/fax/send` delegates to `send-batch` so backward compatibility holds. An APScheduler job running inside `lifespan` polls RingCentral for final delivery status. Frontend adds a `FaxBatchModal`, per-doc checkboxes + status chips in `PatientChart.jsx`, and a new `/fax-log` page.

**Tech Stack:** FastAPI, SQLAlchemy, SQLite, pytest, `pypdf` (PDF merge), `APScheduler` (poller), `respx` (HTTP mocking in tests). Frontend: React 18, Vite 5, Tailwind 3, React Query 5.

**Reference spec:** `docs/superpowers/specs/2026-04-19-phase1-document-retrieval-fax-to-ema-design.md`

---

## Pre-flight notes

- The project is already a git repo (Phase 0). HEAD at start of this plan is `9f9f62a` (Phase 1 spec commit).
- Backend has pytest infrastructure from Phase 0 (`backend/tests/conftest.py`). Write new tests alongside `test_dashboard.py`.
- Existing `fax_service.send_fax()` and `check_fax_status()` wrap the real RingCentral API via `httpx`. Tests **must mock** these module-level functions via `monkeypatch.setattr("app.routers.fax_batch.send_fax", ...)` — do not hit the real API.
- `app.database.init_db()` hardcodes its model-import list (`backend/app/database.py:23`). When we add `fax_log` and `practice_config` models we must add them to that import line so `Base.metadata.create_all()` sees them.
- `log_action()` from `app.services.audit_service` is the canonical audit helper — use it, don't invent a new pattern.
- The frontend dev server may still be running from earlier; Tailwind config changes require a restart but component/JSX changes hot-reload fine.

---

## File structure (decisions locked in here)

**Backend — new files:**
- `backend/app/models/fax_log.py` — `FaxLog` model + `FaxLogStatus`/`GroupingMode` enums
- `backend/app/models/practice_config.py` — `PracticeConfig` key/value model + helper `get_setting(db, key, default)`
- `backend/app/services/pdf_merge.py` — `merge_pdfs(paths: list[str]) -> tempfile.NamedTemporaryFile` (uses `pypdf`)
- `backend/app/services/fax_poller.py` — APScheduler job + `poll_outstanding_faxes(db)` function (also importable for tests)
- `backend/app/routers/fax_batch.py` — new `fax_batch` router: `send-batch`, `recent`, `by-chart/{chart_number}`, `retry/{id}`, `fax-log`
- `backend/scripts/seed_practice_config.py` — idempotent seeder; inserts `ema_default_fax` and `ema_fax_label` if missing
- `backend/tests/test_fax_models.py`
- `backend/tests/test_fax_send_batch.py`
- `backend/tests/test_fax_recent.py`
- `backend/tests/test_fax_by_chart.py`
- `backend/tests/test_fax_retry.py`
- `backend/tests/test_fax_log_list.py`
- `backend/tests/test_fax_poller.py`

**Backend — modified:**
- `backend/app/database.py:23` — add `fax_log, practice_config` to the import line
- `backend/app/routers/fax.py` — refactor `/send` to call `_send_batch_impl(single-doc payload)`; leave `/status/{message_id}` alone
- `backend/app/main.py` — register `fax_batch.router` + start poller in `lifespan`
- `backend/requirements.txt` — add `pypdf`, `apscheduler`, `respx`

**Frontend — new files:**
- `frontend/src/components/FaxStatusChip.jsx`
- `frontend/src/components/FaxBatchModal.jsx`
- `frontend/src/hooks/useFaxByChart.js`
- `frontend/src/pages/FaxLog.jsx`

**Frontend — modified:**
- `frontend/src/pages/PatientChart.jsx` — checkboxes per doc row, action bar above each list, integrate `FaxBatchModal`, replace the old `FaxModal` component with a thin single-doc wrapper around `FaxBatchModal`
- `frontend/src/App.jsx` — add `/fax-log` route
- `frontend/src/components/layout/TopNav.jsx` — insert "Fax log" link between "Import" and "Audit"
- `frontend/src/utils/api.js` — add `fmt.faxStatus(status)` and `fmt.faxDate(ts)` helpers

---

## Task 1: Add `FaxLog` and `PracticeConfig` models

**Files:**
- Create: `backend/app/models/fax_log.py`
- Create: `backend/app/models/practice_config.py`
- Create: `backend/tests/test_fax_models.py`
- Modify: `backend/app/database.py` (line 23)

- [ ] **Step 1: Write the failing test**

Create `/Users/wwcclaudecode/Documents/wwc-era-project/backend/tests/test_fax_models.py`:

```python
"""Tests for FaxLog and PracticeConfig model creation + basic queries."""
from datetime import datetime
from app.models.fax_log import FaxLog, FaxLogStatus, GroupingMode
from app.models.practice_config import PracticeConfig, get_setting


def test_fax_log_defaults(db):
    row = FaxLog(
        chart_number="12345",
        doc_ids=["11111111-1111-1111-1111-111111111111"],
        grouping_mode=GroupingMode.SEPARATE.value,
        dest_fax="2402522141",
        sent_by="user@example.com",
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    assert row.id is not None
    assert row.status == FaxLogStatus.QUEUED.value
    assert row.sent_at is not None
    assert row.ringcentral_message_id is None
    assert row.retry_of is None


def test_grouping_mode_values():
    assert {m.value for m in GroupingMode} == {"separate", "combined", "by_type"}


def test_fax_log_status_values():
    assert {s.value for s in FaxLogStatus} == {"queued", "sent", "delivered", "failed"}


def test_practice_config_roundtrip(db):
    db.add(PracticeConfig(key="ema_default_fax", value="2402522141"))
    db.commit()
    assert get_setting(db, "ema_default_fax") == "2402522141"
    assert get_setting(db, "missing_key", default="fallback") == "fallback"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_fax_models.py -v 2>&1 | tail -10
```
Expected: ImportError on `app.models.fax_log` and `app.models.practice_config` (those modules don't exist yet).

- [ ] **Step 3: Create `backend/app/models/fax_log.py`**

```python
"""FaxLog model — one row per fax attempt. Persisted for audit, retry, status polling."""
from sqlalchemy import Column, String, DateTime, Text, ForeignKey, JSON, Enum as SAEnum, Index
from datetime import datetime
import enum
from app.database import Base
from app.models.guid import GUID, new_uuid


class FaxLogStatus(str, enum.Enum):
    QUEUED = "queued"
    SENT = "sent"
    DELIVERED = "delivered"
    FAILED = "failed"


class GroupingMode(str, enum.Enum):
    SEPARATE = "separate"
    COMBINED = "combined"
    BY_TYPE = "by_type"


class FaxLog(Base):
    __tablename__ = "fax_logs"

    id = Column(GUID(), primary_key=True, default=new_uuid)
    chart_number = Column(String(20), nullable=False, index=True)
    doc_ids = Column(JSON, nullable=False)  # list of document UUID strings
    grouping_mode = Column(SAEnum(GroupingMode), default=GroupingMode.SEPARATE, nullable=False)
    dest_fax = Column(String(40), nullable=False)

    ringcentral_message_id = Column(String(64), nullable=True, index=True)
    status = Column(SAEnum(FaxLogStatus), default=FaxLogStatus.QUEUED, nullable=False, index=True)
    sent_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_checked_at = Column(DateTime, nullable=True)
    delivered_at = Column(DateTime, nullable=True)
    error = Column(Text, nullable=True)
    sent_by = Column(String(200), nullable=True)

    retry_of = Column(GUID(), ForeignKey("fax_logs.id"), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_fax_chart_sent", "chart_number", "sent_at"),
        Index("ix_fax_status_checked", "status", "last_checked_at"),
    )
```

- [ ] **Step 4: Create `backend/app/models/practice_config.py`**

```python
"""Simple key/value practice-wide settings (ema fax number, labels, etc.)."""
from sqlalchemy import Column, String, DateTime
from sqlalchemy.orm import Session
from datetime import datetime
from typing import Optional
from app.database import Base


class PracticeConfig(Base):
    __tablename__ = "practice_config"

    key = Column(String(80), primary_key=True)
    value = Column(String(500), nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


def get_setting(db: Session, key: str, default: Optional[str] = None) -> Optional[str]:
    row = db.query(PracticeConfig).filter(PracticeConfig.key == key).first()
    return row.value if row else default
```

- [ ] **Step 5: Modify `backend/app/database.py` line 23**

Change:
```python
from app.models import patient, claim, payment, denial, appeal, audit, document, patient_directory, clinical, payment_analysis  # noqa
```
to:
```python
from app.models import patient, claim, payment, denial, appeal, audit, document, patient_directory, clinical, payment_analysis, fax_log, practice_config  # noqa
```

- [ ] **Step 6: Run tests to verify they pass**

Run:
```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_fax_models.py tests/test_dashboard.py -v 2>&1 | tail -15
```
Expected: all 4 new tests PASS, 2 dashboard tests still PASS (regression check).

- [ ] **Step 7: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/models/fax_log.py backend/app/models/practice_config.py backend/app/database.py backend/tests/test_fax_models.py
git commit -m "feat(backend): add FaxLog + PracticeConfig models"
```

---

## Task 2: Install deps + seed practice config

**Files:**
- Modify: `backend/requirements.txt`
- Create: `backend/scripts/seed_practice_config.py`

- [ ] **Step 1: Append deps to `backend/requirements.txt`**

Append these four lines (not present yet):
```
pypdf
apscheduler
respx
```

- [ ] **Step 2: Install**

Run:
```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && pip install pypdf apscheduler respx 2>&1 | tail -5
```
Expected: three Successfully installed lines.

- [ ] **Step 3: Create `backend/scripts/seed_practice_config.py`**

```python
"""Idempotent seed for practice_config: ema_default_fax + ema_fax_label.

Run once after deploy, or whenever adding new settings.
Safe to run repeatedly — existing rows are left alone.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal, init_db
from app.models.practice_config import PracticeConfig


DEFAULTS = {
    "ema_default_fax": "2402522141",
    "ema_fax_label": "ModMed EMA",
}


def main():
    init_db()
    db = SessionLocal()
    try:
        for key, value in DEFAULTS.items():
            existing = db.query(PracticeConfig).filter(PracticeConfig.key == key).first()
            if existing:
                print(f"  [skip] {key} already set to {existing.value!r}")
                continue
            db.add(PracticeConfig(key=key, value=value))
            print(f"  [add]  {key} = {value!r}")
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the seeder against the real DB**

Run:
```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python scripts/seed_practice_config.py
```
Expected:
```
  [add]  ema_default_fax = '2402522141'
  [add]  ema_fax_label = 'ModMed EMA'
```

If run a second time, should print `[skip]` twice. Run it again to confirm idempotency:
```bash
python scripts/seed_practice_config.py
```

- [ ] **Step 5: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/requirements.txt backend/scripts/seed_practice_config.py
git commit -m "chore(backend): add pypdf/apscheduler/respx deps + practice_config seeder"
```

---

## Task 3: `POST /fax/send-batch` — separate mode (core TDD cycle)

**Files:**
- Create: `backend/app/routers/fax_batch.py`
- Create: `backend/tests/test_fax_send_batch.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: Write the failing test**

Create `/Users/wwcclaudecode/Documents/wwc-era-project/backend/tests/test_fax_send_batch.py`:

```python
"""Tests for POST /api/fax/send-batch."""
import pytest
from app.models.document import PatientDocument
from app.models.patient_directory import PatientDirectory
from app.models.fax_log import FaxLog


def _fake_send_fax_ok(to_number, file_path, cover_page_text=None, patient_name=None):
    return {"success": True, "message_id": "rc-msg-123", "status": "Sent",
            "to": to_number, "pages": 1, "error": None}


def _fake_send_fax_fail(to_number, file_path, cover_page_text=None, patient_name=None):
    return {"success": False, "message_id": None, "status": "Failed",
            "to": to_number, "pages": 0, "error": "Invalid fax number"}


def _seed_doc(db, tmp_path, chart_number="12345", name="Adams, Pamella"):
    """Seed a PatientDocument whose file_path points at a writable temp PDF."""
    pdf_path = tmp_path / f"{chart_number}-doc.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%fake content\n%%EOF")
    doc = PatientDocument(
        chart_number=chart_number, doc_type="insurance_card",
        doc_id="D1", filename=pdf_path.name, file_path=str(pdf_path),
    )
    patient = PatientDirectory(chart_number=chart_number, patient_name=name)
    db.add_all([doc, patient])
    db.commit()
    db.refresh(doc)
    return doc


def test_send_batch_separate_one_doc(client, db, tmp_path, monkeypatch):
    doc = _seed_doc(db, tmp_path)
    monkeypatch.setattr("app.routers.fax_batch.send_fax", _fake_send_fax_ok)

    r = client.post("/api/fax/send-batch", json={
        "chart_number": "12345",
        "doc_ids": [str(doc.id)],
        "dest_fax": "2402522141",
        "grouping_mode": "separate",
        "cover_text": "test",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["faxes"]) == 1
    assert body["faxes"][0]["status"] == "sent"
    assert body["faxes"][0]["ringcentral_message_id"] == "rc-msg-123"

    logs = db.query(FaxLog).all()
    assert len(logs) == 1
    assert logs[0].chart_number == "12345"
    assert logs[0].status.value == "sent"
    assert logs[0].dest_fax == "2402522141"


def test_send_batch_separate_multiple_docs_creates_multiple_fax_logs(client, db, tmp_path, monkeypatch):
    doc_a = _seed_doc(db, tmp_path, chart_number="11111")
    doc_b = _seed_doc(db, tmp_path, chart_number="11111")
    monkeypatch.setattr("app.routers.fax_batch.send_fax", _fake_send_fax_ok)

    r = client.post("/api/fax/send-batch", json={
        "chart_number": "11111",
        "doc_ids": [str(doc_a.id), str(doc_b.id)],
        "dest_fax": "2402522141",
        "grouping_mode": "separate",
    })
    assert r.status_code == 200
    assert len(r.json()["faxes"]) == 2
    assert db.query(FaxLog).count() == 2


def test_send_batch_per_fax_failure_does_not_abort_batch(client, db, tmp_path, monkeypatch):
    doc_a = _seed_doc(db, tmp_path, chart_number="22222")
    doc_b = _seed_doc(db, tmp_path, chart_number="22222")

    # First call succeeds, second fails
    calls = {"n": 0}
    def mock(*args, **kwargs):
        calls["n"] += 1
        return _fake_send_fax_ok(*args, **kwargs) if calls["n"] == 1 else _fake_send_fax_fail(*args, **kwargs)

    monkeypatch.setattr("app.routers.fax_batch.send_fax", mock)

    r = client.post("/api/fax/send-batch", json={
        "chart_number": "22222",
        "doc_ids": [str(doc_a.id), str(doc_b.id)],
        "dest_fax": "2402522141",
        "grouping_mode": "separate",
    })
    assert r.status_code == 200
    faxes = r.json()["faxes"]
    statuses = {f["status"] for f in faxes}
    assert statuses == {"sent", "failed"}

    all_logs = db.query(FaxLog).all()
    assert len(all_logs) == 2
    failed = [l for l in all_logs if l.status.value == "failed"]
    assert len(failed) == 1
    assert failed[0].error == "Invalid fax number"


def test_send_batch_rejects_missing_doc(client, db, monkeypatch):
    monkeypatch.setattr("app.routers.fax_batch.send_fax", _fake_send_fax_ok)
    r = client.post("/api/fax/send-batch", json={
        "chart_number": "99999",
        "doc_ids": ["00000000-0000-0000-0000-000000000000"],
        "dest_fax": "2402522141",
        "grouping_mode": "separate",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["faxes"][0]["status"] == "failed"
    assert "not found" in body["faxes"][0]["error"].lower()


def test_send_batch_validates_payload(client, db):
    # Missing dest_fax
    r = client.post("/api/fax/send-batch", json={
        "chart_number": "12345", "doc_ids": ["x"], "grouping_mode": "separate",
    })
    assert r.status_code == 422

    # Empty doc_ids
    r = client.post("/api/fax/send-batch", json={
        "chart_number": "12345", "doc_ids": [], "dest_fax": "2402522141",
        "grouping_mode": "separate",
    })
    assert r.status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_fax_send_batch.py -v 2>&1 | tail -15
```
Expected: all 5 tests FAIL with 404 (`/api/fax/send-batch` doesn't exist yet).

- [ ] **Step 3: Create `backend/app/routers/fax_batch.py`**

```python
"""Fax batch router — send-batch is the core entry; separate mode only in this task.

Combined and by_type modes are added in later tasks.
"""
from typing import Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.document import PatientDocument
from app.models.patient_directory import PatientDirectory
from app.models.fax_log import FaxLog, FaxLogStatus, GroupingMode
from app.services.fax_service import send_fax
from app.services.audit_service import log_action

router = APIRouter(prefix="/fax", tags=["fax-batch"])


class SendBatchPayload(BaseModel):
    chart_number: str
    doc_ids: list[str]
    dest_fax: str
    grouping_mode: str = "separate"
    cover_text: Optional[str] = None


def _patient_name(db: Session, chart_number: str) -> str:
    p = db.query(PatientDirectory).filter(PatientDirectory.chart_number == chart_number).first()
    return p.patient_name if p else chart_number


def _send_one_and_log(
    db: Session,
    chart_number: str,
    dest_fax: str,
    doc_ids: list[str],
    file_path: Optional[str],
    cover_text: Optional[str],
    patient_name: str,
    grouping_mode: str,
    not_found_error: Optional[str] = None,
) -> dict:
    """Create FaxLog row, call RingCentral (unless pre-failed), return payload row dict."""
    log = FaxLog(
        chart_number=chart_number,
        doc_ids=doc_ids,
        grouping_mode=grouping_mode,
        dest_fax=dest_fax,
    )
    db.add(log)
    db.flush()

    if not_found_error:
        log.status = FaxLogStatus.FAILED
        log.error = not_found_error
        db.commit()
        log_action(db, "FAX_FAILED", "fax", resource_id=str(log.id),
                   description=f"Fax failed: {not_found_error}")
        return {"fax_log_id": str(log.id), "doc_ids": doc_ids,
                "status": "failed", "error": not_found_error,
                "ringcentral_message_id": None}

    result = send_fax(
        to_number=dest_fax, file_path=file_path,
        cover_page_text=cover_text, patient_name=patient_name,
    )
    if result.get("error"):
        log.status = FaxLogStatus.FAILED
        log.error = result["error"]
        db.commit()
        log_action(db, "FAX_FAILED", "fax", resource_id=str(log.id),
                   description=f"Fax to {dest_fax} failed: {result['error']}")
        return {"fax_log_id": str(log.id), "doc_ids": doc_ids,
                "status": "failed", "error": result["error"],
                "ringcentral_message_id": None}

    log.status = FaxLogStatus.SENT
    log.ringcentral_message_id = result.get("message_id")
    log.sent_at = datetime.utcnow()
    db.commit()
    log_action(db, "FAX_SENT", "fax", resource_id=str(log.id),
               description=f"Faxed {len(doc_ids)} doc(s) to {dest_fax} — msg {result.get('message_id')}")
    return {"fax_log_id": str(log.id), "doc_ids": doc_ids,
            "status": "sent", "error": None,
            "ringcentral_message_id": result.get("message_id")}


@router.post("/send-batch")
def send_batch(payload: SendBatchPayload, db: Session = Depends(get_db)):
    if not payload.doc_ids:
        raise HTTPException(status_code=400, detail="doc_ids must not be empty")
    if payload.grouping_mode not in {m.value for m in GroupingMode}:
        raise HTTPException(status_code=400, detail=f"Invalid grouping_mode: {payload.grouping_mode}")

    patient_name = _patient_name(db, payload.chart_number)
    mode = payload.grouping_mode

    log_action(db, "FAX_BATCH_SENT", "fax",
               description=f"Batch fax chart={payload.chart_number} docs={len(payload.doc_ids)} mode={mode} to {payload.dest_fax}")

    faxes = []
    if mode == "separate":
        for doc_id in payload.doc_ids:
            doc = db.query(PatientDocument).filter(PatientDocument.id == doc_id).first()
            if not doc:
                faxes.append(_send_one_and_log(
                    db, payload.chart_number, payload.dest_fax, [doc_id],
                    file_path=None, cover_text=payload.cover_text,
                    patient_name=patient_name, grouping_mode=mode,
                    not_found_error=f"Document {doc_id} not found",
                ))
                continue
            faxes.append(_send_one_and_log(
                db, payload.chart_number, payload.dest_fax, [doc_id],
                file_path=doc.file_path, cover_text=payload.cover_text,
                patient_name=patient_name, grouping_mode=mode,
            ))
    else:
        # combined / by_type implemented in Tasks 4 and 5
        raise HTTPException(status_code=501, detail=f"Grouping mode {mode!r} not implemented yet")

    return {"batch_id": None, "faxes": faxes}
```

- [ ] **Step 4: Register the router in `backend/app/main.py`**

Change the router-imports line:
```python
from app.routers import waystar, ar, documents, intake, chart, fax, auth, dashboard
```
to:
```python
from app.routers import waystar, ar, documents, intake, chart, fax, auth, dashboard, fax_batch
```

Add this `include_router` call alongside the others (e.g. right after `dashboard`):
```python
app.include_router(fax_batch.router, prefix="/api")
```

- [ ] **Step 5: Run tests to verify they pass**

Run:
```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_fax_send_batch.py -v 2>&1 | tail -15
```
Expected: all 5 tests PASS.

- [ ] **Step 6: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/routers/fax_batch.py backend/app/main.py backend/tests/test_fax_send_batch.py
git commit -m "feat(backend): POST /fax/send-batch (separate mode) with FaxLog persistence"
```

---

## Task 4: `send-batch` combined mode + pdf_merge service

**Files:**
- Create: `backend/app/services/pdf_merge.py`
- Modify: `backend/app/routers/fax_batch.py`
- Append tests: `backend/tests/test_fax_send_batch.py`

- [ ] **Step 1: Write the failing tests**

Append to `/Users/wwcclaudecode/Documents/wwc-era-project/backend/tests/test_fax_send_batch.py`:

```python
def _write_pdf(path, body=b"%PDF-1.4\n%content\n%%EOF"):
    path.write_bytes(body)
    return path


def test_send_batch_combined_merges_into_one_fax(client, db, tmp_path, monkeypatch):
    doc_a = _seed_doc(db, tmp_path, chart_number="33333")
    doc_b = _seed_doc(db, tmp_path, chart_number="33333")

    calls = []
    def mock(to_number, file_path, cover_page_text=None, patient_name=None):
        calls.append(file_path)
        return {"success": True, "message_id": f"msg-{len(calls)}",
                "status": "Sent", "to": to_number, "pages": 2, "error": None}
    monkeypatch.setattr("app.routers.fax_batch.send_fax", mock)

    # Make the seeded docs valid single-page PDFs so pypdf can open them.
    # Use a minimal-but-valid one-page PDF for both.
    from pypdf import PdfWriter
    for d in (doc_a, doc_b):
        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        with open(d.file_path, "wb") as f:
            writer.write(f)

    r = client.post("/api/fax/send-batch", json={
        "chart_number": "33333",
        "doc_ids": [str(doc_a.id), str(doc_b.id)],
        "dest_fax": "2402522141",
        "grouping_mode": "combined",
    })
    assert r.status_code == 200, r.text
    faxes = r.json()["faxes"]
    assert len(faxes) == 1
    assert faxes[0]["status"] == "sent"
    assert set(faxes[0]["doc_ids"]) == {str(doc_a.id), str(doc_b.id)}
    # send_fax called exactly once with the merged PDF path
    assert len(calls) == 1
    assert calls[0].endswith(".pdf")


def test_send_batch_combined_reports_single_failure(client, db, tmp_path, monkeypatch):
    doc_a = _seed_doc(db, tmp_path, chart_number="44444")
    from pypdf import PdfWriter
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    with open(doc_a.file_path, "wb") as f:
        writer.write(f)

    monkeypatch.setattr("app.routers.fax_batch.send_fax", _fake_send_fax_fail)

    r = client.post("/api/fax/send-batch", json={
        "chart_number": "44444",
        "doc_ids": [str(doc_a.id)],
        "dest_fax": "2402522141",
        "grouping_mode": "combined",
    })
    assert r.status_code == 200
    assert r.json()["faxes"][0]["status"] == "failed"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_fax_send_batch.py -v -k combined 2>&1 | tail -10
```
Expected: both tests FAIL with 501 `grouping_mode 'combined' not implemented yet`.

- [ ] **Step 3: Create `backend/app/services/pdf_merge.py`**

```python
"""Concat PDFs into a single temp file. Caller owns deletion (use a finally)."""
import os
import tempfile
from pypdf import PdfWriter, PdfReader


def merge_pdfs(paths: list[str]) -> str:
    """Merge the PDFs at `paths` into a single temp PDF. Returns the temp path.

    Raises FileNotFoundError if any input is missing.
    Raises ValueError if an input isn't a readable PDF.
    Caller is responsible for os.unlink(path) when done.
    """
    for p in paths:
        if not os.path.isfile(p):
            raise FileNotFoundError(p)

    writer = PdfWriter()
    for p in paths:
        try:
            reader = PdfReader(p)
        except Exception as e:
            raise ValueError(f"Failed to read PDF {p}: {e}")
        for page in reader.pages:
            writer.add_page(page)

    fd, out_path = tempfile.mkstemp(suffix=".pdf", prefix="fax-merged-")
    try:
        with os.fdopen(fd, "wb") as f:
            writer.write(f)
    except Exception:
        os.unlink(out_path)
        raise
    return out_path
```

- [ ] **Step 4: Wire combined mode into `fax_batch.py`**

In `backend/app/routers/fax_batch.py`, add this import at the top:
```python
import os
from app.services.pdf_merge import merge_pdfs
```

Replace the `else: raise HTTPException(...)` block at the end of `send_batch` with:

```python
    elif mode == "combined":
        # Validate every doc exists first.
        docs = []
        missing = []
        for doc_id in payload.doc_ids:
            doc = db.query(PatientDocument).filter(PatientDocument.id == doc_id).first()
            if not doc:
                missing.append(doc_id)
            else:
                docs.append(doc)

        if missing:
            # Record one failed batch and return
            faxes.append(_send_one_and_log(
                db, payload.chart_number, payload.dest_fax, list(payload.doc_ids),
                file_path=None, cover_text=payload.cover_text,
                patient_name=patient_name, grouping_mode=mode,
                not_found_error=f"Documents not found: {', '.join(missing)}",
            ))
            return {"batch_id": None, "faxes": faxes}

        merged_path = None
        try:
            merged_path = merge_pdfs([d.file_path for d in docs])
            faxes.append(_send_one_and_log(
                db, payload.chart_number, payload.dest_fax,
                [str(d.id) for d in docs],
                file_path=merged_path, cover_text=payload.cover_text,
                patient_name=patient_name, grouping_mode=mode,
            ))
        except (FileNotFoundError, ValueError) as e:
            faxes.append(_send_one_and_log(
                db, payload.chart_number, payload.dest_fax,
                [str(d.id) for d in docs],
                file_path=None, cover_text=payload.cover_text,
                patient_name=patient_name, grouping_mode=mode,
                not_found_error=f"PDF merge failed: {e}",
            ))
        finally:
            if merged_path and os.path.isfile(merged_path):
                os.unlink(merged_path)
    else:
        # by_type implemented in Task 5
        raise HTTPException(status_code=501, detail=f"Grouping mode {mode!r} not implemented yet")
```

- [ ] **Step 5: Run tests to verify they pass**

Run:
```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_fax_send_batch.py -v 2>&1 | tail -15
```
Expected: all 7 tests PASS (5 from Task 3 + 2 new combined tests).

- [ ] **Step 6: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/services/pdf_merge.py backend/app/routers/fax_batch.py backend/tests/test_fax_send_batch.py
git commit -m "feat(backend): fax/send-batch combined mode via pypdf merge"
```

---

## Task 5: `send-batch` by_type mode

**Files:**
- Modify: `backend/app/routers/fax_batch.py`
- Append tests: `backend/tests/test_fax_send_batch.py`

- [ ] **Step 1: Write the failing test**

Append to `/Users/wwcclaudecode/Documents/wwc-era-project/backend/tests/test_fax_send_batch.py`:

```python
def _seed_doc_type(db, tmp_path, chart_number, doc_type, idx=0):
    pdf_path = tmp_path / f"{chart_number}-{doc_type}-{idx}.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%content\n%%EOF")
    from pypdf import PdfWriter
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    with open(pdf_path, "wb") as f:
        writer.write(f)
    doc = PatientDocument(
        chart_number=chart_number, doc_type=doc_type,
        doc_id=f"D{idx}", filename=pdf_path.name, file_path=str(pdf_path),
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


def test_send_batch_by_type_groups_docs(client, db, tmp_path, monkeypatch):
    db.add(PatientDirectory(chart_number="55555", patient_name="Nguyen, Mai"))
    db.commit()

    card_a = _seed_doc_type(db, tmp_path, "55555", "insurance_card", 0)
    card_b = _seed_doc_type(db, tmp_path, "55555", "insurance_card", 1)
    note_a = _seed_doc_type(db, tmp_path, "55555", "office_visit_note", 0)

    calls = []
    def mock(to_number, file_path, cover_page_text=None, patient_name=None):
        calls.append((file_path, cover_page_text))
        return {"success": True, "message_id": f"msg-{len(calls)}",
                "status": "Sent", "to": to_number, "pages": 1, "error": None}
    monkeypatch.setattr("app.routers.fax_batch.send_fax", mock)

    r = client.post("/api/fax/send-batch", json={
        "chart_number": "55555",
        "doc_ids": [str(card_a.id), str(card_b.id), str(note_a.id)],
        "dest_fax": "2402522141",
        "grouping_mode": "by_type",
    })
    assert r.status_code == 200, r.text
    faxes = r.json()["faxes"]
    assert len(faxes) == 2  # one per doc_type
    # Each group's doc_ids are the ones matching its type
    sizes = sorted(len(f["doc_ids"]) for f in faxes)
    assert sizes == [1, 2]
    assert len(calls) == 2
```

- [ ] **Step 2: Run to verify it fails**

Run:
```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_fax_send_batch.py::test_send_batch_by_type_groups_docs -v 2>&1 | tail -10
```
Expected: FAIL with 501 `grouping_mode 'by_type' not implemented yet`.

- [ ] **Step 3: Implement by_type in `fax_batch.py`**

Replace the final `else: ... 501` block with:

```python
    elif mode == "by_type":
        # Group loaded docs by their doc_type, merge each group, send one fax per group.
        loaded = []
        missing = []
        for doc_id in payload.doc_ids:
            doc = db.query(PatientDocument).filter(PatientDocument.id == doc_id).first()
            if doc is None:
                missing.append(doc_id)
            else:
                loaded.append(doc)

        if missing:
            faxes.append(_send_one_and_log(
                db, payload.chart_number, payload.dest_fax, list(payload.doc_ids),
                file_path=None, cover_text=payload.cover_text,
                patient_name=patient_name, grouping_mode=mode,
                not_found_error=f"Documents not found: {', '.join(missing)}",
            ))
            return {"batch_id": None, "faxes": faxes}

        groups: dict[str, list[PatientDocument]] = {}
        for doc in loaded:
            groups.setdefault(doc.doc_type, []).append(doc)

        for doc_type, group in groups.items():
            merged_path = None
            try:
                if len(group) == 1:
                    # No merge needed; send the single file directly
                    faxes.append(_send_one_and_log(
                        db, payload.chart_number, payload.dest_fax,
                        [str(group[0].id)],
                        file_path=group[0].file_path, cover_text=payload.cover_text,
                        patient_name=patient_name, grouping_mode=mode,
                    ))
                    continue

                merged_path = merge_pdfs([d.file_path for d in group])
                faxes.append(_send_one_and_log(
                    db, payload.chart_number, payload.dest_fax,
                    [str(d.id) for d in group],
                    file_path=merged_path, cover_text=payload.cover_text,
                    patient_name=patient_name, grouping_mode=mode,
                ))
            except (FileNotFoundError, ValueError) as e:
                faxes.append(_send_one_and_log(
                    db, payload.chart_number, payload.dest_fax,
                    [str(d.id) for d in group],
                    file_path=None, cover_text=payload.cover_text,
                    patient_name=patient_name, grouping_mode=mode,
                    not_found_error=f"PDF merge failed for doc_type={doc_type}: {e}",
                ))
            finally:
                if merged_path and os.path.isfile(merged_path):
                    os.unlink(merged_path)
```

(There is no `else` block after this — `by_type` is the last mode. Remove the 501 branch entirely.)

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_fax_send_batch.py -v 2>&1 | tail -15
```
Expected: 8 tests PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/routers/fax_batch.py backend/tests/test_fax_send_batch.py
git commit -m "feat(backend): fax/send-batch by_type mode"
```

---

## Task 6: Refactor old `POST /fax/send` to delegate to send-batch

**Files:**
- Modify: `backend/app/routers/fax.py`
- Create: `backend/tests/test_fax_send_compat.py`

- [ ] **Step 1: Write the failing backward-compat test**

Create `/Users/wwcclaudecode/Documents/wwc-era-project/backend/tests/test_fax_send_compat.py`:

```python
"""Backward-compat: old /fax/send must still work and also create a FaxLog row."""
from app.models.document import PatientDocument
from app.models.patient_directory import PatientDirectory
from app.models.fax_log import FaxLog


def _ok(to_number, file_path, cover_page_text=None, patient_name=None):
    return {"success": True, "message_id": "rc-compat-1", "status": "Sent",
            "to": to_number, "pages": 1, "error": None}


def test_legacy_fax_send_creates_fax_log(client, db, tmp_path, monkeypatch):
    pdf = tmp_path / "c.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%c\n%%EOF")
    doc = PatientDocument(
        chart_number="66666", doc_type="insurance_card",
        doc_id="D9", filename="c.pdf", file_path=str(pdf),
    )
    db.add_all([doc, PatientDirectory(chart_number="66666", patient_name="Compat, Case")])
    db.commit()
    db.refresh(doc)

    monkeypatch.setattr("app.routers.fax_batch.send_fax", _ok)

    r = client.post("/api/fax/send", json={
        "fax_number": "2402522141",
        "doc_type": "document",
        "doc_id": str(doc.id),
        "cover_text": "test",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    # Legacy response shape preserved
    assert body["success"] is True
    assert body["message_id"] == "rc-compat-1"

    # But a FaxLog row was also written
    logs = db.query(FaxLog).all()
    assert len(logs) == 1
    assert logs[0].chart_number == "66666"
    assert logs[0].status.value == "sent"
```

- [ ] **Step 2: Run to verify it fails**

Run:
```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_fax_send_compat.py -v 2>&1 | tail -10
```
Expected: the test runs but fails on the `db.query(FaxLog).all()` assertion — the old `/fax/send` doesn't write a FaxLog row yet.

- [ ] **Step 3: Refactor `backend/app/routers/fax.py`**

Replace the entire body of the `fax_document` function with this. Keep the `/status/{message_id}` endpoint unchanged.

```python
@router.post("/send")
def fax_document(payload: dict, db: Session = Depends(get_db)):
    """
    Legacy single-doc fax. Delegates to the new send-batch path so every
    send is tracked in fax_logs.
    Body: { fax_number, doc_type ("document" or "intake"), doc_id, cover_text? }
    """
    fax_number = payload.get("fax_number", "").strip()
    doc_type = payload.get("doc_type", "document")
    doc_id = payload.get("doc_id", "")
    cover_text = payload.get("cover_text", "")

    if not fax_number:
        raise HTTPException(status_code=400, detail="fax_number is required")
    if not doc_id:
        raise HTTPException(status_code=400, detail="doc_id is required")

    if doc_type == "intake":
        # Intake docs aren't keyed by chart_number; keep legacy behavior — direct send,
        # no FaxLog row (FaxLog is scoped to PatientDocument-based chart flows).
        intake_doc = db.query(IntakeDocument).filter(IntakeDocument.id == doc_id).first()
        if not intake_doc:
            raise HTTPException(status_code=404, detail="Intake document not found")
        result = send_fax(
            to_number=fax_number, file_path=intake_doc.file_path,
            cover_page_text=cover_text,
            patient_name=intake_doc.patient_name_raw,
        )
        if result.get("error"):
            log_action(db, "FAX_FAILED", "fax",
                       description=f"Intake fax failed to {fax_number}: {result['error']}")
            raise HTTPException(status_code=500, detail=result["error"])
        log_action(db, "FAX_SENT", "fax",
                   description=f"Faxed intake to {fax_number} for {intake_doc.patient_name_raw} — msg {result.get('message_id')}")
        return result

    # Patient-doc path delegates to the batch endpoint.
    doc = db.query(PatientDocument).filter(PatientDocument.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    from app.routers.fax_batch import send_batch, SendBatchPayload
    batch_result = send_batch(
        SendBatchPayload(
            chart_number=doc.chart_number,
            doc_ids=[str(doc.id)],
            dest_fax=fax_number,
            grouping_mode="separate",
            cover_text=cover_text or None,
        ),
        db=db,
    )
    fax = batch_result["faxes"][0]
    if fax["status"] == "failed":
        raise HTTPException(status_code=500, detail=fax["error"])
    return {
        "success": True,
        "message_id": fax["ringcentral_message_id"],
        "status": "Sent",
        "to": fax_number,
        "pages": None,
        "error": None,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_fax_send_compat.py tests/test_fax_send_batch.py -v 2>&1 | tail -15
```
Expected: compat test PASS + all 8 send_batch tests still PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/routers/fax.py backend/tests/test_fax_send_compat.py
git commit -m "feat(backend): route legacy /fax/send through batch path for FaxLog tracking"
```

---

## Task 7: `GET /fax/recent` — fixes the Dashboard 404

**Files:**
- Modify: `backend/app/routers/fax_batch.py`
- Create: `backend/tests/test_fax_recent.py`

- [ ] **Step 1: Write the failing test**

Create `/Users/wwcclaudecode/Documents/wwc-era-project/backend/tests/test_fax_recent.py`:

```python
"""GET /api/fax/recent — recent fax activity for the Dashboard card."""
from datetime import datetime, timedelta
from app.models.fax_log import FaxLog, FaxLogStatus, GroupingMode
from app.models.patient_directory import PatientDirectory


def test_recent_returns_latest_first(client, db):
    db.add(PatientDirectory(chart_number="77777", patient_name="Adams, Pamella"))
    db.add(PatientDirectory(chart_number="88888", patient_name="Carter, Janice"))
    db.commit()

    older = FaxLog(
        chart_number="77777", doc_ids=["a"], grouping_mode=GroupingMode.SEPARATE,
        dest_fax="2402522141", status=FaxLogStatus.DELIVERED,
        sent_at=datetime.utcnow() - timedelta(hours=3),
    )
    newer = FaxLog(
        chart_number="88888", doc_ids=["b", "c"], grouping_mode=GroupingMode.COMBINED,
        dest_fax="2402522141", status=FaxLogStatus.SENT,
        sent_at=datetime.utcnow() - timedelta(minutes=5),
    )
    db.add_all([older, newer])
    db.commit()

    r = client.get("/api/fax/recent?limit=5")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    assert len(body) == 2
    # newer first
    assert body[0]["chart_number"] == "88888"
    assert body[0]["patient_name"] == "Carter, Janice"
    assert body[0]["doc_count"] == 2
    assert body[0]["status"] == "sent"
    assert body[1]["chart_number"] == "77777"
    assert body[1]["doc_count"] == 1


def test_recent_defaults_to_5_respects_limit(client, db):
    for i in range(7):
        db.add(FaxLog(
            chart_number=f"C{i}", doc_ids=["x"], grouping_mode=GroupingMode.SEPARATE,
            dest_fax="1", status=FaxLogStatus.SENT,
            sent_at=datetime.utcnow() - timedelta(minutes=i),
        ))
    db.commit()

    assert len(client.get("/api/fax/recent").json()) == 5
    assert len(client.get("/api/fax/recent?limit=3").json()) == 3
    assert len(client.get("/api/fax/recent?limit=100").json()) == 7


def test_recent_handles_missing_patient(client, db):
    db.add(FaxLog(
        chart_number="UNKNOWN", doc_ids=["z"], grouping_mode=GroupingMode.SEPARATE,
        dest_fax="1", status=FaxLogStatus.SENT, sent_at=datetime.utcnow(),
    ))
    db.commit()
    r = client.get("/api/fax/recent")
    assert r.status_code == 200
    assert r.json()[0]["patient_name"] == "UNKNOWN"  # falls back to chart number
```

- [ ] **Step 2: Run to verify it fails**

Run:
```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_fax_recent.py -v 2>&1 | tail -10
```
Expected: 404 on `/api/fax/recent`.

- [ ] **Step 3: Add endpoint to `backend/app/routers/fax_batch.py`**

Append below the existing `send_batch` route:

```python
@router.get("/recent")
def fax_recent(limit: int = 5, db: Session = Depends(get_db)):
    """Recent fax activity for the Dashboard card."""
    rows = (
        db.query(FaxLog)
        .order_by(FaxLog.sent_at.desc())
        .limit(max(1, min(limit, 100)))
        .all()
    )
    if not rows:
        return []

    charts = {r.chart_number for r in rows}
    patients = {
        p.chart_number: p.patient_name
        for p in db.query(PatientDirectory)
        .filter(PatientDirectory.chart_number.in_(charts))
        .all()
    }

    def row_to_dict(r: FaxLog) -> dict:
        return {
            "id": str(r.id),
            "chart_number": r.chart_number,
            "patient_name": patients.get(r.chart_number, r.chart_number),
            "status": r.status.value if hasattr(r.status, "value") else r.status,
            "sent_at": r.sent_at.isoformat() + "Z" if r.sent_at else None,
            "dest_fax": r.dest_fax,
            "doc_count": len(r.doc_ids or []),
        }

    return [row_to_dict(r) for r in rows]
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_fax_recent.py -v 2>&1 | tail -10
```
Expected: 3 tests PASS.

- [ ] **Step 5: Smoke-test with Dashboard (manual)**

Run the backend (`uvicorn`) and hit `http://localhost:3000/` — the "Recent faxes to EMA" card should no longer show the 404 error in the browser console. It may render empty if there's no FaxLog data yet, which is correct.

- [ ] **Step 6: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/routers/fax_batch.py backend/tests/test_fax_recent.py
git commit -m "feat(backend): GET /fax/recent for Dashboard recent-faxes card"
```

---

## Task 8: `GET /fax/by-chart/{chart_number}`

**Files:**
- Modify: `backend/app/routers/fax_batch.py`
- Create: `backend/tests/test_fax_by_chart.py`

- [ ] **Step 1: Write the failing test**

Create `/Users/wwcclaudecode/Documents/wwc-era-project/backend/tests/test_fax_by_chart.py`:

```python
"""GET /api/fax/by-chart/{chart_number} — fax history for the chart view chips."""
from datetime import datetime, timedelta
from app.models.fax_log import FaxLog, FaxLogStatus, GroupingMode


def test_by_chart_returns_rows_for_that_chart_only(client, db):
    db.add_all([
        FaxLog(chart_number="AAAA", doc_ids=["d1"], grouping_mode=GroupingMode.SEPARATE,
               dest_fax="1", status=FaxLogStatus.SENT,
               sent_at=datetime.utcnow() - timedelta(minutes=1)),
        FaxLog(chart_number="AAAA", doc_ids=["d2", "d3"], grouping_mode=GroupingMode.COMBINED,
               dest_fax="1", status=FaxLogStatus.DELIVERED,
               sent_at=datetime.utcnow() - timedelta(minutes=2)),
        FaxLog(chart_number="BBBB", doc_ids=["d4"], grouping_mode=GroupingMode.SEPARATE,
               dest_fax="1", status=FaxLogStatus.SENT,
               sent_at=datetime.utcnow()),
    ])
    db.commit()

    r = client.get("/api/fax/by-chart/AAAA")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 2
    assert {row["chart_number"] for row in rows} == {"AAAA"}

    # Shape check
    first = rows[0]
    assert set(first.keys()) >= {"id", "doc_ids", "status", "sent_at",
                                  "dest_fax", "grouping_mode", "error"}


def test_by_chart_empty_returns_empty_list(client, db):
    r = client.get("/api/fax/by-chart/NOPE")
    assert r.status_code == 200
    assert r.json() == []
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_fax_by_chart.py -v 2>&1 | tail -10
```
Expected: 404.

- [ ] **Step 3: Add endpoint to `backend/app/routers/fax_batch.py`**

Append:

```python
@router.get("/by-chart/{chart_number}")
def fax_by_chart(chart_number: str, db: Session = Depends(get_db)):
    """Every fax attempt for a single chart, newest first. Used by the chart-view chips."""
    rows = (
        db.query(FaxLog)
        .filter(FaxLog.chart_number == chart_number)
        .order_by(FaxLog.sent_at.desc())
        .all()
    )
    return [{
        "id": str(r.id),
        "chart_number": r.chart_number,
        "doc_ids": r.doc_ids or [],
        "grouping_mode": r.grouping_mode.value if hasattr(r.grouping_mode, "value") else r.grouping_mode,
        "dest_fax": r.dest_fax,
        "status": r.status.value if hasattr(r.status, "value") else r.status,
        "sent_at": r.sent_at.isoformat() + "Z" if r.sent_at else None,
        "delivered_at": r.delivered_at.isoformat() + "Z" if r.delivered_at else None,
        "error": r.error,
        "ringcentral_message_id": r.ringcentral_message_id,
    } for r in rows]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_fax_by_chart.py -v 2>&1 | tail -8
```
Expected: 2 tests PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/routers/fax_batch.py backend/tests/test_fax_by_chart.py
git commit -m "feat(backend): GET /fax/by-chart/{chart_number} for chart-view status chips"
```

---

## Task 9: `POST /fax/retry/{fax_log_id}`

**Files:**
- Modify: `backend/app/routers/fax_batch.py`
- Create: `backend/tests/test_fax_retry.py`

- [ ] **Step 1: Write the failing test**

Create `/Users/wwcclaudecode/Documents/wwc-era-project/backend/tests/test_fax_retry.py`:

```python
"""POST /api/fax/retry/{fax_log_id} — resend a failed fax, link to original via retry_of."""
from app.models.document import PatientDocument
from app.models.patient_directory import PatientDirectory
from app.models.fax_log import FaxLog, FaxLogStatus, GroupingMode


def _ok(to_number, file_path, cover_page_text=None, patient_name=None):
    return {"success": True, "message_id": "retry-msg-1", "status": "Sent",
            "to": to_number, "pages": 1, "error": None}


def test_retry_resends_failed_fax_and_links(client, db, tmp_path, monkeypatch):
    pdf = tmp_path / "r.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%r\n%%EOF")
    doc = PatientDocument(
        chart_number="77777", doc_type="insurance_card",
        doc_id="D7", filename="r.pdf", file_path=str(pdf),
    )
    db.add_all([doc, PatientDirectory(chart_number="77777", patient_name="Retry, Case")])
    db.commit()
    db.refresh(doc)

    failed = FaxLog(
        chart_number="77777", doc_ids=[str(doc.id)],
        grouping_mode=GroupingMode.SEPARATE, dest_fax="2402522141",
        status=FaxLogStatus.FAILED, error="prev error",
    )
    db.add(failed)
    db.commit()
    db.refresh(failed)

    monkeypatch.setattr("app.routers.fax_batch.send_fax", _ok)
    r = client.post(f"/api/fax/retry/{failed.id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["faxes"][0]["status"] == "sent"

    all_logs = db.query(FaxLog).order_by(FaxLog.sent_at).all()
    assert len(all_logs) == 2
    new_log = [l for l in all_logs if l.status == FaxLogStatus.SENT][0]
    assert str(new_log.retry_of) == str(failed.id)


def test_retry_404_on_missing(client, db):
    r = client.post("/api/fax/retry/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_fax_retry.py -v 2>&1 | tail -10
```
Expected: 404 on the retry endpoint (not implemented).

- [ ] **Step 3: Add endpoint to `backend/app/routers/fax_batch.py`**

Append:

```python
@router.post("/retry/{fax_log_id}")
def fax_retry(fax_log_id: str, db: Session = Depends(get_db)):
    """Resend a fax with the same doc_ids / dest / grouping as the original.
    Creates a new FaxLog row that points back to the original via retry_of.
    """
    original = db.query(FaxLog).filter(FaxLog.id == fax_log_id).first()
    if not original:
        raise HTTPException(status_code=404, detail="Fax log not found")

    mode = original.grouping_mode.value if hasattr(original.grouping_mode, "value") else original.grouping_mode
    batch = send_batch(
        SendBatchPayload(
            chart_number=original.chart_number,
            doc_ids=list(original.doc_ids or []),
            dest_fax=original.dest_fax,
            grouping_mode=mode,
            cover_text=None,  # cover text isn't persisted; retry regenerates
        ),
        db=db,
    )
    # Link every new FaxLog in the batch to the original
    for fax in batch["faxes"]:
        new_id = fax.get("fax_log_id")
        if new_id:
            new_log = db.query(FaxLog).filter(FaxLog.id == new_id).first()
            if new_log:
                new_log.retry_of = original.id
    db.commit()
    return batch
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_fax_retry.py -v 2>&1 | tail -8
```
Expected: 2 tests PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/routers/fax_batch.py backend/tests/test_fax_retry.py
git commit -m "feat(backend): POST /fax/retry/{id} links retry to original via retry_of"
```

---

## Task 10: `GET /fax-log` paginated listing

**Files:**
- Modify: `backend/app/routers/fax_batch.py`
- Create: `backend/tests/test_fax_log_list.py`

- [ ] **Step 1: Write the failing test**

Create `/Users/wwcclaudecode/Documents/wwc-era-project/backend/tests/test_fax_log_list.py`:

```python
"""GET /api/fax-log — paginated fax listing with filters."""
from datetime import datetime, timedelta
from app.models.fax_log import FaxLog, FaxLogStatus, GroupingMode
from app.models.patient_directory import PatientDirectory


def test_fax_log_pagination_filters(client, db):
    db.add(PatientDirectory(chart_number="ZZ", patient_name="Z, P"))
    db.commit()
    base = datetime.utcnow()
    for i in range(12):
        db.add(FaxLog(
            chart_number="ZZ",
            doc_ids=["x"],
            grouping_mode=GroupingMode.SEPARATE,
            dest_fax="2402522141",
            status=FaxLogStatus.SENT if i % 2 == 0 else FaxLogStatus.FAILED,
            sent_at=base - timedelta(minutes=i),
        ))
    db.commit()

    # default page size 50 but cap at total
    r1 = client.get("/api/fax-log")
    body1 = r1.json()
    assert body1["total"] == 12
    assert len(body1["rows"]) == 12
    assert body1["page"] == 1

    # page size and paging
    r2 = client.get("/api/fax-log?page_size=5&page=2")
    body2 = r2.json()
    assert len(body2["rows"]) == 5
    assert body2["page"] == 2
    assert body2["total"] == 12

    # filter by status
    r3 = client.get("/api/fax-log?status=failed")
    assert all(r["status"] == "failed" for r in r3.json()["rows"])
    assert r3.json()["total"] == 6

    # filter by chart
    r4 = client.get("/api/fax-log?chart=ZZ")
    assert r4.json()["total"] == 12
    r5 = client.get("/api/fax-log?chart=NOPE")
    assert r5.json()["total"] == 0
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_fax_log_list.py -v 2>&1 | tail -8
```
Expected: 404.

- [ ] **Step 3: Add the endpoint**

IMPORTANT: this endpoint uses the path prefix `/fax-log`, NOT `/fax/...`. Add a second APIRouter at the top of `backend/app/routers/fax_batch.py`:

```python
log_router = APIRouter(prefix="/fax-log", tags=["fax-log"])
```

Then append (use `log_router`, not `router`):

```python
@log_router.get("")
def fax_log_list(
    status: Optional[str] = None,
    chart: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db),
):
    page = max(1, page)
    page_size = max(1, min(page_size, 200))

    q = db.query(FaxLog)
    if status:
        q = q.filter(FaxLog.status == status)
    if chart:
        q = q.filter(FaxLog.chart_number == chart)
    if date_from:
        q = q.filter(FaxLog.sent_at >= date_from)
    if date_to:
        q = q.filter(FaxLog.sent_at <= date_to)

    total = q.count()
    rows = (
        q.order_by(FaxLog.sent_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    charts = {r.chart_number for r in rows}
    patients = {
        p.chart_number: p.patient_name
        for p in db.query(PatientDirectory)
        .filter(PatientDirectory.chart_number.in_(charts))
        .all()
    } if charts else {}

    def serialize(r: FaxLog) -> dict:
        return {
            "id": str(r.id),
            "chart_number": r.chart_number,
            "patient_name": patients.get(r.chart_number, r.chart_number),
            "doc_count": len(r.doc_ids or []),
            "grouping_mode": r.grouping_mode.value if hasattr(r.grouping_mode, "value") else r.grouping_mode,
            "dest_fax": r.dest_fax,
            "status": r.status.value if hasattr(r.status, "value") else r.status,
            "sent_at": r.sent_at.isoformat() + "Z" if r.sent_at else None,
            "delivered_at": r.delivered_at.isoformat() + "Z" if r.delivered_at else None,
            "error": r.error,
        }

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "rows": [serialize(r) for r in rows],
    }
```

- [ ] **Step 4: Register `log_router` in `backend/app/main.py`**

Find the line:
```python
app.include_router(fax_batch.router, prefix="/api")
```
and add directly below:
```python
app.include_router(fax_batch.log_router, prefix="/api")
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_fax_log_list.py -v 2>&1 | tail -8
```
Expected: 1 test PASS (4 assertions inside it).

- [ ] **Step 6: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/routers/fax_batch.py backend/app/main.py backend/tests/test_fax_log_list.py
git commit -m "feat(backend): GET /fax-log paginated listing with status/chart filters"
```

---

## Task 11: Background poller for delivery status

**Files:**
- Create: `backend/app/services/fax_poller.py`
- Create: `backend/tests/test_fax_poller.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: Write the failing test**

Create `/Users/wwcclaudecode/Documents/wwc-era-project/backend/tests/test_fax_poller.py`:

```python
"""Tests for the fax status poller — state transitions based on RingCentral status."""
from datetime import datetime, timedelta
from app.models.fax_log import FaxLog, FaxLogStatus, GroupingMode
from app.services.fax_poller import poll_outstanding_faxes


def test_poll_promotes_sent_to_delivered(db, monkeypatch):
    row = FaxLog(
        chart_number="PP1", doc_ids=["x"], grouping_mode=GroupingMode.SEPARATE,
        dest_fax="1", status=FaxLogStatus.SENT, ringcentral_message_id="rc-1",
        sent_at=datetime.utcnow() - timedelta(minutes=5),
    )
    db.add(row); db.commit(); db.refresh(row)

    def fake_check_fax_status(message_id):
        return {"status": "Sent"}  # RingCentral's final "Sent" = our "delivered"

    monkeypatch.setattr("app.services.fax_poller.check_fax_status", fake_check_fax_status)
    n = poll_outstanding_faxes(db)
    db.refresh(row)
    assert n >= 1
    assert row.status == FaxLogStatus.DELIVERED
    assert row.delivered_at is not None


def test_poll_marks_failed(db, monkeypatch):
    row = FaxLog(
        chart_number="PP2", doc_ids=["y"], grouping_mode=GroupingMode.SEPARATE,
        dest_fax="1", status=FaxLogStatus.SENT, ringcentral_message_id="rc-2",
        sent_at=datetime.utcnow() - timedelta(minutes=5),
    )
    db.add(row); db.commit(); db.refresh(row)
    monkeypatch.setattr("app.services.fax_poller.check_fax_status",
                        lambda mid: {"status": "SendingFailed", "error": "no answer"})
    poll_outstanding_faxes(db)
    db.refresh(row)
    assert row.status == FaxLogStatus.FAILED
    assert "no answer" in (row.error or "")


def test_poll_skips_terminal_rows(db, monkeypatch):
    row = FaxLog(
        chart_number="PP3", doc_ids=["z"], grouping_mode=GroupingMode.SEPARATE,
        dest_fax="1", status=FaxLogStatus.DELIVERED, ringcentral_message_id="rc-3",
        sent_at=datetime.utcnow() - timedelta(minutes=5),
        delivered_at=datetime.utcnow() - timedelta(minutes=4),
    )
    db.add(row); db.commit()

    called = {"n": 0}
    def never(mid):
        called["n"] += 1
        return {"status": "Sent"}
    monkeypatch.setattr("app.services.fax_poller.check_fax_status", never)
    poll_outstanding_faxes(db)
    assert called["n"] == 0


def test_poll_skips_rows_past_max_age(db, monkeypatch):
    row = FaxLog(
        chart_number="PP4", doc_ids=["z"], grouping_mode=GroupingMode.SEPARATE,
        dest_fax="1", status=FaxLogStatus.SENT, ringcentral_message_id="rc-4",
        sent_at=datetime.utcnow() - timedelta(hours=3),  # older than default 1h window
    )
    db.add(row); db.commit()

    called = {"n": 0}
    def never(mid):
        called["n"] += 1
        return {"status": "Sent"}
    monkeypatch.setattr("app.services.fax_poller.check_fax_status", never)
    poll_outstanding_faxes(db)
    assert called["n"] == 0
```

- [ ] **Step 2: Run to verify failure**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_fax_poller.py -v 2>&1 | tail -10
```
Expected: ImportError on `app.services.fax_poller`.

- [ ] **Step 3: Create `backend/app/services/fax_poller.py`**

```python
"""Poll RingCentral for outstanding fax statuses and update FaxLog rows."""
import os
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from apscheduler.schedulers.background import BackgroundScheduler

from app.database import SessionLocal
from app.models.fax_log import FaxLog, FaxLogStatus
from app.services.fax_service import check_fax_status
from app.services.audit_service import log_action

POLL_INTERVAL_MINUTES = int(os.environ.get("FAX_POLL_INTERVAL_MINUTES", "2"))
POLL_MAX_AGE_MINUTES = int(os.environ.get("FAX_POLL_MAX_AGE_MINUTES", "60"))


# RingCentral statuses → our FaxLogStatus
_DELIVERED_STATES = {"Sent", "Delivered", "Received"}
_FAILED_STATES = {"SendingFailed", "DeliveryFailed", "Failed"}
_IN_FLIGHT_STATES = {"Queued", "Sending"}


def poll_outstanding_faxes(db: Session) -> int:
    """One polling pass. Returns the number of rows whose status transitioned."""
    cutoff = datetime.utcnow() - timedelta(minutes=POLL_MAX_AGE_MINUTES)
    candidates = (
        db.query(FaxLog)
        .filter(
            FaxLog.status.in_([FaxLogStatus.QUEUED, FaxLogStatus.SENT]),
            FaxLog.sent_at >= cutoff,
            FaxLog.ringcentral_message_id.isnot(None),
        )
        .all()
    )

    changed = 0
    now = datetime.utcnow()
    for row in candidates:
        try:
            rc = check_fax_status(row.ringcentral_message_id)
        except Exception as e:
            # Don't fail the batch; mark last_checked_at and continue
            row.last_checked_at = now
            db.commit()
            continue

        rc_status = (rc.get("status") or "").strip() if rc else ""
        row.last_checked_at = now

        if rc_status in _DELIVERED_STATES:
            if row.status != FaxLogStatus.DELIVERED:
                row.status = FaxLogStatus.DELIVERED
                row.delivered_at = now
                changed += 1
                log_action(db, "FAX_DELIVERED", "fax", resource_id=str(row.id),
                           description=f"Fax {row.ringcentral_message_id} delivered")
        elif rc_status in _FAILED_STATES:
            if row.status != FaxLogStatus.FAILED:
                row.status = FaxLogStatus.FAILED
                row.error = rc.get("error") or rc_status
                changed += 1
                log_action(db, "FAX_FAILED", "fax", resource_id=str(row.id),
                           description=f"Fax {row.ringcentral_message_id} failed: {row.error}")
        # In-flight / unknown → leave status alone

        db.commit()

    return changed


def _tick():
    db = SessionLocal()
    try:
        poll_outstanding_faxes(db)
    finally:
        db.close()


def start_scheduler() -> BackgroundScheduler:
    sched = BackgroundScheduler(daemon=True)
    sched.add_job(_tick, "interval", minutes=POLL_INTERVAL_MINUTES, id="fax_poller",
                  max_instances=1, coalesce=True)
    sched.start()
    return sched
```

- [ ] **Step 4: Wire the scheduler into `lifespan` in `backend/app/main.py`**

Replace the existing `lifespan` with:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    from app.services.fax_poller import start_scheduler
    sched = start_scheduler()
    try:
        yield
    finally:
        sched.shutdown(wait=False)
```

Note: `lifespan` is already imported via `asynccontextmanager` — no new top-level imports needed besides `start_scheduler`, which is done locally to avoid a circular import at module load time.

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_fax_poller.py -v 2>&1 | tail -10
```
Expected: 4 tests PASS.

- [ ] **Step 6: Smoke-start the backend and watch for scheduler init**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && uvicorn app.main:app --port 8765 &
sleep 3
kill %1 2>/dev/null
```
Expected: no exceptions; the `BackgroundScheduler` starts silently.

- [ ] **Step 7: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/services/fax_poller.py backend/app/main.py backend/tests/test_fax_poller.py
git commit -m "feat(backend): APScheduler-driven fax status poller (2 min, 1 h max age)"
```

---

## Task 12: Frontend — FaxStatusChip + useFaxByChart hook

**Files:**
- Create: `frontend/src/components/FaxStatusChip.jsx`
- Create: `frontend/src/hooks/useFaxByChart.js`
- Modify: `frontend/src/utils/api.js`

- [ ] **Step 1: Add fmt helpers to `frontend/src/utils/api.js`**

Open `frontend/src/utils/api.js`. In the `fmt` object, add two entries:

```javascript
faxStatus(status) {
  switch (status) {
    case 'queued':    return '⟳ Queued'
    case 'sent':      return '⟳ Sending'
    case 'delivered': return '✓ Delivered'
    case 'failed':    return '✗ Failed'
    default:          return status || '—'
  }
},
faxDate(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  return d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })
},
```

(Place them after the existing formatters. If `fmt` doesn't already exist as an exported object, this file is the one that `Dashboard.jsx` already uses for `fmt.currency` — check first and match the export pattern.)

- [ ] **Step 2: Create `frontend/src/hooks/useFaxByChart.js`**

```jsx
import { useQuery } from '@tanstack/react-query'
import api from '../utils/api'

// Returns FaxLog rows for a chart; auto-refreshes every 30s while any row is non-terminal.
export function useFaxByChart(chartNumber, { enabled = true } = {}) {
  return useQuery({
    queryKey: ['fax-by-chart', chartNumber],
    queryFn: () => api.get(`/fax/by-chart/${chartNumber}`).then(r => r.data),
    enabled: !!chartNumber && enabled,
    refetchInterval: (data) =>
      Array.isArray(data) && data.some(r => r.status === 'queued' || r.status === 'sent')
        ? 30_000
        : false,
  })
}

// Given an array of fax log rows, return a map of doc_id → most-recent row.
export function faxByDocId(rows) {
  const out = {}
  if (!Array.isArray(rows)) return out
  // rows arrive newest-first from the API; iterate that way so the first hit per
  // doc_id wins and later (older) rows don't overwrite it.
  for (const r of rows) {
    for (const docId of r.doc_ids || []) {
      if (!out[docId]) out[docId] = r
    }
  }
  return out
}
```

- [ ] **Step 3: Create `frontend/src/components/FaxStatusChip.jsx`**

```jsx
import { fmt } from '../utils/api'

const STYLES = {
  queued:    'bg-plum-100 text-plum-700',
  sent:      'bg-plum-100 text-plum-700',
  delivered: 'bg-green-100 text-green-800',
  failed:    'bg-red-100 text-red-800',
}

export default function FaxStatusChip({ row, onRetry }) {
  if (!row) return null
  const style = STYLES[row.status] || 'bg-gray-100 text-gray-600'
  const label = fmt.faxStatus(row.status)

  const content = (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${style}`}>
      {label}
      {row.status === 'delivered' && row.delivered_at && (
        <span className="ml-1 opacity-75">· {fmt.faxDate(row.delivered_at)}</span>
      )}
      {row.status === 'sent' && row.sent_at && (
        <span className="ml-1 opacity-75">· {fmt.faxDate(row.sent_at)}</span>
      )}
    </span>
  )

  if (row.status === 'failed' && onRetry) {
    return (
      <button
        onClick={() => onRetry(row)}
        title={row.error || 'Retry'}
        className="inline-flex items-center gap-1.5"
      >
        {content}
        <span className="text-[11px] text-plum-700 underline">retry</span>
      </button>
    )
  }
  return content
}
```

- [ ] **Step 4: Verify frontend build**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/frontend && npx vite build 2>&1 | tail -8
```
Expected: build succeeds.

- [ ] **Step 5: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add frontend/src/utils/api.js frontend/src/components/FaxStatusChip.jsx frontend/src/hooks/useFaxByChart.js
git commit -m "feat(frontend): FaxStatusChip + useFaxByChart hook"
```

---

## Task 13: Frontend — FaxBatchModal component

**Files:**
- Create: `frontend/src/components/FaxBatchModal.jsx`

- [ ] **Step 1: Create the modal**

```jsx
import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import api from '../utils/api'

const MODES = [
  { value: 'separate', label: 'Separate', help: 'One fax per doc' },
  { value: 'combined', label: 'Combined', help: 'Merge into one fax' },
  { value: 'by_type',  label: 'By doc type', help: 'Group by category' },
]

export default function FaxBatchModal({
  chartNumber, docIds, defaultDestFax, defaultCover, onClose,
}) {
  const [dest, setDest] = useState(defaultDestFax || '2402522141')
  const [mode, setMode] = useState('separate')
  const [cover, setCover] = useState(defaultCover || '')
  const [result, setResult] = useState(null)
  const queryClient = useQueryClient()

  const send = useMutation({
    mutationFn: () => api.post('/fax/send-batch', {
      chart_number: chartNumber,
      doc_ids: docIds,
      dest_fax: dest,
      grouping_mode: mode,
      cover_text: cover,
    }).then(r => r.data),
    onSuccess: (data) => {
      setResult(data)
      queryClient.invalidateQueries({ queryKey: ['fax-by-chart', chartNumber] })
      queryClient.invalidateQueries({ queryKey: ['fax-recent'] })
    },
  })

  const busy = send.isPending
  const hasResult = !!result

  return (
    <div
      className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4"
      onClick={(e) => { if (e.target === e.currentTarget && !busy) onClose() }}
    >
      <div className="bg-white rounded-lg border border-border-subtle w-[520px] p-5">
        <h2 className="font-serif text-lg text-ink m-0">
          Fax {docIds.length} doc{docIds.length === 1 ? '' : 's'} to EMA
        </h2>
        <div className="text-[13px] text-muted mt-0.5">Chart {chartNumber}</div>

        {!hasResult ? (
          <>
            <div className="mt-4">
              <label className="eyebrow block mb-1">Destination fax</label>
              <input className="input" value={dest}
                     onChange={(e) => setDest(e.target.value)} disabled={busy} />
            </div>

            <div className="mt-3">
              <label className="eyebrow block mb-1">Grouping</label>
              <div className="flex gap-2">
                {MODES.map(({ value, label, help }) => (
                  <label key={value}
                         className={`flex-1 p-2 rounded border cursor-pointer text-[13px] ${
                           mode === value
                             ? 'border-plum-700 bg-plum-100'
                             : 'border-border-subtle hover:border-plum-300'
                         }`}>
                    <input type="radio" className="hidden"
                           checked={mode === value}
                           onChange={() => setMode(value)}
                           disabled={busy || docIds.length === 1 && value !== 'separate'} />
                    <div className="font-medium text-ink">{label}</div>
                    <div className="text-muted text-[11px]">{help}</div>
                  </label>
                ))}
              </div>
            </div>

            <div className="mt-3">
              <label className="eyebrow block mb-1">Cover note</label>
              <textarea className="input" rows={3} value={cover}
                        onChange={(e) => setCover(e.target.value)} disabled={busy} />
            </div>

            {send.isError && (
              <div className="mt-3 text-[12px] text-danger">
                {send.error?.response?.data?.detail || 'Send failed'}
              </div>
            )}

            <div className="mt-4 flex gap-2 justify-end">
              <button className="btn-secondary" onClick={onClose} disabled={busy}>Cancel</button>
              <button className="btn-primary" onClick={() => send.mutate()} disabled={busy || !dest}>
                {busy ? 'Sending...' : `Send${docIds.length > 1 ? ` ${docIds.length}` : ''}`}
              </button>
            </div>
          </>
        ) : (
          <>
            <div className="mt-4 text-[13px]">
              {result.faxes.map((f, i) => (
                <div key={i}
                     className={`flex justify-between py-1.5 border-b border-plum-100 last:border-b-0 ${
                       f.status === 'failed' ? 'text-danger' : 'text-ink'
                     }`}>
                  <span>
                    {f.status === 'failed' ? `✗ ${f.error || 'failed'}` : `✓ sent`}
                    <span className="text-muted ml-2">
                      ({f.doc_ids.length} doc{f.doc_ids.length === 1 ? '' : 's'})
                    </span>
                  </span>
                  <span className="text-muted">{f.ringcentral_message_id || ''}</span>
                </div>
              ))}
            </div>
            <div className="mt-4 flex justify-end">
              <button className="btn-primary" onClick={onClose}>Done</button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Verify the build**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/frontend && npx vite build 2>&1 | tail -6
```
Expected: success.

- [ ] **Step 3: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add frontend/src/components/FaxBatchModal.jsx
git commit -m "feat(frontend): FaxBatchModal with separate/combined/by_type grouping"
```

---

## Task 14: PatientChart integration

**Files:**
- Modify: `frontend/src/pages/PatientChart.jsx`

- [ ] **Step 1: Read the current file**

Read `/Users/wwcclaudecode/Documents/wwc-era-project/frontend/src/pages/PatientChart.jsx` to understand where the doc sections and existing `FaxModal` live. Note: the existing `FaxModal` component at the top of the file (lines ~12–104) is the single-doc modal being replaced. Leave all other page structure (patient header, intake docs, etc.) alone.

- [ ] **Step 2: Replace the old FaxModal import/component with the new flow**

At the top of `PatientChart.jsx`, add:

```jsx
import FaxBatchModal from '../components/FaxBatchModal'
import FaxStatusChip from '../components/FaxStatusChip'
import { useFaxByChart, faxByDocId } from '../hooks/useFaxByChart'
```

Remove the old `FaxModal` component definition (entire `function FaxModal(...)` block near the top) and any remaining `useState` hooks that were supporting it.

In the main component body, near the top, add:

```jsx
const [selected, setSelected] = useState(new Set())
const [showModal, setShowModal] = useState(false)

const faxQuery = useFaxByChart(chartNumber)
const byDoc = faxByDocId(faxQuery.data)

function toggle(docId) {
  setSelected(prev => {
    const next = new Set(prev)
    if (next.has(docId)) next.delete(docId)
    else next.add(docId)
    return next
  })
}

function selectAllUnsent(docs) {
  const unsent = docs.filter(d => !byDoc[d.id] || byDoc[d.id].status === 'failed').map(d => d.id)
  setSelected(prev => new Set([...prev, ...unsent]))
}

function clearSelection() { setSelected(new Set()) }
```

- [ ] **Step 3: Add the action bar above each doc section**

Each doc-section render currently starts with a heading. Above the first doc list (intake docs + PrimeSuite docs in turn), insert an action bar:

```jsx
<div className="flex items-center justify-between mb-3">
  <h2 className="font-serif font-semibold text-ink text-[15px] m-0">
    PrimeSuite Documents ({primeSuiteDocs.length})
  </h2>
  <div className="flex items-center gap-3">
    <button
      onClick={() => selectAllUnsent(primeSuiteDocs)}
      className="text-[12px] text-plum-700 underline"
    >
      Select unsent
    </button>
    {selected.size > 0 && (
      <>
        <button onClick={clearSelection} className="text-[12px] text-muted underline">
          Clear ({selected.size})
        </button>
        <button className="btn-primary" onClick={() => setShowModal(true)}>
          Fax {selected.size} {selected.size === 1 ? 'doc' : 'docs'} to EMA →
        </button>
      </>
    )}
  </div>
</div>
```

(Do the same for the intake docs section with its own variable names, OR use a shared helper — the intake docs section is smaller; just pass `intakeDocs` to `selectAllUnsent`.)

- [ ] **Step 4: Add checkboxes + chips to each doc row**

In the row markup of each doc, prepend a checkbox and append a status chip:

```jsx
<tr key={doc.id} className="table-row">
  <td className="table-td w-8">
    <input
      type="checkbox"
      checked={selected.has(doc.id)}
      onChange={() => toggle(doc.id)}
    />
  </td>
  {/* ... existing cells (type, date, pages, etc.) ... */}
  <td className="table-td">
    <FaxStatusChip
      row={byDoc[doc.id]}
      onRetry={(row) =>
        api.post(`/fax/retry/${row.id}`).then(() => faxQuery.refetch())
      }
    />
  </td>
  <td className="table-td">{/* existing action buttons */}</td>
</tr>
```

- [ ] **Step 5: Render the modal conditionally**

At the end of the component return (still inside the outer `<div>`):

```jsx
{showModal && (
  <FaxBatchModal
    chartNumber={chartNumber}
    docIds={Array.from(selected)}
    defaultDestFax="2402522141"  // TODO Task 15 polish: pull from practice_config
    defaultCover={`Patient: ${patientName}\nDOB: ${dob || 'Unknown'}\nChart #${chartNumber}`}
    onClose={() => { setShowModal(false); clearSelection() }}
  />
)}
```

- [ ] **Step 6: Verify build + live behavior**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/frontend && npx vite build 2>&1 | tail -6
```

Then start the full stack (`./start.sh` from repo root), open a chart at `/chart/<any-chart-with-docs>`, tick a doc, click "Fax 1 doc to EMA" — the modal should open. You can cancel without sending.

- [ ] **Step 7: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add frontend/src/pages/PatientChart.jsx
git commit -m "feat(frontend): PatientChart checkboxes + batch-fax action bar + status chips"
```

---

## Task 15: FaxLog page + TopNav entry + smoke test

**Files:**
- Create: `frontend/src/pages/FaxLog.jsx`
- Modify: `frontend/src/App.jsx`
- Modify: `frontend/src/components/layout/TopNav.jsx`

- [ ] **Step 1: Create `frontend/src/pages/FaxLog.jsx`**

```jsx
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { format } from 'date-fns'
import api, { fmt } from '../utils/api'
import FaxStatusChip from '../components/FaxStatusChip'

const STATUSES = [
  { value: '',          label: 'All' },
  { value: 'queued',    label: 'Queued' },
  { value: 'sent',      label: 'Sent' },
  { value: 'delivered', label: 'Delivered' },
  { value: 'failed',    label: 'Failed' },
]

export default function FaxLog() {
  const [status, setStatus] = useState('')
  const [chart, setChart] = useState('')
  const [page, setPage] = useState(1)

  const q = useQuery({
    queryKey: ['fax-log', status, chart, page],
    queryFn: () => api.get('/fax-log', {
      params: { status: status || undefined, chart: chart || undefined, page, page_size: 50 },
    }).then(r => r.data),
    keepPreviousData: true,
  })

  const data = q.data

  async function retry(id) {
    await api.post(`/fax/retry/${id}`)
    q.refetch()
  }

  return (
    <div>
      <div className="flex items-baseline justify-between mb-4">
        <h1 className="font-serif font-semibold text-ink text-[24px] m-0">Fax log</h1>
        {data && <div className="text-muted text-[13px]">{data.total} total</div>}
      </div>

      <div className="flex gap-3 mb-3">
        <select className="input w-40" value={status}
                onChange={(e) => { setStatus(e.target.value); setPage(1) }}>
          {STATUSES.map(s => <option key={s.value} value={s.value}>{s.label}</option>)}
        </select>
        <input className="input w-48" placeholder="Chart #"
               value={chart} onChange={(e) => { setChart(e.target.value); setPage(1) }} />
      </div>

      <div className="card p-0 overflow-hidden">
        <table className="w-full">
          <thead className="bg-plum-50">
            <tr>
              <th className="table-th">Sent</th>
              <th className="table-th">Chart</th>
              <th className="table-th">Patient</th>
              <th className="table-th">Docs</th>
              <th className="table-th">Grouping</th>
              <th className="table-th">Dest</th>
              <th className="table-th">Status</th>
              <th className="table-th"></th>
            </tr>
          </thead>
          <tbody>
            {data?.rows?.map(r => (
              <tr key={r.id} className="table-row">
                <td className="table-td whitespace-nowrap">
                  {r.sent_at ? format(new Date(r.sent_at), 'MM/dd h:mm a') : '—'}
                </td>
                <td className="table-td">{r.chart_number}</td>
                <td className="table-td">{r.patient_name}</td>
                <td className="table-td">{r.doc_count}</td>
                <td className="table-td">{r.grouping_mode}</td>
                <td className="table-td">{r.dest_fax}</td>
                <td className="table-td"><FaxStatusChip row={r} onRetry={() => retry(r.id)} /></td>
                <td className="table-td text-[12px]">{r.error ? <span className="text-danger" title={r.error}>error</span> : ''}</td>
              </tr>
            ))}
            {data?.rows?.length === 0 && (
              <tr><td colSpan={8} className="table-td text-center text-muted py-8">
                No faxes match these filters.
              </td></tr>
            )}
          </tbody>
        </table>
      </div>

      {data && data.total > 50 && (
        <div className="flex items-center justify-end gap-2 mt-3 text-[13px]">
          <button className="btn-secondary" disabled={page <= 1}
                  onClick={() => setPage(p => Math.max(1, p - 1))}>Prev</button>
          <span className="text-muted">Page {data.page}</span>
          <button className="btn-secondary" disabled={page * 50 >= data.total}
                  onClick={() => setPage(p => p + 1)}>Next</button>
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Add the route to `frontend/src/App.jsx`**

Inside `ProtectedApp`, below the existing `/audit` route, add:
```jsx
import FaxLogPage from './pages/FaxLog'
```
(Import goes next to the other page imports at the top of the file, alphabetical insert near `ImportFiles`.)

And add the route inside `<Routes>`:
```jsx
<Route path="/fax-log" element={<FaxLogPage />} />
```

- [ ] **Step 3: Add "Fax log" link to TopNav**

Open `frontend/src/components/layout/TopNav.jsx`. Update the `nav` array to insert a `Fax log` entry between `Import` and `Audit`:

```jsx
const nav = [
  { to: '/',          label: 'Dashboard' },
  { to: '/ar',        label: 'A/R' },
  { to: '/documents', label: 'Charts' },
  { to: '/claims',    label: 'Claims' },
  { to: '/denials',   label: 'Denials' },
  { to: '/appeals',   label: 'Appeals' },
  { to: '/import',    label: 'Import' },
  { to: '/fax-log',   label: 'Fax log' },
  { to: '/audit',     label: 'Audit' },
]
```

- [ ] **Step 4: Run the full test suite one more time**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/ -v 2>&1 | tail -20
```
Expected: all tests PASS (Phase 0 + Phase 1). If any fail, stop and fix before the final smoke.

- [ ] **Step 5: Frontend build**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/frontend && npx vite build 2>&1 | tail -6
```
Expected: success.

- [ ] **Step 6: Manual end-to-end smoke**

From repo root:
```bash
./start.sh
```

1. Navigate to `/fax-log` — empty table with filter controls visible.
2. Navigate to `/chart/<any-real-chart-number>`.
3. Tick two docs. The "Fax 2 docs to EMA" button appears.
4. Click it. Modal opens with destination `2402522141`, Separate default. Click Send.
5. On success, chips appear next to those docs as `⟳ Sending`.
6. Visit `/fax-log` — the new entries show as `Sent`.
7. Visit `/` (Dashboard) — "Recent faxes to EMA" card shows those entries.
8. Wait ~2 minutes. Chips flip to `✓ Delivered`.

- [ ] **Step 7: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add frontend/src/pages/FaxLog.jsx frontend/src/App.jsx frontend/src/components/layout/TopNav.jsx
git commit -m "feat(frontend): /fax-log page + TopNav entry; Phase 1 wired end-to-end"
```

- [ ] **Step 8: Final empty verification commit**

```bash
git commit --allow-empty -m "test: Phase 1 fax-to-EMA verified end-to-end (chart → batch → poller → log)"
```

---

## Self-review results

- **Spec coverage:** ✓ Each spec section has a task. Data model (T1), config (T2), `/send-batch` separate/combined/by_type (T3/T4/T5), legacy `/send` delegation (T6), `/recent` (T7), `/by-chart` (T8), `/retry` (T9), `/fax-log` (T10), poller (T11), frontend chip+hook (T12), modal (T13), PatientChart integration (T14), FaxLog page + nav (T15).
- **Placeholder scan:** One intentional forward-reference marked: `TODO Task 15 polish: pull from practice_config` in the `PatientChart.jsx` modal's `defaultDestFax` prop. The frontend hardcoded default is acceptable for Phase 1; a follow-up in Phase 5 can swap it for a query against PracticeConfig. Otherwise zero "TBD"/"implement later".
- **Type consistency:** FaxLogStatus and GroupingMode enum values are referenced consistently across tests and code (`"separate"/"combined"/"by_type"`, `"queued"/"sent"/"delivered"/"failed"`). `SendBatchPayload` field names are consistent across tasks. The `fmt.faxStatus` and `fmt.faxDate` helpers in T12 are used by both `FaxStatusChip` (T12) and `FaxLog.jsx` (T15).
- **Schema seed:** `PracticeConfig` row `ema_default_fax` is seeded in T2 so every subsequent task can rely on it existing.
- **Test isolation:** Every send-batch test mocks `app.routers.fax_batch.send_fax` — no real RingCentral calls.
