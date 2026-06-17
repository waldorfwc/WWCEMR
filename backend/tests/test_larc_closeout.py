"""Patient-owned LARC close-out: an inserted patient-owned device is closed
without a claim, reaching the terminal 'billed' state so it drops off the list.
`client` is the super-admin fixture."""
from app.models.larc import LarcAssignment, LarcDevice, LarcDeviceType
from app.services.larc.workflow import assignment_buckets


def _seed(db, *, ownership="patient_owned", a_status="inserted", d_status="inserted"):
    dt = LarcDeviceType(name=f"Liletta-{ownership}-{a_status}")
    db.add(dt); db.flush()
    d = LarcDevice(our_id=f"LAR-{ownership}-{a_status}", device_type_id=dt.id,
                   ownership=ownership, status=d_status)
    db.add(d); db.flush()
    a = LarcAssignment(chart_number="MRN-PO", patient_name="Doe, Pat",
                       source_flow="larc", status=a_status, device_id=d.id, is_active=True)
    db.add(a); db.commit(); db.refresh(a)
    return a, d


def test_close_out_patient_owned_inserted(client, db):
    a, d = _seed(db)
    r = client.post(f"/api/larc/assignments/{a.id}/close-out")
    assert r.status_code == 200, r.text
    db.refresh(a); db.refresh(d)
    assert a.status == "billed"
    assert a.claim_number is None
    assert a.billed_at is not None and a.billed_by
    assert d.status == "billed"
    assert assignment_buckets(a) == set()


def test_close_out_requires_inserted(client, db):
    a, _ = _seed(db, a_status="checked_out", d_status="checked_out")
    r = client.post(f"/api/larc/assignments/{a.id}/close-out")
    assert r.status_code == 409
    assert "inserted" in r.json()["detail"].lower()


def test_close_out_rejects_non_patient_owned(client, db):
    a, _ = _seed(db, ownership="wwc_owned")
    r = client.post(f"/api/larc/assignments/{a.id}/close-out")
    assert r.status_code == 409
    assert "patient-owned" in r.json()["detail"].lower()


def test_bill_still_rejects_patient_owned(client, db):
    a, _ = _seed(db)
    r = client.post(f"/api/larc/assignments/{a.id}/bill", json={"claim_number": "CLM-1"})
    assert r.status_code == 409
    assert "patient-owned" in r.json()["detail"].lower()
