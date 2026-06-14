"""R3: validated GET/PUT /recalls/config endpoints."""


def test_get_recall_config_returns_defaults(client):
    r = client.get("/api/recalls/config")
    assert r.status_code == 200
    body = r.json()
    assert body["claim_ttl_minutes"] == 5
    assert body["overdue_window_months"] == 24
    assert isinstance(body["recall_outcomes"], list)


def test_put_scalar_roundtrip(client):
    assert client.put("/api/recalls/config",
                      json={"claim_ttl_minutes": 10,
                            "overdue_window_months": 36}).status_code == 200
    body = client.get("/api/recalls/config").json()
    assert body["claim_ttl_minutes"] == 10
    assert body["overdue_window_months"] == 36


def test_put_recall_outcomes_roundtrip_and_catalog(client):
    outs = [
        {"label": "No answer", "category": "cooldown", "cooldown_days": 4},
        {"label": "Scheduled", "category": "completed"},
        {"label": "Do not call", "category": "permanent", "reason_code": "do_not_call"},
    ]
    r = client.put("/api/recalls/config", json={"recall_outcomes": outs})
    assert r.status_code == 200, r.text
    assert r.json()["recall_outcomes"] == outs

    cat = {o["value"]: o for o in
           client.get("/api/recalls/outcomes/catalog").json()["outcomes"]}
    assert cat["No answer"]["cooldown_days"] == 4
    assert cat["Scheduled"]["completes_recall"] is True
    assert cat["Do not call"]["permanent_suppression"] is True


def test_put_422_cooldown_without_days(client):
    r = client.put("/api/recalls/config", json={"recall_outcomes": [
        {"label": "No answer", "category": "cooldown"},
    ]})
    assert r.status_code == 422


def test_put_422_duplicate_labels(client):
    r = client.put("/api/recalls/config", json={"recall_outcomes": [
        {"label": "Scheduled", "category": "completed"},
        {"label": "Scheduled", "category": "neutral"},
    ]})
    assert r.status_code == 422


def test_put_422_empty_list(client):
    r = client.put("/api/recalls/config", json={"recall_outcomes": []})
    assert r.status_code == 422


def test_put_422_bad_category(client):
    r = client.put("/api/recalls/config", json={"recall_outcomes": [
        {"label": "Weird", "category": "bogus"},
    ]})
    assert r.status_code == 422


def test_put_422_out_of_range_claim_ttl(client):
    assert client.put("/api/recalls/config",
                      json={"claim_ttl_minutes": 0}).status_code == 422
    assert client.put("/api/recalls/config",
                      json={"claim_ttl_minutes": 999}).status_code == 422
