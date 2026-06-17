"""Pellet Reports aggregation service."""
from datetime import date, datetime, timedelta

from app.models.pellet import PelletPatient, PelletVisit
from app.services.pellet import reports as rpt


def _patient(db, **kw):
    base = dict(chart_number="PC1", patient_name="Doe, J", status="active")
    base.update(kw)
    p = PelletPatient(**base); db.add(p); db.commit(); db.refresh(p)
    return p


def _visit(db, p, **kw):
    base = dict(patient_id=p.id, visit_kind="initial", status="new",
                location="white_plains", provider="Cooke, Aryian, MD")
    base.update(kw)
    v = PelletVisit(**base); db.add(v); db.commit(); db.refresh(v)
    return v


def test_status_funnel_counts_and_filters(db):
    p = _patient(db)
    _visit(db, p, status="new")
    _visit(db, p, status="inserted")
    _visit(db, p, status="inserted", location="arlington")
    out = rpt.status_funnel(db, location=None, provider=None)
    assert out["by_status"]["inserted"] == 2
    assert out["by_status"]["new"] == 1
    out2 = rpt.status_funnel(db, location="arlington", provider=None)
    assert out2["by_status"]["inserted"] == 1 and out2["by_status"].get("new", 0) == 0


def test_status_funnel_excludes_historical(db):
    p = _patient(db)
    _visit(db, p, status="inserted")
    _visit(db, p, status="inserted", is_historical=True)
    assert rpt.status_funnel(db, location=None, provider=None)["by_status"]["inserted"] == 1


def test_insertions_in_range_with_prior(db):
    p = _patient(db)
    df, dt = date(2026, 6, 1), date(2026, 6, 30)
    _visit(db, p, status="inserted", visit_kind="initial", inserted_at=datetime(2026, 6, 10))
    _visit(db, p, status="billed", visit_kind="booster", inserted_at=datetime(2026, 6, 20))
    _visit(db, p, status="inserted", visit_kind="initial", inserted_at=datetime(2026, 5, 15))
    out = rpt.insertions(db, date_from=df, date_to=dt, location=None, provider=None)
    assert out["total"] == 2
    assert out["by_kind"] == {"initial": 1, "booster": 1}
    assert out["prior_total"] == 1
    assert out["delta"] == 1
    assert out["prior_from"] == date(2026, 5, 2) and out["prior_to"] == date(2026, 5, 31)


def test_providers_lists_distinct(db):
    p = _patient(db)
    _visit(db, p, provider="Cooke, Aryian, MD")
    _visit(db, p, provider="Smith, Pat, NP")
    _visit(db, p, provider="Cooke, Aryian, MD")
    assert rpt.providers(db) == ["Cooke, Aryian, MD", "Smith, Pat, NP"]
