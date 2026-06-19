"""T1 — boarding-slip email data layer + service."""
from __future__ import annotations

from datetime import timedelta

import pytest

from app.utils.dt import now_utc_naive
from app.models.surgery import Surgery, SurgeryFile, SurgerySlot, BlockDay
from app.models.surgery_config import SurgeryConfig
from app.services.surgery import boarding_slip_email as bse


# ─── Fixtures / helpers ───────────────────────────────────────────────

def _set_cfg(db, key, value):
    db.add(SurgeryConfig(key=key, value=value, updated_by="test"))
    db.commit()


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


def _make_slot(db, s, *, created_at=None):
    bd = BlockDay(
        facility=s.selected_facility,
        block_date=now_utc_naive().date(),
        block_kind="mixed",
        start_time=now_utc_naive().time().replace(second=0, microsecond=0),
        end_time=now_utc_naive().time().replace(second=0, microsecond=0),
    )
    db.add(bd)
    db.commit()
    slot = SurgerySlot(
        block_day_id=bd.id,
        surgery_id=s.id,
        start_time=bd.start_time,
        duration_minutes=180,
        procedure_kind="major",
    )
    db.add(slot)
    db.commit()
    if created_at is not None:
        slot.created_at = created_at
        db.commit()
    return slot


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
    _FakeSMTP.sent = []
    monkeypatch.setattr(bse, "_smtp_settings",
                        lambda: {"host": "smtp.test", "port": 587,
                                 "user": "", "password": "", "from": "from@wwc.test"})
    monkeypatch.setattr(bse.smtplib, "SMTP", _FakeSMTP)
    monkeypatch.setattr("app.services.storage.read_blob", lambda key: b"%PDF-1.4")
    monkeypatch.setattr("app.services.storage.is_legacy_local_path", lambda p: False)
    return _FakeSMTP


# ─── recipients_for ───────────────────────────────────────────────────

def test_recipients_for_per_facility(db):
    _set_cfg(db, "boarding_slip_recipients_medstar",
             ["a@x.com", "B@X.COM", "", "not-an-email"])
    _set_cfg(db, "boarding_slip_recipients_crmc", ["c@x.com"])

    assert bse.recipients_for(db, "medstar") == ["a@x.com", "b@x.com"]
    assert bse.recipients_for(db, "crmc") == ["c@x.com"]
    # Non-hospital facilities never have a slip.
    assert bse.recipients_for(db, "office") == []


# ─── send_boarding_slip_email ─────────────────────────────────────────

def test_send_to_multiple_recipients(db, smtp_seam):
    s = _make_surgery(db)
    f = _make_slip(db, s)

    result = bse.send_boarding_slip_email(
        db, s, f, ["one@x.com", "Two@X.com"], sent_by="staff@wwc.test")

    assert result == {"ok": True, "to": ["one@x.com", "two@x.com"]}
    assert len(smtp_seam.sent) == 1
    call = smtp_seam.sent[0]
    assert call["to"] == ["one@x.com", "two@x.com"]
    assert "one@x.com" in call["body"] and "two@x.com" in call["body"]
    # Send history recorded on the file.
    db.refresh(f)
    assert f.send_history and f.send_history[-1]["status"] == "sent"
    assert f.send_history[-1]["to"] == ["one@x.com", "two@x.com"]


def test_send_raises_when_smtp_unconfigured(db, monkeypatch):
    s = _make_surgery(db)
    f = _make_slip(db, s)
    monkeypatch.setattr(bse, "_smtp_settings",
                        lambda: {"host": "", "port": 587, "user": "",
                                 "password": "", "from": ""})
    with pytest.raises(ValueError):
        bse.send_boarding_slip_email(db, s, f, ["one@x.com"], sent_by="x@wwc.test")


# ─── auto_email_sweep ─────────────────────────────────────────────────

def test_sweep_disabled(db):
    _set_cfg(db, "boarding_slip_auto_email_enabled", False)
    assert bse.auto_email_sweep(db) == {"skipped": "disabled"}


def test_sweep_sends_and_stamps(db, smtp_seam):
    _set_cfg(db, "boarding_slip_auto_email_enabled", True)
    _set_cfg(db, "boarding_slip_auto_email_hours", 24)
    _set_cfg(db, "boarding_slip_recipients_medstar", ["sched@hospital.test"])

    s = _make_surgery(db)
    _make_slip(db, s)
    old = now_utc_naive() - timedelta(hours=48)
    _make_slot(db, s, created_at=old)

    out = bse.auto_email_sweep(db)
    assert out == {"sent": 1, "skipped_no_recipients": 0, "errors": 0}
    db.refresh(s)
    assert s.boarding_slip_auto_emailed_at is not None
    assert len(smtp_seam.sent) == 1


def test_sweep_skips_when_no_recipients(db, smtp_seam):
    _set_cfg(db, "boarding_slip_auto_email_enabled", True)
    _set_cfg(db, "boarding_slip_auto_email_hours", 24)
    _set_cfg(db, "boarding_slip_recipients_medstar", [])

    s = _make_surgery(db)
    _make_slip(db, s)
    _make_slot(db, s, created_at=now_utc_naive() - timedelta(hours=48))

    out = bse.auto_email_sweep(db)
    assert out == {"sent": 0, "skipped_no_recipients": 1, "errors": 0}
    db.refresh(s)
    assert s.boarding_slip_auto_emailed_at is None
    assert smtp_seam.sent == []


def test_sweep_does_not_resend_already_stamped(db, smtp_seam):
    _set_cfg(db, "boarding_slip_auto_email_enabled", True)
    _set_cfg(db, "boarding_slip_auto_email_hours", 24)
    _set_cfg(db, "boarding_slip_recipients_medstar", ["sched@hospital.test"])

    s = _make_surgery(db)
    s.boarding_slip_auto_emailed_at = now_utc_naive() - timedelta(hours=1)
    db.commit()
    _make_slip(db, s)
    _make_slot(db, s, created_at=now_utc_naive() - timedelta(hours=48))

    out = bse.auto_email_sweep(db)
    assert out == {"sent": 0, "skipped_no_recipients": 0, "errors": 0}
    assert smtp_seam.sent == []


def test_sweep_ignores_recent_slot(db, smtp_seam):
    _set_cfg(db, "boarding_slip_auto_email_enabled", True)
    _set_cfg(db, "boarding_slip_auto_email_hours", 24)
    _set_cfg(db, "boarding_slip_recipients_medstar", ["sched@hospital.test"])

    s = _make_surgery(db)
    _make_slip(db, s)
    _make_slot(db, s, created_at=now_utc_naive() - timedelta(hours=2))

    out = bse.auto_email_sweep(db)
    assert out == {"sent": 0, "skipped_no_recipients": 0, "errors": 0}
    assert smtp_seam.sent == []
