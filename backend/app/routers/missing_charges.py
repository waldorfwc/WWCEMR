"""Missing Charges router.

Workflow vocab (column 'status'):
  new                 — just uploaded; biller hasn't triaged
  needs_to_be_billed  — biller marked Seen; provider must complete chargeable note
  provider_billed     — provider says billed; biller enters claim #
  provider_error      — provider can't bill; sees explanation; biller follows up
  billed              — claim # entered (terminal)
  no_show             — patient was a no-show (terminal)
  canceled            — appointment was canceled (terminal)

Endpoints here cover the *biller-facing* workflow. Provider self-service
(signed-token portal + weekly cron email) lives in a separate router.
"""
from __future__ import annotations

from datetime import date as _date, datetime
from app.utils.dt import now_utc_naive
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models.missing_charge import (
    MissingCharge, MissingChargeImport, MissingChargeNote,
    ProviderUserMapping, STATUSES, TERMINAL_STATUSES,
)
from app.routers.auth import get_current_user
from app.permissions.catalog import Module, Tier
from app.permissions.dependencies import requires_tier
from app.services import missing_charges_import as importer
from app.services import missing_charges_token as token_svc


router = APIRouter(prefix="/billing/missing-charges", tags=["billing-missing-charges"])


def _charge_dict(c: MissingCharge, include_notes: bool = False) -> dict:
    out = {
        "id": str(c.id),
        "patient_mrn":          c.patient_mrn,
        "patient_name":         c.patient_name,
        "patient_dob":          str(c.patient_dob) if c.patient_dob else None,
        "appointment_date":     str(c.appointment_date),
        "appointment_type":     c.appointment_type,
        "appointment_status":   c.appointment_status,
        "visit_status":         c.visit_status,
        "payer":                c.payer,
        "primary_provider":     c.primary_provider,
        "bill_same_dos":        c.bill_same_dos,
        "bill_same_dos_loc":    c.bill_same_dos_loc,
        "appointment_count":    c.appointment_count,
        "patient_link":         c.patient_link,
        "status":               c.status,
        "status_label":         dict(STATUSES).get(c.status, c.status),
        "claim_number":         c.claim_number,
        "provider_response_note": c.provider_response_note,
        "resolved_at":          c.resolved_at.isoformat() if c.resolved_at else None,
        "resolved_by":          c.resolved_by,
        "last_emailed_at":      c.last_emailed_at.isoformat() if c.last_emailed_at else None,
        "created_at":           c.created_at.isoformat() if c.created_at else None,
        "updated_at":           c.updated_at.isoformat() if c.updated_at else None,
    }
    if include_notes:
        out["notes"] = [
            {"id": str(n.id), "author": n.author, "body": n.body,
             "created_at": n.created_at.isoformat()}
            for n in (c.notes_rel or [])
        ]
    return out


# ─── Picklists ──────────────────────────────────────────────────────

@router.get("/picklists")
def picklists(db: Session = Depends(get_db),
               current_user: dict = Depends(requires_tier(Module.MISSING_CHARGES, Tier.VIEW))):
    def _distinct(col):
        return sorted({
            v for (v,) in db.query(col).filter(col.isnot(None)).distinct().all()
            if v and str(v).strip()
        })
    return {
        "statuses": [{"v": k, "l": v} for k, v in STATUSES],
        "terminal_statuses": sorted(TERMINAL_STATUSES),
        "providers":         _distinct(MissingCharge.primary_provider),
        "payers":            _distinct(MissingCharge.payer),
        "appointment_types": _distinct(MissingCharge.appointment_type),
    }


# ─── Upload ─────────────────────────────────────────────────────────

@router.post("/upload", status_code=201)
async def upload_report(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.MISSING_CHARGES, Tier.WORK)),
):
    """Parse an 'Appointment Missing Charges' Excel and upsert rows.
    Dedupe key: (patient_mrn, appointment_date)."""
    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=422, detail="empty file")
    if len(contents) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="file >25MB; split it")

    try:
        rows = importer.parse_excel(contents)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Could not parse Excel: {e}")

    imp = MissingChargeImport(
        original_filename=file.filename or "upload.xlsx",
        uploaded_by=current_user.get("email") or "system",
        total_rows=len(rows),
    )
    db.add(imp); db.flush()

    new_count, dup_count, err_count = importer.import_rows(
        db, rows, import_id=imp.id,
    )
    imp.new_rows = new_count
    imp.duplicate_rows = dup_count
    imp.error_rows = err_count
    db.commit(); db.refresh(imp)

    return {
        "import_id":     str(imp.id),
        "filename":      imp.original_filename,
        "total_rows":    imp.total_rows,
        "new_rows":      imp.new_rows,
        "duplicate_rows": imp.duplicate_rows,
        "error_rows":    imp.error_rows,
    }


