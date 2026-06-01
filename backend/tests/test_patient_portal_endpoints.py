"""Portal endpoints — login + verify."""
from datetime import date
from unittest.mock import patch

from app.models.surgery import Surgery


def _seed_surgery(db, cell="+12405551234", dob=date(1990, 1, 1)):
    s = Surgery(chart_number="1", patient_name="Pat",
                  cell_phone=cell, dob=dob, status="new")
    db.add(s); db.commit(); db.refresh(s)
    return s


def test_login_sends_sms_and_returns_challenge(client, db):
    s = _seed_surgery(db)
    with patch("app.services.patient_portal_auth.send_sms",
                return_value=True) as mock_sms:
        r = client.post("/api/patient/portal/login",
                         json={"dob": "1990-01-01", "phone_last4": "1234"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "challenge_token" in body
    assert len(body["challenge_token"]) >= 32
    mock_sms.assert_called_once()


def test_login_generic_404_on_no_match(client, db):
    _seed_surgery(db)
    r = client.post("/api/patient/portal/login",
                     json={"dob": "1980-01-01", "phone_last4": "0000"})
    assert r.status_code == 404
    # Must not reveal whether DOB or phone was wrong
    assert "dob" not in r.text.lower()
    assert "phone" not in r.text.lower()


def test_login_locked_out_after_three_fails(client, db):
    _seed_surgery(db)
    for _ in range(3):
        # Same DOB so the surgery is identifiable for lockout tracking,
        # but wrong last4 — so login fails and records an attempt against
        # the matched surgery id.
        client.post("/api/patient/portal/login",
                     json={"dob": "1990-01-01", "phone_last4": "0000"})
    r = client.post("/api/patient/portal/login",
                     json={"dob": "1990-01-01", "phone_last4": "1234"})
    assert r.status_code == 429
    assert "15 minutes" in r.text
    assert "240-252-2140" in r.text


def test_login_validates_dob_format(client, db):
    _seed_surgery(db)
    r = client.post("/api/patient/portal/login",
                     json={"dob": "not-a-date", "phone_last4": "1234"})
    assert r.status_code == 422


def test_login_validates_last4_length(client, db):
    _seed_surgery(db)
    r = client.post("/api/patient/portal/login",
                     json={"dob": "1990-01-01", "phone_last4": "12"})
    assert r.status_code == 422


def test_verify_returns_token_on_correct_code(client, db):
    s = _seed_surgery(db)
    with patch("app.services.patient_portal_auth._generate_code",
                return_value="111111"):
        with patch("app.services.patient_portal_auth.send_sms",
                    return_value=True):
            login = client.post("/api/patient/portal/login",
                                  json={"dob": "1990-01-01",
                                          "phone_last4": "1234"}).json()
    r = client.post("/api/patient/portal/verify",
                     json={"challenge_token": login["challenge_token"],
                              "code": "111111"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "token" in body and body["token"].count(".") == 2  # JWT shape
    assert body["surgery_id"] == str(s.id)
    assert "expires_at" in body and "T" in body["expires_at"]


def test_verify_rejects_wrong_code(client, db):
    _seed_surgery(db)
    with patch("app.services.patient_portal_auth._generate_code",
                return_value="111111"):
        with patch("app.services.patient_portal_auth.send_sms",
                    return_value=True):
            login = client.post("/api/patient/portal/login",
                                  json={"dob": "1990-01-01",
                                          "phone_last4": "1234"}).json()
    r = client.post("/api/patient/portal/verify",
                     json={"challenge_token": login["challenge_token"],
                              "code": "000000"})
    assert r.status_code == 401


def test_verify_rejects_unknown_challenge(client, db):
    _seed_surgery(db)
    r = client.post("/api/patient/portal/verify",
                     json={"challenge_token": "not-real", "code": "111111"})
    assert r.status_code == 401


def test_verify_rejects_replay_of_correct_code(client, db):
    _seed_surgery(db)
    with patch("app.services.patient_portal_auth._generate_code",
                return_value="111111"):
        with patch("app.services.patient_portal_auth.send_sms",
                    return_value=True):
            login = client.post("/api/patient/portal/login",
                                  json={"dob": "1990-01-01",
                                          "phone_last4": "1234"}).json()
    # First call succeeds.
    r1 = client.post("/api/patient/portal/verify",
                      json={"challenge_token": login["challenge_token"],
                                "code": "111111"})
    assert r1.status_code == 200
    # Replay with same code + same challenge_token must be rejected.
    r2 = client.post("/api/patient/portal/verify",
                      json={"challenge_token": login["challenge_token"],
                                "code": "111111"})
    assert r2.status_code == 401


def test_verify_kills_challenge_after_three_wrong_codes(client, db):
    _seed_surgery(db)
    with patch("app.services.patient_portal_auth._generate_code",
                return_value="111111"):
        with patch("app.services.patient_portal_auth.send_sms",
                    return_value=True):
            login = client.post("/api/patient/portal/login",
                                  json={"dob": "1990-01-01",
                                          "phone_last4": "1234"}).json()
    ch = login["challenge_token"]
    for _ in range(3):
        r = client.post("/api/patient/portal/verify",
                         json={"challenge_token": ch, "code": "000000"})
        assert r.status_code == 401
    # Even the correct code is now refused — challenge is dead.
    r = client.post("/api/patient/portal/verify",
                     json={"challenge_token": ch, "code": "111111"})
    assert r.status_code == 401


def test_dashboard_requires_token(client, db):
    s = _seed_surgery(db)
    r = client.get(f"/api/patient/portal/{s.id}/dashboard")
    assert r.status_code == 401


def test_dashboard_returns_surgery_and_milestones(client, db):
    from app.services.patient_portal_auth import issue_portal_token
    from datetime import date as _d
    s = Surgery(
        chart_number="1", patient_name="Doe, Jane", first_name="Jane",
        cell_phone="+12405551234", dob=_d(1990, 1, 1),
        scheduled_date=_d(2026, 6, 15),
        eligible_facilities=["office"], selected_facility="office",
        procedures=[{"cpt": "58558", "description": "Hysteroscopy with D&C"}],
        patient_responsibility=250,
        status="confirmed",
    )
    db.add(s); db.commit(); db.refresh(s)
    token = issue_portal_token(s)
    r = client.get(f"/api/patient/portal/{s.id}/dashboard",
                     headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    body = r.json()
    # Surgery summary
    assert body["surgery"]["procedure"] == "Hysteroscopy with D&C"
    assert body["surgery"]["surgery_date"] == "2026-06-15"
    assert body["surgery"]["facility"] == "the office"  # FACILITY_SHORT
    assert body["surgery"]["patient_responsibility"] == 250
    # Milestones — list of {key, label, status, ...}
    keys = [m["key"] for m in body["milestones"]]
    assert "payment" in keys
    assert "schedule" in keys
    assert "consent" in keys
    # Next-thing banner
    assert "next_action" in body


def test_dashboard_rejects_token_for_different_surgery(client, db):
    from app.services.patient_portal_auth import issue_portal_token
    s1 = _seed_surgery(db, cell="+12405551111", dob=date(1990, 1, 1))
    s2 = _seed_surgery(db, cell="+12405552222", dob=date(1992, 2, 2))
    token = issue_portal_token(s1)
    r = client.get(f"/api/patient/portal/{s2.id}/dashboard",
                     headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


def test_dashboard_payment_milestone_reflects_paid_amount(client, db):
    """Payment milestone must read from SurgeryPayment.amount_paid where
    status=='paid' — verifies the column-name fix."""
    from app.services.patient_portal_auth import issue_portal_token
    from app.models.stripe_payment import SurgeryPayment
    from datetime import date as _d
    s = Surgery(
        chart_number="X1", patient_name="Doe, Jane", first_name="Jane",
        cell_phone="+12405551234", dob=_d(1990, 1, 1),
        eligible_facilities=["office"], selected_facility="office",
        patient_responsibility=250,
        status="confirmed",
    )
    db.add(s); db.commit(); db.refresh(s)
    # 150 of 250 collected — partial → in_progress
    db.add(SurgeryPayment(
        surgery_id=s.id, status="paid",
        amount_requested=250, amount_paid=150,
        requested_by="staff",
    ))
    db.commit()
    token = issue_portal_token(s)
    r = client.get(f"/api/patient/portal/{s.id}/dashboard",
                     headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    payment_m = next(m for m in r.json()["milestones"] if m["key"] == "payment")
    assert payment_m["paid"] == 150
    assert payment_m["due"] == 250
    assert payment_m["status"] == "in_progress"


def test_dashboard_hides_fmla_row_when_status_is_null(client, db):
    """Patient did not request FMLA — row should not appear."""
    from app.services.patient_portal_auth import issue_portal_token
    from datetime import date as _d
    s = Surgery(
        chart_number="X2", patient_name="Pat", first_name="Pat",
        cell_phone="+12405551234", dob=_d(1990, 1, 1),
        eligible_facilities=["office"], selected_facility="office",
        status="new",
    )
    db.add(s); db.commit(); db.refresh(s)
    assert s.fmla_status is None       # baseline
    token = issue_portal_token(s)
    r = client.get(f"/api/patient/portal/{s.id}/dashboard",
                     headers={"Authorization": f"Bearer {token}"})
    keys = [m["key"] for m in r.json()["milestones"]]
    assert "fmla" not in keys


def test_dashboard_shows_fmla_row_when_status_is_set(client, db):
    """Patient requested FMLA — row appears with whatever status the
    coordinator has set."""
    from app.services.patient_portal_auth import issue_portal_token
    from datetime import date as _d
    s = Surgery(
        chart_number="X3", patient_name="Pat", first_name="Pat",
        cell_phone="+12405551234", dob=_d(1990, 1, 1),
        eligible_facilities=["office"], selected_facility="office",
        fmla_status="requested",
        status="new",
    )
    db.add(s); db.commit(); db.refresh(s)
    token = issue_portal_token(s)
    r = client.get(f"/api/patient/portal/{s.id}/dashboard",
                     headers={"Authorization": f"Bearer {token}"})
    fmla = next((m for m in r.json()["milestones"] if m["key"] == "fmla"), None)
    assert fmla is not None
    assert fmla["status"] == "requested"


def test_dashboard_hides_fmla_row_when_status_is_empty_string(client, db):
    """Whitespace-only status should be treated as 'not requested.'"""
    from app.services.patient_portal_auth import issue_portal_token
    from datetime import date as _d
    s = Surgery(
        chart_number="X4", patient_name="Pat", first_name="Pat",
        cell_phone="+12405551234", dob=_d(1990, 1, 1),
        eligible_facilities=["office"], selected_facility="office",
        fmla_status="   ",
        status="new",
    )
    db.add(s); db.commit(); db.refresh(s)
    token = issue_portal_token(s)
    r = client.get(f"/api/patient/portal/{s.id}/dashboard",
                     headers={"Authorization": f"Bearer {token}"})
    keys = [m["key"] for m in r.json()["milestones"]]
    assert "fmla" not in keys


def test_payments_returns_balance_and_history(client, db):
    from app.services.patient_portal_auth import issue_portal_token
    from app.models.stripe_payment import SurgeryPayment
    s = _seed_surgery(db)
    s.patient_responsibility = 500
    s.amount_paid = 100
    db.add(SurgeryPayment(
        surgery_id=s.id, status="paid",
        amount_requested=100, amount_paid=100,
        requested_by="staff",
    ))
    db.commit()
    token = issue_portal_token(s)
    r = client.get(f"/api/patient/portal/{s.id}/payments",
                     headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert float(body["due"])     == 500
    assert float(body["paid"])    == 100
    assert float(body["balance"]) == 400
    assert len(body["history"]) == 1
    assert body["history"][0]["status"] == "paid"


def test_step_up_sends_payment_purpose_sms(client, db):
    from app.services.patient_portal_auth import issue_portal_token
    s = _seed_surgery(db); s.patient_responsibility = 250; db.commit()
    token = issue_portal_token(s)
    with patch("app.services.patient_portal_auth.send_sms",
                return_value=True) as mock_sms:
        r = client.post(f"/api/patient/portal/{s.id}/payments/step-up",
                          headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    assert "step_up_token" in r.json()
    body = mock_sms.call_args[0][1]
    assert "payment" in body.lower() or "charge" in body.lower()


def test_step_up_blocks_when_no_balance(client, db):
    from app.services.patient_portal_auth import issue_portal_token
    s = _seed_surgery(db); s.patient_responsibility = 0; db.commit()
    token = issue_portal_token(s)
    r = client.post(f"/api/patient/portal/{s.id}/payments/step-up",
                     headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 422  # no outstanding balance


def test_checkout_rejects_invalid_code(client, db):
    from app.services.patient_portal_auth import issue_portal_token
    s = _seed_surgery(db); s.patient_responsibility = 250; db.commit()
    token = issue_portal_token(s)
    with patch("app.services.patient_portal_auth._generate_code",
                return_value="111111"):
        with patch("app.services.patient_portal_auth.send_sms",
                    return_value=True):
            step = client.post(
                f"/api/patient/portal/{s.id}/payments/step-up",
                headers={"Authorization": f"Bearer {token}"}
            ).json()
    r = client.post(
        f"/api/patient/portal/{s.id}/payments/checkout",
        json={"step_up_token": step["step_up_token"], "code": "000000"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 401


def test_checkout_creates_session_with_correct_code(client, db):
    from app.services.patient_portal_auth import issue_portal_token
    s = _seed_surgery(db); s.patient_responsibility = 250; db.commit()
    token = issue_portal_token(s)

    class FakePay:
        id = "pay_test_id"
        checkout_url = "https://stripe.test/cs_123"

    with patch("app.services.patient_portal_auth._generate_code",
                return_value="111111"), \
         patch("app.services.patient_portal_auth.send_sms",
                return_value=True), \
         patch("app.services.stripe_payments.is_configured",
                return_value=True), \
         patch("app.services.stripe_payments.create_checkout_session",
                return_value=FakePay()):
        step = client.post(
            f"/api/patient/portal/{s.id}/payments/step-up",
            headers={"Authorization": f"Bearer {token}"}
        ).json()
        r = client.post(
            f"/api/patient/portal/{s.id}/payments/checkout",
            json={"step_up_token": step["step_up_token"], "code": "111111"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert r.status_code == 200, r.text
    assert r.json()["checkout_url"].startswith("https://stripe.test/")


def test_checkout_passes_decimal_amount_to_stripe(client, db):
    """Regression: the checkout handler must pass a Decimal (not float) to
    create_checkout_session, because that service calls .quantize() on the
    value. A float would 502 every checkout in prod."""
    from decimal import Decimal
    from app.services.patient_portal_auth import issue_portal_token
    s = _seed_surgery(db); s.patient_responsibility = 250; db.commit()
    token = issue_portal_token(s)

    class FakePay:
        id = "pay_test_id"
        checkout_url = "https://stripe.test/cs_123"

    captured = {}
    def _capture(db, surgery, *, amount, description, actor):
        captured["amount"] = amount
        return FakePay()

    with patch("app.services.patient_portal_auth._generate_code",
                return_value="111111"), \
         patch("app.services.patient_portal_auth.send_sms",
                return_value=True), \
         patch("app.services.stripe_payments.is_configured",
                return_value=True), \
         patch("app.services.stripe_payments.create_checkout_session",
                side_effect=_capture):
        step = client.post(
            f"/api/patient/portal/{s.id}/payments/step-up",
            headers={"Authorization": f"Bearer {token}"}
        ).json()
        client.post(
            f"/api/patient/portal/{s.id}/payments/checkout",
            json={"step_up_token": step["step_up_token"], "code": "111111"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert isinstance(captured["amount"], Decimal), \
        f"expected Decimal, got {type(captured['amount']).__name__}"
    assert captured["amount"] == Decimal("250")


def test_slots_returns_gate_state_when_unpaid(client, db):
    from app.services.patient_portal_auth import issue_portal_token
    s = _seed_surgery(db)
    s.patient_responsibility = 250
    s.amount_paid = 0
    s.procedure_classification = "office_d_and_c"
    db.commit()
    token = issue_portal_token(s)
    r = client.get(f"/api/patient/portal/{s.id}/slots",
                     headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["gate"]["allowed"] is False
    assert "$250" in body["gate"]["reason"]
    assert body["block_days"] == []  # hidden when gate blocks


def test_slots_returns_block_days_when_gate_passes(client, db):
    from app.services.patient_portal_auth import issue_portal_token
    from app.models.surgery import BlockDay
    from datetime import date as _d, time as _t, timedelta as _td
    s = _seed_surgery(db)
    s.patient_responsibility = 0
    s.eligible_facilities = ["office"]
    s.procedure_classification = "office_d_and_c"
    s.estimated_minutes = 60
    db.add(BlockDay(
        block_date=_d.today() + _td(days=14),
        facility="office",
        start_time=_t(8, 0), end_time=_t(15, 0),
        block_kind="office_d_and_c",
    ))
    db.commit()
    token = issue_portal_token(s)
    r = client.get(f"/api/patient/portal/{s.id}/slots",
                     headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["gate"]["allowed"] is True
    assert len(body["block_days"]) >= 1
    bd0 = body["block_days"][0]
    # T9 (frontend) consumes these — assert they're present so a future
    # regression doesn't quietly drop them.
    for key in ("block_day_id", "facility", "block_date", "weekday",
                  "proposed_start_time", "duration_minutes",
                  "block_window", "cases_already_booked"):
        assert key in bd0, f"missing {key} in block_days[0]: {bd0}"


def test_claim_blocks_when_gate_fails(client, db):
    from app.services.patient_portal_auth import issue_portal_token
    from app.models.surgery import BlockDay
    from datetime import date as _d, time as _t, timedelta as _td
    s = _seed_surgery(db)
    s.patient_responsibility = 250  # gate blocks
    s.eligible_facilities = ["office"]
    s.procedure_classification = "office_d_and_c"
    bd = BlockDay(
        block_date=_d.today() + _td(days=14),
        facility="office",
        start_time=_t(8, 0), end_time=_t(15, 0),
        block_kind="office_d_and_c",
    )
    db.add(bd); db.commit(); db.refresh(bd)
    token = issue_portal_token(s)
    r = client.post(
        f"/api/patient/portal/{s.id}/slots/{bd.id}/claim",
        json={"start_time": "08:00"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 409


def test_consent_returns_empty_when_unscheduled(client, db):
    from app.services.patient_portal_auth import issue_portal_token
    s = _seed_surgery(db)
    s.procedures = [{"cpt": "58558", "description": "Hysteroscopy with D&C"}]
    s.selected_facility = "office"
    # scheduled_date is None
    db.commit()
    token = issue_portal_token(s)
    r = client.get(f"/api/patient/portal/{s.id}/consent",
                     headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scheduled_date"] is None
    assert body["envelopes"] == []
    assert body["can_resend"] is False  # not scheduled yet


def test_consent_returns_envelopes_when_present(client, db):
    from datetime import date as _d
    from app.services.patient_portal_auth import issue_portal_token
    from app.models.surgery import ConsentTemplate, SurgeryConsentEnvelope
    s = _seed_surgery(db)
    s.scheduled_date = _d(2026, 7, 1)
    s.procedures = [{"cpt": "58558", "description": "Hysteroscopy with D&C"}]
    s.selected_facility = "office"
    t = ConsentTemplate(name="Office — Hysteroscopy D&C Consent",
                          boldsign_template_id="bs_t1",
                          procedure_match=["hysteroscopy with d&c"],
                          facility_match=["office"])
    db.add(t); db.flush()
    env = SurgeryConsentEnvelope(
        surgery_id=s.id, template_id=t.id,
        boldsign_envelope_id="bs_doc_1",
        status="sent",
    )
    db.add(env); db.commit()
    token = issue_portal_token(s)
    r = client.get(f"/api/patient/portal/{s.id}/consent",
                     headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert len(body["envelopes"]) == 1
    assert body["envelopes"][0]["template_name"] == "Office — Hysteroscopy D&C Consent"
    assert body["envelopes"][0]["status"] == "sent"
    assert body["envelopes"][0]["can_sign"] is True   # status is "sent"
    assert body["envelopes"][0]["can_download"] is False
    assert body["all_complete"] is False
    assert body["can_resend"] is True  # scheduled


def test_consent_all_complete_when_every_envelope_signed(client, db):
    from datetime import date as _d
    from app.services.patient_portal_auth import issue_portal_token
    from app.models.surgery import ConsentTemplate, SurgeryConsentEnvelope
    s = _seed_surgery(db)
    s.scheduled_date = _d(2026, 7, 1)
    t = ConsentTemplate(name="X", boldsign_template_id="bs_x",
                          procedure_match=[], facility_match=[])
    db.add(t); db.flush()
    db.add(SurgeryConsentEnvelope(
        surgery_id=s.id, template_id=t.id,
        boldsign_envelope_id="bs_doc_2",
        status="signed",
    ))
    db.commit()
    token = issue_portal_token(s)
    r = client.get(f"/api/patient/portal/{s.id}/consent",
                     headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["all_complete"] is True
    assert body["envelopes"][0]["can_sign"] is False
    assert body["envelopes"][0]["can_download"] is True


def test_resend_blocked_when_not_scheduled(client, db):
    from app.services.patient_portal_auth import issue_portal_token
    s = _seed_surgery(db)
    s.procedure_classification = "office_d_and_c"
    db.commit()
    token = issue_portal_token(s)
    r = client.post(f"/api/patient/portal/{s.id}/consent/resend",
                      headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 409
    assert "schedule" in r.text.lower()


def test_resend_calls_send_consent_envelopes(client, db):
    from datetime import date as _d
    from unittest.mock import patch
    from app.services.patient_portal_auth import issue_portal_token
    s = _seed_surgery(db)
    s.scheduled_date = _d(2026, 7, 1)
    s.procedures = [{"cpt": "58558", "description": "Hysteroscopy with D&C"}]
    s.selected_facility = "office"
    db.commit()
    token = issue_portal_token(s)
    with patch("app.services.boldsign_envelopes.send_consent_envelopes",
                return_value={"sent": [], "skipped": [],
                              "unmatched_procedures": [], "warnings": []}) as mock:
        r = client.post(f"/api/patient/portal/{s.id}/consent/resend",
                          headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    mock.assert_called_once()
    _, kwargs = mock.call_args
    assert kwargs.get("sent_by") == "patient:portal:resend"


def test_sign_link_returns_url_for_patient_email(client, db):
    from datetime import date as _d
    from unittest.mock import patch
    from app.services.patient_portal_auth import issue_portal_token
    from app.models.surgery import ConsentTemplate, SurgeryConsentEnvelope
    s = _seed_surgery(db); s.email = "patient@example.com"
    s.scheduled_date = _d(2026, 7, 1)
    t = ConsentTemplate(name="X", boldsign_template_id="bs_x",
                          procedure_match=[], facility_match=[])
    db.add(t); db.flush()
    env = SurgeryConsentEnvelope(
        surgery_id=s.id, template_id=t.id,
        boldsign_envelope_id="bs_doc_999", status="sent",
    )
    db.add(env); db.commit(); db.refresh(env)
    token = issue_portal_token(s)
    with patch("app.services.boldsign_envelopes.get_embedded_sign_link",
                return_value="https://app.boldsign.com/signing/abc"):
        r = client.get(
            f"/api/patient/portal/{s.id}/consent/sign-link/{env.id}",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert r.status_code == 200, r.text
    assert r.json()["sign_url"].startswith("https://app.boldsign.com/")


def test_sign_link_rejects_envelope_from_different_surgery(client, db):
    from app.services.patient_portal_auth import issue_portal_token
    from app.models.surgery import ConsentTemplate, SurgeryConsentEnvelope
    s1 = _seed_surgery(db, cell="+12405551111", dob=date(1990, 1, 1))
    s2 = _seed_surgery(db, cell="+12405552222", dob=date(1991, 2, 2))
    t = ConsentTemplate(name="X", boldsign_template_id="bs_x",
                          procedure_match=[], facility_match=[])
    db.add(t); db.flush()
    env = SurgeryConsentEnvelope(
        surgery_id=s2.id, template_id=t.id,
        boldsign_envelope_id="bs_other", status="sent",
    )
    db.add(env); db.commit(); db.refresh(env)
    # Token is for s1; envelope belongs to s2.
    token = issue_portal_token(s1)
    r = client.get(
        f"/api/patient/portal/{s1.id}/consent/sign-link/{env.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 404  # envelope not found for this surgery


def test_signed_pdf_rejects_unsigned_envelope(client, db):
    from app.services.patient_portal_auth import issue_portal_token
    from app.models.surgery import ConsentTemplate, SurgeryConsentEnvelope
    s = _seed_surgery(db)
    t = ConsentTemplate(name="X", boldsign_template_id="bs_x",
                          procedure_match=[], facility_match=[])
    db.add(t); db.flush()
    env = SurgeryConsentEnvelope(
        surgery_id=s.id, template_id=t.id,
        boldsign_envelope_id="bs_doc_x", status="sent",   # not signed yet
    )
    db.add(env); db.commit(); db.refresh(env)
    token = issue_portal_token(s)
    r = client.get(
        f"/api/patient/portal/{s.id}/consent/signed-pdf/{env.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 409
    assert "not yet" in r.text.lower() or "not signed" in r.text.lower()


def test_signed_pdf_streams_when_signed(client, db):
    from unittest.mock import patch
    from app.services.patient_portal_auth import issue_portal_token
    from app.models.surgery import ConsentTemplate, SurgeryConsentEnvelope
    s = _seed_surgery(db)
    t = ConsentTemplate(name="X", boldsign_template_id="bs_x",
                          procedure_match=[], facility_match=[])
    db.add(t); db.flush()
    env = SurgeryConsentEnvelope(
        surgery_id=s.id, template_id=t.id,
        boldsign_envelope_id="bs_doc_y", status="signed",
    )
    db.add(env); db.commit(); db.refresh(env)
    token = issue_portal_token(s)
    with patch("app.services.boldsign_envelopes.download_signed_pdf",
                return_value=b"%PDF-fake-bytes"):
        r = client.get(
            f"/api/patient/portal/{s.id}/consent/signed-pdf/{env.id}",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert r.status_code == 200
    assert r.content.startswith(b"%PDF")
    assert "pdf" in r.headers["content-type"].lower()


def test_documents_aggregates_consents_and_receipts(client, db):
    from datetime import date as _d, datetime as _dt
    from decimal import Decimal
    from app.services.patient_portal_auth import issue_portal_token
    from app.models.surgery import ConsentTemplate, SurgeryConsentEnvelope
    from app.models.stripe_payment import SurgeryPayment

    s = _seed_surgery(db)
    s.scheduled_date = _d(2026, 7, 1)
    s.procedure_classification = "office_d_and_c"

    # Signed consent + pending consent — only signed should appear
    t1 = ConsentTemplate(name="Office — Hysteroscopy D&C Consent",
                          boldsign_template_id="bs_t1",
                          procedure_match=[], facility_match=[])
    t2 = ConsentTemplate(name="LARC Form", boldsign_template_id="bs_t2",
                          procedure_match=[], facility_match=[])
    db.add_all([t1, t2]); db.flush()
    db.add_all([
        SurgeryConsentEnvelope(surgery_id=s.id, template_id=t1.id,
                                  boldsign_envelope_id="bs_doc_1",
                                  status="signed"),
        SurgeryConsentEnvelope(surgery_id=s.id, template_id=t2.id,
                                  boldsign_envelope_id="bs_doc_2",
                                  status="sent"),
    ])
    db.add(SurgeryPayment(
        surgery_id=s.id, status="paid",
        amount_requested=Decimal("250.00"),
        amount_paid=Decimal("250.00"),
        requested_by="staff",
        paid_at=_dt(2026, 5, 31, 12, 0),
    ))
    db.commit()
    token = issue_portal_token(s)
    r = client.get(f"/api/patient/portal/{s.id}/documents",
                     headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    body = r.json()
    # Only the signed consent appears
    assert len(body["consents"]) == 1
    assert body["consents"][0]["template_name"] == "Office — Hysteroscopy D&C Consent"
    # The paid receipt appears
    assert len(body["receipts"]) == 1
    assert float(body["receipts"][0]["amount"]) == 250.0
    # Instructions structure exists with both kinds present (even if not yet uploaded)
    assert "instructions" in body
    assert "preop" in body["instructions"]
    assert "postop" in body["instructions"]


def test_documents_omits_unsigned_consents(client, db):
    from app.services.patient_portal_auth import issue_portal_token
    from app.models.surgery import ConsentTemplate, SurgeryConsentEnvelope
    s = _seed_surgery(db)
    t = ConsentTemplate(name="X", boldsign_template_id="bs_x",
                          procedure_match=[], facility_match=[])
    db.add(t); db.flush()
    db.add(SurgeryConsentEnvelope(
        surgery_id=s.id, template_id=t.id,
        boldsign_envelope_id="bs_doc",
        status="sent",   # not yet signed
    ))
    db.commit()
    token = issue_portal_token(s)
    r = client.get(f"/api/patient/portal/{s.id}/documents",
                     headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["consents"] == []


def test_documents_no_instructions_when_classification_blank(client, db):
    from app.services.patient_portal_auth import issue_portal_token
    s = _seed_surgery(db)
    # procedure_classification stays None
    db.commit()
    token = issue_portal_token(s)
    r = client.get(f"/api/patient/portal/{s.id}/documents",
                     headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    # When classification is blank, instructions section is null
    assert r.json()["instructions"] is None


def test_instructions_pdf_returns_404_when_classification_blank(client, db):
    from app.services.patient_portal_auth import issue_portal_token
    s = _seed_surgery(db)
    db.commit()
    token = issue_portal_token(s)
    r = client.get(f"/api/patient/portal/{s.id}/documents/instructions/preop",
                     headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 404


def test_instructions_pdf_rejects_invalid_kind(client, db):
    from app.services.patient_portal_auth import issue_portal_token
    s = _seed_surgery(db)
    s.procedure_classification = "office_d_and_c"
    db.commit()
    token = issue_portal_token(s)
    r = client.get(f"/api/patient/portal/{s.id}/documents/instructions/bogus",
                     headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 422


def test_instructions_pdf_streams_when_present(client, db):
    from unittest.mock import patch
    from app.services.patient_portal_auth import issue_portal_token
    s = _seed_surgery(db)
    s.procedure_classification = "office_d_and_c"
    db.commit()
    token = issue_portal_token(s)
    with patch("app.services.surgery_documents.fetch_instructions_pdf",
                return_value=b"%PDF-test-bytes"):
        r = client.get(
            f"/api/patient/portal/{s.id}/documents/instructions/preop",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert r.status_code == 200
    assert r.content.startswith(b"%PDF")
    assert "pdf" in r.headers["content-type"].lower()
    assert "preop" in r.headers["content-disposition"].lower()


def test_self_report_labs_flips_flag(client, db):
    from app.services.patient_portal_auth import issue_portal_token
    s = _seed_surgery(db)
    db.commit()
    token = issue_portal_token(s)
    r = client.post(f"/api/patient/portal/{s.id}/self-report/labs",
                       headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    db.refresh(s)
    assert s.labs_self_reported is True
    assert s.labs_self_reported_at is not None


def test_self_report_labs_is_idempotent(client, db):
    """Second click doesn't restamp _at."""
    from app.services.patient_portal_auth import issue_portal_token
    s = _seed_surgery(db)
    db.commit()
    token = issue_portal_token(s)
    r1 = client.post(f"/api/patient/portal/{s.id}/self-report/labs",
                         headers={"Authorization": f"Bearer {token}"})
    db.refresh(s)
    first_ts = s.labs_self_reported_at
    r2 = client.post(f"/api/patient/portal/{s.id}/self-report/labs",
                         headers={"Authorization": f"Bearer {token}"})
    db.refresh(s)
    assert r2.status_code == 200
    assert s.labs_self_reported_at == first_ts  # not bumped


def test_self_report_hospital_preop_flips_flag(client, db):
    from app.services.patient_portal_auth import issue_portal_token
    s = _seed_surgery(db)
    db.commit()
    token = issue_portal_token(s)
    r = client.post(f"/api/patient/portal/{s.id}/self-report/hospital-preop",
                       headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    db.refresh(s)
    assert s.hospital_preop_self_reported is True
    assert s.hospital_preop_self_reported_at is not None


def test_self_report_rejects_unknown_kind_via_url(client, db):
    """The router only accepts the two paths above — anything else 404s
    via FastAPI routing."""
    from app.services.patient_portal_auth import issue_portal_token
    s = _seed_surgery(db)
    db.commit()
    token = issue_portal_token(s)
    r = client.post(f"/api/patient/portal/{s.id}/self-report/bogus",
                       headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 404


# ─── /{surgery_id}/clearance/template ────────────────────────────────

def test_clearance_template_404_when_not_required(client, db):
    from app.services.patient_portal_auth import issue_portal_token
    s = _seed_surgery(db)
    s.clearance_required = False
    db.commit()
    token = issue_portal_token(s)
    r = client.get(f"/api/patient/portal/{s.id}/clearance/template",
                      headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 409
    assert "clearance" in r.text.lower()


def test_clearance_template_streams_when_present(client, db):
    from unittest.mock import patch
    from app.services.patient_portal_auth import issue_portal_token
    s = _seed_surgery(db)
    s.clearance_required = True
    db.commit()
    token = issue_portal_token(s)
    with patch("app.services.surgery_uploads.stream_static_pdf",
                return_value=b"%PDF-clearance-blank"):
        r = client.get(f"/api/patient/portal/{s.id}/clearance/template",
                          headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.content.startswith(b"%PDF")
    assert "pdf" in r.headers["content-type"].lower()


def test_clearance_template_404_when_object_missing(client, db):
    from unittest.mock import patch
    from app.services.patient_portal_auth import issue_portal_token
    s = _seed_surgery(db)
    s.clearance_required = True
    db.commit()
    token = issue_portal_token(s)
    with patch("app.services.surgery_uploads.stream_static_pdf",
                return_value=None):
        r = client.get(f"/api/patient/portal/{s.id}/clearance/template",
                          headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 404
    assert "online" in r.text.lower() or "available" in r.text.lower()


# ─── /{surgery_id}/clearance/upload ──────────────────────────────

def test_clearance_upload_writes_and_marks_status(client, db):
    from unittest.mock import patch, MagicMock
    from app.services.patient_portal_auth import issue_portal_token
    s = _seed_surgery(db)
    s.clearance_required = True
    db.commit()
    token = issue_portal_token(s)
    pdf_bytes = b"%PDF-1.4\nfake-clearance"
    with patch("app.services.surgery_uploads.storage.Client") as MockClient:
        blob = MagicMock()
        MockClient.return_value.bucket.return_value.blob.return_value = blob
        r = client.post(
            f"/api/patient/portal/{s.id}/clearance/upload",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": ("clearance.pdf", pdf_bytes, "application/pdf")},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["filename"] == "clearance.pdf"
    assert body["kind"] == "clearance"
    db.refresh(s)
    assert s.clearance_status == "uploaded"


def test_clearance_upload_rejects_oversize(client, db):
    from app.services.patient_portal_auth import issue_portal_token
    s = _seed_surgery(db)
    s.clearance_required = True
    db.commit()
    token = issue_portal_token(s)
    huge = b"%PDF-1.4\n" + b"x" * (10 * 1024 * 1024 + 1)
    r = client.post(
        f"/api/patient/portal/{s.id}/clearance/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("big.pdf", huge, "application/pdf")},
    )
    assert r.status_code == 413


def test_clearance_upload_rejects_wrong_mime(client, db):
    from app.services.patient_portal_auth import issue_portal_token
    s = _seed_surgery(db)
    s.clearance_required = True
    db.commit()
    token = issue_portal_token(s)
    r = client.post(
        f"/api/patient/portal/{s.id}/clearance/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("doc.txt", b"plain text", "text/plain")},
    )
    assert r.status_code == 415


# ─── /{surgery_id}/uploads ──────────────────────────────────────────

def test_uploads_returns_patient_documents_with_signed_urls(client, db):
    from unittest.mock import patch
    from app.services.patient_portal_auth import issue_portal_token
    from app.models.surgery import SurgeryDocument
    s = _seed_surgery(db)
    db.commit(); db.refresh(s)
    db.add(SurgeryDocument(
        surgery_id=s.id, kind="clearance",
        filename="my_clearance.pdf",
        gcs_path=f"surgery-uploads/{s.id}/clearance/x.pdf",
        content_type="application/pdf",
        uploaded_by="patient:portal",
    ))
    db.commit()
    token = issue_portal_token(s)
    with patch("app.services.surgery_uploads.signed_download_url",
                return_value="https://signed.example/x"):
        r = client.get(f"/api/patient/portal/{s.id}/uploads",
                          headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert len(body["uploads"]) == 1
    u = body["uploads"][0]
    assert u["filename"] == "my_clearance.pdf"
    assert u["kind"] == "clearance"
    assert u["download_url"].startswith("https://signed.example/")


def test_uploads_empty_list(client, db):
    from app.services.patient_portal_auth import issue_portal_token
    s = _seed_surgery(db)
    db.commit()
    token = issue_portal_token(s)
    r = client.get(f"/api/patient/portal/{s.id}/uploads",
                      headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["uploads"] == []


def test_fmla_upload_creates_fmla_blank_document(client, db):
    from unittest.mock import patch, MagicMock
    from app.services.patient_portal_auth import issue_portal_token
    s = _seed_surgery(db)
    db.commit()
    token = issue_portal_token(s)
    pdf_bytes = b"%PDF-1.4\nfmla blank form\n"
    with patch("app.services.surgery_uploads.storage.Client") as MockClient:
        blob = MagicMock()
        MockClient.return_value.bucket.return_value.blob.return_value = blob
        r = client.post(
            f"/api/patient/portal/{s.id}/fmla/upload",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": ("my_fmla.pdf", pdf_bytes, "application/pdf")},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "fmla_blank"
    assert body["filename"] == "my_fmla.pdf"


def test_fmla_upload_flips_status_when_fee_already_paid(client, db):
    """If patient paid the fee BEFORE uploading (corner case), upload
    auto-flips fmla_status to 'submitted'."""
    from unittest.mock import patch, MagicMock
    from datetime import datetime
    from app.services.patient_portal_auth import issue_portal_token
    s = _seed_surgery(db)
    s.fmla_fee_paid = True
    s.fmla_fee_paid_at = datetime.utcnow()
    db.commit()
    token = issue_portal_token(s)
    pdf_bytes = b"%PDF-1.4\nfmla\n"
    with patch("app.services.surgery_uploads.storage.Client") as MockClient:
        blob = MagicMock()
        MockClient.return_value.bucket.return_value.blob.return_value = blob
        r = client.post(
            f"/api/patient/portal/{s.id}/fmla/upload",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": ("fmla.pdf", pdf_bytes, "application/pdf")},
        )
    assert r.status_code == 200
    db.refresh(s)
    assert s.fmla_status == "submitted"
    assert r.json().get("fmla_status", "") == "submitted"


def test_fmla_upload_does_not_flip_status_when_fee_unpaid(client, db):
    """Upload alone (no fee paid) leaves status unchanged."""
    from unittest.mock import patch, MagicMock
    from app.services.patient_portal_auth import issue_portal_token
    s = _seed_surgery(db)
    db.commit()
    token = issue_portal_token(s)
    pdf_bytes = b"%PDF-1.4\nfmla\n"
    with patch("app.services.surgery_uploads.storage.Client") as MockClient:
        blob = MagicMock()
        MockClient.return_value.bucket.return_value.blob.return_value = blob
        r = client.post(
            f"/api/patient/portal/{s.id}/fmla/upload",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": ("fmla.pdf", pdf_bytes, "application/pdf")},
        )
    assert r.status_code == 200
    db.refresh(s)
    assert (s.fmla_status or "") == ""


def test_fmla_step_up_sends_payment_purpose_sms(client, db):
    from unittest.mock import patch
    from app.services.patient_portal_auth import issue_portal_token
    s = _seed_surgery(db)
    db.commit()
    token = issue_portal_token(s)
    with patch("app.services.patient_portal_auth.send_sms",
                return_value=True) as mock_sms:
        r = client.post(f"/api/patient/portal/{s.id}/fmla/step-up",
                          headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    assert "step_up_token" in r.json()
    assert mock_sms.called


def test_fmla_step_up_rejects_when_fee_already_paid(client, db):
    from app.services.patient_portal_auth import issue_portal_token
    s = _seed_surgery(db)
    s.fmla_fee_paid = True
    db.commit()
    token = issue_portal_token(s)
    r = client.post(f"/api/patient/portal/{s.id}/fmla/step-up",
                      headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 422
    assert "already" in r.json()["detail"].lower()


def test_fmla_checkout_rejects_invalid_code(client, db):
    from unittest.mock import patch
    from app.services.patient_portal_auth import issue_portal_token
    s = _seed_surgery(db)
    db.commit()
    token = issue_portal_token(s)
    with patch("app.services.patient_portal_auth._generate_code",
                return_value="111111"), \
         patch("app.services.patient_portal_auth.send_sms",
                return_value=True):
        step = client.post(
            f"/api/patient/portal/{s.id}/fmla/step-up",
            headers={"Authorization": f"Bearer {token}"}
        ).json()
    r = client.post(
        f"/api/patient/portal/{s.id}/fmla/checkout",
        json={"step_up_token": step["step_up_token"], "code": "000000"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 401


def test_fmla_checkout_creates_session_with_kind_fmla_fee(client, db):
    from unittest.mock import patch, MagicMock
    from decimal import Decimal
    from app.services.patient_portal_auth import issue_portal_token
    s = _seed_surgery(db)
    db.commit()
    token = issue_portal_token(s)

    captured = {}
    class FakePay:
        id = "pay_fmla_test"
        checkout_url = "https://stripe.test/cs_fmla"

    def _capture(db_arg, surgery_arg, *, amount, description, actor,
                   kind="patient_balance"):
        captured["amount"]      = amount
        captured["description"] = description
        captured["kind"]        = kind
        captured["actor"]       = actor
        return FakePay()

    with patch("app.services.patient_portal_auth._generate_code",
                return_value="111111"), \
         patch("app.services.patient_portal_auth.send_sms",
                return_value=True), \
         patch("app.services.stripe_payments.is_configured",
                return_value=True), \
         patch("app.services.stripe_payments.create_checkout_session",
                side_effect=_capture):
        step = client.post(
            f"/api/patient/portal/{s.id}/fmla/step-up",
            headers={"Authorization": f"Bearer {token}"}
        ).json()
        r = client.post(
            f"/api/patient/portal/{s.id}/fmla/checkout",
            json={"step_up_token": step["step_up_token"], "code": "111111"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert r.status_code == 200, r.text
    assert r.json()["checkout_url"].startswith("https://stripe.test/")
    assert captured["kind"] == "fmla_fee"
    assert captured["amount"] == Decimal("25.00")    # default FMLA_FEE_CENTS=2500
    assert "FMLA" in captured["description"]
