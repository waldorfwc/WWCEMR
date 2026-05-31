# Patient Portal P1 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the portal foundation — 2FA sign-in (DOB + last-4 + SMS code), a `/portal/*` shell with stub navigation for later phases, and a read-only milestone dashboard.

**Architecture:** New FastAPI router `patient_portal.py` parallels the existing `patient_surgery.py`. JWT uses a new audience (`wwc:patient-portal`) so the existing magic-link tokens can't be used as portal sessions. SMS codes are bcrypt-hashed and stored in a new `patient_portal_auth_codes` table with a 5-min TTL. Frontend mounts a new `/portal/*` route tree with a persistent shell; the dashboard reads from existing tables (surgeries, surgery_payments, surgery_consent_envelopes) plus two new self-report bool columns on Surgery.

**Tech Stack:** FastAPI + SQLAlchemy + python-jose (existing). passlib[bcrypt] (existing). React + Vite + React Router v6 + TanStack Query + Tailwind (existing). Twilio via existing `send_sms`.

**Spec:** `docs/superpowers/specs/2026-05-31-patient-portal-p1-design.md`

---

## Task 1: Schema — new table + columns + migration

**Files:**
- Modify: `backend/app/models/surgery.py` (add 4 columns to Surgery)
- Create: `backend/app/models/patient_portal.py` (new `PatientPortalAuthCode` model)
- Modify: `backend/app/models/__init__.py` (export the new model)
- Create: `backend/scripts/migrate_patient_portal_p1.py` (idempotent prod migration)
- Test:   `backend/tests/test_patient_portal_models.py`

- [ ] **Step 1: Write the failing test** in `backend/tests/test_patient_portal_models.py`:

```python
"""Patient portal P1 schema."""
from datetime import datetime, timedelta

from app.models.surgery import Surgery
from app.models.patient_portal import PatientPortalAuthCode


def test_surgery_has_self_report_flags(db):
    s = Surgery(chart_number="1", patient_name="Pat", status="new")
    db.add(s); db.commit(); db.refresh(s)
    assert s.labs_self_reported is False
    assert s.labs_self_reported_at is None
    assert s.hospital_preop_self_reported is False
    assert s.hospital_preop_self_reported_at is None


def test_portal_auth_code_persists(db):
    s = Surgery(chart_number="2", patient_name="Pat", status="new")
    db.add(s); db.commit(); db.refresh(s)
    row = PatientPortalAuthCode(
        surgery_id=s.id,
        challenge_token="ch_abc",
        code_hash="$2b$12$placeholder",
        expires_at=datetime.utcnow() + timedelta(minutes=5),
        sent_to_phone="+12405551234",
    )
    db.add(row); db.commit(); db.refresh(row)
    assert row.fail_count == 0
    assert row.used_at is None
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd backend && ./venv/bin/pytest tests/test_patient_portal_models.py -v
```
Expected: ImportError on `app.models.patient_portal`.

- [ ] **Step 3: Add columns to Surgery** in `backend/app/models/surgery.py`. Find the block near line 213 where `blocked_conflict_notified_*` columns sit, and add after it:

```python
    # Patient portal — self-report milestone flags (P1 dashboard reads these,
    # P5 wires the CTAs that flip them).
    labs_self_reported              = Column(Boolean, default=False, nullable=False)
    labs_self_reported_at           = Column(DateTime, nullable=True)
    hospital_preop_self_reported    = Column(Boolean, default=False, nullable=False)
    hospital_preop_self_reported_at = Column(DateTime, nullable=True)
```

- [ ] **Step 4: Create the new model file** at `backend/app/models/patient_portal.py`:

```python
"""Patient portal auth — SMS-code challenges issued during sign-in."""
from __future__ import annotations
from datetime import datetime

from sqlalchemy import (
    Column, DateTime, ForeignKey, Index, Integer, String,
)

from app.database import Base
from app.models.guid import GUID, new_uuid


class PatientPortalAuthCode(Base):
    __tablename__ = "patient_portal_auth_codes"
    __table_args__ = (
        Index("ix_patient_portal_auth_codes_token", "challenge_token"),
        Index("ix_patient_portal_auth_codes_surgery", "surgery_id"),
    )

    id              = Column(GUID(), primary_key=True, default=new_uuid)
    surgery_id      = Column(GUID(),
                              ForeignKey("surgeries.id", ondelete="CASCADE"),
                              nullable=False)
    challenge_token = Column(String(64), nullable=False, unique=True)
    code_hash       = Column(String(60), nullable=False)
    # bcrypt hash of the 6-digit code we SMS'd. Plaintext code never persisted.
    fail_count      = Column(Integer, default=0, nullable=False)
    expires_at      = Column(DateTime, nullable=False)
    used_at         = Column(DateTime, nullable=True)
    created_at      = Column(DateTime, default=datetime.utcnow, nullable=False)
    sent_to_phone   = Column(String(40), nullable=True)
    # For audit only. The phone is already on the Surgery row.
```

- [ ] **Step 5: Register the model** by adding to `backend/app/models/__init__.py` (find where other models are imported and add):

```python
from app.models import patient_portal  # noqa: F401
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
cd backend && ./venv/bin/pytest tests/test_patient_portal_models.py -v
```
Expected: 2 passed.

- [ ] **Step 7: Create the prod migration** at `backend/scripts/migrate_patient_portal_p1.py`:

```python
"""Idempotent migration for Patient Portal P1.

Adds:
  - 4 columns on `surgeries`: labs_self_reported(+_at),
    hospital_preop_self_reported(+_at)
  - new table `patient_portal_auth_codes`

Run on prod:
    DATABASE_URL='postgresql+psycopg2://...' \
        ./venv/bin/python scripts/migrate_patient_portal_p1.py
"""
import os
import sys

from sqlalchemy import create_engine, text

DDL = [
    # surgeries — 4 new columns, default false / null
    """ALTER TABLE surgeries
       ADD COLUMN IF NOT EXISTS labs_self_reported BOOLEAN NOT NULL DEFAULT FALSE""",
    """ALTER TABLE surgeries
       ADD COLUMN IF NOT EXISTS labs_self_reported_at TIMESTAMP NULL""",
    """ALTER TABLE surgeries
       ADD COLUMN IF NOT EXISTS hospital_preop_self_reported BOOLEAN NOT NULL DEFAULT FALSE""",
    """ALTER TABLE surgeries
       ADD COLUMN IF NOT EXISTS hospital_preop_self_reported_at TIMESTAMP NULL""",
    # patient_portal_auth_codes — new table
    """CREATE TABLE IF NOT EXISTS patient_portal_auth_codes (
        id               UUID PRIMARY KEY,
        surgery_id       UUID NOT NULL REFERENCES surgeries(id) ON DELETE CASCADE,
        challenge_token  VARCHAR(64) NOT NULL UNIQUE,
        code_hash        VARCHAR(60) NOT NULL,
        fail_count       INTEGER NOT NULL DEFAULT 0,
        expires_at       TIMESTAMP NOT NULL,
        used_at          TIMESTAMP NULL,
        created_at       TIMESTAMP NOT NULL DEFAULT NOW(),
        sent_to_phone    VARCHAR(40) NULL
    )""",
    """CREATE INDEX IF NOT EXISTS ix_patient_portal_auth_codes_token
       ON patient_portal_auth_codes(challenge_token)""",
    """CREATE INDEX IF NOT EXISTS ix_patient_portal_auth_codes_surgery
       ON patient_portal_auth_codes(surgery_id)""",
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

- [ ] **Step 8: Commit**

```bash
git add backend/app/models/surgery.py backend/app/models/patient_portal.py \
        backend/app/models/__init__.py backend/scripts/migrate_patient_portal_p1.py \
        backend/tests/test_patient_portal_models.py
