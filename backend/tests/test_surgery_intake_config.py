"""Surgery intake option lists — clearance_types + surgery_device_types
configurable via /surgery/config (B2)."""


def test_config_includes_intake_defaults(client):
    body = client.get("/api/surgery/config").json()
    assert body["clearance_types"] == [
        "None", "EKG", "Hematology", "Cardiology", "Pulmonology", "General",
    ]
    assert body["surgery_device_types"] == [
        "None", "Benesta", "Liletta", "Mirena", "Paragard", "Skyla", "Kyleena",
    ]
    assert body["assistant_surgeons"] == ["None", "Dr. Gillespie"]


def test_put_clearance_types_roundtrips(client):
    resp = client.put("/api/surgery/config", json={
        "clearance_types": ["EKG", "Cardiology", "Renal"],
    })
    assert resp.status_code == 200, resp.text
    body = client.get("/api/surgery/config").json()
    assert body["clearance_types"] == ["EKG", "Cardiology", "Renal"]
    # device list untouched → still the default (full-replace per-key)
    assert body["surgery_device_types"] == [
        "None", "Benesta", "Liletta", "Mirena", "Paragard", "Skyla", "Kyleena",
    ]


def test_put_assistant_surgeons_roundtrips(client):
    resp = client.put("/api/surgery/config", json={
        "assistant_surgeons": ["None", "Dr. Gillespie", "Dr. Patel"],
    })
    assert resp.status_code == 200, resp.text
    body = client.get("/api/surgery/config").json()
    assert body["assistant_surgeons"] == ["None", "Dr. Gillespie", "Dr. Patel"]


def test_put_device_types_dedupes_and_preserves_order(client):
    resp = client.put("/api/surgery/config", json={
        "surgery_device_types": ["Mirena", "Paragard", "Mirena", "Skyla"],
    })
    assert resp.status_code == 200, resp.text
    body = client.get("/api/surgery/config").json()
    assert body["surgery_device_types"] == ["Mirena", "Paragard", "Skyla"]


def test_put_empty_list_rejected(client):
    resp = client.put("/api/surgery/config", json={"clearance_types": []})
    assert resp.status_code == 422


def test_put_blank_entry_rejected(client):
    resp = client.put("/api/surgery/config", json={
        "surgery_device_types": ["Mirena", "   "],
    })
    assert resp.status_code == 422


# ── payer_id_insurance_map (order-prefill resolution) ──

def test_config_includes_payer_id_map_default(client):
    body = client.get("/api/surgery/config").json()
    pm = body["payer_id_insurance_map"]
    # Seeded with high-confidence national payers + the verified WWC order one.
    assert pm["75191"] == "Blue Cross & Blue Shield PPO"
    assert pm["60054"] == "Aetna"
    assert pm["87726"] == "UnitedHealthcare"
    # Every mapped value must be a real picklist company (so the dropdown
    # selects it) — guards against typos in the seed.
    picks = client.get("/api/surgery/picklists").json()["insurance_companies"]
    for payer_id, company in pm.items():
        assert company in picks, f"{payer_id} → {company!r} not in insurance picklist"


def test_put_payer_id_map_roundtrips(client):
    resp = client.put("/api/surgery/config", json={
        "payer_id_insurance_map": {"75191": "Blue Cross & Blue Shield PPO",
                                   "60054": "Aetna"},
    })
    assert resp.status_code == 200, resp.text
    body = client.get("/api/surgery/config").json()
    # full-replace: the stored map is exactly what we PUT
    assert body["payer_id_insurance_map"] == {
        "75191": "Blue Cross & Blue Shield PPO",
        "60054": "Aetna",
    }


def test_put_payer_id_map_bad_key_rejected(client):
    resp = client.put("/api/surgery/config", json={
        "payer_id_insurance_map": {"ABC": "Aetna"},
    })
    assert resp.status_code == 422


def test_put_payer_id_map_blank_value_rejected(client):
    resp = client.put("/api/surgery/config", json={
        "payer_id_insurance_map": {"75191": "   "},
    })
    assert resp.status_code == 422