@router.get("/imports")
def list_imports(db: Session = Depends(get_db),
                  current_user: dict = Depends(requires_tier(Module.MISSING_CHARGES, Tier.VIEW))):
    rows = (db.query(MissingChargeImport)
              .order_by(MissingChargeImport.uploaded_at.desc())
              .limit(50).all())
    return [
        {
            "id": str(i.id),
            "filename": i.original_filename,
            "uploaded_by": i.uploaded_by,
            "uploaded_at": i.uploaded_at.isoformat(),
            "total_rows": i.total_rows,
            "new_rows": i.new_rows,
            "duplicate_rows": i.duplicate_rows,
            "error_rows": i.error_rows,
        }
        for i in rows
    ]


# ─── List + filter ──────────────────────────────────────────────────

SORT_COLUMNS = {
    # UI key            → SQLAlchemy column
    "dos":               MissingCharge.appointment_date,
    "appointment_date":  MissingCharge.appointment_date,
    "patient":           MissingCharge.patient_name,
    "patient_name":      MissingCharge.patient_name,
    "mrn":               MissingCharge.patient_mrn,
    "patient_mrn":       MissingCharge.patient_mrn,
    "appointment":       MissingCharge.appointment_type,
    "appointment_type":  MissingCharge.appointment_type,
    "provider":          MissingCharge.primary_provider,
    "primary_provider":  MissingCharge.primary_provider,
    "payer":             MissingCharge.payer,
    "status":            MissingCharge.status,
}


@router.get("")
def list_charges(
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.MISSING_CHARGES, Tier.VIEW)),
    status: Optional[str] = None,
    provider: Optional[str] = None,
    payer: Optional[str] = None,
    appointment: Optional[str] = None,   # filter by appointment_type
    patient: Optional[str] = None,       # filter by patient_name
    mrn: Optional[str] = None,           # filter by patient_mrn
    date_from: Optional[str] = None,
    date_to:   Optional[str] = None,
    open_only: bool = False,    # exclude billed/no_show/canceled
    search: Optional[str] = None,
    sort: Optional[str] = None,          # one of SORT_COLUMNS keys
    sort_dir: str = "desc",              # asc | desc
    page: int = 1,
    per_page: int = 200,
):
    q = db.query(MissingCharge)
    if status:
        q = q.filter(MissingCharge.status == status)
    if provider:
        q = q.filter(MissingCharge.primary_provider == provider)
    if payer:
        q = q.filter(MissingCharge.payer == payer)
    if appointment:
        q = q.filter(MissingCharge.appointment_type == appointment)
    if patient:
        q = q.filter(MissingCharge.patient_name.ilike(f"%{patient}%"))
    if mrn:
        q = q.filter(MissingCharge.patient_mrn.ilike(f"%{mrn}%"))
    if date_from:
        q = q.filter(MissingCharge.appointment_date >= date_from)
    if date_to:
        q = q.filter(MissingCharge.appointment_date <= date_to)
    if open_only:
        q = q.filter(MissingCharge.status.notin_(list(TERMINAL_STATUSES)))
    if search:
        like = f"%{search}%"
        q = q.filter(or_(
            MissingCharge.patient_name.ilike(like),
            MissingCharge.patient_mrn.ilike(like),
            MissingCharge.claim_number.ilike(like),
        ))

    # Sorting — default is DOS desc, patient_name asc (legacy behavior)
    sort_col = SORT_COLUMNS.get(sort) if sort else None
    if sort_col is not None:
        direction = sort_col.desc() if (sort_dir or "").lower() == "desc" else sort_col.asc()
        # Stable tiebreaker on patient_name then id so paging is deterministic
        q = q.order_by(direction, MissingCharge.patient_name.asc(), MissingCharge.id.asc())
    else:
        q = q.order_by(MissingCharge.appointment_date.desc(),
                        MissingCharge.patient_name)

    total = q.count()
    rows = q.offset((page - 1) * per_page).limit(per_page).all()
    return {"total": total, "page": page, "per_page": per_page,
            "charges": [_charge_dict(c) for c in rows]}


