# Pellet Patient Portal — Phase 1 (Requirements + Portal) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up a patient-facing pellet portal where a patient logs in, sees a requirement checklist, uploads a mammogram, self-reports labs, and signs a 1-year insertion consent — with a staff patient-action feed that lets a coordinator verify each item.

**Architecture:** A decoupled pellet portal mirroring the surgery portal. New `patient_pellet.py` (patient JWT endpoints), extend `pellet.py` (staff feed + verify + config), new pellet models (`PelletConsent`, `PelletActivity`, plus a `portal_token_version` column and an auth-attempt table), a `record_pellet_activity` service, BoldSign consent send/webhook, and a `frontend/src/pages/pellet-portal/` page set mirroring `frontend/src/pages/portal/`.

**Tech Stack:** FastAPI + SQLAlchemy (SQLite test / Postgres prod), JWT portal tokens, Twilio SMS, BoldSign e-sign, React + react-query + axios, Tailwind. Spec: `docs/superpowers/specs/2026-06-16-pellet-patient-portal-design.md`.

**Branch:** `feat/pellet-portal-phase1` off `main`.

---

## Reference patterns (read before starting — copy these, don't reinvent)

- **Patient auth (login/verify/token/`require_portal_token`):** `backend/app/routers/patient_portal.py:151-270` and its auth helpers. The pellet versions mirror these but key off `PelletPatient` (DOB + phone last-4) and a `ppv` (pellet-portal-version) claim instead of surgery's `ptv`.
- **Activity feed:** model `backend/app/models/surgery_activity.py`; service `backend/app/services/surgery/activity.py:25` (`record_activity` — SAVEPOINT soft-fail); staff endpoints `backend/app/routers/surgery.py:1744-1824` (`/activity`, `/activity/unread-count`, `/activity/read-all`, `/activity/{id}/read`); frontend badge `frontend/src/components/surgery/SurgeryNav.jsx` `ActivityBadge`.
- **BoldSign consent:** service `backend/app/services/boldsign_envelopes.py` (`_create_envelope`, `send_consent_envelopes`, `select_template_id`); webhook `backend/app/routers/boldsign.py`.
- **Pellet model + config:** `backend/app/models/pellet.py` (`PelletPatient` line 447: `mammo_verified/mammo_date/mammo_result/mammo_verified_by/_at`, `labs_verified/labs_not_required/labs_date/labs_fsh/labs_tsh/labs_estradiol/labs_verified_by/_at`, relationships `mammos`,`labs`; `PelletPatientMammo`, `PelletPatientLab`, `PelletVisit`); config `backend/app/services/pellet/settings.py` (`PELLET_SETTINGS_DEFAULTS`, `cfg(db, key)`).
- **Lightweight migrations:** `backend/app/database.py` — append `(table, column, sqltype)` rows to the `needed` list (~line 128-473); register new model modules in the `init_db` import line (~line 39).
- **Permissions:** `backend/app/permissions/catalog.py` `Module.PELLETS`, `Tier.VIEW/WORK/MANAGE`; gate with `requires_tier(Module.PELLETS, Tier.X)`.
- **Frontend portal structure:** `frontend/src/pages/portal/` (`PortalLogin.jsx`, `PortalVerify.jsx`, `PortalShell.jsx`, `Dashboard.jsx`, `Consent.jsx`, `Documents.jsx`). Token storage: `sessionStorage.getItem('patient-token-${id}')`, sent as `Authorization: Bearer`.
- **Storage:** `backend/app/services/storage.py` `save_blob`/`serve_blob`; needs `STORAGE_BACKEND=gcs` on Cloud Run.
- **Test fixtures:** `backend/tests/conftest.py` `client`, `db` (super-admin staff). For patient-token tests, mint a token via the pellet auth helper directly and pass `Authorization: Bearer`.

**Conventions:** dates MM/DD/YYYY (`strftime("%m/%d/%Y")` / `fmt.date`), titles Title Case, money ≤ $50k guard, no secrets in source, deploy with `--project=wwc-solutions --tag=...`.

### VERIFIED codebase facts (these OVERRIDE any conflicting snippet below)

Confirmed by reading the real source — use these exact names:

- **`PelletPatient` columns** (`backend/app/models/pellet.py:447`): single `patient_name` (String 160) — NOT first/last; `patient_dob` (Date), `patient_phone` (String 40), `patient_email` (String 255), `chart_number`. Plus the requirement flags `mammo_verified/mammo_date/mammo_result/mammo_verified_by/mammo_verified_at`, `labs_verified/labs_not_required/labs_date/.../labs_verified_by/labs_verified_at`. No `portal_token_version` yet (T1 adds it). Display name = `p.patient_name` (already "Last, First").
- **Staff pellet router prefix is `/pellets`** (plural) — `APIRouter(prefix="/pellets")`. So staff feed endpoints live at `/api/pellets/activity`, `/api/pellets/activity/unread-count`, `/api/pellets/activity/read-all`, `/api/pellets/activity/{id}/verify`, and config at `/api/pellets/config`. The **patient** router is new at `/pellet-portal`.
- **Auth lib:** `from jose import JWTError, jwt` (python-jose, NOT PyJWT). Sign/verify with **`settings.secret_key`** (NOT `jwt_secret_key`). Mirror `backend/app/services/patient_portal_auth.py` exactly (it uses `jose`, `settings.secret_key`, `bcrypt` for code hashing, `send_sms`). Reuse its **`send_sms`** import for the Twilio path.
- **`PelletPatientMammo` / `PelletPatientLab` have NO file column** and require NOT-NULL clinical fields (`mammo_date`+`result`; `labs_date`). They are STAFF-entry records. Therefore a patient upload does NOT write these tables. Instead: T1 adds a new **`PelletPortalUpload`** table for the patient's uploaded mammo file, and T1 adds two nullable timestamp columns to `PelletPatient` — `mammo_submitted_at`, `labs_self_reported_at` — to drive the "pending" checklist state. Staff still create the clinical `PelletPatientMammo`/`PelletPatientLab` rows during their own workflow; the portal's job is upload + attest + flip the verified flags via the feed.
- **`PelletConfig` PUT** only persists keys present in `PELLET_SETTINGS_DEFAULTS`; adding a key there + to `PelletConfigPayload` is sufficient.
- **Frontend portal pattern:** modern portal lives in `frontend/src/pages/portal/` with `frontend/src/lib/portal-api.js` (axios, `localStorage` key `wwc.portal.token`, baseURL `/api/patient/portal`, 401→redirect). Mirror this as `frontend/src/lib/pellet-portal-api.js` (key `wwc.pellet-portal.token`, baseURL `/api/pellet-portal`).

---

## File Structure

