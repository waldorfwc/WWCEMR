from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import desc, or_, func
from typing import Optional
import uuid
from datetime import date as date_cls, timedelta
from decimal import Decimal, InvalidOperation

from app.database import get_db
from app.models.claim import Claim, ClaimStatus, InsuranceOrder, EraFile
from app.models.patient import Patient
from app.routers.auth import get_current_user
from app.permissions.catalog import Module, Tier
from app.permissions.dependencies import requires_tier
from app.services.audit_service import log_action
from app.services.claim_math import recompute_balance

router = APIRouter(prefix="/claims", tags=["claims"])


@router.get("")
def list_claims(
    db: Session = Depends(get_db),
    status: Optional[str] = None,
    payer: Optional[str] = None,
    search: Optional[str] = None,
    state: Optional[str] = None,             # Phase 2d: "open" | "closed"
    has_followup: Optional[bool] = None,     # Phase 2d: Open + follow_up_date <= today
    age_bucket: Optional[str] = None,        # "0-30" | "31-60" | "61-90" | "90+"
    order_by: Optional[str] = None,          # "fu_asc" | "dos_desc" (default) | "balance_desc"
    page: int = 1,
    per_page: int = 50,
):
    q = db.query(Claim).options(joinedload(Claim.patient))
    if status:
        q = q.filter(Claim.status == status)
    if payer:
        q = q.filter(Claim.payer_name.ilike(f"%{payer}%"))
    if search:
        s = f"%{search}%"
        q = q.outerjoin(Claim.patient).filter(or_(
            Claim.claim_number.ilike(s),
            Claim.payer_claim_number.ilike(s),
            Claim.subscriber_id.ilike(s),
            Patient.patient_id.ilike(s),
            Patient.first_name.ilike(s),
            Patient.last_name.ilike(s),
        ))
    if state == "open":
        q = q.filter(Claim.claim_state == "Open")
    elif state == "closed":
        q = q.filter(Claim.claim_state == "Closed")
    if has_followup:
        q = q.filter(
            Claim.follow_up_date.isnot(None),
            Claim.follow_up_date <= date_cls.today(),
            Claim.claim_state == "Open",
        )
    today = date_cls.today()
    if age_bucket:
        bounds = {
            "0-30":   (today - timedelta(days=30),  None),
            "31-60":  (today - timedelta(days=60),  today - timedelta(days=31)),
            "61-90":  (today - timedelta(days=90),  today - timedelta(days=61)),
            "90+":    (None,                        today - timedelta(days=91)),
        }.get(age_bucket)
        if bounds:
            lo, hi = bounds
            if lo is not None:
                q = q.filter(Claim.date_of_service_from >= lo)
            if hi is not None:
                q = q.filter(Claim.date_of_service_from <= hi)

    if order_by == "fu_asc":
        # Overdue/oldest follow-up first, then DOS desc
        q = q.order_by(Claim.follow_up_date.asc().nullslast(), desc(Claim.date_of_service_from))
    elif order_by == "balance_desc":
        q = q.order_by(desc(Claim.balance), desc(Claim.date_of_service_from))
    else:
        q = q.order_by(desc(Claim.date_of_service_from))

    total = q.count()
    claims = q.offset((page - 1) * per_page).limit(per_page).all()

    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "claims": [_claim_to_dict(c) for c in claims],
    }


@router.get("/work-queue/summary")
def work_queue_summary(db: Session = Depends(get_db)):
    """Counts/balances for the open AR work queue, plus age bucket breakdown."""
    today = date_cls.today()
    base = db.query(Claim).filter(Claim.claim_state == "Open", Claim.balance > 0)

    open_total = base.count()
    open_balance = float(base.with_entities(func.sum(Claim.balance)).scalar() or 0)
    overdue = base.filter(Claim.follow_up_date.isnot(None),
                          Claim.follow_up_date < today).count()
    due_today = base.filter(Claim.follow_up_date == today).count()
    no_fu = base.filter(Claim.follow_up_date.is_(None)).count()

    buckets = []
    for label, lo_days, hi_days in [
        ("0-30", 0, 30), ("31-60", 31, 60), ("61-90", 61, 90), ("90+", 91, None)
    ]:
        sub = base
        if hi_days is not None:
            sub = sub.filter(Claim.date_of_service_from >= today - timedelta(days=hi_days))
        if lo_days > 0:
            sub = sub.filter(Claim.date_of_service_from <= today - timedelta(days=lo_days))
        bal = sub.with_entities(func.sum(Claim.balance)).scalar() or 0
        buckets.append({"bucket": label, "count": sub.count(), "balance": float(bal)})

    # Top payers by open balance
    payer_rows = (
        base.with_entities(Claim.payer_name, func.count(Claim.id), func.sum(Claim.balance))
        .group_by(Claim.payer_name)
        .order_by(desc(func.sum(Claim.balance)))
        .limit(15)
        .all()
    )
    top_payers = [
        {"payer": r[0] or "—", "count": r[1], "balance": float(r[2] or 0)}
        for r in payer_rows
    ]

    return {
        "open_total": open_total,
        "open_balance": open_balance,
        "overdue": overdue,
        "due_today": due_today,
        "no_fu": no_fu,
        "age_buckets": buckets,
        "top_payers": top_payers,
    }