# ─── Dashboard (counts by status / provider) ────────────────────────

@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db),
                current_user: dict = Depends(requires_tier(Module.MISSING_CHARGES, Tier.VIEW))):
    from sqlalchemy import func
    by_status = dict(
        db.query(MissingCharge.status, func.count(MissingCharge.id))
          .group_by(MissingCharge.status).all()
    )
    by_provider_open = dict(
        db.query(MissingCharge.primary_provider, func.count(MissingCharge.id))
          .filter(MissingCharge.status.notin_(list(TERMINAL_STATUSES)))
          .group_by(MissingCharge.primary_provider).all()
    )
    open_total = sum(by_provider_open.values())
    return {
        "by_status": {k: by_status.get(k, 0) for k in dict(STATUSES).keys()},
        "by_provider_open": by_provider_open,
        "open_total": open_total,
    }


# ─── Detail / patch / notes ─────────────────────────────────────────

def _load(db: Session, charge_id: str) -> MissingCharge:
    c = (db.query(MissingCharge)
           .options(joinedload(MissingCharge.notes_rel))
           .filter(MissingCharge.id == charge_id).first())
    if not c:
        raise HTTPException(status_code=404, detail="missing charge not found")
    return c


@router.get("/{charge_id}")
def get_charge(charge_id: str,
                db: Session = Depends(get_db),
                current_user: dict = Depends(requires_tier(Module.MISSING_CHARGES, Tier.VIEW))):
    return _charge_dict(_load(db, charge_id), include_notes=True)


class ChargePatch(BaseModel):
    status:       Optional[str] = None
    claim_number: Optional[str] = None
    provider_response_note: Optional[str] = None
    patient_link: Optional[str] = None


@router.patch("/{charge_id}")
def patch_charge(charge_id: str, payload: ChargePatch,
                  db: Session = Depends(get_db),
                  current_user: dict = Depends(requires_tier(Module.MISSING_CHARGES, Tier.WORK))):
    c = _load(db, charge_id)
    actor = current_user.get("email") or "system"
    data = payload.model_dump(exclude_unset=True)

    if "status" in data:
        if data["status"] not in dict(STATUSES):
            raise HTTPException(status_code=422,
                                detail=f"unknown status: {data['status']}")
        c.status = data["status"]
        if c.status in TERMINAL_STATUSES:
            c.resolved_at = now_utc_naive()
            c.resolved_by = actor
        else:
            c.resolved_at = None
            c.resolved_by = None

    if "claim_number" in data:
        v = (data["claim_number"] or "").strip() or None
        c.claim_number = v
        if v and c.status != "billed":
            # Auto-advance to billed when a claim # lands.
            c.status = "billed"
            c.resolved_at = now_utc_naive()
            c.resolved_by = actor

    if "provider_response_note" in data:
        c.provider_response_note = (data["provider_response_note"] or "").strip() or None

    if "patient_link" in data:
        c.patient_link = (data["patient_link"] or "").strip() or None

    db.commit(); db.refresh(c)
    return _charge_dict(c, include_notes=True)


class NoteIn(BaseModel):
    body: str


@router.post("/{charge_id}/notes", status_code=201)
def add_note(charge_id: str, payload: NoteIn,
              db: Session = Depends(get_db),
              current_user: dict = Depends(requires_tier(Module.MISSING_CHARGES, Tier.WORK))):
    c = _load(db, charge_id)
    if not payload.body.strip():
        raise HTTPException(status_code=422, detail="note body required")
    n = MissingChargeNote(
        charge_id=c.id, author=current_user.get("email") or "system",
        body=payload.body.strip(),
    )
    db.add(n); db.commit(); db.refresh(n)
    return {"id": str(n.id), "author": n.author, "body": n.body,
            "created_at": n.created_at.isoformat()}


@router.delete("/{charge_id}", status_code=204)
def delete_charge(charge_id: str,
                    db: Session = Depends(get_db),
                    current_user: dict = Depends(requires_tier(Module.MISSING_CHARGES, Tier.MANAGE))):
    """Admin-only hard delete."""
    c = _load(db, charge_id)
    db.delete(c); db.commit()
    return


