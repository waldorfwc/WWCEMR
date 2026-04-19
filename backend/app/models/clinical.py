from sqlalchemy import Column, String, Date, DateTime, Numeric, Integer, Text, Boolean, Index
from datetime import datetime
from app.database import Base
from app.models.guid import GUID, new_uuid


class MedicalHistory(Base):
    __tablename__ = "clinical_medical_history"
    id = Column(GUID(), primary_key=True, default=new_uuid)
    patient_id = Column(String(20), nullable=False, index=True)
    description = Column(String(300))
    category = Column(String(100))
    date_of_onset = Column(String(50))
    icd10_code = Column(String(20))
    note = Column(Text)
    create_date = Column(DateTime)


class SurgicalHistory(Base):
    __tablename__ = "clinical_surgical_history"
    id = Column(GUID(), primary_key=True, default=new_uuid)
    patient_id = Column(String(20), nullable=False, index=True)
    description = Column(String(300))
    category = Column(String(100))
    date_of_procedure = Column(String(50))
    icd10_code = Column(String(20))
    note = Column(Text)
    create_date = Column(DateTime)


class FamilyHistory(Base):
    __tablename__ = "clinical_family_history"
    id = Column(GUID(), primary_key=True, default=new_uuid)
    patient_id = Column(String(20), nullable=False, index=True)
    description = Column(String(300))
    category = Column(String(100))
    relation = Column(String(50))
    age_of_onset = Column(String(30))
    icd10_code = Column(String(20))
    note = Column(Text)
    create_date = Column(DateTime)


class SocialHistory(Base):
    __tablename__ = "clinical_social_history"
    id = Column(GUID(), primary_key=True, default=new_uuid)
    patient_id = Column(String(20), nullable=False, index=True)
    description = Column(String(300))
    category = Column(String(100))
    quantity = Column(String(50))
    age_start = Column(String(30))
    age_stop = Column(String(30))
    note = Column(Text)
    screening_date = Column(String(50))


class Medication(Base):
    __tablename__ = "clinical_medications"
    id = Column(GUID(), primary_key=True, default=new_uuid)
    patient_id = Column(String(20), nullable=False, index=True)
    medication_name = Column(String(300))
    strength = Column(String(100))
    strength_unit = Column(String(30))
    dose_form = Column(String(50))
    dose_quantity = Column(String(50))
    route = Column(String(50))
    frequency = Column(String(100))
    sig = Column(Text)
    start_date = Column(String(50))
    status_id = Column(String(10))
    enabled = Column(Boolean, default=True)
    create_date = Column(DateTime)


class Allergy(Base):
    __tablename__ = "clinical_allergies"
    id = Column(GUID(), primary_key=True, default=new_uuid)
    patient_id = Column(String(20), nullable=False, index=True)
    allergy = Column(String(300))
    reaction = Column(String(300))
    category = Column(String(100))
    notes = Column(Text)
    enabled = Column(Boolean, default=True)
    create_date = Column(DateTime)


class ProblemList(Base):
    __tablename__ = "clinical_problem_list"
    id = Column(GUID(), primary_key=True, default=new_uuid)
    patient_id = Column(String(20), nullable=False, index=True)
    description = Column(String(300))
    category = Column(String(100))
    icd10_code = Column(String(20))
    date_resolved = Column(String(50))
    note = Column(Text)


class Vital(Base):
    __tablename__ = "clinical_vitals"
    id = Column(GUID(), primary_key=True, default=new_uuid)
    patient_id = Column(String(20), nullable=False, index=True)
    date_taken = Column(DateTime, index=True)
    height_cm = Column(Numeric(8, 2))
    weight_grams = Column(Numeric(12, 2))
    systolic_bp = Column(Integer)
    diastolic_bp = Column(Integer)
    heart_rate = Column(Integer)
    resp_rate = Column(Integer)
    temperature_c = Column(Numeric(5, 2))
    spo2_pct = Column(Numeric(5, 2))
    bp_position = Column(String(30))


class InsuranceCoverage(Base):
    __tablename__ = "clinical_insurance"
    id = Column(GUID(), primary_key=True, default=new_uuid)
    patient_id = Column(String(20), nullable=False, index=True)
    plan_name = Column(String(200))
    group_number = Column(String(50))
    policy_number = Column(String(50))
    subscriber_name = Column(String(200))
    subscriber_relation = Column(String(50))
    effective_date = Column(String(50))
    termination_date = Column(String(50))
    coverage_order = Column(Integer, default=1)
