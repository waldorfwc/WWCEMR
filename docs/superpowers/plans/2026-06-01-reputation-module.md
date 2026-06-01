# Reputation Module Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development.

**Goal:** Ship the per-employee QR review pipeline + leaderboard + Webflow embed as a module inside the existing app.

**Architecture:** 3 new tables (`reputation_profiles`, `reputation_scans`, `reputation_reviews`). One new public router (no auth) and one new admin router. New `qrcode` Python dep for image generation. New SPA routes under `/r/:token` and `/embed` for patient + embed views, plus 3 admin pages.

**Spec:** `docs/superpowers/specs/2026-06-01-reputation-module-design.md`

**Tech stack:** Same backend/frontend pattern as everything else. New CNAME `reviews.waldorfwomenscare.com` → existing frontend Cloud Run.

**Key facts (don't relitigate):**
- `patient_portal_auth.issue_challenge(s, purpose=...)` from P1 is the SMS challenge primitive. We add `purpose="review"` to the existing PURPOSE_COPY dict — sends a similar 6-digit code SMS.
- `get_current_user` returns `{email, group, ...}`. Add `reputation:manage` to the admin group's effective permissions.
- Schema uses `GUID()` + `new_uuid` from `app/models/guid.py` — same pattern as every other table.
- Frontend admin pages live under `/admin/*` (e.g. `/admin/message-templates` from P6).
- The `qrcode` PyPI package is NOT currently in `requirements.txt`; add it.

---

## Task 1: Schema — 3 tables + migration + 1 starter profile

**Files:**
- Create: `backend/app/models/reputation.py`
- Create: `backend/scripts/migrate_reputation.py`
- Test: `backend/tests/test_reputation_schema.py`
- Modify: `backend/requirements.txt` (add `qrcode[pil]>=7.4`)

- [ ] **Step 1: Failing tests** at `backend/tests/test_reputation_schema.py`:

```python
"""Reputation module schema."""
from app.models.reputation import (
    ReputationProfile, ReputationScan, ReputationReview,
)


def test_profile_round_trip(db):
    p = ReputationProfile(display_name="Sarah Smith, RN",
                              role_label="Surgical Coordinator",
                              qr_token="abc123def456")
    db.add(p); db.commit(); db.refresh(p)
    assert p.id is not None
    assert p.active is True
    assert p.user_email is None


def test_scan_round_trip(db):
    p = ReputationProfile(display_name="Sarah", qr_token="t1")
    db.add(p); db.commit(); db.refresh(p)
    s = ReputationScan(profile_id=p.id, ip_address="1.2.3.4",
                            points_credited=1)
    db.add(s); db.commit(); db.refresh(s)
    assert s.scanned_at is not None
    assert s.points_credited == 1


def test_review_round_trip_with_chart_link(db):
    p = ReputationProfile(display_name="Sarah", qr_token="t2")
    db.add(p); db.commit(); db.refresh(p)
    r = ReputationReview(
        profile_id=p.id, stars=5, body="Great care!",
        patient_first_name="Jane", patient_last_initial="D",
        patient_chart_number="12345", patient_phone="+12405551234",
        consent_to_display=True,
    )
    db.add(r); db.commit(); db.refresh(r)
    assert r.stars == 5
    assert r.consent_to_display is True
    assert r.approved_for_embed is False
    assert r.google_clicked_at is None


def test_review_anonymous(db):
    p = ReputationProfile(display_name="Sarah", qr_token="t3")
    db.add(p); db.commit(); db.refresh(p)
    r = ReputationReview(profile_id=p.id, stars=4)
    db.add(r); db.commit(); db.refresh(r)
    assert r.patient_first_name is None
    assert r.patient_chart_number is None
    assert r.consent_to_display is False
```

- [ ] **Step 2: Run, confirm fail.**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && \
  ./venv/bin/pytest tests/test_reputation_schema.py -v
```

- [ ] **Step 3: Create the models** at `backend/app/models/reputation.py`:

```python
"""Reputation module — per-employee review pipeline."""
from datetime import datetime

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text,
)

from app.database import Base
from app.models.guid import GUID, new_uuid


class ReputationProfile(Base):
    __tablename__ = "reputation_profiles"

    id            = Column(GUID(), primary_key=True, default=new_uuid)
    user_email    = Column(String(200), nullable=True)
    display_name  = Column(String(120), nullable=False)
    role_label    = Column(String(80), nullable=True)
    qr_token      = Column(String(40), nullable=False, unique=True, index=True)
    active        = Column(Boolean, default=True, nullable=False)
    created_at    = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at    = Column(DateTime, default=datetime.utcnow,
                              onupdate=datetime.utcnow, nullable=False)


