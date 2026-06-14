"""Intake-consents (B1/B2/B3) — curated consent template selection on a
surgery, the match-preview + template-picker endpoints, and stored-selection-
driven send/preview."""
from unittest.mock import patch, MagicMock

import pytest

from app.models.surgery import Surgery, ConsentTemplate, SurgeryConsentEnvelope


@pytest.fixture(autouse=True)
def _no_pg_sequence():
    # next_surgery_number() relies on a Postgres sequence SQLite lacks.
    with patch(
        "app.services.surgery.local_helpers.maybe_assign_surgery_number",
        return_value="SUR00001",
    ):
        yield


def _base_payload(**overrides):
    p = {
        "chart_number": "C100",
        "patient_name": "",
        "first_name": "Jane",
        "last_name": "Doe",
        "dob": "1990-04-15",
        "phone": "240-555-0100",
        "email": "jane@example.com",
        "address_street": "1 Main St",
        "address_city": "Waldorf",
        "address_state": "MD",
        "address_zip": "20601",
        "primary_insurance": "Aetna",
        "primary_member_id": "A123",
        "surgeon_primary": "",
        "surgery_name": "Hysterectomy",
        "procedures": [{"cpt": "58573", "description": "Total laparoscopic hysterectomy"}],
        "diagnoses": [{"icd": "N84.0", "description": "Polyp"}],
        "eligible_facilities": ["medstar"],
        "estimated_minutes": 180,
        "preop_date": "2026-07-01",
    }
    p.update(overrides)
    return p


def _make_template(db, **kw):
    defaults = dict(
        name="Hysterectomy consent",
        boldsign_template_id="bs_tmpl_hyst",
        procedure_match=["hysterectomy"],
        facility_match=[],
        insurance_match=[],
        is_active=True,
    )
    defaults.update(kw)
    t = ConsentTemplate(**defaults)
    db.add(t); db.commit(); db.refresh(t)
    return t


# ─── B1 — storage + persistence ──────────────────────────────────────

def test_manual_create_persists_consent_template_ids(client, db):
    t = _make_template(db)
    resp = client.post("/api/surgery/manual",
                       json=_base_payload(consent_template_ids=[str(t.id)]))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["consent_template_ids"] == [str(t.id)]
    assert body["consent_overrides"] == {"added": [], "removed": []}
    sel = body["consent_templates_selected"]
    assert len(sel) == 1
    assert sel[0]["id"] == str(t.id)
    assert sel[0]["name"] == "Hysterectomy consent"
    assert sel[0]["is_supplemental"] is False
    # consent_status escalated not_required → required
    assert body["consent_status"] == "required"


def test_manual_create_no_consents_leaves_status_not_required(client, db):
    resp = client.post("/api/surgery/manual", json=_base_payload())
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["consent_template_ids"] == []
    assert body["consent_status"] == "not_required"


def test_get_returns_resolved_selection(client, db):
    t = _make_template(db)
    sid = client.post("/api/surgery/manual",
                      json=_base_payload(consent_template_ids=[str(t.id)])).json()["id"]
    body = client.get(f"/api/surgery/{sid}").json()
    assert body["consent_template_ids"] == [str(t.id)]
    assert [r["id"] for r in body["consent_templates_selected"]] == [str(t.id)]


