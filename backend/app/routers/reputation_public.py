"""Public (no-auth) review endpoints for patients who scanned a QR."""
import secrets
from datetime import datetime, timedelta
from app.utils.dt import now_utc_naive
from typing import Optional

import bcrypt as _bcrypt
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.reputation import (
    ReputationProfile, ReputationScan, ReputationReview,
    ReputationPhoneChallenge,
)
from app.models.surgery import Surgery
from app.services.checklist_notifications import send_sms

router = APIRouter(prefix="/api/r", tags=["reputation-public"])

POINTS = {"scan": 1, "review": 2, "five_star": 5, "google_share": 3}
SCAN_DEDUP_HOURS = 24
CHALLENGE_TTL_MINUTES = 5


def _generate_code() -> str:
    """6-digit numeric code, leading zeros preserved.
    Module-level so tests can patch it."""
    return f"{secrets.randbelow(10**6):06d}"


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return (request.client.host if request.client else "") or ""


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
    cutoff = now_utc_naive() - timedelta(hours=SCAN_DEDUP_HOURS)
    prior = (db.query(ReputationScan)
                 .filter(ReputationScan.profile_id == p.id,
                          ReputationScan.ip_address == ip,
                          ReputationScan.scanned_at >= cutoff)
                 .first()) if ip else None
    s = ReputationScan(
        profile_id=p.id,
        ip_address=ip or None,
        user_agent=request.headers.get("User-Agent", "")[:300] or None,
        points_credited=0 if prior else POINTS["scan"],
    )
    db.add(s); db.commit(); db.refresh(s)
    from app.services.google_review_urls import google_review_url_for
    return {
        "scan_id":          str(s.id),
        "display_name":     p.display_name,
        "role_label":       p.role_label,
        "location":         p.location,
        "google_review_url": google_review_url_for(p.location),
    }


class VerifyStart(BaseModel):
    phone: str


@router.post("/{token}/verify-patient/start")
def verify_start(token: str, payload: VerifyStart,
                    db: Session = Depends(get_db)):
    _profile(db, token)
    phone = (payload.phone or "").strip()
    if not phone:
        raise HTTPException(status_code=422, detail="phone required")

    code = _generate_code()
    challenge_token = secrets.token_urlsafe(32)
    c = ReputationPhoneChallenge(
        challenge_token=challenge_token,
        code_hash=_bcrypt.hashpw(code.encode(), _bcrypt.gensalt()).decode(),
        phone=phone,
        expires_at=now_utc_naive() + timedelta(minutes=CHALLENGE_TTL_MINUTES),
    )
    db.add(c); db.commit()
    send_sms(phone,
                f"WWC: Code to confirm you're a patient for your review: "
                f"{code}. Expires in {CHALLENGE_TTL_MINUTES} minutes.")
    return {"challenge_token": challenge_token}


class VerifyCheck(BaseModel):
    challenge_token: str
    code: str


@router.post("/{token}/verify-patient/check")
def verify_check(token: str, payload: VerifyCheck,
                    db: Session = Depends(get_db)):
    _profile(db, token)
    c = (db.query(ReputationPhoneChallenge)
             .filter(ReputationPhoneChallenge.challenge_token
                       == payload.challenge_token)
             .first())
    if not c or c.expires_at < now_utc_naive():
        raise HTTPException(status_code=401, detail="invalid or expired")
    code_digits = "".join(ch for ch in (payload.code or "") if ch.isdigit())
    if (len(code_digits) != 6 or not _bcrypt.checkpw(
            code_digits.encode(), c.code_hash.encode())):
        raise HTTPException(status_code=401, detail="invalid code")
    s = (db.query(Surgery)
              .filter(Surgery.cell_phone == c.phone)
              .order_by(Surgery.created_at.desc())
              .first())
    chart = s.chart_number if s else None
    phone = c.phone   # cache before delete
    db.delete(c); db.commit()
    return {"chart_number": chart, "phone": phone}


class ReviewSubmit(BaseModel):
    stars: int
    body: Optional[str] = None
    patient_first_name: Optional[str] = None
    patient_last_initial: Optional[str] = None
    patient_chart_number: Optional[str] = None
    patient_phone: Optional[str] = None
    consent_to_display: bool = False


@router.post("/{token}/submit")
def submit(token: str, payload: ReviewSubmit,
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
        patient_chart_number=(payload.patient_chart_number or "").strip() or None,
        patient_phone=(payload.patient_phone or "").strip() or None,
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
    r = (db.query(ReputationReview)
              .filter(ReputationReview.id == payload.review_id).first())
    if r is None:
        raise HTTPException(status_code=404, detail="review not found")
    r.google_clicked_at = now_utc_naive()
    db.commit()
    return {"ok": True}


# ─── Public embed (for Webflow) — no PHI ───────────────────────────

embed_router = APIRouter(prefix="/api/reviews", tags=["reputation-embed"])


@embed_router.get("/public")
def public_reviews(limit: int = 20, db: Session = Depends(get_db)):
    """Reviews approved for the public embed. Strictly NO PHI:
    no chart_number, no phone, no last name (only an initial). Only
    `stars`, `body`, a display_name like "Jane D.", and submitted_at.
    """
    limit = min(max(1, limit), 100)
    rows = (db.query(ReputationReview)
                .filter(ReputationReview.consent_to_display.is_(True),
                         ReputationReview.approved_for_embed.is_(True))
                .order_by(ReputationReview.submitted_at.desc())
                .limit(limit)
                .all())
    out = []
    for r in rows:
        last_initial = (r.patient_last_initial or "").strip().rstrip(".")
        first = (r.patient_first_name or "").strip()
        if first and last_initial:
            display = f"{first} {last_initial}."
        elif first:
            display = first
        else:
            display = "Anonymous"
        out.append({
            "stars":        r.stars,
            "body":         r.body,
            "display_name": display,
            "submitted_at": r.submitted_at.isoformat() if r.submitted_at else None,
        })
    return {"reviews": out}
