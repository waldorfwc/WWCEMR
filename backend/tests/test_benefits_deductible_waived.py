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


def test_waiver_flag_returned_by_surgery_get(client, db):
    # The card reads surgery.deductible_waived to initialize the checkbox on
    # reload — so the GET must surface it.
    s = _mk(db)
    client.post(f"/api/surgery/{s.id}/benefits",
                json={"allowed_amount": 5000, "deductible": 1000,
                      "coinsurance_pct": 10, "deductible_waived": True, "save": True})
    got = client.get(f"/api/surgery/{s.id}").json()
    assert got["deductible_waived"] is True
    assert got["secondary_deductible_waived"] is False


def test_waiver_still_capped_by_oop_max(client, db):
    s = _mk(db)
    # Waived deductible: 20% of 10000 = 2000, but OOP-max remaining is 800 → 800.
    r = client.post(f"/api/surgery/{s.id}/benefits",
                    json={"allowed_amount": 10000, "deductible": 2000,
                          "coinsurance_pct": 20, "oop_max": 800, "oop_met": 0,
                          "deductible_waived": True, "save": False})
    assert float(r.json()["patient_responsibility"]) == 800.0


def test_toggle_waiver_off_restores_deductible(client, db):
    s = _mk(db)
    base = {"allowed_amount": 10000, "deductible": 2000, "coinsurance_pct": 20,
            "oop_max": 0, "save": True}
    client.post(f"/api/surgery/{s.id}/benefits", json={**base, "deductible_waived": True})
    r = client.post(f"/api/surgery/{s.id}/benefits", json={**base, "deductible_waived": False})
    # waiver off → deductible back in play: 2000 + 20% of 8000 = 3600.
    assert float(r.json()["patient_responsibility"]) == 3600.0
    db.expire_all()
    assert db.query(Surgery).get(s.id).deductible_waived is False


def test_estimate_pdf_renders_with_waiver():
    # The estimate PDF must render (no crash) when the deductible is waived,
    # for both primary and secondary.
    from app.services.surgery.benefits_pdf import generate_bytes
    s = Surgery(chart_number="PDF1", patient_name="Doe, J", status="new",
                allowed_amount=10000, deductible=2000, coinsurance_pct=20,
                deductible_waived=True,
                secondary_insurance="Aetna 2nd", secondary_deductible=500,
                secondary_deductible_waived=True)
    breakdown = {
        "deductible_remaining": 0, "deductible_portion": 0, "after_deductible": 10000,
        "coinsurance_portion": 2000, "copay_portion": 0, "oop_remaining": None,
        "raw_responsibility": 2000, "primary_patient_owed": 2000,
        "capped_by_oop_max": False,
        "secondary": {"deductible_remaining": 0, "deductible_portion": 0,
                      "coinsurance_portion": 0, "patient_owed": 2000},
        "patient_responsibility": 2000,
    }
    pdf = generate_bytes(s, breakdown)
    assert pdf[:4] == b"%PDF" and len(pdf) > 1000


def _pr_to_float(v):
    return float(str(v).replace("$", "").replace(",", "")) if v is not None else None


def test_patient_portal_dashboard_reflects_waiver(client, db):
    # The patient sees the WAIVED responsibility in the portal dashboard.
    from app.services.patient_portal_auth import issue_portal_token
    s = _mk(db)
    # 20% of 10000 = 2000 when waived (vs 2000 ded + 20% of 8000 = 3600 normally).
    client.post(f"/api/surgery/{s.id}/benefits",
                json={"allowed_amount": 10000, "deductible": 2000,
                      "coinsurance_pct": 20, "deductible_waived": True, "save": True})
    token = issue_portal_token(s)
    r = client.get(f"/api/patient/portal/{s.id}/dashboard",
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    pr = _pr_to_float(r.json()["surgery"]["patient_responsibility"])
    assert pr == 2000.0, f"portal showed {pr}, expected the waived 2000"
    import json as _json
    assert "3600" not in _json.dumps(r.json())   # un-waived amount never shown


def test_patient_portal_dashboard_without_waiver(client, db):
    # Control: same inputs, no waiver → patient sees 3600.
    from app.services.patient_portal_auth import issue_portal_token
    s = _mk(db)
    client.post(f"/api/surgery/{s.id}/benefits",
                json={"allowed_amount": 10000, "deductible": 2000,
                      "coinsurance_pct": 20, "deductible_waived": False, "save": True})
    token = issue_portal_token(s)
    r = client.get(f"/api/patient/portal/{s.id}/dashboard",
                   headers={"Authorization": f"Bearer {token}"})
    assert _pr_to_float(r.json()["surgery"]["patient_responsibility"]) == 3600.0
