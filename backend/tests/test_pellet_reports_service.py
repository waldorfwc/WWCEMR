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


def test_recall_due_overdue_and_due_soon(db):
    from app.models.pellet import PelletPatient, PelletVisit
    today = date(2026, 6, 15)
    p1 = _patient(db, chart_number="R1", recall_interval_months=4)
    _visit(db, p1, status="billed", inserted_at=datetime(2026, 6, 15) - timedelta(days=200))
    p2 = _patient(db, chart_number="R2", recall_interval_months=4)
    _visit(db, p2, status="billed", inserted_at=datetime(2026, 6, 15) - timedelta(days=110))
    p3 = _patient(db, chart_number="R3", recall_interval_months=4)
    _visit(db, p3, status="billed", inserted_at=datetime(2026, 6, 15) - timedelta(days=10))
    p4 = _patient(db, chart_number="R4", recall_interval_months=4)
    _visit(db, p4, status="billed", inserted_at=datetime(2026, 6, 15) - timedelta(days=200))
    _visit(db, p4, status="new", scheduled_date=date(2026, 6, 20))
    out = rpt.recall_due(db, location=None, provider=None, today=today)
    assert out["overdue"] == 1
    assert out["due_soon"] == 1
    assert out["total"] == 2


def test_prerequisites_blockers(db):
    from app.models.pellet import PelletPatient
    today = date(2026, 6, 15)
    p = _patient(db, chart_number="PR1", mammo_verified=False, labs_verified=False,
                 labs_not_required=False)
    _visit(db, p, status="new", scheduled_date=date(2026, 6, 20))
    p2 = _patient(db, chart_number="PR2", mammo_verified=True, mammo_date=date(2026, 6, 1),
                  labs_verified=True, labs_date=date(2026, 6, 10))
    from app.models.pellet_portal import PelletConsent
    from app.utils.dt import now_utc_naive
    db.add(PelletConsent(pellet_patient_id=p2.id, status="signed",
                         expires_at=now_utc_naive() + timedelta(days=300)))
    _visit(db, p2, status="new", scheduled_date=date(2026, 6, 18))
    db.commit()
    out = rpt.prerequisites(db, location=None, provider=None, today=today)
    assert out["total"] == 1
    assert out["by_blocker"]["mammo"] == 1
    assert out["by_blocker"]["consent"] == 1


def test_prerequisites_labs_staleness_uses_visit_date_not_today(db):
    """Labs staleness is measured against the visit's scheduled_date (matching
    pellet.py _labs_status), not `today`. Labs drawn 10 days before `today` are
    fresh vs today but stale vs a visit 6 days out (labs_valid_days=14)."""
    from app.models.pellet_portal import PelletConsent
    from app.utils.dt import now_utc_naive
    today = date(2026, 6, 15)
    p = _patient(db, chart_number="PRSTALE", mammo_verified=True,
                 mammo_date=date(2026, 6, 1), labs_verified=True,
                 labs_date=date(2026, 6, 5), labs_not_required=False)
    db.add(PelletConsent(pellet_patient_id=p.id, status="signed",
                         expires_at=now_utc_naive() + timedelta(days=300)))
    # Visit 6 days out: ref - 14d = 2026-06-07; labs_date 06-05 < 06-07 => stale.
    _visit(db, p, status="new", scheduled_date=date(2026, 6, 21))
    db.commit()
    out = rpt.prerequisites(db, location=None, provider=None, today=today)
    assert out["total"] == 1
    assert out["by_blocker"]["labs"] == 1
    assert out["by_blocker"]["mammo"] == 0
    assert out["by_blocker"]["consent"] == 0


def test_billing_backlog(db):
    from decimal import Decimal
    p = _patient(db)
    _visit(db, p, status="inserted", inserted_at=datetime(2026, 6, 1),
           price_amount=Decimal("500.00"), billed_at=None)
    _visit(db, p, status="inserted", inserted_at=datetime(2026, 6, 2),
           price_amount=Decimal("400.00"), billed_at=datetime(2026, 6, 3))  # already billed
    _visit(db, p, status="new")   # not inserted
    out = rpt.billing_backlog(db, location=None, provider=None)
    assert out["count"] == 1
    assert out["total_amount"] == 500.0