# ─── Provider self-service (signed-token, no login) ─────────────────

class ProviderTokenMint(BaseModel):
    provider: str
    ttl_days: Optional[int] = None


@router.post("/provider-tokens")
def mint_provider_token(payload: ProviderTokenMint,
                          current_user: dict = Depends(requires_tier(Module.MISSING_CHARGES, Tier.WORK))):
    """Mint a signed token for a provider. Anyone with claim:read can
    generate one (so a biller can copy the link and email it ad hoc).
    Returns both the raw token and the full portal URL."""
    try:
        tok = token_svc.mint_token(payload.provider,
                                     ttl_days=payload.ttl_days or token_svc.TOKEN_TTL_DAYS)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {
        "provider": payload.provider,
        "token":    tok,
        "ttl_days": payload.ttl_days or token_svc.TOKEN_TTL_DAYS,
        "portal_url": f"/p/missing-charges/{tok}",
    }


@router.get("/provider/{token}")
def provider_portal(token: str, db: Session = Depends(get_db)):
    """Public endpoint — provider clicks the link from their email.
    Returns the provider's open `needs_to_be_billed` rows. No login."""
    payload = token_svc.decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="link is invalid or expired")

    provider = payload["provider"]
    rows = (db.query(MissingCharge)
              .filter(MissingCharge.primary_provider == provider,
                      MissingCharge.status == "needs_to_be_billed")
              .order_by(MissingCharge.appointment_date.desc())
              .all())
    return {
        "provider": provider,
        "issued_at": payload.get("iat"),
        "expires_at": payload.get("exp"),
        "open_count": len(rows),
        "charges": [
            {
                "id": str(c.id),
                "appointment_date": str(c.appointment_date),
                "patient_name": c.patient_name,
                "patient_mrn": c.patient_mrn,
                "appointment_type": c.appointment_type,
                "payer": c.payer,
                "patient_link": c.patient_link,
            }
            for c in rows
        ],
    }


class ProviderActionIn(BaseModel):
    action: str             # 'billed' | 'error'
    note: Optional[str] = None


@router.post("/provider/{token}/{charge_id}")
def provider_action(token: str, charge_id: str, payload: ProviderActionIn,
                     db: Session = Depends(get_db)):
    """Provider marks a charge as billed (note complete) or error
    (with an explanation). Token-validated, no login."""
    tok = token_svc.decode_token(token)
    if not tok:
        raise HTTPException(status_code=401, detail="link is invalid or expired")
    provider = tok["provider"]

    c = (db.query(MissingCharge)
           .filter(MissingCharge.id == charge_id).first())
    if not c:
        raise HTTPException(status_code=404, detail="charge not found")
    # Make sure the token's provider matches the row — prevents lateral access
    if c.primary_provider != provider:
        raise HTTPException(status_code=403,
                            detail="this charge isn't assigned to you")

    if payload.action == "billed":
        c.status = "provider_billed"
        c.provider_response_note = (payload.note or "").strip() or None
    elif payload.action == "error":
        if not (payload.note or "").strip():
            raise HTTPException(status_code=422,
                                detail="explanation is required for an error response")
        c.status = "provider_error"
        c.provider_response_note = payload.note.strip()
    else:
        raise HTTPException(status_code=422,
                            detail="action must be 'billed' or 'error'")

    # Auto-write a note row capturing the provider's response in the audit
    n = MissingChargeNote(
        charge_id=c.id,
        author=f"provider:{provider}",
        body=f"[{payload.action.upper()}] {payload.note or ''}".strip(),
    )
    db.add(n)
    db.commit(); db.refresh(c)
    return _charge_dict(c, include_notes=True)


# ─── Weekly email trigger (admin can run on demand) ─────────────────

@router.post("/email-providers")
def email_providers(db: Session = Depends(get_db),
                     current_user: dict = Depends(requires_tier(Module.MISSING_CHARGES, Tier.WORK))):
    """Send one email per provider with their open `needs_to_be_billed`
    rows + a signed portal link. Returns the send report.

    Safe to re-run — `last_emailed_at` is bumped per row but rows aren't
    altered otherwise. SMTP-less envs log instead of sending."""
    from app.services.missing_charges_email import send_provider_emails
    report = send_provider_emails(db, triggered_by=current_user.get("email") or "system")
    return report


# ─── Provider name → user-email mappings ────────────────────────────