- Create `backend/app/models/pellet_portal.py` — `PelletConsent`, `PelletActivity`, `PelletPortalAuthAttempt`.
- Create `backend/app/services/pellet/portal_auth.py` — challenge/verify/token helpers (mirror surgery auth).
- Create `backend/app/services/pellet/activity.py` — `record_pellet_activity`.
- Create `backend/app/routers/patient_pellet.py` — patient-facing endpoints.
- Modify `backend/app/routers/pellet.py` — staff feed + verify check-off + new config keys.
- Modify `backend/app/services/pellet/settings.py` — new config defaults.
- Modify `backend/app/database.py` — register models + columns.
- Modify `backend/app/main.py` — include `patient_pellet` router.
- Modify `backend/app/services/boldsign_envelopes.py` — add a pellet-consent send path (or a thin pellet wrapper).
- Modify `backend/app/routers/boldsign.py` — webhook updates `PelletConsent`.
- Create `frontend/src/pages/pellet-portal/{PelletPortalLogin,PelletPortalVerify,PelletPortalShell,PelletDashboard,PelletMammo,PelletLabs,PelletConsent}.jsx`.
- Modify `frontend/src/routes.jsx` — patient pellet-portal routes (public, no staff auth).
- Modify `frontend/src/pages/Pellets.jsx` + nav — staff "Patient Activity" feed panel.
- Modify `frontend/src/pages/PelletSettings.jsx` — new config fields.
- Tests under `backend/tests/test_pellet_portal_*.py`.

---

## Task 1: Models — `PelletConsent`, `PelletActivity`, `PelletPortalAuthAttempt` + migrations

**Files:**
- Create: `backend/app/models/pellet_portal.py`
- Modify: `backend/app/database.py` (init_db import line + `needed` list)
- Test: `backend/tests/test_pellet_portal_models.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_pellet_portal_models.py
from datetime import date, timedelta
from app.utils.dt import now_utc_naive
from app.models.pellet import PelletPatient
from app.models.pellet_portal import PelletConsent, PelletActivity, PelletPortalAuthAttempt


def _patient(db):
    p = PelletPatient(patient_name="Doe, Jane", chart_number="MRN1",
                      patient_dob=date(1980, 5, 1), patient_phone="3015551234")
    db.add(p); db.commit(); db.refresh(p)
    return p


def test_consent_validity_window(db):
    p = _patient(db)
    signed = now_utc_naive()
    c = PelletConsent(pellet_patient_id=p.id, boldsign_envelope_id="env-1",
                      status="signed", signed_at=signed,
                      expires_at=signed + timedelta(days=365))
    db.add(c); db.commit(); db.refresh(c)
    assert c.is_valid is True
    c.expires_at = signed - timedelta(days=1)
    assert c.is_valid is False


def test_activity_row(db):
    p = _patient(db)
    a = PelletActivity(pellet_patient_id=p.id, kind="mammo_uploaded",
                       summary="Uploaded mammogram", actor="patient")
    db.add(a); db.commit(); db.refresh(a)
    assert a.read_at is None and a.actor == "patient"


def test_auth_attempt_row(db):
    p = _patient(db)
    att = PelletPortalAuthAttempt(pellet_patient_id=p.id, challenge_token="ct",
                                  code_hash="h", purpose="login")
    db.add(att); db.commit(); db.refresh(att)
    assert att.consumed_at is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && source venv/bin/activate && python -m pytest tests/test_pellet_portal_models.py -q`
Expected: FAIL — `ModuleNotFoundError: app.models.pellet_portal` (or `no such table` if column missing).

- [ ] **Step 3: Create the models**

```python
# backend/app/models/pellet_portal.py
"""Pellet patient-portal support models: insertion consent (1-yr validity),
the staff patient-action feed, and login-challenge throttling. Mirrors the
surgery portal's SurgeryActivity + auth-attempt patterns but keyed off
PelletPatient. (Pellet Patient Portal — Phase 1.)"""
from __future__ import annotations

from sqlalchemy import Column, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import relationship

from app.database import Base
from app.models.guid import GUID, new_uuid
from app.utils.dt import now_utc_naive


class PelletConsent(Base):
    __tablename__ = "pellet_consents"
    __table_args__ = (Index("ix_pellet_consent_patient", "pellet_patient_id"),)

    id = Column(GUID(), primary_key=True, default=new_uuid)
    pellet_patient_id = Column(GUID(), ForeignKey("pellet_patients.id", ondelete="CASCADE"),
                               nullable=False, index=True)
    boldsign_envelope_id = Column(String(120), nullable=True)
    template_id = Column(String(120), nullable=True)
    status = Column(String(20), nullable=False, default="sent")  # sent|signed|declined|expired
    signed_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)   # signed_at + 365d
    created_at = Column(DateTime, default=now_utc_naive, nullable=False)

    @property
    def is_valid(self) -> bool:
        return (self.status == "signed" and self.expires_at is not None
                and self.expires_at > now_utc_naive())


class PelletActivity(Base):
    __tablename__ = "pellet_activity"
    __table_args__ = (
        Index("ix_pellet_activity_patient", "pellet_patient_id"),
        Index("ix_pellet_activity_created", "created_at"),
    )

    id = Column(GUID(), primary_key=True, default=new_uuid)
    pellet_patient_id = Column(GUID(), ForeignKey("pellet_patients.id", ondelete="CASCADE"),
                               nullable=False, index=True)
    # mammo_uploaded | labs_self_reported | consent_signed | payment_made | booked
    kind = Column(String(40), nullable=False)
    summary = Column(String(300), nullable=False)
    actor = Column(String(20), nullable=False, default="patient")  # patient | system
    detail = Column(Text, nullable=True)            # optional JSON string
    created_at = Column(DateTime, default=now_utc_naive, nullable=False, index=True)
    handled_at = Column(DateTime, nullable=True)    # staff verified/cleared
    handled_by = Column(String(200), nullable=True)
    read_at = Column(DateTime, nullable=True)
    read_by = Column(String(200), nullable=True)


class PelletPortalAuthAttempt(Base):
    __tablename__ = "pellet_portal_auth_attempts"
    __table_args__ = (Index("ix_pellet_authattempt_token", "challenge_token"),)

    id = Column(GUID(), primary_key=True, default=new_uuid)
    pellet_patient_id = Column(GUID(), ForeignKey("pellet_patients.id", ondelete="CASCADE"),
                               nullable=False)
    challenge_token = Column(String(80), nullable=False)
    code_hash = Column(String(120), nullable=False)
    purpose = Column(String(20), nullable=False, default="login")
    attempts = Column(String(4), nullable=True)     # simple counter as string; or Integer
    created_at = Column(DateTime, default=now_utc_naive, nullable=False)
    expires_at = Column(DateTime, nullable=True)
    consumed_at = Column(DateTime, nullable=True)


class PelletPortalUpload(Base):
    """A file the PATIENT uploaded through the portal (the mammogram image/PDF).
    The clinical PelletPatientMammo/Lab tables are staff-entry with NOT-NULL
    fields and no file column, so patient uploads land here for staff review."""
    __tablename__ = "pellet_portal_uploads"
    __table_args__ = (Index("ix_pellet_upload_patient", "pellet_patient_id"),)

    id = Column(GUID(), primary_key=True, default=new_uuid)
    pellet_patient_id = Column(GUID(), ForeignKey("pellet_patients.id", ondelete="CASCADE"),
                               nullable=False, index=True)
    kind = Column(String(20), nullable=False, default="mammo")   # mammo
    filename = Column(String(255), nullable=True)
    storage_path = Column(Text, nullable=False)
    content_type = Column(String(100), nullable=True)
    uploaded_at = Column(DateTime, default=now_utc_naive, nullable=False)
```

