"""Per-facility booking freeze, exercised through the real portal slots endpoint."""
from datetime import date, time, timedelta
from app.models.surgery import Surgery, BlockDay
from app.models.surgery_config import SurgeryConfig
from app.services.patient_portal_auth import issue_portal_token


def _cfg(db, key, value):
    db.add(SurgeryConfig(key=key, value=value)); db.commit()


def _surgery(db, facility="medstar", proc="robotic_180"):
    s = Surgery(chart_number="F1", patient_name="Pat",
                eligible_facilities=[facility], status="in_progress",
                procedure_classification=proc,
                procedures=[{"name": "X", "kind": proc}])
    db.add(s); db.commit(); db.refresh(s)
    return s


def _block(db, facility, days_out, kind="robotic_180"):
    bd = BlockDay(facility=facility,
                  block_date=date.today() + timedelta(days=days_out),
                  block_kind=kind, start_time=time(7, 30), end_time=time(15, 0))
    db.add(bd); db.commit()
    return bd


def _portal_dates(client, s):
    token = issue_portal_token(s)
    r = client.get(f"/api/patient/portal/{s.id}/slots",
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    body = r.json()
    items = body.get("block_days") or body.get("days") or []
    return {b.get("block_date") for b in items}


def test_frozen_facility_hides_dates_before_reopen(client, db):
    s = _surgery(db, "medstar")
    _block(db, "medstar", 30)
    _block(db, "medstar", 90)
    _cfg(db, "patient_earliest_booking_date",
         {"medstar": (date.today() + timedelta(days=60)).isoformat()})
    dates = _portal_dates(client, s)
    near = (date.today() + timedelta(days=30)).isoformat()
    far  = (date.today() + timedelta(days=90)).isoformat()
    assert near not in dates          # before MedStar reopens → hidden
    assert far in dates               # on/after reopen → shown


def test_freeze_is_per_facility(client, db):
    # MedStar surgery; only CRMC is frozen → MedStar's near date still shows.
    s = _surgery(db, "medstar")
    _block(db, "medstar", 30)
    _cfg(db, "patient_earliest_booking_date",
         {"crmc": (date.today() + timedelta(days=120)).isoformat()})
    assert (date.today() + timedelta(days=30)).isoformat() in _portal_dates(client, s)
