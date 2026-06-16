"""B2: reason-coded labs/mammo readiness driven by configurable windows."""
from datetime import date, timedelta

from app.models.pellet import PelletPatient, PelletVisit


def _seed_patient_with_visit(db, *, chart, sched, **patient_kwargs):
    """Create a patient + an active (future-scheduled) visit. Returns the
    patient id (str) so the list endpoint can be filtered by search."""
    p = PelletPatient(chart_number=chart, patient_name=f"P {chart}",
                      patient_type="established", **patient_kwargs)
    db.add(p); db.flush()
    v = PelletVisit(patient_id=p.id, status="new", scheduled_date=sched)
    db.add(v); db.commit()
    return chart


def _fetch(client, chart):
    r = client.get("/api/pellets/patients", params={"search": chart, "view": "roster"})
    assert r.status_code == 200
    pats = [p for p in r.json()["patients"] if p["chart_number"] == chart]
    assert len(pats) == 1, pats
    return pats[0]


def test_labs_stale_at_default_window(client, db):
    sched = date.today() + timedelta(days=10)
    _seed_patient_with_visit(
        db, chart="RDY-LABS-STALE", sched=sched,
        labs_fsh="40", labs_tsh="2.0", labs_estradiol="30",
        labs_date=sched - timedelta(days=20),
        # make mammo ok so labs is the only failing gate
        mammo_result="BI-RADS 1", mammo_date=sched - timedelta(days=10),
    )
    pat = _fetch(client, "RDY-LABS-STALE")
    assert pat["active_visit_labs_ready"] is False
    assert pat["active_visit_labs_reason"] == "stale"
    assert pat["labs_valid_days"] == 14
    assert pat["mammo_valid_days"] == 365
    assert pat["active_visit_mammo_reason"] == "ok"


def test_labs_ok_after_widening_window(client, db):
    assert client.put("/api/pellets/config", json={"labs_valid_days": 30}).status_code == 200
    sched = date.today() + timedelta(days=10)
    _seed_patient_with_visit(
        db, chart="RDY-LABS-WIDE", sched=sched,
        labs_fsh="40", labs_tsh="2.0", labs_estradiol="30",
        labs_date=sched - timedelta(days=20),
    )
    pat = _fetch(client, "RDY-LABS-WIDE")
    assert pat["active_visit_labs_ready"] is True
    assert pat["active_visit_labs_reason"] == "ok"
    assert pat["labs_valid_days"] == 30


def test_labs_missing_values(client, db):
    sched = date.today() + timedelta(days=10)
    _seed_patient_with_visit(
        db, chart="RDY-LABS-MISS", sched=sched,
        labs_fsh="40", labs_tsh="2.0", labs_estradiol=None,
        labs_date=sched - timedelta(days=2),
    )
    pat = _fetch(client, "RDY-LABS-MISS")
    assert pat["active_visit_labs_ready"] is False
    assert pat["active_visit_labs_reason"] == "missing_values"


def test_labs_not_required(client, db):
    sched = date.today() + timedelta(days=10)
    _seed_patient_with_visit(
        db, chart="RDY-LABS-NR", sched=sched,
        labs_not_required=True,
    )
    pat = _fetch(client, "RDY-LABS-NR")
    assert pat["active_visit_labs_ready"] is True
    assert pat["active_visit_labs_reason"] == "not_required"


def test_labs_none(client, db):
    sched = date.today() + timedelta(days=10)
    _seed_patient_with_visit(db, chart="RDY-LABS-NONE", sched=sched)
    pat = _fetch(client, "RDY-LABS-NONE")
    assert pat["active_visit_labs_reason"] == "none"


def test_mammo_stale(client, db):
    sched = date.today() + timedelta(days=10)
    _seed_patient_with_visit(
        db, chart="RDY-MAMMO-STALE", sched=sched,
        mammo_result="BI-RADS 1",
        mammo_date=sched - timedelta(days=400),
    )
    pat = _fetch(client, "RDY-MAMMO-STALE")
    assert pat["active_visit_mammo_ready"] is False
    assert pat["active_visit_mammo_reason"] == "stale"


def test_mammo_unacceptable(client, db):
    sched = date.today() + timedelta(days=10)
    _seed_patient_with_visit(
        db, chart="RDY-MAMMO-BAD", sched=sched,
        mammo_result="BI-RADS 4",
        mammo_date=sched - timedelta(days=10),
    )
    pat = _fetch(client, "RDY-MAMMO-BAD")
    assert pat["active_visit_mammo_ready"] is False
    assert pat["active_visit_mammo_reason"] == "unacceptable"