- [ ] **Step 4: Register models + add the `portal_token_version` column**

In `backend/app/database.py`, add `pellet_portal` to the `init_db` model-import line (the long `from app.models import ...` list, ~line 39). Then append to the `needed` list (before its closing `]`, ~line 472):

```python
        # Pellet patient portal (Phase 1)
        ("pellet_patients", "portal_token_version", "INTEGER"),
        ("pellet_patients", "mammo_submitted_at",   "DATETIME"),
        ("pellet_patients", "labs_self_reported_at", "DATETIME"),
```

(The four new tables — `pellet_consents`, `pellet_activity`, `pellet_portal_auth_attempts`,
`pellet_portal_uploads` — auto-create via Base metadata once `pellet_portal` is imported in
`init_db`.)

- [ ] **Step 5: Run to verify pass**

Run: `cd backend && source venv/bin/activate && python -m pytest tests/test_pellet_portal_models.py -q`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add backend/app/models/pellet_portal.py backend/app/database.py backend/tests/test_pellet_portal_models.py
git commit -m "feat(pellet-portal): consent + activity + auth-attempt models (T1)"
```

---

## Task 2: Pellet portal auth service + endpoints (login / verify / token)

**Files:**
- Create: `backend/app/services/pellet/portal_auth.py`
- Create: `backend/app/routers/patient_pellet.py`
- Modify: `backend/app/main.py` (include router)
- Test: `backend/tests/test_pellet_portal_auth.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_pellet_portal_auth.py
from datetime import date
import pytest
from app.models.pellet import PelletPatient
from app.services.pellet import portal_auth


@pytest.fixture
def patient(db):
    p = PelletPatient(patient_name="Doe, Jane", chart_number="MRN1",
                      patient_dob=date(1980, 5, 1), patient_phone="3015551234")
    db.add(p); db.commit(); db.refresh(p)
    return p


def test_token_roundtrip(db, patient):
    tok = portal_auth.issue_portal_token(patient)
    claims = portal_auth.decode_portal_token(tok)
    assert claims["pellet_patient_id"] == str(patient.id)
    assert claims["ppv"] == (patient.portal_token_version or 0)


def test_login_then_verify(client, db, patient, monkeypatch):
    sent = {}
    monkeypatch.setattr(portal_auth, "_send_sms",
                        lambda phone, body: sent.update(phone=phone, body=body))
    r = client.post("/api/pellet-portal/login",
                    json={"dob": "1980-05-01", "last4": "1234"})
    assert r.status_code == 200, r.text
    ct = r.json()["challenge_token"]
    # The test code is the one we just "sent" — extract from sent body or a test hook.
    code = sent["body"].split()[-1]
    r2 = client.post("/api/pellet-portal/verify",
                     json={"challenge_token": ct, "code": code})
    assert r2.status_code == 200, r2.text
    assert "token" in r2.json()


def test_verify_bad_code(client, db, patient, monkeypatch):
    monkeypatch.setattr(portal_auth, "_send_sms", lambda *a, **k: None)
    ct = client.post("/api/pellet-portal/login",
                     json={"dob": "1980-05-01", "last4": "1234"}).json()["challenge_token"]
    r = client.post("/api/pellet-portal/verify",
                    json={"challenge_token": ct, "code": "000000"})
    assert r.status_code in (400, 401)
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && source venv/bin/activate && python -m pytest tests/test_pellet_portal_auth.py -q`
Expected: FAIL — `ModuleNotFoundError: app.services.pellet.portal_auth`.

- [ ] **Step 3: Implement the auth service**

Mirror `backend/app/routers/patient_portal.py` auth helpers. Create `backend/app/services/pellet/portal_auth.py`:

```python
"""Pellet portal auth: DOB+last4 login → SMS code challenge → JWT.
Mirrors the surgery portal auth (patient_portal.py) but keys off
PelletPatient and a `ppv` (pellet-portal-version) revocation claim."""
from __future__ import annotations

import hashlib
import secrets
from datetime import date, datetime, timedelta

from jose import JWTError, jwt          # python-jose, same as patient_portal_auth.py
from sqlalchemy.orm import Session

from app.config import settings
from app.utils.dt import now_utc_naive
from app.models.pellet import PelletPatient
from app.models.pellet_portal import PelletPortalAuthAttempt

_ALGO = "HS256"
_TOKEN_TTL_DAYS = 30
_CODE_TTL_MIN = 10


def _secret() -> str:
    # SAME secret patient_portal_auth.py uses — env / Secret Manager; never hard-code.
    return settings.secret_key


def issue_portal_token(p: PelletPatient) -> str:
    payload = {
        "pellet_patient_id": str(p.id),
        "ppv": int(p.portal_token_version or 0),
        # python-jose serializes `exp` from a datetime; keep it tz-naive UTC.
        "exp": now_utc_naive() + timedelta(days=_TOKEN_TTL_DAYS),
        "scope": "pellet_portal",
    }
    return jwt.encode(payload, _secret(), algorithm=_ALGO)


def decode_portal_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, _secret(), algorithms=[_ALGO])
    except JWTError:
        return None


def compute_token_exp(p: PelletPatient) -> datetime:
    return now_utc_naive() + timedelta(days=_TOKEN_TTL_DAYS)


def _send_sms(phone: str, body: str) -> None:
    """Twilio send — reuse the SAME `send_sms` that patient_portal_auth.py imports
    (grep its imports for the exact module). Patched in tests."""
    from app.services.surgery.checklist_notifications import send_sms  # confirm path vs patient_portal_auth.py
    send_sms(phone, body)


def match_patient(db: Session, dob: date, last4: str) -> PelletPatient | None:
    q = (db.query(PelletPatient)
           .filter(PelletPatient.patient_dob == dob)
           .filter(PelletPatient.patient_phone.like(f"%{last4}")))
    rows = q.all()
    return rows[0] if len(rows) == 1 else None


def issue_challenge(db: Session, p: PelletPatient, purpose: str = "login") -> str:
    code = f"{secrets.randbelow(900000) + 100000}"
    ct = secrets.token_urlsafe(24)
    db.add(PelletPortalAuthAttempt(
        pellet_patient_id=p.id, challenge_token=ct,
        code_hash=hashlib.sha256(code.encode()).hexdigest(),
        purpose=purpose, created_at=now_utc_naive(),
        expires_at=now_utc_naive() + timedelta(minutes=_CODE_TTL_MIN)))
    db.commit()
    _send_sms(p.patient_phone, f"Your Waldorf Women's Care pellet portal code is {code}")
    return ct


def verify_code(db: Session, challenge_token: str, code: str) -> PelletPatient | None:
    att = (db.query(PelletPortalAuthAttempt)
             .filter(PelletPortalAuthAttempt.challenge_token == challenge_token,
                     PelletPortalAuthAttempt.consumed_at.is_(None))
             .first())
    if att is None or (att.expires_at and att.expires_at < now_utc_naive()):
        return None
    if hashlib.sha256(code.encode()).hexdigest() != att.code_hash:
        return None
    att.consumed_at = now_utc_naive()
    db.commit()
    return db.query(PelletPatient).filter(PelletPatient.id == att.pellet_patient_id).first()
