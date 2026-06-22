from app.services.missing_charges_triage import (
    get_triage_recipients, set_triage_recipients, TRIAGE_RECIPIENTS_KEY,
)


def test_recipients_roundtrip(db):
    assert get_triage_recipients(db) == []
    set_triage_recipients(db, "a@wwc.com, b@wwc.com ,")
    assert get_triage_recipients(db) == ["a@wwc.com", "b@wwc.com"]


def test_recipients_endpoint(client, db):
    # super-admin `client` passes the MANAGE gate
    r = client.put("/api/billing/missing-charges/triage-recipients",
                   json={"recipients": ["x@wwc.com"]})
    assert r.status_code == 200
    g = client.get("/api/billing/missing-charges/triage-recipients")
    assert g.status_code == 200
    assert g.json()["recipients"] == ["x@wwc.com"]
