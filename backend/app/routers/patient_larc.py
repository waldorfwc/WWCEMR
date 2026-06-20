"""Patient-facing LARC device portal API (no staff auth; uses a larc-portal
JWT). Cloned from app/routers/patient_pellet.py but keys off a LarcAssignment
and an `lpv` (larc-portal-version) revocation claim. Endpoints: login/verify
(DOB+last4 → SMS code → JWT), dashboard tracker, patient-responsibility
payment via Stripe Checkout, BoldSign enrollment sign-link, and documents."""
from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.larc import LarcAssignment, LarcEnrollmentEnvelope
from app.services.larc import portal_auth
from app.services.larc.patient_track import patient_track
from app.services import stripe_payments
from app.utils.dt import now_utc_naive

router = APIRouter(prefix="/larc-portal", tags=["larc-portal"])


# ─── Auth ───────────────────────────────────────────────────────────

class LoginIn(BaseModel):
    dob: str        # YYYY-MM-DD
    phone_last4: str


@router.post("/login")
def login(payload: LoginIn, db: Session = Depends(get_db)):
    try:
        dob = date.fromisoformat(payload.dob)
    except ValueError:
        raise HTTPException(status_code=422, detail="bad dob")
    last4 = "".join(c for c in (payload.phone_last4 or "") if c.isdigit())[-4:]
    if len(last4) != 4:
        raise HTTPException(status_code=422,
                            detail="Enter the last 4 digits of your phone")
    a = portal_auth.match_assignment(db, dob, last4)
    if a is None:
        # Mirror pellet's no-match behavior: a clear 404 (rate-limiting is
        # handled per-challenge in issue_challenge/verify_code).
        raise HTTPException(status_code=404, detail="No matching record found")
    ct = portal_auth.issue_challenge(db, a, purpose="login")
    return {"challenge_token": ct}


class VerifyIn(BaseModel):
    challenge_token: str
    code: str
    sms_opt_in: Optional[bool] = None


@router.post("/verify")
def verify(payload: VerifyIn, db: Session = Depends(get_db)):
    a = portal_auth.verify_code(db, payload.challenge_token, payload.code.strip())
    if a is None:
        raise HTTPException(status_code=401, detail="Invalid or expired code")
    if payload.sms_opt_in and not a.sms_consent:
        a.sms_consent = True
        a.sms_consented_at = now_utc_naive()
        a.sms_consented_by = "patient:self-service"
        db.commit()
        db.refresh(a)
    return {
        "token": portal_auth.issue_portal_token(a),
        "assignment_id": str(a.id),
        "expires_at": portal_auth.compute_token_exp(a).isoformat(),
    }


def require_larc_portal_token(request: Request, authorization: str = Header(None),
                              db: Session = Depends(get_db)) -> LarcAssignment:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing token")
    claims = portal_auth.decode_portal_token(authorization.split(" ", 1)[1].strip())
    if not claims or claims.get("scope") != "larc_portal":
        raise HTTPException(status_code=401, detail="Invalid token")
    a = (db.query(LarcAssignment)
           .filter(LarcAssignment.id == claims.get("sub")).first())
    if a is None:
        raise HTTPException(status_code=401, detail="Unknown assignment")
    if int(claims.get("lpv", 0)) != int(a.portal_token_version or 0):
        raise HTTPException(status_code=401, detail="Token revoked")
    # Staff preview tokens are read-only — coordinators view, they don't act.
    if (claims.get("viewer") or "").startswith("staff:") and request.method != "GET":
        raise HTTPException(status_code=403, detail="Preview mode is read-only.")
    return a


# ─── Helpers ────────────────────────────────────────────────────────

def _payment_summary(a: LarcAssignment) -> dict:
    return {
        "responsibility": (float(a.patient_responsibility)
                           if a.patient_responsibility is not None else None),
        "paid": a.patient_paid_at is not None,
        "paid_at": a.patient_paid_at.isoformat() if a.patient_paid_at else None,
    }


def _enrollment_envelopes(db: Session, a: LarcAssignment) -> list[LarcEnrollmentEnvelope]:
    return (db.query(LarcEnrollmentEnvelope)
              .filter(LarcEnrollmentEnvelope.assignment_id == a.id)
              .order_by(LarcEnrollmentEnvelope.created_at.desc())
              .all())


def _enrollment_summary(db: Session, a: LarcAssignment) -> dict:
    required = a.source_flow == "pharmacy_order"
    env = _enrollment_envelopes(db, a)
    latest = env[0] if env else None
    return {
        "required": required,
        "status": latest.status if latest else None,
        "envelope_id": str(latest.id) if latest else None,
    }


def _documents(db: Session, a: LarcAssignment) -> list[dict]:
    # Best-effort: list signed enrollment PDFs + nothing else yet (receipts
    # are surfaced through Stripe and don't have local document rows).
    out: list[dict] = []
    for e in _enrollment_envelopes(db, a):
        if e.status in ("signed", "faxed") and e.boldsign_envelope_id:
            out.append({
                "kind": "enrollment_form",
                "label": "Signed Enrollment Form",
                "envelope_id": str(e.id),
                "status": e.status,
                "signed_at": e.signed_at.isoformat() if e.signed_at else None,
            })
    return out


# ─── Endpoints ──────────────────────────────────────────────────────

@router.get("/dashboard")
def dashboard(a: LarcAssignment = Depends(require_larc_portal_token),
              db: Session = Depends(get_db)):
    track = patient_track(a)
    return {
        "patient": {"patient_name": a.patient_name, "chart_number": a.chart_number},
        "track": track["track"],
        "steps": track["steps"],
        "payment": _payment_summary(a),
        "enrollment": _enrollment_summary(db, a),
        "documents": _documents(db, a),
    }


@router.get("/payments")
def payments(a: LarcAssignment = Depends(require_larc_portal_token)):
    return _payment_summary(a)


@router.post("/payments/checkout")
def payments_checkout(a: LarcAssignment = Depends(require_larc_portal_token),
                      db: Session = Depends(get_db)):
    if not a.patient_responsibility or float(a.patient_responsibility) <= 0:
        raise HTTPException(status_code=400, detail="nothing due")
    result = stripe_payments.create_larc_checkout(
        db, a, amount=a.patient_responsibility, actor="patient")
    return {"checkout_url": result["checkout_url"]}


@router.get("/enrollment")
def enrollment(a: LarcAssignment = Depends(require_larc_portal_token),
               db: Session = Depends(get_db)):
    return {"items": [{"id": str(e.id), "status": e.status}
                      for e in _enrollment_envelopes(db, a)]}


@router.get("/enrollment/sign-link/{envelope_id}")
def enrollment_sign_link(envelope_id: str,
                         a: LarcAssignment = Depends(require_larc_portal_token),
                         db: Session = Depends(get_db)):
    env = (db.query(LarcEnrollmentEnvelope)
             .filter(LarcEnrollmentEnvelope.id == envelope_id).first())
    if env is None or str(env.assignment_id) != str(a.id):
        raise HTTPException(status_code=404, detail="envelope not found")
    if not env.boldsign_envelope_id:
        raise HTTPException(status_code=409, detail="envelope not yet sent")
    from app.services.boldsign_envelopes import get_embedded_sign_link
    url = get_embedded_sign_link(env.boldsign_envelope_id, a.patient_email or "")
    return {"sign_url": url}


@router.get("/documents")
def documents(a: LarcAssignment = Depends(require_larc_portal_token),
              db: Session = Depends(get_db)):
    return {"documents": _documents(db, a)}
