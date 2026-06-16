"""B2/B3: surgery → Device Tracking bridge service + scheduling hook."""
import datetime as _dt

from app.models.larc import LarcAssignment, LarcDevice, LarcDeviceType
from app.models.surgery import Surgery
from app.services.surgery.device_requests import sync_surgery_device_requests


def _seed_device_types(db):
    """Seed three device types: one in-stock (with an unassigned device),
    one pharmacy_order (no stock), one office_procedure (no stock)."""
    liletta = LarcDeviceType(name="Liletta", default_flow="in_stock",
                             category="larc", is_active=True)
    mirena = LarcDeviceType(name="Mirena", default_flow="pharmacy_order",
                            category="larc", is_active=True)
    benesta = LarcDeviceType(name="Benesta", default_flow="office_procedure",
                             category="office_procedure", is_active=True)
    db.add_all([liletta, mirena, benesta])
    db.flush()
    # An unassigned Liletta device → makes Liletta resolve to "in_stock".
    db.add(LarcDevice(our_id="LIL-1", device_type_id=liletta.id,
                      status="unassigned", location="white_plains"))
    db.commit()
    return liletta, mirena, benesta


def _make_surgery(db, **overrides):
    fields = dict(
        chart_number="CH100",
        patient_name="Doe, Jane",
        first_name="Jane",
        last_name="Doe",
        dob=_dt.date(1990, 1, 2),
        primary_insurance="BCBS",
        surgeon_primary="Dr. Aryian Cooke",
        surgery_number="SUR00500",
        status="new",
        device_required=True,
        device_types=["Liletta", "Mirena", "Benesta", "None"],
    )
    fields.update(overrides)
    s = Surgery(**fields)
    db.add(s)
    db.commit()
    return s


def test_sync_creates_requests_with_auto_picked_flows(db):
    _seed_device_types(db)
    s = _make_surgery(db)

    result = sync_surgery_device_requests(db, s, actor_email="staff@x.com")

    assert len(result["created"]) == 3
    assert result["skipped_existing"] == 0
    assert result["unmatched"] == []

    rows = (db.query(LarcAssignment)
              .filter(LarcAssignment.linked_surgery_id == s.id)
              .all())
    by_type = {}
    for a in rows:
        dt = db.query(LarcDeviceType).filter(
            LarcDeviceType.id == a.device_type_id).first()
        by_type[dt.name] = a

    assert by_type["Liletta"].source_flow == "in_stock"
    assert by_type["Mirena"].source_flow == "pharmacy_order"
    assert by_type["Benesta"].source_flow == "office_procedure"

    for a in rows:
        assert a.linked_surgery_id == s.id
        assert a.requested_by_provider == "Dr. Aryian Cooke"
        assert a.created_by == "staff@x.com"
        assert a.status == "new"
        assert a.chart_number == "CH100"
        assert a.patient_dob == _dt.date(1990, 1, 2)


def test_sync_is_idempotent(db):
    _seed_device_types(db)
    s = _make_surgery(db)

    first = sync_surgery_device_requests(db, s)
    assert len(first["created"]) == 3

    second = sync_surgery_device_requests(db, s)
    assert second["created"] == []
    assert second["skipped_existing"] == 3

    total = (db.query(LarcAssignment)
               .filter(LarcAssignment.linked_surgery_id == s.id)
               .count())
    assert total == 3


def test_sync_unmatched_device_name(db):
    _seed_device_types(db)
    s = _make_surgery(db, device_types=["Liletta", "Unobtanium"])

    result = sync_surgery_device_requests(db, s)
    assert len(result["created"]) == 1
    assert result["unmatched"] == ["Unobtanium"]


def test_sync_default_actor_when_none(db):
    _seed_device_types(db)
    s = _make_surgery(db, device_types=["Liletta"])

    sync_surgery_device_requests(db, s)
    a = (db.query(LarcAssignment)
           .filter(LarcAssignment.linked_surgery_id == s.id)
           .first())
    assert a.created_by == "system:surgery-schedule"


def test_sync_no_devices_is_noop(db):
    _seed_device_types(db)
    s = _make_surgery(db, device_types=["None"])

    result = sync_surgery_device_requests(db, s)
    assert result == {"created": [], "skipped_existing": 0, "unmatched": []}
