from app.models.patient_email import PatientEmail
from app.models.patient_sms import PatientSms


def test_patient_email_has_larc_assignment_id(db):
    e = PatientEmail(to_email="p@example.com", status="sent",
                     rendered_subject="hi", rendered_html="<p>hi</p>",
                     larc_assignment_id="11111111-1111-1111-1111-111111111111")
    db.add(e); db.commit(); db.refresh(e)
    assert e.larc_assignment_id == "11111111-1111-1111-1111-111111111111"


def test_patient_sms_has_larc_assignment_id(db):
    s = PatientSms(to_phone="+12405550001", status="sent",
                   rendered_body="hi",
                   larc_assignment_id="22222222-2222-2222-2222-222222222222")
    db.add(s); db.commit(); db.refresh(s)
    assert s.larc_assignment_id == "22222222-2222-2222-2222-222222222222"
