"""B1 — Bai2Exclusion model + identity helpers.

Round-trips the sticky-exclusion key/identity helpers and confirms the
model persists + queries via the active-identity helper.
"""
from datetime import date
from decimal import Decimal

from app.models.bai2_exclusion import Bai2Exclusion
from app.routers.bank_recon import (
    _exclusion_key, _active_exclusion_identities, _q2,
)


def test_exclusion_key_is_stable_and_identity_normalized():
    # Same identity → same key regardless of float/Decimal/repr noise.
    k1 = _exclusion_key(date(2026, 5, 1), 500.0, "1234")
    k2 = _exclusion_key(date(2026, 5, 1), Decimal("500.00"), "1234")
    k3 = _exclusion_key(date(2026, 5, 1), "500.000001", "1234")
    assert k1 == k2 == k3
    assert len(k1) == 64  # sha256 hex
    # Different last4 / amount / date → different keys.
    assert _exclusion_key(date(2026, 5, 1), 500.0, "9999") != k1
    assert _exclusion_key(date(2026, 5, 1), 600.0, "1234") != k1
    assert _exclusion_key(date(2026, 5, 2), 500.0, "1234") != k1
    # None last4 collapses to "".
    assert _exclusion_key(date(2026, 5, 1), 500.0, None) == \
        _exclusion_key(date(2026, 5, 1), 500.0, "")


def test_active_exclusion_identities_round_trip(db):
    active = Bai2Exclusion(
        exclusion_key=_exclusion_key(date(2026, 5, 1), 500.0, "1234"),
        transaction_date=date(2026, 5, 1),
        amount=Decimal("500.00"), last_4="1234",
        description="SomePayer ACH x1234", reason="not ours",
        excluded_by="tester@example.com",
    )
    reinstated = Bai2Exclusion(
        exclusion_key=_exclusion_key(date(2026, 5, 2), 250.0, "9999"),
        transaction_date=date(2026, 5, 2),
        amount=Decimal("250.00"), last_4="9999",
        excluded_by="tester@example.com",
    )
    reinstated.soft_delete("manager@example.com")  # reinstated → not active
    db.add(active); db.add(reinstated); db.commit()

    ids = _active_exclusion_identities(db)
    assert (date(2026, 5, 1), _q2(500.0), "1234") in ids
    # Reinstated (soft-deleted) row must NOT appear.
    assert (date(2026, 5, 2), _q2(250.0), "9999") not in ids
