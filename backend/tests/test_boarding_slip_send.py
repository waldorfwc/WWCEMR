"""T2 — boarding-slip send endpoint (multi-recipient) + reschedule re-arm."""
from __future__ import annotations

from datetime import date, time, timedelta

import pytest

from app.utils.dt import now_utc_naive
from app.models.surgery import Surgery, SurgeryFile, SurgerySlot, BlockDay
from app.services.surgery import boarding_slip_email as bse


# ─── Fixtures / helpers ───────────────────────────────────────────────

class _FakeSMTP:
    """Context-manager SMTP stub that captures sendmail calls."""
    sent = []

    def __init__(self, host, port):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self):
        pass

    def login(self, user, pw):
        pass

    def sendmail(self, from_addr, to_addrs, body):
        _FakeSMTP.sent.append({"from": from_addr, "to": to_addrs, "body": body})


@pytest.fixture
def smtp_seam(monkeypatch):
    """Monkeypatch the service's SMTP + storage seams so no real mail/IO."""
    _FakeSMTP.sent = []
    monkeypatch.setattr(bse, "_smtp_settings",
                        lambda: {"host": "smtp.test", "port": 587,
                                 "user": "", "password": "", "from": "from@wwc.test"})
    monkeypatch.setattr(bse.smtplib, "SMTP", _FakeSMTP)
    monkeypatch.setattr("app.services.storage.read_blob", lambda key: b"%PDF-1.4")
    monkeypatch.setattr("app.services.storage.is_legacy_local_path", lambda p: False)
    return _FakeSMTP


def _make_surgery(db, *, facility="medstar", status="confirmed"):
    s = Surgery(
        chart_number="C123",
        patient_name="Doe, Jane",
        selected_facility=facility,
        eligible_facilities=[facility],
        status=status,
        surgery_number="SUR00001",
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


def _make_slip(db, s):
    f = SurgeryFile(
        surgery_id=s.id,
        kind="boarding_slip",
        filename="medstar_C123.pdf",
        path="surgery_boarding_slips/medstar_C123.pdf",
        mime_type="application/pdf",
        size_bytes=10,
    )
    db.add(f)
    db.commit()
    db.refresh(f)
    return f


# ─── Endpoint: multi-recipient send ───────────────────────────────────

def test_send_email_recipients_list(client, db, smtp_seam):
    s = _make_surgery(db)
    _make_slip(db, s)

    resp = client.post(f"/api/surgery/{s.id}/boarding-slip/send", json={
        "kind": "email",
        "recipients": ["a@x.com", "b@y.com"],
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True and body["kind"] == "email"
    assert body["to"] == ["a@x.com", "b@y.com"]
    assert len(smtp_seam.sent) == 1
    assert smtp_seam.sent[0]["to"] == ["a@x.com", "b@y.com"]


def test_send_email_comma_separated_to(client, db, smtp_seam):
    s = _make_surgery(db)
    _make_slip(db, s)

    resp = client.post(f"/api/surgery/{s.id}/boarding-slip/send", json={
        "kind": "email",
        "to": "a@x.com, b@y.com",
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["to"] == ["a@x.com", "b@y.com"]
    assert len(smtp_seam.sent) == 1
    assert smtp_seam.sent[0]["to"] == ["a@x.com", "b@y.com"]


def test_send_email_rejects_no_valid_recipient(client, db, smtp_seam):
    s = _make_surgery(db)
    _make_slip(db, s)
    resp = client.post(f"/api/surgery/{s.id}/boarding-slip/send", json={
        "kind": "email",
        "recipients": ["not-an-email", ""],
    })
    assert resp.status_code == 422
    assert smtp_seam.sent == []


# ─── Reschedule re-arm ────────────────────────────────────────────────

def test_reschedule_clears_boarding_slip_stamp(db):
    from app.services.surgery.date_picker import pick_or_reschedule

    s = Surgery(
        chart_number="R1", patient_name="Resch, Patient",
        eligible_facilities=["medstar"], selected_facility="medstar",
        status="confirmed", procedure_classification="robotic_180",
    )
    db.add(s)
    db.flush()

    bd1 = BlockDay(facility="medstar",
                   block_date=date.today() + timedelta(days=14),
                   block_kind="robotic_180",
                   start_time=time(7, 0), end_time=time(17, 0))
    bd2 = BlockDay(facility="medstar",
                   block_date=date.today() + timedelta(days=21),
                   block_kind="robotic_180",
                   start_time=time(7, 0), end_time=time(17, 0))
    db.add_all([bd1, bd2])
    db.commit()

    # Initial booking onto bd1.
    pick_or_reschedule(db, s, block_day_id=str(bd1.id), picked_by="staff@wwc.test")
    # Arm the auto-email stamp.
    s.boarding_slip_auto_emailed_at = now_utc_naive()
    db.commit()

    # Reschedule onto bd2 — should clear the stamp.
    result = pick_or_reschedule(db, s, block_day_id=str(bd2.id),
                                picked_by="staff@wwc.test")
    assert result["is_reschedule"] is True
    db.refresh(s)
    assert s.boarding_slip_auto_emailed_at is None
