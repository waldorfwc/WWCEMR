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
