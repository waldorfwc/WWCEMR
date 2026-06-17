"""Patient-facing pellet portal API (no staff auth; uses a pellet-portal JWT).
Phase 1: login/verify; dashboard + requirements added in T4. Mirrors
patient_portal.py."""
from __future__ import annotations

import json
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.pellet import PelletPatient
from app.models.pellet_payment import PelletSubscription
from app.models.pellet_portal import PelletConsent, PelletPortalUpload
from app.services.pellet import portal_auth
from app.services.pellet import payments as pelletpay
from app.services.pellet.activity import record_pellet_activity
from app.services.pellet.settings import cfg
from app.services.storage import save_blob
from app.utils.dt import now_utc_naive

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
    last4 = "".join(c for c in (payload.last4 or "") if c.isdigit())[-4:]
    if len(last4) != 4:
        raise HTTPException(status_code=422,
                            detail="Enter the last 4 digits of your phone")
    p = portal_auth.match_patient(db, dob, last4)
    if p is None:
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
    }


@router.post("/mammo")
def upload_mammo(file: UploadFile = File(...),
                 p: PelletPatient = Depends(require_pellet_token),
                 db: Session = Depends(get_db)):
    raw = file.file.read()
    if not raw:
        raise HTTPException(status_code=422, detail="empty file")
    path = save_blob(prefix="pellet-mammo", body=raw, filename=file.filename or "mammo")
    db.add(PelletPortalUpload(pellet_patient_id=p.id, kind="mammo",
                              filename=file.filename, storage_path=path,
                              content_type=file.content_type))
    p.mammo_submitted_at = now_utc_naive()
    record_pellet_activity(db, p, "mammo_uploaded", "Patient uploaded a mammogram")
    db.commit()
    return {"ok": True, "status": "pending_verification"}


@router.post("/consent")
def request_consent(p: PelletPatient = Depends(require_pellet_token),
                    db: Session = Depends(get_db)):
    import app.services.boldsign_envelopes as be
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
    p.labs_self_reported_at = now_utc_naive()
    record_pellet_activity(db, p, "labs_self_reported",
                           "Patient self-reported labs complete",
                           detail=json.dumps({"drawn_date": drawn.isoformat() if drawn else None}))
    db.commit()
    return {"ok": True, "status": "pending_verification"}


@router.get("/payment/options")
def payment_options(p: PelletPatient = Depends(require_pellet_token),
                    db: Session = Depends(get_db)):
    return {
        "insertion_price": float(pelletpay.insertion_price(db)),
        "package_tiers": cfg(db, "package_discount_tiers") or [],
        "subscription_monthly_amount": cfg(db, "subscription_monthly_amount"),
        "enable_single": bool(cfg(db, "enable_single")),
        "enable_package": bool(cfg(db, "enable_package")),
        "enable_subscription": bool(cfg(db, "enable_subscription")),
        "available_insertions": pelletpay.available_insertions(db, p),
    }


@router.post("/payment/single")
def pay_single(p: PelletPatient = Depends(require_pellet_token),
               db: Session = Depends(get_db)):
    if not cfg(db, "enable_single"):
        raise HTTPException(status_code=409, detail="single payment disabled")
    if not pelletpay.is_configured():
        raise HTTPException(status_code=503, detail="payments not configured")
    row = pelletpay.create_insertion_checkout(db, p, kind="single", count=1,
                                              amount=pelletpay.insertion_price(db),
                                              actor="patient")
    return {"checkout_url": row.checkout_url}


class PackageIn(BaseModel):
    count: int


@router.post("/payment/package")
def pay_package(payload: PackageIn, p: PelletPatient = Depends(require_pellet_token),
                db: Session = Depends(get_db)):
    if not cfg(db, "enable_package"):
        raise HTTPException(status_code=409, detail="package payment disabled")
    if payload.count < 2:
        raise HTTPException(status_code=422, detail="package count must be >= 2")
    if not pelletpay.is_configured():
        raise HTTPException(status_code=503, detail="payments not configured")
    amount = pelletpay.package_price(db, payload.count)
    row = pelletpay.create_insertion_checkout(db, p, kind="package",
                                              count=payload.count, amount=amount,
                                              actor="patient")
    return {"checkout_url": row.checkout_url}


@router.post("/payment/subscribe")
def subscribe(p: PelletPatient = Depends(require_pellet_token),
              db: Session = Depends(get_db)):
    if not cfg(db, "enable_subscription"):
        raise HTTPException(status_code=409, detail="subscription disabled")
    monthly = cfg(db, "subscription_monthly_amount")
    if not monthly:
        raise HTTPException(status_code=409, detail="subscription not configured")
    if not pelletpay.is_configured():
        raise HTTPException(status_code=503, detail="payments not configured")
    existing = (db.query(PelletSubscription)
                  .filter(PelletSubscription.pellet_patient_id == p.id,
                          PelletSubscription.status == "active").first())
    if existing:
        raise HTTPException(status_code=409, detail="already subscribed")
    from decimal import Decimal as _D
    row = pelletpay.create_subscription(db, p, monthly_amount=_D(str(monthly)))
    return {"ok": True, "subscription_id": row.stripe_subscription_id, "status": row.status}