```

Note: confirm `settings.jwt_secret_key` and the real SMS module name against `patient_portal.py`; reuse the exact same secret + SMS helper it uses.

- [ ] **Step 4: Implement login/verify endpoints + the token dependency**

Create `backend/app/routers/patient_pellet.py`:

```python
"""Patient-facing pellet portal API (no staff auth; uses a pellet-portal JWT).
Phase 1: login/verify, requirement dashboard, mammo upload, labs self-report,
consent. Mirrors patient_portal.py."""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, File, UploadFile, Form
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.pellet import PelletPatient
from app.services.pellet import portal_auth

router = APIRouter(prefix="/pellet-portal", tags=["pellet-portal"])


class LoginIn(BaseModel):
    dob: str        # YYYY-MM-DD
    last4: str


@router.post("/login")
def login(payload: LoginIn, db: Session = Depends(get_db)):
    try:
        dob = date.fromisoformat(payload.dob)
    except ValueError:
        raise HTTPException(status_code=422, detail="bad dob")
    p = portal_auth.match_patient(db, dob, payload.last4.strip()[-4:])
    if p is None:
        # Don't reveal which field failed.
        raise HTTPException(status_code=404, detail="No matching record found")
    ct = portal_auth.issue_challenge(db, p, purpose="login")
    return {"challenge_token": ct}


class VerifyIn(BaseModel):
    challenge_token: str
    code: str


@router.post("/verify")
def verify(payload: VerifyIn, db: Session = Depends(get_db)):
    p = portal_auth.verify_code(db, payload.challenge_token, payload.code.strip())
    if p is None:
        raise HTTPException(status_code=401, detail="Invalid or expired code")
    return {
        "token": portal_auth.issue_portal_token(p),
        "pellet_patient_id": str(p.id),
        "expires_at": portal_auth.compute_token_exp(p).isoformat(),
    }


def require_pellet_token(authorization: str = Header(None),
                         db: Session = Depends(get_db)) -> PelletPatient:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing token")
    claims = portal_auth.decode_portal_token(authorization.split(" ", 1)[1].strip())
    if not claims or claims.get("scope") != "pellet_portal":
        raise HTTPException(status_code=401, detail="Invalid token")
    p = (db.query(PelletPatient)
           .filter(PelletPatient.id == claims["pellet_patient_id"]).first())
    if p is None:
        raise HTTPException(status_code=401, detail="Unknown patient")
    if int(claims.get("ppv", 0)) != int(p.portal_token_version or 0):
        raise HTTPException(status_code=401, detail="Token revoked")
    return p
```

Register it in `backend/app/main.py`: `from app.routers import patient_pellet` and `app.include_router(patient_pellet.router, prefix="/api")` (match the existing include style).

- [ ] **Step 5: Run to verify pass**

Run: `cd backend && source venv/bin/activate && python -m pytest tests/test_pellet_portal_auth.py -q`
Expected: PASS. (Adjust the `_send_sms`/secret imports if the test reveals a wrong module name; rerun.)

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/pellet/portal_auth.py backend/app/routers/patient_pellet.py backend/app/main.py backend/tests/test_pellet_portal_auth.py
git commit -m "feat(pellet-portal): DOB+last4 login → SMS code → JWT (T2)"
```

---

## Task 3: Pellet activity feed service + staff endpoints + verify check-off

**Files:**
- Create: `backend/app/services/pellet/activity.py`
- Modify: `backend/app/routers/pellet.py` (staff feed endpoints + verify check-off)
- Test: `backend/tests/test_pellet_activity.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_pellet_activity.py
from datetime import date
from app.models.pellet import PelletPatient
from app.models.pellet_portal import PelletActivity
from app.services.pellet.activity import record_pellet_activity


def _patient(db):
    p = PelletPatient(patient_name="Doe, Jane", chart_number="MRN1",
                      patient_dob=date(1980, 5, 1), patient_phone="3015551234")
    db.add(p); db.commit(); db.refresh(p)
    return p


def test_record_and_list_feed(client, db):
    p = _patient(db)
    record_pellet_activity(db, p, "mammo_uploaded", "Uploaded mammogram")
    db.commit()
    body = client.get("/api/pellets/activity").json()
    assert body["items"][0]["kind"] == "mammo_uploaded"
    assert body["items"][0]["patient_name"] == "Doe, Jane" or "Jane" in body["items"][0]["patient_name"]


def test_unread_count_and_read_all(client, db):
    p = _patient(db)
    record_pellet_activity(db, p, "labs_self_reported", "Self-reported labs")
    db.commit()
    assert client.get("/api/pellets/activity/unread-count").json()["count"] == 1
    client.post("/api/pellets/activity/read-all")
    assert client.get("/api/pellets/activity/unread-count").json()["count"] == 0


def test_verify_checkoff_sets_flag(client, db):
    p = _patient(db)
    record_pellet_activity(db, p, "mammo_uploaded", "Uploaded mammogram")
    db.commit()
    act_id = client.get("/api/pellets/activity").json()["items"][0]["id"]
    r = client.post(f"/api/pellets/activity/{act_id}/verify")
    assert r.status_code == 200
    db.refresh(p)
    assert p.mammo_verified is True
    assert p.mammo_verified_by
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && source venv/bin/activate && python -m pytest tests/test_pellet_activity.py -q`
Expected: FAIL — `ModuleNotFoundError: app.services.pellet.activity`.

- [ ] **Step 3: Implement the activity service**

```python
# backend/app/services/pellet/activity.py
"""record_pellet_activity — one feed row per patient action, SAVEPOINT
soft-fail so it never poisons the caller's transaction. Mirrors
app/services/surgery/activity.py."""
from __future__ import annotations

import logging
from sqlalchemy.orm import Session
from app.models.pellet_portal import PelletActivity

log = logging.getLogger(__name__)


def record_pellet_activity(db: Session, patient, kind: str, summary: str,
                           actor: str = "patient", detail: str | None = None) -> None:
    try:
        with db.begin_nested():
            db.add(PelletActivity(
                pellet_patient_id=patient.id, kind=kind,
                summary=(summary or "")[:300], actor=actor, detail=detail))
    except Exception:                       # pragma: no cover - soft-fail
        log.exception("record_pellet_activity failed (kind=%s)", kind)
```

- [ ] **Step 4: Implement staff feed + verify endpoints**

Add to `backend/app/routers/pellet.py` (mirror surgery.py:1744-1824; gate VIEW for reads, WORK for mutations). Use the patient's display name `f"{p.last_name}, {p.first_name}"`.

