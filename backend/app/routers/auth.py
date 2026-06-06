"""
Google OAuth2 authentication.
Only allows @waldorfwomenscare.com and @caribcall.com emails.
"""

from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.responses import JSONResponse
from jose import jwt, JWTError
import httpx

from app.config import settings
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.user import User, UserGroup
from app.services.audit_service import log_action

router = APIRouter(prefix="/auth", tags=["auth"])

ALLOWED_DOMAINS = [d.strip() for d in settings.allowed_domains.split(",")]


def create_access_token(data: dict, expires_hours: int = 8) -> str:
    to_encode = data.copy()
    to_encode["exp"] = datetime.utcnow() + timedelta(hours=expires_hours)
    return jwt.encode(to_encode, settings.secret_key, algorithm=settings.algorithm)


def verify_token(token: str) -> Optional[dict]:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        email = payload.get("email")
        if not email:
            return None
        domain = email.split("@")[-1]
        if domain not in ALLOWED_DOMAINS:
            return None
        return payload
    except JWTError:
        return None


def get_current_user(request: Request, db: Session = Depends(get_db)) -> dict:
    token = request.cookies.get("session_token")
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = verify_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    email = (payload.get("email") or "").lower().strip()
    if not email:
        raise HTTPException(status_code=401, detail="Token missing email")

    user_row = db.query(User).filter(User.email == email).first()
    if user_row is None:
        user_row = User(
            email=email,
            group=UserGroup.CLINICAL,
            display_name=payload.get("name"),
        )
        db.add(user_row)
        db.commit()
        db.refresh(user_row)
        log_action(db, "USER_CREATED", "user",
                   resource_id=email,
                   user_name=email,
                   description=f"Auto-created with default group clinical")
        # Auto-join Default Staff so new hires get the baseline tiers
        # (Chart View + My Checklist Work) without admin intervention.
        from app.services.default_staff_group import (
            auto_join_default_staff, ensure_default_staff_group,
        )
        ensure_default_staff_group(db)
        auto_join_default_staff(db, email)

    # Active-user gate (Phase 7) — refuse access for suspended accounts
    if not user_row.is_active:
        raise HTTPException(status_code=403,
                            detail="Account is suspended. Contact your administrator.")

    return {
        "email": email,
        "name": payload.get("name") or user_row.display_name,
        "picture": payload.get("picture"),
        "group": user_row.group.value if hasattr(user_row.group, "value") else user_row.group,
    }


@router.post("/google")
async def google_login(payload: dict):
    """
    Exchange Google OAuth authorization code for user info and create session.
    Body: { code: "auth_code_from_google" }
    """
    code = payload.get("code")
    redirect_uri = payload.get("redirect_uri", "http://localhost:3000/auth/callback")

    if not code:
        raise HTTPException(status_code=400, detail="Authorization code required")

    # Exchange code for tokens with Google
    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )

    if token_resp.status_code != 200:
        raise HTTPException(status_code=400, detail=f"Google token exchange failed: {token_resp.text[:200]}")

    token_data = token_resp.json()
    id_token = token_data.get("id_token")

    if not id_token:
        raise HTTPException(status_code=400, detail="No ID token received")

    # Verify and decode the Google ID token
    async with httpx.AsyncClient() as client:
        userinfo_resp = await client.get(
            "https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": f"Bearer {token_data['access_token']}"},
        )

    if userinfo_resp.status_code != 200:
        raise HTTPException(status_code=400, detail="Failed to get user info")

    userinfo = userinfo_resp.json()
    email = userinfo.get("email", "")
    domain = email.split("@")[-1] if email else ""

    if domain not in ALLOWED_DOMAINS:
        raise HTTPException(
            status_code=403,
            detail=f"Access denied. Only {', '.join(ALLOWED_DOMAINS)} emails are allowed."
        )

    # Active-user gate — block suspended accounts at login (Phase 7).
    # We only block if the user already exists; first-time logins still
    # auto-create via get_current_user with is_active=True default.
    from app.database import SessionLocal
    _db = SessionLocal()
    try:
        existing = _db.query(User).filter(User.email == email).first()
        if existing is not None and not existing.is_active:
            raise HTTPException(
                status_code=403,
                detail="Your account is suspended. Contact your administrator.",
            )
    finally:
        _db.close()

    # Create session token
    session_token = create_access_token({
        "email": email,
        "name": userinfo.get("name", ""),
        "picture": userinfo.get("picture", ""),
    })

    response = JSONResponse({
        "email": email,
        "name": userinfo.get("name", ""),
        "picture": userinfo.get("picture", ""),
        "token": session_token,
    })
    response.set_cookie(
        key="session_token",
        value=session_token,
        httponly=True,
        samesite="lax",
        max_age=8 * 3600,
    )
    return response


