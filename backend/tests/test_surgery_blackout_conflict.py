"""Phase C — blackout/surgery conflict detection."""
from datetime import date, timedelta

from app.models.surgery import Surgery, SurgeryBlackoutDay
from app.services.surgery_blackout_conflict import find_blocked_conflicts


def _surgery(db, days_out: int, facility="medstar", status="confirmed"):
    s = Surgery(
        chart_number="1",
        patient_name="Pat",
        scheduled_date=date.today() + timedelta(days=days_out),
        selected_facility=facility,
        status=status,
        eligible_facilities=[facility],
    )
    db.add(s); db.flush()
    return s


def _blackout(db, days_out: int, scope: str, facility=None, reason="pto",
              label="Dr. Cooke PTO", owner_email=None):
    b = SurgeryBlackoutDay(
        blackout_date=date.today() + timedelta(days=days_out),
        scope=scope, reason=reason, label=label,
        facility=facility, owner_email=owner_email,
    )
    db.add(b); db.flush()
    return b


def test_office_scope_flags_all_surgeries_on_date(db):
    s1 = _surgery(db, 3, facility="office")
    s2 = _surgery(db, 3, facility="medstar")
    _blackout(db, 3, scope="office", reason="holiday", label="Memorial Day")
    db.commit()

    out = find_blocked_conflicts(db)
    ids = {c["surgery_id"] for c in out}
    assert str(s1.id) in ids
    assert str(s2.id) in ids


def test_facility_scope_flags_matching_facility_only(db):
    s_medstar = _surgery(db, 3, facility="medstar")
    s_crmc    = _surgery(db, 3, facility="crmc")
    _blackout(db, 3, scope="facility", facility="medstar", reason="facility_closed",
               label="MedStar closed for maintenance")
    db.commit()

    out = find_blocked_conflicts(db)
    ids = {c["surgery_id"] for c in out}
    assert str(s_medstar.id) in ids
    assert str(s_crmc.id) not in ids


def test_provider_scope_flags_all_surgeries_on_date(db):
    # Single-surgeon practice; provider PTO grounds the whole day.
    s = _surgery(db, 3)
    _blackout(db, 3, scope="provider", reason="pto",
               label="Aryian Cooke PTO",
               owner_email="acooke@waldorfwomenscare.com")
    db.commit()

    out = find_blocked_conflicts(db)
    assert any(c["surgery_id"] == str(s.id) for c in out)


def test_resolved_conflicts_are_excluded(db):
    from datetime import datetime
    s = _surgery(db, 3, facility="office")
    _blackout(db, 3, scope="office", reason="holiday", label="Holiday")
    s.blocked_conflict_notified_at = datetime.utcnow()
    db.commit()

    assert find_blocked_conflicts(db) == []


def test_cancelled_surgeries_excluded(db):
    _surgery(db, 3, facility="office", status="cancelled")
    _blackout(db, 3, scope="office", reason="holiday", label="Holiday")
    db.commit()

    assert find_blocked_conflicts(db) == []
