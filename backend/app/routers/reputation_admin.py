"""Admin endpoints for reputation management. Reuses get_current_user."""
import secrets
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
        "id":           str(p.id),
        "user_email":   p.user_email,
        "display_name": p.display_name,
        "role_label":   p.role_label,
        "qr_token":     p.qr_token,
        "active":       p.active,
        "created_at":   p.created_at.isoformat() if p.created_at else None,
    }


class ProfileIn(BaseModel):
    display_name: str
    role_label: Optional[str] = None
    user_email: Optional[str] = None


class ProfilePatch(BaseModel):
    display_name: Optional[str] = None
    role_label: Optional[str] = None
    user_email: Optional[str] = None
    active: Optional[bool] = None


@router.get("/profiles")
def list_profiles(db: Session = Depends(get_db),
                     user: dict = Depends(get_current_user)):
    rows = (db.query(ReputationProfile)
                .order_by(ReputationProfile.active.desc(),
                           ReputationProfile.display_name.asc())
                .all())
    return {"profiles": [_profile_dict(p) for p in rows]}


@router.post("/profiles")
def create_profile(payload: ProfileIn,
                      db: Session = Depends(get_db),
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
def update_profile(pid: str, payload: ProfilePatch,
                      db: Session = Depends(get_db),
                      user: dict = Depends(get_current_user)):
    p = (db.query(ReputationProfile)
              .filter(ReputationProfile.id == pid).first())
    if p is None:
        raise HTTPException(status_code=404, detail="profile not found")
    if payload.display_name is not None:
        p.display_name = payload.display_name.strip()
    if payload.role_label is not None:
        p.role_label = payload.role_label.strip() or None
    if payload.user_email is not None:
        p.user_email = payload.user_email.strip() or None
    if payload.active is not None:
        p.active = payload.active
    db.commit(); db.refresh(p)
    return _profile_dict(p)


@router.post("/profiles/{pid}/rotate-token")
def rotate_token(pid: str, db: Session = Depends(get_db),
                    user: dict = Depends(get_current_user)):
    p = (db.query(ReputationProfile)
              .filter(ReputationProfile.id == pid).first())
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
        scan_pts = db.query(func.coalesce(
            func.sum(ReputationScan.points_credited), 0)
        ).filter(ReputationScan.profile_id == p.id).scalar() or 0
        reviews = (db.query(ReputationReview)
                       .filter(ReputationReview.profile_id == p.id).all())
        review_count = len(reviews)
        five_star_count = sum(1 for r in reviews if r.stars == 5)
        google_share_count = sum(
            1 for r in reviews if r.google_clicked_at is not None)
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
    rows.sort(key=lambda r: (-r["points"], r["display_name"]))
    return {"rows": rows}


@router.get("/reviews")
def list_reviews(db: Session = Depends(get_db),
                    user: dict = Depends(get_current_user)):
    rows = (db.query(ReputationReview)
                .order_by(ReputationReview.submitted_at.desc())
                .limit(500).all())
    profiles = {p.id: p for p in db.query(ReputationProfile).all()}
    out = []
    for r in rows:
        prof = profiles.get(r.profile_id)
        out.append({
            "id":                   str(r.id),
            "profile_id":           str(r.profile_id),
            "profile_display_name": prof.display_name if prof else "(unknown)",
            "stars":                r.stars,
            "body":                 r.body,
            "patient_first_name":   r.patient_first_name,
            "patient_last_initial": r.patient_last_initial,
            "patient_chart_number": r.patient_chart_number,
            "patient_phone":        r.patient_phone,
            "consent_to_display":   r.consent_to_display,
            "approved_for_embed":   r.approved_for_embed,
            "google_clicked_at":    r.google_clicked_at.isoformat()
                                        if r.google_clicked_at else None,
            "submitted_at":         r.submitted_at.isoformat()
                                        if r.submitted_at else None,
        })
    return {"reviews": out}


class ReviewPatch(BaseModel):
    approved_for_embed: Optional[bool] = None


@router.patch("/reviews/{rid}")
def patch_review(rid: str, payload: ReviewPatch,
                    db: Session = Depends(get_db),
                    user: dict = Depends(get_current_user)):
    r = (db.query(ReputationReview)
              .filter(ReputationReview.id == rid).first())
    if r is None:
        raise HTTPException(status_code=404, detail="review not found")
    if payload.approved_for_embed is not None:
        r.approved_for_embed = payload.approved_for_embed
    db.commit(); db.refresh(r)
    return {"ok": True, "approved_for_embed": r.approved_for_embed}
