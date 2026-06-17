"""Authenticated walk-through of the Qualgen receive-shipment flow, focused
on the 'Unscheduled' (found-in-cabinet) mode with a free-form pack size:
create an unscheduled receipt (notes required) with a non-standard pack_size,
the pack×packs=doses cross-check still applies, then manifest-verify to bring
the lot into inventory.
"""
from datetime import date, timedelta

from app.models.pellet import PelletDoseType, PelletLot


def _dose_type(db):
    dt = PelletDoseType(hormone="estradiol", dose_mg=12.5, label="Estradiol 12.5mg")
    db.add(dt); db.commit(); db.refresh(dt)
    return dt


def _lot(dt, *, pack_size, packs, doses, lot_no="QG-CAB-1"):
    return {
        "dose_type_id": str(dt.id),
        "qualgen_lot_number": lot_no,
        "expiration_date": (date.today() + timedelta(days=400)).isoformat(),
        "pack_size": pack_size,
        "packs_received": packs,
        "doses_received": doses,
    }


def test_receive_shipment_walkthrough(client, db, capsys):
    log = []
    dt = _dose_type(db)

    # 1. Unscheduled receipt requires a notes explanation.
    no_notes = client.post("/api/pellets/receipts", json={
        "location": "white_plains", "is_unscheduled": True,
        "lots": [_lot(dt, pack_size=10, packs=2, doses=20)]})
    assert no_notes.status_code == 422
    log.append("1. unscheduled receipt with no notes → 422 (explanation required)")

    # 2. Pack × packs must equal doses — the cross-check still applies to a
    #    free-form (non-6/12/30) pack size.
    bad_math = client.post("/api/pellets/receipts", json={
        "location": "white_plains", "is_unscheduled": True,
        "notes": "found in white plains cabinet during count",
        "lots": [_lot(dt, pack_size=10, packs=2, doses=25)]})
    assert bad_math.status_code == 422
    log.append("2. pack 10 × 2 ≠ 25 doses → 422 (pack/doses cross-check holds for any pack size)")

    # 3. Valid unscheduled receipt with a non-standard pack size of 10.
    r = client.post("/api/pellets/receipts", json={
        "location": "white_plains", "is_unscheduled": True,
        "notes": "found in white plains cabinet during count",
        "lots": [_lot(dt, pack_size=10, packs=2, doses=20)]})
    assert r.status_code == 201, r.text
    receipt_id = r.json()["receipt_id"]
    lot = db.query(PelletLot).filter(PelletLot.qualgen_lot_number == "QG-CAB-1").first()
    assert lot is not None and lot.pack_size == 10          # free-form pack persisted
    assert lot.doses_originally_received == 20
    log.append("3. unscheduled receipt accepted with pack_size=10 → lot created (20 doses), "
               "stock not yet live")

    # 4. Manifest-verify brings the lot into inventory (estradiol = not controlled, no witness).
    v = client.post(f"/api/pellets/receipts/{receipt_id}/verify-manifest", json={})
    assert v.status_code == 200, v.text
    log.append("4. manifest-verified → stock incremented (lot now usable)")

    # 5. The dose type now shows the 20 received doses on hand.
    types = client.get("/api/pellets/dose-types").json()
    row = next((t for t in types if t["id"] == str(dt.id)), None)
    assert row is not None and row.get("on_hand_doses", 0) >= 20
    log.append(f"5. dose-types inventory: {row['on_hand_doses']} doses on hand for {dt.label}")

    with capsys.disabled():
        print("\n  -- Pellet receive-shipment walk-through (authenticated) --")
        for line in log:
            print("   " + line)
