"""Comprehensive authenticated walk-through of LARC Reports.

Seeds a realistic dataset spanning every tile, then drives the real router +
permission stack (super-admin `client`) through: the summary, all 7 tiles'
drill-downs, the location / device-type / date filters, and a CSV export.
Run with -s to see the printed walk-through log.
"""
from datetime import datetime, date

from app.models.larc import (LarcAssignment, LarcDevice, LarcDeviceType,
                             LarcOwedPatient, LarcCheckout)


def _seed(db):
    """A small but tile-spanning dataset."""
    liletta = LarcDeviceType(name="Liletta", category="larc", reorder_threshold=5)
    nova = LarcDeviceType(name="NovaSure", category="office_procedure", reorder_threshold=2)
    db.add_all([liletta, nova]); db.commit(); db.refresh(liletta); db.refresh(nova)

    # In-stock devices (inventory health): 2 Liletta on hand (< reorder 5 -> below),
    # one expiring soon; 0 NovaSure on hand (< reorder 2 -> below).
    inserted_dev = LarcDevice(our_id="WP-3", device_type_id=liletta.id, status="inserted",
                              ownership="wwc_owned", location="white_plains")  # not in stock
    db.add_all([
        LarcDevice(our_id="WP-1", device_type_id=liletta.id, status="unassigned",
                   ownership="wwc_owned", location="white_plains",
                   expiration_date=date(2026, 7, 15)),          # expiring <=90d of 6/18
        LarcDevice(our_id="WP-2", device_type_id=liletta.id, status="received",
                   ownership="wwc_owned", location="brandywine",
                   expiration_date=date(2027, 1, 1)),
        inserted_dev,
    ])
    db.commit(); db.refresh(inserted_dev)

    # Insertions in range (June 2026): one LARC (inserted), one office (billed).
    a_ins = LarcAssignment(chart_number="C-INS", patient_name="Adams, Mary",
                           status="inserted", source_flow="in_stock",
                           device_type_id=liletta.id,
                           inserted_at=datetime(2026, 6, 10), billed_at=None)
    a_billed = LarcAssignment(chart_number="C-BILL", patient_name="Brown, Sue",
                              status="billed", source_flow="in_stock",
                              device_type_id=nova.id,
                              inserted_at=datetime(2026, 6, 20),
                              billed_at=datetime(2026, 6, 22))
    # Prior-period insertion (May) for the delta.
    a_prior = LarcAssignment(chart_number="C-PRIOR", patient_name="Cole, Ann",
                             status="inserted", source_flow="in_stock",
                             device_type_id=liletta.id,
                             inserted_at=datetime(2026, 5, 12),
                             billed_at=datetime(2026, 5, 20))  # billed -> out of backlog
    # Billing backlog: inserted, not billed (a_ins already qualifies).
    # Pharmacy-order in flight (enrollment / funnel buckets).
    a_pharm = LarcAssignment(chart_number="C-PHARM", patient_name="Diaz, Lou",
                             status="in_progress", source_flow="pharmacy_order",
                             device_type_id=liletta.id)
    # Failed-used awaiting replacement (owed tile).
    a_failed = LarcAssignment(chart_number="C-FAIL", patient_name="Eaton, Kay",
                              status="failed_used", source_flow="in_stock",
                              device_type_id=liletta.id,
                              replacement_assignment_id=None)
    db.add_all([a_ins, a_billed, a_prior, a_pharm, a_failed]); db.commit()
    db.refresh(a_ins); db.refresh(a_failed)

    # Owed-patient debt: one open, one resolved (only the open counts).
    db.add_all([
        LarcOwedPatient(chart_number="C-OWE1", patient_name="Frye, Deb",
                        original_assignment_id=a_failed.id,
                        original_device_type_id=liletta.id,
                        owed_since=datetime(2026, 6, 1)),
        LarcOwedPatient(chart_number="C-OWE2", patient_name="Gill, Pat",
                        original_assignment_id=a_failed.id,
                        original_device_type_id=liletta.id,
                        owed_since=datetime(2026, 5, 1),
                        resolved_at=datetime(2026, 6, 5)),
    ])
    # Insertion outcomes (June): 2 success, 1 failed_unused, 1 failed_used, 1 no-show.
    for oc in ("inserted", "inserted", "failed_unused", "failed_used", "patient_no_show"):
        db.add(LarcCheckout(assignment_id=a_ins.id, device_id=inserted_dev.id,
                            requested_by="ma@wwc.com",
                            outcome=oc, requested_at=datetime(2026, 6, 15)))
    db.commit()
    return liletta


