"""L2: LARC workflow windows are read from the settings registry at call
time, so a saved LarcConfig row changes runtime thresholds without code
changes. Behavior-preserving when no config rows exist."""
from datetime import timedelta

from app.models.larc import LarcAssignment, LarcAuditEvent
from app.models.larc_config import LarcConfig
from app.services.larc.sweeps import sweep_pharmacy_sla
from app.utils.dt import now_utc_naive


def _mk_pharmacy_assignment(db, *, faxed_days_ago: int) -> LarcAssignment:
    a = LarcAssignment(
        chart_number="C100",
        patient_name="Test Patient",
        source_flow="pharmacy_order",
        status="new",
        request_faxed_at=now_utc_naive() - timedelta(days=faxed_days_ago),
        device_received_at=None,
    )
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


def _sla_breach_count(db) -> int:
    return (db.query(LarcAuditEvent)
              .filter(LarcAuditEvent.action == "pharmacy_sla_breach")
              .count())


def test_pharmacy_sla_uses_default_when_no_config(db):
    # 16 days ago is past the default 14-day SLA → flagged.
    _mk_pharmacy_assignment(db, faxed_days_ago=16)
    sweep_pharmacy_sla(db)
    assert _sla_breach_count(db) == 1


def test_pharmacy_sla_respects_config_override_not_yet_breached(db):
    # 16 days ago, but config raises SLA to 30 days → NOT flagged.
    db.add(LarcConfig(key="pharmacy_order_sla_days", value=30))
    db.commit()
    _mk_pharmacy_assignment(db, faxed_days_ago=16)
    sweep_pharmacy_sla(db)
    assert _sla_breach_count(db) == 0


def test_pharmacy_sla_respects_config_override_breached_sooner(db):
    # 10 days ago — under default 14 — but config tightens SLA to 7 → flagged.
    db.add(LarcConfig(key="pharmacy_order_sla_days", value=7))
    db.commit()
    _mk_pharmacy_assignment(db, faxed_days_ago=10)
    sweep_pharmacy_sla(db)
    assert _sla_breach_count(db) == 1


def test_fax_pharmacy_expected_received_by_reflects_config(db, monkeypatch):
    """fax_pharmacy stamps expected_received_by using the configured SLA."""
    from app.routers import larc as larc_router
    from app.routers.larc import FaxPharmacyIn

    db.add(LarcConfig(key="pharmacy_order_sla_days", value=21))
    db.commit()
    a = _mk_pharmacy_assignment(db, faxed_days_ago=0)
    a.request_faxed_at = None
    db.commit()

    larc_router.fax_pharmacy(
        assignment_id=str(a.id),
        payload=FaxPharmacyIn(),
        db=db,
        current_user={"email": "tester@waldorfwomenscare.com"},
    )
    db.refresh(a)
    expected = (now_utc_naive().date() + timedelta(days=21))
    assert a.expected_received_by == expected
