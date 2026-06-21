"""Admin user manager — admin-only CRUD on the users table."""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User, UserGroup, PRACTICE_ROLES
from app.services.audit_service import log_action
from app.routers.auth import get_current_user, normalize_email
from app.permissions.dependencies import requires_super_admin

router = APIRouter(prefix="/admin/users", tags=["admin-users"])


class CreateUserPayload(BaseModel):
    email: EmailStr
    group: UserGroup
    display_name: Optional[str] = None
    npi: Optional[str] = None
    clinician_role: Optional[str] = None
    credential: Optional[str] = None


class UpdateUserPayload(BaseModel):
    group: Optional[UserGroup] = None
    display_name: Optional[str] = None
    # Empty string = clear the role; None = no change
    practice_role: Optional[str] = None
    # RingCentral identity (admin-editable). Empty string clears.
    ringcentral_user_id: Optional[str] = None
    ringcentral_extension: Optional[str] = None
    ringcentral_callback_number: Optional[str] = None
    # When True, the email-matching auto-sync skips this user.
    ringcentral_manual_override: Optional[bool] = None
    # Active flag — manual override (Google sync may flip this back next run
    # if the Google state still says suspended; for hard manual control,
    # add the email to GoogleSyncExclusion as well).
    is_active: Optional[bool] = None
    # Clinician identity for LARC enrollment-form pickers. Empty string
    # clears (removes the user from the clinicians dropdown).
    npi: Optional[str] = None
    # Values: 'provider' | 'app' | '' (clear)
    clinician_role: Optional[str] = None
    # Values: 'MD' | 'DO' | 'NP' | 'PA' | '' (clear) — printed on Bayer
    # LARC enrollment forms via the provider-credentials checkbox row.
    credential: Optional[str] = None


def _sort_key(u: User) -> tuple:
    # admin → billing → clinical, then email asc
    order = {UserGroup.ADMIN: 0, UserGroup.BILLING: 1, UserGroup.CLINICAL: 2}
    return (order.get(u.group, 99), u.email or "")


def _serialize(u: User) -> dict:
    group_val = u.group.value if hasattr(u.group, "value") else u.group
    return {
        "email": u.email,
        "group": group_val,
        "display_name": u.display_name,
        "practice_role": u.practice_role,
        "ringcentral_user_id": u.ringcentral_user_id,
        "ringcentral_extension": u.ringcentral_extension,
        "ringcentral_callback_number": u.ringcentral_callback_number,
        "ringcentral_manual_override": bool(u.ringcentral_manual_override),
        "is_active": bool(u.is_active),
        "auto_provisioned": bool(u.auto_provisioned),
        "last_google_sync": u.last_google_sync.isoformat() + "Z" if u.last_google_sync else None,
        "created_at": u.created_at.isoformat() + "Z" if u.created_at else None,
        "updated_at": u.updated_at.isoformat() + "Z" if u.updated_at else None,
        "npi": u.npi,
        "clinician_role": u.clinician_role,
        "credential": u.credential,
    }


@router.get("")
def list_users(db: Session = Depends(get_db),
               current_user: dict = Depends(requires_super_admin())):
    rows = db.query(User).all()
    rows.sort(key=_sort_key)
    return [_serialize(u) for u in rows]


@router.get("/clinicians")
def list_clinicians(db: Session = Depends(get_db),
                    current_user: dict = Depends(get_current_user)):
    """Active users with a non-blank NPI — populates the LARC enrollment
    pickers (Inserting Provider + APP). Front-end filters/groups by
    `clinician_role`. Returns email/display_name/npi/clinician_role/is_active."""
    rows = (db.query(User)
              .filter(User.is_active.is_(True),
                      User.npi.isnot(None),
                      User.npi != "")
              .all())
    rows.sort(key=lambda u: (u.clinician_role or "zz", u.display_name or u.email))
    return [
        {
            "email": u.email,
            "display_name": u.display_name or u.email,
            "npi": u.npi,
            "clinician_role": u.clinician_role,
            "credential": u.credential,
        }
        for u in rows
    ]