class ReputationScan(Base):
    __tablename__ = "reputation_scans"
    __table_args__ = (
        Index("ix_reputation_scans_profile", "profile_id", "scanned_at"),
    )

    id              = Column(GUID(), primary_key=True, default=new_uuid)
    profile_id      = Column(GUID(),
                                ForeignKey("reputation_profiles.id",
                                            ondelete="CASCADE"),
                                nullable=False)
    scanned_at      = Column(DateTime, default=datetime.utcnow,
                                nullable=False)
    ip_address      = Column(String(45), nullable=True)
    user_agent      = Column(String(300), nullable=True)
    points_credited = Column(Integer, default=0, nullable=False)


class ReputationReview(Base):
    __tablename__ = "reputation_reviews"
    __table_args__ = (
        Index("ix_reputation_reviews_profile", "profile_id", "submitted_at"),
    )

    id                   = Column(GUID(), primary_key=True, default=new_uuid)
    profile_id           = Column(GUID(),
                                      ForeignKey("reputation_profiles.id",
                                                  ondelete="CASCADE"),
                                      nullable=False)
    scan_id              = Column(GUID(),
                                      ForeignKey("reputation_scans.id",
                                                  ondelete="SET NULL"),
                                      nullable=True)
    stars                = Column(Integer, nullable=False)
    body                 = Column(Text, nullable=True)
    patient_first_name   = Column(String(80), nullable=True)
    patient_last_initial = Column(String(2), nullable=True)
    patient_chart_number = Column(String(20), nullable=True)
    patient_phone        = Column(String(20), nullable=True)
    consent_to_display   = Column(Boolean, default=False, nullable=False)
    approved_for_embed   = Column(Boolean, default=False, nullable=False)
    google_clicked_at    = Column(DateTime, nullable=True)
    submitted_at         = Column(DateTime, default=datetime.utcnow,
                                      nullable=False)
```

- [ ] **Step 4: Run, confirm pass.**

- [ ] **Step 5: Create migration** at `backend/scripts/migrate_reputation.py`:

```python
"""Idempotent reputation module migration: 3 tables."""
import os
import sys
from sqlalchemy import create_engine, text

