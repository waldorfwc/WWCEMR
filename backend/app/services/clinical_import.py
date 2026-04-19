"""
Import PrimeSuite clinical data from text exports.
Each file is pipe or semicolon delimited.
"""

import csv
import os
from datetime import datetime
from typing import Optional
from sqlalchemy.orm import Session

from app.models.clinical import (
    MedicalHistory, SurgicalHistory, FamilyHistory, SocialHistory,
    Medication, Allergy, ProblemList, Vital, InsuranceCoverage,
)

EXPORT_DIR = "/Volumes/OWC External/400387"


def _parse_dt(raw: Optional[str]) -> Optional[datetime]:
    if not raw or "1899" in raw:
        return None
    for fmt in ["%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]:
        try:
            return datetime.strptime(raw.strip(), fmt)
        except ValueError:
            continue
    return None


def _safe_int(val: str) -> Optional[int]:
    try:
        return int(float(val)) if val and val.strip() else None
    except (ValueError, TypeError):
        return None


def _safe_float(val: str) -> Optional[float]:
    try:
        return float(val) if val and val.strip() else None
    except (ValueError, TypeError):
        return None


def import_all_clinical(db: Session) -> dict:
    """Import all clinical data tables from the PrimeSuite export."""
    stats = {}

    stats["medical_history"] = _import_medical_history(db)
    stats["surgical_history"] = _import_surgical_history(db)
    stats["family_history"] = _import_family_history(db)
    stats["social_history"] = _import_social_history(db)
    stats["medications"] = _import_medications(db)
    stats["allergies"] = _import_allergies(db)
    stats["problem_list"] = _import_problem_list(db)
    stats["vitals"] = _import_vitals(db)
    stats["insurance"] = _import_insurance(db)

    return stats


def _import_medical_history(db: Session) -> int:
    db.query(MedicalHistory).delete()
    db.commit()
    path = os.path.join(EXPORT_DIR, "PatHistMedical.txt")
    if not os.path.isfile(path):
        return 0
    batch, count = [], 0
    with open(path, "r", errors="replace") as f:
        for row in csv.DictReader(f, delimiter="|"):
            batch.append(MedicalHistory(
                patient_id=row.get("PatientID", "").strip(),
                description=row.get("Description", "").strip(),
                category=row.get("Category", "").strip(),
                date_of_onset=row.get("DateOfOnset", "").strip(),
                icd10_code=row.get("ICD10_Code", "").strip(),
                note=row.get("PMHNote", "").strip(),
                create_date=_parse_dt(row.get("CreateDate")),
            ))
            count += 1
            if len(batch) >= 1000:
                db.bulk_save_objects(batch)
                db.commit()
                batch = []
    if batch:
        db.bulk_save_objects(batch)
        db.commit()
    return count


def _import_surgical_history(db: Session) -> int:
    db.query(SurgicalHistory).delete()
    db.commit()
    path = os.path.join(EXPORT_DIR, "PatHistSurgical.txt")
    if not os.path.isfile(path):
        return 0
    batch, count = [], 0
    with open(path, "r", errors="replace") as f:
        for row in csv.DictReader(f, delimiter="|"):
            batch.append(SurgicalHistory(
                patient_id=row.get("PatientID", "").strip(),
                description=row.get("Description", "").strip(),
                category=row.get("Category", "").strip(),
                date_of_procedure=row.get("DateOfProcedure", "").strip(),
                icd10_code=row.get("ICD10_Code", "").strip(),
                note=row.get("PSHNote", "").strip(),
                create_date=_parse_dt(row.get("CreateDate")),
            ))
            count += 1
            if len(batch) >= 1000:
                db.bulk_save_objects(batch)
                db.commit()
                batch = []
    if batch:
        db.bulk_save_objects(batch)
        db.commit()
    return count


def _import_family_history(db: Session) -> int:
    db.query(FamilyHistory).delete()
    db.commit()
    path = os.path.join(EXPORT_DIR, "PatHistFamily.txt")
    if not os.path.isfile(path):
        return 0
    batch, count = [], 0
    with open(path, "r", errors="replace") as f:
        for row in csv.DictReader(f, delimiter="|"):
            batch.append(FamilyHistory(
                patient_id=row.get("PatientID", "").strip(),
                description=row.get("Description", "").strip(),
                category=row.get("Category", "").strip(),
                relation=row.get("Relation", "").strip(),
                age_of_onset=row.get("AgeOfOnset", "").strip(),
                icd10_code=row.get("ICD10_Code", "").strip(),
                note=row.get("PFHNote", "").strip(),
                create_date=_parse_dt(row.get("CreateDate")),
            ))
            count += 1
            if len(batch) >= 1000:
                db.bulk_save_objects(batch)
                db.commit()
                batch = []
    if batch:
        db.bulk_save_objects(batch)
        db.commit()
    return count


def _import_social_history(db: Session) -> int:
    db.query(SocialHistory).delete()
    db.commit()
    path = os.path.join(EXPORT_DIR, "PatHistSocial.txt")
    if not os.path.isfile(path):
        return 0
    batch, count = [], 0
    with open(path, "r", errors="replace") as f:
        for row in csv.DictReader(f, delimiter="|"):
            batch.append(SocialHistory(
                patient_id=row.get("PatientID", "").strip(),
                description=row.get("Description", "").strip(),
                category=row.get("Category", "").strip(),
                quantity=row.get("Quantity", "").strip(),
                age_start=row.get("AgeStart", "").strip(),
                age_stop=row.get("AgeStop", "").strip(),
                note=row.get("PHSNote", "").strip(),
                screening_date=row.get("ScreeningDate", "").strip(),
            ))
            count += 1
            if len(batch) >= 1000:
                db.bulk_save_objects(batch)
                db.commit()
                batch = []
    if batch:
        db.bulk_save_objects(batch)
        db.commit()
    return count


def _import_medications(db: Session) -> int:
    db.query(Medication).delete()
    db.commit()
    path = os.path.join(EXPORT_DIR, "Medications.txt")
    if not os.path.isfile(path):
        return 0
    batch, count = [], 0
    with open(path, "r", errors="replace") as f:
        for row in csv.DictReader(f, delimiter="|"):
            batch.append(Medication(
                patient_id=row.get("PatientID", "").strip(),
                medication_name=row.get("MedicationName", "").strip(),
                strength=row.get("MedicationStrength", "").strip(),
                strength_unit=row.get("MedicationStrengthUnit", "").strip(),
                dose_form=row.get("DoseForm", "").strip(),
                dose_quantity=row.get("DoseQuantity", "").strip(),
                route=row.get("Route", "").strip(),
                frequency=row.get("Frequency", "").strip(),
                sig=row.get("SIG", "").strip(),
                start_date=row.get("StartDateText", "").strip(),
                status_id=row.get("StatusID", "").strip(),
                enabled=row.get("Enable", "1").strip() == "1",
                create_date=_parse_dt(row.get("CreateDate")),
            ))
            count += 1
            if len(batch) >= 1000:
                db.bulk_save_objects(batch)
                db.commit()
                batch = []
    if batch:
        db.bulk_save_objects(batch)
        db.commit()
    return count


def _import_allergies(db: Session) -> int:
    db.query(Allergy).delete()
    db.commit()
    path = os.path.join(EXPORT_DIR, "Allergy.txt")
    if not os.path.isfile(path):
        return 0
    batch, count = [], 0
    with open(path, "r", errors="replace") as f:
        for row in csv.DictReader(f, delimiter="|"):
            batch.append(Allergy(
                patient_id=row.get("PatientID", "").strip(),
                allergy=row.get("Allergy", "").strip(),
                reaction=row.get("Reaction", "").strip(),
                category=row.get("AllergyCategory", "").strip(),
                notes=row.get("Notes", "").strip(),
                enabled=row.get("Enabled", "1").strip() == "1",
                create_date=_parse_dt(row.get("Createdate")),
            ))
            count += 1
            if len(batch) >= 1000:
                db.bulk_save_objects(batch)
                db.commit()
                batch = []
    if batch:
        db.bulk_save_objects(batch)
        db.commit()
    return count


def _import_problem_list(db: Session) -> int:
    db.query(ProblemList).delete()
    db.commit()
    path = os.path.join(EXPORT_DIR, "ProblemList.txt")
    if not os.path.isfile(path):
        return 0
    batch, count = [], 0
    with open(path, "r", errors="replace") as f:
        for row in csv.DictReader(f, delimiter="|"):
            batch.append(ProblemList(
                patient_id=row.get("PatientID", "").strip(),
                description=row.get("Description", "").strip(),
                category=row.get("Category", "").strip(),
                icd10_code=row.get("ICD10_Code", "").strip(),
                date_resolved=row.get("DateResolved", "").strip(),
                note=row.get("ProblemNote", "").strip(),
            ))
            count += 1
            if len(batch) >= 500:
                db.bulk_save_objects(batch)
                db.commit()
                batch = []
    if batch:
        db.bulk_save_objects(batch)
        db.commit()
    return count


def _import_vitals(db: Session) -> int:
    db.query(Vital).delete()
    db.commit()
    path = os.path.join(EXPORT_DIR, "ClinicalVital.txt")
    if not os.path.isfile(path):
        return 0
    batch, count = [], 0
    with open(path, "r", errors="replace") as f:
        for row in csv.DictReader(f, delimiter="|"):
            batch.append(Vital(
                patient_id=row.get("PatientID", "").strip(),
                date_taken=_parse_dt(row.get("DateTimeTaken")),
                height_cm=_safe_float(row.get("Height_cm")),
                weight_grams=_safe_float(row.get("Weight_grams")),
                systolic_bp=_safe_int(row.get("SystolicBP")),
                diastolic_bp=_safe_int(row.get("DiastolicBP")),
                heart_rate=_safe_int(row.get("HeartRate")),
                resp_rate=_safe_int(row.get("RespRate")),
                temperature_c=_safe_float(row.get("Temperature_Celcius")),
                spo2_pct=_safe_float(row.get("Spo2Percent")),
                bp_position=row.get("BPPosition", "").strip(),
            ))
            count += 1
            if len(batch) >= 1000:
                db.bulk_save_objects(batch)
                db.commit()
                batch = []
    if batch:
        db.bulk_save_objects(batch)
        db.commit()
    return count


def _import_insurance(db: Session) -> int:
    db.query(InsuranceCoverage).delete()
    db.commit()

    # Load plan names
    plans = {}
    plan_path = os.path.join(EXPORT_DIR, "InsurancePlan.txt")
    if os.path.isfile(plan_path):
        with open(plan_path, "r", errors="replace") as f:
            for row in csv.DictReader(f, delimiter="|"):
                pid = row.get("PlanID", "").strip()
                name = row.get("PlanName", "").strip()
                if pid:
                    plans[pid] = name

    path = os.path.join(EXPORT_DIR, "InsCoveragePatient.txt")
    if not os.path.isfile(path):
        return 0
    batch, count = [], 0
    with open(path, "r", errors="replace") as f:
        for row in csv.DictReader(f, delimiter="|"):
            plan_id = row.get("PlanID", "").strip()
            batch.append(InsuranceCoverage(
                patient_id=row.get("PatientID", "").strip(),
                plan_name=plans.get(plan_id, plan_id),
                group_number=row.get("GroupNumber", "").strip(),
                policy_number=row.get("PolicyNumber", "").strip(),
                subscriber_name=row.get("SubscriberName", "").strip(),
                subscriber_relation=row.get("Relationship", "").strip(),
                effective_date=row.get("EffectiveDate", "").strip(),
                termination_date=row.get("TerminationDate", "").strip(),
                coverage_order=_safe_int(row.get("CoverageOrder")) or 1,
            ))
            count += 1
            if len(batch) >= 500:
                db.bulk_save_objects(batch)
                db.commit()
                batch = []
    if batch:
        db.bulk_save_objects(batch)
        db.commit()
    return count
