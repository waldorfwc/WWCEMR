"""Regression tests for the Set-Dose-Card double-pull bug.

A `planned` dose created by Set Dose Card already has a lot and already
decremented stock. fill_bag and confirm_doses_as_planned used to debit it
AGAIN (treating every `planned` dose as un-pulled), corrupting the
Schedule III ledger. These tests pin the fix and the legitimate single-pull
paths.
"""
from datetime import date

from app.models.user import User
from app.models.pellet import (
    PelletPatient, PelletVisit, PelletVisitDose, PelletDoseType,
    PelletLot, PelletStock,
)


def _mgr(db):
    u = User(email="dp@waldorfwomenscare.com", display_name="DP", is_super_admin=True)
    db.add(u); db.commit()
    return u


def _client(client_factory, db):
    return client_factory(user=_mgr(db))


def _patient(db):
    p = PelletPatient(patient_name="Doe, Jane", chart_number="DP1",
                      patient_dob=date(1980, 1, 1))
    db.add(p); db.commit(); db.refresh(p)
    return p


def _dose_type(db, label="Testosterone 200mg"):
    dt = PelletDoseType(label=label, hormone="testosterone", dose_mg=200,
                        is_controlled=True)
    db.add(dt); db.commit(); db.refresh(dt)
    return dt


def _lot(db, dt, qty=10, loc="white_plains", number="LOT-A"):
    lot = PelletLot(dose_type_id=dt.id, qualgen_lot_number=number,
                    expiration_date=date(2027, 1, 1), doses_originally_received=qty)
    db.add(lot); db.flush()
    db.add(PelletStock(lot_id=lot.id, location=loc, doses_on_hand=qty, status="active"))
    db.commit(); db.refresh(lot)
    return lot


def _visit(db, p, loc="white_plains"):
    v = PelletVisit(patient_id=p.id, visit_kind="initial", status="in_progress",
                    location=loc, scheduled_date=date(2026, 6, 5))
    db.add(v); db.commit(); db.refresh(v)
    return v


def _stock(db, lot, loc="white_plains"):
    db.expire_all()
    return (db.query(PelletStock)
              .filter(PelletStock.lot_id == lot.id, PelletStock.location == loc)
              .first())


# --- the bug: set dose card then fill bag must NOT double-decrement ---

def test_set_dose_card_then_fill_bag_no_double_pull(client_factory, db):
    p = _patient(db); dt = _dose_type(db); lot = _lot(db, dt, qty=10)
    v = _visit(db, p)
    c = _client(client_factory, db)
    r = c.put(f"/api/pellets/visits/{v.id}/dose-card",
              json={"doses": [{"dose_type_id": str(dt.id), "quantity": 3, "lot_id": str(lot.id)}]})
    assert r.status_code == 200, r.text
    assert _stock(db, lot).doses_on_hand == 7   # set-card pulled 3
    dose_id = r.json()["doses"][0]["id"]
    r2 = c.post(f"/api/pellets/visits/{v.id}/fill-bag",
                json={"lines": [{"visit_dose_id": dose_id, "lot_id": str(lot.id)}]})
    assert r2.status_code == 200, r2.text
    assert _stock(db, lot).doses_on_hand == 7   # STILL 7 — not 4 (no double pull)


def test_set_dose_card_then_confirm_as_planned_no_double_pull(client_factory, db):
    p = _patient(db); dt = _dose_type(db); lot = _lot(db, dt, qty=10)
    v = _visit(db, p)
    c = _client(client_factory, db)
    r = c.put(f"/api/pellets/visits/{v.id}/dose-card",
              json={"doses": [{"dose_type_id": str(dt.id), "quantity": 3, "lot_id": str(lot.id)}]})
    assert r.status_code == 200, r.text
    assert _stock(db, lot).doses_on_hand == 7
    r2 = c.post(f"/api/pellets/visits/{v.id}/confirm-as-planned")
    assert r2.status_code == 200, r2.text
    assert r2.json()["status"] == "inserted"
    assert _stock(db, lot).doses_on_hand == 7   # not 4


# --- regressions: the legitimate single-pull paths still work ---

def test_fill_bag_plan_only_dose_pulls_once(client_factory, db):
    p = _patient(db); dt = _dose_type(db); lot = _lot(db, dt, qty=10)
    v = _visit(db, p)
    d = PelletVisitDose(visit_id=v.id, dose_type_id=dt.id, quantity=3,
                        position=1, status="planned", lot_id=None)
    db.add(d); db.commit(); db.refresh(d)
    c = _client(client_factory, db)
    r = c.post(f"/api/pellets/visits/{v.id}/fill-bag",
               json={"lines": [{"visit_dose_id": str(d.id), "lot_id": str(lot.id)}]})
    assert r.status_code == 200, r.text
    assert _stock(db, lot).doses_on_hand == 7   # one pull
    db.refresh(d); assert d.status == "pulled" and str(d.lot_id) == str(lot.id)


def test_confirm_as_planned_plan_only_dose_pulls_once(client_factory, db):
    p = _patient(db); dt = _dose_type(db); lot = _lot(db, dt, qty=10)
    v = _visit(db, p)
    d = PelletVisitDose(visit_id=v.id, dose_type_id=dt.id, quantity=3,
                        position=1, status="planned", lot_id=None)
    db.add(d); db.commit(); db.refresh(d)
    c = _client(client_factory, db)
    r = c.post(f"/api/pellets/visits/{v.id}/confirm-as-planned")
    assert r.status_code == 200, r.text
    assert _stock(db, lot).doses_on_hand == 7
    db.refresh(d); assert d.status == "inserted" and d.lot_id is not None


def test_fill_bag_reserved_dose_lot_swap(client_factory, db):
    p = _patient(db); dt = _dose_type(db)
    lot_a = _lot(db, dt, qty=10, number="A"); lot_b = _lot(db, dt, qty=10, number="B")
    v = _visit(db, p)
    c = _client(client_factory, db)
    r = c.put(f"/api/pellets/visits/{v.id}/dose-card",
              json={"doses": [{"dose_type_id": str(dt.id), "quantity": 3, "lot_id": str(lot_a.id)}]})
    assert r.status_code == 200, r.text
    assert _stock(db, lot_a).doses_on_hand == 7
    dose_id = r.json()["doses"][0]["id"]
    # fill bag picks a DIFFERENT lot -> return A, pull B
    r2 = c.post(f"/api/pellets/visits/{v.id}/fill-bag",
                json={"lines": [{"visit_dose_id": dose_id, "lot_id": str(lot_b.id)}]})
    assert r2.status_code == 200, r2.text
    assert _stock(db, lot_a).doses_on_hand == 10   # A returned
    assert _stock(db, lot_b).doses_on_hand == 7    # B pulled
