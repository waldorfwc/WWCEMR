"""Integration tests for the Code Helper router."""
import pytest


def test_create_denial(client):
    res = client.post("/api/billing/code-helper/denials", json={
        "code": "97110", "code_type": "cpt",
        "payer_name": "Cigna", "reason": "not separately reimbursable",
    })
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["code"] == "97110"
    assert body["is_active"] is True
    assert body["added_by"]   # the test user from conftest


def test_list_denials_returns_active_only_by_default(client):
    client.post("/api/billing/code-helper/denials", json={
        "code": "A1", "code_type": "cpt", "payer_name": None,
    })
    r2 = client.post("/api/billing/code-helper/denials", json={
        "code": "A2", "code_type": "cpt", "payer_name": None,
    })
    # deactivate one
    did = r2.json()["id"]
    client.patch(f"/api/billing/code-helper/denials/{did}",
                  json={"is_active": False})

    res = client.get("/api/billing/code-helper/denials")
    body = res.json()
    codes = sorted(d["code"] for d in body["denials"])
    assert codes == ["A1"]

    # include inactive on demand
    res2 = client.get("/api/billing/code-helper/denials?active=false")
    assert len(res2.json()["denials"]) == 2


def test_list_denials_filter_by_payer(client):
    client.post("/api/billing/code-helper/denials", json={
        "code": "B1", "code_type": "cpt", "payer_name": "Cigna",
    })
    client.post("/api/billing/code-helper/denials", json={
        "code": "B2", "code_type": "cpt", "payer_name": "Aetna",
    })
    client.post("/api/billing/code-helper/denials", json={
        "code": "B3", "code_type": "cpt", "payer_name": None,
    })
    res = client.get("/api/billing/code-helper/denials?payer=Cigna")
    codes = sorted(d["code"] for d in res.json()["denials"])
    # Cigna-tagged + universal
    assert codes == ["B1", "B3"]