def test_inventory_health(db):
    from datetime import date as _d
    from app.models.pellet import PelletDoseType, PelletLot, PelletStock
    dt_type = PelletDoseType(hormone="estradiol", dose_mg=12.5, label="Estradiol 12.5mg",
                             reorder_thresholds_by_location={"white_plains": 20})
    db.add(dt_type); db.flush()
    lot = PelletLot(dose_type_id=dt_type.id, qualgen_lot_number="QG-1",
                    expiration_date=_d(2026, 7, 1), doses_originally_received=5)
    db.add(lot); db.flush()
    db.add(PelletStock(lot_id=lot.id, location="white_plains", doses_on_hand=5, status="active"))
    db.commit()
    out = rpt.inventory_health(db, location=None, today=_d(2026, 6, 15))
    assert out["total_on_hand"] == 5
    assert out["by_location"]["white_plains"] == 5
    assert out["expiring_lots"] == 1
    assert out["below_reorder"] == 1


def test_rows_for_status_funnel_bucket(db):
    p = _patient(db)
    _visit(db, p, status="cancelled")
    _visit(db, p, status="cancelled")
    _visit(db, p, status="new")
    rows = rpt.rows_for(db, "status_funnel", date_from=date(2026, 6, 1),
                        date_to=date(2026, 6, 30), location=None, provider=None,
                        bucket="cancelled", today=date(2026, 6, 15))
    assert len(rows) == 2 and all(r["status"] == "cancelled" for r in rows)
    assert {"visit_id", "chart_number", "patient_name", "status"} <= set(rows[0])


def test_rows_to_csv_has_header_and_rows():
    csv_text = rpt.rows_to_csv([{"a": 1, "b": "x"}, {"a": 2, "b": "y"}])
    lines = [ln for ln in csv_text.splitlines() if ln.strip()]
    assert lines[0] == "a,b" and len(lines) == 3


def test_rows_for_inventory_bucket_filters(db):
    from datetime import date as _d
    from app.models.pellet import PelletDoseType, PelletLot, PelletStock
    dt_type = PelletDoseType(hormone="estradiol", dose_mg=12.5, label="E 12.5",
                             reorder_thresholds_by_location={"white_plains": 20})
    db.add(dt_type); db.flush()
    near = PelletLot(dose_type_id=dt_type.id, qualgen_lot_number="NEAR",
                     expiration_date=_d(2026, 7, 1), doses_originally_received=5)
    far = PelletLot(dose_type_id=dt_type.id, qualgen_lot_number="FAR",
                    expiration_date=_d(2027, 1, 1), doses_originally_received=5)
    db.add(near); db.add(far); db.flush()
    db.add(PelletStock(lot_id=near.id, location="white_plains", doses_on_hand=3, status="active"))
    db.add(PelletStock(lot_id=far.id, location="arlington", doses_on_hand=4, status="active"))
    db.commit()
    kw = dict(date_from=_d(2026, 6, 1), date_to=_d(2026, 6, 30),
              location=None, provider=None, today=_d(2026, 6, 15))
    # bucket=location filters to that location
    loc_rows = rpt.rows_for(db, "inventory_health", bucket="arlington", **kw)
    assert len(loc_rows) == 1 and loc_rows[0]["location"] == "arlington"
    # bucket=expiring keeps only the lot expiring within 90 days
    exp_rows = rpt.rows_for(db, "inventory_health", bucket="expiring", **kw)
    assert len(exp_rows) == 1 and exp_rows[0]["lot_number"] == "NEAR"


def test_below_reorder_drill_matches_count_incl_depleted(db):
    from datetime import date as _d
    from app.models.pellet import PelletDoseType, PelletStock, PelletLot
    # Dose type with a white_plains threshold of 20 but NO stock on hand.
    dt_type = PelletDoseType(hormone="testosterone", dose_mg=200, label="T 200",
                             reorder_thresholds_by_location={"white_plains": 20})
    db.add(dt_type); db.commit()
    # Headline counts it as below-reorder (0 < 20)...
    tile = rpt.inventory_health(db, location=None, today=_d(2026, 6, 15))
    assert tile["below_reorder"] == 1
    # ...and the drill now surfaces it (synthetic 0-on-hand row), matching count.
    rows = rpt.rows_for(db, "inventory_health", date_from=_d(2026, 6, 1),
                        date_to=_d(2026, 6, 30), location=None, provider=None,
                        bucket="below_reorder", today=_d(2026, 6, 15))
    assert len(rows) == 1
    assert rows[0]["dose_type"] == "T 200" and rows[0]["doses_on_hand"] == 0
    assert rows[0]["reorder_threshold"] == 20
