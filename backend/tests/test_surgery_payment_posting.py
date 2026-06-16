"""Surgery 'Payment Posting' tab — list paid Stripe payments, mark them
transferred/posted to ModMed (records typed initials + authenticated user),
and let a manager reverse the mark.

Drives the real /surgery/payment-postings endpoints. The test user is a
super-admin (effective tier MANAGE on every module), so both the WORK-gated
mark and the MANAGE-gated un-mark are reachable here; tier enforcement
itself is covered by the shared permission tests.
"""
from datetime import date
from decimal import Decimal

from app.models.surgery import Surgery
from app.models.stripe_payment import SurgeryPayment
from app.utils.dt import now_utc_naive


def _seed_paid_payment(db, *, chart="MRN100", name="Doe, Jane",
                       kind="patient_balance", amount="500.00"):
    s = Surgery(chart_number=chart, patient_name=name, status="new",
                surgery_number="SUR00100",
                procedures=[{"description": "Laparoscopic hysterectomy", "cpt": "58571"}])
    db.add(s); db.flush()
    p = SurgeryPayment(
        surgery_id=s.id, kind=kind,
        stripe_payment_intent_id=f"pi_test_{chart}",
        amount_requested=Decimal(amount), amount_paid=Decimal(amount),
        status="paid", requested_by="reception@x.com",
        paid_at=now_utc_naive(),
    )
    db.add(p); db.commit()
    return s, p


def test_list_returns_paid_payments_with_mrn_name_and_confirmation(client, db):
    _seed_paid_payment(db)
    r = client.get("/api/surgery/payment-postings")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["unposted_count"] == 1
    row = body["items"][0]
    assert row["chart_number"] == "MRN100"
    assert row["patient_name"] == "Doe, Jane"
    assert row["surgery_number"] == "SUR00100"
    assert "hysterectomy" in (row["procedure_summary"] or "").lower()
    assert row["confirmation"] == "pi_test_MRN100"
    assert row["amount_paid"] == 500.0
    assert row["kind_label"] == "Patient Balance"
    assert row["posted"] is False


def test_list_includes_all_paid_kinds(client, db):
    _seed_paid_payment(db, chart="MRN1", kind="patient_balance")
    _seed_paid_payment(db, chart="MRN2", kind="fmla_fee")
    _seed_paid_payment(db, chart="MRN3", kind="cancellation_fee")
    body = client.get("/api/surgery/payment-postings").json()
    kinds = {it["kind"] for it in body["items"]}
    assert kinds == {"patient_balance", "fmla_fee", "cancellation_fee"}


def test_unpaid_payments_are_excluded(client, db):
    s, p = _seed_paid_payment(db)
    p.status = "requested"; p.paid_at = None
    db.commit()
    body = client.get("/api/surgery/payment-postings").json()
    assert body["items"] == []


def test_mark_posted_records_initials_and_user(client, db):
    _s, p = _seed_paid_payment(db)
    r = client.post(f"/api/surgery/payment-postings/{p.id}/post",
                    json={"initials": "JD"})
    assert r.status_code == 200, r.text
    row = r.json()
    assert row["posted"] is True
    assert row["posted_initials"] == "JD"
    assert row["posted_by"]            # the authenticated user's email
    assert row["posted_at"] is not None

    db.refresh(p)
    assert p.posted_to_modmed_at is not None
    assert p.posted_to_modmed_initials == "JD"
    assert p.posted_to_modmed_by


def test_marking_twice_conflicts(client, db):
    _s, p = _seed_paid_payment(db)
    client.post(f"/api/surgery/payment-postings/{p.id}/post", json={"initials": "JD"})
    r = client.post(f"/api/surgery/payment-postings/{p.id}/post", json={"initials": "JD"})
    assert r.status_code == 409


def test_initials_required(client, db):
    _s, p = _seed_paid_payment(db)
    r = client.post(f"/api/surgery/payment-postings/{p.id}/post", json={"initials": ""})
    assert r.status_code == 422


def test_unpost_reverses_and_stamps(client, db):
    _s, p = _seed_paid_payment(db)
    client.post(f"/api/surgery/payment-postings/{p.id}/post", json={"initials": "JD"})
    r = client.post(f"/api/surgery/payment-postings/{p.id}/unpost")
    assert r.status_code == 200, r.text
    assert r.json()["posted"] is False

    db.refresh(p)
    assert p.posted_to_modmed_at is None
    assert p.posted_to_modmed_initials is None
    assert p.posting_unmarked_at is not None
    assert p.posting_unmarked_by


def test_unpost_when_not_posted_conflicts(client, db):
    _s, p = _seed_paid_payment(db)
    r = client.post(f"/api/surgery/payment-postings/{p.id}/unpost")
    assert r.status_code == 409


def test_posted_filter(client, db):
    _s, p1 = _seed_paid_payment(db, chart="MRN1")
    _s2, _p2 = _seed_paid_payment(db, chart="MRN2")
    client.post(f"/api/surgery/payment-postings/{p1.id}/post", json={"initials": "AB"})

    posted = client.get("/api/surgery/payment-postings?posted=posted").json()
    assert [it["chart_number"] for it in posted["items"]] == ["MRN1"]

    unposted = client.get("/api/surgery/payment-postings?posted=unposted").json()
    assert [it["chart_number"] for it in unposted["items"]] == ["MRN2"]
