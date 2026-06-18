"""Authenticated walk-through of LARC Reports: seed a small set, read the
summary, drill into a tile, and export CSV. `client` is the super-admin fixture."""
from datetime import datetime

from app.models.larc import LarcAssignment, LarcDevice, LarcDeviceType


def test_larc_reports_walkthrough(client, db, capsys):
    log = []
    t = LarcDeviceType(name="Liletta", category="larc", reorder_threshold=5)
    db.add(t); db.commit(); db.refresh(t)
    d = LarcDevice(our_id="LAR-WT", device_type_id=t.id, status="unassigned",
                   ownership="wwc_owned", location="white_plains")
    db.add(d); db.commit(); db.refresh(d)
    db.add(LarcAssignment(chart_number="WT1", patient_name="Roe, Pat", status="inserted",
                          source_flow="in_stock", device_type_id=t.id, device_id=d.id,
                          inserted_at=datetime(2026, 6, 10), billed_at=None))
    db.commit()

    body = client.get("/api/larc/reports/summary?from=2026-06-01&to=2026-06-30").json()
    assert set(body) >= {"workflow_funnel", "outstanding_enrollment", "insertions",
                         "billing_backlog", "owed_patients", "inventory_health",
                         "insertion_outcomes", "period", "device_types"}
    log.append(f"1. /summary -> insertions {body['insertions']['total']}, "
               f"billing backlog {body['billing_backlog']['count']}, "
               f"inventory on hand {body['inventory_health']['total_on_hand']}")

    items = client.get("/api/larc/reports/billing_backlog/rows").json()["items"]
    assert len(items) == 1 and items[0]["chart_number"] == "WT1"
    log.append(f"2. drill billing_backlog -> {len(items)} unbilled insertion")

    csv_resp = client.get("/api/larc/reports/billing_backlog/rows?format=csv")
    assert csv_resp.status_code == 200
    assert csv_resp.headers["content-type"].startswith("text/csv")
    assert csv_resp.text.splitlines()[0].startswith("assignment_id")
    log.append("3. CSV export -> text/csv with header + 1 row")

    with capsys.disabled():
        print("\n  -- LARC Reports walk-through (authenticated) --")
        for line in log:
            print("   " + line)