```python
# --- Patient-action feed (pellet portal) ---
from app.models.pellet_portal import PelletActivity
from app.utils.dt import now_utc_naive

@router.get("/activity")
def pellet_activity(unread_only: bool = False, limit: int = 100,
                    db: Session = Depends(get_db),
                    current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.VIEW))):
    q = (db.query(PelletActivity, PelletPatient)
           .join(PelletPatient, PelletPatient.id == PelletActivity.pellet_patient_id))
    if unread_only:
        q = q.filter(PelletActivity.read_at.is_(None))
    pairs = q.order_by(PelletActivity.created_at.desc()).limit(max(0, int(limit))).all()
    return {"items": [{
        "id": str(a.id), "pellet_patient_id": str(a.pellet_patient_id),
        "patient_name": p.patient_name, "chart_number": p.chart_number,
        "kind": a.kind, "summary": a.summary, "actor": a.actor,
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "handled_at": a.handled_at.isoformat() if a.handled_at else None,
        "read_at": a.read_at.isoformat() if a.read_at else None,
    } for a, p in pairs]}


@router.get("/activity/unread-count")
def pellet_activity_unread(db: Session = Depends(get_db),
                           current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.VIEW))):
    n = db.query(PelletActivity).filter(PelletActivity.read_at.is_(None)).count()
    return {"count": n}


@router.post("/activity/read-all")
def pellet_activity_read_all(db: Session = Depends(get_db),
                             current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.WORK))):
    by = (current_user.get("email") or "").lower() or None
    (db.query(PelletActivity).filter(PelletActivity.read_at.is_(None))
       .update({"read_at": now_utc_naive(), "read_by": by}))
    db.commit(); return {"ok": True}


@router.post("/activity/{activity_id}/verify")
def pellet_activity_verify(activity_id: str, db: Session = Depends(get_db),
                           current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.WORK))):
    a = db.query(PelletActivity).filter(PelletActivity.id == activity_id).first()
    if a is None:
        raise HTTPException(status_code=404, detail="activity not found")
    p = db.query(PelletPatient).filter(PelletPatient.id == a.pellet_patient_id).first()
    by = (current_user.get("email") or "").lower() or None
    now = now_utc_naive()
    if a.kind == "mammo_uploaded":
        p.mammo_verified = True; p.mammo_verified_by = by; p.mammo_verified_at = now
    elif a.kind == "labs_self_reported":
        p.labs_verified = True; p.labs_verified_by = by; p.labs_verified_at = now
    a.handled_at = now; a.handled_by = by
    if a.read_at is None:
        a.read_at = now; a.read_by = by
    db.commit()
    return {"ok": True, "kind": a.kind}
```

Ensure `PelletPatient` is imported in `pellet.py` (it is, for existing endpoints).

- [ ] **Step 5: Run to verify pass**

Run: `cd backend && source venv/bin/activate && python -m pytest tests/test_pellet_activity.py -q`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/pellet/activity.py backend/app/routers/pellet.py backend/tests/test_pellet_activity.py
git commit -m "feat(pellet-portal): staff patient-action feed + verify check-off (T3)"
```

---

## Task 4: Patient requirement endpoints — dashboard, mammo upload, labs self-report

**Files:**
- Modify: `backend/app/routers/patient_pellet.py`
- Test: `backend/tests/test_pellet_portal_requirements.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_pellet_portal_requirements.py
from datetime import date
import io
import pytest
from app.models.pellet import PelletPatient
from app.models.pellet_portal import PelletActivity
from app.services.pellet import portal_auth


@pytest.fixture(autouse=True)
def _local_storage_root(tmp_path, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "documents_local_root", str(tmp_path))


@pytest.fixture
def auth(db):
    p = PelletPatient(patient_name="Doe, Jane", chart_number="MRN1",
                      patient_dob=date(1980, 5, 1), patient_phone="3015551234")
    db.add(p); db.commit(); db.refresh(p)
    token = portal_auth.issue_portal_token(p)
    return p, {"Authorization": f"Bearer {token}"}


def test_dashboard_initial_checklist(client, db, auth):
    _p, h = auth
    body = client.get("/api/pellet-portal/dashboard", headers=h).json()
    reqs = {r["key"]: r for r in body["requirements"]}
    assert reqs["mammo"]["status"] == "todo"
    assert reqs["labs"]["status"] == "todo"
    assert reqs["consent"]["status"] == "todo"


def test_mammo_upload_creates_pending_and_activity(client, db, auth):
    p, h = auth
    r = client.post("/api/pellet-portal/mammo",
                    files={"file": ("m.pdf", io.BytesIO(b"%PDF-1.4 x"), "application/pdf")},
                    headers=h)
    assert r.status_code == 200, r.text
    assert db.query(PelletActivity).filter(PelletActivity.kind == "mammo_uploaded").count() == 1
    db.refresh(p)
    assert p.mammo_verified is False         # still needs staff verify


def test_labs_self_report_creates_activity(client, db, auth):
    p, h = auth
    r = client.post("/api/pellet-portal/labs",
                    json={"completed": True, "drawn_date": "2026-06-10"}, headers=h)
    assert r.status_code == 200, r.text
    assert db.query(PelletActivity).filter(PelletActivity.kind == "labs_self_reported").count() == 1
    db.refresh(p)
    assert p.labs_verified is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && source venv/bin/activate && python -m pytest tests/test_pellet_portal_requirements.py -q`
Expected: FAIL — 404 (endpoints not defined).

- [ ] **Step 3: Implement the endpoints**

Add to `backend/app/routers/patient_pellet.py`. Use `save_blob` for the mammo file, mirror surgery upload (`patient_portal.py:1215`). Pull validity windows from pellet config.

```python
import json
from app.services.pellet import portal_auth
from app.services.pellet.activity import record_pellet_activity
from app.services.pellet.settings import cfg
from app.services.storage import save_blob
from app.models.pellet_portal import PelletConsent, PelletPortalUpload


def _requirements(db, p) -> list[dict]:
    consent = (db.query(PelletConsent)
                 .filter(PelletConsent.pellet_patient_id == p.id,
                         PelletConsent.status == "signed")
                 .order_by(PelletConsent.signed_at.desc()).first())
    consent_ok = bool(consent and consent.is_valid)
    return [
        {"key": "mammo", "label": "Mammogram",
         "status": "done" if p.mammo_verified
                   else ("pending" if p.mammo_submitted_at else "todo")},
        {"key": "labs", "label": "Labs",
         "status": "done" if (p.labs_verified or p.labs_not_required)
                   else ("pending" if p.labs_self_reported_at else "todo")},
        {"key": "consent", "label": "Insertion Consent",
         "status": "done" if consent_ok else "todo"},
    ]


@router.get("/dashboard")
def dashboard(p: PelletPatient = Depends(require_pellet_token),
              db: Session = Depends(get_db)):
    return {
        "patient": {"patient_name": p.patient_name, "chart_number": p.chart_number},
        "requirements": _requirements(db, p),
        # Payment + scheduling come in later phases — surfaced as locked client-side.
    }


@router.post("/mammo")
def upload_mammo(file: UploadFile = File(...),
                 p: PelletPatient = Depends(require_pellet_token),
                 db: Session = Depends(get_db)):
    raw = file.file.read()
    if not raw:
        raise HTTPException(status_code=422, detail="empty file")
    # save_blob signature: save_blob(prefix, body_bytes, filename) -> storage key.
    # CONFIRM exact arg order against app/services/storage.py before relying on it.
    path = save_blob("pellet-mammo", raw, file.filename or "mammo")
    db.add(PelletPortalUpload(pellet_patient_id=p.id, kind="mammo",
                              filename=file.filename, storage_path=path,
                              content_type=file.content_type))
    p.mammo_submitted_at = now_utc_naive()      # drives the "pending" checklist state
    record_pellet_activity(db, p, "mammo_uploaded", "Patient uploaded a mammogram")
    db.commit()
    return {"ok": True, "status": "pending_verification"}


