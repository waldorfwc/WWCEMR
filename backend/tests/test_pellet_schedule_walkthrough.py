"""Authenticated Phase-3 walk-through: staff publishes a weekly availability
template (which materializes recurring slots), a "ready" patient lists open
slots in the portal and books one, then staff marks it complete and the
insertion credit is drawn down to zero."""
from datetime import date, timedelta
from app.models.pellet import PelletPatient
from app.models.pellet_portal import PelletConsent
from app.models.pellet_payment import PelletInsertionCredit
from app.models.pellet_schedule import PelletSlot
from app.services.pellet import portal_auth
from app.services.pellet import payments as pay
from app.utils.dt import now_utc_naive


def test_phase3_walkthrough(client, db, capsys):
    log = []

    # 1. Staff publishes a weekly availability template -> materializes slots.
    r = client.post("/api/pellets/availability/templates", json={
        "location": "white_plains", "recurrence_kind": "weekly", "weekday": 2,
        "start_time": "09:00", "end_time": "12:00", "slot_minutes": 60})
    assert r.status_code == 201, r.text
    created = r.json()["materialized"]["created"]
    assert created > 0, "weekly template should materialize at least one slot"
    log.append(f"1. staff published weekly template (Wed 09:00-12:00) -> {created} slots materialized")

    # 2. Seed a "ready" patient: mammo+labs verified, signed non-expired
    #    consent, and one insertion credit. Mint a portal token.
    p = PelletPatient(patient_name="Doe, Jane", chart_number="MRN1",
                      patient_dob=date(1980, 5, 1), patient_phone="3015551234",
                      patient_email="j@x.com",
                      mammo_verified=True, labs_verified=True)
    db.add(p); db.flush()
    db.add(PelletConsent(pellet_patient_id=p.id, boldsign_envelope_id="e1",
                         status="signed", signed_at=now_utc_naive(),
                         expires_at=now_utc_naive() + timedelta(days=300)))
    db.add(PelletInsertionCredit(pellet_patient_id=p.id, delta=1, source="single"))
    db.commit(); db.refresh(p)
    h = {"Authorization": f"Bearer {portal_auth.issue_portal_token(p)}"}
    assert pay.credit_balance(db, p) == 1
    log.append("2. seeded a ready patient (mammo+labs verified, signed consent, 1 credit)")

    # Deterministic bookable target: an ad-hoc far-future open slot via staff.
    # (The recurrence path is already proven by step 1's materialized count.)
    a = client.post("/api/pellets/availability/slots", json={
        "location": "white_plains", "slot_date": "2099-07-01",
        "start_time": "09:00", "end_time": "10:00"})
    assert a.status_code == 201, a.text

    # 3. Patient lists open slots in the portal.
    listing = client.get("/api/pellet-portal/schedule/slots?location=white_plains",
                         headers=h).json()
    assert listing["can_schedule"] is True
    assert len(listing["items"]) >= 1
    sid = listing["items"][0]["id"]
    log.append(f"3. patient listed open slots (can_schedule=True, {len(listing['items'])} open)")

    # 4. Patient books the slot.
    b = client.post(f"/api/pellet-portal/schedule/slots/{sid}/book", headers=h)
    assert b.status_code == 200, b.text
    log.append("4. patient booked an insertion slot")

    # 5. Staff marks it complete -> credit drawn down to zero.
    c = client.post(f"/api/pellets/slots/{sid}/complete")
    assert c.status_code == 200, c.text
    assert pay.credit_balance(db, p) == 0
    log.append("5. staff marked it complete -> insertion credit drawn down to 0")

    with capsys.disabled():
        print("\n  -- Pellet scheduling Phase-3 walk-through (authenticated) --")
        for line in log:
            print("   " + line)
