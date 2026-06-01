"""Patient portal P2 schema."""
from app.models.surgery import Surgery


def test_surgery_has_schedule_gate_override(db):
    s = Surgery(chart_number="1", patient_name="Pat", status="new")
    db.add(s); db.commit(); db.refresh(s)
    assert s.schedule_gate_override is False
    assert s.schedule_gate_override_at is None
    assert s.schedule_gate_override_by is None
