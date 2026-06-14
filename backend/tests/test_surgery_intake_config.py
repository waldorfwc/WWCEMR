"""Surgery intake option lists — clearance_types + surgery_device_types
configurable via /surgery/config (B2)."""


def test_config_includes_intake_defaults(client):
    body = client.get("/api/surgery/config").json()
    assert body["clearance_types"] == [
        "EKG", "Hematology", "Cardiology", "Pulmonology", "General",
    ]
    assert body["surgery_device_types"] == [
        "Benesta", "Liletta", "Mirena", "Paragard", "Skyla", "Kyleena",
    ]


def test_put_clearance_types_roundtrips(client):
    resp = client.put("/api/surgery/config", json={
        "clearance_types": ["EKG", "Cardiology", "Renal"],
    })
    assert resp.status_code == 200, resp.text
    body = client.get("/api/surgery/config").json()
    assert body["clearance_types"] == ["EKG", "Cardiology", "Renal"]
    # device list untouched → still the default (full-replace per-key)
    assert body["surgery_device_types"] == [
        "Benesta", "Liletta", "Mirena", "Paragard", "Skyla", "Kyleena",
    ]


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