@router.get("/me")
def get_me(user: dict = Depends(get_current_user),
           db: Session = Depends(get_db)):
    """Return current authenticated user with derived RBAC flags.

    The legacy `is_admin` / `is_billing` / `is_clinical` booleans are
    still returned for frontend nav gating, but now computed from the
    per-module tier model:
      - is_admin    = caller is a Super Admin
      - is_billing  = caller has Active AR:View or higher
      - is_clinical = caller has Chart:View AND no billing/admin reach
    """
    from app.permissions.catalog import Module, Tier
    from app.permissions.resolver import effective_tier

    email = (user.get("email") or "").lower().strip()
    user_row = db.query(User).filter(User.email == email).first()
    is_super_admin = bool(user_row and user_row.is_super_admin)
    active_ar_tier = effective_tier(db, email, Module.ACTIVE_AR)
    chart_tier     = effective_tier(db, email, Module.CHART)

    has_billing = active_ar_tier >= Tier.VIEW
    has_chart   = chart_tier >= Tier.VIEW

    # Compute the user's tier on every module for the frontend nav.
    module_tiers = {
        m.value: int(effective_tier(db, email, m)) for m in Module
    }

    return {
        "email": user.get("email"),
        "name": user.get("name"),
        "picture": user.get("picture"),
        # Legacy nav flags, derived from tiers:
        "is_admin":    is_super_admin,
        "is_billing":  has_billing,
        "is_clinical": has_chart and not has_billing and not is_super_admin,
        # New: per-module effective tier (ints from the Tier IntEnum).
        "module_tiers":  module_tiers,
        "is_super_admin": is_super_admin,
        # Legacy "group" enum — kept until every caller migrates.
        "group": user.get("group"),
    }


@router.post("/logout")
def logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie("session_token")
    return response


@router.get("/config")
def auth_config():
    """Return OAuth config for the frontend (no secrets)."""
    return {
        "client_id": settings.google_client_id,
        "allowed_domains": ALLOWED_DOMAINS,
    }


@router.get("/me/profile")
def my_profile(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Authenticated user's profile + group memberships + effective tiers.

    Visible to any logged-in user — this is the "what can I do?" page.
    """
    from app.permissions.catalog import MODULE_REGISTRY, Module, Tier
    from app.permissions.resolver import effective_tier_with_source

    email = (current_user.get("email") or "").lower().strip()
    user_row = db.query(User).filter(User.email == email).first()
    if user_row is None:
        raise HTTPException(status_code=404, detail="user not in directory")

    groups = [
        {"id": g.id, "name": g.name, "description": g.description}
        for g in user_row.groups
    ]
    tiers = []
    for m in Module:
        result = effective_tier_with_source(db, email, m)
        tiers.append({
            "module": m.value,
            "label": MODULE_REGISTRY[m].label,
            "tier": result.tier.name.lower(),
            "source_kind": result.source_kind,
            "source_label": result.source_label,
        })
    return {
        "email": user_row.email,
        "display_name": user_row.display_name,
        "legacy_group": user_row.group.value if hasattr(user_row.group, "value") else user_row.group,
        "is_super_admin": bool(user_row.is_super_admin),
        "groups": groups,
        "tiers": tiers,
    }


# require_permission was removed in Phase 4. Use:
#   from app.permissions.dependencies import requires_tier
#   Depends(requires_tier(Module.X, Tier.Y))
