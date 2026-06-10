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

ALLOWED_DOMAINS = [d.strip().lower() for d in settings.allowed_domains.split(",")]


def normalize_email(email: Optional[str]) -> str:
    """Single source of truth for email casing — every boundary (token
    issue/verify, OAuth callback, admin path params, override storage,
    domain check) must run through this helper. The previous code used
    `.lower()` in some places and raw `email == path_param` in others;
    an admin who created a 'denied' override for John.Doe@... could
    silently have it never apply against the lowercased token email.
    (Fable auth audit H2.)
    """
    return (email or "").strip().lower()


def create_access_token(data: dict, expires_hours: int = 8) -> str:
    to_encode = data.copy()
    if "email" in to_encode:
        to_encode["email"] = normalize_email(to_encode["email"])
    to_encode["exp"] = datetime.utcnow() + timedelta(hours=expires_hours)
    to_encode["iat"] = datetime.utcnow()
    return jwt.encode(to_encode, settings.secret_key, algorithm=settings.algorithm)


def verify_token(token: str) -> Optional[dict]:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        email = normalize_email(payload.get("email"))
        if not email:
            return None
        # Domain check on the lowercased email so a token issued for
        # User@WaldorfWomensCare.com still maps to a configured
        # allowed domain. (Fable auth audit H2.)
        domain = email.split("@")[-1]
        if domain not in ALLOWED_DOMAINS:
            return None
        payload["email"] = email
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
        # Refuse access — do NOT auto-provision. A token whose subject
        # has no User row means either (a) the user was deleted by an
        # admin and is still presenting an unexpired JWT, or (b) the
        # token was issued against a User row that has since been
        # removed for some other reason. Either way, the right answer
        # is 401 + force re-login: the Google login flow is the only
        # place that creates accounts now, so a deleted user can't
        # silently resurrect themselves and a never-provisioned
        # token-holder can't bypass the login provisioning step.
        # (Fable auth audit C2.)
        raise HTTPException(
            status_code=401,
            detail="Account no longer exists. Sign in again to recreate it.")

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

    # Exchange code for tokens with Google. Use an explicit timeout —
    # the previous code had none, so a slow Google response would hang
    # a worker indefinitely. (Fable auth audit M5.)
    async with httpx.AsyncClient(timeout=10.0) as client:
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

    # Verify the Google ID token signature, audience, and issuer using
    # Google's public keys. Previously the code only checked that
    # id_token was *present*, then trusted the userinfo endpoint
    # response (which is anyone-can-call given a leaked access_token).
    # An attacker with a leaked access_token for a *consumer* Google
    # account whose self-claimed email is in our allowed domain could
    # mint a session as that staff member. We now reject any token
    # where email_verified isn't true or the audience doesn't match
    # our client_id. (Fable auth audit H1.)
    try:
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token as google_id_token
        idinfo = google_id_token.verify_oauth2_token(
            id_token, google_requests.Request(),
            settings.google_client_id)
    except Exception as exc:
        raise HTTPException(
            status_code=401,
            detail=f"ID token verification failed: {exc}")
    iss = idinfo.get("iss") or ""
    if iss not in ("accounts.google.com", "https://accounts.google.com"):
        raise HTTPException(
            status_code=401,
            detail=f"Unexpected ID token issuer: {iss!r}")
    if not idinfo.get("email_verified"):
        raise HTTPException(
            status_code=403,
            detail="Google account email is not verified — cannot sign in.")

    email = normalize_email(idinfo.get("email") or "")
    if not email:
        raise HTTPException(status_code=400, detail="ID token has no email")
    domain = email.split("@")[-1] if email else ""

    if domain not in ALLOWED_DOMAINS:
        raise HTTPException(
            status_code=403,
            detail=f"Access denied. Only {', '.join(ALLOWED_DOMAINS)} emails are allowed."
        )
    # Userinfo only needed for the display name/picture now; the
    # authoritative email + identity come from the verified ID token.
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            userinfo_resp = await client.get(
                "https://www.googleapis.com/oauth2/v3/userinfo",
                headers={"Authorization": f"Bearer {token_data['access_token']}"},
            )
        userinfo = userinfo_resp.json() if userinfo_resp.status_code == 200 else {}
    except Exception:
        userinfo = {}
    # Prefer the verified ID-token name; fall back to userinfo + ""
    userinfo = {
        "email": email,
        "name": idinfo.get("name") or userinfo.get("name", ""),
        "picture": idinfo.get("picture") or userinfo.get("picture", ""),
    }

    # First-time login: provision the User row here, NOT in
    # get_current_user. The previous design auto-provisioned on any
    # request whose token had no matching User row, which meant a
    # deleted user with an unexpired JWT would silently re-create
    # their account on the next request and regain default-staff
    # access. (Fable auth audit C2.)
    from app.database import SessionLocal
    _db = SessionLocal()
    try:
        email_norm = email.lower().strip()
        existing = _db.query(User).filter(User.email == email_norm).first()
        if existing is not None and not existing.is_active:
            raise HTTPException(
                status_code=403,
                detail="Your account is suspended. Contact your administrator.",
            )
        if existing is None:
            # Provision the new account here, where intent-to-create is
            # explicit (the user just completed an OAuth handshake).
            new_user = User(
                email=email_norm,
                group=UserGroup.CLINICAL,
                display_name=userinfo.get("name") or "",
            )
            _db.add(new_user)
            try:
                _db.commit()
            except Exception:
                # Concurrent first login (Fable M1). Roll back and
                # re-query — the other request already provisioned us.
                _db.rollback()
            log_action(_db, "USER_CREATED", "user",
                       resource_id=email_norm,
                       user_name=email_norm,
                       description="Auto-created via Google OAuth login")
            # Auto-join Default Staff so new hires get the baseline
            # tiers (Chart View + My Checklist Work) without admin
            # intervention.
            from app.services.default_staff_group import (
                auto_join_default_staff, ensure_default_staff_group,
            )
            ensure_default_staff_group(_db)
            auto_join_default_staff(_db, email_norm)
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
    # `secure=True` so the session cookie isn't sent over plaintext
    # HTTP. Production is HTTPS-only; local dev bypasses this via the
    # WWC_DEV_INSECURE_COOKIES env flag. (Fable auth audit H3.)
    import os
    secure_cookie = os.environ.get(
        "WWC_DEV_INSECURE_COOKIES", "").lower() not in ("true", "1", "yes")
    response.set_cookie(
        key="session_token",
        value=session_token,
        httponly=True,
        samesite="lax",
        secure=secure_cookie,
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
    # The User.is_super_admin column is the canonical flag, but membership
    # in the "Super Admin" group is treated as equivalent — the group is
    # how Office Managers grant super-admin in the admin UI, and we don't
    # want the column flip step to leave them locked out of admin-gated
    # frontend routes (e.g. /admin/reputation/*).
    in_super_admin_group = bool(user_row and any(
        (g.name or "").lower() == "super admin" for g in (user_row.groups or [])
    ))
    is_super_admin = bool(user_row and (user_row.is_super_admin or in_super_admin_group))
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
