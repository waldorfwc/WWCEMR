"""Surgery Reports aggregation service."""
from datetime import date, datetime, timedelta

from app.models.surgery import Surgery
from app.services.surgery import reports as rpt
from app.utils.dt import now_utc_naive


def _surg(db, **kw):
    base = dict(chart_number="M1", patient_name="Doe, J", status="new",
                surgeon_primary="Cooke, Aryian, MD", selected_facility="medstar")
    base.update(kw)
    s = Surgery(**base)
    db.add(s); db.commit(); db.refresh(s)
    return s


def test_status_funnel_counts_and_filters(db):
    _surg(db, status="new")
    _surg(db, status="confirmed")
    _surg(db, status="confirmed", surgeon_primary="Other, MD")
    out = rpt.status_funnel(db, facility=None, surgeon=None)
    assert out["by_status"]["confirmed"] == 2
    assert out["by_status"]["new"] == 1
    out2 = rpt.status_funnel(db, facility=None, surgeon="Other, MD")
    assert out2["by_status"]["confirmed"] == 1 and out2["by_status"].get("new", 0) == 0


def test_completed_in_range_with_prior_period(db):
    df, dt = date(2026, 6, 1), date(2026, 6, 30)
    _surg(db, status="completed", procedure_classification="major",
          completed_at=datetime(2026, 6, 10, 9, 0))
    _surg(db, status="completed", procedure_classification="minor",
          completed_at=datetime(2026, 6, 20, 9, 0))
    _surg(db, status="completed", procedure_classification="major",
          completed_at=datetime(2026, 5, 15, 9, 0))
    out = rpt.completed(db, date_from=df, date_to=dt, facility=None, surgeon=None)
    assert out["total"] == 2
    assert out["by_classification"] == {"major": 1, "minor": 1}
    assert out["prior_total"] == 1
    assert out["delta"] == 1
    assert out["prior_from"] == date(2026, 5, 2) and out["prior_to"] == date(2026, 5, 31)


def test_cycle_time_lead_and_reschedule(db):
    df, dt = date(2026, 6, 1), date(2026, 6, 30)
    s1 = _surg(db, status="completed", completed_at=datetime(2026, 6, 10),
               scheduled_date=date(2026, 6, 10), reschedule_count=2)
    s1.created_at = datetime(2026, 6, 1); db.commit()
    s2 = _surg(db, status="completed", completed_at=datetime(2026, 6, 20),
               scheduled_date=date(2026, 6, 20), reschedule_count=0)
    s2.created_at = datetime(2026, 6, 9); db.commit()
    out = rpt.cycle_time(db, date_from=df, date_to=dt, facility=None, surgeon=None)
    assert out["n"] == 2
    assert out["avg_lead_days"] == 10.0
    assert out["reschedule_rate"] == 0.5
    assert out["avg_reschedules"] == 1.0


def test_status_funnel_excludes_soft_deleted(db):
    from app.utils.dt import now_utc_naive
    _surg(db, status="new")
    d = _surg(db, status="new")
    d.deleted_at = now_utc_naive(); db.commit()
    out = rpt.status_funnel(db, facility=None, surgeon=None)
    assert out["by_status"]["new"] == 1   # soft-deleted row not counted


def test_not_ready_blockers(db):
    from datetime import date, datetime
    today = date(2026, 6, 15)
    # Inside window, benefits not verified -> blocker on "benefits".
    _surg(db, status="confirmed", scheduled_date=date(2026, 6, 20),
          benefits_verified_at=None)
    # Inside window but fully ready -> excluded.
    _surg(db, status="confirmed", scheduled_date=date(2026, 6, 18),
          benefits_verified_at=datetime(2026, 6, 1), consent_status="not_required",
          auth_status="not_required", clearance_required=False, device_required=False,
          labs_sent_to_hospital=True)
    # Outside window (>14 days) -> excluded.
    _surg(db, status="confirmed", scheduled_date=date(2026, 7, 30))
    # Completed -> excluded.
    _surg(db, status="completed", scheduled_date=date(2026, 6, 19))
    out = rpt.not_ready(db, facility=None, surgeon=None, today=today)
    assert out["total"] == 1
    assert out["by_blocker"]["benefits"] == 1
    assert out["by_blocker"].get("labs", 0) == 1


def test_posting_backlog(db):
    from datetime import datetime
    from decimal import Decimal
    from app.models.stripe_payment import SurgeryPayment
    s = _surg(db, status="confirmed")
    db.add(SurgeryPayment(surgery_id=s.id, kind="deposit", status="paid",
                          amount_requested=Decimal("400.00"),
                          amount_paid=Decimal("400.00"), stripe_payment_intent_id="pi_1",
                          paid_at=datetime(2026, 6, 1), posted_to_modmed_at=None,
                          requested_by="staff@example.com"))
    db.add(SurgeryPayment(surgery_id=s.id, kind="manual_offset", status="paid",
                          amount_requested=Decimal("999.00"),
                          amount_paid=Decimal("999.00"), paid_at=datetime(2026, 6, 2),
                          posted_to_modmed_at=None,
                          requested_by="staff@example.com"))   # excluded (manual offset)
    db.add(SurgeryPayment(surgery_id=s.id, kind="deposit", status="paid",
                          amount_requested=Decimal("100.00"),
                          amount_paid=Decimal("100.00"), stripe_payment_intent_id="pi_2",
                          paid_at=datetime(2026, 6, 3), posted_to_modmed_at=datetime(2026, 6, 4),
                          requested_by="staff@example.com"))  # already posted
    db.commit()
    out = rpt.posting_backlog(db, facility=None, surgeon=None)
    assert out["count"] == 1
    assert out["total_amount"] == 400.0


def test_utilization_booked_vs_capacity(db):
    from datetime import date, time
    from app.models.surgery import BlockDay, SurgerySlot
    df, dt = date(2026, 6, 1), date(2026, 6, 30)
    s = _surg(db, status="confirmed", selected_facility="office")
    bd = BlockDay(facility="office", block_date=date(2026, 6, 10),
                  block_kind="office", start_time=time(7, 30), end_time=time(16, 0))
    db.add(bd); db.flush()
    db.add(SurgerySlot(block_day_id=bd.id, surgery_id=s.id,
                       start_time=time(7, 30),
                       duration_minutes=60, procedure_kind="office"))
    db.commit()
    out = rpt.utilization(db, date_from=df, date_to=dt, facility=None)
    assert out["by_facility"]["office"]["capacity"] == 7
    assert out["by_facility"]["office"]["booked"] == 1
    assert out["overall_pct"] == round(1 / 7 * 100, 1)


def test_rows_for_status_funnel_bucket(db):
    from datetime import date
    _surg(db, status="hold")
    _surg(db, status="hold")
    _surg(db, status="new")
    rows = rpt.rows_for(db, "status_funnel", date_from=date(2026, 6, 1),
                        date_to=date(2026, 6, 30), facility=None, surgeon=None,
                        bucket="hold", today=date(2026, 6, 15))
    assert len(rows) == 2 and all(r["status"] == "hold" for r in rows)
    assert {"surgery_id", "chart_number", "patient_name", "status"} <= set(rows[0])


def test_rows_to_csv_has_header_and_rows():
    csv_text = rpt.rows_to_csv([{"a": 1, "b": "x"}, {"a": 2, "b": "y"}])
    lines = [ln for ln in csv_text.splitlines() if ln.strip()]
    assert lines[0] == "a,b"
    assert len(lines) == 3
