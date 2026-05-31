"""Patient portal P1 schema."""
from datetime import datetime, timedelta

from app.models.surgery import Surgery
from app.models.patient_portal import PatientPortalAuthCode


def test_surgery_has_self_report_flags(db):
    s = Surgery(chart_number="1", patient_name="Pat", status="new")
    db.add(s); db.commit(); db.refresh(s)
    assert s.labs_self_reported is False
    assert s.labs_self_reported_at is None
    assert s.hospital_preop_self_reported is False
    assert s.hospital_preop_self_reported_at is None


def test_portal_auth_code_persists(db):
    s = Surgery(chart_number="2", patient_name="Pat", status="new")
    db.add(s); db.commit(); db.refresh(s)
    row = PatientPortalAuthCode(
        surgery_id=s.id,
        challenge_token="ch_abc",
        code_hash="$2b$12$placeholder",
        expires_at=datetime.utcnow() + timedelta(minutes=5),
        sent_to_phone="+12405551234",
    )
    db.add(row); db.commit(); db.refresh(row)
    assert row.fail_count == 0
    assert row.used_at is None
