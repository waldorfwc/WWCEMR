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


# ─── B3: fires on the coordinator scheduling endpoint ───────────────

def test_coordinator_schedule_creates_linked_request(client, db):
    """Scheduling a device-bearing surgery via the coordinator pick-date
    endpoint creates a linked LARC device request."""
    from datetime import time, timedelta
    from app.models.surgery import BlockDay

    _seed_device_types(db)
    s = Surgery(
        chart_number="CH200", patient_name="Roe, Mary",
        first_name="Mary", last_name="Roe",
        surgeon_primary="Dr. Aryian Cooke",
        eligible_facilities=["office"], status="new",
        procedure_classification="office_d_and_c",
        estimated_minutes=60,
        device_required=True, device_types=["Mirena"],
    )
    db.add(s)
    bd = BlockDay(
        block_date=_dt.date.today() + timedelta(days=21),
        facility="office",
        start_time=time(7, 0), end_time=time(15, 0),
        block_kind="office_d_and_c",
    )
    db.add(bd)
    db.commit()
    db.refresh(s); db.refresh(bd)

    r = client.post(f"/api/surgery/{s.id}/pick-date",
                    json={"block_day_id": str(bd.id)})
    assert r.status_code == 200, r.text

    rows = (db.query(LarcAssignment)
              .filter(LarcAssignment.linked_surgery_id == s.id)
              .all())
    assert len(rows) == 1
    a = rows[0]
    assert a.source_flow == "pharmacy_order"  # no Mirena in stock
    assert a.requested_by_provider == "Dr. Aryian Cooke"
    assert a.from_surgery if hasattr(a, "from_surgery") else True

    # The endpoint payload exposes the link (B4).
    body = r.json()
    reqs = body["surgery"]["device_requests"]
    assert len(reqs) == 1
    assert reqs[0]["device_type"] == "Mirena"
    assert reqs[0]["source_flow"] == "pharmacy_order"
    assert reqs[0]["requested_by_provider"] == "Dr. Aryian Cooke"


# ─── B4: LARC assignment dict surfaces the surgery origin ───────────

def test_larc_assignment_dict_exposes_from_surgery(client, db):
    liletta, _, _ = _seed_device_types(db)
    s = _make_surgery(db, device_types=["Liletta"])
    sync_surgery_device_requests(db, s, actor_email="staff@x.com")
    a = (db.query(LarcAssignment)
           .filter(LarcAssignment.linked_surgery_id == s.id)
           .first())

    r = client.get(f"/api/larc/assignments/{a.id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["from_surgery"] is True
    assert body["linked_surgery_id"] == str(s.id)
    assert body["requested_by_provider"] == "Dr. Aryian Cooke"

    # list direction too
    rl = client.get("/api/larc/assignments")
    assert rl.status_code == 200
    row = next(x for x in rl.json()["assignments"] if x["id"] == str(a.id))
    assert row["from_surgery"] is True
    assert row["requested_by_provider"] == "Dr. Aryian Cooke"


def test_larc_assignment_dict_non_surgery_origin(client, db):
    """A normally-created assignment (no surgery link) reports from_surgery False."""
    _seed_device_types(db)
    dt = db.query(LarcDeviceType).filter(LarcDeviceType.name == "Mirena").first()
    r = client.post("/api/larc/assignments", json={
        "chart_number": "CH900",
        "patient_name": "Solo, Pat",
        "source_flow": "pharmacy_order",
        "device_type_id": str(dt.id),
    })
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["from_surgery"] is False
    assert body["linked_surgery_id"] is None
    assert body["requested_by_provider"] is None