@router.patch("/{email}")
def update_user(
    email: str,
    payload: UpdateUserPayload,
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_super_admin()),
):
    email = normalize_email(email)
    row = db.query(User).filter(User.email == email).first()
    if row is None:
        raise HTTPException(status_code=404, detail="user not found")

    # Capture every audit-relevant field BEFORE mutation, not just the
    # subset that update_user used to log. is_active flips and clinical
    # identity fields (npi, clinician_role, credential) are
    # security/compliance-relevant; ringcentral_manual_override decides
    # whether the auto-sync overwrites a user's phone identity.
    # (Fable auth audit M3.)
    _audited = ("group", "display_name", "practice_role",
                "ringcentral_user_id", "ringcentral_extension",
                "ringcentral_callback_number", "ringcentral_manual_override",
                "is_active", "npi", "clinician_role", "credential")
    def _snapshot(r) -> dict:
        out = {}
        for k in _audited:
            v = getattr(r, k, None)
            if hasattr(v, "value"):  # enum
                v = v.value
            out[k] = v
        return out
    old = _snapshot(row)

    # NB — the legacy `group` column (UserGroup enum) no longer drives
    # any privilege; the actual authority is User.is_super_admin (set
    # via /admin/users/{email}/super_admin) and per-module tier grants.
    # The remaining last-admin guards live on those endpoints
    # (set_super_admin, delete_user) so demoting via update_user.group
    # is safe — it doesn't change anyone's effective access.

    if payload.group is not None:
        row.group = payload.group
    if payload.display_name is not None:
        row.display_name = payload.display_name
    if payload.practice_role is not None:
        # Empty string clears the role; otherwise must be one of PRACTICE_ROLES
        if payload.practice_role and payload.practice_role not in PRACTICE_ROLES:
            raise HTTPException(
                status_code=422,
                detail=f"invalid practice_role; must be one of {list(PRACTICE_ROLES)}",
            )
        row.practice_role = payload.practice_role or None
    if payload.ringcentral_user_id is not None:
        row.ringcentral_user_id = payload.ringcentral_user_id.strip() or None
    if payload.ringcentral_extension is not None:
        ext = (payload.ringcentral_extension or "").strip()
        # Digits-only with a sane length envelope. The sync overwrites
        # this anyway unless ringcentral_manual_override is set, so the
        # only path that reaches this assignment is a manual edit —
        # validating means a typo'd "12345abc" doesn't quietly land in
        # the directory and break click-to-dial. (Fable auth audit L5.)
        if ext and (not ext.isdigit() or not (1 <= len(ext) <= 8)):
            raise HTTPException(
                status_code=422,
                detail=("ringcentral_extension must be 1–8 digits "
                        "(empty string clears it)"))
        row.ringcentral_extension = ext or None
    if payload.ringcentral_manual_override is not None:
        row.ringcentral_manual_override = bool(payload.ringcentral_manual_override)
    if payload.is_active is not None:
        # Suspending a user also revokes any outstanding JWTs they hold
        # — bump token_version so the captured token can't outlive the
        # suspension. (Fable auth audit L4.)
        if row.is_active and not payload.is_active:
            row.token_version = int(getattr(row, "token_version", 0) or 0) + 1
        row.is_active = payload.is_active
    if payload.npi is not None:
        row.npi = (payload.npi or "").strip() or None
    if payload.clinician_role is not None:
        cr = (payload.clinician_role or "").strip().lower()
        if cr and cr not in ("provider", "app"):
            raise HTTPException(
                status_code=422,
                detail="clinician_role must be 'provider', 'app', or empty",
            )
        row.clinician_role = cr or None
    if payload.credential is not None:
        c = (payload.credential or "").strip().upper()
        if c and c not in ("MD", "DO", "NP", "PA"):
            raise HTTPException(
                status_code=422,
                detail="credential must be 'MD', 'DO', 'NP', 'PA', or empty",
            )
        row.credential = c or None
    if payload.ringcentral_callback_number is not None:
        cb = payload.ringcentral_callback_number.strip()
        if cb:
            # Normalize to E.164 — accept "(240) 565-3594", "240-565-3594", etc.
            digits = "".join(c for c in cb if c.isdigit() or c == "+")
            if digits.startswith("+"):
                cb = digits
            elif len(digits) == 10:
                cb = f"+1{digits}"
            elif len(digits) == 11 and digits.startswith("1"):
                cb = f"+{digits}"
            else:
                raise HTTPException(status_code=422,
                                    detail=f"callback number must be a valid US phone")
        row.ringcentral_callback_number = cb or None
    db.commit()
    db.refresh(row)

    new = _snapshot(row)
    # Only audit fields that actually changed — keeps the diff readable
    # and the action label informative.
    diff_old = {k: v for k, v in old.items() if old[k] != new.get(k)}
    diff_new = {k: v for k, v in new.items() if old.get(k) != v}
    changed = sorted(diff_new.keys()) if diff_new else []
    # Distinguish suspension from a regular edit so the audit log
    # filterable by action surfaces the security-relevant flip first.
    action_label = "USER_UPDATED"
    if "is_active" in diff_new:
        action_label = ("USER_SUSPENDED"
                        if not diff_new["is_active"] else "USER_REACTIVATED")
    log_action(db, action_label, "user",
               resource_id=email,
               user_name=current_user.get("email"),
               old_values=diff_old, new_values=diff_new,
               description=(f"admin {current_user.get('email')} updated "
                            f"{email}" + (f" ({', '.join(changed)})"
                                            if changed else "")))

    return _serialize(row)


