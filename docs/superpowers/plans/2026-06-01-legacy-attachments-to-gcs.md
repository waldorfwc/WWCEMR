# Migrate Legacy Attachments to GCS — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development.

**Goal:** Move six broken file-serving surfaces off ephemeral Cloud Run local disk and onto GCS. New uploads land in `gs://wwc-app-docs/<feature>/`, downloads serve from the same. A one-time migration script copies historical files from the Mac Mini's local disk to GCS and rewrites DB rows.

**Architecture:** Add a `save_blob()` helper to `app/services/storage.py` (symmetric with the existing `serve_blob()`). Each upload handler uses `save_blob()` and stores the returned GCS key in DB. Each download handler detects whether the DB path is a GCS key (relative, no leading slash) or a legacy local path (starts with `/`) and serves accordingly — legacy paths 404 in prod until the migration runs.

**Spec:** This document doubles as spec — the design is mechanical refactor.

**Tech Stack:** Same as everywhere else. New code in `app/services/storage.py` + the six routers. Migration script in `backend/scripts/`.

**Key facts (don't relitigate):**
- `serve_blob()` already supports both backends via `STORAGE_BACKEND` env var. Reusing.
- `STORAGE_BACKEND=gcs` is already set on prod Cloud Run.
- Legacy local paths all start with `/Users/wwcclaudecode/...` or `/Volumes/OWC External/...` — leading slash is sufficient discriminator.
- GCS keys never start with `/` and have a `<prefix>/<uuid>.<ext>` shape.
- Bucket `wwc-app-docs` is the canonical home (same as billing-docs + extracted + intake).

**Per-feature GCS prefixes:**

| Feature | DB column | GCS prefix |
|---|---|---|
| Pellet attachments | `storage_path` | `pellet-attachments/` |
| Surgery files | `path` | `surgery-files/` |
| Active claim documents | `file_path` | `active-ar-docs/` |
| Appeal letter PDFs | `pdf_path` | `appeal-letters/` |
| Bank recon BAI2 | `bai2_path` | `bank-recon/` |
| Waystar reports | (filename, no DB col) | `waystar-reports/` |

---

## Task 1: Add `save_blob()` to storage adapter

**Files:**
- Modify: `backend/app/services/storage.py`
- Test: `backend/tests/test_storage_save_blob.py` (new)

`serve_blob()` already exists and handles both backends. Add the upload half.

- [ ] **Step 1: Failing tests:**

```python
"""storage.save_blob — upload bytes to GCS or local."""
from unittest.mock import patch, MagicMock


def test_save_blob_gcs_returns_prefixed_key(monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "gcs")
    # Reload to pick up the env var
    import importlib
    from app.services import storage as s
    importlib.reload(s)

    fake_blob = MagicMock()
    fake_bucket = MagicMock()
    fake_bucket.blob.return_value = fake_blob
    fake_client = MagicMock()
    fake_client.bucket.return_value = fake_bucket
    with patch.object(s, "_gcs_client", return_value=fake_client):
        key = s.save_blob(prefix="pellet-attachments",
                              body=b"%PDF-1.4 test",
                              filename="invoice.pdf")

    assert key.startswith("pellet-attachments/")
    assert key.endswith(".pdf")
    fake_blob.upload_from_string.assert_called_once()


def test_save_blob_gcs_no_extension_when_no_filename(monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "gcs")
    import importlib
    from app.services import storage as s
    importlib.reload(s)
    fake_blob = MagicMock()
    fake_bucket = MagicMock()
    fake_bucket.blob.return_value = fake_blob
    fake_client = MagicMock()
    fake_client.bucket.return_value = fake_bucket
    with patch.object(s, "_gcs_client", return_value=fake_client):
        key = s.save_blob(prefix="x", body=b"raw")
    assert "/" in key
    assert key.startswith("x/")


def test_save_blob_local_writes_to_disk(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("DOCUMENTS_LOCAL_ROOT", str(tmp_path))
    import importlib
    from app.services import storage as s
    importlib.reload(s)
    key = s.save_blob(prefix="x", body=b"hello", filename="a.txt")
    assert key.startswith("x/")
    assert key.endswith(".txt")
    # File should exist at tmp_path/key
    assert (tmp_path / key).read_bytes() == b"hello"


def test_is_legacy_local_path():
    from app.services.storage import is_legacy_local_path
    assert is_legacy_local_path("/Users/wwcclaudecode/foo.pdf")
    assert is_legacy_local_path("/Volumes/OWC External/x.pdf")
    assert not is_legacy_local_path("pellet-attachments/abc.pdf")
    assert not is_legacy_local_path("")
    assert not is_legacy_local_path(None)
```

- [ ] **Step 2: Run, confirm fail.**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && \
  ./venv/bin/pytest tests/test_storage_save_blob.py -v
```

- [ ] **Step 3: Implementation.** Append to `app/services/storage.py`:

```python
import mimetypes
import uuid
from pathlib import Path


def save_blob(*, prefix: str, body: bytes,
                filename: str = "") -> str:
    """Persist bytes to storage (GCS or local) and return the storage key.
    Caller stores the returned key in DB. Format: `{prefix}/{uuid}{.ext}`.
    """
    safe_ext = ""
    if filename and "." in filename:
        safe_ext = "." + filename.rsplit(".", 1)[-1].lower()[:10]
    key = f"{prefix}/{uuid.uuid4().hex}{safe_ext}"

    if _STORAGE_BACKEND == "gcs":
        client = _gcs_client()
        content_type = (mimetypes.guess_type(filename)[0]
                          if filename else "application/octet-stream")
        blob = client.bucket(_GCS_BUCKET).blob(key)
        blob.upload_from_string(body, content_type=content_type)
        return key

    root = Path(os.environ.get("DOCUMENTS_LOCAL_ROOT", "/var/data/wwc-docs"))
    out = root / key
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(body)
    return key


def is_legacy_local_path(path: Optional[str]) -> bool:
    """True if `path` looks like a pre-migration absolute filesystem path
    rather than a GCS object key. GCS keys never start with `/`."""
    return bool(path) and path.startswith("/")
```

- [ ] **Step 4: Run, confirm pass + commit.**

```bash
git add backend/app/services/storage.py backend/tests/test_storage_save_blob.py
git commit -m "feat(storage): add save_blob + is_legacy_local_path helpers"
```

---

## Task 2: Pellet attachments (3 endpoint pairs)

**Files:**
- Modify: `backend/app/routers/pellet.py`
- Test: `backend/tests/test_pellet_attachments.py` (new — focused tests)

The 3 attachment kinds (order, receipt, disposal) share the model `PelletAttachment` with `storage_path`. The 3 download endpoints (`pellet.py:1168, 1477, 2257`) all return `FileResponse(att.storage_path)`. The 3 upload handlers all write to a local path.

- [ ] **Step 1: Failing tests** in `tests/test_pellet_attachments.py`:

```python
"""Pellet attachment uploads + downloads use GCS via save_blob/serve_blob."""
from unittest.mock import patch
from io import BytesIO


def test_pellet_order_attachment_upload_stores_gcs_key(client, db,
                                                            monkeypatch):
    """Upload a fake PDF, verify the DB row has a GCS key (no leading '/')."""
    monkeypatch.setenv("STORAGE_BACKEND", "gcs")
    # Seed an order to attach to (smallest viable shape)
    from app.models.pellet import PelletOrder
    o = PelletOrder(...minimal fields...)
    db.add(o); db.commit(); db.refresh(o)
    with patch("app.services.storage.save_blob",
                return_value="pellet-attachments/abc123.pdf"):
        r = client.post(
            f"/api/pellets/orders/{o.id}/attachments",
            files={"file": ("invoice.pdf", b"%PDF-1.4 x", "application/pdf")},
        )
    assert r.status_code == 200, r.text
    # DB row should record the returned key, not a local path
    from app.models.pellet import PelletAttachment
    att = db.query(PelletAttachment).first()
    assert att.storage_path == "pellet-attachments/abc123.pdf"


def test_pellet_attachment_download_serves_via_gcs_when_key(client, db,
                                                                 monkeypatch):
    """Row with a GCS key → serve_blob streams from bucket."""
    monkeypatch.setenv("STORAGE_BACKEND", "gcs")
    # Seed a row pointing at a GCS key
    from app.models.pellet import PelletOrder, PelletAttachment
    o = PelletOrder(...); db.add(o); db.commit(); db.refresh(o)
    att = PelletAttachment(order_id=o.id, filename="x.pdf",
                                storage_path="pellet-attachments/exists.pdf",
                                content_type="application/pdf")
    db.add(att); db.commit(); db.refresh(att)
    # Mock serve_blob
    with patch("app.routers.pellet.serve_blob") as mock_serve:
        from fastapi.responses import Response
        mock_serve.return_value = Response(content=b"%PDF-1.4 ok",
                                              media_type="application/pdf")
        r = client.get(f"/api/pellets/orders/{o.id}/attachments/{att.id}")
    assert r.status_code == 200
    _, kwargs = mock_serve.call_args
    assert kwargs["gcs_object"] == "pellet-attachments/exists.pdf"


def test_pellet_attachment_download_404s_legacy_local_path(client, db):
    """Row with a legacy local path → 404 'file not migrated'."""
    from app.models.pellet import PelletOrder, PelletAttachment
    o = PelletOrder(...); db.add(o); db.commit(); db.refresh(o)
    att = PelletAttachment(order_id=o.id, filename="x.pdf",
                                storage_path="/Users/wwcclaudecode/x.pdf",
                                content_type="application/pdf")
    db.add(att); db.commit(); db.refresh(att)
    r = client.get(f"/api/pellets/orders/{o.id}/attachments/{att.id}")
    assert r.status_code == 410
    assert "migrate" in r.json()["detail"].lower() or "available" in r.json()["detail"].lower()
```

(Fill in `…minimal fields…` based on the actual PelletOrder model. Use `smart_outline` or grep to find required fields.)

- [ ] **Step 2: Run, confirm fail.**

- [ ] **Step 3: Refactor upload handlers** (3 spots — `pellet.py:1109`, `pellet.py:~1417`, and the disposal one near `pellet.py:~2200`):

Each gets a per-call edit. The shape becomes:

```python
from app.services.storage import save_blob, serve_blob, using_gcs, is_legacy_local_path

# upload
@router.post("/orders/{order_id}/attachments")
async def upload_order_attachment(...):
    contents = await file.read()
    key = save_blob(prefix="pellet-attachments",
                       body=contents,
                       filename=file.filename or "upload")
    att = PelletAttachment(
        order_id=order_id,
        filename=file.filename,
        storage_path=key,
        content_type=file.content_type,
        size_bytes=len(contents),
    )
    db.add(att); db.commit(); db.refresh(att)
    return _att_dict(att)
```

- [ ] **Step 4: Refactor download handlers** (3 spots — `pellet.py:1158`, `pellet.py:1477`, `pellet.py:2257`):

```python
@router.get("/orders/{order_id}/attachments/{att_id}")
def download_order_attachment(order_id: str, att_id: str, ...):
    att = db.query(PelletAttachment).filter(...).first()
    if not att:
        raise HTTPException(status_code=404, detail="attachment not found")
    if is_legacy_local_path(att.storage_path):
        raise HTTPException(status_code=410,
                              detail="File is from before the cloud migration "
                                     "and is no longer available.")
    return serve_blob(
        local_path=None,
        gcs_object=att.storage_path,
        media_type=att.content_type or "application/octet-stream",
        filename=att.filename,
        disposition="attachment",
    )
```

Refactor all 3 sites identically.

- [ ] **Step 5: Run, confirm pass + commit.**

```bash
git add backend/app/routers/pellet.py backend/tests/test_pellet_attachments.py
git commit -m "fix(pellet): attachments use GCS via storage adapter"
```

---

## Task 3: Surgery files

**Files:**
- Modify: `backend/app/routers/surgery.py`
- Locate the matching upload handler (grep `SurgeryFile(` or `path=` near upload paths in surgery.py)

Same shape as T2. Upload → `save_blob(prefix="surgery-files", …)` → stores key in `SurgeryFile.path`. Download → `is_legacy_local_path()` check + `serve_blob()`.

- [ ] **Step 1: Find the upload handler.**

```bash
/usr/bin/grep -n "SurgeryFile(\|UploadFile\|@router.post.*files\|async def.*upload" \
  backend/app/routers/surgery.py | /usr/bin/head -10
```

- [ ] **Step 2: Failing tests + refactor uploads + downloads** identically to T2 pattern.

- [ ] **Step 3: Commit.**

```bash
git commit -m "fix(surgery): file attachments use GCS via storage adapter"
```

---

## Task 4: Active AR claim docs + appeal letters

**Files:**
- Modify: `backend/app/routers/active_ar.py`
- Tests for both endpoints

Two related but separate flows:

**Claim docs** (`active_ar.py:869` download, plus a sibling upload handler):
- prefix: `active-ar-docs/`
- DB column: `ActiveClaimDocument.file_path`

**Appeal letters** (`active_ar.py:1485`):
- prefix: `appeal-letters/`
- DB column: `AppealLetter.pdf_path`
- Appeals are GENERATED, not uploaded. The generator function currently writes to local disk — needs to switch to `save_blob()`.

- [ ] Locate the appeal generator (look for `pdf_path =` or `AppealLetter(` near a generation function).
- [ ] Refactor each pair following T2's shape.
- [ ] Commit each separately for clean history.

---

## Task 5: Bank recon BAI2 + Waystar reports

**Files:**
- Modify: `backend/app/routers/bank_recon.py`
- Modify: `backend/app/routers/waystar.py`

**Bank recon** (`bank_recon.py:347`):
- prefix: `bank-recon/`
- DB column: `Bai2Import.bai2_path`
- BAI2 files arrive via import; storage location is in the import flow.

**Waystar reports** (`waystar.py:196`):
- prefix: `waystar-reports/`
- No DB column — the endpoint reads from a hardcoded `uploads/waystar_reports/<filename>` path. The report generator (separate flow) needs to also write to GCS.
- Simpler fix: pass the filename through and resolve via `serve_blob(gcs_object="waystar-reports/" + safe_name, ...)`.

- [ ] Locate the import + generator handlers.
- [ ] Refactor.
- [ ] Tests + commit.

---

## Task 6: Legacy data migration script

**Files:**
- Create: `backend/scripts/migrate_legacy_attachments_to_gcs.py`

The script runs on the Mac Mini (where the legacy files exist). For each of the 6 tables, it:

1. Selects rows with `storage_path` (or equivalent column) starting with `/`
2. Checks if the local file exists at that path
3. Uploads bytes to GCS under the appropriate prefix
4. Updates the DB row with the new GCS key
5. Logs the result + count of unrecoverable rows (file missing on disk)

```python
"""Backfill legacy local-path attachments into GCS.

Run on the Mac Mini, with the external drive mounted and access to prod
Cloud SQL. Updates these tables in place:
  - pellet_attachments         (storage_path → pellet-attachments/)
  - surgery_files              (path → surgery-files/)
  - active_claim_documents     (file_path → active-ar-docs/)
  - appeal_letters             (pdf_path → appeal-letters/)
  - bai2_imports               (bai2_path → bank-recon/)
  - (waystar reports — flat directory, no DB col — copy as-is)

Usage:
    DATABASE_URL='postgresql+psycopg2://...' \
        ./venv/bin/python scripts/migrate_legacy_attachments_to_gcs.py
"""
import os, sys, uuid, mimetypes
from pathlib import Path
from sqlalchemy import create_engine, text
from google.cloud import storage as gcs

BUCKET = os.environ.get("DOCUMENTS_GCS_BUCKET", "wwc-app-docs")

PLAN = [
    {"table": "pellet_attachments", "path_col": "storage_path",
     "gcs_prefix": "pellet-attachments"},
    {"table": "surgery_files",      "path_col": "path",
     "gcs_prefix": "surgery-files"},
    {"table": "active_claim_documents", "path_col": "file_path",
     "gcs_prefix": "active-ar-docs"},
    {"table": "appeal_letters",     "path_col": "pdf_path",
     "gcs_prefix": "appeal-letters"},
    {"table": "bai2_imports",       "path_col": "bai2_path",
     "gcs_prefix": "bank-recon"},
]


def main():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL not set", file=sys.stderr); sys.exit(2)
    eng = create_engine(db_url)
    client = gcs.Client()
    bucket = client.bucket(BUCKET)

    for entry in PLAN:
        table, col, prefix = entry["table"], entry["path_col"], entry["gcs_prefix"]
        print(f"=== {table} ===")
        with eng.begin() as conn:
            rows = conn.execute(text(
                f"SELECT id, {col} FROM {table} "
                f"WHERE {col} LIKE '/%'"
            )).fetchall()
            print(f"  {len(rows)} legacy rows to inspect")
            ok = missing = 0
            for r_id, local_path in rows:
                p = Path(local_path)
                if not p.exists():
                    missing += 1
                    continue
                ext = p.suffix.lower()[:10]
                key = f"{prefix}/{uuid.uuid4().hex}{ext}"
                ct = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
                bucket.blob(key).upload_from_filename(str(p), content_type=ct)
                conn.execute(text(
                    f"UPDATE {table} SET {col} = :key WHERE id = :id"
                ), {"key": key, "id": r_id})
                ok += 1
            print(f"  migrated {ok}, missing {missing}")

    print("\nDone.")


if __name__ == "__main__":
    main()
```

- [ ] Write the script.
- [ ] DO NOT run yet — that's part of T8.
- [ ] Commit.

---

## Task 7: Deploy backend v50

- [ ] Build + deploy backend `v50`. (No frontend changes in this work.)

```bash
cd backend && gcloud builds submit --project=wwc-solutions --region=us-east4 \
  --tag=us-east4-docker.pkg.dev/wwc-solutions/app/backend:v50 .
gcloud run deploy backend --project=wwc-solutions --region=us-east4 \
  --image=us-east4-docker.pkg.dev/wwc-solutions/app/backend:v50 --quiet
```

---

## Task 8: Smoke test + migration

- [ ] **Step 1: Run the migration on the Mac Mini** (Cloud SQL public IP needs to be open):

```bash
DATABASE_URL='postgresql+psycopg2://postgres:...@<ip>:5432/wwc_app?sslmode=require' \
  ./venv/bin/python scripts/migrate_legacy_attachments_to_gcs.py
```

Expected output: per-table counts of migrated vs. missing rows.

- [ ] **Step 2: Spot-check each surface** in the staff UI:

For each (pellet, surgery file, active-AR doc, appeal letter, bank recon, waystar):
- Upload a fresh test file → DB row has a non-slash key
- Click the download → PDF/bytes returned 200
- Try downloading a known legacy row → 410 "file is from before the cloud migration" (if its file was missing on Mac Mini)

- [ ] **Step 3: Cleanup test uploads** (delete the rows + GCS objects).

- [ ] **Step 4: Mark complete.**
