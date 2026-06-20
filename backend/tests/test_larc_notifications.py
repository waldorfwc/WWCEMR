from app.models.larc import LarcAssignment, LarcDeviceType
from app.models.patient_email import PatientEmail
from app.models.patient_sms import PatientSms
from app.services.larc.notifications import notify_larc_step


def _a(db, sms=False, cell="240-555-0001", email="p@example.com"):
    dt = LarcDeviceType(name="Mirena", category="larc", default_flow="pharmacy_order", is_active=True)
    db.add(dt); db.commit(); db.refresh(dt)
    a = LarcAssignment(chart_number="N1", patient_name="Doe, J", device_type_id=dt.id,
                       source_flow="pharmacy_order", status="new",
                       patient_email=email, patient_cell=cell, sms_consent=sms)
    db.add(a); db.commit(); db.refresh(a)
    return a


def test_notify_emails_always(db):
    a = _a(db, sms=False)
    notify_larc_step(db, a, "enrollment_completed")
    assert db.query(PatientEmail).filter(PatientEmail.larc_assignment_id == a.id).count() == 1
    assert db.query(PatientSms).filter(PatientSms.larc_assignment_id == a.id).count() == 0


def test_notify_texts_when_opted_in(db):
    a = _a(db, sms=True)
    notify_larc_step(db, a, "enrollment_completed")
    assert db.query(PatientSms).filter(PatientSms.larc_assignment_id == a.id).count() == 1


def test_notify_idempotent_per_step(db):
    a = _a(db, sms=False)
    notify_larc_step(db, a, "enrollment_completed")
    notify_larc_step(db, a, "enrollment_completed")
    assert db.query(PatientEmail).filter(PatientEmail.larc_assignment_id == a.id).count() == 1


def test_notify_unknown_step_noops(db):
    a = _a(db, sms=False)
    notify_larc_step(db, a, "request_received")   # not a notify step
    assert db.query(PatientEmail).count() == 0