@router.delete("/{email}", status_code=204)
def delete_user(
    email: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_super_admin()),
):
    """Hard-delete a user. Refuses to delete:
      • yourself (avoid lockout)
      • the last user with `user:manage` permission

    The user's group memberships go with them (cascade via the
    user_groups association table). String-keyed historical references
    (task instances, audit log, trainer authorizations) keep the email
    as a historical attribution — they aren't FK-linked so they don't
    block deletion.
    """
    email = normalize_email(email)
    row = db.query(User).filter(User.email == email).first()
    if row is None:
        raise HTTPException(status_code=404, detail="user not found")

    me_email = normalize_email(current_user.get("email"))
    target_email = normalize_email(row.email)
    if me_email and me_email == target_email:
        raise HTTPException(
            status_code=409,
            detail="You can't delete your own account. Have another admin do it.")

    # Last-Super-Admin guard. With the tier model, "the system admin" =
    # User.is_super_admin. Refuse to delete the last one — same protection
    # that lives in permission_grants.set_super_admin.
    if row.is_super_admin:
        others = (db.query(User)
                    .filter(User.is_super_admin.is_(True),
                            User.email != row.email)
                    .count())
        if others == 0:
            raise HTTPException(
                status_code=409,
                detail=("Cannot delete the last Super Admin. Promote "
                        "another user to Super Admin first."))

    # Audit BEFORE the delete so the email is preserved.
    log_action(db, "USER_DELETED", "user",
               resource_id=email,
               user_name=current_user.get("email"),
               old_values={"email": row.email, "display_name": row.display_name,
                           "group": row.group.value if hasattr(row.group, "value") else row.group,
                           "is_active": bool(row.is_active),
                           "groups": [g.name for g in (row.groups or [])]},
               description=f"admin {current_user.get('email')} deleted {email}")

    # Clear group memberships first (cascade should handle it, but be
    # explicit so we don't depend on table-level cascade settings).
    row.groups = []
    db.flush()
    db.delete(row)
    db.commit()
    return


@router.post("", status_code=201)
def create_user(
    payload: CreateUserPayload,
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_super_admin()),
):
    email = normalize_email(payload.email)
    existing = db.query(User).filter(User.email == email).first()
    if existing is not None:
        raise HTTPException(status_code=409, detail="user already exists")

    row = User(
        email=email,
        group=payload.group,
        display_name=payload.display_name,
    )
    if payload.npi is not None:
        row.npi = payload.npi.strip() or None
    if payload.clinician_role is not None:
        row.clinician_role = payload.clinician_role.strip() or None
    if payload.credential is not None:
        row.credential = payload.credential.strip() or None
    db.add(row)
    try:
        db.commit()
    except Exception:
        # Concurrent create_user (Fable auth audit M1). Roll back and
        # surface a clean 409 — the other request already created the
        # row, so this caller's "create" is implicitly an "exists."
        db.rollback()
        raise HTTPException(status_code=409, detail="user already exists")
    db.refresh(row)
    log_action(db, "USER_CREATED_BY_ADMIN", "user",
               resource_id=email,
               user_name=current_user.get("email"),
               new_values={"group": payload.group.value, "display_name": payload.display_name},
               description=f"admin {current_user.get('email')} pre-created {email} as {payload.group.value}")
    return _serialize(row)


