"""Seed top WWC payer appeal addresses.

Addresses below are publicly published payer-appeals addresses as of 2025–26.
Verify before sending — payers update these periodically. Edit in the UI
after seeding.

Usage:
    python scripts/seed_payer_addresses.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal, init_db
from app.models import (patient, claim, payment, denial, appeal, audit, clinical,
                        document, fax_log, guid, import_audit, patient_directory,
                        adjustment_code_reference, payment_analysis, practice_config,
                        user, active_ar, appeal_letters)
from app.models.appeal_letters import PayerAddress


PAYERS = [
    {
        "payer_name": "BCBS Carefirst FEP",
        "payer_id": "SB580",
        "appeals_dept_name": "CareFirst Member Services Appeals",
        "address_line_1": "P.O. Box 14114",
        "city": "Lexington", "state": "KY", "zip_code": "40512",
        "appeals_fax": "410-998-5345",
        "appeals_phone": "800-628-8549",
        "notes": "Federal Employee Program — verify before sending",
    },
    {
        "payer_name": "Carefirst",
        "payer_id": "580",
        "appeals_dept_name": "CareFirst BlueCross BlueShield Appeals",
        "address_line_1": "Mail Administrator",
        "address_line_2": "P.O. Box 14114",
        "city": "Lexington", "state": "KY", "zip_code": "40512",
        "appeals_fax": "410-998-5345",
        "appeals_phone": "800-296-5742",
    },
    {
        "payer_name": "United Health Care",
        "payer_id": "87726",
        "appeals_dept_name": "UnitedHealthcare Appeals & Grievances",
        "address_line_1": "P.O. Box 740800",
        "city": "Atlanta", "state": "GA", "zip_code": "30374",
        "appeals_fax": "866-940-6428",
        "appeals_phone": "800-722-7471",
    },
    {
        "payer_name": "UHC Community Plan",
        "payer_id": "87726",
        "appeals_dept_name": "UnitedHealthcare Community Plan Appeals",
        "address_line_1": "P.O. Box 31364",
        "city": "Salt Lake City", "state": "UT", "zip_code": "84131",
        "appeals_fax": "801-994-1083",
        "appeals_phone": "888-980-8728",
    },
    {
        "payer_name": "UHC UMR",
        "payer_id": "39026",
        "appeals_dept_name": "UMR Appeals Department",
        "address_line_1": "P.O. Box 30546",
        "city": "Salt Lake City", "state": "UT", "zip_code": "84130",
        "appeals_fax": "877-291-3248",
        "appeals_phone": "800-826-9781",
    },
    {
        "payer_name": "Aetna",
        "payer_id": "60054",
        "appeals_dept_name": "Aetna Provider Resolution Team",
        "address_line_1": "P.O. Box 14079",
        "city": "Lexington", "state": "KY", "zip_code": "40512",
        "appeals_fax": "859-455-8650",
        "appeals_phone": "888-632-3862",
    },
    {
        "payer_name": "Cigna",
        "payer_id": "62308",
        "appeals_dept_name": "Cigna Appeals Department",
        "address_line_1": "P.O. Box 188062",
        "city": "Chattanooga", "state": "TN", "zip_code": "37422",
        "appeals_fax": "865-401-7525",
        "appeals_phone": "800-244-6224",
    },
    {
        "payer_name": "Tricare",
        "payer_id": "TDDIR",
        "appeals_dept_name": "Humana Military Appeals (TRICARE East)",
        "address_line_1": "P.O. Box 7032",
        "city": "Camden", "state": "SC", "zip_code": "29021",
        "appeals_fax": "800-457-8506",
        "appeals_phone": "800-444-5445",
        "notes": "TRICARE for Life: Wisconsin Physicians Service (WPS) handles secondary appeals",
    },
    {
        "payer_name": "Medstar Family Choice",
        "payer_id": "RP063",
        "appeals_dept_name": "MedStar Family Choice Provider Appeals",
        "address_line_1": "5233 King Avenue, Suite 400",
        "city": "Baltimore", "state": "MD", "zip_code": "21237",
        "appeals_fax": "410-933-2274",
        "appeals_phone": "800-905-1722",
    },
    {
        "payer_name": "Wellpoint Amerigroup",
        "payer_id": "27514",
        "appeals_dept_name": "Wellpoint (formerly Amerigroup) Provider Disputes",
        "address_line_1": "P.O. Box 62509",
        "city": "Virginia Beach", "state": "VA", "zip_code": "23466",
        "appeals_fax": "877-271-4054",
        "appeals_phone": "800-454-3730",
    },
    {
        "payer_name": "Priority Partners",
        "appeals_dept_name": "Priority Partners Appeals (Johns Hopkins HealthCare)",
        "address_line_1": "P.O. Box 830698",
        "city": "Birmingham", "state": "AL", "zip_code": "35283",
        "appeals_fax": "410-762-5611",
        "appeals_phone": "800-654-9728",
    },
    {
        "payer_name": "Johns Hopkins USFHP",
        "appeals_dept_name": "Johns Hopkins US Family Health Plan Appeals",
        "address_line_1": "P.O. Box 830698",
        "city": "Birmingham", "state": "AL", "zip_code": "35283",
        "appeals_fax": "410-424-2602",
        "appeals_phone": "800-808-7347",
    },
]


def seed():
    init_db()
    db = SessionLocal()
    inserted = 0
    skipped = 0
    for spec in PAYERS:
        existing = db.query(PayerAddress).filter(PayerAddress.payer_name == spec["payer_name"]).first()
        if existing:
            skipped += 1
            continue
        db.add(PayerAddress(**spec))
        inserted += 1
    db.commit()
    print(f"Seeded payer addresses: {inserted} new, {skipped} already-present")
    db.close()


if __name__ == "__main__":
    seed()