@router.get("/summary")
def claim_summary(db: Session = Depends(get_db)):
    rows = db.query(Claim.status, func.count(Claim.id), func.sum(Claim.billed_amount),
                    func.sum(Claim.paid_amount), func.sum(Claim.balance)).group_by(Claim.status).all()
    total_billed = db.query(func.sum(Claim.billed_amount)).scalar() or 0
    total_paid = db.query(func.sum(Claim.paid_amount)).scalar() or 0
    total_balance = db.query(func.sum(Claim.balance)).scalar() or 0

    by_status = {}
    for row in rows:
        by_status[row[0].value if row[0] else "unknown"] = {
            "count": row[1],
            "billed": float(row[2] or 0),
            "paid": float(row[3] or 0),
            "balance": float(row[4] or 0),
        }

    return {
        "total_claims": db.query(func.count(Claim.id)).scalar(),
        "total_billed": float(total_billed),
        "total_paid": float(total_paid),
        "total_balance": float(total_balance),
        "by_status": by_status,
    }


@router.get("/{claim_id}")
def get_claim(claim_id: str, db: Session = Depends(get_db)):
    claim = db.query(Claim).filter(Claim.id == claim_id).first()
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found")
    log_action(db, "VIEW", "claim", resource_id=claim_id,
               patient_id=str(claim.patient_id) if claim.patient_id else None)
    return _claim_to_dict(claim, detailed=True)


EDITABLE_CLAIM_FIELDS = {
    # strings
    "claim_number", "payer_claim_number", "payer_name", "payer_id",
    "subscriber_id", "group_number", "check_number",
    "rendering_provider_name", "rendering_provider_npi", "notes",
    # enums
    "status", "insurance_order",
    # dates
    "date_of_service_from", "date_of_service_to", "check_date",
    # money
    "billed_amount", "allowed_amount", "paid_amount",
    "patient_responsibility", "contractual_adjustment", "other_adjustment",
    # relation
    "patient_id",
    # Phase 2d
    "follow_up_date", "follow_up_reason", "last_submission_date", "claim_state",
}

MONEY_FIELDS = {
    "billed_amount", "allowed_amount", "paid_amount",
    "patient_responsibility", "contractual_adjustment", "other_adjustment",
}

DATE_FIELDS = {
    "date_of_service_from", "date_of_service_to", "check_date",
    "follow_up_date", "last_submission_date",
}


def _coerce_claim_value(k: str, v):
    """Coerce incoming JSON value to the type the ORM column expects."""
    if v is None:
        return None
    if k == "status":
        try:
            return ClaimStatus(v)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"invalid status: {v}")
    if k == "insurance_order":
        try:
            return InsuranceOrder(v)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"invalid insurance_order: {v}")
    if k in MONEY_FIELDS:
        try:
            return Decimal(str(v))
        except (InvalidOperation, TypeError, ValueError):
            raise HTTPException(status_code=422, detail=f"invalid number for {k}: {v!r}")
    if k in DATE_FIELDS:
        if isinstance(v, str):
            try:
                return date_cls.fromisoformat(v)
            except ValueError:
                raise HTTPException(status_code=422, detail=f"invalid date for {k}: {v!r}")
        return v
    return v


@router.patch("/{claim_id}")
def update_claim(
    claim_id: str,
    data: dict,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    claim = db.query(Claim).filter(Claim.id == claim_id).first()
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found")

    # Validate patient_id exists (if provided and not null)
    if "patient_id" in data and data["patient_id"]:
        if not db.query(Patient).filter(Patient.id == data["patient_id"]).first():
            raise HTTPException(status_code=422, detail="patient_id does not exist")

    old = {}
    new = {}
    for k, raw in data.items():
        if k not in EDITABLE_CLAIM_FIELDS:
            continue  # silently drop balance, era_file_id, etc.
        if not hasattr(claim, k):
            continue
        v = _coerce_claim_value(k, raw)
        current = getattr(claim, k)
        if current != v:
            # Capture before/after — stringify enums/decimals/dates for JSON audit
            old[k] = _audit_val(current)
            new[k] = _audit_val(v)
            setattr(claim, k, v)

    if any(k in new for k in MONEY_FIELDS):
        recompute_balance(claim)

    db.commit()
    if old or new:
        log_action(db, "UPDATE", "claim", resource_id=claim_id,
                   patient_id=str(claim.patient_id) if claim.patient_id else None,
                   user_name=current_user.get("email"),
                   old_values=old, new_values=new)
    db.refresh(claim)
    return _claim_to_dict(claim, detailed=True)


def _audit_val(v):
    if v is None:
        return None
    if hasattr(v, "value"):  # enum
        return v.value
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, date_cls):
        return v.isoformat()
    return v


