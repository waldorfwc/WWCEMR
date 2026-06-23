from datetime import date
from app.models.user import User
from app.models.pellet import (
    PelletPatient, PelletVisit, PelletVisitDose, PelletDoseType,
    PelletLot, PelletStock, PelletAuditEvent,
)
from app.routers.pellet import _visit_missing_lot


def _mgr(db):
    u = User(email="mgr@waldorfwomenscare.com", display_name="Mgr", is_super_admin=True)
    db.add(u); db.commit()
    return u


def _patient(db):
    p = PelletPatient(patient_name="Tober, Catrina", chart_number="14943",
                      patient_dob=date(1975, 3, 2))
    db.add(p); db.commit(); db.refresh(p)
    return p


def _dose_type(db):
    # hormone and dose_mg are nullable=False with no default — must be supplied
    dt = PelletDoseType(label="Testosterone 200mg", hormone="testosterone",
                        dose_mg=200, is_controlled=True)
    db.add(dt); db.commit(); db.refresh(dt)
    return dt


def _lot(db, dt, qty=10, loc="white_plains", number="LOT-A"):
    # doses_originally_received is nullable=False with no default — must be supplied
    lot = PelletLot(dose_type_id=dt.id, qualgen_lot_number=number,
                    expiration_date=date(2027, 1, 1),
                    doses_originally_received=qty)
    db.add(lot); db.flush()
    db.add(PelletStock(lot_id=lot.id, location=loc, doses_on_hand=qty, status="active"))
    db.commit(); db.refresh(lot)
    return lot


def _visit(db, p, status="inserted", historical=False, location="white_plains"):
    v = PelletVisit(patient_id=p.id, visit_kind="initial", status=status,
                    location=location, is_historical=historical,
                    scheduled_date=date(2026, 6, 5))
    db.add(v); db.commit(); db.refresh(v)
    return v


def test_missing_lot_true_when_zero_doses(db):
    p = _patient(db); v = _visit(db, p, status="inserted")
    assert _visit_missing_lot(v) is True


def test_missing_lot_true_when_a_dose_has_no_lot(db):
    p = _patient(db); dt = _dose_type(db); v = _visit(db, p, status="billed")
    db.add(PelletVisitDose(visit_id=v.id, dose_type_id=dt.id, quantity=2,
                           position=1, status="inserted", lot_id=None))
    db.commit(); db.refresh(v)
    assert _visit_missing_lot(v) is True


def test_missing_lot_false_when_all_doses_lotted(db):
    p = _patient(db); dt = _dose_type(db); lot = _lot(db, dt); v = _visit(db, p, status="inserted")
    db.add(PelletVisitDose(visit_id=v.id, dose_type_id=dt.id, quantity=2,
                           position=1, status="inserted", lot_id=lot.id))
    db.commit(); db.refresh(v)
    assert _visit_missing_lot(v) is False


def test_missing_lot_false_for_historical(db):
    p = _patient(db); v = _visit(db, p, status="inserted", historical=True)
    assert _visit_missing_lot(v) is False


def test_missing_lot_false_for_non_completed(db):
    p = _patient(db); v = _visit(db, p, status="in_progress")
    assert _visit_missing_lot(v) is False


def _client(client_factory, db):
    return client_factory(user=_mgr(db))


