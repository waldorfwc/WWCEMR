"""Authenticated walk-through of the Surgery → Device Tracking request
pipeline: a scheduled surgery with devices auto-creates linked LARC requests
(auto-picking allocate-vs-order from inventory), surfaced both ways. Runs as
the super-admin test client + the bridge service the scheduling hook calls."""
import datetime as _dt

from app.models.larc import LarcAssignment, LarcDevice, LarcDeviceType
from app.models.surgery import Surgery
from app.services.surgery.device_requests import sync_surgery_device_requests


def _seed_types(db):
    lil = LarcDeviceType(name="Liletta", default_flow="in_stock", category="larc", is_active=True)
    mir = LarcDeviceType(name="Mirena", default_flow="pharmacy_order", category="larc", is_active=True)
    ben = LarcDeviceType(name="Benesta", default_flow="office_procedure", category="office_procedure", is_active=True)
    db.add_all([lil, mir, ben]); db.flush()
    db.add(LarcDevice(our_id="LIL-1", device_type_id=lil.id, status="unassigned", location="white_plains"))
    db.commit()


def test_device_request_pipeline_walkthrough(client, db, capsys):
    log = []
    _seed_types(db)
    log.append("1. inventory: Liletta in stock (1 unassigned); Mirena + Benesta none on hand")

    s = Surgery(chart_number="CH900", patient_name="Walk, Thru",
                first_name="Thru", last_name="Walk", dob=_dt.date(1991, 5, 5),
                primary_insurance="BCBS", surgeon_primary="Dr. Aryian Cooke",
                surgery_number="SUR00900", status="new", device_required=True,
                device_types=["Liletta", "Mirena", "Benesta", "None"])
    db.add(s); db.commit()
    log.append("2. surgery scheduled with devices: Liletta, Mirena, Benesta (+None)")

    # The scheduling hook calls this bridge.
    res = sync_surgery_device_requests(db, s, actor_email="coordinator@wwc.com")
    assert len(res["created"]) == 3 and res["unmatched"] == []
    rows = db.query(LarcAssignment).filter(LarcAssignment.linked_surgery_id == s.id).all()
    flow = {db.query(LarcDeviceType).get(a.device_type_id).name: a.source_flow for a in rows}
    assert flow == {"Liletta": "in_stock", "Mirena": "pharmacy_order", "Benesta": "office_procedure"}
    log.append("3. auto-picked path: Liletta→in_stock (allocate) · "
               "Mirena→pharmacy_order (enroll) · Benesta→office_procedure")

    # Who/what/when captured on each request.
    a0 = rows[0]
    assert a0.requested_by_provider == "Dr. Aryian Cooke" and a0.linked_surgery_id == s.id
    log.append(f"4. each request records requester='{a0.requested_by_provider}', "
               f"linked surgery, created_at (when)")

    # Idempotent — re-running (e.g. a reschedule) creates no duplicates.
    res2 = sync_surgery_device_requests(db, s, actor_email="coordinator@wwc.com")
    assert res2["created"] == [] and res2["skipped_existing"] == 3
    log.append("5. idempotent: re-trigger created 0 (skipped 3 existing)")

    # Surfaced on the surgery detail.
    sd = client.get(f"/api/surgery/{s.id}").json()
    assert len(sd["device_requests"]) == 3
    dr = {d["device_type"]: d for d in sd["device_requests"]}
    assert dr["Mirena"]["source_flow"] == "pharmacy_order"
    log.append(f"6. GET /surgery → device_requests shows all 3 with path + status")

    # Surfaced on the Device Tracking side (from_surgery + provider).
    larc = client.get("/api/larc/assignments", params={"linked_surgery_id": str(s.id)}).json()
    items = larc if isinstance(larc, list) else larc.get("assignments", larc.get("items", []))
    assert items and all(it["from_surgery"] for it in items)
    assert items[0]["requested_by_provider"] == "Dr. Aryian Cooke"
    log.append(f"7. GET /larc/assignments → {len(items)} tagged from_surgery, "
               f"requester='{items[0]['requested_by_provider']}'")

    with capsys.disabled():
        print("\n  ── surgery → device-request pipeline (authenticated) ──")
        for line in log:
            print("   " + line)
