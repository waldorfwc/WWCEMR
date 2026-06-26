from app.models.surgery import Surgery


def _mk(db, **kw):
    s = Surgery(chart_number="BW1", patient_name="Doe, J", status="new", **kw)
    db.add(s); db.commit(); db.refresh(s)
    return s


def test_deductible_waived_zeroes_deductible_in_calc(client, db):
    s = _mk(db)
    # allowed 10000, deductible 2000 unmet, 20% coinsurance, no OOP cap.
    base = {"allowed_amount": 10000, "deductible": 2000, "deductible_met": 0,
            "copay": 0, "coinsurance_pct": 20, "oop_max": 0, "oop_met": 0}
    # Without waiver: 2000 ded + 20% of 8000 = 3600.
    r = client.post(f"/api/surgery/{s.id}/benefits", json={**base, "save": False})
    assert r.status_code == 200, r.text
    assert float(r.json()["patient_responsibility"]) == 3600.0
    # With waiver: deductible treated as 0 → 20% of 10000 = 2000.
    r = client.post(f"/api/surgery/{s.id}/benefits",
                    json={**base, "deductible_waived": True, "save": True})
    assert r.status_code == 200, r.text
    assert float(r.json()["patient_responsibility"]) == 2000.0
    db.expire_all()
    s2 = db.query(Surgery).get(s.id)
    assert s2.deductible_waived is True
    assert float(s2.patient_responsibility) == 2000.0


def test_secondary_deductible_waived(client, db):
    s = _mk(db, secondary_insurance="Aetna Secondary")
    body = {"allowed_amount": 1000, "deductible": 0, "coinsurance_pct": 0,
            "secondary_deductible": 500, "secondary_deductible_met": 0,
            "secondary_coinsurance_pct": 0, "secondary_deductible_waived": True,
            "save": True}
    r = client.post(f"/api/surgery/{s.id}/benefits", json=body)
    assert r.status_code == 200, r.text
    # primary owes 1000; secondary deductible waived (0) + 0% coins → patient 0.
    assert float(r.json()["patient_responsibility"]) == 0.0
    db.expire_all()
    assert db.query(Surgery).get(s.id).secondary_deductible_waived is True
