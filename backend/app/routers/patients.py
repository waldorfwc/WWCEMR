from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import or_, func, desc
from typing import Optional
from pydantic import BaseModel
from datetime import date

from app.database import get_db
from app.models.patient import Patient
from app.services.audit_service import log_action

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
def create_patient(data: PatientCreate, db: Session = Depends(get_db)):
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
def get_patient(patient_id: str, db: Session = Depends(get_db)):
    p = db.query(Patient).filter(Patient.id == patient_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Patient not found")
    log_action(db, "VIEW", "patient", resource_id=patient_id, patient_id=patient_id)
    return _to_dict(p)


@router.patch("/{patient_id}")
def update_patient(patient_id: str, data: dict, db: Session = Depends(get_db)):
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
def get_ledger(patient_id: str, db: Session = Depends(get_db)):
    from app.services.ledger_service import get_patient_ledger
    ledger = get_patient_ledger(db, patient_id)
    if not ledger:
        raise HTTPException(status_code=404, detail="Patient not found")
    log_action(db, "VIEW", "ledger", resource_id=patient_id, patient_id=patient_id,
               description="Patient ledger viewed")
    return ledger


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