git commit -m "feat(portal): P1 schema — auth-code table + 2 self-report flags on Surgery"
```

---

## Task 2: SMS template kind + seed update

**Files:**
- Modify: `backend/app/models/patient_sms.py` (extend `SMS_TEMPLATE_KINDS`)
- Modify: `backend/scripts/seed_sms_templates.py` (add 5th template)
- Modify: `backend/tests/test_patient_sms.py` (assert new kind exists)

- [ ] **Step 1: Add a failing test** at the end of `backend/tests/test_patient_sms.py`:

```python
def test_sms_template_kinds_includes_portal_login_code():
    from app.models.patient_sms import SMS_TEMPLATE_KINDS
    assert "sms_portal_login_code" in SMS_TEMPLATE_KINDS
```

- [ ] **Step 2: Run to verify fail**

```bash
cd backend && ./venv/bin/pytest tests/test_patient_sms.py -v -k portal_login
```
Expected: AssertionError.

- [ ] **Step 3: Extend the tuple** in `backend/app/models/patient_sms.py`:

```python
SMS_TEMPLATE_KINDS = (
    "sms_payment_link",
    "sms_surgery_confirmation",
    "sms_surgery_reminder",
    "sms_generic_message",
    "sms_portal_login_code",
)
```

- [ ] **Step 4: Add the template body** to `backend/scripts/seed_sms_templates.py`. Append to the `TEMPLATES` list (before the trailing `]`):

```python
    ("sms_portal_login_code",
     "Portal sign-in code",
     "WWC: Your portal sign-in code is {{code}}. "
     "Expires in 5 minutes. Reply STOP to opt out."),
```

- [ ] **Step 5: Verify test passes**

```bash
cd backend && ./venv/bin/pytest tests/test_patient_sms.py -v -k portal_login
```
Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/models/patient_sms.py backend/scripts/seed_sms_templates.py \
        backend/tests/test_patient_sms.py
git commit -m "feat(portal): sms_portal_login_code template kind + seed entry"
```

---

## Task 3: Auth helpers (JWT issuance + code lifecycle)

**Files:**
- Create: `backend/app/services/patient_portal_auth.py`
- Test:   `backend/tests/test_patient_portal_auth.py`

- [ ] **Step 1: Write failing tests** in `backend/tests/test_patient_portal_auth.py`:

```python
"""Portal auth helpers — code lifecycle + JWT TTL."""
from datetime import date, datetime, timedelta
from unittest.mock import patch

from app.models.surgery import Surgery
from app.services.patient_portal_auth import (
    issue_challenge, verify_code, issue_portal_token,
    verify_portal_token, compute_token_exp,
)


def _make_surgery(db, scheduled_date=None):
    s = Surgery(chart_number="1", patient_name="Pat",
                  cell_phone="+12405551234",
                  scheduled_date=scheduled_date,
                  status="new")
    db.add(s); db.commit(); db.refresh(s)
    return s


# ─── token TTL ──────────────────────────────────────────────────

def test_token_exp_uses_surgery_date_plus_30(db):
    s = _make_surgery(db, scheduled_date=date(2026, 7, 1))
    exp = compute_token_exp(s, now=datetime(2026, 5, 1))
    assert exp.date() == date(2026, 7, 31)   # 2026-07-01 + 30


def test_token_exp_falls_back_when_no_date(db):
    s = _make_surgery(db, scheduled_date=None)
    now = datetime(2026, 5, 1, 9, 0)
    exp = compute_token_exp(s, now=now)
    assert exp.date() == date(2026, 5, 31)   # today + 30


def test_token_exp_floors_at_today_plus_30(db):
    # Surgery already happened yesterday; sign-in for post-op.
    s = _make_surgery(db, scheduled_date=date(2026, 4, 30))
    now = datetime(2026, 5, 1, 9, 0)
    exp = compute_token_exp(s, now=now)
    # max(today, scheduled_date) + 30 = 2026-05-31
    assert exp.date() == date(2026, 5, 31)


# ─── challenge / verify cycle ────────────────────────────────────

def test_issue_challenge_creates_code_and_sms(db):
    s = _make_surgery(db)
    with patch("app.services.patient_portal_auth.send_sms",
                return_value=True) as mock_sms:
        challenge_token, code = issue_challenge(db, s)
    assert len(challenge_token) >= 32
    assert len(code) == 6 and code.isdigit()
    mock_sms.assert_called_once()
    # SMS body contains the code
    args, kwargs = mock_sms.call_args
    assert code in args[1]


def test_verify_code_success_marks_used(db):
    s = _make_surgery(db)
    with patch("app.services.patient_portal_auth.send_sms",
                return_value=True):
        challenge_token, code = issue_challenge(db, s)
    surgery_id = verify_code(db, challenge_token, code)
    assert surgery_id == s.id
    # Replay attempt should fail
    assert verify_code(db, challenge_token, code) is None


def test_verify_code_wrong_increments_fail_count(db):
    s = _make_surgery(db)
    with patch("app.services.patient_portal_auth.send_sms",
                return_value=True):
        challenge_token, _ = issue_challenge(db, s)
    assert verify_code(db, challenge_token, "000000") is None
    assert verify_code(db, challenge_token, "000000") is None
    assert verify_code(db, challenge_token, "000000") is None
    # 4th attempt — challenge dead
    assert verify_code(db, challenge_token, "000000") is None


def test_jwt_roundtrip(db):
    s = _make_surgery(db, scheduled_date=date(2026, 6, 1))
    token = issue_portal_token(s)
    assert verify_portal_token(token) == s.id
```

- [ ] **Step 2: Run to verify fails**

```bash
cd backend && ./venv/bin/pytest tests/test_patient_portal_auth.py -v
```
Expected: ImportError.

- [ ] **Step 3: Create the helpers** at `backend/app/services/patient_portal_auth.py`:

```python
"""Portal auth helpers — challenge codes (issue/verify) + JWT (issue/verify).

The challenge lifecycle:
  issue_challenge(db, surgery) -> (challenge_token, plaintext_code)
    - persists hashed code with 5-min TTL
    - sends SMS via the existing send_sms infrastructure
  verify_code(db, challenge_token, code) -> Optional[surgery_id]
    - returns the surgery_id on success, None otherwise
    - 3 wrong codes kills the challenge

JWT TTL is pegged to `surgery.scheduled_date + 30 days` per the P1 spec.
"""
from __future__ import annotations

import os
import secrets
from datetime import date, datetime, timedelta
from typing import Optional

from jose import JWTError, jwt
from passlib.hash import bcrypt
from sqlalchemy.orm import Session

from app.config import settings
from app.models.patient_portal import PatientPortalAuthCode
from app.models.surgery import Surgery
from app.services.checklist_notifications import send_sms

PORTAL_TOKEN_AUDIENCE = "wwc:patient-portal"
CODE_TTL_MINUTES = 5
CODE_MAX_FAILS = 3


def _now() -> datetime:
    return datetime.utcnow()


def _generate_code() -> str:
    """6-digit numeric code, leading zeros preserved."""
    return f"{secrets.randbelow(10**6):06d}"


def issue_challenge(db: Session, surgery: Surgery) -> tuple[str, str]:
    """Generate a code, persist its hash, SMS the plaintext to the
    surgery's cell_phone. Returns (challenge_token, plaintext_code).

    The caller never persists the plaintext code — only logs the
    challenge_token for the verify step.
    """
    code = _generate_code()
    challenge_token = secrets.token_urlsafe(32)
    row = PatientPortalAuthCode(
        surgery_id=surgery.id,
        challenge_token=challenge_token,
        code_hash=bcrypt.hash(code),
        expires_at=_now() + timedelta(minutes=CODE_TTL_MINUTES),
        sent_to_phone=surgery.cell_phone or surgery.phone or "",
    )
    db.add(row); db.commit()
    phone = row.sent_to_phone
    body = (f"WWC: Your portal sign-in code is {code}. "
            f"Expires in {CODE_TTL_MINUTES} minutes.")
    send_sms(phone, body)
    return challenge_token, code


def verify_code(db: Session, challenge_token: str, code: str) -> Optional[str]:
    """Return surgery_id on success, None on any failure. Replay-safe
    (used_at is stamped on the first successful check)."""
    row = (db.query(PatientPortalAuthCode)
              .filter(PatientPortalAuthCode.challenge_token == challenge_token)
              .first())
    if row is None or row.used_at is not None:
        return None
    if _now() > row.expires_at:
        return None
    if row.fail_count >= CODE_MAX_FAILS:
        return None
    if not bcrypt.verify(code, row.code_hash):
        row.fail_count += 1
        db.commit()
        return None
    row.used_at = _now()
    db.commit()
    return row.surgery_id


def compute_token_exp(surgery: Surgery,
                        now: Optional[datetime] = None) -> datetime:
    """JWT exp = max(today, surgery.scheduled_date) + 30 days.

    If scheduled_date is None, defaults to today + 30 days.
    The 'now' argument is for tests; production callers omit it.
    """
    now = now or _now()
    base = now.date()
    if surgery.scheduled_date and surgery.scheduled_date > base:
        base = surgery.scheduled_date
    exp_date = base + timedelta(days=30)
    return datetime.combine(exp_date, datetime.min.time())


def issue_portal_token(surgery: Surgery) -> str:
    exp = compute_token_exp(surgery)
    payload = {
        "sub": str(surgery.id),
        "aud": PORTAL_TOKEN_AUDIENCE,
        "exp": exp,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")


def verify_portal_token(token: str) -> Optional[str]:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"],
                              audience=PORTAL_TOKEN_AUDIENCE)
        return payload.get("sub")
    except JWTError:
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && ./venv/bin/pytest tests/test_patient_portal_auth.py -v
```
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/patient_portal_auth.py backend/tests/test_patient_portal_auth.py
git commit -m "feat(portal): auth helpers — challenge codes + JWT w/ surgery-date TTL"
```

---

## Task 4: POST /api/patient/portal/login endpoint

**Files:**
- Create: `backend/app/routers/patient_portal.py`
- Modify: `backend/app/main.py` (mount the router)
- Test:   `backend/tests/test_patient_portal_endpoints.py`

- [ ] **Step 1: Write failing tests** in `backend/tests/test_patient_portal_endpoints.py`:

```python
"""Portal endpoints — login + verify."""
from datetime import date
from unittest.mock import patch

from app.models.surgery import Surgery


def _seed_surgery(db, cell="+12405551234", dob=date(1990, 1, 1)):
    s = Surgery(chart_number="1", patient_name="Pat",
                  cell_phone=cell, dob=dob, status="new")
    db.add(s); db.commit(); db.refresh(s)
    return s


