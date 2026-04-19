"""
Patient chart — comprehensive view of a patient's clinical data.
Pulls demographics, history, medications, allergies, vitals, insurance, and documents.
"""

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import func, desc

from app.database import get_db, SessionLocal
from app.models.patient_directory import PatientDirectory
from app.models.clinical import (
    MedicalHistory, SurgicalHistory, FamilyHistory, SocialHistory,
    Medication, Allergy, ProblemList, Vital, InsuranceCoverage,
)
from app.models.document import PatientDocument
from app.models.patient_directory import IntakeDocument
from app.services.audit_service import log_action

router = APIRouter(prefix="/chart", tags=["chart"])


@router.get("/{chart_number}")
def get_chart(chart_number: str, db: Session = Depends(get_db)):
    """Full patient chart for a given chart number."""
    # Demographics
    patient = db.query(PatientDirectory).filter(
        PatientDirectory.chart_number == chart_number
    ).first()
    if not patient:
        raise HTTPException(status_code=404, detail=f"Patient {chart_number} not found")

    log_action(db, "VIEW", "patient_chart", resource_id=chart_number,
               description=f"Viewed chart for {patient.patient_name}")

    # Medical History
    pmh = db.query(MedicalHistory).filter(
        MedicalHistory.patient_id == chart_number
    ).order_by(MedicalHistory.category).all()

    # Surgical History
    psh = db.query(SurgicalHistory).filter(
        SurgicalHistory.patient_id == chart_number
    ).order_by(SurgicalHistory.category).all()

    # Family History
    fhx = db.query(FamilyHistory).filter(
        FamilyHistory.patient_id == chart_number
    ).order_by(FamilyHistory.relation).all()

    # Social History
    shx = db.query(SocialHistory).filter(
        SocialHistory.patient_id == chart_number
    ).order_by(SocialHistory.category).all()

    # Medications
    meds = db.query(Medication).filter(
        Medication.patient_id == chart_number
    ).order_by(desc(Medication.create_date)).all()

    # Allergies
    allergies = db.query(Allergy).filter(
        Allergy.patient_id == chart_number
    ).all()

    # Problem List
    problems = db.query(ProblemList).filter(
        ProblemList.patient_id == chart_number
    ).all()

    # Recent Vitals (last 20)
    vitals = db.query(Vital).filter(
        Vital.patient_id == chart_number
    ).order_by(desc(Vital.date_taken)).limit(20).all()

    # Insurance
    insurance = db.query(InsuranceCoverage).filter(
        InsuranceCoverage.patient_id == chart_number
    ).order_by(InsuranceCoverage.coverage_order).all()

    # Document count
    doc_count = db.query(func.count(PatientDocument.id)).filter(
        PatientDocument.chart_number == chart_number
    ).scalar() or 0

    # Intake documents (ID cards, insurance cards, agreements, etc.)
    intake_docs = db.query(IntakeDocument).filter(
        IntakeDocument.matched_chart_number == chart_number
    ).order_by(IntakeDocument.doc_category, IntakeDocument.dob).all()

    return {
        "demographics": {
            "chart_number": patient.chart_number,
            "patient_name": patient.patient_name,
            "first_name": patient.first_name,
            "last_name": patient.last_name,
            "middle_name": patient.middle_name,
            "dob": str(patient.dob) if patient.dob else None,
            "gender": patient.gender,
            "address": patient.address,
            "phone": patient.phone,
            "email": patient.email,
        },
        "medical_history": [
            {"description": r.description, "category": r.category,
             "date_of_onset": r.date_of_onset, "icd10": r.icd10_code, "note": r.note}
            for r in pmh
        ],
        "surgical_history": [
            {"description": r.description, "category": r.category,
             "date": r.date_of_procedure, "icd10": r.icd10_code, "note": r.note}
            for r in psh
        ],
        "family_history": [
            {"description": r.description, "category": r.category,
             "relation": r.relation, "age_of_onset": r.age_of_onset, "icd10": r.icd10_code}
            for r in fhx
        ],
        "social_history": [
            {"description": r.description, "category": r.category,
             "quantity": r.quantity, "note": r.note}
            for r in shx
        ],
        "medications": [
            {"name": r.medication_name, "strength": r.strength, "unit": r.strength_unit,
             "dose_form": r.dose_form, "frequency": r.frequency, "sig": r.sig,
             "start_date": r.start_date, "active": r.enabled}
            for r in meds
        ],
        "allergies": [
            {"allergy": r.allergy, "reaction": r.reaction, "category": r.category,
             "notes": r.notes, "active": r.enabled}
            for r in allergies
        ],
        "problem_list": [
            {"description": r.description, "category": r.category,
             "icd10": r.icd10_code, "resolved": r.date_resolved, "note": r.note}
            for r in problems
        ],
        "vitals": [
            {"date": r.date_taken.isoformat() if r.date_taken else None,
             "systolic": r.systolic_bp, "diastolic": r.diastolic_bp,
             "heart_rate": r.heart_rate, "resp_rate": r.resp_rate,
             "height_cm": float(r.height_cm) if r.height_cm else None,
             "weight_kg": round(float(r.weight_grams) / 1000, 1) if r.weight_grams else None,
             "temp_c": float(r.temperature_c) if r.temperature_c else None,
             "spo2": float(r.spo2_pct) if r.spo2_pct else None,
             "bp_position": r.bp_position}
            for r in vitals
        ],
        "insurance": [
            {"plan": r.plan_name, "group": r.group_number, "policy": r.policy_number,
             "subscriber": r.subscriber_name, "relation": r.subscriber_relation,
             "effective": r.effective_date, "terminated": r.termination_date,
             "order": r.coverage_order}
            for r in insurance
        ],
        "document_count": doc_count,
        "intake_documents": [
            {
                "id": str(d.id),
                "patient_name": d.patient_name_raw,
                "doc_category": d.doc_category,
                "doc_year": d.doc_year,
                "filename": d.filename,
                "file_type": d.file_type,
                "file_size_kb": d.file_size_kb,
                "match_confidence": d.match_confidence,
            }
            for d in intake_docs
        ],
    }


@router.post("/import-clinical")
def import_clinical_data(background_tasks: BackgroundTasks):
    """Import all clinical data from the PrimeSuite export on the external drive."""
    import os
    if not os.path.isdir("/Volumes/OWC External/400387"):
        raise HTTPException(status_code=404, detail="External drive not connected or 400387 folder missing")
    background_tasks.add_task(_bg_import_clinical)
    return {"status": "importing"}


def _bg_import_clinical():
    from app.services.clinical_import import import_all_clinical
    db = SessionLocal()
    try:
        result = import_all_clinical(db)
        print(f"[chart] Clinical import result: {result}")
    finally:
        db.close()


@router.get("/import-status/clinical")
def clinical_import_status(db: Session = Depends(get_db)):
    """Check how many records are loaded per table."""
    return {
        "medical_history": db.query(func.count(MedicalHistory.id)).scalar() or 0,
        "surgical_history": db.query(func.count(SurgicalHistory.id)).scalar() or 0,
        "family_history": db.query(func.count(FamilyHistory.id)).scalar() or 0,
        "social_history": db.query(func.count(SocialHistory.id)).scalar() or 0,
        "medications": db.query(func.count(Medication.id)).scalar() or 0,
        "allergies": db.query(func.count(Allergy.id)).scalar() or 0,
        "problem_list": db.query(func.count(ProblemList.id)).scalar() or 0,
        "vitals": db.query(func.count(Vital.id)).scalar() or 0,
        "insurance": db.query(func.count(InsuranceCoverage.id)).scalar() or 0,
    }
