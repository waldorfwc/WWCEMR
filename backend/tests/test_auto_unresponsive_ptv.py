"""Audit #26 regression — mark_unresponsive must bump portal_token_version.

Unresponsive is a terminal state like a manual cancel; the auto sweep
previously never revoked outstanding portal/magic-link JWTs, so a swept
patient could keep acting via a still-valid token. cancel_surgery bumps
portal_token_version; the sweep must too.
"""
from app.models.surgery import Surgery
from app.services.surgery.auto_unresponsive import mark_unresponsive


def test_mark_unresponsive_bumps_portal_token_version(db):
    s = Surgery(
        chart_number="1", patient_name="Pat",
        status="in_progress",
        portal_token_version=0,
    )
    db.add(s); db.commit(); db.refresh(s)
    before = int(s.portal_token_version or 0)

    ok = mark_unresponsive(db, s, by="system:test")
    assert ok is True
    db.refresh(s)
    assert s.status == "unresponsive"
    assert int(s.portal_token_version or 0) == before + 1
