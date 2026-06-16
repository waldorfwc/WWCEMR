"""Authenticated walk-through of the Device Tracking billing-gate fix:
you cannot record a ModMed claim # until the device is actually inserted.

Drives the real /larc/assignments/{id}/bill and /outcome endpoints through
the authenticated client. Reproduces the 409 a user hit when saving billing
on a not-yet-inserted assignment, then shows the correct order (insert →
bill) succeeds. The frontend fix (gating the claim entry until inserted)
mirrors this exact backend rule.

Run: pytest tests/test_larc_billing_gate_walkthrough.py -s
"""
from app.models.larc import LarcAssignment


def _seed(db, *, status="checked_out", source_flow="larc"):
    a = LarcAssignment(chart_number="MRN200", patient_name="Doe, Jane",
                       source_flow=source_flow, status=status)
    db.add(a); db.commit(); db.refresh(a)
    return a


def test_larc_billing_gate_walkthrough(client, db, capsys):
    log = []
    a = _seed(db)
    log.append(f"seeded LARC assignment for Doe, Jane — status '{a.status}' (not yet inserted)")

    # 1. Attempt to bill before inserting → the 409 the user reported.
    r = client.post(f"/api/larc/assignments/{a.id}/bill", json={"claim_number": "CLM-555"})
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert "Can only bill an inserted assignment" in detail
    assert "checked_out" in detail        # the current-status hint
    log.append(f"1. POST /bill while '{a.status}' → 409: \"{detail}\"")

    # 2. Record the insertion outcome → status flips to 'inserted'.
    r = client.post(f"/api/larc/assignments/{a.id}/outcome", json={"outcome": "inserted"})
    assert r.status_code == 200, r.text
    db.refresh(a)
    assert a.status == "inserted"
    log.append("2. POST /outcome {inserted} → status now 'inserted' (device-inserted milestone done)")

    # 3. Now billing succeeds and closes the assignment.
    r = client.post(f"/api/larc/assignments/{a.id}/bill", json={"claim_number": "CLM-555"})
    assert r.status_code == 200, r.text
    db.refresh(a)
    assert a.status == "billed"
    assert a.claim_number == "CLM-555"
    assert a.billed_by                     # the authenticated user is recorded
    log.append(f"3. POST /bill while 'inserted' → 200; status 'billed', claim #{a.claim_number} "
               f"recorded by {a.billed_by}")

    with capsys.disabled():
        print("\n  ── Device Tracking billing-gate walk-through (authenticated) ──")
        for line in log:
            print("   " + line)