class LabsIn(BaseModel):
    completed: bool
    drawn_date: Optional[str] = None


@router.post("/labs")
def self_report_labs(payload: LabsIn,
                     p: PelletPatient = Depends(require_pellet_token),
                     db: Session = Depends(get_db)):
    if not payload.completed:
        raise HTTPException(status_code=422, detail="completed must be true")
    drawn = None
    if payload.drawn_date:
        try:
            drawn = date.fromisoformat(payload.drawn_date)
        except ValueError:
            raise HTTPException(status_code=422, detail="bad drawn_date")
    # Labs are an ATTESTATION — do NOT write the staff-entry PelletPatientLab table
    # (it requires clinical values). Just stamp the flag + emit a feed action; the
    # attested draw date rides along in the activity detail for staff reference.
    p.labs_self_reported_at = now_utc_naive()
    record_pellet_activity(db, p, "labs_self_reported",
                           "Patient self-reported labs complete",
                           detail=json.dumps({"drawn_date": drawn.isoformat() if drawn else None}))
    db.commit()
    return {"ok": True, "status": "pending_verification"}
```

Add `now_utc_naive` to the `patient_pellet.py` imports (`from app.utils.dt import now_utc_naive`).
Note: the verify check-off (T3) already sets `mammo_verified`/`labs_verified`; it should ALSO be
fine to leave `mammo_submitted_at`/`labs_self_reported_at` as-is (they're only read when not yet
verified).

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && source venv/bin/activate && python -m pytest tests/test_pellet_portal_requirements.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/patient_pellet.py backend/tests/test_pellet_portal_requirements.py
git commit -m "feat(pellet-portal): dashboard + mammo upload + labs self-report (T4)"
```

---

## Task 5: Consent send + BoldSign webhook → PelletConsent (1-yr)

**Files:**
- Modify: `backend/app/services/boldsign_envelopes.py` (add `send_pellet_consent(p, db)`)
- Modify: `backend/app/routers/patient_pellet.py` (`POST /consent`)
- Modify: `backend/app/routers/boldsign.py` (webhook: on signed, write/refresh PelletConsent)
- Modify: `backend/app/services/pellet/settings.py` (`consent_template_id` key)
- Test: `backend/tests/test_pellet_consent.py`

- [ ] **Step 1: Write the failing test** (mock BoldSign HTTP; assert a PelletConsent row + 1-yr expiry)

```python
# backend/tests/test_pellet_consent.py
from datetime import date, timedelta
import pytest
from app.models.pellet import PelletPatient
from app.models.pellet_portal import PelletConsent
from app.services.pellet import portal_auth
from app.utils.dt import now_utc_naive


@pytest.fixture
def auth(db):
    p = PelletPatient(patient_name="Doe, Jane", chart_number="MRN1",
                      patient_dob=date(1980, 5, 1), patient_phone="3015551234", patient_email="j@x.com")
    db.add(p); db.commit(); db.refresh(p)
    return p, {"Authorization": f"Bearer {portal_auth.issue_portal_token(p)}"}


def test_consent_send_creates_sent_row(client, db, auth, monkeypatch):
    import app.services.boldsign_envelopes as be
    monkeypatch.setattr(be, "_create_pellet_envelope", lambda p, tid: "env-123")
    monkeypatch.setattr("app.services.pellet.settings.cfg",
                        lambda db, k: "tmpl-1" if k == "consent_template_id" else None)
    p, h = auth
    r = client.post("/api/pellet-portal/consent", headers=h)
    assert r.status_code == 200, r.text
    row = db.query(PelletConsent).filter(PelletConsent.pellet_patient_id == p.id).first()
    assert row.status == "sent" and row.boldsign_envelope_id == "env-123"


def test_webhook_signed_sets_expiry(client, db, auth):
    p, _h = auth
    c = PelletConsent(pellet_patient_id=p.id, boldsign_envelope_id="env-9", status="sent")
    db.add(c); db.commit()
    # Simulate the BoldSign "Completed" webhook for env-9.
    from app.routers.boldsign import _apply_pellet_signed   # helper under test
    _apply_pellet_signed(db, "env-9")
    db.refresh(c)
    assert c.status == "signed"
    assert c.signed_at is not None
    assert abs((c.expires_at - c.signed_at) - timedelta(days=365)) < timedelta(seconds=5)
    assert c.is_valid is True
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && source venv/bin/activate && python -m pytest tests/test_pellet_consent.py -q`
Expected: FAIL — missing `_create_pellet_envelope` / `_apply_pellet_signed` / endpoint.

- [ ] **Step 3: Implement consent send + webhook helper**

In `backend/app/services/boldsign_envelopes.py`, add a thin pellet path reusing the existing `_http()/_headers()/_create_envelope` machinery (it currently takes a `Surgery`; add a generic create that takes name/email/template_id):

```python
def _create_pellet_envelope(p, template_id: str) -> str:
    """Create a BoldSign envelope from a template for a pellet patient.
    Reuses _http()/_headers(); prefills patient name/email. Returns the
    BoldSign document/envelope id."""
    if not _is_configured():
        raise BoldSignEnvelopeError("BoldSign not configured")
    body = {
        "templateId": template_id,
        "roles": [{
            "roleIndex": 1,
            "signerName": f"{p.first_name} {p.last_name}",
            "signerEmail": p.email,
        }],
    }
    with _http() as c:
        r = c.post("https://api.boldsign.com/v1/template/send", headers=_headers(),
                   json=body)
        r.raise_for_status()
        return r.json()["documentId"]
```

(Confirm the exact BoldSign send-from-template endpoint + response key against the existing `_create_envelope` implementation and use identical conventions.)

In `backend/app/routers/patient_pellet.py`:

```python
@router.post("/consent")
def request_consent(p: PelletPatient = Depends(require_pellet_token),
                    db: Session = Depends(get_db)):
    import app.services.boldsign_envelopes as be
    # Reuse a still-valid consent if present.
    existing = (db.query(PelletConsent)
                  .filter(PelletConsent.pellet_patient_id == p.id,
                          PelletConsent.status == "signed")
                  .order_by(PelletConsent.signed_at.desc()).first())
    if existing and existing.is_valid:
        return {"ok": True, "status": "already_valid"}
    tid = cfg(db, "consent_template_id")
    if not tid:
        raise HTTPException(status_code=409, detail="consent template not configured")
    env_id = be._create_pellet_envelope(p, tid)
    db.add(PelletConsent(pellet_patient_id=p.id, boldsign_envelope_id=env_id,
                         template_id=tid, status="sent"))
    record_pellet_activity(db, p, "consent_sent", "Consent envelope sent")
    db.commit()
    return {"ok": True, "status": "sent", "envelope_id": env_id}
```

In `backend/app/routers/boldsign.py`, add the helper and call it from the existing webhook handler when an envelope completes:

