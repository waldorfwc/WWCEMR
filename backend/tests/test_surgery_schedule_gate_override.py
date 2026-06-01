"""Staff endpoint for flipping schedule_gate_override."""
from app.models.surgery import Surgery


def test_override_flag_starts_false(db):
    s = Surgery(chart_number="1", patient_name="Pat", status="new")
    db.add(s); db.commit(); db.refresh(s)
    assert s.schedule_gate_override is False


def test_staff_can_enable_override(client, db):
    s = Surgery(chart_number="1", patient_name="Pat", status="new")
    db.add(s); db.commit(); db.refresh(s)
    r = client.patch(f"/api/surgery/{s.id}/schedule-gate-override",
                     json={"enabled": True})
    assert r.status_code == 200, r.text
    db.refresh(s)
    assert s.schedule_gate_override is True
    assert s.schedule_gate_override_at is not None
    assert s.schedule_gate_override_by is not None


def test_staff_can_disable_override(client, db):
    s = Surgery(chart_number="1", patient_name="Pat",
                status="new", schedule_gate_override=True)
    db.add(s); db.commit(); db.refresh(s)
    r = client.patch(f"/api/surgery/{s.id}/schedule-gate-override",
                     json={"enabled": False})
    assert r.status_code == 200
    db.refresh(s)
    assert s.schedule_gate_override is False
    assert s.schedule_gate_override_at is not None
    assert s.schedule_gate_override_by is not None


def test_unknown_surgery_returns_404(client, db):
    r = client.patch("/api/surgery/00000000-0000-0000-0000-000000000000/schedule-gate-override",
                     json={"enabled": True})
    assert r.status_code == 404