def test_reopen_inserted_visit_flips_to_in_progress(client_factory, db):
    p = _patient(db); v = _visit(db, p, status="inserted")
    client = _client(client_factory, db)
    r = client.post(f"/api/pellets/visits/{v.id}/reopen", json={"reason": "missing lot"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "in_progress"
    assert body["pre_reopen_status"] == "inserted"
    assert body["reopened_by"] and body["reopened_reason"] == "missing lot"


def test_reopen_rejects_non_completed(client_factory, db):
    p = _patient(db); v = _visit(db, p, status="in_progress")
    client = _client(client_factory, db)
    r = client.post(f"/api/pellets/visits/{v.id}/reopen", json={"reason": "x"})
    assert r.status_code == 409


def test_reopen_requires_reason(client_factory, db):
    p = _patient(db); v = _visit(db, p, status="inserted")
    client = _client(client_factory, db)
    r = client.post(f"/api/pellets/visits/{v.id}/reopen", json={"reason": "  "})
    assert r.status_code == 422


def test_reopen_twice_409(client_factory, db):
    p = _patient(db); v = _visit(db, p, status="inserted")
    client = _client(client_factory, db)
    client.post(f"/api/pellets/visits/{v.id}/reopen", json={"reason": "a"})
    r = client.post(f"/api/pellets/visits/{v.id}/reopen", json={"reason": "b"})
    assert r.status_code == 409


def test_close_reopen_billed_returns_to_billed(client_factory, db):
    p = _patient(db); v = _visit(db, p, status="billed")
    client = _client(client_factory, db)
    client.post(f"/api/pellets/visits/{v.id}/reopen", json={"reason": "fix"})
    r = client.post(f"/api/pellets/visits/{v.id}/close-reopen")
    assert r.status_code == 200
    assert r.json()["status"] == "billed"
    assert r.json()["reopened_at"] is None


def test_close_reopen_inserted_returns_to_inserted(client_factory, db):
    p = _patient(db); v = _visit(db, p, status="inserted")
    client = _client(client_factory, db)
    client.post(f"/api/pellets/visits/{v.id}/reopen", json={"reason": "fix"})
    r = client.post(f"/api/pellets/visits/{v.id}/close-reopen")
    assert r.json()["status"] == "inserted"


def test_close_reopen_from_cancelled_goes_inserted(client_factory, db):
    p = _patient(db); v = _visit(db, p, status="cancelled")
    client = _client(client_factory, db)
    client.post(f"/api/pellets/visits/{v.id}/reopen", json={"reason": "un-cancel"})
    r = client.post(f"/api/pellets/visits/{v.id}/close-reopen")
    assert r.json()["status"] == "inserted"


def test_close_reopen_not_reopened_409(client_factory, db):
    p = _patient(db); v = _visit(db, p, status="inserted")
    client = _client(client_factory, db)
    r = client.post(f"/api/pellets/visits/{v.id}/close-reopen")
    assert r.status_code == 409


def _stock(db, lot, loc="white_plains"):
    return (db.query(PelletStock)
              .filter(PelletStock.lot_id == lot.id, PelletStock.location == loc)
              .first())


def test_correct_dose_binds_lot_and_decrements_stock(client_factory, db):
    p = _patient(db); dt = _dose_type(db); lot = _lot(db, dt, qty=10)
    v = _visit(db, p, status="inserted")
    d = PelletVisitDose(visit_id=v.id, dose_type_id=dt.id, quantity=3,
                        position=1, status="inserted", lot_id=None)
    db.add(d); db.commit(); db.refresh(d)
    client = _client(client_factory, db)
    client.post(f"/api/pellets/visits/{v.id}/reopen", json={"reason": "lot"})
    r = client.patch(f"/api/pellets/visits/{v.id}/doses/{d.id}",
                     json={"lot_id": str(lot.id)})
    assert r.status_code == 200
    assert _stock(db, lot).doses_on_hand == 7


def test_correct_dose_swap_returns_old_and_pulls_new(client_factory, db):
    p = _patient(db); dt = _dose_type(db)
    lot_a = _lot(db, dt, qty=5, number="A")
    lot_b = _lot(db, dt, qty=5, number="B")
    v = _visit(db, p, status="inserted")
    d = PelletVisitDose(visit_id=v.id, dose_type_id=dt.id, quantity=2,
                        position=1, status="inserted", lot_id=lot_a.id)
    db.add(d)
    _stock(db, lot_a).doses_on_hand = 3
    db.commit(); db.refresh(d)
    client = _client(client_factory, db)
    client.post(f"/api/pellets/visits/{v.id}/reopen", json={"reason": "swap"})
    r = client.patch(f"/api/pellets/visits/{v.id}/doses/{d.id}",
                     json={"lot_id": str(lot_b.id)})
    assert r.status_code == 200
    assert _stock(db, lot_a).doses_on_hand == 5
    assert _stock(db, lot_b).doses_on_hand == 3


def test_correct_dose_historical_is_stock_neutral(client_factory, db):
    p = _patient(db); dt = _dose_type(db); lot = _lot(db, dt, qty=10)
    v = _visit(db, p, status="inserted", historical=True)
    d = PelletVisitDose(visit_id=v.id, dose_type_id=dt.id, quantity=3,
                        position=1, status="inserted", lot_id=None)
    db.add(d); db.commit(); db.refresh(d)
    client = _client(client_factory, db)
    client.post(f"/api/pellets/visits/{v.id}/reopen", json={"reason": "lot"})
    r = client.patch(f"/api/pellets/visits/{v.id}/doses/{d.id}",
                     json={"lot_id": str(lot.id)})
    assert r.status_code == 200
    assert _stock(db, lot).doses_on_hand == 10
    db.refresh(d); assert str(d.lot_id) == str(lot.id)


def test_correct_dose_requires_reopened(client_factory, db):
    p = _patient(db); dt = _dose_type(db); lot = _lot(db, dt)
    v = _visit(db, p, status="inserted")
    d = PelletVisitDose(visit_id=v.id, dose_type_id=dt.id, quantity=1,
                        position=1, status="inserted", lot_id=None)
    db.add(d); db.commit(); db.refresh(d)
    client = _client(client_factory, db)
    r = client.patch(f"/api/pellets/visits/{v.id}/doses/{d.id}",
                     json={"lot_id": str(lot.id)})
    assert r.status_code == 409


# ── Part B: legacy (non-reopened) lot-swap path regression tests ──────────
# Note: using the existing superadmin _mgr user for all three tests.
# The endpoint requires Tier.WORK; super_admin satisfies that. The point
# of these tests is to pin the planned/pulled restriction, the confirmed-
# dose 409, and the mandatory lot_id 422 so they can't silently regress.

def test_legacy_swap_lot_on_pulled_dose_reconciles_stock(client_factory, db):
    """WORK user can swap lot on a pulled dose of a non-reopened in_progress
    visit; stock on both lots reconciles and response has the narrow dose dict."""
    p = _patient(db); dt = _dose_type(db)
    lot_a = _lot(db, dt, qty=5, number="LA-1")
    lot_b = _lot(db, dt, qty=5, number="LB-1")
    v = _visit(db, p, status="in_progress")
    d = PelletVisitDose(visit_id=v.id, dose_type_id=dt.id, quantity=2,
                        position=1, status="pulled", lot_id=lot_a.id)
    db.add(d)
    # Simulate that 2 were already pulled from lot_a → it has 3 remaining.
    _stock(db, lot_a).doses_on_hand = 3
    db.commit(); db.refresh(d)

    client = _client(client_factory, db)
    r = client.patch(f"/api/pellets/visits/{v.id}/doses/{d.id}",
                     json={"lot_id": str(lot_b.id)})
    assert r.status_code == 200
    body = r.json()
    assert "dose_id" in body
    assert "lot_id" in body
    assert "qualgen_lot_number" in body
    # old lot returned → back to 5; new lot pulled → 3 remaining
    db.expire_all()
    assert _stock(db, lot_a).doses_on_hand == 5
    assert _stock(db, lot_b).doses_on_hand == 3


def test_legacy_swap_lot_on_planned_dose_reconciles_stock(client_factory, db):
    """WORK user can also swap lot on a *planned* dose of a non-reopened visit."""
    p = _patient(db); dt = _dose_type(db)
    lot_a = _lot(db, dt, qty=10, number="LP-A")
    lot_b = _lot(db, dt, qty=8, number="LP-B")
    v = _visit(db, p, status="in_progress")
    d = PelletVisitDose(visit_id=v.id, dose_type_id=dt.id, quantity=3,
                        position=1, status="planned", lot_id=lot_a.id)
    db.add(d)
    _stock(db, lot_a).doses_on_hand = 7
    db.commit(); db.refresh(d)

    client = _client(client_factory, db)
    r = client.patch(f"/api/pellets/visits/{v.id}/doses/{d.id}",
                     json={"lot_id": str(lot_b.id)})
    assert r.status_code == 200
    db.expire_all()
    assert _stock(db, lot_a).doses_on_hand == 10
    assert _stock(db, lot_b).doses_on_hand == 5


def test_legacy_swap_lot_confirmed_dose_returns_409(client_factory, db):
    """Legacy path returns 409 when the dose status is confirmed (inserted)."""
    p = _patient(db); dt = _dose_type(db)
    lot_a = _lot(db, dt, qty=5, number="LC-A")
    lot_b = _lot(db, dt, qty=5, number="LC-B")
    v = _visit(db, p, status="in_progress")
    d = PelletVisitDose(visit_id=v.id, dose_type_id=dt.id, quantity=1,
                        position=1, status="inserted", lot_id=lot_a.id)
    db.add(d); db.commit(); db.refresh(d)

    client = _client(client_factory, db)
    r = client.patch(f"/api/pellets/visits/{v.id}/doses/{d.id}",
                     json={"lot_id": str(lot_b.id)})
    assert r.status_code == 409


def test_legacy_swap_lot_omitting_lot_id_returns_422(client_factory, db):
    """Legacy path returns 422 when lot_id is omitted (mandatory on that path)."""
    p = _patient(db); dt = _dose_type(db)
    lot_a = _lot(db, dt, qty=5, number="LM-A")
    v = _visit(db, p, status="in_progress")
    d = PelletVisitDose(visit_id=v.id, dose_type_id=dt.id, quantity=1,
                        position=1, status="pulled", lot_id=lot_a.id)
    db.add(d); db.commit(); db.refresh(d)

    client = _client(client_factory, db)
    # Send empty body — lot_id is not provided
    r = client.patch(f"/api/pellets/visits/{v.id}/doses/{d.id}", json={})
    assert r.status_code == 422


# ── Part C: assert per-lot delta audit events on the reopened correction path ─

def test_dose_correction_swap_emits_per_lot_audit_events(client_factory, db):
    """After a non-historical A→B dose correction on a reopened visit,
    two PelletAuditEvent rows must exist:
      - action="dose_correction_return"  with delta_doses=+old_qty  on old lot
      - action="dose_correction_pull"   with delta_doses=-new_qty  on new lot
    """
    p = _patient(db); dt = _dose_type(db)
    lot_a = _lot(db, dt, qty=5, number="AUD-A")
    lot_b = _lot(db, dt, qty=5, number="AUD-B")
    v = _visit(db, p, status="inserted")
    d = PelletVisitDose(visit_id=v.id, dose_type_id=dt.id, quantity=2,
                        position=1, status="inserted", lot_id=lot_a.id)
    db.add(d)
    _stock(db, lot_a).doses_on_hand = 3
    db.commit(); db.refresh(d)

    client = _client(client_factory, db)
    client.post(f"/api/pellets/visits/{v.id}/reopen", json={"reason": "audit-test"})
    r = client.patch(f"/api/pellets/visits/{v.id}/doses/{d.id}",
                     json={"lot_id": str(lot_b.id)})
    assert r.status_code == 200

    # Query audit events for these two new actions
    db.expire_all()
    ret_event = (db.query(PelletAuditEvent)
                   .filter(PelletAuditEvent.action == "dose_correction_return")
                   .first())
    pull_event = (db.query(PelletAuditEvent)
                    .filter(PelletAuditEvent.action == "dose_correction_pull")
                    .first())

    assert ret_event is not None, "missing dose_correction_return audit event"
    assert pull_event is not None, "missing dose_correction_pull audit event"

    # Return event: +old_qty on old lot
    assert str(ret_event.lot_id) == str(lot_a.id)
    assert ret_event.delta_doses == 2      # old quantity returned

    # Pull event: -new_qty on new lot
    assert str(pull_event.lot_id) == str(lot_b.id)
    assert pull_event.delta_doses == -2    # new quantity pulled


def test_dose_correction_historical_no_stock_audit_events(client_factory, db):
    """Historical visit correction emits dose_corrected but NOT
    dose_correction_return or dose_correction_pull (nothing moved)."""
    p = _patient(db); dt = _dose_type(db)
    lot_a = _lot(db, dt, qty=5, number="HIST-A")
    lot_b = _lot(db, dt, qty=5, number="HIST-B")
    v = _visit(db, p, status="inserted", historical=True)
    d = PelletVisitDose(visit_id=v.id, dose_type_id=dt.id, quantity=2,
                        position=1, status="inserted", lot_id=lot_a.id)
    db.add(d); db.commit(); db.refresh(d)

    client = _client(client_factory, db)
    client.post(f"/api/pellets/visits/{v.id}/reopen", json={"reason": "hist-test"})
    r = client.patch(f"/api/pellets/visits/{v.id}/doses/{d.id}",
                     json={"lot_id": str(lot_b.id)})
    assert r.status_code == 200

    db.expire_all()
    ret_count = (db.query(PelletAuditEvent)
                   .filter(PelletAuditEvent.action == "dose_correction_return")
                   .count())
    pull_count = (db.query(PelletAuditEvent)
                    .filter(PelletAuditEvent.action == "dose_correction_pull")
                    .count())
    assert ret_count == 0, "historical correction must not emit stock return event"
    assert pull_count == 0, "historical correction must not emit stock pull event"


def test_missing_lot_count_and_view(client_factory, db):
    p1 = _patient(db)
    _visit(db, p1, status="inserted")  # zero doses → missing
    p2 = PelletPatient(patient_name="Ok, Pat", chart_number="222",
                       patient_dob=date(1980, 1, 1))
    db.add(p2); db.commit(); db.refresh(p2)
    dt = _dose_type(db); lot = _lot(db, dt)
    v2 = _visit(db, p2, status="inserted")
    db.add(PelletVisitDose(visit_id=v2.id, dose_type_id=dt.id, quantity=1,
                           position=1, status="inserted", lot_id=lot.id))
    db.commit()
    client = _client(client_factory, db)
    counts = client.get("/api/pellets/patient-view-counts").json()
    assert counts["missing_lot"] == 1
    lst = client.get("/api/pellets/patients?view=missing_lot").json()
    rows = lst if isinstance(lst, list) else lst.get("patients", lst.get("items", []))
    names = [row["patient_name"] for row in rows]
    assert "Tober, Catrina" in names and "Ok, Pat" not in names
