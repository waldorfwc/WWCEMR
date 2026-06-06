"""Admin CRUD for the surgery fee schedule + CCI/MPR edits, plus a
per-surgery calculator endpoint."""
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.surgery import Surgery
from app.models.fee_schedule import (
    SurgeryFeeScheduleEntry, SurgeryCciEdit, CCI_ACTIONS,
)
from app.services.fee_schedule_calc import calculate_allowed_for_surgery
from app.routers.auth import get_current_user
from app.permissions.catalog import Module, Tier
from app.permissions.dependencies import requires_tier

router = APIRouter(prefix="/surgery", tags=["fee-schedule"])


# ─── Fee schedule entries ───────────────────────────────────────────

class FeeEntryIn(BaseModel):
    insurance_name:  str
    cpt_code:        str
    allowed_amount:  float
    notes:           Optional[str] = None
    effective_from:  Optional[str] = None    # YYYY-MM-DD


def _entry_dict(e: SurgeryFeeScheduleEntry) -> dict:
    return {
        "id":              str(e.id),
        "insurance_name":  e.insurance_name,
        "cpt_code":        e.cpt_code,
        "allowed_amount":  float(e.allowed_amount),
        "notes":           e.notes,
        "effective_from":  str(e.effective_from) if e.effective_from else None,
        "updated_at":      e.updated_at.isoformat() if e.updated_at else None,
    }


@router.get("/fee-schedule")
def list_entries(insurance: Optional[str] = Query(None),
                  cpt: Optional[str] = Query(None),
                  db: Session = Depends(get_db),
                  _: dict = Depends(requires_tier(Module.SURGERY, Tier.VIEW))):
    q = db.query(SurgeryFeeScheduleEntry)
    if insurance:
        q = q.filter(SurgeryFeeScheduleEntry.insurance_name == insurance)
    if cpt:
        q = q.filter(SurgeryFeeScheduleEntry.cpt_code == cpt)
    rows = q.order_by(SurgeryFeeScheduleEntry.insurance_name,
                        SurgeryFeeScheduleEntry.cpt_code).all()
    return {"rows": [_entry_dict(r) for r in rows]}


@router.post("/fee-schedule", status_code=201)
def upsert_entry(payload: FeeEntryIn,
                  db: Session = Depends(get_db),
                  current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.WORK))):
    """Insert or update the row for (insurance_name, cpt_code)."""
    from datetime import datetime
    insurance = (payload.insurance_name or "").strip()
    cpt = (payload.cpt_code or "").strip()
    if not insurance or not cpt:
        raise HTTPException(status_code=422,
                            detail="insurance_name and cpt_code are required")
    if payload.allowed_amount is None or payload.allowed_amount < 0:
        raise HTTPException(status_code=422,
                            detail="allowed_amount must be ≥ 0")

    eff = None
    if payload.effective_from:
        try:
            eff = datetime.strptime(payload.effective_from[:10], "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=422,
                                detail="effective_from must be YYYY-MM-DD")

    row = (db.query(SurgeryFeeScheduleEntry)
             .filter(SurgeryFeeScheduleEntry.insurance_name == insurance,
                     SurgeryFeeScheduleEntry.cpt_code == cpt)
             .first())
    if row is None:
        row = SurgeryFeeScheduleEntry(
            insurance_name=insurance, cpt_code=cpt,
            allowed_amount=Decimal(str(payload.allowed_amount)),
            notes=payload.notes, effective_from=eff,
            created_by=current_user.get("email"),
        )
        db.add(row)
    else:
        row.allowed_amount = Decimal(str(payload.allowed_amount))
        row.notes          = payload.notes
        row.effective_from = eff
    db.commit(); db.refresh(row)
    return _entry_dict(row)


@router.delete("/fee-schedule/{entry_id}", status_code=204)
def delete_entry(entry_id: str,
                  db: Session = Depends(get_db),
                  _: dict = Depends(requires_tier(Module.SURGERY, Tier.WORK))):
    row = (db.query(SurgeryFeeScheduleEntry)
             .filter(SurgeryFeeScheduleEntry.id == entry_id).first())
    if row is None:
        raise HTTPException(status_code=404, detail="entry not found")
    db.delete(row); db.commit()
    return None


# ─── CCI / MPR edits ────────────────────────────────────────────────

class CciEditIn(BaseModel):
    cpt_primary:   str
    cpt_secondary: str
    action:        str   # blocked | reduce_50 | allow_100
    notes:         Optional[str] = None


def _cci_dict(e: SurgeryCciEdit) -> dict:
    return {
        "id":             str(e.id),
        "cpt_primary":    e.cpt_primary,
        "cpt_secondary":  e.cpt_secondary,
        "action":         e.action,
        "notes":          e.notes,
        "created_at":     e.created_at.isoformat() if e.created_at else None,
    }


@router.get("/cci-edits")
def list_cci(db: Session = Depends(get_db),
              _: dict = Depends(requires_tier(Module.SURGERY, Tier.VIEW))):
    rows = (db.query(SurgeryCciEdit)
              .order_by(SurgeryCciEdit.cpt_primary,
                        SurgeryCciEdit.cpt_secondary).all())
    return {"rows": [_cci_dict(r) for r in rows]}


