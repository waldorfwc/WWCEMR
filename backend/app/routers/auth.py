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
def get_me(user: dict = Depends(get_current_user)):
    """Return current authenticated user (email, name, picture, group)."""
    return {
        "email": user.get("email"),
        "name": user.get("name"),
        "picture": user.get("picture"),
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
