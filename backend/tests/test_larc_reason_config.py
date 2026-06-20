"""Reason-for-request configurable list via /api/larc/config."""


def test_config_includes_reason_defaults(client, db):
    r = client.get("/api/larc/config")
    assert r.status_code == 200, r.text
    opts = r.json()["reason_for_request_options"]
    labels = {o["reason"] for o in opts}
    assert {"Contraception", "Menorrhagia"} <= labels
    by = {o["reason"]: o["icd10"] for o in opts}
    assert by["Contraception"] == "Z30.430"
    assert by["Menorrhagia"] == "N92.0"


def test_config_put_updates_reasons(client, db):
    new = [{"reason": "Dysmenorrhea", "icd10": "N94.6"}]
    r = client.put("/api/larc/config", json={"reason_for_request_options": new})
    assert r.status_code == 200, r.text
    assert r.json()["reason_for_request_options"] == new


def test_config_put_rejects_invalid_reason_item(client, db):
    r = client.put("/api/larc/config",
                   json={"reason_for_request_options": [{"reason": "No code"}]})
    assert r.status_code == 422


def test_config_put_rejects_blank_icd10(client, db):
    r = client.put("/api/larc/config",
                   json={"reason_for_request_options":
                         [{"reason": "X", "icd10": "  "}]})
    assert r.status_code == 422
