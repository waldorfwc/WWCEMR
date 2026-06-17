"""Patient-facing pellet portal API (no staff auth; uses a pellet-portal JWT).
Phase 1: login/verify; dashboard + requirements added in T4. Mirrors
patient_portal.py."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Header, HTTPException
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
