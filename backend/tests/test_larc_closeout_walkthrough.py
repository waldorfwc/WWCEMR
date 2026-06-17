"""Authenticated walk-through: a patient-owned LARC device is inserted, then
closed out (no claim) and drops off the active list. `client` is super-admin."""
from app.models.larc import LarcAssignment, LarcDevice, LarcDeviceType
from app.services.larc.workflow import assignment_buckets


def test_closeout_walkthrough(client, db, capsys):
    log = []
    dt = LarcDeviceType(name="Liletta-WT")
    db.add(dt); db.flush()
    d = LarcDevice(our_id="LAR-WT-1", device_type_id=dt.id,
                   ownership="patient_owned", status="inserted")
    db.add(d); db.flush()
    a = LarcAssignment(chart_number="MRN-WT", patient_name="Roe, Pat",
                       source_flow="larc", status="inserted", device_id=d.id, is_active=True)
    db.add(a); db.commit(); db.refresh(a)
    log.append("seeded a patient-owned device, inserted (sits in 'inserted_not_billed')")

    # 1. /bill rejects it (the gap the user reported).
    r = client.post(f"/api/larc/assignments/{a.id}/bill", json={"claim_number": "CLM-X"})
    assert r.status_code == 409 and "patient-owned" in r.json()["detail"].lower()
    log.append(f"1. POST /bill -> 409: \"{r.json()['detail'][:60]}...\"")

    # 2. /close-out reaches the terminal billed state with no claim.
    r = client.post(f"/api/larc/assignments/{a.id}/close-out")
    assert r.status_code == 200, r.text
    db.refresh(a)
    assert a.status == "billed" and a.claim_number is None and a.billed_by
    log.append(f"2. POST /close-out -> 200; status 'billed', no claim, by {a.billed_by}")

    # 3. It's off every active list.
    assert assignment_buckets(a) == set()
    log.append("3. assignment_buckets empty -> dropped off the active Device Tracking list")

    with capsys.disabled():
        print("\n  -- LARC patient-owned close-out walk-through (authenticated) --")
        for line in log:
            print("   " + line)
