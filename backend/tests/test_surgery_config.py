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


def test_recipients_empty_by_default(client):
    resp = client.get("/api/surgery/admin/alert-recipients")
    assert resp.status_code == 200
    assert resp.json() == {"office_release": [], "hospital_release": []}


def test_add_recipient(client):
    resp = client.post("/api/surgery/admin/alert-recipients",
                       json={"alert_kind": "office_release",
                              "email": "manager@waldorfwomenscare.com"})
    assert resp.status_code == 201
    out = client.get("/api/surgery/admin/alert-recipients").json()
    assert "manager@waldorfwomenscare.com" in out["office_release"]


def test_dup_recipient_returns_409(client):
    client.post("/api/surgery/admin/alert-recipients",
                json={"alert_kind": "office_release", "email": "a@b.com"})
    resp = client.post("/api/surgery/admin/alert-recipients",
                       json={"alert_kind": "office_release", "email": "a@b.com"})
    assert resp.status_code == 409


def test_unknown_alert_kind_returns_422(client):
    resp = client.post("/api/surgery/admin/alert-recipients",
                       json={"alert_kind": "totally_made_up", "email": "x@y.com"})
    assert resp.status_code == 422


def test_delete_recipient(client):
    client.post("/api/surgery/admin/alert-recipients",
                json={"alert_kind": "office_release", "email": "x@y.com"})
    resp = client.delete("/api/surgery/admin/alert-recipients",
                          params={"alert_kind": "office_release", "email": "x@y.com"})
    assert resp.status_code == 204
    out = client.get("/api/surgery/admin/alert-recipients").json()
    assert out["office_release"] == []
