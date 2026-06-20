from app.services.larc.workflow import (
    IN_STOCK_MILESTONES, PHARMACY_ORDER_MILESTONES, ALL_BUCKETS)


def test_no_appt_scheduled_milestone():
    kinds_in = [k for k, *_ in IN_STOCK_MILESTONES]
    kinds_ph = [k for k, *_ in PHARMACY_ORDER_MILESTONES]
    assert "appt_scheduled" not in kinds_in
    assert "appt_scheduled" not in kinds_ph
    assert "appt_scheduled" not in ALL_BUCKETS
