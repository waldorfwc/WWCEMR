from app.models.larc_payment import LarcPayment


def test_larc_payment_roundtrip(db):
    p = LarcPayment(assignment_id="a-1", kind="larc_patient_responsibility",
                    status="requested", amount_requested=120.00,
                    stripe_checkout_session_id="cs_test_1", checkout_url="https://x")
    db.add(p); db.commit(); db.refresh(p)
    assert p.id and p.status == "requested" and float(p.amount_requested) == 120.00
