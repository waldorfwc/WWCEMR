"""Admin user manager — admin-only CRUD on the users table."""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User, UserGroup, PRACTICE_ROLES
from app.services.audit_service import log_action
from app.routers.auth import get_current_user
from app.permissions.dependencies import requires_super_admin

router = APIRouter(prefix="/admin/users", tags=["admin-users"])


class CreateUserPayload(BaseModel):
    email: EmailStr
    group: UserGroup
    display_name: Optional[str] = None


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
    }


@router.get("")
def list_users(db: Session = Depends(get_db),
               current_user: dict = Depends(get_current_user)):
    rows = db.query(User).all()
    rows.sort(key=_sort_key)
    return [_serialize(u) for u in rows]


@router.patch("/{email}")
def update_user(
    email: str,
    payload: UpdateUserPayload,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    row = db.query(User).filter(User.email == email).first()
    if row is None:
        raise HTTPException(status_code=404, detail="user not found")

    old = {"group": row.group.value if hasattr(row.group, "value") else row.group,
           "display_name": row.display_name,
           "practice_role": row.practice_role,
           "ringcentral_user_id": row.ringcentral_user_id,
           "ringcentral_extension": row.ringcentral_extension,
           "ringcentral_callback_number": row.ringcentral_callback_number}

    # Last-admin guard
    if payload.group is not None and payload.group != UserGroup.ADMIN and row.group == UserGroup.ADMIN:
        admin_count = db.query(User).filter(User.group == UserGroup.ADMIN).count()
        if admin_count <= 1:
            raise HTTPException(status_code=409, detail="cannot remove the last admin")

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
        row.ringcentral_extension = payload.ringcentral_extension.strip() or None
    if payload.ringcentral_manual_override is not None:
        row.ringcentral_manual_override = bool(payload.ringcentral_manual_override)
    if payload.is_active is not None:
        row.is_active = payload.is_active
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

    new = {"group": row.group.value if hasattr(row.group, "value") else row.group,
           "display_name": row.display_name,
           "practice_role": row.practice_role,
           "ringcentral_user_id": row.ringcentral_user_id,
           "ringcentral_extension": row.ringcentral_extension,
           "ringcentral_callback_number": row.ringcentral_callback_number}
    log_action(db, "USER_UPDATED", "user",
               resource_id=email,
               user_name=current_user.get("email"),
               old_values=old, new_values=new,
               description=f"admin {current_user.get('email')} updated {email}")

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
    row = db.query(User).filter(User.email == email).first()
    if row is None:
        raise HTTPException(status_code=404, detail="user not found")

    me_email = (current_user.get("email") or "").lower().strip()
    target_email = (row.email or "").lower().strip()
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
    current_user: dict = Depends(get_current_user),
):
    email = str(payload.email).lower().strip()
    existing = db.query(User).filter(User.email == email).first()
    if existing is not None:
        raise HTTPException(status_code=409, detail="user already exists")

    row = User(
        email=email,
        group=payload.group,
        display_name=payload.display_name,
    )
    db.add(row)
    db.commit()
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
    current_user: dict = Depends(get_current_user),
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
    jwt = os.environ.get("RC_JWT_TOKEN", "").strip()
    base = os.environ.get("RC_SERVER_URL", "https://platform.ringcentral.com").strip()
    basic = base64.b64encode(f"{cid}:{csec}".encode()).decode()
    tok_resp = httpx.post(
        f"{base}/restapi/oauth/token",
        data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
              "assertion": jwt},
        headers={"Authorization": f"Basic {basic}",
                 "Content-Type": "application/x-www-form-urlencoded"},
        timeout=15,
    ).json()
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
            pass  # leave as-is

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
