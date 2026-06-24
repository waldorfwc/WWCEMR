from datetime import date
from app.models.pellet import PelletLot, PelletDoseType


def _dt(db, label="Testosterone 100mg"):
    dt = PelletDoseType(label=label, hormone="testosterone", dose_mg=100, is_controlled=True)
    db.add(dt); db.commit(); db.refresh(dt)
    return dt


def test_pellet_lot_has_location_column(db):
    dt = _dt(db)
    lot = PelletLot(dose_type_id=dt.id, qualgen_lot_number="L1",
                    expiration_date=date(2027, 1, 1), doses_originally_received=10,
                    location="white_plains")
    db.add(lot); db.commit(); db.refresh(lot)
    assert lot.location == "white_plains"


from app.models.pellet import (
    PelletStock, PelletVisitDose, PelletAuditEvent, PelletPatient, PelletVisit,
)
from app.services.pellet.lot_merge import merge_lot, UNKNOWN_EXP


def _lot(db, dt, *, number, loc, exp=date(2027, 1, 1), orig=10, on_hand=None,
         receipt_id=None):
    lot = PelletLot(dose_type_id=dt.id, qualgen_lot_number=number,
                    expiration_date=exp, doses_originally_received=orig,
                    location=loc, receipt_id=receipt_id)
    db.add(lot); db.flush()
    if on_hand is not None:
        db.add(PelletStock(lot_id=lot.id, location=loc, doses_on_hand=on_hand, status="active"))
    db.commit(); db.refresh(lot)
    return lot


def _oh(db, lot, loc):
    s = (db.query(PelletStock)
           .filter(PelletStock.lot_id == lot.id, PelletStock.location == loc).first())
    return s.doses_on_hand if s else 0


def test_merge_lot_repoints_stock_doses_audit_and_deletes_src(db):
    dt = _dt(db)
    dst = _lot(db, dt, number="L9", loc="white_plains", exp=date(2027, 5, 1), orig=20, on_hand=5)
    src = _lot(db, dt, number="L9", loc="white_plains", exp=UNKNOWN_EXP, orig=8, on_hand=3)
    p = PelletPatient(patient_name="A", chart_number="C1", patient_dob=date(1980, 1, 1))
    db.add(p); db.flush()
    v = PelletVisit(patient_id=p.id, visit_kind="initial", status="inserted",
                    location="white_plains", scheduled_date=date(2026, 6, 1))
    db.add(v); db.flush()
    d = PelletVisitDose(visit_id=v.id, dose_type_id=dt.id, quantity=2, position=1,
                        status="inserted", lot_id=src.id)
    db.add(d)
    db.add(PelletAuditEvent(actor="x", action="dose_pulled", lot_id=src.id, delta_doses=-2))
    db.commit()

    res = merge_lot(db, src=src, dst=dst, actor="system:test")
    db.commit()

    assert res["merged"] is True
    assert db.query(PelletLot).filter(PelletLot.id == src.id).first() is None
    assert _oh(db, dst, "white_plains") == 8
    assert db.query(PelletStock).filter(PelletStock.lot_id == src.id).count() == 0
    db.refresh(d); assert str(d.lot_id) == str(dst.id)
    assert db.query(PelletAuditEvent).filter(
        PelletAuditEvent.action == "dose_pulled",
        PelletAuditEvent.lot_id == dst.id).count() == 1
    db.refresh(dst)
    assert dst.doses_originally_received == 28
    assert dst.expiration_date == date(2027, 5, 1)
    assert db.query(PelletAuditEvent).filter(
        PelletAuditEvent.action == "lot_merged", PelletAuditEvent.lot_id == dst.id).count() == 1


def test_merge_lot_carries_real_exp_onto_placeholder_canonical(db):
    dt = _dt(db)
    dst = _lot(db, dt, number="L8", loc="brandywine", exp=UNKNOWN_EXP, orig=5, on_hand=0)
    src = _lot(db, dt, number="L8", loc="brandywine", exp=date(2027, 9, 1), orig=4, on_hand=4)
    merge_lot(db, src=src, dst=dst, actor="system:test"); db.commit()
    db.refresh(dst)
    assert dst.expiration_date == date(2027, 9, 1)
    assert _oh(db, dst, "brandywine") == 4


from app.models.user import User

_WITNESS_EMAIL = "witness@waldorfwomenscare.com"


def _mgr(db):
    u = User(email="dedup@waldorfwomenscare.com", display_name="D", is_super_admin=True)
    db.add(u); db.commit()
    # Also create the witness user so controlled-lot verify passes
    w = User(email=_WITNESS_EMAIL, display_name="W", is_super_admin=True)
    db.add(w); db.commit()
    return u


def _receive_and_verify(client, dt, *, number, loc, doses, exp="2027-03-01"):
    r = client.post("/api/pellets/receipts", json={
        "location": loc, "is_unscheduled": True, "notes": "test receive",
        "lots": [{"dose_type_id": str(dt.id), "qualgen_lot_number": number,
                  "expiration_date": exp, "doses_received": doses}],
    })
    assert r.status_code == 201, r.text
    rid = r.json()["receipt_id"]
    v = client.post(f"/api/pellets/receipts/{rid}/verify-manifest",
                    json={"witness_user": _WITNESS_EMAIL})
    assert v.status_code == 200, v.text
    return rid


def test_verify_merges_second_receipt_of_same_lot_same_office(client_factory, db):
    dt = _dt(db); u = _mgr(db); client = client_factory(user=u)
    _receive_and_verify(client, dt, number="LZ", loc="white_plains", doses=10)
    _receive_and_verify(client, dt, number="LZ", loc="white_plains", doses=6)
    lots = db.query(PelletLot).filter(PelletLot.qualgen_lot_number == "LZ").all()
    assert len(lots) == 1
    assert _oh(db, lots[0], "white_plains") == 16
    assert lots[0].doses_originally_received == 16


def test_verify_keeps_same_lot_at_two_offices_separate(client_factory, db):
    dt = _dt(db); u = _mgr(db); client = client_factory(user=u)
    _receive_and_verify(client, dt, number="LX", loc="white_plains", doses=10)
    _receive_and_verify(client, dt, number="LX", loc="brandywine", doses=7)
    lots = db.query(PelletLot).filter(PelletLot.qualgen_lot_number == "LX").all()
    assert len(lots) == 2
    by_loc = {l.location: l for l in lots}
    assert _oh(db, by_loc["white_plains"], "white_plains") == 10
    assert _oh(db, by_loc["brandywine"], "brandywine") == 7