```python
def _apply_pellet_signed(db, envelope_id: str) -> None:
    from datetime import timedelta
    from app.models.pellet_portal import PelletConsent
    from app.utils.dt import now_utc_naive
    c = (db.query(PelletConsent)
           .filter(PelletConsent.boldsign_envelope_id == envelope_id).first())
    if c is None or c.status == "signed":
        return
    c.signed_at = now_utc_naive()
    c.expires_at = c.signed_at + timedelta(days=365)
    c.status = "signed"
    db.commit()
    # feed row
    from app.models.pellet import PelletPatient
    from app.services.pellet.activity import record_pellet_activity
    p = db.query(PelletPatient).filter(PelletPatient.id == c.pellet_patient_id).first()
    if p:
        record_pellet_activity(db, p, "consent_signed", "Patient signed insertion consent",
                               actor="patient")
        db.commit()
```

In the existing BoldSign webhook completion branch, after handling surgery envelopes, also call `_apply_pellet_signed(db, envelope_id)` (idempotent — no-ops if not a pellet envelope).

- [ ] **Step 4: Add the config default**

In `backend/app/services/pellet/settings.py` `PELLET_SETTINGS_DEFAULTS`, add:

```python
    "consent_template_id":      None,   # BoldSign template id for the insertion consent
    "require_mammo":            True,
    "require_labs":             True,
    "require_consent":          True,
```

- [ ] **Step 5: Run to verify pass**

Run: `cd backend && source venv/bin/activate && python -m pytest tests/test_pellet_consent.py -q`
Expected: PASS (2 tests).

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/boldsign_envelopes.py backend/app/routers/patient_pellet.py backend/app/routers/boldsign.py backend/app/services/pellet/settings.py backend/tests/test_pellet_consent.py
git commit -m "feat(pellet-portal): BoldSign insertion consent + 1-yr validity (T5)"
```

---

## Task 6: Config payload validation for the new pellet settings

**Files:**
- Modify: `backend/app/routers/pellet.py` (the `/config` PUT `ConfigPayload`)
- Test: `backend/tests/test_pellet_portal_config.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_pellet_portal_config.py
def test_put_pellet_config_accepts_new_keys(client, db):
    r = client.put("/api/pellet/config", json={
        "require_mammo": True, "require_labs": False, "require_consent": True,
        "consent_template_id": "tmpl-123",
    })
    assert r.status_code == 200, r.text
    got = client.get("/api/pellet/config").json()
    assert got["require_labs"] is False
    assert got["consent_template_id"] == "tmpl-123"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && source venv/bin/activate && python -m pytest tests/test_pellet_portal_config.py -q`
Expected: FAIL — keys rejected/ignored by the current `ConfigPayload`.

- [ ] **Step 3: Extend `ConfigPayload`**

In `backend/app/routers/pellet.py`, add to the pellet `ConfigPayload` Pydantic model (match its existing optional-field style):

```python
    require_mammo: Optional[bool] = None
    require_labs: Optional[bool] = None
    require_consent: Optional[bool] = None
    consent_template_id: Optional[str] = None
```

(The GET/PUT handlers already iterate keys via the config registry; ensure these keys persist + read back. If the PUT only writes keys present in `PELLET_SETTINGS_DEFAULTS`, no handler change is needed beyond T5's defaults.)

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && source venv/bin/activate && python -m pytest tests/test_pellet_portal_config.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/pellet.py backend/tests/test_pellet_portal_config.py
git commit -m "feat(pellet-portal): config keys for requirement toggles + consent template (T6)"
```

---

## Task 7: Frontend — patient pellet portal (login, verify, dashboard, mammo, labs, consent)

**Files:**
- Create: `frontend/src/pages/pellet-portal/PelletPortalLogin.jsx`, `PelletPortalVerify.jsx`, `PelletPortalShell.jsx`, `PelletDashboard.jsx`, `PelletMammo.jsx`, `PelletLabs.jsx`, `PelletConsent.jsx`
- Modify: `frontend/src/routes.jsx` (public patient routes under `/pellet-portal`)
- Test: build + headless render (no unit-test infra on the frontend)

Mirror `frontend/src/pages/portal/` exactly. Token in `sessionStorage` key `pellet-token`; a small axios instance (or the existing `api`) with `Authorization: Bearer ${token}`; base `/api/pellet-portal`.

- [ ] **Step 1: Login page** — `PelletPortalLogin.jsx`: DOB + last-4 form → `POST /pellet-portal/login` → store `challenge_token` in component state → navigate to verify. Mirror `portal/PortalLogin.jsx`. Title Case headings; dates MM/DD/YYYY.

- [ ] **Step 2: Verify page** — `PelletPortalVerify.jsx`: code input → `POST /pellet-portal/verify` → `sessionStorage.setItem('pellet-token', token)` → navigate to `/pellet-portal/home`. Mirror `portal/PortalVerify.jsx`.

- [ ] **Step 3: Shell + dashboard** — `PelletPortalShell.jsx` (reads token, redirects to login if absent) wrapping `PelletDashboard.jsx`: `GET /pellet-portal/dashboard` → render the requirement checklist (mammo / labs / consent) with status chips (`done` green / `pending` amber / `todo` grey) and a CTA per `todo`/`pending` item. Locked "Payment" and "Scheduling" rows shown greyed with a "coming soon" hint.

- [ ] **Step 4: Mammo upload** — `PelletMammo.jsx`: file input → `POST /pellet-portal/mammo` (multipart) → success state "Submitted — awaiting staff review." Mirror `portal/Documents.jsx` upload.

- [ ] **Step 5: Labs self-report** — `PelletLabs.jsx`: attestation checkbox ("I have completed my labs") + optional drawn date → `POST /pellet-portal/labs`.

- [ ] **Step 6: Consent** — `PelletConsent.jsx`: "Sign Insertion Consent" button → `POST /pellet-portal/consent` → show the returned BoldSign link / "sent to your email" state. Mirror `portal/Consent.jsx`.

- [ ] **Step 7: Routes** — in `frontend/src/routes.jsx`, add PUBLIC routes (no `PrivateRoute`/staff gate), e.g.:

```jsx
{ path: '/pellet-portal',        element: <PelletPortalLogin /> },
{ path: '/pellet-portal/verify', element: <PelletPortalVerify /> },
{ path: '/pellet-portal/home',   element: <PelletPortalShell />, children: [
    { index: true,      element: <PelletDashboard /> },
    { path: 'mammo',    element: <PelletMammo /> },
    { path: 'labs',     element: <PelletLabs /> },
    { path: 'consent',  element: <PelletConsent /> },
]},
```

(Confirm how the existing surgery `/portal` routes are registered — match that placement so they bypass the staff app shell.)

- [ ] **Step 8: Build**

Run: `cd frontend && npm run build`
Expected: builds clean.

- [ ] **Step 9: Commit**

```bash
git add frontend/src/pages/pellet-portal frontend/src/routes.jsx
git commit -m "feat(pellet-portal): patient portal UI — login, dashboard, mammo, labs, consent (T7)"
```

---

## Task 8: Frontend — staff "Patient Activity" feed + verify check-off