def test_patch_updates_consent_selection(client, db):
    t1 = _make_template(db, name="Consent A", boldsign_template_id="bs_a")
    t2 = _make_template(db, name="Consent B", boldsign_template_id="bs_b")
    sid = client.post("/api/surgery/manual",
                      json=_base_payload(consent_template_ids=[str(t1.id)])).json()["id"]
    resp = client.patch(f"/api/surgery/{sid}", json={
        "consent_template_ids": [str(t2.id)],
        "consent_overrides": {"added": [str(t2.id)], "removed": [str(t1.id)]},
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["consent_template_ids"] == [str(t2.id)]
    assert body["consent_overrides"] == {"added": [str(t2.id)], "removed": [str(t1.id)]}


def test_patch_empty_list_does_not_downgrade_sent(client, db):
    t = _make_template(db)
    sid = client.post("/api/surgery/manual",
                      json=_base_payload(consent_template_ids=[str(t.id)])).json()["id"]
    # Move the surgery to consent_status='sent'
    s = db.query(Surgery).filter(Surgery.id == sid).first()
    s.consent_status = "sent"
    db.commit()
    resp = client.patch(f"/api/surgery/{sid}", json={"consent_template_ids": []})
    assert resp.status_code == 200, resp.text
    assert resp.json()["consent_template_ids"] == []
    # status stays 'sent' — never downgraded
    assert resp.json()["consent_status"] == "sent"


# ─── B2 — match-preview + template-picker endpoints ──────────────────

def test_match_preview_returns_matching_template(client, db):
    t = _make_template(db)
    resp = client.post("/api/surgery/consent/match-preview", json={
        "procedures": [{"cpt": "58573", "description": "Total laparoscopic hysterectomy"}],
        "selected_facility": "medstar",
        "primary_insurance": "Aetna",
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    ids = [m["template_id"] for m in body["matches"]]
    assert str(t.id) in ids
    m = next(m for m in body["matches"] if m["template_id"] == str(t.id))
    assert m["name"] == "Hysterectomy consent"
    assert m["is_supplemental"] is False
    assert isinstance(m["warnings"], list)


def test_match_preview_empty_procedures_returns_empty(client, db):
    _make_template(db)
    resp = client.post("/api/surgery/consent/match-preview", json={"procedures": []})
    assert resp.status_code == 200, resp.text
    assert resp.json()["matches"] == []


def test_match_preview_single_eligible_facility_used(client, db):
    t = _make_template(db, facility_match=["medstar"])
    resp = client.post("/api/surgery/consent/match-preview", json={
        "procedures": [{"cpt": "58573", "description": "hysterectomy"}],
        "eligible_facilities": ["medstar"],
    })
    assert resp.status_code == 200, resp.text
    ids = [m["template_id"] for m in resp.json()["matches"]]
    assert str(t.id) in ids


def test_templates_picker_lists_active_only(client, db):
    active = _make_template(db, name="Active consent", boldsign_template_id="bs_act")
    _make_template(db, name="Inactive consent", boldsign_template_id="bs_inact",
                   is_active=False)
    resp = client.get("/api/surgery/consent/templates")
    assert resp.status_code == 200, resp.text
    names = [r["name"] for r in resp.json()]
    assert "Active consent" in names
    assert "Inactive consent" not in names
    row = next(r for r in resp.json() if r["id"] == str(active.id))
    assert set(row.keys()) == {"id", "name", "is_supplemental"}


# ─── B3 — stored selection drives send + preview ─────────────────────

def test_template_matches_reflects_stored_selection(client, db):
    t_a = _make_template(db, name="Consent A", boldsign_template_id="bs_a",
                         procedure_match=["hysterectomy"])
    # A second active template that the matcher would NOT pick (no proc match),
    # so we can prove the stored selection wins.
    _make_template(db, name="Consent B", boldsign_template_id="bs_b",
                   procedure_match=["colonoscopy"])
    sid = client.post("/api/surgery/manual",
                      json=_base_payload(consent_template_ids=[str(t_a.id)])).json()["id"]
    resp = client.get(f"/api/surgery/{sid}/consent/template-matches")
    assert resp.status_code == 200, resp.text
    ids = [m["template_id"] for m in resp.json()["matches"]]
    assert ids == [str(t_a.id)]


def test_send_uses_stored_selection(client, db, monkeypatch):
    monkeypatch.setenv("BOLDSIGN_API_KEY", "xxx")
    t_a = _make_template(db, name="Consent A", boldsign_template_id="bs_a",
                         procedure_match=["hysterectomy"])
    # Another template the matcher would also match — stored selection must
    # exclude it so only A is sent.
    _make_template(db, name="Consent extra", boldsign_template_id="bs_extra",
                   procedure_match=["hysterectomy", "laparoscopic"])
    sid = client.post("/api/surgery/manual",
                      json=_base_payload(consent_template_ids=[str(t_a.id)])).json()["id"]

    from app.models.patient_email import EmailTemplate
    db.add(EmailTemplate(
        kind="docusign_consent_sent", label="x",
        subject="Sign your forms", html_body="<p>Hi {{patient_name}}</p>",
    ))
    db.commit()

    fake_resp = MagicMock(status_code=201)
    fake_resp.json.return_value = {"documentId": "bs_doc_99"}
    fake_client = MagicMock()
    fake_client.__enter__.return_value.post.return_value = fake_resp

    with patch("app.services.boldsign_envelopes._http", return_value=fake_client), \
         patch("app.services.patient_email.send_email", return_value=True):
        resp = client.post(f"/api/surgery/{sid}/consent/boldsign-send")
    assert resp.status_code == 200, resp.text

    rows = (db.query(SurgeryConsentEnvelope)
              .filter(SurgeryConsentEnvelope.surgery_id == sid).all())
    assert len(rows) == 1
    assert str(rows[0].template_id) == str(t_a.id)
