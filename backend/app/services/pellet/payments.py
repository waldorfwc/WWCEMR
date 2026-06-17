"""Pellet payment pricing + insertion-credit ledger (Phase 2).
Stripe checkout/subscription creation + webhook handlers are added in
later tasks but live in this same module."""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from sqlalchemy.orm import Session

from app.services.pellet.settings import cfg
from app.models.pellet_payment import PelletInsertionCredit, PelletSubscription


class InsufficientCredit(Exception):
    pass


def _money(v) -> Decimal:
    return Decimal(str(v if v is not None else 0)).quantize(Decimal("0.01"), ROUND_HALF_UP)


def insertion_price(db: Session) -> Decimal:
    return _money(cfg(db, "insertion_price"))


def package_price(db: Session, count: int) -> Decimal:
    """count × insertion_price × (1 − tier%). Highest tier whose `count`
    is ≤ the requested count applies; no tier → full price."""
    price = insertion_price(db)
    tiers = cfg(db, "package_discount_tiers") or []
    pct = 0
    for t in sorted(tiers, key=lambda t: t.get("count", 0)):
        if count >= int(t.get("count", 0)):
            pct = int(t.get("percent_off", 0))
    gross = price * count
    return _money(gross * (Decimal(100 - pct) / Decimal(100)))


def credit_balance(db: Session, patient) -> int:
    rows = (db.query(PelletInsertionCredit)
              .filter(PelletInsertionCredit.pellet_patient_id == patient.id).all())
    return sum(r.delta for r in rows)


def _active_subscription(db: Session, patient):
    return (db.query(PelletSubscription)
              .filter(PelletSubscription.pellet_patient_id == patient.id,
                      PelletSubscription.status == "active").first())


def available_insertions(db: Session, patient) -> int:
    bal = credit_balance(db, patient)
    sub = _active_subscription(db, patient)
    price = insertion_price(db)
    sub_units = int((Decimal(sub.accrued_credit) / price)) if (sub and price > 0) else 0
    return bal + sub_units


def consume_insertion(db: Session, patient, *, by: str | None = None,
                      reason: str = "insertion completed") -> str:
    """Draw down one insertion: prefer a package/single credit, else a
    subscription's accrued credit (by the insertion price). Raises
    InsufficientCredit if neither is available. Caller commits."""
    if credit_balance(db, patient) >= 1:
        db.add(PelletInsertionCredit(pellet_patient_id=patient.id, delta=-1,
                                     source="consume", reason=reason, created_by=by))
        return "credit"
    sub = _active_subscription(db, patient)
    price = insertion_price(db)
    if sub and Decimal(sub.accrued_credit) >= price:
        sub.accrued_credit = _money(Decimal(sub.accrued_credit) - price)
        return "subscription"
    raise InsufficientCredit("no insertion credit available")