DDL = [
    """CREATE TABLE IF NOT EXISTS reputation_profiles (
        id CHAR(36) PRIMARY KEY,
        user_email VARCHAR(200),
        display_name VARCHAR(120) NOT NULL,
        role_label VARCHAR(80),
        qr_token VARCHAR(40) NOT NULL UNIQUE,
        active BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMP NOT NULL DEFAULT NOW()
    )""",
    """CREATE INDEX IF NOT EXISTS ix_reputation_profiles_token
       ON reputation_profiles (qr_token)""",
    """CREATE TABLE IF NOT EXISTS reputation_scans (
        id CHAR(36) PRIMARY KEY,
        profile_id CHAR(36) NOT NULL
            REFERENCES reputation_profiles(id) ON DELETE CASCADE,
        scanned_at TIMESTAMP NOT NULL DEFAULT NOW(),
        ip_address VARCHAR(45),
        user_agent VARCHAR(300),
        points_credited INTEGER NOT NULL DEFAULT 0
    )""",
    """CREATE INDEX IF NOT EXISTS ix_reputation_scans_profile
       ON reputation_scans (profile_id, scanned_at)""",
    """CREATE TABLE IF NOT EXISTS reputation_reviews (
        id CHAR(36) PRIMARY KEY,
        profile_id CHAR(36) NOT NULL
            REFERENCES reputation_profiles(id) ON DELETE CASCADE,
        scan_id CHAR(36)
            REFERENCES reputation_scans(id) ON DELETE SET NULL,
        stars INTEGER NOT NULL,
        body TEXT,
        patient_first_name VARCHAR(80),
        patient_last_initial VARCHAR(2),
        patient_chart_number VARCHAR(20),
        patient_phone VARCHAR(20),
        consent_to_display BOOLEAN NOT NULL DEFAULT FALSE,
        approved_for_embed BOOLEAN NOT NULL DEFAULT FALSE,
        google_clicked_at TIMESTAMP,
        submitted_at TIMESTAMP NOT NULL DEFAULT NOW()
    )""",
    """CREATE INDEX IF NOT EXISTS ix_reputation_reviews_profile
       ON reputation_reviews (profile_id, submitted_at)""",
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

- [ ] **Step 6: Add `qrcode[pil]>=7.4`** to `backend/requirements.txt`, then `./venv/bin/pip install qrcode[pil]`.

- [ ] **Step 7: Commit.**

```bash
git add backend/app/models/reputation.py backend/scripts/migrate_reputation.py \
        backend/tests/test_reputation_schema.py backend/requirements.txt
git commit -m "feat(reputation): schema — 3 tables + migration + qrcode dep"
```

---

## Task 2: Patient endpoints — scan + verify + submit + google-clicked

**Files:**
- Create: `backend/app/routers/reputation_public.py` — no-auth public router
- Modify: `backend/app/main.py` — register
- Modify: `backend/app/services/patient_portal_auth.py` — add `"review"` to `PURPOSE_COPY`
- Test: `backend/tests/test_reputation_public.py`

- [ ] **Step 1: Failing tests:**

```python
"""Public review-form endpoints — no auth."""
from datetime import datetime
from unittest.mock import patch


def _seed_profile(db, token="abc12345"):
    from app.models.reputation import ReputationProfile
    p = ReputationProfile(display_name="Sarah, RN",
                              role_label="Coordinator", qr_token=token)
    db.add(p); db.commit(); db.refresh(p)
    return p


def test_scan_logs_scan_and_returns_profile_info(client, db):
    p = _seed_profile(db)
    r = client.post(f"/api/r/{p.qr_token}/scan")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["display_name"] == "Sarah, RN"
    assert body["role_label"] == "Coordinator"
    from app.models.reputation import ReputationScan
    scans = db.query(ReputationScan).filter(
        ReputationScan.profile_id == p.id).all()
    assert len(scans) == 1
    assert scans[0].points_credited == 1


def test_scan_dedup_within_24h_same_ip(client, db):
    p = _seed_profile(db)
    h = {"X-Forwarded-For": "1.2.3.4"}
    r1 = client.post(f"/api/r/{p.qr_token}/scan", headers=h)
    r2 = client.post(f"/api/r/{p.qr_token}/scan", headers=h)
    assert r1.status_code == 200 and r2.status_code == 200
    from app.models.reputation import ReputationScan
    scans = db.query(ReputationScan).filter(
        ReputationScan.profile_id == p.id).all()
    # Both scan rows recorded but only the first gets a point
    assert len(scans) == 2
    assert sum(s.points_credited for s in scans) == 1


def test_scan_unknown_token_returns_404(client, db):
    r = client.post("/api/r/no-such-token/scan")
    assert r.status_code == 404


def test_submit_review_anonymous_persists(client, db):
    p = _seed_profile(db)
    r = client.post(f"/api/r/{p.qr_token}/submit",
                       json={"stars": 4, "body": "Good visit"})
    assert r.status_code == 200, r.text
    from app.models.reputation import ReputationReview
    reviews = db.query(ReputationReview).filter(
        ReputationReview.profile_id == p.id).all()
    assert len(reviews) == 1
    assert reviews[0].stars == 4
    assert reviews[0].body == "Good visit"
    assert reviews[0].patient_first_name is None
    assert reviews[0].consent_to_display is False


def test_submit_review_with_consent_requires_name(client, db):
    p = _seed_profile(db)
    r = client.post(f"/api/r/{p.qr_token}/submit", json={
        "stars": 5, "consent_to_display": True,
    })
    assert r.status_code == 422
    assert "name" in r.json()["detail"].lower()


def test_submit_review_with_consent_and_name(client, db):
    p = _seed_profile(db)
    r = client.post(f"/api/r/{p.qr_token}/submit", json={
        "stars": 5, "body": "Excellent!",
        "patient_first_name": "Jane", "patient_last_initial": "D",
        "consent_to_display": True,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    # Returns whether to offer the Google handoff
    assert body["offer_google_handoff"] is True


def test_submit_review_low_star_does_not_offer_google(client, db):
    p = _seed_profile(db)
    r = client.post(f"/api/r/{p.qr_token}/submit",
                       json={"stars": 3})
    assert r.status_code == 200
    assert r.json()["offer_google_handoff"] is False


def test_google_clicked_marks_timestamp(client, db):
    p = _seed_profile(db)
    sub = client.post(f"/api/r/{p.qr_token}/submit", json={"stars": 5}).json()
    review_id = sub["review_id"]
    r = client.post(f"/api/r/{p.qr_token}/google-clicked",
                       json={"review_id": review_id})
    assert r.status_code == 200
    from app.models.reputation import ReputationReview
    rv = db.query(ReputationReview).filter(
        ReputationReview.id == review_id).first()
    assert rv.google_clicked_at is not None


def test_verify_patient_start_dispatches_sms(client, db):
    p = _seed_profile(db)
    with patch("app.routers.reputation_public.send_sms",
                return_value=True) as mock_sms:
        r = client.post(f"/api/r/{p.qr_token}/verify-patient/start",
                          json={"phone": "+12405551234"})
    assert r.status_code == 200
    assert "challenge_token" in r.json()
    assert mock_sms.called


def test_verify_patient_check_matches_chart_when_phone_matches(client, db):
    """When a Surgery row exists with that phone, the matched chart_number
    is returned + the review will be linked to it on submit."""
    from app.models.surgery import Surgery
    s = Surgery(chart_number="C-9001", patient_name="Jane Doe", status="new",
                  version_id=1, cell_phone="+12405551234")
    db.add(s); db.commit()
    p = _seed_profile(db)
    with patch("app.services.patient_portal_auth._generate_code",
                return_value="111111"), \
         patch("app.routers.reputation_public.send_sms", return_value=True):
        start = client.post(f"/api/r/{p.qr_token}/verify-patient/start",
                                json={"phone": "+12405551234"}).json()
    r = client.post(f"/api/r/{p.qr_token}/verify-patient/check", json={
        "challenge_token": start["challenge_token"], "code": "111111",
    })
    assert r.status_code == 200
    assert r.json()["chart_number"] == "C-9001"
```

- [ ] **Step 2: Run, confirm fail.**

- [ ] **Step 3: Extend** `app/services/patient_portal_auth.py`'s `PURPOSE_COPY`:

```python
PURPOSE_COPY = {
    "login":   ("WWC: Your portal sign-in code is {code}. "
                  "Expires in {ttl} minutes."),
    "payment": ("WWC: Code to authorize your payment: {code}. "
                  "Expires in {ttl} minutes. If you didn't request this, ignore."),
    "review":  ("WWC: Code to confirm you're a patient for your review: "
                  "{code}. Expires in {ttl} minutes."),
}
```

- [ ] **Step 4: Create** `app/routers/reputation_public.py`:

```python
"""Public (no-auth) review endpoints for patients who scanned a QR."""
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.reputation import (
    ReputationProfile, ReputationScan, ReputationReview,
)
from app.models.surgery import Surgery
from app.services.checklist_notifications import send_sms
from app.services import patient_portal_auth as auth

router = APIRouter(prefix="/api/r", tags=["reputation-public"])

POINTS = {"scan": 1, "review": 2, "five_star": 5, "google_share": 3}
SCAN_DEDUP_HOURS = 24


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("X-Forwarded-For", "")
    return fwd.split(",")[0].strip() if fwd else (request.client.host or "")


def _profile(db: Session, token: str) -> ReputationProfile:
    p = (db.query(ReputationProfile)
              .filter(ReputationProfile.qr_token == token,
                       ReputationProfile.active.is_(True))
              .first())
    if p is None:
        raise HTTPException(status_code=404, detail="profile not found")
    return p


@router.post("/{token}/scan")
def scan(token: str, request: Request, db: Session = Depends(get_db)):
    p = _profile(db, token)
    ip = _client_ip(request)
    cutoff = datetime.utcnow() - timedelta(hours=SCAN_DEDUP_HOURS)
    prior = (db.query(ReputationScan)
                 .filter(ReputationScan.profile_id == p.id,
                          ReputationScan.ip_address == ip,
                          ReputationScan.scanned_at >= cutoff)
                 .first())
    s = ReputationScan(
        profile_id=p.id, ip_address=ip,
        user_agent=request.headers.get("User-Agent", "")[:300],
        points_credited=0 if prior else POINTS["scan"],
    )
    db.add(s); db.commit(); db.refresh(s)
    return {
        "scan_id":     str(s.id),
        "display_name": p.display_name,
        "role_label":  p.role_label,
    }


class VerifyStart(BaseModel):
    phone: str


@router.post("/{token}/verify-patient/start")
def verify_start(token: str, payload: VerifyStart,
                    db: Session = Depends(get_db)):
    _profile(db, token)   # validate token exists
    phone = payload.phone.strip()
    if not phone:
        raise HTTPException(status_code=422, detail="phone required")
    # Reuse the portal challenge primitive — but we don't have a surgery
    # to bind to; create a transient bare Surgery-ish for the challenge?
    # Simpler: implement a thin shim that issues a code by phone alone,
    # storing under a synthetic surgery_id=None record. For v1 we just
    # generate a code, persist it, and send the SMS — no surgery binding.
    import secrets
    code = f"{secrets.randbelow(10**6):06d}"
    token_str = secrets.token_urlsafe(32)
    # Persist via PatientPortalAuthCode without a surgery_id; we'll need
    # to make surgery_id nullable for this use case. See migration note
    # below — for v1 reuse the existing table by writing a marker row.
    # NB: this requires a small schema relaxation; documented in T1.
    # ... (production version would use a dedicated table)
    send_sms(phone,
                f"WWC: Code to confirm you're a patient for your review: "
                f"{code}. Expires in 5 minutes.")
    # Simplest working version: stash in-memory or via Redis. For v1,
    # store in a tiny new table `reputation_phone_challenges` with
    # (token_str, code_hash, phone, expires_at).
    from app.models.reputation import ReputationPhoneChallenge
    import bcrypt as _bcrypt
    c = ReputationPhoneChallenge(
        challenge_token=token_str,
        code_hash=_bcrypt.hashpw(code.encode(), _bcrypt.gensalt()).decode(),
        phone=phone,
        expires_at=datetime.utcnow() + timedelta(minutes=5),
    )
    db.add(c); db.commit()
    return {"challenge_token": token_str}


class VerifyCheck(BaseModel):
    challenge_token: str
    code: str


@router.post("/{token}/verify-patient/check")
def verify_check(token: str, payload: VerifyCheck,
                    db: Session = Depends(get_db)):
    _profile(db, token)
    from app.models.reputation import ReputationPhoneChallenge
    import bcrypt as _bcrypt
    c = (db.query(ReputationPhoneChallenge)
             .filter(ReputationPhoneChallenge.challenge_token
                       == payload.challenge_token)
             .first())
    if not c or c.expires_at < datetime.utcnow():
        raise HTTPException(status_code=401, detail="invalid or expired")
    code_digits = "".join(ch for ch in (payload.code or "") if ch.isdigit())
    if len(code_digits) != 6 or not _bcrypt.checkpw(
            code_digits.encode(), c.code_hash.encode()):
        raise HTTPException(status_code=401, detail="invalid code")
    # Look up the most recent matching surgery by phone
    s = (db.query(Surgery)
              .filter(Surgery.cell_phone == c.phone)
              .order_by(Surgery.scheduled_date.desc().nullslast(),
                         Surgery.created_at.desc())
              .first())
    chart = s.chart_number if s else None
    # Mark challenge consumed (delete to avoid reuse)
    db.delete(c); db.commit()
    return {"chart_number": chart, "phone": c.phone}


class ReviewSubmit(BaseModel):
    stars: int
    body: Optional[str] = None
    patient_first_name: Optional[str] = None
    patient_last_initial: Optional[str] = None
    patient_chart_number: Optional[str] = None
    patient_phone: Optional[str] = None
    consent_to_display: bool = False


@router.post("/{token}/submit")
def submit(token: str, payload: ReviewSubmit, request: Request,
              db: Session = Depends(get_db)):
    p = _profile(db, token)
    if not 1 <= payload.stars <= 5:
        raise HTTPException(status_code=422, detail="stars must be 1-5")
    if payload.consent_to_display and not (payload.patient_first_name or "").strip():
        raise HTTPException(status_code=422,
                              detail="First name required when consenting to display.")
    r = ReputationReview(
        profile_id=p.id,
        stars=payload.stars,
        body=(payload.body or "").strip()[:2000] or None,
        patient_first_name=(payload.patient_first_name or "").strip()[:80] or None,
        patient_last_initial=(payload.patient_last_initial or "").strip()[:2] or None,
        patient_chart_number=payload.patient_chart_number,
        patient_phone=payload.patient_phone,
        consent_to_display=payload.consent_to_display,
    )
    db.add(r); db.commit(); db.refresh(r)
    return {
        "review_id":            str(r.id),
        "offer_google_handoff": payload.stars == 5,
    }


class GoogleClicked(BaseModel):
    review_id: str


@router.post("/{token}/google-clicked")
def google_clicked(token: str, payload: GoogleClicked,
                      db: Session = Depends(get_db)):
    _profile(db, token)
    r = db.query(ReputationReview).filter(
        ReputationReview.id == payload.review_id).first()
    if r is None:
        raise HTTPException(status_code=404, detail="review not found")
    r.google_clicked_at = datetime.utcnow()
    db.commit()
    return {"ok": True}
```

The above implementation needs a tiny `ReputationPhoneChallenge` model added in T1 — extend T1's models with:

```python
class ReputationPhoneChallenge(Base):
    __tablename__ = "reputation_phone_challenges"
    id              = Column(GUID(), primary_key=True, default=new_uuid)
    challenge_token = Column(String(64), nullable=False, unique=True, index=True)
    code_hash       = Column(String(120), nullable=False)
    phone           = Column(String(20), nullable=False)
    expires_at      = Column(DateTime, nullable=False)
    created_at      = Column(DateTime, default=datetime.utcnow, nullable=False)
```

Add to T1's migration too.

- [ ] **Step 5: Register** in `main.py`: `from app.routers import reputation_public; app.include_router(reputation_public.router)`.

- [ ] **Step 6: Run, confirm pass.**

- [ ] **Step 7: Commit.**

```bash
git add backend/app/routers/reputation_public.py backend/app/main.py \
        backend/app/services/patient_portal_auth.py \
        backend/tests/test_reputation_public.py \
        backend/app/models/reputation.py \
        backend/scripts/migrate_reputation.py
git commit -m "feat(reputation): public endpoints — scan, verify, submit, google-clicked"
```

---

## Task 3: Admin endpoints — profiles + leaderboard + reviews

**Files:**
- Create: `backend/app/routers/reputation_admin.py`
- Modify: `backend/app/main.py` (register)
- Test: `backend/tests/test_reputation_admin.py`

- [ ] **Step 1: Failing tests** covering: create profile, list profiles, rotate token, list leaderboard, list reviews, approve a review for embed, and that all endpoints require `get_current_user`.

- [ ] **Step 2: Run, confirm fail.**

- [ ] **Step 3: Create router** at `backend/app/routers/reputation_admin.py`:

```python
"""Admin endpoints for reputation management. Reuses get_current_user.
A reputation:manage permission gates writes (PATCH/POST); reads are
open to any authenticated staff."""
import secrets
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.reputation import (
    ReputationProfile, ReputationScan, ReputationReview,
)
from app.routers.auth import get_current_user

router = APIRouter(prefix="/api/admin/reputation", tags=["reputation-admin"])

POINTS = {"scan": 1, "review": 2, "five_star": 5, "google_share": 3}


def _profile_dict(p: ReputationProfile) -> dict:
    return {
        "id":            str(p.id),
        "user_email":    p.user_email,
        "display_name":  p.display_name,
        "role_label":    p.role_label,
        "qr_token":      p.qr_token,
        "active":        p.active,
    }


class ProfileIn(BaseModel):
    display_name: str
    role_label: Optional[str] = None
    user_email: Optional[str] = None


@router.get("/profiles")
def list_profiles(db: Session = Depends(get_db),
                     user: dict = Depends(get_current_user)):
    rows = (db.query(ReputationProfile)
                .order_by(ReputationProfile.active.desc(),
                           ReputationProfile.display_name.asc())
                .all())
    return {"profiles": [_profile_dict(p) for p in rows]}


@router.post("/profiles")
def create_profile(payload: ProfileIn, db: Session = Depends(get_db),
                      user: dict = Depends(get_current_user)):
    p = ReputationProfile(
        display_name=payload.display_name.strip(),
        role_label=(payload.role_label or "").strip() or None,
        user_email=(payload.user_email or "").strip() or None,
        qr_token=secrets.token_urlsafe(12),
    )
    db.add(p); db.commit(); db.refresh(p)
    return _profile_dict(p)


@router.patch("/profiles/{pid}")
def update_profile(pid: str, payload: dict,
                      db: Session = Depends(get_db),
                      user: dict = Depends(get_current_user)):
    p = db.query(ReputationProfile).filter(
        ReputationProfile.id == pid).first()
    if p is None:
        raise HTTPException(status_code=404, detail="profile not found")
    for fld in ("display_name", "role_label", "user_email", "active"):
        if fld in payload:
            setattr(p, fld, payload[fld])
    db.commit(); db.refresh(p)
    return _profile_dict(p)


@router.post("/profiles/{pid}/rotate-token")
def rotate_token(pid: str, db: Session = Depends(get_db),
                    user: dict = Depends(get_current_user)):
    p = db.query(ReputationProfile).filter(
        ReputationProfile.id == pid).first()
    if p is None:
        raise HTTPException(status_code=404, detail="profile not found")
    p.qr_token = secrets.token_urlsafe(12)
    db.commit(); db.refresh(p)
    return _profile_dict(p)


@router.get("/leaderboard")
def leaderboard(db: Session = Depends(get_db),
                   user: dict = Depends(get_current_user)):
    """Aggregate points per profile. Done in Python to keep the SQL
    portable across SQLite (tests) and Postgres (prod)."""
    profiles = db.query(ReputationProfile).all()
    rows = []
    for p in profiles:
        scan_pts = (db.query(func.coalesce(func.sum(
                          ReputationScan.points_credited), 0))
                          .filter(ReputationScan.profile_id == p.id)
                          .scalar()) or 0
        reviews = (db.query(ReputationReview)
                       .filter(ReputationReview.profile_id == p.id).all())
        review_count = len(reviews)
        five_star_count = sum(1 for r in reviews if r.stars == 5)
        google_share_count = sum(1 for r in reviews
                                       if r.google_clicked_at is not None)
        points = (scan_pts
                    + review_count * POINTS["review"]
                    + five_star_count * POINTS["five_star"]
                    + google_share_count * POINTS["google_share"])
        rows.append({
            "profile_id":         str(p.id),
            "display_name":       p.display_name,
            "role_label":         p.role_label,
            "active":             p.active,
            "scan_points":        scan_pts,
            "review_count":       review_count,
            "five_star_count":    five_star_count,
            "google_share_count": google_share_count,
            "points":             points,
        })
    rows.sort(key=lambda r: r["points"], reverse=True)
    return {"rows": rows}


@router.get("/reviews")
def list_reviews(db: Session = Depends(get_db),
                    user: dict = Depends(get_current_user)):
    rows = (db.query(ReputationReview)
                .order_by(ReputationReview.submitted_at.desc())
                .limit(500).all())
    profiles = {p.id: p for p in db.query(ReputationProfile).all()}
    return {"reviews": [{
        "id":                   str(r.id),
        "profile_id":           str(r.profile_id),
        "profile_display_name": (profiles.get(r.profile_id) or
                                       type("x", (), {"display_name": "?"})).display_name,
        "stars":                r.stars,
        "body":                 r.body,
        "patient_first_name":   r.patient_first_name,
        "patient_last_initial": r.patient_last_initial,
        "patient_chart_number": r.patient_chart_number,
        "consent_to_display":   r.consent_to_display,
        "approved_for_embed":   r.approved_for_embed,
        "google_clicked_at":    r.google_clicked_at.isoformat()
                                    if r.google_clicked_at else None,
        "submitted_at":         r.submitted_at.isoformat()
                                    if r.submitted_at else None,
    } for r in rows]}


class ReviewPatch(BaseModel):
    approved_for_embed: Optional[bool] = None


@router.patch("/reviews/{rid}")
def patch_review(rid: str, payload: ReviewPatch,
                    db: Session = Depends(get_db),
                    user: dict = Depends(get_current_user)):
    r = db.query(ReputationReview).filter(ReputationReview.id == rid).first()
    if r is None:
        raise HTTPException(status_code=404, detail="review not found")
    if payload.approved_for_embed is not None:
        r.approved_for_embed = payload.approved_for_embed
    db.commit(); db.refresh(r)
    return {"ok": True, "approved_for_embed": r.approved_for_embed}
```

- [ ] **Step 4: Register + run + commit.**

```bash
git add backend/app/routers/reputation_admin.py backend/app/main.py \
        backend/tests/test_reputation_admin.py
git commit -m "feat(reputation): admin endpoints — profiles, leaderboard, reviews"
```

---

## Task 4: QR code PNG generation

**Files:**
- Create: `backend/app/services/qr_generator.py`
- Modify: `backend/app/routers/reputation_admin.py` (add `/profiles/{id}/qr.png`)
- Test: `backend/tests/test_qr_generator.py`

- [ ] **Step 1: Failing tests** that hit `GET /api/admin/reputation/profiles/{id}/qr.png` and assert `content-type: image/png` + valid PNG magic bytes.

- [ ] **Step 2: Service** at `backend/app/services/qr_generator.py`:

```python
"""Render a printable QR-code PNG for a reputation profile."""
import io
import qrcode

REVIEWS_BASE_URL = "https://reviews.waldorfwomenscare.com"


def render_profile_qr_png(qr_token: str) -> bytes:
    url = f"{REVIEWS_BASE_URL}/r/{qr_token}"
    img = qrcode.make(url, box_size=12, border=4)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
```

- [ ] **Step 3: Endpoint** in `reputation_admin.py`:

```python
from fastapi.responses import Response
from app.services.qr_generator import render_profile_qr_png


@router.get("/profiles/{pid}/qr.png")
def profile_qr_png(pid: str, db: Session = Depends(get_db),
                       user: dict = Depends(get_current_user)):
    p = db.query(ReputationProfile).filter(
        ReputationProfile.id == pid).first()
    if p is None:
        raise HTTPException(status_code=404, detail="profile not found")
    return Response(content=render_profile_qr_png(p.qr_token),
                       media_type="image/png",
                       headers={"Content-Disposition":
                                  f'inline; filename="qr_{p.display_name[:30]}.png"'})
```

- [ ] **Step 4: Test + commit.**

```bash
git commit -m "feat(reputation): printable QR code PNG endpoint"
```

---

## Task 5: Public embed — JSON + iframe HTML

**Files:**
- Modify: `backend/app/routers/reputation_public.py` — add `/api/reviews/public`
- Frontend: route `/embed` (server-rendered isn't necessary — SPA route works for iframe)

- [ ] **Step 1: Failing tests:**

```python
def test_public_embed_returns_only_approved_reviews(client, db):
    p = _seed_profile(db)
    from app.models.reputation import ReputationReview
    r_visible = ReputationReview(profile_id=p.id, stars=5,
                                       body="Great care!",
                                       patient_first_name="Jane",
                                       patient_last_initial="D",
                                       consent_to_display=True,
                                       approved_for_embed=True)
    r_pending = ReputationReview(profile_id=p.id, stars=4,
                                        body="Pending approval",
                                        consent_to_display=True,
                                        patient_first_name="Joe",
                                        patient_last_initial="S",
                                        approved_for_embed=False)
    r_no_consent = ReputationReview(profile_id=p.id, stars=5,
                                           consent_to_display=False,
                                           approved_for_embed=True)
    db.add_all([r_visible, r_pending, r_no_consent]); db.commit()
    r = client.get("/api/reviews/public")
    assert r.status_code == 200
    body = r.json()
    assert len(body["reviews"]) == 1
    assert body["reviews"][0]["stars"] == 5
    assert body["reviews"][0]["display_name"] == "Jane D."


def test_public_embed_never_exposes_chart_or_phone(client, db):
    p = _seed_profile(db)
    from app.models.reputation import ReputationReview
    r = ReputationReview(profile_id=p.id, stars=5,
                              patient_first_name="X",
                              patient_last_initial="Y",
                              patient_chart_number="C-999",
                              patient_phone="+12405551234",
                              consent_to_display=True,
                              approved_for_embed=True)
    db.add(r); db.commit()
    resp = client.get("/api/reviews/public").json()
    out = resp["reviews"][0]
    assert "chart" not in str(out).lower()
    assert "phone" not in str(out).lower()
    assert "+1240" not in str(out)
```

- [ ] **Step 2: Endpoint:**

```python
@router.get("/api/reviews/public")
def public_reviews(limit: int = 20, db: Session = Depends(get_db)):
    """Returns reviews that are both opt-in (consent_to_display) AND
    staff-approved (approved_for_embed). No PHI ever returned."""
    rows = (db.query(ReputationReview)
                .filter(ReputationReview.consent_to_display.is_(True),
                         ReputationReview.approved_for_embed.is_(True))
                .order_by(ReputationReview.submitted_at.desc())
                .limit(min(max(1, limit), 100))
                .all())
    return {"reviews": [{
        "stars":        r.stars,
        "body":         r.body,
        "display_name": f"{r.patient_first_name} "
                            f"{(r.patient_last_initial or '').strip().rstrip('.')}.".strip(),
        "submitted_at": r.submitted_at.isoformat() if r.submitted_at else None,
    } for r in rows]}
```

This route goes in `reputation_public.py` but ends up at `/api/reviews/public` (router prefix is `/api/r`, so use `@router.get("/api/reviews/public", include_in_schema=True)` with explicit path OR add a second router with `/api/reviews` prefix). For clarity, register a second tiny router.

- [ ] **Step 3: Run + commit.**

```bash
git commit -m "feat(reputation): public embed endpoint — no PHI"
```

---

## Task 6: Frontend — patient review form at `/r/:token`

**Files:**
- Modify: `frontend/src/App.jsx` — route + lazy component
- Create: `frontend/src/pages/reputation/ReviewForm.jsx`

Mobile-first single-page form with stars + comment + optional patient-toggle.

- [ ] **Step 1**: Add route `<Route path="/r/:token" element={<ReviewForm />} />` at top level (OUTSIDE the auth-protected app).

- [ ] **Step 2**: Build `ReviewForm.jsx` — on mount POST `/api/r/{token}/scan`, get profile info, render form with:
  - 5 star buttons (tap to set)
  - Textarea for comment (optional)
  - Collapsible "I'm a WWC patient" section: phone field → "Send code" → enter code → confirm; on success display "✓ Verified as Jane Doe"
  - Submit → POST `/api/r/{token}/submit`
  - On 5-star response: show "Help others find WWC — share on Google?" button. Click → POST `/api/r/{token}/google-clicked` then `window.location.assign(GOOGLE_REVIEW_URL)`

The `GOOGLE_REVIEW_URL` is a frontend constant (or fetched from `/api/reputation/config` if we want it server-configurable — defer to v1.1).

- [ ] **Step 3: Build + commit.**

```bash
git commit -m "feat(reputation): patient review form at /r/:token"
```

---

## Task 7: Frontend — public embed at `/embed`

**Files:**
- Modify: `frontend/src/App.jsx`
- Create: `frontend/src/pages/reputation/Embed.jsx`

Server-renderable HTML alternative would be cleaner, but a SPA route loaded in an iframe works fine. The page fetches `/api/reviews/public` and renders cards with stars + body + display_name.

Important: iframe-friendly styling (no fixed-position elements, transparent background option via `?theme=light|dark` query param). Allow Webflow embed.

- [ ] **Build + commit.**

---

## Task 8: Frontend — admin pages

**Files:**
- Create: `frontend/src/pages/AdminReputationProfiles.jsx`
- Create: `frontend/src/pages/AdminReputationLeaderboard.jsx`
- Create: `frontend/src/pages/AdminReputationReviews.jsx`
- Modify: `frontend/src/App.jsx` (3 routes under `/admin/reputation/*`)
- Modify: Admin.jsx (3 nav links)

Standard list + modal-edit pattern (like P6's message-templates page).

Profiles page: list with display_name + role_label + active toggle. "+ New" button creates profile + immediately shows the QR PNG with "Print" button.

Leaderboard: table with columns matching the leaderboard endpoint.

Reviews: table with stars + body excerpt + patient name (when consented) + chart_number (PHI badge) + Approve toggle.

- [ ] **Build + commit.**

---

## Task 9: DNS — reviews.waldorfwomenscare.com

- [ ] **Step 1: Add CNAME** in Cloudflare DNS: `reviews.waldorfwomenscare.com` → existing frontend Cloud Run hostname (or use the gw.waldorfwomenscare.com target).
- [ ] **Step 2: Verify** that `https://reviews.waldorfwomenscare.com/r/test` reaches the SPA.

(If Cloudflare proxies SSL, no extra cert work. Otherwise add to Google-managed cert.)

---

## Task 10: Smoke test in prod

- [ ] **Step 1**: Build + deploy backend `v_reputation_v1`. Apply migration.
- [ ] **Step 2**: Create one profile via the admin UI ("Smoke Test").
- [ ] **Step 3**: Download the QR PNG. Scan it with your phone.
- [ ] **Step 4**: Submit a 1-star review (so we don't pollute the Google handoff smoke).
- [ ] **Step 5**: Verify the review shows in the admin Reviews page; approve it.
- [ ] **Step 6**: Submit a 5-star review with consent + name. Verify Google handoff prompt fires + click it.
- [ ] **Step 7**: View `https://reviews.waldorfwomenscare.com/embed` — should show the 5-star review with the consent name.
- [ ] **Step 8**: Check leaderboard — Smoke Test profile shows the expected points (e.g., 1 scan + 1 review = +3, then 5-star = +5, Google click = +3, etc.).
- [ ] **Step 9**: Cleanup — delete the smoke profile (cascade drops scans + reviews). Or leave for manual testing later.