def _mapping_dict(m: ProviderUserMapping) -> dict:
    return {
        "id":            str(m.id),
        "provider_name": m.provider_name,
        "user_email":    m.user_email,
        "is_active":     m.is_active == "Y",
        "is_ignored":    m.is_ignored == "Y",
        "created_at":    m.created_at.isoformat() if m.created_at else None,
        "created_by":    m.created_by,
        "updated_at":    m.updated_at.isoformat() if m.updated_at else None,
    }


@router.get("/provider-mappings")
def list_provider_mappings(
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.MISSING_CHARGES, Tier.VIEW)),
):
    """Return the full mapping list + the set of provider names from
    active rows that DON'T have a mapping yet (so the UI can prompt the
    biller to fill them in)."""
    mappings = (db.query(ProviderUserMapping)
                  .order_by(ProviderUserMapping.provider_name).all())
    # Both mapped-to-user AND explicitly-ignored count as "decided"
    decided_names = {m.provider_name for m in mappings}

    # Providers referenced by any open (non-terminal) row
    open_providers = set(
        row[0] for row in
        db.query(MissingCharge.primary_provider)
          .filter(MissingCharge.status.notin_(list(TERMINAL_STATUSES)),
                  MissingCharge.primary_provider.isnot(None))
          .distinct().all()
        if row[0]
    )
    unmapped = sorted(open_providers - decided_names)
    return {
        "mappings": [_mapping_dict(m) for m in mappings],
        "unmapped_providers": unmapped,
    }


def _normalize_name_to_tokens(name: str) -> Optional[tuple]:
    """Turn either "Last, First [M.]" or "First [M.] Last" into a sorted
    set of lowercase tokens we can compare. Drops 1-letter middle initials
    and punctuation. Returns None for unparseable / placeholder names."""
    if not name:
        return None
    s = name.strip()
    # "Last, First M." → "First M. Last"
    if "," in s:
        parts = [p.strip() for p in s.split(",", 1)]
        if len(parts) == 2 and parts[0] and parts[1]:
            s = f"{parts[1]} {parts[0]}"
    # Tokenize, drop middle initials, lowercase, strip punctuation
    tokens = []
    for raw in s.replace(".", " ").split():
        t = raw.strip().lower()
        if not t or len(t) == 1:  # middle initial or single letter — skip
            continue
        if not any(c.isalpha() for c in t):
            continue
        tokens.append(t)
    if len(tokens) < 2:
        return None
    return tuple(sorted(tokens))


@router.post("/provider-mappings/auto-match")
def auto_match_provider_mappings(
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.MISSING_CHARGES, Tier.WORK)),
):
    """Walk unmapped provider names, pair each with an active Google-sync'd
    user whose display_name matches by tokens (first + last, case-insensitive,
    middle initials ignored). Skips ambiguous matches and unparseable
    placeholder names (e.g. 'Nurse, Schedule')."""
    from app.models.user import User

    # Build name → email index from active workforce
    users = db.query(User).filter(User.is_active.is_(True)).all()
    by_tokens: dict[tuple, list[User]] = {}
    for u in users:
        for candidate in (u.display_name or "", u.email.split("@")[0].replace(".", " ") if u.email else ""):
            toks = _normalize_name_to_tokens(candidate)
            if toks:
                by_tokens.setdefault(toks, []).append(u)

    # Pull current unmapped providers (reuse list_provider_mappings logic)
    decided = {m.provider_name for m in db.query(ProviderUserMapping).all()}
    open_providers = set(
        row[0] for row in
        db.query(MissingCharge.primary_provider)
          .filter(MissingCharge.status.notin_(list(TERMINAL_STATUSES)),
                  MissingCharge.primary_provider.isnot(None))
          .distinct().all()
        if row[0]
    )
    candidates = sorted(open_providers - decided)

    by = current_user.get("email") or "system"
    results = []
    for prov in candidates:
        toks = _normalize_name_to_tokens(prov)
        if not toks:
            results.append({"provider_name": prov, "matched": False,
                             "reason": "unparseable (placeholder?)"})
            continue
        users_matched = by_tokens.get(toks) or []
        # Deduplicate by email (a user can be indexed via display_name AND email-local)
        unique = {u.email: u for u in users_matched}
        if len(unique) == 1:
            u = next(iter(unique.values()))
            m = ProviderUserMapping(
                provider_name=prov,
                user_email=u.email,
                is_active="Y",
                is_ignored="N",
                created_by=by,
            )
            db.add(m)
            results.append({"provider_name": prov, "matched": True,
                             "user_email": u.email,
                             "user_name": u.display_name})
        elif len(unique) > 1:
            results.append({"provider_name": prov, "matched": False,
                             "reason": f"ambiguous — {len(unique)} candidates: "
                                       + ", ".join(sorted(unique.keys()))})
        else:
            results.append({"provider_name": prov, "matched": False,
                             "reason": "no workforce match"})
    db.commit()
    return {
        "candidates": len(candidates),
        "matched":    sum(1 for r in results if r["matched"]),
        "unmatched":  sum(1 for r in results if not r["matched"]),
        "results":    results,
    }


