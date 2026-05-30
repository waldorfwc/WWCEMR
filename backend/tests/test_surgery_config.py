"""Phase B config endpoints — coverage for the four admin areas."""

def test_get_config_returns_defaults_when_empty(client):
    resp = client.get("/api/surgery/config")
    assert resp.status_code == 200
    body = resp.json()
    assert body["office_full_threshold"] == 6
    assert body["office_lookahead_days"] == 6
    assert body["hospital_lookahead_days"] == 14


def test_put_config_persists_values(client):
    resp = client.put("/api/surgery/config", json={
        "office_full_threshold": 8,
        "hospital_lookahead_days": 21,
    })
    assert resp.status_code == 200
    body = client.get("/api/surgery/config").json()
    assert body["office_full_threshold"] == 8
    assert body["office_lookahead_days"] == 6      # untouched, falls back to default
    assert body["hospital_lookahead_days"] == 21


def test_put_config_rejects_unknown_key(client):
    # Unknown keys silently ignored — Pydantic discards them.
    resp = client.put("/api/surgery/config", json={"bogus_key": 9000})
    assert resp.status_code == 200
    body = client.get("/api/surgery/config").json()
    assert "bogus_key" not in body
