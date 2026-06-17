def test_put_pellet_config_accepts_new_keys(client, db):
    r = client.put("/api/pellets/config", json={
        "require_mammo": True, "require_labs": False, "require_consent": True,
        "consent_template_id": "tmpl-123",
    })
    assert r.status_code == 200, r.text
    got = client.get("/api/pellets/config").json()
    assert got["require_labs"] is False
    assert got["consent_template_id"] == "tmpl-123"
    assert got["require_mammo"] is True


def test_pellet_config_defaults_present(client, db):
    got = client.get("/api/pellets/config").json()
    # Defaults exist even before any PUT.
    assert got["require_consent"] is True
    assert got["consent_template_id"] is None
