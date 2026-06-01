"""Portal P6 schema — surgery_messages + message_templates."""
from datetime import datetime
from app.models.surgery import Surgery
from app.models.surgery_message import SurgeryMessage, MessageTemplate


def test_surgery_message_round_trip(db):
    s = Surgery(chart_number="1", patient_name="Pat", status="new")
    db.add(s); db.commit(); db.refresh(s)
    m = SurgeryMessage(
        surgery_id=s.id,
        author_kind="patient",
        body="Hi, when can I eat before surgery?",
    )
    db.add(m); db.commit(); db.refresh(m)
    assert m.surgery_id == s.id
    assert m.author_kind == "patient"
    assert m.author_email is None
    assert m.read_by_patient_at is None
    assert m.read_by_staff_at is None
    assert m.sent_at is not None


def test_surgery_message_staff_author_records_email(db):
    s = Surgery(chart_number="1", patient_name="Pat", status="new")
    db.add(s); db.commit(); db.refresh(s)
    m = SurgeryMessage(
        surgery_id=s.id,
        author_kind="staff",
        author_email="ocooke@example.com",
        body="Clear liquids OK until 2 hours before.",
    )
    db.add(m); db.commit(); db.refresh(m)
    assert m.author_email == "ocooke@example.com"


def test_message_template_round_trip(db):
    t = MessageTemplate(
        name="Eating/drinking",
        body="Hi {{patient_name}}, you can have clear liquids until "
             "2 hours before your {{surgery_date}} surgery.",
    )
    db.add(t); db.commit(); db.refresh(t)
    assert t.id is not None
    assert "{{patient_name}}" in t.body
    assert t.created_at is not None
    assert t.updated_at is not None
