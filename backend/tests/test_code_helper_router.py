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


# ─── POST /requests (text input) ─────────────────────────────────────────────

from unittest.mock import patch, MagicMock


def _fake_ai_response(*, input_tokens=1200, output_tokens=400):
    resp = MagicMock()
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.name = "submit_coding"
    tool_block.input = {
        "patient_name": "Smith, Jane",
        "patient_dob":  "1985-03-12",
        "cpt_codes": [{
            "code": "99214", "modifiers": ["25"], "position": 1,
            "justification_type": "e_m_mdm",
            "justification": {"problems_addressed": "Mod",
                               "data_reviewed": "Ltd", "risk": "Mod"},
        }],
        "icd10_codes": [{"code": "I10", "position": 1,
                          "description": "Essential hypertension"}],
    }
    resp.content = [tool_block]
    resp.usage = MagicMock(input_tokens=input_tokens, output_tokens=output_tokens)
    return resp


def test_create_request_text_input(client):
    with patch("app.services.code_helper_ai.Anthropic") as M:
        M.return_value.messages.create.return_value = _fake_ai_response()
        res = client.post("/api/billing/code-helper/requests",
                           data={"note_text": "65yo F w/ HTN.",
                                  "payer_name": "Cigna"})
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["payer_name"]   == "Cigna"
    assert body["patient_name"] == "Smith, Jane"
    assert body["cpt_codes"][0]["code"] == "99214"
    assert body["icd10_codes"][0]["code"] == "I10"
    assert body["ai_model"] == "claude-opus-4-7"
    assert body["ai_input_tokens"]  == 1200


def test_create_request_includes_denials_in_prompt(client):
    client.post("/api/billing/code-helper/denials", json={
        "code": "97110", "code_type": "cpt", "payer_name": "Cigna",
    })
    captured = {}
    def fake_create(**kw):
        captured["messages"] = kw["messages"]
        return _fake_ai_response()
    with patch("app.services.code_helper_ai.Anthropic") as M:
        M.return_value.messages.create.side_effect = fake_create
        res = client.post("/api/billing/code-helper/requests",
                           data={"note_text": "PT note", "payer_name": "Cigna"})
    assert res.status_code == 201
    user_blocks = captured["messages"][0]["content"]
    text = " ".join(b.get("text", "") for b in user_blocks if b["type"] == "text")
    assert "97110" in text
    assert "Cigna" in text


def test_create_request_missing_note_returns_422(client):
    res = client.post("/api/billing/code-helper/requests", data={})
    assert res.status_code == 422


import base64
from io import BytesIO


def _tiny_valid_pdf_bytes() -> bytes:
    """Minimal one-page PDF header. Real PDFs are bigger; this is just
    enough for the upload to be accepted and forwarded to the mocked AI."""
    return b"%PDF-1.4\n%fake\n%%EOF\n"


def test_create_request_pdf_input(client):
    pdf_bytes = _tiny_valid_pdf_bytes()
    captured = {}
    def fake_create(**kw):
        captured["messages"] = kw["messages"]
        return _fake_ai_response()
    with patch("app.services.code_helper_ai.Anthropic") as M:
        M.return_value.messages.create.side_effect = fake_create
        res = client.post(
            "/api/billing/code-helper/requests",
            data={"payer_name": "Aetna"},
            files={"note_pdf": ("clinical-note.pdf", pdf_bytes, "application/pdf")},
        )
    assert res.status_code == 201, res.text
    assert res.json()["payer_name"] == "Aetna"
    # PDF should have produced a document content block
    types = [b["type"] for b in captured["messages"][0]["content"]]
    assert "document" in types


def test_create_request_pdf_too_large_returns_422(client):
    too_big = b"%PDF-1.4\n" + b"A" * (10 * 1024 * 1024 + 1)  # 10 MB + 1 byte
    res = client.post(
        "/api/billing/code-helper/requests",
        data={"payer_name": "Cigna"},
        files={"note_pdf": ("big.pdf", too_big, "application/pdf")},
    )
    assert res.status_code == 422
    assert "too large" in res.text.lower() or "10" in res.text


def _make_request_row(client):
    with patch("app.services.code_helper_ai.Anthropic") as M:
        M.return_value.messages.create.return_value = _fake_ai_response()
        return client.post("/api/billing/code-helper/requests",
                            data={"note_text": "x"}).json()


def test_list_requests_paginates(client):
    for _ in range(3):
        _make_request_row(client)
    res = client.get("/api/billing/code-helper/requests?page=1&per_page=2")
    body = res.json()
    assert body["total"] == 3
    assert len(body["requests"]) == 2


def test_get_one_request(client):
    row = _make_request_row(client)
    res = client.get(f"/api/billing/code-helper/requests/{row['id']}")
    assert res.status_code == 200
    assert res.json()["id"] == row["id"]


def test_patch_request_updates_patient(client):
    row = _make_request_row(client)
    res = client.patch(f"/api/billing/code-helper/requests/{row['id']}",
                        json={"patient_name": "Override Name",
                               "patient_dob":  "1970-01-01"})
    assert res.status_code == 200
    assert res.json()["patient_name"] == "Override Name"
    assert res.json()["patient_dob"]  == "1970-01-01"


def test_patch_request_rejects_disallowed_fields(client):
    row = _make_request_row(client)
    res = client.patch(f"/api/billing/code-helper/requests/{row['id']}",
                        json={"cpt_codes": []})
    # PATCH ignores unrecognized fields silently — verify cpt_codes unchanged
    assert res.status_code == 200
    body = res.json()
    assert body["cpt_codes"] == row["cpt_codes"]


def test_delete_request(client):
    row = _make_request_row(client)
    res = client.delete(f"/api/billing/code-helper/requests/{row['id']}")
    assert res.status_code == 204
    res2 = client.get(f"/api/billing/code-helper/requests/{row['id']}")
    assert res2.status_code == 404
