# Patient Portal P5 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire self-report milestones, clearance template download, and patient document uploads. Builds the upload infrastructure that P5b will reuse for FMLA.

**Architecture:** One new SQLAlchemy model (`SurgeryDocument`), one new service (`surgery_uploads.py`), six new portal endpoints, two frontend touch-ups. GCS bucket `gs://wwc-documents` is reused; the backend service account needs `objectCreator` added (see prereq below).

**Spec:** `docs/superpowers/specs/2026-06-01-patient-portal-p5-design.md`

**Prerequisites:**
1. The backend service account needs write access on the GCS bucket:
   ```bash
   gcloud storage buckets add-iam-policy-binding gs://wwc-documents \
     --project=wwc-solutions \
     --member=serviceAccount:backend@wwc-solutions.iam.gserviceaccount.com \
     --role=roles/storage.objectCreator
   ```
2. The clearance template PDF must be uploaded for the template endpoint to return real content:
   ```bash
   gcloud storage cp clearance_template.pdf \
     gs://wwc-documents/clearance/template.pdf
   ```
   Until that's done, the endpoint returns the patient-friendly 404 fallback.

**Key existing-code facts (don't relitigate):**
- `Surgery.clearance_required` (Bool) and `Surgery.clearance_status` (String) already exist (`surgery.py:162-163`).
- `Surgery.labs_self_reported` + `_at` and `Surgery.hospital_preop_self_reported` + `_at` already exist from P1 T1 (`surgery.py:216-219`).
- Prod GUID columns use `CHAR(36)`, not `UUID` — confirmed during P1 smoke test. Migration script must use `CHAR(36)`.
- `app/services/surgery_documents.py` exists from P4 (`fetch_instructions_pdf`). P5 adds a sibling service `app/services/surgery_uploads.py`; don't merge them.

---

## Task 1: Schema — `SurgeryDocument` model + migration

**Files:**
- Modify: `backend/app/models/surgery.py` — append the new model
- Modify: `backend/app/database.py` — register the model for `init_db()`
- Create: `backend/scripts/migrate_patient_portal_p5.py`
- Test: `backend/tests/test_patient_portal_p5_schema.py`

- [ ] **Step 1: Failing test** at `backend/tests/test_patient_portal_p5_schema.py`:

```python
"""Patient portal P5 schema."""
from datetime import datetime

from app.models.surgery import Surgery, SurgeryDocument


def test_surgery_document_persists(db):
    s = Surgery(chart_number="1", patient_name="Pat", status="new")
    db.add(s); db.commit(); db.refresh(s)
    doc = SurgeryDocument(
        surgery_id=s.id,
        kind="clearance",
        filename="clearance.pdf",
        gcs_path=f"surgery-uploads/{s.id}/clearance/2026-06-01_clearance.pdf",
        content_type="application/pdf",
        size_bytes=12345,
        uploaded_by="patient:portal",
    )
    db.add(doc); db.commit(); db.refresh(doc)
    assert doc.id is not None
    assert doc.uploaded_at is not None  # default fired
    assert doc.kind == "clearance"


def test_surgery_documents_relationship(db):
    s = Surgery(chart_number="2", patient_name="Pat", status="new")
    db.add(s); db.commit(); db.refresh(s)
    db.add_all([
        SurgeryDocument(surgery_id=s.id, kind="clearance",
                          filename="a.pdf",
                          gcs_path=f"surgery-uploads/{s.id}/a.pdf",
                          uploaded_by="patient:portal"),
        SurgeryDocument(surgery_id=s.id, kind="ekg",
                          filename="b.pdf",
                          gcs_path=f"surgery-uploads/{s.id}/b.pdf",
                          uploaded_by="patient:portal"),
    ])
    db.commit(); db.refresh(s)
    assert len(s.documents) == 2
    kinds = {d.kind for d in s.documents}
    assert kinds == {"clearance", "ekg"}
```

- [ ] **Step 2: Run, confirm fail** (ImportError on SurgeryDocument):

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && \
  ./venv/bin/pytest tests/test_patient_portal_p5_schema.py -v
```

- [ ] **Step 3: Add the model** to `backend/app/models/surgery.py`. Append at the end of the file (after the existing class definitions):

```python
class SurgeryDocument(Base):
    """Patient-uploaded documents (clearance, EKG, FMLA, …)."""
    __tablename__ = "surgery_documents"
    __table_args__ = (
        Index("ix_surgery_documents_surgery_id", "surgery_id"),
    )

    id           = Column(GUID(), primary_key=True, default=new_uuid)
    surgery_id   = Column(GUID(),
                            ForeignKey("surgeries.id", ondelete="CASCADE"),
                            nullable=False)
    kind         = Column(String(40), nullable=False)
    filename     = Column(String(255), nullable=False)
    gcs_path     = Column(String(500), nullable=False)
    content_type = Column(String(100), nullable=True)
    size_bytes   = Column(Integer, nullable=True)
    uploaded_at  = Column(DateTime, default=datetime.utcnow, nullable=False)
    uploaded_by  = Column(String(120), nullable=False)
```

And add the relationship on the Surgery class. Find the existing relationship block on `Surgery` (look for `payments`, `consent_envelopes`, etc.) and append:

```python
    documents = relationship(
        "SurgeryDocument",
        backref="surgery",
        cascade="all, delete-orphan",
        order_by="SurgeryDocument.uploaded_at.desc()",
    )
```

If `Index` or `Integer` aren't imported at the top of `surgery.py`, add them to the SQLAlchemy imports.

- [ ] **Step 4: Register the model** in `backend/app/database.py`. Find the `init_db` import block (the pattern P1 T1 established — `from app.models import patient_portal` etc.) and add:

```python
from app.models import surgery  # already there, but `SurgeryDocument` lives in it
```

If `surgery` is already imported (it almost certainly is), no change needed — adding the class to that module makes it available via the existing import.

- [ ] **Step 5: Run, confirm pass.**

- [ ] **Step 6: Create the migration** at `backend/scripts/migrate_patient_portal_p5.py`:

```python
"""Idempotent migration for Patient Portal P5.

Adds: new table `surgery_documents`.

Run on prod:
    DATABASE_URL='postgresql+psycopg2://...' \
        ./venv/bin/python scripts/migrate_patient_portal_p5.py
"""
import os
import sys
from sqlalchemy import create_engine, text

DDL = [
    """CREATE TABLE IF NOT EXISTS surgery_documents (
        id            CHAR(36) PRIMARY KEY,
        surgery_id    CHAR(36) NOT NULL REFERENCES surgeries(id) ON DELETE CASCADE,
        kind          VARCHAR(40) NOT NULL,
        filename      VARCHAR(255) NOT NULL,
        gcs_path      VARCHAR(500) NOT NULL,
        content_type  VARCHAR(100) NULL,
        size_bytes    INTEGER NULL,
        uploaded_at   TIMESTAMP NOT NULL DEFAULT NOW(),
        uploaded_by   VARCHAR(120) NOT NULL
    )""",
    """CREATE INDEX IF NOT EXISTS ix_surgery_documents_surgery_id
       ON surgery_documents(surgery_id)""",
]


def main():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL not set", file=sys.stderr); sys.exit(2)
    eng = create_engine(db_url)
    with eng.begin() as conn:
        for ddl in DDL:
            conn.execute(text(ddl))
            print(f"  ✓ {ddl.split(chr(10))[0][:80]}")
    print("\nDone.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 7: Commit.**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/models/surgery.py backend/scripts/migrate_patient_portal_p5.py \
        backend/tests/test_patient_portal_p5_schema.py
git commit -m "feat(portal-p5): SurgeryDocument model + migration"
```

Do NOT push.

---

## Task 2: `surgery_uploads` service

**Files:**
- Create: `backend/app/services/surgery_uploads.py`
- Create: `backend/tests/test_surgery_uploads.py`

- [ ] **Step 1: Failing tests** at `backend/tests/test_surgery_uploads.py`:

```python
"""Surgery uploads service — multipart write + signed-URL read."""
from unittest.mock import patch, MagicMock

import pytest

from app.models.surgery import Surgery, SurgeryDocument
from app.services.surgery_uploads import (
    ALLOWED_MIME, MAX_BYTES,
    UploadError,
    store_upload,
    signed_download_url,
    stream_static_pdf,
)


PDF_BYTES = b"%PDF-1.4\nthis is a test pdf"
JPEG_BYTES = b"\xff\xd8\xff\xe0\x00\x10JFIFfake-jpeg-data"


def test_store_upload_writes_to_gcs_and_creates_row(db):
    s = Surgery(chart_number="1", patient_name="Pat", status="new")
    db.add(s); db.commit(); db.refresh(s)
    with patch("app.services.surgery_uploads.storage.Client") as MockClient:
        blob = MagicMock()
        MockClient.return_value.bucket.return_value.blob.return_value = blob

        doc = store_upload(db, s, kind="clearance",
                              filename="my form.pdf",
                              file_bytes=PDF_BYTES,
                              content_type="application/pdf",
                              uploaded_by="patient:portal")

    assert doc.surgery_id == s.id
    assert doc.kind == "clearance"
    assert doc.filename == "my form.pdf"
    assert doc.content_type == "application/pdf"
    assert doc.size_bytes == len(PDF_BYTES)
    # GCS path includes timestamp + sanitized filename
    assert doc.gcs_path.startswith(f"surgery-uploads/{s.id}/clearance/")
    assert doc.gcs_path.endswith(".pdf")
    # Backed write happened
    blob.upload_from_string.assert_called_once_with(
        PDF_BYTES, content_type="application/pdf"
    )


def test_store_upload_rejects_oversize(db):
    s = Surgery(chart_number="1", patient_name="Pat", status="new")
    db.add(s); db.commit(); db.refresh(s)
    huge = b"x" * (MAX_BYTES + 1)
    with pytest.raises(UploadError, match="too large"):
        store_upload(db, s, kind="clearance",
                       filename="big.pdf",
                       file_bytes=huge,
                       content_type="application/pdf",
                       uploaded_by="patient:portal")


def test_store_upload_rejects_unknown_mime(db):
    s = Surgery(chart_number="1", patient_name="Pat", status="new")
    db.add(s); db.commit(); db.refresh(s)
    with pytest.raises(UploadError, match="file type"):
        store_upload(db, s, kind="clearance",
                       filename="run.exe",
                       file_bytes=b"MZfake-exe",
                       content_type="application/x-msdownload",
                       uploaded_by="patient:portal")


def test_store_upload_rejects_mime_mismatch(db):
    """Caller says application/pdf but the bytes are JPEG → reject."""
    s = Surgery(chart_number="1", patient_name="Pat", status="new")
    db.add(s); db.commit(); db.refresh(s)
    with pytest.raises(UploadError, match="content"):
        store_upload(db, s, kind="clearance",
                       filename="trick.pdf",
                       file_bytes=JPEG_BYTES,
                       content_type="application/pdf",
                       uploaded_by="patient:portal")


def test_signed_download_url_calls_blob_v4(db):
    s = Surgery(chart_number="1", patient_name="Pat", status="new")
    db.add(s); db.commit(); db.refresh(s)
    doc = SurgeryDocument(surgery_id=s.id, kind="clearance",
                            filename="x.pdf",
                            gcs_path=f"surgery-uploads/{s.id}/x.pdf",
                            uploaded_by="patient:portal")
    db.add(doc); db.commit(); db.refresh(doc)
    with patch("app.services.surgery_uploads.storage.Client") as MockClient:
        blob = MagicMock()
        blob.generate_signed_url.return_value = "https://signed.example/x.pdf"
        MockClient.return_value.bucket.return_value.blob.return_value = blob

        url = signed_download_url(doc, ttl_minutes=5)
        assert url == "https://signed.example/x.pdf"
        # Verify v4 + TTL
        _, kwargs = blob.generate_signed_url.call_args
        assert kwargs.get("version") == "v4"


def test_stream_static_pdf_returns_bytes():
    with patch("app.services.surgery_uploads.storage.Client") as MockClient:
        blob = MagicMock()
        blob.exists.return_value = True
        blob.download_as_bytes.return_value = b"%PDF-blank"
        MockClient.return_value.bucket.return_value.blob.return_value = blob

        result = stream_static_pdf("clearance/template.pdf")
        assert result == b"%PDF-blank"


def test_stream_static_pdf_returns_none_when_missing():
    with patch("app.services.surgery_uploads.storage.Client") as MockClient:
        blob = MagicMock()
        blob.exists.return_value = False
        MockClient.return_value.bucket.return_value.blob.return_value = blob

        result = stream_static_pdf("clearance/template.pdf")
        assert result is None
```

- [ ] **Step 2: Run, confirm fail** (ImportError):

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && \
  ./venv/bin/pytest tests/test_surgery_uploads.py -v
```

- [ ] **Step 3: Create the service** at `backend/app/services/surgery_uploads.py`:

```python
"""Patient-uploaded document storage in GCS, plus the static-PDF read
path used by the clearance template endpoint.

Bucket: gs://wwc-documents (same as the P4 instructions library).
Patient uploads live under:
    gs://wwc-documents/surgery-uploads/{surgery_id}/{kind}/{ts}_{safe_name}.{ext}

Validation:
  - Max 10 MB per upload
  - Content-Type must be one of ALLOWED_MIME
  - Magic-byte sniff verifies the bytes match the declared Content-Type
    so a renamed .exe can't slip through as an .pdf
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Optional

from google.cloud import storage
from sqlalchemy.orm import Session

from app.models.surgery import Surgery, SurgeryDocument

log = logging.getLogger(__name__)

BUCKET = "wwc-documents"
ALLOWED_MIME = (
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/heic",
)
MAX_BYTES = 10 * 1024 * 1024

# Magic-byte signatures for each MIME we accept. First N bytes must match.
_MAGIC = {
    "application/pdf": (b"%PDF",),
    "image/jpeg":      (b"\xff\xd8\xff",),
    "image/png":       (b"\x89PNG\r\n\x1a\n",),
    "image/heic":      (b"ftypheic", b"ftypheix", b"ftyphevc", b"ftypheim"),
    # HEIC magic appears at offset 4-12 (after the box-size prefix), so the
    # check is "any of these substrings appears in the first 32 bytes."
}


class UploadError(Exception):
    """Raised when an upload can't be stored. Message is patient-facing."""
    def __init__(self, message: str, *, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def _safe_filename(name: str) -> str:
    """Strip path components + non-alphanumerics so the GCS object name is
    predictable and free of injection risk."""
    name = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    stem, _, ext = name.rpartition(".")
    if not stem:
        stem, ext = ext, ""
    stem = re.sub(r"[^a-zA-Z0-9_.-]", "_", stem)[:80] or "upload"
    ext  = re.sub(r"[^a-zA-Z0-9]", "", ext)[:8] or "bin"
    return f"{stem}.{ext}"


def _magic_matches(content_type: str, first_bytes: bytes) -> bool:
    sigs = _MAGIC.get(content_type)
    if not sigs:
        return False
    if content_type == "image/heic":
        return any(s in first_bytes[:32] for s in sigs)
    return any(first_bytes.startswith(s) for s in sigs)


def store_upload(db: Session, surgery: Surgery, *, kind: str,
                   filename: str, file_bytes: bytes,
                   content_type: str,
                   uploaded_by: str) -> SurgeryDocument:
    """Validate → write to GCS → create SurgeryDocument row."""
    if len(file_bytes) > MAX_BYTES:
        raise UploadError(
            f"That file is too large. The maximum is "
            f"{MAX_BYTES // (1024 * 1024)} MB.",
            status_code=413,
        )
    if content_type not in ALLOWED_MIME:
        raise UploadError(
            "That file type isn't supported. Please upload a PDF, JPEG, "
            "PNG, or HEIC.",
            status_code=415,
        )
    if not _magic_matches(content_type, file_bytes[:64]):
        raise UploadError(
            "The file content doesn't match its type. Please re-export "
            "and try again.",
            status_code=415,
        )

    ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    safe = _safe_filename(filename)
    object_path = f"surgery-uploads/{surgery.id}/{kind}/{ts}_{safe}"

    client = storage.Client()
    bucket = client.bucket(BUCKET)
    blob   = bucket.blob(object_path)
    blob.upload_from_string(file_bytes, content_type=content_type)

    doc = SurgeryDocument(
        surgery_id=surgery.id,
        kind=kind,
        filename=filename,
        gcs_path=object_path,
        content_type=content_type,
        size_bytes=len(file_bytes),
        uploaded_by=uploaded_by,
    )
    db.add(doc); db.commit(); db.refresh(doc)
    return doc


def signed_download_url(doc: SurgeryDocument,
                          ttl_minutes: int = 5) -> str:
    """V4 signed URL good for the requested TTL. Default is 5 min."""
    client = storage.Client()
    bucket = client.bucket(BUCKET)
    blob   = bucket.blob(doc.gcs_path)
    return blob.generate_signed_url(
        version="v4",
        expiration=timedelta(minutes=ttl_minutes),
        method="GET",
    )


def stream_static_pdf(gcs_path: str) -> Optional[bytes]:
    """Read a static object (e.g. clearance/template.pdf) — returns the
    raw bytes or None if the object doesn't exist. Soft-fail on storage
    errors so the calling endpoint can render a friendly 404."""
    try:
        client = storage.Client()
        bucket = client.bucket(BUCKET)
        blob   = bucket.blob(gcs_path)
        if not blob.exists():
            return None
        return blob.download_as_bytes()
    except Exception as e:
        log.warning("static PDF fetch failed for %s: %s", gcs_path, e)
        return None
```

- [ ] **Step 4: Run, confirm pass.**

- [ ] **Step 5: Commit.**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/services/surgery_uploads.py backend/tests/test_surgery_uploads.py
git commit -m "feat(portal-p5): surgery_uploads service — validate, store, sign-URL, static-PDF"
```

Do NOT push.

---

## Task 3: Self-report endpoints

**Files:**
- Modify: `backend/app/routers/patient_portal.py` — append 2 handlers
- Modify: `backend/tests/test_patient_portal_endpoints.py` — append 4 tests

- [ ] **Step 1: Failing tests** — append:

```python
def test_self_report_labs_flips_flag(client, db):
    from app.services.patient_portal_auth import issue_portal_token
    s = _seed_surgery(db)
    db.commit()
    token = issue_portal_token(s)
    r = client.post(f"/api/patient/portal/{s.id}/self-report/labs",
                       headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    db.refresh(s)
    assert s.labs_self_reported is True
    assert s.labs_self_reported_at is not None


def test_self_report_labs_is_idempotent(client, db):
    """Second click doesn't restamp _at."""
    from app.services.patient_portal_auth import issue_portal_token
    s = _seed_surgery(db)
    db.commit()
    token = issue_portal_token(s)
    r1 = client.post(f"/api/patient/portal/{s.id}/self-report/labs",
                         headers={"Authorization": f"Bearer {token}"})
    db.refresh(s)
    first_ts = s.labs_self_reported_at
    r2 = client.post(f"/api/patient/portal/{s.id}/self-report/labs",
                         headers={"Authorization": f"Bearer {token}"})
    db.refresh(s)
    assert r2.status_code == 200
    assert s.labs_self_reported_at == first_ts  # not bumped


def test_self_report_hospital_preop_flips_flag(client, db):
    from app.services.patient_portal_auth import issue_portal_token
    s = _seed_surgery(db)
    db.commit()
    token = issue_portal_token(s)
    r = client.post(f"/api/patient/portal/{s.id}/self-report/hospital-preop",
                       headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    db.refresh(s)
    assert s.hospital_preop_self_reported is True
    assert s.hospital_preop_self_reported_at is not None


def test_self_report_rejects_unknown_kind_via_url(client, db):
    """The router only accepts the two paths above — anything else 404s
    via FastAPI routing."""
    from app.services.patient_portal_auth import issue_portal_token
    s = _seed_surgery(db)
    db.commit()
    token = issue_portal_token(s)
    r = client.post(f"/api/patient/portal/{s.id}/self-report/bogus",
                       headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 404
```

- [ ] **Step 2: Run, confirm fail.**

- [ ] **Step 3: Add handlers** to `backend/app/routers/patient_portal.py` (append at end):

```python
# ─── /{surgery_id}/self-report/* ──────────────────────────────────

def _flip_if_unset(surgery: Surgery, flag_attr: str, ts_attr: str) -> None:
    """Idempotent flip: only stamps the first time the flag goes True."""
    if not getattr(surgery, flag_attr, False):
        setattr(surgery, flag_attr, True)
        setattr(surgery, ts_attr, datetime.utcnow())


@router.post("/{surgery_id}/self-report/labs")
def portal_self_report_labs(surgery_id: str,
                                db: Session = Depends(get_db),
                                _: str = Depends(require_portal_token)):
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="surgery not found")
    _flip_if_unset(s, "labs_self_reported", "labs_self_reported_at")
    db.commit()
    return {
        "labs_self_reported": s.labs_self_reported,
        "labs_self_reported_at":
            s.labs_self_reported_at.isoformat()
            if s.labs_self_reported_at else None,
    }


@router.post("/{surgery_id}/self-report/hospital-preop")
def portal_self_report_hospital_preop(
    surgery_id: str,
    db: Session = Depends(get_db),
    _: str = Depends(require_portal_token),
):
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="surgery not found")
    _flip_if_unset(s, "hospital_preop_self_reported",
                       "hospital_preop_self_reported_at")
    db.commit()
    return {
        "hospital_preop_self_reported": s.hospital_preop_self_reported,
        "hospital_preop_self_reported_at":
            s.hospital_preop_self_reported_at.isoformat()
            if s.hospital_preop_self_reported_at else None,
    }
```

`datetime` should already be imported at the top of the file. If not, add `from datetime import datetime`.

- [ ] **Step 4: Run, confirm pass.**

- [ ] **Step 5: Commit.**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/routers/patient_portal.py backend/tests/test_patient_portal_endpoints.py
git commit -m "feat(portal-p5): POST /self-report/labs + /self-report/hospital-preop"
```

---

## Task 4: GET /clearance/template

**Files:**
- Modify: `backend/app/routers/patient_portal.py`
- Modify: `backend/tests/test_patient_portal_endpoints.py`

- [ ] **Step 1: Failing tests** — append:

```python
def test_clearance_template_404_when_not_required(client, db):
    from app.services.patient_portal_auth import issue_portal_token
    s = _seed_surgery(db)
    s.clearance_required = False
    db.commit()
    token = issue_portal_token(s)
    r = client.get(f"/api/patient/portal/{s.id}/clearance/template",
                      headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 409
    assert "clearance" in r.text.lower()


def test_clearance_template_streams_when_present(client, db):
    from unittest.mock import patch
    from app.services.patient_portal_auth import issue_portal_token
    s = _seed_surgery(db)
    s.clearance_required = True
    db.commit()
    token = issue_portal_token(s)
    with patch("app.services.surgery_uploads.stream_static_pdf",
                return_value=b"%PDF-clearance-blank"):
        r = client.get(f"/api/patient/portal/{s.id}/clearance/template",
                          headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.content.startswith(b"%PDF")
    assert "pdf" in r.headers["content-type"].lower()


def test_clearance_template_404_when_object_missing(client, db):
    from unittest.mock import patch
    from app.services.patient_portal_auth import issue_portal_token
    s = _seed_surgery(db)
    s.clearance_required = True
    db.commit()
    token = issue_portal_token(s)
    with patch("app.services.surgery_uploads.stream_static_pdf",
                return_value=None):
        r = client.get(f"/api/patient/portal/{s.id}/clearance/template",
                          headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 404
    assert "online" in r.text.lower() or "available" in r.text.lower()
```

- [ ] **Step 2: Run, confirm fail.**

- [ ] **Step 3: Add handler** to `backend/app/routers/patient_portal.py`:

```python
# ─── /{surgery_id}/clearance/* ─────────────────────────────────────

@router.get("/{surgery_id}/clearance/template")
def portal_clearance_template(
    surgery_id: str,
    db: Session = Depends(get_db),
    _: str = Depends(require_portal_token),
):
    """Stream the blank clearance template PDF from GCS. Gated on
    clearance_required."""
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="surgery not found")
    if not s.clearance_required:
        raise HTTPException(
            status_code=409,
            detail="Clearance isn't required for this surgery.",
        )
    from app.services.surgery_uploads import stream_static_pdf
    pdf_bytes = stream_static_pdf("clearance/template.pdf")
    if pdf_bytes is None:
        raise HTTPException(
            status_code=404,
            detail="The clearance template isn't online yet — please call "
                   "our office at 240-252-2140.",
        )
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition":
                'attachment; filename="wwc_clearance_template.pdf"',
        },
    )
```

- [ ] **Step 4: Run, confirm pass.**

- [ ] **Step 5: Commit.**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/routers/patient_portal.py backend/tests/test_patient_portal_endpoints.py
git commit -m "feat(portal-p5): GET /clearance/template — stream blank template from GCS"
```

---

## Task 5: POST /clearance/upload (multipart)

**Files:**
- Modify: `backend/app/routers/patient_portal.py`
- Modify: `backend/tests/test_patient_portal_endpoints.py`

- [ ] **Step 1: Failing tests** — append:

```python
def test_clearance_upload_writes_and_marks_status(client, db):
    from unittest.mock import patch, MagicMock
    from app.services.patient_portal_auth import issue_portal_token
    s = _seed_surgery(db)
    s.clearance_required = True
    db.commit()
    token = issue_portal_token(s)
    pdf_bytes = b"%PDF-1.4\nfake-clearance"
    with patch("app.services.surgery_uploads.storage.Client") as MockClient:
        blob = MagicMock()
        MockClient.return_value.bucket.return_value.blob.return_value = blob
        r = client.post(
            f"/api/patient/portal/{s.id}/clearance/upload",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": ("clearance.pdf", pdf_bytes, "application/pdf")},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["filename"] == "clearance.pdf"
    assert body["kind"] == "clearance"
    db.refresh(s)
    assert s.clearance_status == "uploaded"


def test_clearance_upload_rejects_oversize(client, db):
    from app.services.patient_portal_auth import issue_portal_token
    s = _seed_surgery(db)
    s.clearance_required = True
    db.commit()
    token = issue_portal_token(s)
    huge = b"%PDF-1.4\n" + b"x" * (10 * 1024 * 1024 + 1)
    r = client.post(
        f"/api/patient/portal/{s.id}/clearance/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("big.pdf", huge, "application/pdf")},
    )
    assert r.status_code == 413


def test_clearance_upload_rejects_wrong_mime(client, db):
    from app.services.patient_portal_auth import issue_portal_token
    s = _seed_surgery(db)
    s.clearance_required = True
    db.commit()
    token = issue_portal_token(s)
    r = client.post(
        f"/api/patient/portal/{s.id}/clearance/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("doc.txt", b"plain text", "text/plain")},
    )
    assert r.status_code == 415
```

- [ ] **Step 2: Run, confirm fail.**

- [ ] **Step 3: Add handler** to `backend/app/routers/patient_portal.py`:

```python
from fastapi import File, UploadFile, Form


@router.post("/{surgery_id}/clearance/upload")
async def portal_clearance_upload(
    surgery_id: str,
    file: UploadFile = File(...),
    kind: str = Form("clearance"),
    db: Session = Depends(get_db),
    _: str = Depends(require_portal_token),
):
    """Accept a multipart upload of the patient's completed clearance form
    or EKG. kind defaults to 'clearance'; pass 'ekg' for EKG uploads."""
    if kind not in ("clearance", "ekg"):
        raise HTTPException(status_code=422,
                              detail="kind must be 'clearance' or 'ekg'")
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="surgery not found")
    contents = await file.read()
    from app.services.surgery_uploads import store_upload, UploadError
    try:
        doc = store_upload(
            db, s, kind=kind,
            filename=file.filename or "upload",
            file_bytes=contents,
            content_type=file.content_type or "application/octet-stream",
            uploaded_by="patient:portal",
        )
    except UploadError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    # Move clearance_status forward if it was "required"; don't downgrade
    # "approved" rows.
    if (s.clearance_status or "") in ("required", "not_required", ""):
        s.clearance_status = "uploaded"
        db.commit()
    return {
        "id":           str(doc.id),
        "kind":         doc.kind,
        "filename":     doc.filename,
        "uploaded_at":  doc.uploaded_at.isoformat(),
        "clearance_status": s.clearance_status,
    }
```

- [ ] **Step 4: Run, confirm pass.**

- [ ] **Step 5: Commit.**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/routers/patient_portal.py backend/tests/test_patient_portal_endpoints.py
git commit -m "feat(portal-p5): POST /clearance/upload — multipart, MIME-checked, sets clearance_status"
```

---

## Task 6: GET /uploads — patient's uploaded files

**Files:**
- Modify: `backend/app/routers/patient_portal.py`
- Modify: `backend/tests/test_patient_portal_endpoints.py`

- [ ] **Step 1: Failing tests** — append:

```python
def test_uploads_returns_patient_documents_with_signed_urls(client, db):
    from unittest.mock import patch
    from app.services.patient_portal_auth import issue_portal_token
    from app.models.surgery import SurgeryDocument
    s = _seed_surgery(db)
    db.commit(); db.refresh(s)
    db.add(SurgeryDocument(
        surgery_id=s.id, kind="clearance",
        filename="my_clearance.pdf",
        gcs_path=f"surgery-uploads/{s.id}/clearance/x.pdf",
        content_type="application/pdf",
        uploaded_by="patient:portal",
    ))
    db.commit()
    token = issue_portal_token(s)
    with patch("app.services.surgery_uploads.signed_download_url",
                return_value="https://signed.example/x"):
        r = client.get(f"/api/patient/portal/{s.id}/uploads",
                          headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert len(body["uploads"]) == 1
    u = body["uploads"][0]
    assert u["filename"] == "my_clearance.pdf"
    assert u["kind"] == "clearance"
    assert u["download_url"].startswith("https://signed.example/")


def test_uploads_empty_list(client, db):
    from app.services.patient_portal_auth import issue_portal_token
    s = _seed_surgery(db)
    db.commit()
    token = issue_portal_token(s)
    r = client.get(f"/api/patient/portal/{s.id}/uploads",
                      headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["uploads"] == []
```

- [ ] **Step 2: Run, confirm fail.**

- [ ] **Step 3: Add handler** to `backend/app/routers/patient_portal.py`:

```python
# ─── /{surgery_id}/uploads ────────────────────────────────────────

@router.get("/{surgery_id}/uploads")
def portal_uploads(surgery_id: str, db: Session = Depends(get_db),
                       _: str = Depends(require_portal_token)):
    """Return the patient's uploaded documents with fresh 5-minute
    signed-URL downloads."""
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="surgery not found")
    from app.services.surgery_uploads import signed_download_url
    docs = []
    for d in (s.documents or []):
        try:
            url = signed_download_url(d, ttl_minutes=5)
        except Exception:
            url = None
        docs.append({
            "id":           str(d.id),
            "kind":         d.kind,
            "filename":     d.filename,
            "uploaded_at":  d.uploaded_at.isoformat() if d.uploaded_at else None,
            "size_bytes":   d.size_bytes,
            "content_type": d.content_type,
            "download_url": url,
        })
    return {"uploads": docs}
```

- [ ] **Step 4: Run, confirm pass.**

- [ ] **Step 5: Commit.**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/routers/patient_portal.py backend/tests/test_patient_portal_endpoints.py
git commit -m "feat(portal-p5): GET /uploads — patient's docs + 5-min signed URLs"
```

---

## Task 7: Frontend — Dashboard self-report buttons

**Files:**
- Modify: `frontend/src/pages/portal/Dashboard.jsx`

The existing Dashboard milestone list (from P1) renders rows with `key`, `label`, `status`. P5 adds a "Mark as done" button next to each `todo` row where `key` is `labs` or `hospital_preop`.

- [ ] **Step 1: Read the existing component** so you understand the milestone rendering pattern.

```bash
/usr/bin/grep -n "milestone\|status_badge\|status.*todo" \
  /Users/wwcclaudecode/Documents/wwc-era-project/frontend/src/pages/portal/Dashboard.jsx
```

- [ ] **Step 2: Add the mutation + button.** Locate the milestone `<ul>` render block. Above the export, add:

```jsx
function SelfReportButton({ sid, kind, onDone }) {
  const [busy, setBusy] = useState(false)
  async function click() {
    setBusy(true)
    try {
      const path = kind === 'labs'
        ? `/${sid}/self-report/labs`
        : `/${sid}/self-report/hospital-preop`
      await portalApi.post(path)
      onDone?.()
    } finally { setBusy(false) }
  }
  return (
    <button onClick={click} disabled={busy}
             className="btn-primary text-xs ml-2">
      {busy ? 'Saving…' : 'Mark as done'}
    </button>
  )
}
```

Then inside the milestone `<li>` render (where each row shows label + status badge), conditionally render the button:

```jsx
{(m.key === 'labs' || m.key === 'hospital_preop') && m.status === 'todo' && (
  <SelfReportButton sid={sid} kind={m.key}
    onDone={() => qc.invalidateQueries({ queryKey: ['portal-dashboard', sid] })} />
)}
```

You'll need to import `useState` (if not already) and use the existing `useQueryClient` for `qc`. Use the same TanStack Query invalidate pattern already in the file.

- [ ] **Step 3: Build check.**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/frontend && npm run build 2>&1 | tail -6
```

- [ ] **Step 4: Commit.**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add frontend/src/pages/portal/Dashboard.jsx
git commit -m "feat(portal-p5): Dashboard 'Mark as done' for labs + hospital pre-op milestones"
```

---

## Task 8: Frontend — Clearance card on Documents page

**Files:**
- Modify: `frontend/src/pages/portal/Documents.jsx`
- Read first: `frontend/src/lib/portal-api.js` so you understand the axios client

Documents page currently has three cards (Instructions, Consents, Receipts). Add a fourth card — Clearance — that appears only when the surgery requires clearance. Use a second TanStack Query against `/uploads` to fetch the patient's uploaded docs.

- [ ] **Step 1: Add a separate query** in the `Documents` component for clearance state. The simplest path: a new endpoint isn't needed — the surgery's `clearance_required` + `clearance_status` are already returned by the dashboard query. But to avoid coupling Documents to Dashboard, just add the fields you need to the existing `GET /documents` response.

Update T2's `portal_documents` handler in `backend/app/routers/patient_portal.py` to also include:

```python
return {
    "instructions": instructions,
    "consents":     consents,
    "receipts":     receipts,
    "clearance": {
        "required": bool(s.clearance_required),
        "status":   s.clearance_status or "not_required",
    },
}
```

And update T2's tests to include a sanity assertion on the new `clearance` block. Run the existing P4 tests to confirm they don't break — they all assert membership of specific keys, not equality, so adding `clearance` is safe. Verify:

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && \
  ./venv/bin/pytest tests/test_patient_portal_endpoints.py -v -k "documents"
```

- [ ] **Step 2: Add the `ClearanceCard` component** to `Documents.jsx`. After the existing `ReceiptsCard`, add:

```jsx
function ClearanceCard({ sid, clearance, uploads, refetchUploads }) {
  const [file, setFile] = useState(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  if (!clearance?.required) return null   // hide entirely

  async function upload() {
    if (!file) return
    setBusy(true); setErr('')
    try {
      const form = new FormData()
      form.append('file', file)
      form.append('kind', 'clearance')
      await portalApi.post(`/${sid}/clearance/upload`, form, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      setFile(null)
      refetchUploads()
    } catch (e) {
      setErr(e?.response?.data?.detail || 'Upload failed.')
    } finally { setBusy(false) }
  }

  const statusBadge =
    clearance.status === 'approved'
      ? 'bg-green-100 text-green-700'
      : clearance.status === 'uploaded'
      ? 'bg-amber-100 text-amber-700'
      : 'bg-gray-200 text-gray-700'

  const myClearanceUploads = (uploads || []).filter(u =>
    u.kind === 'clearance' || u.kind === 'ekg'
  )

  return (
    <section className="bg-white rounded-lg shadow p-4 space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-gray-700">Clearance</h2>
        <span className={`text-xs px-2 py-1 rounded ${statusBadge}`}>
          {clearance.status}
        </span>
      </div>

      <div>
        <div className="text-xs text-gray-500 mb-1">
          Step 1: Download the blank template
        </div>
        <PdfDownloadButton
          url={`/${sid}/clearance/template`}
          filename="wwc_clearance_template.pdf"
          label="Download template" />
      </div>

      <div>
        <div className="text-xs text-gray-500 mb-1">
          Step 2: Upload your completed form or EKG (PDF, JPEG, PNG, HEIC, max 10 MB)
        </div>
        <div className="flex items-center gap-2">
          <input type="file"
                  accept="application/pdf,image/jpeg,image/png,image/heic"
                  onChange={e => setFile(e.target.files?.[0] || null)}
                  className="text-xs" />
          <button onClick={upload} disabled={!file || busy}
                   className="btn-primary text-sm">
            {busy ? 'Uploading…' : 'Upload'}
          </button>
        </div>
        {err && <div className="text-xs text-red-600 mt-1">{err}</div>}
      </div>

      {myClearanceUploads.length > 0 && (
        <div>
          <div className="text-xs text-gray-500 mb-1">Your uploads:</div>
          <ul className="text-sm">
            {myClearanceUploads.map(u => (
              <li key={u.id} className="flex items-center justify-between py-1">
                <span className="truncate mr-2">
                  {u.filename}
                  <span className="text-xs text-gray-500 ml-2">
                    {u.uploaded_at?.slice(0, 10)}
                  </span>
                </span>
                {u.download_url && (
                  <a href={u.download_url} target="_blank" rel="noreferrer"
                      className="btn-secondary text-xs">
                    Download
                  </a>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}
```

The signed-URL download is the one place we use a plain `<a href>` instead of `PdfDownloadButton` — because the URL is GCS-signed and bypasses the portal API, no Authorization header needed. The `target="_blank"` opens GCS directly.

- [ ] **Step 3: Wire the queries in `Documents` and render the new card.** Update the main `Documents` component to also query `/uploads`:

```jsx
const { data: uploadsData, refetch: refetchUploads } = useQuery({
  queryKey: ['portal-uploads', sid],
  queryFn: () => portalApi.get(`/${sid}/uploads`).then(r => r.data),
  staleTime: 30_000,
})

// ...
return (
  <div className="space-y-4">
    <h1 className="text-2xl font-semibold text-gray-900">Documents</h1>
    <InstructionsCard sid={sid} instructions={data.instructions} />
    <ConsentDocsCard sid={sid} consents={data.consents} />
    <ReceiptsCard receipts={data.receipts} />
    <ClearanceCard sid={sid}
                       clearance={data.clearance}
                       uploads={uploadsData?.uploads}
                       refetchUploads={refetchUploads} />
  </div>
)
```

- [ ] **Step 4: Build check.**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/frontend && npm run build 2>&1 | tail -6
```

- [ ] **Step 5: Commit.**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add frontend/src/pages/portal/Documents.jsx backend/app/routers/patient_portal.py
git commit -m "feat(portal-p5): Documents page Clearance card + GET /documents returns clearance state"
```

---

## Task 9: Smoke test in prod (manual)

Done after Tasks 1–8 are merged and deployed. I drive this.

- [ ] **Step 1: Grant the backend SA `objectCreator` on the bucket:**

```bash
gcloud storage buckets add-iam-policy-binding gs://wwc-documents \
  --project=wwc-solutions \
  --member=serviceAccount:backend@wwc-solutions.iam.gserviceaccount.com \
  --role=roles/storage.objectCreator
```

- [ ] **Step 2: Upload a sample clearance template** so the download endpoint returns content:

```bash
printf '%%PDF-1.4\nfake clearance template\n%%EOF\n' > /tmp/clearance_template.pdf
gcloud storage cp /tmp/clearance_template.pdf \
  gs://wwc-documents/clearance/template.pdf
```

- [ ] **Step 3: Push, build, deploy** backend `v45` + frontend `v_portal_p5`. Run the migration:

```bash
DATABASE_URL='postgresql+psycopg2://...' \
  ./venv/bin/python scripts/migrate_patient_portal_p5.py
```

- [ ] **Step 4: Insert a test surgery** with `procedure_classification="office_d_and_c"`, `clearance_required=True`, `version_id=1`. Surgery should have `labs_self_reported=False` and `hospital_preop_self_reported=False` so the dashboard CTAs show.

- [ ] **Step 5: Portal sign-in.** Confirm Documents nav is still live (it shipped in P4).

- [ ] **Step 6: Hit GET /documents.** Confirm the response includes a `clearance: {required: true, status: "not_required"}` (or similar) block.

- [ ] **Step 7: Hit GET /clearance/template.** Confirm it streams the sample template PDF.

- [ ] **Step 8: Upload a small PDF** via POST /clearance/upload (multipart curl with a real ~1KB PDF). Confirm:
  - 200 response
  - `clearance_status` flips to "uploaded"
  - GCS object lands at `gs://wwc-documents/surgery-uploads/<sid>/clearance/<ts>_<name>.pdf`
  - GET /uploads returns the row with a signed URL

- [ ] **Step 9: Try POSTing a fake JPEG with `Content-Type: application/pdf`** — should 415 (magic-byte sniff catches the mismatch).

- [ ] **Step 10: Hit POST /self-report/labs.** Confirm 200, then GET /portal/.../dashboard — the labs milestone row should now show `status="done"`.

- [ ] **Step 11: Hit POST /self-report/hospital-preop.** Same verification.

- [ ] **Step 12: Cleanup** — delete test surgery (cascade drops SurgeryDocument rows), delete sample clearance template from GCS, delete the uploaded test file from GCS, close Cloud SQL public IP.
