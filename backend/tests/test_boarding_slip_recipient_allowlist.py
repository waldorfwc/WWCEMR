"""Recipient-domain allowlist (fail-closed) for boarding-slip email.

When `boarding_slip_recipient_allowed_domains` is non-empty, a boarding slip
may only be emailed if EVERY recipient's domain is on the list — otherwise
send_boarding_slip_email raises ValueError and NOTHING is sent (the manual
endpoint maps ValueError → 4xx; the auto-sweep catches per-provider). An
empty allowlist behaves exactly as before (backward-compatible).
"""
from __future__ import annotations

import pytest

from app.models.surgery import Surgery, SurgeryFile
from app.models.surgery_config import SurgeryConfig
from app.services.surgery import boarding_slip_email as bse


def _set_cfg(db, key, value):
    db.add(SurgeryConfig(key=key, value=value, updated_by="test"))
    db.commit()


def _make_surgery(db, *, facility="medstar"):
    s = Surgery(chart_number="C123", patient_name="Doe, Jane",
                selected_facility=facility, eligible_facilities=[facility],
                status="confirmed", surgery_number="SUR00001")
    db.add(s); db.commit(); db.refresh(s)
    return s


def _make_slip(db, s):
    f = SurgeryFile(surgery_id=s.id, kind="boarding_slip",
                    filename="medstar_C123.pdf",
                    path="surgery_boarding_slips/medstar_C123.pdf",
                    mime_type="application/pdf", size_bytes=10)
    db.add(f); db.commit(); db.refresh(f)
    return f


class _FakeSMTP:
    sent: list = []

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
    _FakeSMTP.sent = []
    monkeypatch.setattr(bse, "_smtp_settings",
                        lambda: {"host": "smtp.test", "port": 587,
                                 "user": "", "password": "", "from": "from@wwc.test"})
    monkeypatch.setattr(bse.smtplib, "SMTP", _FakeSMTP)
    monkeypatch.setattr("app.services.storage.read_blob", lambda key: b"%PDF-1.4")
    monkeypatch.setattr("app.services.storage.is_legacy_local_path", lambda p: False)
    return _FakeSMTP


def test_disallowed_domain_raises_and_sends_nothing(db, smtp_seam):
    _set_cfg(db, "boarding_slip_recipient_allowed_domains", ["medstar.org"])
    s = _make_surgery(db)
    f = _make_slip(db, s)
    with pytest.raises(ValueError) as exc:
        bse.send_boarding_slip_email(db, s, f, ["a@gmail.com"], sent_by="x@wwc.test")
    assert "allowed domain" in str(exc.value)
    assert smtp_seam.sent == []  # fail closed — nothing left the building


def test_allowed_domain_sends(db, smtp_seam):
    _set_cfg(db, "boarding_slip_recipient_allowed_domains", ["medstar.org"])
    s = _make_surgery(db)
    f = _make_slip(db, s)
    result = bse.send_boarding_slip_email(db, s, f, ["a@medstar.org"],
                                          sent_by="x@wwc.test")
    assert result == {"ok": True, "to": ["a@medstar.org"]}
    assert len(smtp_seam.sent) == 1


def test_mixed_recipients_fail_closed(db, smtp_seam):
    _set_cfg(db, "boarding_slip_recipient_allowed_domains", ["medstar.org"])
    s = _make_surgery(db)
    f = _make_slip(db, s)
    with pytest.raises(ValueError):
        bse.send_boarding_slip_email(
            db, s, f, ["ok@medstar.org", "bad@gmail.com"], sent_by="x@wwc.test")
    # One bad recipient blocks the whole send (no PHI to the good one either).
    assert smtp_seam.sent == []


def test_empty_allowlist_is_backward_compatible(db, smtp_seam):
    # No config row at all → default [] → no restriction.
    s = _make_surgery(db)
    f = _make_slip(db, s)
    result = bse.send_boarding_slip_email(db, s, f, ["a@gmail.com"],
                                          sent_by="x@wwc.test")
    assert result == {"ok": True, "to": ["a@gmail.com"]}
    assert len(smtp_seam.sent) == 1
