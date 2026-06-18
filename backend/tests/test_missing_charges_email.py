"""Missing Charges provider emails: HTML escaping of untrusted Excel values,
stamp-only-on-actual-send, and per-provider error isolation."""
from datetime import date

import app.services.missing_charges_email as mce
from app.models.missing_charge import MissingCharge, ProviderUserMapping
from app.models.user import User


def _charge(db, provider="Salley, Danielle", name="Smith, Ann", **kw):
    base = dict(patient_mrn="100", appointment_date=date(2026, 5, 1),
                patient_name=name, status="needs_to_be_billed",
                primary_provider=provider, appointment_type="WWE - Est",
                payer="BCBS")
    base.update(kw)
    c = MissingCharge(**base); db.add(c); db.commit(); db.refresh(c)
    return c


def _user(db, email="dani@wwc.com", display_name="Salley, Danielle"):
    u = User(email=email, display_name=display_name, is_active=True)
    db.add(u)
    db.add(ProviderUserMapping(provider_name=display_name, user_email=email,
                               is_active="Y", is_ignored="N"))
    db.commit()
    return u


def test_build_email_escapes_untrusted_values():
    c = MissingCharge(patient_mrn="1", appointment_date=date(2026, 5, 1),
                      patient_name="<b>Smith</b> & Co", appointment_type="WWE <x>",
                      payer="A & B", status="needs_to_be_billed",
                      primary_provider="Salley, Danielle")
    _subj, html, _text = mce._build_email("Salley, Danielle", "https://x/p/t", [c])
    assert "<b>Smith</b>" not in html          # raw markup must not survive
    assert "&lt;b&gt;Smith&lt;/b&gt; &amp; Co" in html
    assert "A &amp; B" in html


def test_stamp_only_on_actual_send(db, monkeypatch):
    _user(db)
    c = _charge(db)
    monkeypatch.setattr(mce, "send_email", lambda *a, **k: True)
    rep = mce.send_provider_emails(db)
    db.refresh(c)
    assert c.last_emailed_at is not None
    assert rep["sent_count"] == 1


def test_no_stamp_when_send_fails(db, monkeypatch):
    _user(db)
    c = _charge(db)
    monkeypatch.setattr(mce, "send_email", lambda *a, **k: False)   # logged_only
    rep = mce.send_provider_emails(db)
    db.refresh(c)
    assert c.last_emailed_at is None
    assert rep["sent_count"] == 0
    assert rep["providers"][0]["status"] == "logged_only"


def test_one_provider_failure_does_not_abort_batch(db, monkeypatch):
    _user(db, email="dani@wwc.com", display_name="Salley, Danielle")
    _user(db, email="pat@wwc.com", display_name="Nurse, Pat")
    _charge(db, provider="Salley, Danielle", name="A")
    _charge(db, provider="Nurse, Pat", name="B", patient_mrn="200")

    calls = {"n": 0}
    def flaky(to, *a, **k):
        calls["n"] += 1
        if to == "dani@wwc.com":
            raise RuntimeError("smtp blew up")
        return True
    monkeypatch.setattr(mce, "send_email", flaky)

    rep = mce.send_provider_emails(db)        # must NOT raise
    statuses = {p["provider"]: p["status"] for p in rep["providers"]}
    assert statuses["Salley, Danielle"] == "error"
    assert statuses["Nurse, Pat"] == "sent"
    assert rep["sent_count"] == 1