def test_larc_reports_full_walkthrough(client, db, capsys):
    liletta = _seed(db)
    P = "from=2026-06-01&to=2026-06-30"
    out = []

    # 1. Summary — every tile present, headline numbers.
    body = client.get(f"/api/larc/reports/summary?{P}").json()
    assert set(body) >= {"workflow_funnel", "outstanding_enrollment", "insertions",
                         "billing_backlog", "owed_patients", "inventory_health",
                         "insertion_outcomes", "period", "device_types"}
    ins = body["insertions"]
    inv = body["inventory_health"]
    oc = body["insertion_outcomes"]
    owed = body["owed_patients"]
    assert ins["total"] == 2 and ins["by_category"] == {"larc": 1, "office_procedure": 1}
    assert ins["prior_total"] == 1 and ins["delta"] == 1
    assert body["billing_backlog"]["count"] == 1
    assert owed["owed_count"] == 1 and owed["awaiting_replacement"] == 1
    assert inv["total_on_hand"] == 2 and inv["expiring"] == 1 and inv["below_reorder"] == 2
    assert oc["success"] == 2 and oc["failed_unused"] == 1 and oc["failed_used"] == 1
    assert oc["total"] == 4 and oc["failure_rate"] == 0.5
    out.append(f"1. SUMMARY  insertions={ins['total']} (larc {ins['by_category'].get('larc',0)} / "
               f"office {ins['by_category'].get('office_procedure',0)}, Δ{ins['delta']:+d} vs prior {ins['prior_total']})  "
               f"backlog={body['billing_backlog']['count']}  "
               f"owed={owed['owed_count']}+awaiting {owed['awaiting_replacement']}  "
               f"inventory on-hand={inv['total_on_hand']} expiring={inv['expiring']} below-reorder={inv['below_reorder']}  "
               f"outcomes ok={oc['success']} fail={oc['failed_unused']+oc['failed_used']} rate={int(oc['failure_rate']*100)}%")
    out.append(f"   device_types dropdown: {[d['name'] for d in body['device_types']]}  "
               f"funnel buckets: {body['workflow_funnel']['by_bucket']}  "
               f"enrollment stages: { {k:v for k,v in body['outstanding_enrollment']['by_stage'].items() if v} }")

    # 2. Drill-downs — each tile's rows match its headline.
    def rows(tile, extra=""):
        r = client.get(f"/api/larc/reports/{tile}/rows?{P}{extra}")
        assert r.status_code == 200, r.text
        return r.json()["items"]

    bl = rows("billing_backlog")
    assert len(bl) == 1 and bl[0]["chart_number"] == "C-INS"
    out.append(f"2. drill billing_backlog -> {len(bl)} row(s): {bl[0]['chart_number']} ({bl[0]['device_type']}, {bl[0]['status']})")

    ins_rows = rows("insertions")
    assert len(ins_rows) == 2
    larc_rows = rows("insertions", "&bucket=larc")
    assert len(larc_rows) == 1 and larc_rows[0]["category"] == "larc"
    out.append(f"   drill insertions -> {len(ins_rows)} total, bucket=larc -> {len(larc_rows)} ({larc_rows[0]['chart_number']})")

    owe_rows = rows("owed_patients")
    assert len(owe_rows) == 1 and owe_rows[0]["chart_number"] == "C-OWE1"
    await_rows = rows("owed_patients", "&bucket=awaiting_replacement")
    assert len(await_rows) == 1 and await_rows[0]["chart_number"] == "C-FAIL"
    out.append(f"   drill owed_patients -> {len(owe_rows)} debt ({owe_rows[0]['chart_number']}), "
               f"bucket=awaiting_replacement -> {len(await_rows)} ({await_rows[0]['chart_number']})")

    exp_rows = rows("inventory_health", "&bucket=expiring")
    assert len(exp_rows) == 1 and exp_rows[0]["our_id"] == "WP-1"
    out.append(f"   drill inventory_health bucket=expiring -> {len(exp_rows)} ({exp_rows[0]['our_id']}, exp {exp_rows[0]['expiration_date']})")

    succ_rows = rows("insertion_outcomes", "&bucket=success")
    fail_rows = rows("insertion_outcomes", "&bucket=failed_used")
    assert len(succ_rows) == 2 and len(fail_rows) == 1
    out.append(f"   drill insertion_outcomes bucket=success -> {len(succ_rows)}, bucket=failed_used -> {len(fail_rows)}")

    # 3. Filters — location + device type narrow the data.
    wp = client.get(f"/api/larc/reports/summary?{P}&location=white_plains").json()
    assert wp["inventory_health"]["total_on_hand"] == 1   # only WP-1 in stock at white_plains
    nova_only = client.get(f"/api/larc/reports/summary?{P}"
                           f"&device_type_id={body['device_types'][1]['id']}").json()
    out.append(f"3. filter location=white_plains -> inventory on-hand={wp['inventory_health']['total_on_hand']}; "
               f"filter device_type=NovaSure -> insertions={nova_only['insertions']['total']} "
               f"({nova_only['insertions']['by_category']})")

    # 4. CSV export — real attachment with header + the backlog row.
    csv_resp = client.get(f"/api/larc/reports/billing_backlog/rows?{P}&format=csv")
    assert csv_resp.status_code == 200
    assert csv_resp.headers["content-type"].startswith("text/csv")
    assert "attachment; filename=" in csv_resp.headers.get("content-disposition", "")
    lines = [l for l in csv_resp.text.splitlines() if l.strip()]
    assert lines[0].startswith("assignment_id") and len(lines) == 2
    out.append(f"4. CSV export -> {csv_resp.headers['content-type'].split(';')[0]}, "
               f"{csv_resp.headers['content-disposition'].split('filename=')[1]}, "
               f"{len(lines)-1} data row, header: {lines[0]}")

    # 5. Unknown tile + permission gate behavior.
    assert client.get(f"/api/larc/reports/bogus/rows").status_code == 404
    out.append("5. unknown tile -> 404; all endpoints required Tier.VIEW (super-admin client passed)")

    with capsys.disabled():
        print("\n  === LARC Reports — authenticated walk-through ===")
        for line in out:
            print("   " + line)
        print("   === all assertions passed ===\n")