@router.post("/sync-ringcentral")
def sync_ringcentral(
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_super_admin()),
):
    """Pull every WWC user's RingCentral identity from the RC API and
    populate ringcentral_user_id, _extension, and _callback_number.

    Match users by email. For callback_number, prefer ForwardedNumber
    > DirectNumber > MobileInfrastructureNumber. Skips users already
    fully-mapped unless their callback_number is empty.
    """
    import httpx, base64, os
    from app.services.ringcentral_client import client as rc_client

    rc = rc_client()
    extensions = rc.list_extensions()

    # Build email → extension map (first match wins for shared mailboxes)
    email_to_ext = {}
    for ex in extensions:
        contact = ex.get("contact") or {}
        email = (contact.get("email") or "").lower().strip()
        if email and email not in email_to_ext:
            email_to_ext[email] = ex

    # Build access token for phone-number lookups
    cid = os.environ.get("RC_CLIENT_ID", "").strip()
    csec = os.environ.get("RC_CLIENT_SECRET", "").strip()
    # Local `rc_jwt` instead of shadowing the module-level `jwt` import
    # — the previous variable name `jwt` masked it for the rest of the
    # function and made auth_router debugging trickier. (Fable L1.)
    rc_jwt = os.environ.get("RC_JWT_TOKEN", "").strip()
    base = os.environ.get("RC_SERVER_URL", "https://platform.ringcentral.com").strip()
    basic = base64.b64encode(f"{cid}:{csec}".encode()).decode()
    try:
        tok_http = httpx.post(
            f"{base}/restapi/oauth/token",
            data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                  "assertion": rc_jwt},
            headers={"Authorization": f"Basic {basic}",
                     "Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
        tok_resp = tok_http.json()
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"RingCentral OAuth failed: {exc}")
    if "access_token" not in tok_resp:
        # KeyError used to bubble up as a 500 (Fable L1). Surface a
        # cleaner error with what we know.
        raise HTTPException(
            status_code=502,
            detail=(f"RingCentral OAuth returned no access_token; "
                    f"response: {str(tok_resp)[:200]}"))
    H = {"Authorization": f"Bearer {tok_resp['access_token']}"}

    USAGE_PRIORITY = ["ForwardedNumber", "DirectNumber",
                       "MobileInfrastructureNumber"]
    SKIP_USAGES = {"MainCompanyNumber", "CompanyFaxNumber", "CompanyNumber"}

    updated, skipped, missing, locked = 0, 0, [], 0
    for u in db.query(User).all():
        # Honor manual overrides — admins set these by hand because the user's
        # RC seat is registered under a different email (or two WWC accounts
        # share one RC seat). Auto-sync must leave these rows alone.
        if u.ringcentral_manual_override:
            locked += 1
            continue
        em = (u.email or "").lower().strip()
        ex = email_to_ext.get(em)
        if not ex:
            missing.append(u.email)
            continue

        new_uid = str(ex["id"])
        new_ext = str(ex.get("extensionNumber") or "")

        # Pull phones for callback number
        new_cb = u.ringcentral_callback_number  # keep existing if we can't find better
        try:
            r = httpx.get(
                f"{base}/restapi/v1.0/account/~/extension/{new_uid}/phone-number",
                headers=H, timeout=15,
            )
            if r.status_code == 200:
                voice = [n for n in r.json().get("records", [])
                         if n.get("type") != "FaxOnly"]
                chosen = None
                for usage in USAGE_PRIORITY:
                    chosen = next((n for n in voice
                                   if n.get("usageType") == usage), None)
                    if chosen: break
                if not chosen:
                    chosen = next((n for n in voice
                                   if n.get("usageType") not in SKIP_USAGES), None)
                if chosen and not u.ringcentral_callback_number:
                    new_cb = chosen["phoneNumber"]
        except Exception as exc:
            # Log so a recurring per-user failure is visible; previous
            # `except Exception: pass` silently masked auth/network
            # issues that affected only some rows. (Fable L1.)
            import logging
            logging.getLogger(__name__).warning(
                "ringcentral phone-number fetch failed for %s: %s",
                u.email, exc)

        changed = (
            u.ringcentral_user_id != new_uid
            or u.ringcentral_extension != new_ext
            or u.ringcentral_callback_number != new_cb
        )
        if changed:
            u.ringcentral_user_id = new_uid
            u.ringcentral_extension = new_ext
            u.ringcentral_callback_number = new_cb
            updated += 1
        else:
            skipped += 1

    db.commit()
    log_action(db, "RC_SYNC", "user", user_name=current_user.get("email"),
               description=f"RingCentral sync: updated {updated}, "
                           f"skipped {skipped}, no_match {len(missing)}, "
                           f"locked {locked}")
    return {
        "updated": updated,
        "unchanged": skipped,
        "no_rc_match": missing,
        "manual_override_locked": locked,
    }
