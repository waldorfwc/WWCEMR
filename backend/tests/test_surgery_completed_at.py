"""Surgery.completed_at is stamped when status becomes 'completed' via PATCH,
and cleared if the case is reopened. `client` is the super-admin fixture."""
from app.models.surgery import Surgery


def _surg(db, **kw):
    base = dict(chart_number="MC1", patient_name="Doe, J", status="confirmed",
                surgeon_primary="Cooke, Aryian, MD", selected_facility="medstar")
    base.update(kw); s = Surgery(**base); db.add(s); db.commit(); db.refresh(s)
    return s


def test_completed_at_stamped_on_complete(client, db):
    s = _surg(db, status="confirmed")
    assert s.completed_at is None
    r = client.patch(f"/api/surgery/{s.id}", json={"status": "completed"})
    assert r.status_code == 200, r.text
    db.refresh(s)
    assert s.completed_at is not None


def test_completed_at_cleared_on_reopen(client, db):
    s = _surg(db, status="completed")
    from app.utils.dt import now_utc_naive
    s.completed_at = now_utc_naive(); db.commit()
    r = client.patch(f"/api/surgery/{s.id}", json={"status": "confirmed"})
    assert r.status_code == 200, r.text
    db.refresh(s)
    assert s.completed_at is None
