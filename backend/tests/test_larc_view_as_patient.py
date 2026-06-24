from datetime import date
from unittest.mock import patch
from app.models.larc import LarcAssignment, LarcDeviceType
from app.services.larc import portal_auth


def _a(db):
    dt = LarcDeviceType(name="Mirena", category="larc", default_flow="pharmacy_order", is_active=True)
    db.add(dt); db.commit(); db.refresh(dt)
    a = LarcAssignment(chart_number="V1", patient_name="Doe, J", device_type_id=dt.id,
                       source_flow="in_stock", status="in_progress", is_active=True,
                       patient_dob=date(1990,5,1), patient_cell="240-555-0123")
    db.add(a); db.commit(); db.refresh(a)
    return a


def test_mint_preview_token(client, db):
    a = _a(db)
    r = client.post(f"/api/larc/assignments/{a.id}/portal-preview-token")
    assert r.status_code == 200, r.text
    tok = r.json()["token"]
    payload = portal_auth.decode_portal_token(tok)
    assert payload["viewer"].startswith("staff:")
    assert payload["sub"] == str(a.id)


def test_preview_token_is_read_only(client, db):
    a = _a(db)
    tok = client.post(f"/api/larc/assignments/{a.id}/portal-preview-token").json()["token"]
    hdr = {"Authorization": f"Bearer {tok}"}
    # GET works (dashboard) ...
    assert client.get("/api/larc-portal/dashboard", headers=hdr).status_code == 200
    # ... but a non-GET portal action is rejected read-only (403)
    assert client.post("/api/larc-portal/payments/checkout", headers=hdr).status_code == 403


def test_mint_404_for_unknown(client, db):
    r = client.post("/api/larc/assignments/00000000-0000-0000-0000-000000000000/portal-preview-token")
    assert r.status_code == 404


def test_send_portal_access_emails_login_link(client, db):
    a = _a(db)
    a.patient_email = "pat@example.com"; db.commit()
    with patch("app.services.patient_email.send_email", return_value=True) as m:
        r = client.post(f"/api/larc/assignments/{a.id}/portal-access/send")
    assert r.status_code == 200, r.text
    assert r.json()["sent_to"] == "pat@example.com"
    assert m.called
    to_email, subject, html = m.call_args[0][:3]
    assert to_email == "pat@example.com"
    assert "larc-portal/login" in html


def test_send_portal_access_422_when_no_email(client, db):
    a = _a(db)   # no patient_email on the fixture
    r = client.post(f"/api/larc/assignments/{a.id}/portal-access/send")
    assert r.status_code == 422


def test_send_portal_access_404_for_unknown(client, db):
    r = client.post("/api/larc/assignments/00000000-0000-0000-0000-000000000000/portal-access/send")
    assert r.status_code == 404