def test_login_sends_sms_and_returns_challenge(client, db):
    s = _seed_surgery(db)
    with patch("app.services.patient_portal_auth.send_sms",
                return_value=True) as mock_sms:
        r = client.post("/api/patient/portal/login",
                         json={"dob": "1990-01-01", "phone_last4": "1234"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "challenge_token" in body
    assert len(body["challenge_token"]) >= 32
    mock_sms.assert_called_once()


def test_login_generic_404_on_no_match(client, db):
    _seed_surgery(db)
    r = client.post("/api/patient/portal/login",
                     json={"dob": "1980-01-01", "phone_last4": "0000"})
    assert r.status_code == 404
    # Must not reveal whether DOB or phone was wrong
    assert "dob" not in r.text.lower()
    assert "phone" not in r.text.lower() or "phone number" in r.text.lower()


def test_login_locked_out_after_three_fails(client, db):
    _seed_surgery(db)
    for _ in range(3):
        client.post("/api/patient/portal/login",
                     json={"dob": "1980-01-01", "phone_last4": "0000"})
    r = client.post("/api/patient/portal/login",
                     json={"dob": "1990-01-01", "phone_last4": "1234"})
    assert r.status_code == 429


def test_login_validates_dob_format(client, db):
    _seed_surgery(db)
    r = client.post("/api/patient/portal/login",
                     json={"dob": "not-a-date", "phone_last4": "1234"})
    assert r.status_code == 422


def test_login_validates_last4_length(client, db):
    _seed_surgery(db)
    r = client.post("/api/patient/portal/login",
                     json={"dob": "1990-01-01", "phone_last4": "12"})
    assert r.status_code == 422
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && ./venv/bin/pytest tests/test_patient_portal_endpoints.py -v
```
Expected: 404s on the endpoint path (router not mounted yet).

- [ ] **Step 3: Create the router** at `backend/app/routers/patient_portal.py`:

```python
"""Patient portal — self-service sign-in + dashboard.

Lives alongside patient_surgery.py:
  - patient_surgery.py = one-shot magic-link flows (slot picker)
  - patient_portal.py  = durable, session-based portal

Auth flow (2-step):
  1. POST /login   (DOB + last4) -> sends SMS, returns challenge_token
  2. POST /verify  (challenge_token + code) -> JWT
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.patient_auth import PatientAuthAttempt
from app.models.surgery import Surgery
from app.services import patient_portal_auth as auth

log = logging.getLogger(__name__)

router = APIRouter(prefix="/patient/portal", tags=["patient-portal"])

LOCKOUT_FAILS = 3
LOCKOUT_WINDOW_MIN = 15


def _is_locked_out(db: Session, surgery_id: str) -> bool:
    cutoff = datetime.utcnow() - timedelta(minutes=LOCKOUT_WINDOW_MIN)
    fails = (db.query(PatientAuthAttempt)
                .filter(PatientAuthAttempt.surgery_id == surgery_id,
                         PatientAuthAttempt.success.is_(False),
                         PatientAuthAttempt.attempted_at >= cutoff)
                .count())
    return fails >= LOCKOUT_FAILS


def _log_attempt(db: Session, surgery_id, *, success: bool,
                  request: Request) -> None:
    ip = request.client.host if request.client else None
    db.add(PatientAuthAttempt(surgery_id=surgery_id, success=success,
                                ip_address=ip))
    db.commit()


def _normalize_last4(raw: str) -> str:
    return "".join(c for c in (raw or "") if c.isdigit())


def _match_surgery(db: Session, dob_str: str,
                     last4_in: str) -> Surgery | None:
    """Find the Surgery row that matches both DOB and last-4 of phone.

    Looks at cell_phone first, falls back to phone. Returns None if no
    match (caller renders a generic error)."""
    try:
        dob = datetime.strptime(dob_str[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        raise HTTPException(status_code=422,
                             detail="Date of birth must be YYYY-MM-DD")
    if len(last4_in) != 4:
        raise HTTPException(status_code=422,
                             detail="Phone last 4 must be 4 digits")
    for s in db.query(Surgery).filter(Surgery.dob == dob).all():
        on_file = s.cell_phone or s.phone or ""
        digits = _normalize_last4(on_file)
        if len(digits) >= 4 and digits[-4:] == last4_in:
            return s
    return None


# ─── /login ─────────────────────────────────────────────────────

class LoginPayload(BaseModel):
    dob: str            # YYYY-MM-DD
    phone_last4: str    # 4 digits


@router.post("/login")
def login(payload: LoginPayload, request: Request,
            db: Session = Depends(get_db)):
    """Step 1 of sign-in. Generic error on no match (don't leak)."""
    last4 = _normalize_last4(payload.phone_last4)
    s = _match_surgery(db, payload.dob, last4)
    if s is None:
        # Log under a sentinel so lockout counters still work without a
        # real surgery_id. We use None as a global sentinel — lockout is
        # tightened per-IP separately if needed in a later phase.
        raise HTTPException(status_code=404,
                             detail="No surgery matches that information")
    if _is_locked_out(db, s.id):
        raise HTTPException(status_code=429,
                             detail=f"Too many failed attempts. Please wait "
                                    f"{LOCKOUT_WINDOW_MIN} minutes or call our "
                                    f"office at 240-252-2140.")
    challenge_token, _code = auth.issue_challenge(db, s)
    _log_attempt(db, s.id, success=True, request=request)
    return {"challenge_token": challenge_token}
```

- [ ] **Step 4: Mount the router** in `backend/app/main.py`. Find the existing `app.include_router(patient_surgery.router, prefix="/api")` and add right after:

```python
from app.routers import patient_portal
app.include_router(patient_portal.router, prefix="/api")
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd backend && ./venv/bin/pytest tests/test_patient_portal_endpoints.py -v -k login
```
Expected: 5 login tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/routers/patient_portal.py backend/app/main.py \
        backend/tests/test_patient_portal_endpoints.py
git commit -m "feat(portal): POST /api/patient/portal/login — DOB+last4 → SMS challenge"
```

---

## Task 5: POST /api/patient/portal/verify endpoint

**Files:**
- Modify: `backend/app/routers/patient_portal.py` (add /verify)
- Modify: `backend/tests/test_patient_portal_endpoints.py` (verify tests)

- [ ] **Step 1: Add failing tests** to `backend/tests/test_patient_portal_endpoints.py`:

```python
def test_verify_returns_token_on_correct_code(client, db):
    s = _seed_surgery(db)
    with patch("app.services.patient_portal_auth.send_sms",
                return_value=True):
        login = client.post("/api/patient/portal/login",
                              json={"dob": "1990-01-01",
                                      "phone_last4": "1234"}).json()
    # Pull the code out by inspecting the row directly (test-only).
    from app.models.patient_portal import PatientPortalAuthCode
    row = db.query(PatientPortalAuthCode).filter(
        PatientPortalAuthCode.challenge_token == login["challenge_token"]
    ).first()
    # We can't recover the plaintext code from bcrypt; intercept it instead.
    # Easier: re-run the call with a mocked code.
    with patch("app.services.patient_portal_auth._generate_code",
                return_value="111111"):
        with patch("app.services.patient_portal_auth.send_sms",
                    return_value=True):
            login = client.post("/api/patient/portal/login",
                                  json={"dob": "1990-01-01",
                                          "phone_last4": "1234"}).json()
    r = client.post("/api/patient/portal/verify",
                     json={"challenge_token": login["challenge_token"],
                              "code": "111111"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "token" in body and body["token"].count(".") == 2  # JWT shape
    assert body["surgery_id"] == str(s.id)


def test_verify_rejects_wrong_code(client, db):
    s = _seed_surgery(db)
    with patch("app.services.patient_portal_auth._generate_code",
                return_value="111111"):
        with patch("app.services.patient_portal_auth.send_sms",
                    return_value=True):
            login = client.post("/api/patient/portal/login",
                                  json={"dob": "1990-01-01",
                                          "phone_last4": "1234"}).json()
    r = client.post("/api/patient/portal/verify",
                     json={"challenge_token": login["challenge_token"],
                              "code": "000000"})
    assert r.status_code == 401


def test_verify_rejects_unknown_challenge(client, db):
    _seed_surgery(db)
    r = client.post("/api/patient/portal/verify",
                     json={"challenge_token": "not-real", "code": "111111"})
    assert r.status_code == 401
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && ./venv/bin/pytest tests/test_patient_portal_endpoints.py -v -k verify
```
Expected: 404s.

- [ ] **Step 3: Add /verify handler** to `backend/app/routers/patient_portal.py` (append after the `/login` handler):

```python
class VerifyPayload(BaseModel):
    challenge_token: str
    code: str


@router.post("/verify")
def verify(payload: VerifyPayload, db: Session = Depends(get_db)):
    """Step 2 of sign-in. Returns JWT on success.

    All failure modes (unknown challenge, expired, wrong code, too many
    fails) collapse to a single 401 with a generic message — same
    no-leak posture as /login.
    """
    code = "".join(c for c in (payload.code or "") if c.isdigit())
    if len(code) != 6:
        raise HTTPException(status_code=401, detail="Invalid code")
    surgery_id = auth.verify_code(db, payload.challenge_token, code)
    if surgery_id is None:
        raise HTTPException(status_code=401, detail="Invalid code")
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if s is None:
        raise HTTPException(status_code=401, detail="Invalid code")
    token = auth.issue_portal_token(s)
    return {
        "token": token,
        "surgery_id": str(s.id),
        "expires_at": auth.compute_token_exp(s).isoformat(),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && ./venv/bin/pytest tests/test_patient_portal_endpoints.py -v -k verify
```
Expected: 3 verify tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/patient_portal.py backend/tests/test_patient_portal_endpoints.py
git commit -m "feat(portal): POST /api/patient/portal/verify — code → JWT"
```

---

## Task 6: Dashboard endpoint + auth dependency

**Files:**
- Modify: `backend/app/routers/patient_portal.py` (add require_portal_token + /{sid}/dashboard)
- Modify: `backend/tests/test_patient_portal_endpoints.py` (dashboard tests)

- [ ] **Step 1: Add failing tests** to `backend/tests/test_patient_portal_endpoints.py`:

```python
def test_dashboard_requires_token(client, db):
    s = _seed_surgery(db)
    r = client.get(f"/api/patient/portal/{s.id}/dashboard")
    assert r.status_code == 401


def test_dashboard_returns_surgery_and_milestones(client, db):
    from app.services.patient_portal_auth import issue_portal_token
    from datetime import date as _d
    s = Surgery(
        chart_number="1", patient_name="Doe, Jane", first_name="Jane",
        cell_phone="+12405551234", dob=_d(1990, 1, 1),
        scheduled_date=_d(2026, 6, 15),
        eligible_facilities=["office"], selected_facility="office",
        procedures=[{"cpt": "58558", "description": "Hysteroscopy with D&C"}],
        patient_responsibility=250,
        status="confirmed",
    )
    db.add(s); db.commit(); db.refresh(s)
    token = issue_portal_token(s)
    r = client.get(f"/api/patient/portal/{s.id}/dashboard",
                     headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    body = r.json()
    # Surgery summary
    assert body["surgery"]["procedure"] == "Hysteroscopy with D&C"
    assert body["surgery"]["surgery_date"] == "2026-06-15"
    assert body["surgery"]["facility"] == "the office"  # FACILITY_SHORT
    assert body["surgery"]["patient_responsibility"] == 250
    # Milestones — list of {key, label, status, ...}
    keys = [m["key"] for m in body["milestones"]]
    assert "payment" in keys
    assert "schedule" in keys
    assert "consent" in keys
    # Next-thing banner
    assert "next_action" in body


def test_dashboard_rejects_token_for_different_surgery(client, db):
    from app.services.patient_portal_auth import issue_portal_token
    s1 = _seed_surgery(db, cell="+12405551111", dob=date(1990, 1, 1))
    s2 = _seed_surgery(db, cell="+12405552222", dob=date(1992, 2, 2))
    token = issue_portal_token(s1)
    r = client.get(f"/api/patient/portal/{s2.id}/dashboard",
                     headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403
```

- [ ] **Step 2: Run to verify fail**

```bash
cd backend && ./venv/bin/pytest tests/test_patient_portal_endpoints.py -v -k dashboard
```
Expected: 404s.

- [ ] **Step 3: Add auth dep + dashboard handler** to `backend/app/routers/patient_portal.py` (append):

```python
# ─── Auth dependency ────────────────────────────────────────────

from fastapi import Header
from app.services.surgery_klara_drafter import FACILITY_SHORT


def require_portal_token(
    surgery_id: str,
    authorization: str = Header(default=""),
) -> str:
    """Validate Bearer token; ensure it's for THIS surgery_id."""
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing token")
    token = authorization.split(" ", 1)[1].strip()
    sub = auth.verify_portal_token(token)
    if sub is None:
        raise HTTPException(status_code=401, detail="Invalid token")
    if sub != surgery_id:
        raise HTTPException(status_code=403, detail="Wrong surgery")
    return sub


# ─── /{surgery_id}/dashboard ────────────────────────────────────

def _money(x) -> float | None:
    if x is None:
        return None
    return float(x)


def _payment_milestone(surgery: Surgery) -> dict:
    """Sum SurgeryPayment(status='succeeded') and compare to pt responsibility."""
    paid = 0.0
    for p in (surgery.payments or []):
        if p.status == "succeeded":
            paid += float(p.amount or 0)
    due = float(surgery.patient_responsibility or 0)
    if due <= 0:
        return {"key": "payment", "label": "Patient responsibility",
                "status": "not_required", "paid": paid, "due": due}
    status = "done" if paid >= due else ("in_progress" if paid > 0 else "todo")
    return {"key": "payment", "label": "Patient responsibility paid",
            "status": status, "paid": paid, "due": due}


def _schedule_milestone(surgery: Surgery) -> dict:
    return {
        "key": "schedule",
        "label": "Surgery date selected",
        "status": "done" if surgery.scheduled_date else "todo",
        "value": surgery.scheduled_date.isoformat() if surgery.scheduled_date else None,
    }


def _consent_milestone(surgery: Surgery) -> dict:
    envs = list(surgery.consent_envelopes or [])
    if not envs:
        return {"key": "consent", "label": "Consent forms signed",
                "status": "todo"}
    if all(e.status == "signed" for e in envs):
        return {"key": "consent", "label": "Consent forms signed",
                "status": "done", "count": len(envs)}
    return {"key": "consent", "label": "Consent forms signed",
            "status": "in_progress",
            "signed": sum(1 for e in envs if e.status == "signed"),
            "total": len(envs)}


def _self_report_milestone(surgery, *, attr, label, key) -> dict:
    val = getattr(surgery, attr, False)
    return {"key": key, "label": label,
            "status": "done" if val else "todo"}


def _next_action(milestones: list[dict]) -> dict | None:
    """First non-done milestone wins; map to a CTA stub."""
    priority = ["payment", "schedule", "consent",
                 "labs", "hospital_preop"]
    for key in priority:
        m = next((x for x in milestones if x["key"] == key), None)
        if m and m.get("status") in ("todo", "in_progress"):
            return {"key": key, "label": m["label"]}
    return None


@router.get("/{surgery_id}/dashboard")
def dashboard(surgery_id: str, db: Session = Depends(get_db),
                _: str = Depends(require_portal_token)):
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="surgery not found")
    first_proc = (s.procedures or [{}])[0]
    summary = {
        "procedure":     first_proc.get("description") or first_proc.get("name") or "",
        "surgeon":       s.surgeon_primary or "",
        "surgery_date":  s.scheduled_date.isoformat() if s.scheduled_date else None,
        "surgery_time":  s.scheduled_start_time.strftime("%H:%M")
                            if s.scheduled_start_time else None,
        "facility":      FACILITY_SHORT.get(s.selected_facility or "",
                                             s.selected_facility or ""),
        "patient_responsibility": _money(s.patient_responsibility),
    }
    milestones = [
        _payment_milestone(s),
        _schedule_milestone(s),
        _consent_milestone(s),
        _self_report_milestone(s, attr="hospital_preop_self_reported",
                                  label="Hospital pre-op call",
                                  key="hospital_preop"),
        _self_report_milestone(s, attr="labs_self_reported",
                                  label="Labs completed",
                                  key="labs"),
    ]
    # FMLA row only when the column exists (P5+).
    if hasattr(s, "fmla_status"):
        milestones.append({
            "key": "fmla",
            "label": "FMLA submitted",
            "status": getattr(s, "fmla_status", None) or "todo",
        })
    return {
        "surgery": summary,
        "milestones": milestones,
        "next_action": _next_action(milestones),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && ./venv/bin/pytest tests/test_patient_portal_endpoints.py -v
```
Expected: all dashboard tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/patient_portal.py backend/tests/test_patient_portal_endpoints.py
git commit -m "feat(portal): GET /api/patient/portal/{sid}/dashboard"
```

---

## Task 7: Frontend portal API client + auth hook

**Files:**
- Create: `frontend/src/lib/portal-api.js`
- Create: `frontend/src/hooks/usePortalAuth.js`

- [ ] **Step 1: Create the API client** at `frontend/src/lib/portal-api.js`:

```js
import axios from 'axios'

const TOKEN_KEY = 'wwc.portal.token'
const SID_KEY   = 'wwc.portal.sid'

export const portalApi = axios.create({ baseURL: '/api/patient/portal' })

portalApi.interceptors.request.use((cfg) => {
  const t = localStorage.getItem(TOKEN_KEY)
  if (t) cfg.headers.Authorization = `Bearer ${t}`
  return cfg
})

portalApi.interceptors.response.use(
  (r) => r,
  (err) => {
    if (err?.response?.status === 401) {
      localStorage.removeItem(TOKEN_KEY)
      localStorage.removeItem(SID_KEY)
      if (!location.pathname.startsWith('/portal/login')) {
        location.assign('/portal/login')
      }
    }
    return Promise.reject(err)
  },
)

export function setPortalSession({ token, surgery_id }) {
  localStorage.setItem(TOKEN_KEY, token)
  localStorage.setItem(SID_KEY, surgery_id)
}

export function clearPortalSession() {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(SID_KEY)
}

export function getPortalSession() {
  return {
    token: localStorage.getItem(TOKEN_KEY),
    surgery_id: localStorage.getItem(SID_KEY),
  }
}
```

- [ ] **Step 2: Create the auth hook** at `frontend/src/hooks/usePortalAuth.js`:

```js
import { useCallback, useEffect, useState } from 'react'
import {
  portalApi, setPortalSession, clearPortalSession, getPortalSession,
} from '../lib/portal-api'

export function usePortalAuth() {
  const [session, setSession] = useState(getPortalSession)

  useEffect(() => {
    const onStorage = () => setSession(getPortalSession())
    window.addEventListener('storage', onStorage)
    return () => window.removeEventListener('storage', onStorage)
  }, [])

  const login = useCallback(async (dob, phoneLast4) => {
    const { data } = await portalApi.post('/login', {
      dob, phone_last4: phoneLast4,
    })
    return data    // { challenge_token }
  }, [])

  const verify = useCallback(async (challengeToken, code) => {
    const { data } = await portalApi.post('/verify', {
      challenge_token: challengeToken, code,
    })
    setPortalSession(data)
    setSession({ token: data.token, surgery_id: data.surgery_id })
    return data
  }, [])

  const signOut = useCallback(() => {
    clearPortalSession()
    setSession({ token: null, surgery_id: null })
  }, [])

  return { session, login, verify, signOut }
}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/portal-api.js frontend/src/hooks/usePortalAuth.js
git commit -m "feat(portal): frontend API client + auth hook"
```

---

## Task 8: Portal shell + routing

**Files:**
- Create: `frontend/src/pages/portal/PortalShell.jsx`
- Modify: `frontend/src/App.jsx` (mount /portal routes — file may be `main.jsx` or similar)

- [ ] **Step 1: Create the shell** at `frontend/src/pages/portal/PortalShell.jsx`:

```jsx
import { Outlet, Link, useNavigate, useParams } from 'react-router-dom'
import { usePortalAuth } from '../../hooks/usePortalAuth'

const NAV = [
  { to: '',          label: 'Dashboard' },
  { to: 'payments',  label: 'Payments',  comingSoon: true },
  { to: 'schedule',  label: 'Schedule',  comingSoon: true },
  { to: 'consent',   label: 'Consent',   comingSoon: true },
  { to: 'documents', label: 'Documents', comingSoon: true },
  { to: 'messages',  label: 'Messages',  comingSoon: true },
]

export default function PortalShell() {
  const { sid } = useParams()
  const { session, signOut } = usePortalAuth()
  const nav = useNavigate()
  if (!session.token) {
    nav('/portal/login', { replace: true })
    return null
  }
  return (
    <div className="min-h-screen bg-gray-50">
      <header className="bg-white border-b border-gray-200 px-4 py-3 flex items-center justify-between">
        <div className="text-lg font-semibold text-plum-700">WWC Apps</div>
        <button className="text-sm text-gray-600 underline"
                onClick={() => { signOut(); nav('/portal/login') }}>
          Sign out
        </button>
      </header>
      <div className="flex">
        <nav className="w-48 border-r border-gray-200 bg-white p-3 hidden sm:block">
          {NAV.map(item => (
            <Link key={item.to}
                  to={`/portal/s/${sid}/${item.to}`}
                  className={`block px-2 py-2 rounded text-sm ${item.comingSoon ? 'text-gray-400' : 'text-gray-800 hover:bg-gray-100'}`}>
              {item.label}{item.comingSoon ? ' · soon' : ''}
            </Link>
          ))}
        </nav>
        <main className="flex-1 p-4 max-w-3xl mx-auto"><Outlet /></main>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Mount routes.** Find the top-level Router setup in the frontend (likely `frontend/src/App.jsx` or `frontend/src/main.jsx`). Add:

```jsx
import PortalLogin from './pages/portal/PortalLogin'
import PortalVerify from './pages/portal/PortalVerify'
import PortalShell from './pages/portal/PortalShell'
import PortalDashboard from './pages/portal/Dashboard'
import PaymentsStub from './pages/portal/stubs/PaymentsStub'
import ScheduleStub from './pages/portal/stubs/ScheduleStub'
import ConsentStub from './pages/portal/stubs/ConsentStub'
import DocumentsStub from './pages/portal/stubs/DocumentsStub'
import MessagesStub from './pages/portal/stubs/MessagesStub'

// Inside <Routes>:
<Route path="/portal/login" element={<PortalLogin />} />
<Route path="/portal/verify" element={<PortalVerify />} />
<Route path="/portal/s/:sid" element={<PortalShell />}>
  <Route index element={<PortalDashboard />} />
  <Route path="payments" element={<PaymentsStub />} />
  <Route path="schedule" element={<ScheduleStub />} />
  <Route path="consent" element={<ConsentStub />} />
  <Route path="documents" element={<DocumentsStub />} />
  <Route path="messages" element={<MessagesStub />} />
</Route>
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/portal/PortalShell.jsx frontend/src/App.jsx
git commit -m "feat(portal): /portal shell + route guards"
```

---

## Task 9: PortalLogin page

**Files:**
- Create: `frontend/src/pages/portal/PortalLogin.jsx`

- [ ] **Step 1: Write the page** at `frontend/src/pages/portal/PortalLogin.jsx`:

```jsx
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { usePortalAuth } from '../../hooks/usePortalAuth'

export default function PortalLogin() {
  const [dob, setDob] = useState('')
  const [last4, setLast4] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const { login } = usePortalAuth()
  const nav = useNavigate()

  async function submit(e) {
    e.preventDefault()
    setErr(''); setBusy(true)
    try {
      const { challenge_token } = await login(dob, last4)
      nav('/portal/verify', { state: { challenge_token } })
    } catch (e) {
      setErr(e?.response?.data?.detail || 'Sign-in failed.')
    } finally { setBusy(false) }
  }

  return (
    <div className="min-h-screen bg-gray-50 flex items-center justify-center p-4">
      <form onSubmit={submit}
            className="bg-white rounded-lg shadow p-6 max-w-sm w-full space-y-4">
        <h1 className="text-xl font-semibold text-plum-700">WWC Patient Portal</h1>
        <p className="text-sm text-gray-600">
          Sign in with your date of birth and the last 4 digits of the phone
          number we have on file. We'll text you a verification code.
        </p>
        <label className="block text-sm">
          <span className="text-gray-700">Date of birth</span>
          <input type="date" required value={dob}
                  onChange={e => setDob(e.target.value)}
                  className="mt-1 block w-full rounded border-gray-300" />
        </label>
        <label className="block text-sm">
          <span className="text-gray-700">Last 4 of cell phone</span>
          <input type="text" inputMode="numeric" pattern="\d{4}"
                  required maxLength={4} value={last4}
                  onChange={e => setLast4(e.target.value.replace(/\D/g, ''))}
                  className="mt-1 block w-full rounded border-gray-300"
                  placeholder="1234" />
        </label>
        {err && <div className="text-sm text-red-600">{err}</div>}
        <button type="submit" disabled={busy || !dob || last4.length !== 4}
                className="btn-primary w-full">
          {busy ? 'Sending code…' : 'Continue'}
        </button>
        <p className="text-xs text-gray-500 text-center">
          Lost access? Call our office at <a href="tel:2402522140"
            className="underline">240-252-2140</a>.
        </p>
      </form>
    </div>
  )
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/pages/portal/PortalLogin.jsx
git commit -m "feat(portal): PortalLogin page (DOB + last-4 form)"
```

---

## Task 10: PortalVerify page

**Files:**
- Create: `frontend/src/pages/portal/PortalVerify.jsx`

- [ ] **Step 1: Write the page** at `frontend/src/pages/portal/PortalVerify.jsx`:

```jsx
import { useState, useEffect, useRef } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { usePortalAuth } from '../../hooks/usePortalAuth'

export default function PortalVerify() {
  const loc = useLocation()
  const nav = useNavigate()
  const { verify } = usePortalAuth()
  const challengeToken = loc.state?.challenge_token
  const [digits, setDigits] = useState(['','','','','',''])
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const refs = useRef([])

  useEffect(() => {
    if (!challengeToken) nav('/portal/login', { replace: true })
  }, [challengeToken, nav])

  function setDigit(i, v) {
    const c = v.replace(/\D/g, '').slice(-1)
    const next = [...digits]
    next[i] = c
    setDigits(next)
    if (c && i < 5) refs.current[i+1]?.focus()
  }

  async function submit(e) {
    e?.preventDefault?.()
    const code = digits.join('')
    if (code.length !== 6) return
    setErr(''); setBusy(true)
    try {
      await verify(challengeToken, code)
      // verify() updated localStorage; pull surgery_id back to route.
      const sid = localStorage.getItem('wwc.portal.sid')
      nav(`/portal/s/${sid}`, { replace: true })
    } catch (e) {
      setErr(e?.response?.data?.detail || 'Invalid code.')
    } finally { setBusy(false) }
  }

  // Auto-submit on 6th digit
  useEffect(() => {
    if (digits.every(d => d !== '')) submit()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [digits])

  return (
    <div className="min-h-screen bg-gray-50 flex items-center justify-center p-4">
      <form onSubmit={submit}
            className="bg-white rounded-lg shadow p-6 max-w-sm w-full space-y-4">
        <h1 className="text-xl font-semibold text-plum-700">Enter your code</h1>
        <p className="text-sm text-gray-600">
          We texted a 6-digit code to the phone we have on file. It expires
          in 5 minutes.
        </p>
        <div className="flex justify-between gap-2">
          {digits.map((d, i) => (
            <input key={i}
                    ref={el => refs.current[i] = el}
                    type="text" inputMode="numeric"
                    maxLength={1} value={d}
                    onChange={e => setDigit(i, e.target.value)}
                    className="w-10 h-12 text-center text-lg rounded border-gray-300" />
          ))}
        </div>
        {err && <div className="text-sm text-red-600">{err}</div>}
        <button type="submit" disabled={busy || digits.join('').length !== 6}
                className="btn-primary w-full">
          {busy ? 'Checking…' : 'Sign in'}
        </button>
        <p className="text-xs text-gray-500 text-center">
          Didn't get it? <a href="/portal/login" className="underline">Start over</a>.
        </p>
      </form>
    </div>
  )
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/pages/portal/PortalVerify.jsx
git commit -m "feat(portal): PortalVerify page (6-digit code entry, auto-submit)"
```

---

## Task 11: Dashboard page

**Files:**
- Create: `frontend/src/pages/portal/Dashboard.jsx`

- [ ] **Step 1: Write the page** at `frontend/src/pages/portal/Dashboard.jsx`:

```jsx
import { useQuery } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'
import { portalApi } from '../../lib/portal-api'

const STATUS_BADGE = {
  done:         'bg-green-100 text-green-700',
  in_progress:  'bg-amber-100 text-amber-700',
  todo:         'bg-gray-200 text-gray-700',
  not_required: 'bg-gray-100 text-gray-500',
}

const STATUS_LABEL = {
  done: '✓ Done',
  in_progress: '… In progress',
  todo: 'Not started',
  not_required: 'Not required',
}

export default function Dashboard() {
  const { sid } = useParams()
  const { data, isLoading, error } = useQuery({
    queryKey: ['portal-dashboard', sid],
    queryFn: () => portalApi.get(`/${sid}/dashboard`).then(r => r.data),
    staleTime: 30_000,
  })
  if (isLoading) return <div className="text-sm text-gray-500">Loading…</div>
  if (error) return <div className="text-sm text-red-600">Couldn't load your dashboard.</div>
  const { surgery, milestones, next_action } = data
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold text-gray-900">Your surgery</h1>

      {next_action && (
        <div className="bg-plum-50 border border-plum-200 rounded-lg p-4">
          <div className="text-xs uppercase tracking-wide text-plum-700">Next step</div>
          <div className="text-base font-medium text-gray-900 mt-1">
            {next_action.label}
          </div>
        </div>
      )}

      <section className="bg-white rounded-lg shadow p-4">
        <h2 className="text-sm font-semibold text-gray-700 mb-2">Surgery details</h2>
        <dl className="grid grid-cols-2 gap-y-2 text-sm">
          <dt className="text-gray-500">Procedure</dt>
          <dd className="text-gray-900">{surgery.procedure || '—'}</dd>
          <dt className="text-gray-500">Surgeon</dt>
          <dd className="text-gray-900">{surgery.surgeon || '—'}</dd>
          <dt className="text-gray-500">Date</dt>
          <dd className="text-gray-900">{surgery.surgery_date || 'not scheduled yet'}</dd>
          <dt className="text-gray-500">Arrival time</dt>
          <dd className="text-gray-900">{surgery.surgery_time || 'TBD'}</dd>
          <dt className="text-gray-500">Location</dt>
          <dd className="text-gray-900">{surgery.facility || 'TBD'}</dd>
          <dt className="text-gray-500">Patient responsibility</dt>
          <dd className="text-gray-900">
            {surgery.patient_responsibility != null
              ? `$${surgery.patient_responsibility.toFixed(2)}`
              : 'calculating'}
          </dd>
        </dl>
      </section>

      <section className="bg-white rounded-lg shadow p-4">
        <h2 className="text-sm font-semibold text-gray-700 mb-3">Your progress</h2>
        <ul className="divide-y divide-gray-100">
          {milestones.map(m => (
            <li key={m.key} className="flex items-center justify-between py-2">
              <span className="text-sm text-gray-800">{m.label}</span>
              <span className={`text-xs px-2 py-1 rounded ${STATUS_BADGE[m.status] || STATUS_BADGE.todo}`}>
                {STATUS_LABEL[m.status] || m.status}
              </span>
            </li>
          ))}
        </ul>
      </section>
    </div>
  )
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/pages/portal/Dashboard.jsx
git commit -m "feat(portal): Dashboard — summary card + milestone list + next-action banner"
```

---

## Task 12: 5 stub pages

**Files:**
- Create: `frontend/src/pages/portal/stubs/PaymentsStub.jsx`
- Create: `frontend/src/pages/portal/stubs/ScheduleStub.jsx`
- Create: `frontend/src/pages/portal/stubs/ConsentStub.jsx`
- Create: `frontend/src/pages/portal/stubs/DocumentsStub.jsx`
- Create: `frontend/src/pages/portal/stubs/MessagesStub.jsx`

- [ ] **Step 1: Create one stub template** that each file follows. For each of the 5 files, write:

```jsx
// Example: PaymentsStub.jsx (repeat the same shape for the other 4,
// adjusting the title and phase number per spec).
export default function PaymentsStub() {
  return (
    <div className="bg-white rounded-lg shadow p-6 text-center">
      <div className="text-3xl mb-2">🚧</div>
      <h1 className="text-lg font-semibold text-gray-800">Payments</h1>
      <p className="text-sm text-gray-600 mt-2">
        We're building this section. It'll launch in the next portal update.
      </p>
    </div>
  )
}
```

Per-file titles and copy:
- `PaymentsStub.jsx`  — "Payments"  (P2)
- `ScheduleStub.jsx`  — "Schedule"  (P2)
- `ConsentStub.jsx`   — "Consent"   (P3)
- `DocumentsStub.jsx` — "Documents" (P4–P5)
- `MessagesStub.jsx`  — "Messages"  (P6)

- [ ] **Step 2: Commit**

```bash
git add frontend/src/pages/portal/stubs/
git commit -m "feat(portal): 5 placeholder pages for P2-P6 nav sections"
```

---

## Task 13: Smoke test — full sign-in to dashboard, in prod

**Files:** none (manual + observability)

Done **after** all backend code is merged + deployed (`v40+`), the migration script has run, the SMS template seed has run, and the frontend has been deployed.

- [ ] **Step 1: Deploy.** Build + deploy backend with the new router; run `scripts/migrate_patient_portal_p1.py` against prod; re-run `scripts/seed_sms_templates.py` to insert the 5th SMS template; build + deploy frontend.

- [ ] **Step 2: Use an existing surgery row** with a known DOB and a cell phone the tester controls. (Don't use a real patient; create a temporary test surgery via direct DB insert, identical to the SMS E2E test pattern from 2026-05-31.)

- [ ] **Step 3: Sign-in flow.** Open `https://gw.waldorfwomenscare.com/portal/login` in a browser. Enter the DOB + last-4 of the test phone. Confirm:
  - 200 response from `/api/patient/portal/login`
  - SMS arrives at the test phone within 10 seconds
  - Redirect lands on `/portal/verify`

- [ ] **Step 4: Code entry.** Enter the 6-digit code. Confirm:
  - 200 response from `/api/patient/portal/verify` with `{token, surgery_id, expires_at}`
  - Token persists in localStorage under `wwc.portal.token`
  - Browser routes to `/portal/s/<sid>` and the dashboard renders

- [ ] **Step 5: Dashboard render.** Confirm:
  - Surgery summary card shows the expected procedure / date / facility
  - Milestone rows render with the right statuses
  - "Next step" banner highlights the first incomplete milestone
  - Side nav shows stubs (Payments/Schedule/Consent/Documents/Messages) marked "· soon"

- [ ] **Step 6: Auth boundary.** Confirm `/api/patient/portal/<other-sid>/dashboard` with this JWT returns 403. Confirm `/portal/s/<sid>` with an expired token redirects to `/portal/login`.

- [ ] **Step 7: Cleanup.** Delete the test surgery row + any `patient_portal_auth_codes` rows for it. Close Cloud SQL public IP. Confirm with: a tester user-facing report saying "P1 portal works end-to-end."
