"""Coordinator portal preview — viewer claim + read-only enforcement."""
from app.models.surgery import Surgery
from app.services.patient_portal_auth import (
    issue_portal_token, verify_portal_token, decode_portal_token,
)


def test_issue_portal_token_default_has_no_viewer(db):
    s = Surgery(chart_number="1", patient_name="Pat", status="new")
    db.add(s); db.commit(); db.refresh(s)
    token = issue_portal_token(s)
    payload = decode_portal_token(token)
    assert payload["sub"] == str(s.id)
    assert payload.get("viewer") is None


def test_issue_portal_token_with_viewer(db):
    s = Surgery(chart_number="2", patient_name="Pat", status="new")
    db.add(s); db.commit(); db.refresh(s)
    token = issue_portal_token(s, viewer="staff:ocooke@example.com",
                                  ttl_minutes=60)
    payload = decode_portal_token(token)
    assert payload["sub"] == str(s.id)
    assert payload["viewer"] == "staff:ocooke@example.com"
    # verify_portal_token still returns just the sub for backward compat
    assert verify_portal_token(token) == str(s.id)


def test_require_portal_token_blocks_writes_when_viewer_is_staff(client, db):
    """A token with viewer='staff:*' may GET but not POST."""
    from app.services.patient_portal_auth import issue_portal_token
    s = Surgery(chart_number="3", patient_name="Pat", status="new",
                  cell_phone="+12405551234", email="p@example.com")
    db.add(s); db.commit(); db.refresh(s)
    staff_tok = issue_portal_token(s, viewer="staff:ocooke@example.com",
                                       ttl_minutes=60)
    # GET works
    r_get = client.get(f"/api/patient/portal/{s.id}/dashboard",
                          headers={"Authorization": f"Bearer {staff_tok}"})
    assert r_get.status_code == 200, r_get.text
    # POST is blocked at the middleware
    r_post = client.post(f"/api/patient/portal/{s.id}/self-report/labs",
                            headers={"Authorization": f"Bearer {staff_tok}"})
    assert r_post.status_code == 403
    assert "read" in r_post.json()["detail"].lower()


def test_require_portal_token_allows_writes_for_patient_token(client, db):
    """A normal patient token (no viewer claim) can still POST."""
    from app.services.patient_portal_auth import issue_portal_token
    s = Surgery(chart_number="4", patient_name="Pat", status="new",
                  cell_phone="+12405551234", email="p@example.com")
    db.add(s); db.commit(); db.refresh(s)
    patient_tok = issue_portal_token(s)
    r_post = client.post(f"/api/patient/portal/{s.id}/self-report/labs",
                            headers={"Authorization": f"Bearer {patient_tok}"})
    assert r_post.status_code == 200, r_post.text