class MappingIn(BaseModel):
    provider_name: str
    user_email:    Optional[str] = None
    is_active:     Optional[bool] = True
    is_ignored:    Optional[bool] = False


@router.post("/provider-mappings", status_code=201)
def create_provider_mapping(payload: MappingIn,
                              db: Session = Depends(get_db),
                              current_user: dict = Depends(requires_tier(Module.MISSING_CHARGES, Tier.WORK))):
    name = payload.provider_name.strip()
    email = (payload.user_email or "").strip().lower()
    ignored = bool(payload.is_ignored)
    if not name:
        raise HTTPException(status_code=422, detail="provider_name required")
    if not ignored and not email:
        raise HTTPException(status_code=422,
                            detail="user_email required unless is_ignored=true")
    existing = (db.query(ProviderUserMapping)
                  .filter(ProviderUserMapping.provider_name == name).first())
    if existing:
        raise HTTPException(status_code=409,
                            detail=f"mapping for {name!r} already exists")
    m = ProviderUserMapping(
        provider_name=name,
        user_email=email or None,
        is_active="Y" if payload.is_active is not False else "N",
        is_ignored="Y" if ignored else "N",
        created_by=current_user.get("email") or "system",
    )
    db.add(m); db.commit(); db.refresh(m)
    return _mapping_dict(m)


class MappingPatch(BaseModel):
    user_email: Optional[str] = None
    is_active:  Optional[bool] = None
    is_ignored: Optional[bool] = None


@router.patch("/provider-mappings/{mapping_id}")
def patch_provider_mapping(mapping_id: str, payload: MappingPatch,
                             db: Session = Depends(get_db),
                             current_user: dict = Depends(requires_tier(Module.MISSING_CHARGES, Tier.WORK))):
    m = db.query(ProviderUserMapping).filter(ProviderUserMapping.id == mapping_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="mapping not found")
    data = payload.model_dump(exclude_unset=True)
    if "is_ignored" in data:
        m.is_ignored = "Y" if data["is_ignored"] else "N"
    if "user_email" in data:
        v = (data["user_email"] or "").strip().lower()
        m.user_email = v or None
    if "is_active" in data:
        m.is_active = "Y" if data["is_active"] else "N"
    # Validate the final state — a row must either have an email OR be ignored
    if m.is_ignored != "Y" and not (m.user_email or "").strip():
        raise HTTPException(status_code=422,
                            detail="user_email required unless is_ignored=true")
    db.commit(); db.refresh(m)
    return _mapping_dict(m)


@router.delete("/provider-mappings/{mapping_id}", status_code=204)
def delete_provider_mapping(mapping_id: str,
                              db: Session = Depends(get_db),
                              current_user: dict = Depends(requires_tier(Module.MISSING_CHARGES, Tier.MANAGE))):
    m = db.query(ProviderUserMapping).filter(ProviderUserMapping.id == mapping_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="mapping not found")
    db.delete(m); db.commit()
    return


# Route-order fix: FastAPI matches routes in declaration order. The
# `GET /{charge_id}` route was declared early in this file and would
# shadow later static paths like `/provider-mappings` (matching it as
# a charge_id and returning a spurious 404 "missing charge not found").
# Re-sort so every dynamic `{charge_id}` route is pushed to the end.
def _has_charge_id_param(route) -> bool:
    return "{charge_id}" in getattr(route, "path", "")

router.routes[:] = (
    [r for r in router.routes if not _has_charge_id_param(r)]
    + [r for r in router.routes if _has_charge_id_param(r)]
)
