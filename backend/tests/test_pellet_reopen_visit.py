from datetime import date
from app.models.user import User
from app.models.pellet import (
    PelletPatient, PelletVisit, PelletVisitDose, PelletDoseType,
    PelletLot, PelletStock,
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
