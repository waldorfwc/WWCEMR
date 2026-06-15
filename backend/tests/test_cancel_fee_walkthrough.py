"""Authenticated walk-through of the configurable cancellation fee: set the
config, then see the portal status + patient-cancel behavior change per the
criteria (scheduled + within window). Runs as the super-admin test client —
the same endpoints Surgery Settings and the patient portal call."""
from datetime import date, timedelta

from app.models.surgery import Surgery


def _seed(db, *, scheduled_date=None, status="new"):
    s = Surgery(chart_number="WT1", patient_name="Walk, Thru",
                cell_phone="+12405550000", dob=date(1990, 1, 1),
                status=status, scheduled_date=scheduled_date)
    db.add(s); db.commit(); db.refresh(s)
    return s


def test_cancel_fee_config_walkthrough(client, db, capsys):
    log = []

    # 1. Default config (what Surgery Settings shows out of the box)
    cfg = client.get("/api/surgery/config").json()
    log.append(f"1. defaults: fee ${cfg['cancellation_fee_amount']} within "
               f"{cfg['cancellation_fee_days_before']} days")
    assert cfg["cancellation_fee_amount"] == 351
    assert cfg["cancellation_fee_days_before"] == 14

    # 2. Admin edits the config in Surgery Settings → PUT /surgery/config
    client.put("/api/surgery/config", json={
        "cancellation_fee_amount": 500,
        "cancellation_fee_days_before": 21,
    })
    cfg = client.get("/api/surgery/config").json()
    log.append(f"2. updated: fee ${cfg['cancellation_fee_amount']} within "
               f"{cfg['cancellation_fee_days_before']} days")
    assert cfg["cancellation_fee_amount"] == 500 and cfg["cancellation_fee_days_before"] == 21

    # 3. Scheduled WITHIN the window → portal shows the fee notice (criteria met)
    inside = _seed(db, scheduled_date=date.today() + timedelta(days=10), status="confirmed")
    st = client.get(f"/api/p/surgery/{inside.id}/status").json()
    assert st["cancellation_fee_applies"] is True and st["cancellation_fee_amount"] == 500
    log.append(f"3. scheduled in 10d → fee notice SHOWS (${st['cancellation_fee_amount']}); "
               f"can_cancel={st['can_cancel']}")

    # 4. Scheduled BEYOND the window → no fee notice
    outside = _seed(db, scheduled_date=date.today() + timedelta(days=30), status="confirmed")
    st = client.get(f"/api/p/surgery/{outside.id}/status").json()
    assert st["cancellation_fee_applies"] is False
    log.append("4. scheduled in 30d → fee notice HIDDEN (outside window)")

    # 5. UNSCHEDULED (pre-scheduling) → cancel allowed, no fee notice
    unsched = _seed(db, scheduled_date=None, status="new")
    st = client.get(f"/api/p/surgery/{unsched.id}/status").json()
    assert st["cancellation_fee_applies"] is False and st["can_cancel"] is True
    log.append("5. unscheduled → cancel allowed, fee notice HIDDEN")

    # 6. Patient actually cancels the unscheduled one → no fee charged
    r = client.post(f"/api/p/surgery/{unsched.id}/cancel", json={"reason_text": "changed mind"}).json()
    assert r["fee_required"] is False
    log.append(f"6. patient cancels unscheduled → fee_required={r['fee_required']}")

    # 7. Patient cancels the within-window scheduled one → fee required
    r = client.post(f"/api/p/surgery/{inside.id}/cancel", json={"reason_text": None}).json()
    assert r["fee_required"] is True
    log.append(f"7. patient cancels in-window scheduled → fee_required={r['fee_required']}")

    with capsys.disabled():
        print("\n  ── cancellation-fee config walk-through (authenticated) ──")
        for line in log:
            print("   " + line)