@router.post("/cci-edits", status_code=201)
def upsert_cci(payload: CciEditIn,
                db: Session = Depends(get_db),
                current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.WORK))):
    primary   = (payload.cpt_primary   or "").strip()
    secondary = (payload.cpt_secondary or "").strip()
    if not primary or not secondary:
        raise HTTPException(status_code=422,
                            detail="cpt_primary and cpt_secondary are required")
    if primary == secondary:
        raise HTTPException(status_code=422,
                            detail="cpt_primary and cpt_secondary must differ")
    if payload.action not in CCI_ACTIONS:
        raise HTTPException(status_code=422,
                            detail=f"action must be one of {list(CCI_ACTIONS)}")

    row = (db.query(SurgeryCciEdit)
             .filter(SurgeryCciEdit.cpt_primary   == primary,
                     SurgeryCciEdit.cpt_secondary == secondary)
             .first())
    if row is None:
        row = SurgeryCciEdit(
            cpt_primary=primary, cpt_secondary=secondary,
            action=payload.action, notes=payload.notes,
            created_by=current_user.get("email"),
        )
        db.add(row)
    else:
        row.action = payload.action
        row.notes  = payload.notes
    db.commit(); db.refresh(row)
    return _cci_dict(row)


@router.delete("/cci-edits/{edit_id}", status_code=204)
def delete_cci(edit_id: str,
                db: Session = Depends(get_db),
                _: dict = Depends(requires_tier(Module.SURGERY, Tier.WORK))):
    row = (db.query(SurgeryCciEdit)
             .filter(SurgeryCciEdit.id == edit_id).first())
    if row is None:
        raise HTTPException(status_code=404, detail="edit not found")
    db.delete(row); db.commit()
    return None


# ─── Per-surgery calculator ─────────────────────────────────────────

@router.get("/{surgery_id}/fee-schedule/preview")
def preview_from_fee_schedule(surgery_id: str,
                                db: Session = Depends(get_db),
                                _: dict = Depends(requires_tier(Module.SURGERY, Tier.VIEW))):
    """Dry-run: show what the allowed amount would be without changing
    the surgery row."""
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="surgery not found")
    result = calculate_allowed_for_surgery(db, s)
    return {
        "insurance":     result["insurance"],
        "total_allowed": float(result["total_allowed"]),
        "per_cpt":       [
            {**r,
              "allowed_from_schedule": (float(r["allowed_from_schedule"])
                                        if r["allowed_from_schedule"] is not None else None),
              "applied":               (float(r["applied"])
                                        if r["applied"] is not None else None)}
            for r in result["per_cpt"]
        ],
        "warnings":      result["warnings"],
    }


@router.post("/{surgery_id}/fee-schedule/apply")
def apply_from_fee_schedule(surgery_id: str,
                              db: Session = Depends(get_db),
                              current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.WORK))):
    """Compute the allowed amount and write it to Surgery.allowed_amount.
    Returns the same payload as /preview plus the updated value."""
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="surgery not found")
    result = calculate_allowed_for_surgery(db, s)
    s.allowed_amount = result["total_allowed"]

    # Re-run the patient-responsibility formula against the new allowed amount
    # using the existing deductible/copay/coinsurance/OOP-max fields on the
    # surgery. Mirrors the BenefitsPanel live calc on the frontend.
    def _f(v): return float(v or 0)
    allowed     = _f(s.allowed_amount)
    deductible  = _f(s.deductible)
    ded_met     = _f(s.deductible_met)
    copay       = _f(s.copay)
    coins_pct   = _f(s.coinsurance_pct)
    oop_max     = _f(s.oop_max)
    oop_met     = _f(s.oop_met)

    ded_remaining = max(0.0, deductible - ded_met)
    ded_portion   = min(allowed, ded_remaining)
    after_ded     = max(0.0, allowed - ded_portion)
    coins_portion = round(after_ded * (coins_pct / 100.0), 2)
    raw           = round(ded_portion + coins_portion + copay, 2)
    if oop_max > 0:
        oop_remaining = max(0.0, oop_max - oop_met)
        s.patient_responsibility = round(min(raw, oop_remaining), 2)
    else:
        s.patient_responsibility = raw

    db.commit()

    from app.models.surgery import SurgeryNote
    db.add(SurgeryNote(
        surgery_id=s.id,
        created_by=current_user.get("email"),
        content=(f"Allowed amount set to ${result['total_allowed']:.2f} "
                  f"from fee schedule ({result['insurance'] or 'no insurance set'}). "
                  f"Patient responsibility recalculated to "
                  f"${float(s.patient_responsibility or 0):.2f}. "
                  f"CPTs: {', '.join(r['cpt'] for r in result['per_cpt']) or 'none'}"),
    ))
    db.commit()

    return {
        "allowed_amount": float(s.allowed_amount or 0),
        "patient_responsibility": float(s.patient_responsibility or 0),
        "preview": {
            "insurance":     result["insurance"],
            "total_allowed": float(result["total_allowed"]),
            "per_cpt":       [
                {**r,
                  "allowed_from_schedule": (float(r["allowed_from_schedule"])
                                            if r["allowed_from_schedule"] is not None else None),
                  "applied":               (float(r["applied"])
                                            if r["applied"] is not None else None)}
                for r in result["per_cpt"]
            ],
            "warnings":      result["warnings"],
        },
    }
