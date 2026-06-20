"""Start-LARC-Process create-assignment behavior: reason + provider capture."""
from app.models.larc import LarcAssignment, LarcDeviceType


def _dt(db, name="Mirena", default_flow="pharmacy_order"):
    dt = LarcDeviceType(name=name, category="larc",
                        default_flow=default_flow, is_active=True)
    db.add(dt); db.commit(); db.refresh(dt)
    return dt


def test_assignment_model_has_reason_columns(db):
    dt = _dt(db)
    a = LarcAssignment(chart_number="MRN1", patient_name="Doe, Jane",
                       device_type_id=dt.id, source_flow="pharmacy_order",
                       status="new", reason_for_request="Contraception",
                       reason_icd10="Z30.430")
    db.add(a); db.commit(); db.refresh(a)
    assert a.reason_for_request == "Contraception"
    assert a.reason_icd10 == "Z30.430"


def test_create_persists_reason_and_provider(client, db):
    dt = _dt(db)
    r = client.post("/api/larc/assignments", json={
        "chart_number": "MRN2",
        "patient_name": "Roe, Mary",
        "patient_first_name": "Mary", "patient_last_name": "Roe",
        "device_type_id": str(dt.id),
        "source_flow": "pharmacy_order",
        "reason_for_request": "Contraception",
        "reason_icd10": "Z30.430",
        "requested_by_provider": "Aryian Cooke, MD",
        "inserting_provider_email": "acooke@waldorfwomenscare.com",
        "inserting_provider_name": "Aryian Cooke, MD",
        "inserting_provider_npi": "1234567890",
    })
    assert r.status_code == 201, r.text
    aid = r.json()["id"]
    a = db.query(LarcAssignment).filter(LarcAssignment.id == aid).first()
    assert a.reason_for_request == "Contraception"
    assert a.reason_icd10 == "Z30.430"
    assert a.requested_by_provider == "Aryian Cooke, MD"
    assert a.inserting_provider_email == "acooke@waldorfwomenscare.com"
    assert a.inserting_provider_npi == "1234567890"


def test_create_accepts_office_procedure(client, db):
    dt = _dt(db, name="NovaSure", default_flow="office_procedure")
    r = client.post("/api/larc/assignments", json={
        "chart_number": "MRN3",
        "patient_name": "Poe, Edna",
        "patient_first_name": "Edna", "patient_last_name": "Poe",
        "device_type_id": str(dt.id),
        "source_flow": "office_procedure",
        "reason_for_request": "Menorrhagia",
        "reason_icd10": "N92.0",
        "requested_by_provider": "Aryian Cooke, MD",
    })
    assert r.status_code == 201, r.text
    a = db.query(LarcAssignment).filter(
        LarcAssignment.id == r.json()["id"]).first()
    assert a.source_flow == "office_procedure"


def test_assignment_dict_echoes_reason(client, db):
    dt = _dt(db)
    aid = client.post("/api/larc/assignments", json={
        "chart_number": "MRN4", "patient_name": "Foe, Ann",
        "patient_first_name": "Ann", "patient_last_name": "Foe",
        "device_type_id": str(dt.id), "source_flow": "pharmacy_order",
        "reason_for_request": "Contraception", "reason_icd10": "Z30.430",
        "requested_by_provider": "Aryian Cooke, MD",
    }).json()["id"]
    got = client.get(f"/api/larc/assignments/{aid}").json()
    assert got["reason_for_request"] == "Contraception"
    assert got["reason_icd10"] == "Z30.430"
