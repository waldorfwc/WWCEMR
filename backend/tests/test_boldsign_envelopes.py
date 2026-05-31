"""BoldSign envelope service — port of DocuSign service tests."""
from unittest.mock import patch, MagicMock

import pytest

from app.models.surgery import (
    Surgery, ConsentTemplate, SurgeryConsentEnvelope,
)
from app.services.boldsign_envelopes import (
    select_template_id, send_consent_envelopes,
    _build_signer_payload, _apply_status_to_row,
    BoldSignEnvelopeError, _is_configured,
)


def _make_surgery(db, procedures=None):
    s = Surgery(
        chart_number="1", patient_name="Jane Doe", email="jane@example.com",
        eligible_facilities=["medstar"], selected_facility="medstar",
        status="confirmed",
        procedures=procedures or [{"name": "Robotic hysterectomy"}],
    )
    db.add(s); db.commit(); db.refresh(s)
    return s


def _make_template(db, **kw):
    defaults = dict(
        name="Robotic hyst consent",
        boldsign_template_id="bs_tmpl_robotic_hyst",
        procedure_match=["Robotic", "hysterectomy"],
        facility_match=None,
    )
    defaults.update(kw)
    t = ConsentTemplate(**defaults)
    db.add(t); db.commit(); db.refresh(t)
    return t


def test_is_configured_reflects_env(monkeypatch):
    monkeypatch.delenv("BOLDSIGN_API_KEY", raising=False)
    assert _is_configured() is False
    monkeypatch.setenv("BOLDSIGN_API_KEY", "xxx")
    assert _is_configured() is True


def test_select_template_matches_by_procedure(db):
    s = _make_surgery(db)
    t = _make_template(db)
    assert select_template_id(s, db) == "bs_tmpl_robotic_hyst"


def test_select_template_returns_none_when_no_match(db):
    s = _make_surgery(db, procedures=[{"name": "Endometrial biopsy"}])
    _make_template(db)  # only matches Robotic/hysterectomy
    assert select_template_id(s, db) is None


def test_build_signer_payload_includes_patient(db):
    s = _make_surgery(db)
    t = _make_template(db)
    signers = _build_signer_payload(s, t)
    assert len(signers) >= 1
    assert signers[0]["name"] == "Jane Doe"
    assert signers[0]["emailAddress"] == "jane@example.com"


def test_send_creates_envelope_row_and_calls_email_hook(db, monkeypatch):
    monkeypatch.setenv("BOLDSIGN_API_KEY", "xxx")
    s = _make_surgery(db)
    _make_template(db)

    # Mock BoldSign HTTP response + email sender
    fake_resp = MagicMock(status_code=201)
    fake_resp.json.return_value = {"documentId": "bs_doc_99"}
    fake_client = MagicMock()
    fake_client.__enter__.return_value.post.return_value = fake_resp

    from app.models.patient_email import EmailTemplate
    db.add(EmailTemplate(
        kind="docusign_consent_sent", label="x",
        subject="Sign your forms", html_body="<p>Hi {{patient_name}}</p>",
    ))
    db.commit()

    with patch("app.services.boldsign_envelopes._http", return_value=fake_client), \
         patch("app.services.patient_email.send_email", return_value=True):
        result = send_consent_envelopes(db, s, sent_by="ocooke@x.com")

    assert len(result["sent"]) == 1
    assert result["sent"][0]["envelope_id"] == "bs_doc_99"

    # Verify the DB row was created
    rows = (db.query(SurgeryConsentEnvelope)
              .filter(SurgeryConsentEnvelope.surgery_id == s.id).all())
    assert len(rows) == 1
    assert rows[0].boldsign_envelope_id == "bs_doc_99"

    # Email hook fired
    from app.models.patient_email import PatientEmail
    em = (db.query(PatientEmail)
            .filter(PatientEmail.template_kind == "docusign_consent_sent",
                    PatientEmail.surgery_id == s.id).first())
    assert em is not None


def test_send_raises_when_unconfigured(db, monkeypatch):
    monkeypatch.delenv("BOLDSIGN_API_KEY", raising=False)
    s = _make_surgery(db)
    _make_template(db)
    with pytest.raises(BoldSignEnvelopeError):
        send_consent_envelopes(db, s, sent_by="x@y.com")


def test_apply_status_maps_completed(db):
    s = _make_surgery(db)
    t = _make_template(db)
    row = SurgeryConsentEnvelope(
        surgery_id=s.id, template_id=t.id,
        boldsign_envelope_id="bs_doc_99", status="sent",
    )
    _apply_status_to_row(row, {"status": "Completed"})
    assert row.status == "signed"


def test_apply_status_maps_declined(db):
    s = _make_surgery(db)
    t = _make_template(db)
    row = SurgeryConsentEnvelope(
        surgery_id=s.id, template_id=t.id,
        boldsign_envelope_id="bs_doc_99", status="sent",
    )
    _apply_status_to_row(row, {"status": "Declined"})
    assert row.status == "declined"
