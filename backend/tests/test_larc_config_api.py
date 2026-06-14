"""L3: validated GET/PUT /larc/config endpoints."""


def test_get_larc_config_returns_defaults(client):
    r = client.get("/api/larc/config")
    assert r.status_code == 200
    assert r.json()["pharmacy_order_sla_days"] == 14


def test_put_larc_config_roundtrips(client):
    assert client.put("/api/larc/config", json={"pharmacy_order_sla_days": 21}).status_code == 200
    assert client.get("/api/larc/config").json()["pharmacy_order_sla_days"] == 21


def test_put_larc_config_rejects_out_of_range(client):
    assert client.put("/api/larc/config", json={"checkout_ack_window_hours": 0}).status_code == 422
    assert client.put("/api/larc/config", json={"device_expiry_hold_days": 99999}).status_code == 422
