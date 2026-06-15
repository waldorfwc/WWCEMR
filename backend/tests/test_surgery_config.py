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


# ─── Cancellation fee config (B1) ───────────────────────────────────

def test_get_config_includes_cancellation_fee_defaults(client):
    body = client.get("/api/surgery/config").json()
    assert body["cancellation_fee_amount"] == 351
    assert body["cancellation_fee_days_before"] == 14


def test_put_config_cancellation_fee_round_trip(client):
    resp = client.put("/api/surgery/config", json={
        "cancellation_fee_amount": 500,
        "cancellation_fee_days_before": 21,
    })
    assert resp.status_code == 200
    body = client.get("/api/surgery/config").json()
    assert body["cancellation_fee_amount"] == 500
    assert body["cancellation_fee_days_before"] == 21


def test_put_config_cancellation_days_out_of_range_returns_422(client):
    resp = client.put("/api/surgery/config",
                      json={"cancellation_fee_days_before": 400})
    assert resp.status_code == 422


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


def test_facility_crud_round_trip(client):
    # Create
    resp = client.post("/api/surgery/admin/facilities", json={
        "code": "medstar", "label": "MedStar Southern Maryland",
        "address": "7503 Surratts Rd, Clinton, MD",
        "sort_order": 1,
    })
    assert resp.status_code == 201
    fid = resp.json()["id"]

    # List
    out = client.get("/api/surgery/admin/facilities").json()
    assert any(f["code"] == "medstar" for f in out["facilities"])

    # Patch
    resp = client.patch(f"/api/surgery/admin/facilities/{fid}", json={"label": "MedStar SMH"})
    assert resp.status_code == 200
    assert resp.json()["label"] == "MedStar SMH"

    # Picklist (claim:read) returns only active facilities, sorted
    out = client.get("/api/surgery/picklists/facilities").json()
    codes = [f["code"] for f in out["facilities"]]
    assert "medstar" in codes

    # Deactivate
    client.patch(f"/api/surgery/admin/facilities/{fid}", json={"is_active": False})
    out = client.get("/api/surgery/picklists/facilities").json()
    assert "medstar" not in [f["code"] for f in out["facilities"]]


def test_facility_dup_code_returns_409(client):
    client.post("/api/surgery/admin/facilities", json={"code": "office", "label": "Office"})
    resp = client.post("/api/surgery/admin/facilities", json={"code": "office", "label": "Office 2"})
    assert resp.status_code == 409


def test_template_crud_round_trip(client):
    resp = client.post("/api/surgery/admin/procedure-templates", json={
        "code": "robotic_180", "name": "Robotic hysterectomy",
        "procedure_kind": "robotic_180",
        "default_duration_minutes": 180,
        "default_cpt_code": "58571",
    })
    assert resp.status_code == 201
    tid = resp.json()["id"]
    out = client.get("/api/surgery/picklists/procedure-templates").json()
    assert any(t["code"] == "robotic_180" for t in out["templates"])

    resp = client.patch(f"/api/surgery/admin/procedure-templates/{tid}",
                         json={"default_duration_minutes": 200})
    assert resp.json()["default_duration_minutes"] == 200


def test_template_unknown_kind_returns_422(client):
    resp = client.post("/api/surgery/admin/procedure-templates", json={
        "code": "bogus", "name": "Bogus", "procedure_kind": "not_a_kind",
        "default_duration_minutes": 60,
    })
    assert resp.status_code == 422