def _claim_to_dict(claim: Claim, detailed: bool = False) -> dict:
    p = claim.patient
    # Derive allowed_amount when not explicitly set (CA-sourced claims never
    # have it). Standard definition: allowed = billed - contractual_writeoff.
    explicit_allowed = float(claim.allowed_amount or 0)
    derived_allowed = float((claim.billed_amount or 0) - (claim.contractual_adjustment or 0))
    allowed_for_claim = explicit_allowed if explicit_allowed > 0 else derived_allowed
    d = {
        "id": str(claim.id),
        "claim_number": claim.claim_number,
        "payer_claim_number": claim.payer_claim_number,
        "patient_id": str(claim.patient_id) if claim.patient_id else None,
        "patient": {
            "id": str(p.id),
            "chart_number": p.patient_id,
            "first_name": p.first_name,
            "last_name": p.last_name,
            "date_of_birth": str(p.date_of_birth) if p.date_of_birth else None,
        } if p else None,
        "payer_name": claim.payer_name,
        "payer_id": claim.payer_id,
        "subscriber_id": claim.subscriber_id,
        "group_number": claim.group_number,
        "date_of_service_from": str(claim.date_of_service_from) if claim.date_of_service_from else None,
        "date_of_service_to": str(claim.date_of_service_to) if claim.date_of_service_to else None,
        "insurance_order": claim.insurance_order.value if claim.insurance_order else "primary",
        "status": claim.status.value if claim.status else "pending",
        "billed_amount": float(claim.billed_amount or 0),
        "allowed_amount": allowed_for_claim,
        "paid_amount": float(claim.paid_amount or 0),
        "patient_responsibility": float(claim.patient_responsibility or 0),
        "contractual_adjustment": float(claim.contractual_adjustment or 0),
        "other_adjustment": float(claim.other_adjustment or 0),
        "balance": float(claim.balance or 0),
        "check_number": claim.check_number,
        "check_date": str(claim.check_date) if claim.check_date else None,
        "rendering_provider_name": claim.rendering_provider_name,
        "rendering_provider_npi": claim.rendering_provider_npi,
        "notes": claim.notes,
        # Phase 2d
        "follow_up_date": str(claim.follow_up_date) if claim.follow_up_date else None,
        "follow_up_reason": claim.follow_up_reason,
        "last_submission_date": str(claim.last_submission_date) if claim.last_submission_date else None,
        "claim_state": claim.claim_state,
    }
    if detailed:
        d["service_lines"] = [
            {
                "id": str(s.id),
                "procedure_code": s.procedure_code,
                "modifier_1": s.modifier_1,
                "modifier_2": s.modifier_2,
                "modifier_3": s.modifier_3,
                "modifier_4": s.modifier_4,
                "revenue_code": s.revenue_code,
                "units": float(s.units or 1),
                "billed_amount": float(s.billed_amount or 0),
                "paid_amount": float(s.paid_amount or 0),
                "patient_responsibility": float(s.patient_responsibility or 0),
                "date_of_service_from": str(s.date_of_service_from) if s.date_of_service_from else None,
                "adjustments": [
                    {"group_code": a.group_code, "reason_code": a.reason_code,
                     "amount": float(a.amount or 0), "reason_description": a.reason_description}
                    for a in s.adjustments
                ],
            }
            for s in claim.service_lines
        ]
        d["adjustments"] = [
            {"group_code": a.group_code, "reason_code": a.reason_code,
             "amount": float(a.amount or 0), "reason_description": a.reason_description}
            for a in claim.adjustments
        ]
        d["denials"] = [
            {
                "id": str(dn.id),
                "carc_code": dn.carc_code,
                "carc_description": dn.carc_description,
                "rarc_code": dn.rarc_code,
                "rarc_description": dn.rarc_description,
                "category": dn.category.value if dn.category else "other",
                "denied_amount": float(dn.denied_amount or 0),
                "status": dn.status.value if dn.status else "open",
                "appeal_deadline": str(dn.appeal_deadline) if dn.appeal_deadline else None,
                "recommended_action": dn.recommended_action,
                "write_off_recommended": dn.write_off_recommended,
                "appealable": dn.appealable,
            }
            for dn in claim.denials
        ]
    return d
