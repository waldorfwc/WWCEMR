"""Active AR API — work queue + payment posting + summary endpoints.

The active AR module is intentionally decoupled from the legacy claim/payment
tables. Linkage to the existing `patients` table is by `patient_external_id`
(= chart number) for chart-context lookups only.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, date, timedelta
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import desc, or_, func
from sqlalchemy.orm import Session, joinedload

from app.config import settings
from app.database import get_db
from app.models.active_ar import (
    ActiveClaim, ActiveClaimNote, InsurancePayment, PaymentAllocation,
    ActiveClaimDocument,
)
from app.models.appeal_letters import AppealLetter
from app.models.patient import Patient
from app.models.patient_directory import IntakeDocument
from app.models.user import User
from app.routers.auth import get_current_user
from app.permissions.catalog import Module, Tier
from app.permissions.dependencies import requires_tier
from app.services.active_ar_importer import import_unpaid_claims
from app.services.audit_service import log_action
from app.services.appeal_letter_pdf import render_pdf
from app.services.storage import save_blob, serve_blob, is_legacy_local_path
from app.services.timely_filing import timely_filing_info

router = APIRouter(prefix="/active-ar", tags=["active-ar"])


# ---------- pydantic schemas ----------

class NoteCreate(BaseModel):
    action_type: str = "note"
    note: str


class PaymentCreate(BaseModel):
    check_number: Optional[str] = None
    check_date: Optional[date] = None
    payer_name: str
    # gt=0 and an explicit ceiling that matches Numeric(10,2) on the column —
    # rejects NaN/Inf cleanly and catches column overflow before SQLAlchemy.
    total_amount: Decimal = Field(gt=0, le=99_999_999.99)
    payment_method: Optional[str] = "Check"
    notes: Optional[str] = None
    allocations: List["AllocationItem"] = []


class AllocationItem(BaseModel):
    active_claim_id: str
    amount_applied: Decimal = Field(gt=0, le=99_999_999.99)
    allocation_note: Optional[str] = None


PaymentCreate.model_rebuild()


# Bounded money for AR write payloads — rejects NaN/Inf/negatives at
# the API boundary so a typed `"NaN"` or `-100` can't land in
# Numeric(10,2) and poison Claim.balance > 0 filters or AR summary.
# (Fable cross-cutting audit #8.)
ArMoney = Decimal


class StatusUpdate(BaseModel):
    workflow_state: Optional[str] = None
    assigned_to: Optional[str] = None
    written_off_amount: Optional[ArMoney] = Field(
        default=None, ge=0, le=99_999_999.99)
    written_off_reason: Optional[str] = None


class EobDetailsUpdate(BaseModel):
    """Manually entered EOB fields. Any field omitted/null is preserved."""
    allowed_amount: Optional[ArMoney] = Field(default=None, ge=0, le=99_999_999.99)
    contractual_adjustment: Optional[ArMoney] = Field(default=None, ge=0, le=99_999_999.99)
    copay: Optional[ArMoney] = Field(default=None, ge=0, le=99_999_999.99)
    deductible: Optional[ArMoney] = Field(default=None, ge=0, le=99_999_999.99)
    coinsurance: Optional[ArMoney] = Field(default=None, ge=0, le=99_999_999.99)
    patient_balance: Optional[ArMoney] = Field(default=None, ge=0, le=99_999_999.99)
    eob_notes: Optional[str] = None


# ---------- helpers ----------

def _claim_to_dict(c: ActiveClaim, patient: Optional[Patient] = None,
                    latest_note: Optional[dict] = None) -> dict:
    age_days = (date.today() - c.dos).days if c.dos else None
    tf = timely_filing_info(c.insurance_company, c.dos)
    # Parse service-lines JSON if present
    service_lines = []
    if c.service_lines_json:
        try:
            import json as _json
            service_lines = _json.loads(c.service_lines_json)
        except Exception:
            service_lines = []
    # Denial summary — aggregates adjustment codes across lines and tags
    # appealable ones with issue + resolution + suggested template
    from app.services.denial_classifier import summarize_claim_denials
    denial_summary = summarize_claim_denials(service_lines)
    return {
        "tf_days_allowed":        tf["tf_days_allowed"],
        "tf_deadline_date":       str(tf["tf_deadline_date"]) if tf["tf_deadline_date"] else None,
        "days_until_tf_deadline": tf["days_until_tf_deadline"],
        "tf_status":              tf["tf_status"],
        "id": str(c.id),
        "claim_number": c.claim_number,
        "patient_external_id": c.patient_external_id,
        "patient_name": c.patient_name,
        "patient_id": str(patient.id) if patient else None,  # internal Patient.id for chart link
        "patient_dob": str(patient.date_of_birth) if patient and patient.date_of_birth else None,
        "dos": str(c.dos) if c.dos else None,
        "age_days": age_days,
        "care_provider": c.care_provider,
        "claim_state": c.claim_state,
        "claim_status": c.claim_status,
        "claim_amount": float(c.claim_amount or 0),
        "line_balance": float(c.line_balance or 0),
        "insurance_balance": float(c.insurance_balance or 0),
        "total_charges": float(c.total_charges or 0),
        # EOB-derived fields (null = unknown; entered manually or via 835)
        "allowed_amount":          float(c.allowed_amount) if c.allowed_amount is not None else None,
        "contractual_adjustment":  float(c.contractual_adjustment) if c.contractual_adjustment is not None else None,
        "copay":                   float(c.copay) if c.copay is not None else None,
        "deductible":              float(c.deductible) if c.deductible is not None else None,
        "coinsurance":             float(c.coinsurance) if c.coinsurance is not None else None,
        "patient_balance":         float(c.patient_balance) if c.patient_balance is not None else None,
        "eob_notes":               c.eob_notes,
        "insurance_priority": c.insurance_priority,
        "payor_id": c.payor_id,
        "insurance_company": c.insurance_company,
        "plan_name": c.plan_name,
        "policy_number": c.policy_number,
        "practice_location": c.practice_location,
        "workflow_state": c.workflow_state,
        "assigned_to": c.assigned_to,
        "paid_amount": float(c.paid_amount or 0),
        "paid_in_full_at": str(c.paid_in_full_at) if c.paid_in_full_at else None,
        "last_status_check_at": str(c.last_status_check_at) if c.last_status_check_at else None,
        "imported_at": str(c.imported_at) if c.imported_at else None,
        "last_seen_in_export_at": str(c.last_seen_in_export_at) if c.last_seen_in_export_at else None,
        # Charge Analysis enrichment
        "procedure_codes":              c.procedure_codes,
        "procedure_modifiers":          c.procedure_modifiers,
        "diagnosis_codes":              c.diagnosis_codes,
        "billable_provider_npi":        c.billable_provider_npi,
        "rendering_provider_name_full": c.rendering_provider_name_full,
        "rendering_provider_npi":       c.rendering_provider_npi,
        "service_location":             c.service_location,
        "patient_dob":                  str(c.patient_dob) if c.patient_dob else None,
        "secondary_insurance_company":  c.secondary_insurance_company,
        "secondary_plan_name":          c.secondary_plan_name,
        "secondary_policy_number":      c.secondary_policy_number,
        "primary_plan_detail":          c.primary_plan_detail,
        "enriched_at":                  str(c.enriched_at) if c.enriched_at else None,
        "service_lines":                service_lines,
        "denial_summary":               denial_summary,
        # Latest person-written note (action_type='note' only — excludes
        # auto-generated audit entries like status_check, payment_applied)
        "latest_note":                  latest_note,
    }


def _note_to_dict(n: ActiveClaimNote) -> dict:
    return {
        "id": str(n.id),
        "user": n.user,
        "action_type": n.action_type,
        "note": n.note,
        "created_at": str(n.created_at),
    }


# ---------- endpoints ----------

@router.post("/upload")
async def upload_unpaid_claims(
    file: UploadFile = File(...),
    mark_missing_as_closed: bool = Query(
        False,
        description="If true, claims previously in the DB but not in this "
                    "new export are moved to workflow_state='closed'. Use "
                    "only when the export represents the full current AR.",
    ),
    db: Session = Depends(get_db),
    current_user: dict = Depends(
        requires_tier(Module.ACTIVE_AR, Tier.WORK)),
):
    # The mark_missing_as_closed flag is a mass-mutation switch that
    # bulk-closes every prior-existing AR claim not in the file. The
    # whole router is gated at Tier.VIEW; routine imports require
    # WORK; the destructive flag additionally requires MANAGE.
    # (Fable cross-cutting audit #5.)
    if mark_missing_as_closed:
        from app.permissions.resolver import effective_tier
        actor_email = (current_user.get("email") or "").lower().strip()
        if effective_tier(db, actor_email, Module.ACTIVE_AR) < Tier.MANAGE:
            raise HTTPException(
                status_code=403,
                detail="mark_missing_as_closed requires Tier.MANAGE on Active AR")
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in (".xls", ".xlsx"):
        raise HTTPException(status_code=422, detail="file must be .xls or .xlsx")

    subdir = os.path.join(settings.upload_dir, "active_ar")
    os.makedirs(subdir, exist_ok=True)
    upload_id = str(uuid.uuid4())
    save_path = os.path.join(subdir, f"{upload_id}{ext}")
    content = await file.read()
    with open(save_path, "wb") as fh:
        fh.write(content)

    try:
        result = import_unpaid_claims(
            db, save_path,
            posted_by=current_user.get("email"),
            mark_missing_as_closed=mark_missing_as_closed,
        )
    except Exception as exc:
        # Log the traceback server-side. Importer exceptions frequently
        # quote row content (patient names, policy numbers) in their
        # messages, which would land in this HTTP response and any
        # access log. (Fable cross-cutting audit #10.)
        import logging
        logging.getLogger(__name__).exception(
            "active_ar upload failed (upload_id=%s)", upload_id)
        raise HTTPException(
            status_code=422,
            detail=(f"Import failed. Reference upload_id={upload_id} when "
                    "asking IT to look at the server log."))

    return {
        "filename": file.filename,
        "total_rows": result.total_rows,
        "new_claims": result.new_claims,
        "updated_claims": result.updated_claims,
        "closed_claims": result.closed_claims,
        "unchanged": result.unchanged,
        "errors": result.errors[:50],
        "error_count": len(result.errors),
    }


@router.get("/claims")
def list_active_claims(
    db: Session = Depends(get_db),
    search: Optional[str] = None,
    workflow_state: Optional[str] = None,
    payer: Optional[str] = None,
    plan: Optional[str] = None,
    insurance_priority: Optional[str] = None,
    age_bucket: Optional[str] = None,           # 0-30, 31-60, 61-90, 90+
    tf_status: Optional[str] = None,            # past | urgent | soon | safe
    assigned_to: Optional[str] = None,
    min_balance: Optional[float] = None,
    sort: str = "balance_desc",                 # age_desc | balance_desc | dos_desc | tf_asc
    include_aged: bool = False,                 # include claims with DOS > 2 years
    page: int = 1,
    per_page: int = Query(50, ge=1, le=200),    # cap at 200 — was unbounded
    current_user: dict = Depends(get_current_user),
):
    q = db.query(ActiveClaim)

    # workflow_state: caller can request any state explicitly; otherwise
    # default to the active-workload set (everything except closed
    # variants). The previous form rebuilt the query from scratch via
    # `q = db.query(ActiveClaim)...` inside the workflow_state branch,
    # which silently wiped the aged-claim filter and any earlier
    # predicate — behavior depended on parameter order in code, not
    # user intent. (Fable cross-cutting audit #1.)
    if workflow_state:
        q = q.filter(ActiveClaim.workflow_state == workflow_state)
    else:
        q = q.filter(ActiveClaim.workflow_state.notin_(
            ["paid", "rebilled_modmed", "written_off", "closed"]))

    # Hide claims with DOS > 2 years old by default — aged-out write-off
    # candidates that clutter the working queue. They remain searchable
    # via include_aged=true (e.g. when chasing a specific claim # or MRN).
    if not include_aged:
        two_years_ago = date.today() - timedelta(days=730)
        q = q.filter(or_(ActiveClaim.dos.is_(None), ActiveClaim.dos >= two_years_ago))
    if payer:
        q = q.filter(ActiveClaim.insurance_company.ilike(f"%{payer}%"))
    if plan:
        q = q.filter(ActiveClaim.plan_name.ilike(f"%{plan}%"))
    if insurance_priority:
        q = q.filter(ActiveClaim.insurance_priority == insurance_priority)
    if assigned_to:
        q = q.filter(ActiveClaim.assigned_to == assigned_to)
    if min_balance is not None:
        q = q.filter(ActiveClaim.insurance_balance >= min_balance)
    if search:
        s = f"%{search}%"
        q = q.filter(or_(
            ActiveClaim.claim_number.ilike(s),
            ActiveClaim.patient_name.ilike(s),
            ActiveClaim.patient_external_id.ilike(s),
            ActiveClaim.policy_number.ilike(s),
        ))

    today = date.today()
    if age_bucket:
        bounds = {
            "0-30":  (today - timedelta(days=30),  None),
            "31-60": (today - timedelta(days=60),  today - timedelta(days=31)),
            "61-90": (today - timedelta(days=90),  today - timedelta(days=61)),
            "90+":   (None,                        today - timedelta(days=91)),
        }.get(age_bucket)
        if bounds:
            lo, hi = bounds
            if lo is not None:
                q = q.filter(ActiveClaim.dos >= lo)
            if hi is not None:
                q = q.filter(ActiveClaim.dos <= hi)

    order_map = {
        "balance_desc": desc(ActiveClaim.insurance_balance),
        "age_desc":     ActiveClaim.dos.asc().nullslast(),  # oldest DOS = highest age
        "dos_desc":     desc(ActiveClaim.dos),
    }

    # tf_status filter and tf_asc sort require per-claim TF computation —
    # we can't push them to SQL cleanly. Fall back to in-memory filter+sort
    # when either is requested. Otherwise stay on SQL pagination (fast).
    needs_tf_pass = bool(tf_status) or sort == "tf_asc"
    if needs_tf_pass:
        all_rows = q.all()
        if tf_status:
            all_rows = [r for r in all_rows
                        if timely_filing_info(r.insurance_company, r.dos)["tf_status"] == tf_status]
        if sort == "tf_asc":
            all_rows.sort(key=lambda r: (
                timely_filing_info(r.insurance_company, r.dos)["days_until_tf_deadline"]
                if r.dos else 99999
            ))
        else:
            # Apply default order on the in-memory list (balance desc)
            all_rows.sort(key=lambda r: -(float(r.insurance_balance or 0)))
        total = len(all_rows)
        rows = all_rows[(page - 1) * per_page : page * per_page]
    else:
        q = q.order_by(order_map.get(sort, desc(ActiveClaim.insurance_balance)))
        total = q.count()
        rows = q.offset((page - 1) * per_page).limit(per_page).all()

    # Bulk-load corresponding patients for chart linkage
    pat_ids = {r.patient_external_id for r in rows if r.patient_external_id}
    pats = {p.patient_id: p for p in db.query(Patient).filter(Patient.patient_id.in_(pat_ids)).all()}

    # Bulk-load the latest person-written note per claim (action_type='note'
    # only — excludes auto-generated audit entries). One query, no N+1.
    claim_ids = [r.id for r in rows]
    latest_notes: dict = {}
    if claim_ids:
        from sqlalchemy import func
        # Get max(created_at) per claim_id first, then join back for the row
        latest_per_claim = (
            db.query(ActiveClaimNote.active_claim_id,
                     func.max(ActiveClaimNote.created_at).label("max_at"))
              .filter(ActiveClaimNote.active_claim_id.in_(claim_ids),
                      ActiveClaimNote.action_type == "note",
                      ActiveClaimNote.note.isnot(None))
              .group_by(ActiveClaimNote.active_claim_id)
              .subquery()
        )
        note_rows = (
            db.query(ActiveClaimNote)
              .join(latest_per_claim,
                    (ActiveClaimNote.active_claim_id == latest_per_claim.c.active_claim_id)
                    & (ActiveClaimNote.created_at == latest_per_claim.c.max_at))
              .all()
        )
        for n in note_rows:
            if (n.note or "").strip():
                latest_notes[n.active_claim_id] = {
                    "user": n.user,
                    "note": n.note,
                    "created_at": str(n.created_at),
                }

    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "claims": [
            _claim_to_dict(c, pats.get(c.patient_external_id),
                           latest_note=latest_notes.get(c.id))
            for c in rows
        ],
    }


@router.get("/summary")
def active_ar_summary(db: Session = Depends(get_db),
                      current_user: dict = Depends(get_current_user)):
    today = date.today()
    base = db.query(ActiveClaim).filter(
        ActiveClaim.workflow_state.notin_(["paid", "rebilled_modmed", "written_off", "closed"])
    )

    open_count = base.count()
    total_balance = float(base.with_entities(func.sum(ActiveClaim.insurance_balance)).scalar() or 0)

    # Age buckets
    buckets = []
    for label, lo_days, hi_days in [
        ("0-30", 0, 30), ("31-60", 31, 60), ("61-90", 61, 90), ("90+", 91, None),
    ]:
        sub = base
        if hi_days is not None:
            sub = sub.filter(ActiveClaim.dos >= today - timedelta(days=hi_days))
        if lo_days > 0:
            sub = sub.filter(ActiveClaim.dos <= today - timedelta(days=lo_days))
        cnt = sub.count()
        bal = float(sub.with_entities(func.sum(ActiveClaim.insurance_balance)).scalar() or 0)
        buckets.append({"bucket": label, "count": cnt, "balance": bal})

    # Top payers
    payer_rows = (
        base.with_entities(
            ActiveClaim.insurance_company, func.count(ActiveClaim.id),
            func.sum(ActiveClaim.insurance_balance),
        ).group_by(ActiveClaim.insurance_company)
        .order_by(desc(func.sum(ActiveClaim.insurance_balance)))
        .limit(15).all()
    )
    top_payers = [
        {"payer": r[0] or "—", "count": r[1], "balance": float(r[2] or 0)}
        for r in payer_rows
    ]

    # Workflow state breakdown
    state_rows = (
        base.with_entities(ActiveClaim.workflow_state, func.count(ActiveClaim.id))
        .group_by(ActiveClaim.workflow_state).all()
    )
    by_workflow = {r[0]: r[1] for r in state_rows}

    # By insurance priority
    pri_rows = (
        base.with_entities(ActiveClaim.insurance_priority, func.count(ActiveClaim.id),
                           func.sum(ActiveClaim.insurance_balance))
        .group_by(ActiveClaim.insurance_priority).all()
    )
    by_priority = [
        {"priority": r[0], "count": r[1], "balance": float(r[2] or 0)}
        for r in pri_rows
    ]

    # Top distinct plans
    plan_rows = (
        base.with_entities(
            ActiveClaim.plan_name, func.count(ActiveClaim.id),
            func.sum(ActiveClaim.insurance_balance),
        ).filter(ActiveClaim.plan_name.isnot(None))
        .group_by(ActiveClaim.plan_name)
        .order_by(desc(func.sum(ActiveClaim.insurance_balance)))
        .limit(50).all()
    )
    top_plans = [
        {"plan": r[0], "count": r[1], "balance": float(r[2] or 0)}
        for r in plan_rows
    ]

    # Timely-filing buckets (per-payer rules) — computed in Python
    tf_buckets = {"past": {"count": 0, "balance": 0.0},
                  "urgent": {"count": 0, "balance": 0.0},
                  "soon":   {"count": 0, "balance": 0.0},
                  "safe":   {"count": 0, "balance": 0.0}}
    for c in base.all():
        info = timely_filing_info(c.insurance_company, c.dos)
        s = info["tf_status"]
        if s in tf_buckets:
            tf_buckets[s]["count"] += 1
            tf_buckets[s]["balance"] += float(c.insurance_balance or 0)

    return {
        "open_count": open_count,
        "total_balance": total_balance,
        "age_buckets": buckets,
        "top_payers": top_payers,
        "top_plans": top_plans,
        "tf_buckets": tf_buckets,
        "by_workflow_state": by_workflow,
        "by_priority": by_priority,
    }


@router.get("/claims/{claim_id}")
def get_active_claim(claim_id: str, db: Session = Depends(get_db),
                     current_user: dict = Depends(get_current_user)):
    c = db.query(ActiveClaim).options(
        joinedload(ActiveClaim.notes), joinedload(ActiveClaim.allocations),
    ).filter(ActiveClaim.id == claim_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="claim not found")

    pat = db.query(Patient).filter(Patient.patient_id == c.patient_external_id).first()

    # PHI access logging (Fable cross-cutting audit #9). Returns DOB,
    # policy numbers, diagnoses, link to insurance card images, etc.
    actor = current_user.get("email")
    log_action(
        db, action="ACTIVE_CLAIM_VIEW",
        resource_type="active_claim", resource_id=str(c.id),
        patient_id=c.patient_external_id or None,
        user_id=(actor or "").lower() or None, user_name=actor,
        description=f"Viewed active claim {c.claim_number or c.id}",
    )

    return {
        **_claim_to_dict(c, pat),
        "notes": [_note_to_dict(n) for n in c.notes],
        "allocations": [
            {
                "id": str(a.id), "amount_applied": float(a.amount_applied or 0),
                "allocation_note": a.allocation_note,
                "payment_id": str(a.payment_id),
                "check_number": a.payment.check_number if a.payment else None,
                "check_date": str(a.payment.check_date) if a.payment and a.payment.check_date else None,
                "payer_name": a.payment.payer_name if a.payment else None,
                "created_at": str(a.created_at),
            }
            for a in c.allocations
        ],
    }


@router.post("/claims/{claim_id}/notes")
def add_note(claim_id: str, payload: NoteCreate,
             db: Session = Depends(get_db),
             current_user: dict = Depends(
                 requires_tier(Module.ACTIVE_AR, Tier.WORK))):
    c = db.query(ActiveClaim).filter(ActiveClaim.id == claim_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="claim not found")
    n = ActiveClaimNote(
        active_claim_id=c.id, user=current_user.get("email"),
        action_type=payload.action_type, note=payload.note,
    )
    db.add(n); db.commit(); db.refresh(n)
    return _note_to_dict(n)


@router.patch("/claims/{claim_id}")
def update_claim_status(claim_id: str, payload: StatusUpdate,
                        db: Session = Depends(get_db),
                        current_user: dict = Depends(
                            requires_tier(Module.ACTIVE_AR, Tier.WORK))):
    c = db.query(ActiveClaim).filter(ActiveClaim.id == claim_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="claim not found")

    user = current_user.get("email")
    changes = []
    if payload.workflow_state and payload.workflow_state != c.workflow_state:
        # Write-offs are gated higher than other edits — Active AR:Work can
        # change most states, but only Active AR:Manage can send a claim
        # into written_off.
        if payload.workflow_state == "written_off":
            from app.permissions.catalog import Module, Tier
            from app.permissions.resolver import effective_tier
            if effective_tier(db, user, Module.ACTIVE_AR) < Tier.MANAGE:
                raise HTTPException(status_code=403,
                                    detail="forbidden — needs Active AR:Manage to write off a claim")
        changes.append(f"workflow_state {c.workflow_state} → {payload.workflow_state}")
        c.workflow_state = payload.workflow_state
        if payload.workflow_state == "written_off":
            c.written_off_at = datetime.utcnow()
            if payload.written_off_amount is not None:
                c.written_off_amount = payload.written_off_amount
            if payload.written_off_reason is not None:
                c.written_off_reason = payload.written_off_reason
    if payload.assigned_to is not None and payload.assigned_to != c.assigned_to:
        changes.append(f"assigned_to {c.assigned_to} → {payload.assigned_to}")
        c.assigned_to = payload.assigned_to

    if changes:
        db.add(ActiveClaimNote(
            active_claim_id=c.id, user=user, action_type="status_changed",
            note="; ".join(changes),
        ))
    db.commit()
    pat = db.query(Patient).filter(Patient.patient_id == c.patient_external_id).first()
    return _claim_to_dict(c, pat)


@router.post("/payments")
def post_insurance_payment(payload: PaymentCreate,
                           db: Session = Depends(get_db),
                           current_user: dict = Depends(get_current_user),
                           _perm: dict = Depends(requires_tier(Module.ACTIVE_AR, Tier.WORK))):
    """Post a payer check/EFT and allocate to one or more claims."""
    if not payload.allocations:
        raise HTTPException(status_code=422, detail="at least one allocation required")

    alloc_total = sum((a.amount_applied for a in payload.allocations), Decimal("0"))
    if alloc_total > payload.total_amount:
        raise HTTPException(
            status_code=422,
            detail=f"allocations (${alloc_total}) exceed payment total (${payload.total_amount})",
        )

    user = current_user.get("email")
    pmt = InsurancePayment(
        check_number=payload.check_number,
        check_date=payload.check_date,
        payer_name=payload.payer_name,
        total_amount=payload.total_amount,
        payment_method=payload.payment_method,
        notes=payload.notes,
        posted_by=user,
    )
    db.add(pmt); db.flush()

    affected = []
    for a in payload.allocations:
        # SELECT ... FOR UPDATE on the claim row so two concurrent
        # /payments postings against the same claim can't both read
        # the same starting paid_amount/insurance_balance and both
        # commit the increment — silently losing one payment.
        # (Fable cross-cutting audit #2.)
        c = (db.query(ActiveClaim)
                .filter(ActiveClaim.id == a.active_claim_id)
                .with_for_update()
                .first())
        if not c:
            db.rollback()
            raise HTTPException(status_code=404,
                                detail=f"claim {a.active_claim_id} not found")
        alloc = PaymentAllocation(
            payment_id=pmt.id, active_claim_id=c.id,
            amount_applied=a.amount_applied,
            allocation_note=a.allocation_note,
        )
        db.add(alloc)
        # Update claim balance
        c.paid_amount = (c.paid_amount or Decimal(0)) + a.amount_applied
        c.insurance_balance = (c.insurance_balance or Decimal(0)) - a.amount_applied
        if (c.insurance_balance or Decimal(0)) <= Decimal("0.01"):
            c.workflow_state = "paid"
            c.paid_in_full_at = datetime.utcnow()
        db.add(ActiveClaimNote(
            active_claim_id=c.id, user=user,
            action_type="payment_applied",
            note=f"${a.amount_applied} applied from "
                 f"{payload.payer_name} check #{payload.check_number or '—'}",
        ))
        affected.append(str(c.id))

    db.commit()
    log_action(
        db,
        action="PAYMENT_POSTED",
        resource_type="insurance_payment",
        resource_id=str(pmt.id),
        user_id=(user or "").lower() or None,
        user_name=user,
        description=(f"Posted {payload.payer_name} check "
                     f"#{payload.check_number or '—'} ${payload.total_amount}, "
                     f"allocated to {len(affected)} claim(s)"),
    )
    return {
        "payment_id": str(pmt.id),
        "total_amount": float(pmt.total_amount),
        "allocated_to": affected,
        "unallocated": float(pmt.total_amount) - float(alloc_total),
    }


@router.get("/payments")
def list_payments(db: Session = Depends(get_db),
                  page: int = 1, per_page: int = 50,
                  current_user: dict = Depends(get_current_user)):
    q = db.query(InsurancePayment).order_by(desc(InsurancePayment.posted_at))
    total = q.count()
    rows = q.offset((page - 1) * per_page).limit(per_page).all()
    return {
        "total": total,
        "payments": [
            {
                "id": str(p.id), "check_number": p.check_number,
                "check_date": str(p.check_date) if p.check_date else None,
                "payer_name": p.payer_name,
                "total_amount": float(p.total_amount or 0),
                "payment_method": p.payment_method,
                "allocated": p.allocated_total,
                "unallocated": p.unallocated,
                "allocation_count": len(p.allocations),
                "posted_at": str(p.posted_at), "posted_by": p.posted_by,
            }
            for p in rows
        ],
    }


# ---------- EOB details ----------

@router.patch("/claims/{claim_id}/eob-details")
def update_eob_details(claim_id: str, payload: EobDetailsUpdate,
                       db: Session = Depends(get_db),
                       current_user: dict = Depends(get_current_user),
                       _perm: dict = Depends(requires_tier(Module.ACTIVE_AR, Tier.WORK))):
    """Update the manually-entered EOB fields. Any field passed as null is
    treated as 'no change' — pass an explicit 0 to clear a value."""
    c = db.query(ActiveClaim).filter(ActiveClaim.id == claim_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="claim not found")

    user = current_user.get("email")
    changes = []
    for field in ("allowed_amount", "contractual_adjustment", "copay",
                  "deductible", "coinsurance", "patient_balance", "eob_notes"):
        new_val = getattr(payload, field)
        if new_val is None:
            continue
        cur_val = getattr(c, field)
        if cur_val != new_val:
            setattr(c, field, new_val)
            changes.append(f"{field}: {cur_val} → {new_val}")

    if changes:
        db.add(ActiveClaimNote(
            active_claim_id=c.id, user=user, action_type="eob_updated",
            note="EOB details updated:\n" + "\n".join(changes),
        ))
    db.commit()
    db.refresh(c)
    pat = db.query(Patient).filter(Patient.patient_id == c.patient_external_id).first()
    return _claim_to_dict(c, pat)


# ---------- Related claims (same patient + DOS) ----------

@router.get("/claims/{claim_id}/related")
def related_claims(claim_id: str, db: Session = Depends(get_db),
                   current_user: dict = Depends(get_current_user)):
    """Other active claims for the same patient on the same DOS — typically
    the secondary/tertiary on a primary, or multiple separate claims billed
    on the same visit."""
    c = db.query(ActiveClaim).filter(ActiveClaim.id == claim_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="claim not found")

    q = db.query(ActiveClaim).filter(
        ActiveClaim.patient_external_id == c.patient_external_id,
        ActiveClaim.dos == c.dos,
        ActiveClaim.id != c.id,
    ).order_by(ActiveClaim.insurance_priority, ActiveClaim.claim_number)

    return {"related": [_claim_to_dict(r) for r in q.all()]}


@router.get("/claims/by-dos")
def claims_by_dos(
    db: Session = Depends(get_db),
    workflow_state: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    """Group active claims by (patient + DOS). Returns one entry per DOS
    with all claims listed inside, plus DOS-level totals. Useful when a
    patient has primary + secondary still pending and you want to see the
    full picture at the visit level."""
    q = db.query(ActiveClaim)
    if workflow_state:
        q = q.filter(ActiveClaim.workflow_state == workflow_state)
    else:
        q = q.filter(ActiveClaim.workflow_state.notin_(
            ["paid", "rebilled_modmed", "written_off", "closed"]
        ))

    rows = q.order_by(ActiveClaim.dos.desc()).all()

    groups: dict[tuple, dict] = {}
    for c in rows:
        key = (c.patient_external_id, str(c.dos) if c.dos else None)
        if key not in groups:
            groups[key] = {
                "patient_external_id": c.patient_external_id,
                "patient_name": c.patient_name,
                "dos": str(c.dos) if c.dos else None,
                "total_billed": 0.0,
                "total_balance": 0.0,
                "claims": [],
            }
        g = groups[key]
        g["claims"].append(_claim_to_dict(c))
        g["total_billed"] += float(c.claim_amount or 0)
        g["total_balance"] += float(c.insurance_balance or 0)

    out = sorted(groups.values(), key=lambda x: (x["dos"] or ""), reverse=True)
    return {"groups": out, "group_count": len(out)}


# ---------- Documents ----------

ALLOWED_DOC_EXT = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".gif",
                   ".webp", ".heic", ".doc", ".docx", ".txt", ".rtf"}
MAX_DOC_SIZE_BYTES = 25 * 1024 * 1024  # 25 MB


@router.post("/claims/{claim_id}/documents")
async def upload_document(
    claim_id: str,
    file: UploadFile = File(...),
    document_type: str = Query("Other"),
    description: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: dict = Depends(
        requires_tier(Module.ACTIVE_AR, Tier.WORK)),
):
    c = db.query(ActiveClaim).filter(ActiveClaim.id == claim_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="claim not found")

    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_DOC_EXT:
        raise HTTPException(
            status_code=422,
            detail=f"unsupported file type {ext}. Allowed: {sorted(ALLOWED_DOC_EXT)}",
        )

    content = await file.read()
    if len(content) > MAX_DOC_SIZE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"file exceeds {MAX_DOC_SIZE_BYTES // 1024 // 1024} MB limit",
        )

    safe_name = (file.filename or "upload").replace("/", "_").replace("\\", "_")
    key = save_blob(prefix="active-ar-docs", body=content, filename=safe_name)

    doc = ActiveClaimDocument(
        active_claim_id=c.id,
        document_type=document_type,
        filename=safe_name,
        content_type=file.content_type,
        file_size=len(content),
        file_path=key,
        description=description,
        uploaded_by=current_user.get("email"),
    )
    db.add(doc)
    db.add(ActiveClaimNote(
        active_claim_id=c.id, user=current_user.get("email"),
        action_type="document_uploaded",
        note=f"{document_type}: {safe_name}" + (f" — {description}" if description else ""),
    ))
    db.commit(); db.refresh(doc)
    return _doc_to_dict(doc)


@router.get("/claims/{claim_id}/documents")
def list_documents(claim_id: str, db: Session = Depends(get_db),
                   current_user: dict = Depends(get_current_user)):
    c = db.query(ActiveClaim).filter(ActiveClaim.id == claim_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="claim not found")
    return {"documents": [_doc_to_dict(d) for d in c.documents]}


@router.get("/claims/{claim_id}/id-insurance-cards")
def list_id_insurance_cards(claim_id: str,
                            db: Session = Depends(get_db),
                            current_user: dict = Depends(get_current_user)):
    """Patient's ID + Insurance card images, pulled from intake documents
    and matched by chart number. Used by the Active AR detail page to show
    thumbnails for quick verification when posting payments / settling lines.
    """
    c = db.query(ActiveClaim).filter(ActiveClaim.id == claim_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="claim not found")
    chart = c.patient_external_id
    if not chart:
        return {"chart_number": None, "documents": []}

    # PHI access logging — ID + insurance card images are the most
    # access-sensitive PHI we serve. Audit who looks at whose cards.
    # (Fable cross-cutting audit #9.)
    actor = current_user.get("email")
    log_action(
        db, action="ID_INSURANCE_CARDS_LISTED",
        resource_type="active_claim", resource_id=str(c.id),
        patient_id=chart,
        user_id=(actor or "").lower() or None, user_name=actor,
        description=f"Listed ID/insurance cards for claim {c.claim_number or c.id}",
    )

    cat_lower = func.lower(IntakeDocument.doc_category)
    type_lower = func.lower(IntakeDocument.file_type)
    docs = (
        db.query(IntakeDocument)
        .filter(
            IntakeDocument.matched_chart_number == chart,
            or_(
                cat_lower.contains("ins"),       # ID&Insurance, Insurance Card, Ins, etc.
                cat_lower.contains("id card"),   # bare "ID Card 2025" categories
            ),
            # Only show formats the browser can render inline — PDF + photo.
            # DOCX and other intake formats are excluded.
            type_lower.in_(["pdf", "jpg", "jpeg"]),
        )
        .order_by(IntakeDocument.doc_year.desc().nullslast(),
                  IntakeDocument.indexed_at.desc())
        .all()
    )
    return {
        "chart_number": chart,
        "documents": [
            {
                "id": str(d.id),
                "filename": d.filename,
                "doc_category": d.doc_category,
                "file_type": (d.file_type or "").lower(),
                "doc_year": d.doc_year,
                "indexed_at": d.indexed_at.isoformat() if d.indexed_at else None,
                # Inline-served URL — works directly in <img> and <iframe>
                "view_url": f"/api/intake/view/{d.id}",
                "download_url": f"/api/intake/download/{d.id}",
            }
            for d in docs
        ],
    }


@router.get("/claims/{claim_id}/documents/{doc_id}/download")
def download_document(claim_id: str, doc_id: str,
                      db: Session = Depends(get_db),
                      current_user: dict = Depends(get_current_user)):
    doc = db.query(ActiveClaimDocument).filter(
        ActiveClaimDocument.id == doc_id,
        ActiveClaimDocument.active_claim_id == claim_id,
    ).first()
    if not doc:
        raise HTTPException(status_code=404, detail="document not found")
    if is_legacy_local_path(doc.file_path):
        raise HTTPException(status_code=410,
                            detail="This file is from before the cloud migration and is no longer available.")
    # PHI document download audit (Fable cross-cutting #9).
    c = db.query(ActiveClaim).filter(ActiveClaim.id == claim_id).first()
    actor = current_user.get("email")
    log_action(
        db, action="ACTIVE_CLAIM_DOC_DOWNLOAD",
        resource_type="active_claim_document", resource_id=str(doc.id),
        patient_id=(c.patient_external_id if c else None),
        user_id=(actor or "").lower() or None, user_name=actor,
        description=(f"Downloaded {doc.filename} on active claim "
                     f"{(c.claim_number if c else claim_id)}"),
    )
    return serve_blob(
        local_path=None,
        gcs_object=doc.file_path,
        media_type=doc.content_type or "application/octet-stream",
        filename=doc.filename,
    )


@router.delete("/claims/{claim_id}/documents/{doc_id}")
def delete_document(claim_id: str, doc_id: str,
                    db: Session = Depends(get_db),
                    current_user: dict = Depends(
                        requires_tier(Module.ACTIVE_AR, Tier.WORK))):
    doc = db.query(ActiveClaimDocument).filter(
        ActiveClaimDocument.id == doc_id,
        ActiveClaimDocument.active_claim_id == claim_id,
    ).first()
    if not doc:
        raise HTTPException(status_code=404, detail="document not found")
    # No filesystem cleanup — GCS orphans are cheap and the audit trail
    # is preserved.
    fname = doc.filename
    db.delete(doc)
    db.add(ActiveClaimNote(
        active_claim_id=doc.active_claim_id,
        user=current_user.get("email"),
        action_type="document_deleted",
        note=f"Deleted: {fname}",
    ))
    db.commit()
    return {"deleted": True}


def _doc_to_dict(d: ActiveClaimDocument) -> dict:
    return {
        "id": str(d.id),
        "document_type": d.document_type,
        "filename": d.filename,
        "content_type": d.content_type,
        "file_size": d.file_size,
        "description": d.description,
        "uploaded_by": d.uploaded_by,
        "uploaded_at": str(d.uploaded_at),
        "download_url": f"/api/active-ar/claims/{d.active_claim_id}/documents/{d.id}/download",
    }


# ---------- Assignees ----------

@router.get("/assignees")
def list_assignees(db: Session = Depends(get_db),
                   current_user: dict = Depends(get_current_user)):
    """Users who can have claims assigned to them — anyone with Active AR:View
    or higher. Plus any historical assignees already attached to claims (in
    case a user was deleted but their email still appears on past claims)."""
    from app.permissions.catalog import Module, Tier
    from app.permissions.resolver import effective_tier
    role_users = [u for u in db.query(User).all()
                  if effective_tier(db, u.email, Module.ACTIVE_AR) >= Tier.VIEW]
    assignees = {
        u.email: {
            "email": u.email,
            "display_name": u.display_name,
            "groups": sorted(g.name for g in u.groups),
        }
        for u in role_users if u.email
    }
    # Include historical assignees from existing claims so they remain
    # selectable in the picker even if the user record was removed.
    historical = (
        db.query(ActiveClaim.assigned_to)
        .filter(ActiveClaim.assigned_to.isnot(None))
        .distinct().all()
    )
    for (email,) in historical:
        if email and email not in assignees:
            assignees[email] = {"email": email, "display_name": None, "groups": []}

    out = sorted(assignees.values(), key=lambda u: (u["display_name"] or u["email"]).lower())
    return {"assignees": out}


# ---------- Waystar status sync ----------

@router.post("/claims/{claim_id}/sync-status")
def sync_claim_status(
    claim_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(
        requires_tier(Module.ACTIVE_AR, Tier.WORK)),
):
    """Query Waystar for this single claim's status, persist the response,
    log an activity entry, and auto-attach a matching ERA if found."""
    from app.services.active_ar_waystar_sync import sync_one
    c = db.query(ActiveClaim).filter(ActiveClaim.id == claim_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="claim not found")
    return sync_one(db, c, user_email=current_user.get("email"))


@router.post("/sync-status-batch")
def sync_status_batch(
    db: Session = Depends(get_db),
    workflow_state: Optional[str] = None,
    payer: Optional[str] = None,
    age_bucket: Optional[str] = None,
    only_unchecked: bool = Query(
        False,
        description="If true, only sync claims that have never been checked "
                    "or were checked > 24h ago.",
    ),
    max_count: int = Query(50, le=500),
    current_user: dict = Depends(
        requires_tier(Module.ACTIVE_AR, Tier.WORK)),
):
    """Run Waystar status sync across a filter set. Returns per-claim results."""
    from app.services.active_ar_waystar_sync import sync_many
    q = db.query(ActiveClaim).filter(
        ActiveClaim.workflow_state.notin_(["paid", "rebilled_modmed", "written_off", "closed"])
    )
    if workflow_state:
        q = q.filter(ActiveClaim.workflow_state == workflow_state)
    if payer:
        q = q.filter(ActiveClaim.insurance_company.ilike(f"%{payer}%"))

    today = date.today()
    if age_bucket:
        bounds = {
            "0-30":  (today - timedelta(days=30),  None),
            "31-60": (today - timedelta(days=60),  today - timedelta(days=31)),
            "61-90": (today - timedelta(days=90),  today - timedelta(days=61)),
            "90+":   (None,                        today - timedelta(days=91)),
        }.get(age_bucket)
        if bounds:
            lo, hi = bounds
            if lo is not None:
                q = q.filter(ActiveClaim.dos >= lo)
            if hi is not None:
                q = q.filter(ActiveClaim.dos <= hi)

    if only_unchecked:
        cutoff = datetime.utcnow() - timedelta(hours=24)
        q = q.filter(or_(
            ActiveClaim.last_status_check_at.is_(None),
            ActiveClaim.last_status_check_at < cutoff,
        ))

    # Prioritize: oldest DOS first (closest to TF deadline)
    q = q.order_by(ActiveClaim.dos.asc().nullslast()).limit(max_count)
    claims = q.all()
    return sync_many(db, claims, user_email=current_user.get("email"), max_count=max_count)


# ---------- Charge Analysis enrichment ----------

@router.post("/enrich-from-charge-analysis")
async def enrich_active_claims_from_charge_analysis(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(
        requires_tier(Module.ACTIVE_AR, Tier.WORK)),
):
    """Upload a Charge Analysis XLS and enrich existing active_claims with
    procedure codes, dx codes, provider NPIs, secondary insurance, etc.
    Only matches existing claims — does NOT create new ones."""
    from app.services.active_ar_charge_enricher import enrich_from_charge_analysis

    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in (".xls", ".xlsx"):
        raise HTTPException(status_code=422, detail="file must be .xls or .xlsx")

    subdir = os.path.join(settings.upload_dir, "active_ar_charge_enrich")
    os.makedirs(subdir, exist_ok=True)
    save_path = os.path.join(subdir, f"{uuid.uuid4()}{ext}")
    content = await file.read()
    with open(save_path, "wb") as fh:
        fh.write(content)

    try:
        result = enrich_from_charge_analysis(
            db, save_path, posted_by=current_user.get("email")
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"enrichment failed: {exc}")

    return {
        "filename": file.filename,
        "total_rows": result.total_rows,
        "visits_in_file": result.visits_in_file,
        "matched_claim_records": result.matched_claim_records,
        "unmatched_visits": result.unmatched_visits,
        "unmatched_sample": result.unmatched_sample[:25],
        "errors": result.errors[:25],
    }


# ---------- Line-level settle ----------

class AdjustmentCodeItem(BaseModel):
    """One CARC/RARC adjustment code attached to a service line."""
    group_code: str = "CO"           # CO, PR, OA, PI, CR
    reason_code: str                  # numeric/alphanumeric code
    amount: ArMoney = Field(default=Decimal("0"), ge=0, le=99_999_999.99)
    description: Optional[str] = None


class LineSettlePayload(BaseModel):
    """Per-line EOB entry. allowed + copay/deductible/coinsurance are user
    inputs; contractual + insurance_paid + patient_balance auto-compute.

    All money fields rejected for negatives and NaN/Inf at the API
    boundary. insurance_paid_override accepts 0 for true zero-pay
    EOBs (need adjustment codes to explain) but no negatives.
    (Fable cross-cutting audit #8.)
    """
    allowed: Optional[ArMoney] = Field(default=None, ge=0, le=99_999_999.99)
    copay: Optional[ArMoney] = Field(default=None, ge=0, le=99_999_999.99)
    deductible: Optional[ArMoney] = Field(default=None, ge=0, le=99_999_999.99)
    coinsurance: Optional[ArMoney] = Field(default=None, ge=0, le=99_999_999.99)
    insurance_paid_override: Optional[ArMoney] = Field(default=None, ge=0, le=99_999_999.99)
    patient_paid: Optional[ArMoney] = Field(default=None, ge=0, le=99_999_999.99)
    settled: Optional[bool] = None
    notes: Optional[str] = None
    adjustment_codes: Optional[List[AdjustmentCodeItem]] = None


def _recompute_claim_rollup(c: ActiveClaim, lines: list) -> None:
    """Roll up per-line EOB into claim-level allowed/contractual/copay/etc.

    Also derives paid_amount (sum of insurance_paid across lines that have an
    EOB entered) and insurance_balance (charges on lines still awaiting EOB).
    Assumes line-settle is the sole driver of these totals — if check-level
    payments via /payments are ever mixed in on the same claim, this rollup
    would overwrite that increment.
    """
    def _sum(field):
        vals = [ln.get(field) for ln in lines if ln.get(field) is not None]
        return float(sum(vals)) if vals else None

    c.allowed_amount = _sum("allowed")
    c.contractual_adjustment = _sum("contractual")
    c.copay = _sum("copay")
    c.deductible = _sum("deductible")
    c.coinsurance = _sum("coinsurance")
    pt_resps = []
    for ln in lines:
        co = ln.get("copay") or 0
        de = ln.get("deductible") or 0
        ci = ln.get("coinsurance") or 0
        pp = ln.get("patient_paid") or 0
        pt_resps.append(max(0, (co + de + ci) - pp))
    if any(ln.get("allowed") is not None for ln in lines):
        c.patient_balance = float(sum(pt_resps))

    # Insurance paid: sum across lines where EOB has been entered.
    # Insurance balance: remaining charges on lines still without an EOB
    # (lines with `allowed` set are considered EOB-processed; insurance has
    # paid what it's going to pay on those, even if pt_balance > 0).
    ins_paid_total = sum(float(ln.get("insurance_paid") or 0) for ln in lines)
    outstanding = sum(
        float(ln.get("charge") or 0)
        for ln in lines if ln.get("allowed") is None
    )
    if any(ln.get("allowed") is not None for ln in lines):
        c.paid_amount = round(ins_paid_total, 2)
        c.insurance_balance = round(outstanding, 2)


@router.patch("/claims/{claim_id}/service-lines/{line_num}")
def settle_service_line(
    claim_id: str, line_num: int, payload: LineSettlePayload,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _perm: dict = Depends(requires_tier(Module.ACTIVE_AR, Tier.WORK)),
):
    """Settle a single service line on a claim. Auto-computes contractual,
    insurance_paid, patient_balance from the inputs. Updates the claim-level
    rollups so the EOB Details card shows accurate totals."""
    import json as _json
    # SELECT ... FOR UPDATE on the claim so two concurrent
    # /service-lines/{n} settles don't both read+modify+write the
    # service_lines_json blob and silently erase one of them.
    # (Fable cross-cutting audit #2.)
    c = (db.query(ActiveClaim)
            .filter(ActiveClaim.id == claim_id)
            .with_for_update()
            .first())
    if not c:
        raise HTTPException(status_code=404, detail="claim not found")

    # Dual-writer guard. If check-level allocations exist on this claim
    # via /payments, _recompute_claim_rollup below would overwrite
    # paid_amount/insurance_balance from line JSON, silently destroying
    # the posted increments. Refuse line-settle on claims with
    # allocations; the operator should add the EOB amounts via the
    # check-level payment instead. (Fable cross-cutting audit #4.)
    if c.allocations:
        raise HTTPException(
            status_code=409,
            detail=(f"Claim has {len(c.allocations)} check-level payment "
                    "allocation(s) — settle EOB lines on the payment "
                    "directly to keep paid_amount accurate. Line-settle "
                    "would overwrite the posted payments."))

    if not c.service_lines_json:
        raise HTTPException(status_code=422, detail="claim has no service lines (not enriched)")

    try:
        lines = _json.loads(c.service_lines_json) or []
    except Exception:
        raise HTTPException(status_code=500, detail="service_lines_json corrupt")

    target = next((ln for ln in lines if int(ln.get("line", 0)) == int(line_num)), None)
    if target is None:
        raise HTTPException(status_code=404, detail=f"line {line_num} not found on claim")

    # Apply user inputs (only if provided — None means "don't change")
    if payload.allowed is not None:
        target["allowed"] = float(payload.allowed)
    if payload.copay is not None:
        target["copay"] = float(payload.copay)
    if payload.deductible is not None:
        target["deductible"] = float(payload.deductible)
    if payload.coinsurance is not None:
        target["coinsurance"] = float(payload.coinsurance)
    if payload.patient_paid is not None:
        target["patient_paid"] = float(payload.patient_paid)
    if payload.notes is not None:
        target["notes"] = payload.notes

    # Auto-compute derived fields
    charge = float(target.get("charge") or 0)
    allowed = float(target.get("allowed") or 0)
    copay = float(target.get("copay") or 0)
    deductible = float(target.get("deductible") or 0)
    coinsurance = float(target.get("coinsurance") or 0)
    pt_resp = copay + deductible + coinsurance
    pt_paid = float(target.get("patient_paid") or 0)

    contractual_amt = round(max(0, charge - allowed), 2) if target.get("allowed") is not None else None
    target["contractual"] = contractual_amt
    if payload.insurance_paid_override is not None:
        target["insurance_paid"] = float(payload.insurance_paid_override)
    elif target.get("allowed") is not None:
        target["insurance_paid"] = round(max(0, allowed - pt_resp), 2)
    target["patient_resp"] = round(pt_resp, 2) if target.get("allowed") is not None else None
    target["patient_balance"] = round(max(0, pt_resp - pt_paid), 2) if target.get("allowed") is not None else None

    # Adjustment codes per line. If user provided codes explicitly, store
    # them. Otherwise, when allowed is set and contractual > 0, default to a
    # CO-45 (contractual write-off) for the contractual amount.
    if payload.adjustment_codes is not None:
        target["adjustment_codes"] = [
            {"group_code": ac.group_code, "reason_code": ac.reason_code,
             "amount": float(ac.amount or 0), "description": ac.description}
            for ac in payload.adjustment_codes
        ]
    elif target.get("allowed") is not None and contractual_amt and contractual_amt > 0:
        # Don't overwrite if user already added codes manually
        if not target.get("adjustment_codes"):
            target["adjustment_codes"] = [{
                "group_code": "CO", "reason_code": "45",
                "amount": contractual_amt,
                "description": "Charge exceeds fee schedule (contractual)",
            }]
        # If user has codes, append CO-45 only if it's not already there
        else:
            existing = any(
                (ac.get("group_code") or "").upper() == "CO" and
                str(ac.get("reason_code") or "").strip() == "45"
                for ac in target["adjustment_codes"]
            )
            if not existing:
                target["adjustment_codes"].append({
                    "group_code": "CO", "reason_code": "45",
                    "amount": contractual_amt,
                    "description": "Charge exceeds fee schedule (contractual)",
                })

    # Determine settled status
    if payload.settled is not None:
        target["settled"] = payload.settled
    elif target.get("allowed") is not None:
        # Auto-mark settled if patient owes nothing further
        target["settled"] = (target.get("patient_balance") or 0) <= 0.01
    target["settled_at"] = datetime.utcnow().isoformat() + "Z" if target.get("settled") else None

    # Persist + recompute claim rollup
    c.service_lines_json = _json.dumps(lines)
    _recompute_claim_rollup(c, lines)

    # Auto-route to Denials queue if any line carries appealable codes
    from app.services.denial_classifier import is_appealable
    has_appealable = any(
        is_appealable((ac.get("group_code") or "").upper(), ac.get("reason_code"))
        for ln in lines
        for ac in (ln.get("adjustment_codes") or [])
    )
    if has_appealable and c.workflow_state not in ("paid", "rebilled_modmed", "written_off", "closed", "appealed"):
        c.workflow_state = "denied"

    # Auto-mark whole claim as paid once the insurance side is done —
    # i.e., every line has an EOB entered (`allowed` set) and no
    # appealable denials are present. Any remaining patient_balance
    # (copay/deductible/coinsurance) is tracked separately and is the
    # patient's responsibility — it doesn't block the claim from
    # leaving the insurance AR queue.
    #
    # Revenue-leakage guard (Fable cross-cutting audit #11): refuse to
    # flip the claim to 'paid' when every line was entered with
    # allowed > 0 but insurance_paid totals $0 AND no adjustment codes
    # were attached. That combination is almost always a fat-finger
    # (someone typed allowed=$X but left insurance_paid blank); silently
    # flipping the claim to paid drops it out of the work queue and we
    # lose track of the real revenue gap.
    all_insurance_done = lines and all(ln.get("allowed") is not None for ln in lines)
    if all_insurance_done and not has_appealable:
        total_allowed = sum(float(ln.get("allowed") or 0) for ln in lines)
        total_ins_paid = sum(float(ln.get("insurance_paid") or 0) for ln in lines)
        total_pt_resp = sum(
            float(ln.get("copay") or 0) + float(ln.get("deductible") or 0)
            + float(ln.get("coinsurance") or 0)
            for ln in lines
        )
        any_adj_codes = any(
            ln.get("adjustment_codes") for ln in lines
        )
        # Zero-pay legitimacy check: insurance paid $0 only counts as
        # truly settled when adjustment codes explain it OR when the
        # full allowed amount is patient responsibility (deductible
        # season, high-deductible plan).
        zero_pay_legit = (
            total_ins_paid > 0
            or any_adj_codes
            or total_pt_resp >= total_allowed - 0.01
        )
        if not zero_pay_legit:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Cannot auto-paid this claim: every line has an allowed "
                    f"amount (total ${total_allowed:.2f}) but insurance paid "
                    "$0.00, no adjustment codes explain the zero pay, and "
                    f"patient responsibility (${total_pt_resp:.2f}) doesn't "
                    "absorb the allowed amount. If insurance really paid $0, "
                    "add CARC codes (e.g. CO-45 contractual) to the lines."))

        if c.workflow_state not in ("paid", "rebilled_modmed", "written_off", "closed"):
            c.workflow_state = "paid"
            c.paid_in_full_at = datetime.utcnow()

    db.add(ActiveClaimNote(
        active_claim_id=c.id, user=current_user.get("email"),
        action_type="line_settled",
        note=(
            f"Line {line_num} ({target.get('cpt') or '—'}): "
            f"allowed=${allowed:.2f}, contractual=${target.get('contractual') or 0:.2f}, "
            f"ins_paid=${target.get('insurance_paid') or 0:.2f}, "
            f"pt_resp=${pt_resp:.2f} (copay {copay:.2f} + deduct {deductible:.2f} + coins {coinsurance:.2f})"
            + (f" — {payload.notes}" if payload.notes else "")
        ),
    ))
    db.commit()
    db.refresh(c)

    pat = db.query(Patient).filter(Patient.patient_id == c.patient_external_id).first()
    return _claim_to_dict(c, pat)


# ─────────────────────────────────────────────────────────────────────
# Appeal Letters

class AppealDraftRequest(BaseModel):
    template_type: str
    level: int = 1
    additional_verbiage: Optional[str] = None
    use_ai: bool = True
    signer_name: Optional[str] = None
    signer_credentials: Optional[str] = None
    signer_title: Optional[str] = None


class AppealUpdate(BaseModel):
    subject: Optional[str] = None
    body: Optional[str] = None
    additional_verbiage: Optional[str] = None
    recipient_name: Optional[str] = None
    recipient_address: Optional[str] = None
    recipient_fax: Optional[str] = None
    signer_name: Optional[str] = None
    signer_credentials: Optional[str] = None
    signer_title: Optional[str] = None
    response_outcome: Optional[str] = None
    response_notes: Optional[str] = None


def _appeal_to_dict(a) -> dict:
    return {
        "id": str(a.id),
        "active_claim_id":   str(a.active_claim_id),
        "template_type":     a.template_type,
        "level":             a.level,
        "subject":           a.subject,
        "body":              a.body,
        "additional_verbiage": a.additional_verbiage,
        "recipient_name":    a.recipient_name,
        "recipient_address": a.recipient_address,
        "recipient_fax":     a.recipient_fax,
        "signer_name":       a.signer_name,
        "signer_credentials": a.signer_credentials,
        "signer_title":      a.signer_title,
        "status":            a.status,
        "pdf_path":          a.pdf_path,
        "sent_via":          a.sent_via,
        "sent_at":           str(a.sent_at) if a.sent_at else None,
        "sent_to":           a.sent_to,
        "response_received_at": str(a.response_received_at) if a.response_received_at else None,
        "response_outcome":  a.response_outcome,
        "response_notes":    a.response_notes,
        "used_ai_drafting":  bool(a.used_ai_drafting),
        "generated_by":      a.generated_by,
        "created_at":        str(a.created_at),
        "updated_at":        str(a.updated_at),
    }


@router.get("/appeal-templates")
def list_appeal_templates(current_user: dict = Depends(get_current_user)):
    from app.services.appeal_templates import TEMPLATE_TYPES, LEVEL_LABEL
    return {
        "template_types": [{"key": k, "label": v} for k, v in TEMPLATE_TYPES.items()],
        "levels": [{"key": k, "label": v} for k, v in LEVEL_LABEL.items()],
    }


@router.post("/claims/{claim_id}/appeals/draft")
def draft_appeal(claim_id: str, payload: AppealDraftRequest,
                 db: Session = Depends(get_db),
                 current_user: dict = Depends(get_current_user),
                 _perm: dict = Depends(requires_tier(Module.ACTIVE_AR, Tier.WORK))):
    """Draft a new appeal letter for the claim. Renders the template, optionally
    runs Claude over the body for a tailored argument. Saves as a 'draft' status
    AppealLetter record. Does NOT generate PDF or send."""
    from app.models.appeal_letters import AppealLetter
    from app.services.appeal_letter_service import draft_appeal_letter

    c = db.query(ActiveClaim).options(joinedload(ActiveClaim.notes)).filter(ActiveClaim.id == claim_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="claim not found")

    signer_override = None
    if payload.signer_name:
        signer_override = {
            "name": payload.signer_name,
            "credentials": payload.signer_credentials or "",
            "title": payload.signer_title or "Practice Manager",
        }

    drafted = draft_appeal_letter(
        db, c,
        template_type=payload.template_type,
        level=payload.level,
        additional_verbiage=payload.additional_verbiage,
        signer_override=signer_override,
        use_ai=payload.use_ai,
    )

    letter = AppealLetter(
        active_claim_id=c.id,
        template_type=drafted["template_type"],
        level=drafted["level"],
        subject=drafted["subject"],
        body=drafted["body"],
        additional_verbiage=drafted["additional_verbiage"],
        recipient_name=drafted["recipient_name"],
        recipient_address=drafted["recipient_address"],
        recipient_fax=drafted["recipient_fax"],
        signer_name=drafted["signer_name"],
        signer_credentials=drafted["signer_credentials"],
        signer_title=drafted["signer_title"],
        status="draft",
        used_ai_drafting=1 if drafted["used_ai"] else 0,
        generated_by=current_user.get("email"),
    )
    db.add(letter)
    db.add(ActiveClaimNote(
        active_claim_id=c.id, user=current_user.get("email"),
        action_type="appeal_drafted",
        note=f"Drafted {payload.template_type} appeal (Level {payload.level})"
             + (" with Claude assist" if drafted["used_ai"] else " from template only"),
    ))
    db.commit()
    db.refresh(letter)
    return _appeal_to_dict(letter)


@router.get("/claims/{claim_id}/appeals")
def list_appeals_for_claim(claim_id: str,
                           db: Session = Depends(get_db),
                           current_user: dict = Depends(get_current_user)):
    from app.models.appeal_letters import AppealLetter
    rows = (
        db.query(AppealLetter)
        .filter(AppealLetter.active_claim_id == claim_id)
        .order_by(desc(AppealLetter.created_at))
        .all()
    )
    return {"appeals": [_appeal_to_dict(a) for a in rows]}


@router.patch("/appeals/{appeal_id}")
def update_appeal(appeal_id: str, payload: AppealUpdate,
                  db: Session = Depends(get_db),
                  current_user: dict = Depends(
                      requires_tier(Module.ACTIVE_AR, Tier.WORK))):
    from app.models.appeal_letters import AppealLetter
    a = db.query(AppealLetter).filter(AppealLetter.id == appeal_id).first()
    if not a:
        raise HTTPException(status_code=404, detail="appeal not found")
    # Block edits to a sent appeal — rewriting the body or recipient of
    # an already-faxed legal document would silently revise history.
    # (Fable cross-cutting audit #18.)
    if (a.status or "").lower() == "sent":
        raise HTTPException(
            status_code=409,
            detail="Cannot edit an appeal that has already been sent.")
    for field in ("subject", "body", "additional_verbiage",
                  "recipient_name", "recipient_address", "recipient_fax",
                  "signer_name", "signer_credentials", "signer_title",
                  "response_outcome", "response_notes"):
        v = getattr(payload, field)
        if v is not None:
            setattr(a, field, v)
    if payload.response_outcome is not None and a.response_received_at is None:
        a.response_received_at = datetime.utcnow()
    db.commit()
    db.refresh(a)
    return _appeal_to_dict(a)


@router.post("/appeals/{appeal_id}/generate-pdf")
def generate_appeal_pdf(appeal_id: str,
                        db: Session = Depends(get_db),
                        current_user: dict = Depends(
                            requires_tier(Module.ACTIVE_AR, Tier.WORK))):
    """Render the appeal letter to PDF and save to GCS. Auto-attaches to claim."""
    a = db.query(AppealLetter).filter(AppealLetter.id == appeal_id).first()
    if not a:
        raise HTTPException(status_code=404, detail="appeal not found")

    id_slug = str(a.id).replace("-", "")[:8]
    safe_name = f"appeal_{a.template_type}_L{a.level}_{id_slug}.pdf"
    pdf_bytes = render_pdf(a.subject, a.body)   # no output_path → just bytes
    key = save_blob(prefix="appeal-letters", body=pdf_bytes, filename=safe_name)
    a.pdf_path = key
    a.status = "generated"

    # Auto-attach as a Document on the claim — same GCS key
    doc = ActiveClaimDocument(
        active_claim_id=a.active_claim_id,
        document_type="Appeal",
        filename=safe_name,
        content_type="application/pdf",
        file_size=len(pdf_bytes),
        file_path=key,
        description=f"Generated appeal letter — {a.subject}",
        uploaded_by=current_user.get("email"),
    )
    db.add(doc)
    db.add(ActiveClaimNote(
        active_claim_id=a.active_claim_id, user=current_user.get("email"),
        action_type="appeal_generated",
        note=f"Generated PDF for {a.template_type} appeal Level {a.level} ({safe_name})",
    ))
    db.commit()
    db.refresh(a)
    return _appeal_to_dict(a)


@router.get("/appeals/{appeal_id}/pdf")
def download_appeal_pdf(appeal_id: str,
                        db: Session = Depends(get_db),
                        current_user: dict = Depends(get_current_user)):
    a = db.query(AppealLetter).filter(AppealLetter.id == appeal_id).first()
    if not a or not a.pdf_path:
        raise HTTPException(status_code=404, detail="PDF not generated yet")
    if is_legacy_local_path(a.pdf_path):
        raise HTTPException(status_code=410,
                            detail="This file is from before the cloud migration and is no longer available.")
    # PHI document download audit (Fable cross-cutting #9). Appeal
    # letters typically contain patient identifiers + clinical context.
    c = (db.query(ActiveClaim)
            .filter(ActiveClaim.id == a.active_claim_id).first())
    actor = current_user.get("email")
    log_action(
        db, action="APPEAL_PDF_DOWNLOAD",
        resource_type="appeal_letter", resource_id=str(a.id),
        patient_id=(c.patient_external_id if c else None),
        user_id=(actor or "").lower() or None, user_name=actor,
        description=(f"Downloaded Level-{a.level} appeal PDF for claim "
                     f"{(c.claim_number if c else a.active_claim_id)}"),
    )
    return serve_blob(
        local_path=None,
        gcs_object=a.pdf_path,
        media_type="application/pdf",
        filename=os.path.basename(a.pdf_path) if "/" in a.pdf_path else a.pdf_path,
    )


@router.post("/appeals/{appeal_id}/send-fax")
def send_appeal_fax(appeal_id: str,
                    db: Session = Depends(get_db),
                    current_user: dict = Depends(
                        requires_tier(Module.ACTIVE_AR, Tier.WORK))):
    """Queue the generated appeal PDF for fax delivery to the recipient_fax."""
    from app.models.appeal_letters import AppealLetter
    a = db.query(AppealLetter).filter(AppealLetter.id == appeal_id).first()
    if not a:
        raise HTTPException(status_code=404, detail="appeal not found")
    # pdf_path used to be a local-disk path; after the GCS migration
    # it's an object key, so os.path.exists is always False even when
    # the PDF exists. Just check the key is non-empty — the fax
    # service will fail loudly if the blob isn't there. (Fable
    # cross-cutting audit Low #21.)
    if not a.pdf_path:
        raise HTTPException(status_code=422, detail="generate the PDF first")
    if not a.recipient_fax:
        raise HTTPException(status_code=422, detail="no recipient fax number on file")

    # Queue via existing fax infrastructure if available
    try:
        from app.services import fax_service  # type: ignore
        result = fax_service.send_pdf(
            to_fax=a.recipient_fax,
            file_path=a.pdf_path,
            subject=a.subject or "Appeal Letter",
            sent_by=current_user.get("email"),
        )
        a.status = "sent"
        a.sent_via = "fax"
        a.sent_at = datetime.utcnow()
        a.sent_to = a.recipient_fax
        a.fax_log_id = result.get("fax_log_id") if isinstance(result, dict) else None
        db.add(ActiveClaimNote(
            active_claim_id=a.active_claim_id, user=current_user.get("email"),
            action_type="appeal_faxed",
            note=f"Faxed Level-{a.level} appeal to {a.recipient_name} at {a.recipient_fax}",
        ))
        if hasattr(a.claim, "workflow_state") and a.claim.workflow_state in ("denied", "new", "in_progress"):
            a.claim.workflow_state = "appealed"
        db.commit()
        return _appeal_to_dict(a)
    except ImportError:
        raise HTTPException(
            status_code=501,
            detail="Fax service not yet wired up — download the PDF and send manually for now.",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"fax send failed: {exc}")


@router.post("/appeals/{appeal_id}/mark-sent")
def mark_appeal_sent(appeal_id: str,
                     sent_via: str = Query("mail"),
                     sent_to: Optional[str] = Query(None),
                     db: Session = Depends(get_db),
                     current_user: dict = Depends(
                         requires_tier(Module.ACTIVE_AR, Tier.WORK))):
    """Mark an appeal as sent (used when sent manually outside the system —
    e.g., printed and mailed, or sent via portal upload)."""
    from app.models.appeal_letters import AppealLetter
    a = db.query(AppealLetter).filter(AppealLetter.id == appeal_id).first()
    if not a:
        raise HTTPException(status_code=404, detail="appeal not found")
    # State guard: must have a generated PDF first, can't re-mark sent.
    # (Fable cross-cutting audit #18.)
    if (a.status or "").lower() == "sent":
        raise HTTPException(status_code=409,
                            detail="Appeal is already marked sent.")
    if not a.pdf_path:
        raise HTTPException(status_code=409,
                            detail="Generate the PDF before marking the appeal sent.")
    a.status = "sent"
    a.sent_via = sent_via
    a.sent_at = datetime.utcnow()
    a.sent_to = sent_to or a.recipient_address
    db.add(ActiveClaimNote(
        active_claim_id=a.active_claim_id, user=current_user.get("email"),
        action_type="appeal_sent",
        note=f"Marked Level-{a.level} appeal as sent via {sent_via}"
             + (f" to {sent_to}" if sent_to else ""),
    ))
    if hasattr(a.claim, "workflow_state") and a.claim.workflow_state in ("denied", "new", "in_progress"):
        a.claim.workflow_state = "appealed"
    db.commit()
    return _appeal_to_dict(a)


@router.get("/payer-addresses")
def list_payer_addresses(db: Session = Depends(get_db),
                         current_user: dict = Depends(get_current_user)):
    from app.models.appeal_letters import PayerAddress
    rows = db.query(PayerAddress).order_by(PayerAddress.payer_name).all()
    return {"payers": [
        {
            "id": str(p.id), "payer_name": p.payer_name, "payer_id": p.payer_id,
            "appeals_dept_name": p.appeals_dept_name,
            "address_line_1": p.address_line_1, "address_line_2": p.address_line_2,
            "city": p.city, "state": p.state, "zip_code": p.zip_code,
            "appeals_fax": p.appeals_fax, "appeals_phone": p.appeals_phone,
            "appeals_email": p.appeals_email, "notes": p.notes,
        }
        for p in rows
    ]}
