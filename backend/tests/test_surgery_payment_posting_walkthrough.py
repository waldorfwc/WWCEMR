"""Authenticated walk-through of the Surgery 'Payment Posting' tab, driving
the real /surgery/payment-postings endpoints through the authenticated
client: list paid Stripe payments with MRN/patient/surgery/confirmation,
mark one transferred to ModMed (records typed initials + the authenticated
user's email + timestamp), confirm the Posted/Not-Posted filters, then have
a manager reverse the mark.

Run: pytest tests/test_surgery_payment_posting_walkthrough.py -s
"""
from decimal import Decimal

from app.models.surgery import Surgery
from app.models.stripe_payment import SurgeryPayment
from app.utils.dt import now_utc_naive


def _seed(db, *, chart, name, kind, amount, intent):
    s = Surgery(chart_number=chart, patient_name=name, status="new",
                surgery_number=f"SUR00{chart[-1]}",
                procedures=[{"description": "Laparoscopic hysterectomy", "cpt": "58571"}])
    db.add(s); db.flush()
    p = SurgeryPayment(
        surgery_id=s.id, kind=kind,
        stripe_payment_intent_id=intent,
        amount_requested=Decimal(amount), amount_paid=Decimal(amount),
        status="paid", requested_by="reception@waldorf", paid_at=now_utc_naive())
    db.add(p); db.commit()
    return s, p


def test_payment_posting_walkthrough(client, db, capsys):
    log = []

    # Three paid Stripe payments of different kinds + one still-unpaid request.
    _s1, p_balance = _seed(db, chart="MRN101", name="Doe, Jane",
                           kind="patient_balance", amount="1850.00", intent="pi_balance_101")
    _seed(db, chart="MRN102", name="Roe, Mary",
          kind="fmla_fee", amount="35.00", intent="pi_fmla_102")
    _seed(db, chart="MRN103", name="Poe, Edna",
          kind="cancellation_fee", amount="250.00", intent="pi_cancel_103")
    s_unpaid = Surgery(chart_number="MRN104", patient_name="Loe, Ann", status="new")
    db.add(s_unpaid); db.flush()
    db.add(SurgeryPayment(surgery_id=s_unpaid.id, kind="patient_balance",
                          stripe_payment_intent_id="pi_unpaid_104",
                          amount_requested=Decimal("500"), amount_paid=Decimal("0"),
                          status="requested", requested_by="reception@waldorf"))
    # A manual/ModMed offset (ModMed Pay swipe) — paid, but NOT from Stripe.
    s_manual = Surgery(chart_number="MRN105", patient_name="Manu, Al", status="new")
    db.add(s_manual); db.flush()
    db.add(SurgeryPayment(surgery_id=s_manual.id, kind="manual_offset",
                          amount_requested=Decimal("400"), amount_paid=Decimal("400"),
                          status="paid", requested_by="reception@waldorf",
                          paid_at=now_utc_naive()))
    db.commit()
    log.append("seeded 3 PAID Stripe payments (balance/FMLA/cancellation), "
               "1 unpaid request, and 1 PAID manual/ModMed offset")

    # 1. List — Stripe paid only; unpaid AND manual offsets excluded.
    body = client.get("/api/surgery/payment-postings").json()
    by_mrn = {it["chart_number"]: it for it in body["items"]}
    assert set(by_mrn) == {"MRN101", "MRN102", "MRN103"}      # unpaid + manual excluded
    assert body["unposted_count"] == 3
    row = by_mrn["MRN101"]
    assert row["patient_name"] == "Doe, Jane"
    assert row["surgery_number"] == "SUR001"
    assert "hysterectomy" in row["procedure_summary"].lower()
    assert row["kind_label"] == "Patient Balance"
    assert row["amount_paid"] == 1850.0
    assert row["confirmation"] == "pi_balance_101"
    assert row["posted"] is False
    log.append("1. GET list → 3 paid STRIPE rows; unpaid 'requested' AND the paid "
               "manual/ModMed offset (MRN105) both excluded — only Stripe payments post")

    # 2. Reception marks the patient-balance payment transferred to ModMed.
    posted = client.post(f"/api/surgery/payment-postings/{p_balance.id}/post",
                         json={"initials": "jd"}).json()
    assert posted["posted"] is True
    assert posted["posted_initials"] == "jd"
    assert posted["posted_by"]                       # authenticated user's email
    assert posted["posted_at"]
    log.append(f"2. POST .../post (initials 'jd') → posted=True, "
               f"recorded by {posted['posted_by']} at {posted['posted_at'][:19]}")

    # 3. Can't double-mark.
    dup = client.post(f"/api/surgery/payment-postings/{p_balance.id}/post",
                      json={"initials": "jd"})
    assert dup.status_code == 409
    log.append("3. POST .../post again → 409 (already posted, no double-marking)")

    # 4. Filters reflect the mark.
    posted_list = client.get("/api/surgery/payment-postings?posted=posted").json()
    unposted_list = client.get("/api/surgery/payment-postings?posted=unposted").json()
    assert [it["chart_number"] for it in posted_list["items"]] == ["MRN101"]
    assert {it["chart_number"] for it in unposted_list["items"]} == {"MRN102", "MRN103"}
    log.append("4. ?posted=posted → [MRN101]; ?posted=unposted → {MRN102, MRN103}")

    # 5. Manager reverses the posting; it returns to Not-Posted.
    rev = client.post(f"/api/surgery/payment-postings/{p_balance.id}/unpost").json()
    assert rev["posted"] is False
    db.refresh(p_balance)
    assert p_balance.posted_to_modmed_at is None
    assert p_balance.posting_unmarked_by                 # who reversed it is stamped
    again = client.get("/api/surgery/payment-postings?posted=unposted").json()
    assert "MRN101" in {it["chart_number"] for it in again["items"]}
    log.append(f"5. POST .../unpost (manager) → posted=False, reversal stamped to "
               f"{p_balance.posting_unmarked_by}; row back in Not-Posted")

    with capsys.disabled():
        print("\n  ── Surgery Payment Posting walk-through (authenticated) ──")
        for line in log:
            print("   " + line)