**Files:**
- Modify: `frontend/src/pages/Pellets.jsx` (or the pellet nav) — add a "Patient Activity" panel/tab
- Test: build + headless render

- [ ] **Step 1: Activity panel** — a collapsible "Patient Activity" section (mirror the surgery To-Do/Activity feed): `useQuery(['pellet-activity'], () => api.get('/pellet/activity'))`. Render rows (patient name, kind label, summary, when). For `mammo_uploaded` / `labs_self_reported` rows that aren't `handled_at`, show a **Verify** button → `POST /pellet/activity/{id}/verify` (WORK-gated) → invalidate `['pellet-activity']`. A "Mark all read" → `POST /pellet/activity/read-all`.

- [ ] **Step 2: Unread badge** — add a `PelletActivityBadge` to the pellet nav mirroring `ActivityBadge` (`GET /pellet/activity/unread-count`, 60s refetch).

- [ ] **Step 3: Build**

Run: `cd frontend && npm run build`
Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/Pellets.jsx
git commit -m "feat(pellet-portal): staff Patient Activity feed + verify check-off (T8)"
```

---

## Task 9: Settings UI + authenticated walk-through + deploy

**Files:**
- Modify: `frontend/src/pages/PelletSettings.jsx` (requirement toggles + consent template id)
- Create: `backend/tests/test_pellet_portal_walkthrough.py`

- [ ] **Step 1: Settings fields** — add a "Patient Portal" tab/section to `PelletSettings.jsx`: toggles for `require_mammo/require_labs/require_consent` and a text field for `consent_template_id`, saved via the existing `PUT /pellet/config`. Build clean.

- [ ] **Step 2: Authenticated walk-through test**

```python
# backend/tests/test_pellet_portal_walkthrough.py
"""Authenticated end-to-end Phase-1 walk-through: patient logs in, uploads a
mammogram + self-reports labs + signs consent; staff verify via the feed;
the requirement checklist flips to done."""
from datetime import date
import io
import pytest
from app.models.pellet import PelletPatient
from app.models.pellet_portal import PelletConsent
from app.services.pellet import portal_auth


@pytest.fixture(autouse=True)
def _local_storage_root(tmp_path, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "documents_local_root", str(tmp_path))


def test_phase1_walkthrough(client, db, capsys):
    log = []
    p = PelletPatient(patient_name="Doe, Jane", chart_number="MRN1",
                      patient_dob=date(1980, 5, 1), patient_phone="3015551234", patient_email="j@x.com")
    db.add(p); db.commit(); db.refresh(p)
    h = {"Authorization": f"Bearer {portal_auth.issue_portal_token(p)}"}

    reqs = {r["key"]: r["status"] for r in
            client.get("/api/pellet-portal/dashboard", headers=h).json()["requirements"]}
    assert reqs == {"mammo": "todo", "labs": "todo", "consent": "todo"}
    log.append("1. dashboard: mammo/labs/consent all 'todo'")

    client.post("/api/pellet-portal/mammo",
                files={"file": ("m.pdf", io.BytesIO(b"%PDF x"), "application/pdf")}, headers=h)
    client.post("/api/pellet-portal/labs", json={"completed": True}, headers=h)
    log.append("2. patient uploaded mammo + self-reported labs (both pending)")

    feed = client.get("/api/pellets/activity").json()["items"]
    for a in feed:
        if a["kind"] in ("mammo_uploaded", "labs_self_reported"):
            client.post(f"/api/pellets/activity/{a['id']}/verify")
    log.append("3. staff verified mammo + labs via the feed")

    # Mark consent signed directly (BoldSign webhook path covered in T5).
    from datetime import timedelta
    from app.utils.dt import now_utc_naive
    db.add(PelletConsent(pellet_patient_id=p.id, boldsign_envelope_id="e1",
                         status="signed", signed_at=now_utc_naive(),
                         expires_at=now_utc_naive() + timedelta(days=365)))
    db.commit()

    reqs2 = {r["key"]: r["status"] for r in
             client.get("/api/pellet-portal/dashboard", headers=h).json()["requirements"]}
    assert reqs2 == {"mammo": "done", "labs": "done", "consent": "done"}
    log.append("4. dashboard now: mammo/labs/consent all 'done' — ready for payment phase")

    with capsys.disabled():
        print("\n  ── Pellet portal Phase-1 walk-through (authenticated) ──")
        for line in log:
            print("   " + line)
```

- [ ] **Step 3: Run the walk-through + full suite**

Run: `cd backend && source venv/bin/activate && python -m pytest tests/test_pellet_portal_walkthrough.py -s -q` then `python -m pytest -q`
Expected: walk-through PASS; full suite ≤ baseline (69 failed).

- [ ] **Step 4: Headless portal render** — serve the built frontend and confirm `/pellet-portal` renders the login (no console errors) via the Playwright route-mock harness used earlier this session.

- [ ] **Step 5: Merge + deploy + push**

```bash
git checkout main && git merge --no-ff feat/pellet-portal-phase1
gcloud builds submit --project=wwc-solutions --tag=us-east4-docker.pkg.dev/wwc-solutions/app/backend:latest backend
gcloud builds submit --project=wwc-solutions --tag=us-east4-docker.pkg.dev/wwc-solutions/app/frontend:latest frontend
gcloud run deploy backend  --image=us-east4-docker.pkg.dev/wwc-solutions/app/backend:latest  --region=us-east4 --project=wwc-solutions
gcloud run deploy frontend --image=us-east4-docker.pkg.dev/wwc-solutions/app/frontend:latest --region=us-east4 --project=wwc-solutions
```

Smoke: backend `/api/health` 200; `/api/pellet-portal/dashboard` 401 unauthed; `/api/pellets/activity` 401 unauthed; frontend `/pellet-portal` 200. Then `git push origin main`.

- [ ] **Step 6: Commit any settings/walkthrough files**

```bash
git add frontend/src/pages/PelletSettings.jsx backend/tests/test_pellet_portal_walkthrough.py
git commit -m "feat(pellet-portal): settings UI + Phase-1 authenticated walk-through (T9)"
```

---

## Self-review notes (confirm during execution)

Field names / prefix / auth lib / upload model are already corrected per the **VERIFIED codebase
facts** block at the top — those override anything else. Remaining small confirmations:

- **`save_blob` signature** (T4): confirm the exact arg order/keywords in `app/services/storage.py`
  before relying on `save_blob("pellet-mammo", raw, filename)`.
- **`send_sms` import path** (T2): grep `patient_portal_auth.py` for the exact module it imports and
  use that identical import (the path in the snippet is a best-guess).
- **BoldSign send-from-template endpoint/response key** (T5): mirror `_create_envelope`
  (`/v1/template/send`, params `templateId`, response `documentId`); reuse `_http()/_headers()`.
- **Public route registration** (T7): place the `/pellet-portal` routes where the existing
  surgery `/portal` routes live so they bypass the staff app shell + `PrivateRoute`.
- **`init_db` import line** (T1): add `pellet_portal` to the `from app.models import ...` list.
- Suite kept at/under baseline (69 failed) throughout; each task commits independently; deploy with
  `--project=wwc-solutions`.
```
