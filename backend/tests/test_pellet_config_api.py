"""P3: validated GET/PUT /pellets/config endpoints + P2 cfg threading parity."""
from datetime import date, timedelta

from app.models.pellet import (
    PelletDoseType, PelletPatient, PelletVisit, PelletVisitDose,
)
from app.services.pellet.stale_sweep import sweep_stale_visits


def test_get_pellet_config_returns_defaults(client):
    r = client.get("/api/pellets/config")
    assert r.status_code == 200
    assert r.json()["stale_visit_days"] == 7


def test_put_pellet_config_roundtrips(client):
    assert client.put("/api/pellets/config", json={"stale_visit_days": 14}).status_code == 200
    assert client.get("/api/pellets/config").json()["stale_visit_days"] == 14


def test_put_pellet_config_rejects_out_of_range(client):
    assert client.put("/api/pellets/config", json={"stale_visit_days": 0}).status_code == 422
    assert client.put("/api/pellets/config", json={"stale_visit_days": 99999}).status_code == 422
    assert client.put("/api/pellets/config", json={"dose_suggest_max_pellets": 99}).status_code == 422


def test_stale_visit_days_override_changes_sweep(client, db):
    """P2 parity: a visit 10 days stale is NOT swept at the default 7-day
    cutoff is — wait, 10 > 7 so it IS swept by default. Use an 8-day-stale
    visit and raise the cutoff to 30 so it is NOT swept after the override."""
    from app.models.pellet_config import PelletConfig

    dt = PelletDoseType(hormone="estradiol", dose_mg=12.5, label="Estradiol 12.5mg")
    db.add(dt); db.flush()
    p = PelletPatient(patient_name="Test Stale", chart_number="STALE-1")
    db.add(p); db.flush()
    v = PelletVisit(patient_id=p.id, status="new",
                    scheduled_date=date.today() - timedelta(days=8))
    db.add(v); db.flush()
    d = PelletVisitDose(visit_id=v.id, dose_type_id=dt.id, status="planned", quantity=1)
    db.add(d); db.commit()
    vid = v.id

    # Default cutoff = 7 days -> 8-day-stale visit IS cancelled.
    res = sweep_stale_visits(db)
    assert res["visits_cancelled"] == 1

    # Reset and override the window to 30 days -> same visit is NOT stale.
    v2 = db.query(PelletVisit).get(vid)
    v2.status = "new"; v2.outcome = None
    d2 = db.query(PelletVisitDose).filter(PelletVisitDose.visit_id == vid).first()
    d2.status = "planned"; d2.resolved_at = None
    db.add(PelletConfig(key="stale_visit_days", value=30))
    db.commit()

    res2 = sweep_stale_visits(db)
    assert res2["visits_cancelled"] == 0
