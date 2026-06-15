"""B2 — patient portal status + cancel drive the cancellation fee from
config (cancellation_fee_amount / cancellation_fee_days_before) instead
of the old hardcoded $351 / 14 days."""
from datetime import date, timedelta

from app.models.surgery import Surgery


def _seed(db, *, scheduled_date=None, status="new", amount_paid=None):
    s = Surgery(chart_number="1", patient_name="Pat",
                cell_phone="+12405551234", dob=date(1990, 1, 1),
                status=status, scheduled_date=scheduled_date,
                amount_paid=amount_paid)
    db.add(s); db.commit(); db.refresh(s)
    return s


def _set_fee_config(client, *, amount, days):
    resp = client.put("/api/surgery/config", json={
        "cancellation_fee_amount": amount,
        "cancellation_fee_days_before": days,
    })
    assert resp.status_code == 200


def test_status_fee_applies_within_configured_window(client, db):
    _set_fee_config(client, amount=500, days=21)
    s = _seed(db, scheduled_date=date.today() + timedelta(days=10),
              status="confirmed")
    body = client.get(f"/api/p/surgery/{s.id}/status").json()
    assert body["cancellation_fee_applies"] is True
    assert body["cancellation_fee_amount"] == 500
    assert body["cancellation_fee_days_before"] == 21
    assert body["can_cancel"] is True


def test_status_fee_does_not_apply_beyond_window(client, db):
    _set_fee_config(client, amount=500, days=21)
    s = _seed(db, scheduled_date=date.today() + timedelta(days=30),
              status="confirmed")
    body = client.get(f"/api/p/surgery/{s.id}/status").json()
    assert body["cancellation_fee_applies"] is False
    assert body["can_cancel"] is True


def test_status_unscheduled_applies_false_can_cancel_true(client, db):
    s = _seed(db, scheduled_date=None, status="new")
    body = client.get(f"/api/p/surgery/{s.id}/status").json()
    assert body["cancellation_fee_applies"] is False
    assert body["can_cancel"] is True
    # Defaults present when no config override.
    assert body["cancellation_fee_amount"] == 351
    assert body["cancellation_fee_days_before"] == 14


def test_status_can_cancel_false_when_terminal(client, db):
    s = _seed(db, scheduled_date=None, status="cancelled")
    body = client.get(f"/api/p/surgery/{s.id}/status").json()
    assert body["can_cancel"] is False


def test_cancel_unscheduled_surgery_no_fee(client, db):
    s = _seed(db, scheduled_date=None, status="new")
    resp = client.post(f"/api/p/surgery/{s.id}/cancel", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["fee_required"] is False
    assert "fee" not in body["message"].lower()


def test_cancel_within_window_uses_configured_amount(client, db):
    _set_fee_config(client, amount=500, days=21)
    s = _seed(db, scheduled_date=date.today() + timedelta(days=10),
              status="confirmed")
    resp = client.post(f"/api/p/surgery/{s.id}/cancel", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["fee_required"] is True
    assert "$500" in body["message"]
    assert "21 days" in body["message"]
