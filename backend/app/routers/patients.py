from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import or_, func, desc
from typing import Optional
from pydantic import BaseModel
from datetime import date

from app.database import get_db
from app.models.patient import Patient
from app.routers.auth import get_current_user, require_permission
from app.permissions.catalog import Module, Tier
from app.permissions.dependencies import requires_tier
from app.services.audit_service import log_action, log_view

router = APIRouter(prefix="/patients", tags=["patients"])


class PatientCreate(BaseModel):
    patient_id: str
    first_name: str
    last_name: str
    date_of_birth: Optional[date] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    primary_insurance_name: Optional[str] = None
    primary_insurance_id: Optional[str] = None
    primary_group_number: Optional[str] = None
    secondary_insurance_name: Optional[str] = None
    secondary_insurance_id: Optional[str] = None
    secondary_group_number: Optional[str] = None
    tertiary_insurance_name: Optional[str] = None
    tertiary_insurance_id: Optional[str] = None
    tertiary_group_number: Optional[str] = None


@router.get("")
def list_patients(
    db: Session = Depends(get_db),
    search: Optional[str] = None,
    page: int = 1,
    per_page: int = 50,
):
    q = db.query(Patient)
    if search:
        q = q.filter(or_(
            Patient.first_name.ilike(f"%{search}%"),
            Patient.last_name.ilike(f"%{search}%"),
            Patient.patient_id.ilike(f"%{search}%"),
            Patient.primary_insurance_id.ilike(f"%{search}%"),
        ))
    total = q.count()
    patients = q.order_by(Patient.last_name).offset((page - 1) * per_page).limit(per_page).all()
    return {"total": total, "patients": [_to_dict(p) for p in patients]}


@router.post("")
def create_patient(data: PatientCreate, db: Session = Depends(get_db),
                    _: dict = Depends(requires_tier(Module.CHART, Tier.WORK))):
    existing = db.query(Patient).filter(Patient.patient_id == data.patient_id).first()
    if existing:
        raise HTTPException(status_code=409, detail="Patient ID already exists")
    p = Patient(**data.model_dump())
    db.add(p)
    db.commit()
    db.refresh(p)
    log_action(db, "CREATE", "patient", resource_id=str(p.id), patient_id=str(p.id))
    return _to_dict(p)


@router.get("/{patient_id}")
def get_patient(patient_id: str, db: Session = Depends(get_db),
                current_user: dict = Depends(get_current_user)):
    p = db.query(Patient).filter(Patient.id == patient_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Patient not found")
    log_view(db, "patient", resource_id=patient_id, patient_id=patient_id,
             current_user=current_user,
             description=f"Viewed patient {p.last_name}, {p.first_name}")
    return _to_dict(p)


@router.patch("/{patient_id}")
def update_patient(patient_id: str, data: dict, db: Session = Depends(get_db),
                    _: dict = Depends(requires_tier(Module.CHART, Tier.WORK))):
    p = db.query(Patient).filter(Patient.id == patient_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Patient not found")
    allowed = ["first_name", "last_name", "date_of_birth", "address", "phone", "email",
               "primary_insurance_name", "primary_insurance_id", "primary_group_number",
               "secondary_insurance_name", "secondary_insurance_id", "secondary_group_number",
               "tertiary_insurance_name", "tertiary_insurance_id", "tertiary_group_number"]
    for k, v in data.items():
        if k in allowed:
            setattr(p, k, v)
    db.commit()
    log_action(db, "UPDATE", "patient", resource_id=patient_id, patient_id=patient_id, new_values=data)
    return _to_dict(p)


@router.get("/{patient_id}/ledger")
def get_ledger(
    patient_id: str,
    window_years: int = 5,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Return the patient ledger.
    `window_years` defaults to 5; pass 0 for full history."""
    from app.services.ledger_service import get_patient_ledger
    ledger = get_patient_ledger(db, patient_id, window_years=window_years or None)
    if not ledger:
        raise HTTPException(status_code=404, detail="Patient not found")
    email = (current_user.get("email") or "").lower().strip() or None
    log_action(db, "VIEW", "ledger", resource_id=patient_id, patient_id=patient_id,
               user_id=email, user_name=current_user.get("name") or email,
               description="Patient ledger viewed")
    return ledger


@router.get("/{patient_id}/ledger/pdf")
def get_ledger_pdf(
    patient_id: str,
    window_years: int = 5,
    visit_id: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Generate a PDF: full ledger by default, single-visit statement if
    `visit_id` is provided."""
    from app.services.ledger_service import get_patient_ledger
    from app.services.patient_ledger_pdf import (
        generate_full_ledger_pdf, generate_visit_statement_pdf,
    )
    from fastapi.responses import Response

    ledger = get_patient_ledger(db, patient_id, window_years=window_years or None)
    if not ledger:
        raise HTTPException(status_code=404, detail="Patient not found")

    if visit_id:
        pdf_bytes = generate_visit_statement_pdf(ledger, visit_id)
        kind = "statement"
        log_desc = f"Visit statement PDF generated for visit {visit_id}"
        filename_suffix = f"-visit-{visit_id}"
    else:
        pdf_bytes = generate_full_ledger_pdf(ledger)
        kind = "ledger"
        log_desc = f"Full ledger PDF generated ({window_years}y window)"
        filename_suffix = ""

    email = (current_user.get("email") or "").lower().strip() or None
    log_action(
        db, "EXPORT", kind, resource_id=patient_id, patient_id=patient_id,
        user_id=email, user_name=current_user.get("name") or email,
        description=log_desc,
    )

    chart = (ledger.get("patient") or {}).get("patient_id") or "unknown"
    fname = f"WWC-{kind}-chart{chart}{filename_suffix}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


def _to_dict(p: Patient) -> dict:
    return {
        "id": str(p.id),
        "patient_id": p.patient_id,
        "first_name": p.first_name,
        "last_name": p.last_name,
        "full_name": p.full_name,
        "date_of_birth": str(p.date_of_birth) if p.date_of_birth else None,
        "address": p.address,
        "phone": p.phone,
        "email": p.email,
        "primary_insurance_name": p.primary_insurance_name,
        "primary_insurance_id": p.primary_insurance_id,
        "primary_group_number": p.primary_group_number,
        "secondary_insurance_name": p.secondary_insurance_name,
        "secondary_insurance_id": p.secondary_insurance_id,
        "secondary_group_number": p.secondary_group_number,
        "tertiary_insurance_name": p.tertiary_insurance_name,
        "tertiary_insurance_id": p.tertiary_insurance_id,
        "tertiary_group_number": p.tertiary_group_number,
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }
