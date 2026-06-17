"""Authenticated walk-through of the damaged-pellet REPLACEMENT receive flow:
an in-stock lot has pellets disposed (broken), then Qualgen resends pellets
for that disposal — a replacement receipt skips the order, references the
disposal, and (after manifest-verify) restores inventory.
"""
from datetime import date, timedelta

from app.models.pellet import PelletDoseType, PelletLot, PelletDisposal


def _dose_type(db):
    dt = PelletDoseType(hormone="estradiol", dose_mg=12.5, label="Estradiol 12.5mg")
    db.add(dt); db.commit(); db.refresh(dt)
    return dt


def _lot(dt, *, pack_size, packs, doses, lot_no):
    return {
        "dose_type_id": str(dt.id),
        "qualgen_lot_number": lot_no,
        "expiration_date": (date.today() + timedelta(days=400)).isoformat(),
        "pack_size": pack_size, "packs_received": packs, "doses_received": doses,
    }


def _on_hand(client, dt):
    types = client.get("/api/pellets/dose-types").json()
    row = next((t for t in types if t["id"] == str(dt.id)), None)
    return (row or {}).get("on_hand_doses", 0)


def test_replacement_walkthrough(client, db, capsys):
    log = []
    dt = _dose_type(db)

    # 1. Original lot in inventory (unscheduled receive → verify): 6×4 = 24 doses.
    r = client.post("/api/pellets/receipts", json={
        "location": "white_plains", "is_unscheduled": True,
        "notes": "baseline stock for replacement walk-through",
        "lots": [_lot(dt, pack_size=6, packs=4, doses=24, lot_no="QG-ORIG")]})
    assert r.status_code == 201, r.text
    rid = r.json()["receipt_id"]
    assert client.post(f"/api/pellets/receipts/{rid}/verify-manifest", json={}).status_code == 200
    assert _on_hand(client, dt) == 24
    log.append("1. original lot QG-ORIG received + verified → 24 doses on hand")

    # 2. Three doses break and are disposed.
    lot = db.query(PelletLot).filter(PelletLot.qualgen_lot_number == "QG-ORIG").first()
    d = client.post("/api/pellets/disposals", json={
        "lot_id": str(lot.id), "location": "white_plains", "doses": 3, "reason": "broken"})
    assert d.status_code == 201, d.text
    disposal_id = d.json()["disposal_id"]
    assert _on_hand(client, dt) == 21
    log.append("2. disposed 3 broken doses → 21 on hand; disposal recorded")

    # 3. A replacement receipt must reference the disposal.
    bad = client.post("/api/pellets/receipts", json={
        "location": "white_plains", "is_replacement": True,
        "lots": [_lot(dt, pack_size=3, packs=1, doses=3, lot_no="QG-REPL")]})
    assert bad.status_code == 422   # is_replacement requires replaces_disposal_id
    log.append("3. replacement receipt with no disposal reference → 422")

    # 4. Qualgen resends the 3 pellets — replacement receipt referencing the disposal.
    rep = client.post("/api/pellets/receipts", json={
        "location": "white_plains", "is_replacement": True,
        "replaces_disposal_id": disposal_id,
        "lots": [_lot(dt, pack_size=3, packs=1, doses=3, lot_no="QG-REPL")]})
    assert rep.status_code == 201, rep.text
    rep_id = rep.json()["receipt_id"]
    log.append("4. replacement receipt accepted (references the disposal, skips an order)")

    # 5. Manifest-verify the replacement → inventory restored to 24.
    assert client.post(f"/api/pellets/receipts/{rep_id}/verify-manifest", json={}).status_code == 200
    assert _on_hand(client, dt) == 24
    log.append("5. replacement manifest-verified → inventory restored to 24 doses")

    with capsys.disabled():
        print("\n  -- Pellet damaged-pellet replacement walk-through (authenticated) --")
        for line in log:
            print("   " + line)
