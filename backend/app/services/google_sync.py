"""Google Workspace user sync.

Pulls every user from the Workspace Directory API on a schedule and:
  • creates a User row for each Google user not yet in our DB (and not
    on the GoogleSyncExclusion list)
  • flips is_active=True for users Google reports as 'active'
  • flips is_active=False for users Google reports as 'suspended' or
    that have been deleted/disappeared from the directory
  • never deletes a User row — historical data hangs off it

Configuration (env, all required for live sync):
  GOOGLE_WORKSPACE_SA_JSON         JSON service-account credentials
  GOOGLE_WORKSPACE_DELEGATED_ADMIN super admin email the SA impersonates
  GOOGLE_WORKSPACE_CUSTOMER_ID     "my_customer" or your customer ID

If any env var is missing, the sync is a no-op (logs and returns).
This makes the scheduler safe to run pre-provisioning.

Required Google scope (granted via domain-wide delegation):
  https://www.googleapis.com/auth/admin.directory.user.readonly
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from app.utils.dt import now_utc_naive
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.google_sync import GoogleSyncExclusion, GoogleSyncRun
from app.models.user import User, UserGroup
from app.services.audit_service import log_action

log = logging.getLogger(__name__)


SCOPES = ["https://www.googleapis.com/auth/admin.directory.user.readonly"]


# ─── Auth + API client ──────────────────────────────────────────────

def _config() -> dict:
    return {
        "sa_json": os.environ.get("GOOGLE_WORKSPACE_SA_JSON", "").strip(),
        "delegated_admin": os.environ.get("GOOGLE_WORKSPACE_DELEGATED_ADMIN", "").strip(),
        "customer_id": os.environ.get("GOOGLE_WORKSPACE_CUSTOMER_ID", "my_customer").strip(),
    }


def is_configured() -> bool:
    cfg = _config()
    return bool(cfg["sa_json"] and cfg["delegated_admin"])


def _build_client():
    """Build a Directory API service client. Lazy-imports the Google libs
    so the rest of the app boots without them."""
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    cfg = _config()
    if not (cfg["sa_json"] and cfg["delegated_admin"]):
        raise RuntimeError("Google Workspace sync is not configured (missing env vars)")

    info = json.loads(cfg["sa_json"])
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=SCOPES,
    ).with_subject(cfg["delegated_admin"])
    return build("admin", "directory_v1", credentials=creds, cache_discovery=False)


def list_workspace_users() -> List[dict]:
    """Return every user in the Workspace customer, paged.

    Returns a list of dicts with the fields we care about:
      email, suspended, archived, full_name, given_name, family_name,
      photo_url
    """
    cfg = _config()
    svc = _build_client()
    out: List[dict] = []
    page_token = None
    while True:
        resp = svc.users().list(
            customer=cfg["customer_id"],
            maxResults=200,
            orderBy="email",
            pageToken=page_token,
            projection="basic",
        ).execute()
        for u in resp.get("users", []):
            email = (u.get("primaryEmail") or "").lower().strip()
            if not email:
                continue
            name = u.get("name") or {}
            out.append({
                "email": email,
                "suspended": bool(u.get("suspended")),
                "archived": bool(u.get("archived")),
                "full_name": name.get("fullName"),
                "given_name": name.get("givenName"),
                "family_name": name.get("familyName"),
                "photo_url": u.get("thumbnailPhotoUrl"),
            })
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return out


# ─── Sync engine ────────────────────────────────────────────────────

def run_sync(db: Session, *, triggered_by: str = "system:cron") -> dict:
    """Pull from Google Workspace, diff against our DB, apply changes.

    Returns a dict matching the GoogleSyncRun audit row so callers can
    surface results immediately.
    """
    run = GoogleSyncRun(triggered_by=triggered_by, status="running")
    db.add(run); db.commit(); db.refresh(run)

    if not is_configured():
        run.status = "error"
        run.error_message = "Google Workspace sync not configured (missing env vars)"
        run.finished_at = now_utc_naive()
        db.commit()
        log.info("Google sync skipped — not configured")
        return _run_dict(run)

    try:
        google_users = list_workspace_users()
    except Exception as exc:
        run.status = "error"
        run.error_message = f"Directory API call failed: {exc}"
        run.finished_at = now_utc_naive()
        db.commit()
        log.exception("Google sync API call failed")
        return _run_dict(run)

    # Pull our state once
    excluded_emails = {e.email for e in db.query(GoogleSyncExclusion).all()}
    our_users = {u.email: u for u in db.query(User).all()}
    google_emails = {gu["email"] for gu in google_users}

    now = now_utc_naive()
    created = activated = suspended = excluded = 0
    detail = {"created": [], "activated": [], "suspended": []}

    for gu in google_users:
        email = gu["email"]
        if email in excluded_emails:
            excluded += 1
            continue

        existing = our_users.get(email)
        google_active = (not gu["suspended"]) and (not gu["archived"])

        if existing is None:
            # Brand-new user from Google
            row = User(
                email=email,
                group=UserGroup.CLINICAL,    # default — admin reassigns
                display_name=gu["full_name"],
                is_active=google_active,
                auto_provisioned=True,
                last_google_sync=now,
            )
            db.add(row)
            created += 1
            detail["created"].append(email)
        else:
            # Existing user — sync activeness if Google's view differs.
            existing.last_google_sync = now
            if google_active and not existing.is_active:
                existing.is_active = True
                activated += 1
                detail["activated"].append(email)
            elif (not google_active) and existing.is_active:
                existing.is_active = False
                # Revoke any live session — mirror the admin "suspend" path so
                # an auto-deactivated user gets a clean 401 logout instead of a
                # zombie session that 403s on every request.
                existing.token_version = int(getattr(existing, "token_version", 0) or 0) + 1
                suspended += 1
                detail["suspended"].append(email)

    # Users present in our DB and NOT in Google — Google deletion.
    # We never delete the row (audit history hangs off it). Mark inactive.
    # Skip users that are already inactive, are excluded, or were never
    # auto-provisioned (manually-created system accounts shouldn't get
    # auto-suspended just because they aren't in Google Workspace).
    for email, u in our_users.items():
        if email in google_emails:
            continue
        if email in excluded_emails:
            continue
        if not u.auto_provisioned:
            continue
        if u.is_active:
            u.is_active = False
            # Revoke live sessions too (see note above).
            u.token_version = int(getattr(u, "token_version", 0) or 0) + 1
            suspended += 1
            detail["suspended"].append(email)

    run.google_users_seen = len(google_users)
    run.created = created
    run.activated = activated
    run.suspended = suspended
    run.excluded = excluded
    run.status = "success"
    run.finished_at = now_utc_naive()
    run.detail_json = detail

    db.commit()
    log_action(db, "GOOGLE_SYNC", "user",
               user_name=triggered_by,
               description=(f"Google sync: {len(google_users)} seen, "
                            f"{created} created, {activated} re-activated, "
                            f"{suspended} suspended, {excluded} excluded"))
    log.info("Google sync ok — seen=%d created=%d activated=%d suspended=%d excluded=%d",
              len(google_users), created, activated, suspended, excluded)
    return _run_dict(run)


def preview_new_users(db: Session) -> List[str]:
    """Google emails that *would* be created on the next sync.
    Useful for the admin page to pre-add exclusions before the sync runs."""
    if not is_configured():
        return []
    google_users = list_workspace_users()
    excluded_emails = {e.email for e in db.query(GoogleSyncExclusion).all()}
    our_emails = {u.email for u in db.query(User).all()}
    return sorted(
        gu["email"] for gu in google_users
        if gu["email"] not in our_emails
        and gu["email"] not in excluded_emails
    )


# ─── Helpers ─────────────────────────────────────────────────────────

def _run_dict(r: GoogleSyncRun) -> dict:
    return {
        "id": str(r.id),
        "started_at": str(r.started_at),
        "finished_at": str(r.finished_at) if r.finished_at else None,
        "triggered_by": r.triggered_by,
        "status": r.status,
        "google_users_seen": r.google_users_seen,
        "created": r.created,
        "activated": r.activated,
        "suspended": r.suspended,
        "excluded": r.excluded,
        "error_message": r.error_message,
        "detail": r.detail_json,
    }
