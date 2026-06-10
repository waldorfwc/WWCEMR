"""Stripe payments — coordinator + patient endpoints + webhook receiver.

Permissions:
  POST /surgery/{id}/request-payment            surgery:work
  GET  /surgery/{id}/payments                   claim:read
  POST /surgery/payments/{payment_id}/refund    user:manage
  POST /p/surgery/{id}/pay                      token-gated (patient JWT)
  POST /stripe/webhook                          public (signature verified)
"""
from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.stripe_payment import (
    StripeCustomer, SurgeryPayment, SurgeryPaymentHistory,
    SURGERY_PAYMENT_STATUSES,
)
from app.models.surgery import Surgery
from app.routers.auth import get_current_user
from app.permissions.catalog import Module, Tier
from app.permissions.dependencies import requires_tier
from app.routers.patient_surgery import require_patient_token
from app.services import stripe_payments as svc
from app.services.audit_service import log_action
from app.services.patient_email import send_patient_email

log = logging.getLogger(__name__)

router = APIRouter(tags=["stripe-payments"])


# ─── Coordinator: request a payment ─────────────────────────────────

class RequestPaymentIn(BaseModel):
    # Optional because we may auto-compute from outstanding balance. When
    # provided, must fit Numeric(10,2) and be > 0; constraint catches
    # NaN/Inf (which compare False against numbers).
    amount: Optional[Decimal] = Field(default=None, gt=0, le=99_999.99)
    description: Optional[str] = None


@router.post("/surgery/{surgery_id}/request-payment")
def request_payment(
    surgery_id: str,
    payload: RequestPaymentIn,
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.WORK)),
):
    """Create a Stripe Checkout Session for the surgery's outstanding
    pre-op balance. Returns the hosted checkout URL the coordinator can
    share with the patient."""
    if not svc.is_configured():
        raise HTTPException(status_code=503,
                            detail="Stripe is not configured on the server")

    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="surgery not found")

    amount = payload.amount or _outstanding_balance(s)
    if amount is None or amount <= 0:
        raise HTTPException(
            status_code=422,
            detail="no outstanding balance — set patient_responsibility first")

    description = (payload.description or "Pre-op balance").strip()
    actor = current_user.get("email") or "system"
    try:
        pay = svc.create_checkout_session(db, s, amount=amount,
                                            description=description,
                                            actor=actor)
    except Exception as e:
        log.exception("stripe checkout create failed")
        raise HTTPException(status_code=502, detail=f"Stripe error: {e}")

    # Auto-email the patient with the checkout link (soft-fail).
    send_patient_email(
        db,
        kind="stripe_payment_link",
        to_email=s.email,
        context={
            "patient_name":  s.patient_name,
            "amount":        f"{amount:.2f}",
            "checkout_url":  pay.checkout_url,
        },
        sent_by=actor,
        surgery_id=s.id,
        chart_number=s.chart_number,
    )
    from app.services.patient_sms import send_patient_sms, build_sms_context
    send_patient_sms(
        db, kind="sms_payment_link",
        surgery=s,
        context=build_sms_context(s,
                                    amount=f"${amount:.2f}",
                                    payment_link=pay.checkout_url),
        sent_by=actor,
    )

    log_action(
        db,
        action="PAYMENT_REQUESTED",
        resource_type="surgery_payment",
        resource_id=str(pay.id),
        patient_id=s.chart_number or None,
        user_id=(actor or "").lower() or None,
        user_name=actor,
        description=f"Stripe checkout for ${amount:.2f} on surgery {s.id}",
    )

    return _payment_dict(pay)


# ─── Coordinator: list payments for a surgery ───────────────────────

@router.get("/surgery/{surgery_id}/payments")
def list_payments(
    surgery_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.VIEW)),
):
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="surgery not found")
    rows = (db.query(SurgeryPayment)
              .filter(SurgeryPayment.surgery_id == s.id)
              .order_by(SurgeryPayment.requested_at.desc())
              .all())
    return {
        "outstanding_balance": str(_outstanding_balance(s) or 0),
        "patient_responsibility": str(s.patient_responsibility or 0),
        "amount_paid":             str(s.amount_paid or 0),
        "payments": [_payment_dict(p) for p in rows],
    }


# ─── Admin: refund ──────────────────────────────────────────────────

class RefundIn(BaseModel):
    # None = full refund. When set, must be > 0 and fit Numeric(10,2).
    amount: Optional[Decimal] = Field(default=None, gt=0, le=99_999.99)
    reason: Optional[str] = None


@router.post("/surgery/payments/{payment_id}/refund")
def refund(
    payment_id: str,
    payload: RefundIn,
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.MANAGE)),
):
    p = db.query(SurgeryPayment).filter(SurgeryPayment.id == payment_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="payment not found")
    # Allow follow-up partial refunds on an already-partially-refunded
    # payment. Previously the gate was "paid" only, so after one $10 of
    # $500 partial flipped the status to refunded (broken pre-C2), the
    # next refund call was rejected. (Fable billing audit C2.)
    if p.status not in ("paid", "partially_refunded"):
        raise HTTPException(status_code=409,
                            detail=f"can only refund a paid or partially_refunded "
                                   f"payment (current: {p.status})")
    # Local over-refund check: refuse to ask Stripe for more than the
    # remaining refundable amount. Stripe will reject too, but the
    # local check gives a useful error before the API call.
    if payload.amount is not None:
        remaining = Decimal(p.amount_paid or 0) - Decimal(p.amount_refunded or 0)
        if Decimal(payload.amount) > remaining:
            raise HTTPException(
                status_code=422,
                detail=(f"refund amount ${payload.amount} exceeds remaining "
                        f"refundable ${remaining:.2f} on this payment"))
    actor = current_user.get("email") or "system"
    try:
        ref = svc.refund_payment(db, p, amount=payload.amount)
    except Exception as e:
        # Don't leak raw Stripe exception detail to clients (may include
        # internal request ids / metadata). Log + return a generic 502.
        # (Fable billing audit L3.)
        log.exception("Stripe refund failed for payment %s", p.id)
        raise HTTPException(status_code=502, detail="Stripe refund failed")
    db.add(SurgeryPaymentHistory(
        payment_id=p.id, actor=actor,
        event_type="admin.refund_initiated",
        before_status=p.status, after_status=p.status,
        detail={"refund_id": ref.get("id"),
                "amount": str(payload.amount) if payload.amount else "full",
                "reason": payload.reason},
    ))
    log_action(
        db,
        action="REFUND",
        resource_type="surgery_payment",
        resource_id=str(p.id),
        user_id=(actor or "").lower() or None,
        user_name=actor,
        description=(f"Refund {payload.amount or 'full'} on payment {p.id}"
                     + (f" — {payload.reason}" if payload.reason else "")),
    )
    db.commit()
    return {"ok": True, "refund_id": ref.get("id"),
            "amount": str(payload.amount) if payload.amount else "full"}


# ─── Patient: self-service pay ──────────────────────────────────────

@router.post("/p/surgery/{surgery_id}/pay")
def patient_pay(
    surgery_id: str,
    db: Session = Depends(get_db),
    _token: str = Depends(require_patient_token),
):
    """Patient-initiated payment. Gated by the same patient JWT used by
    other /p/surgery/* endpoints (DOB + last-4 auth → bearer token).
    H4 frontend will obtain the token via /p/surgery/{id}/auth first."""
    if not svc.is_configured():
        raise HTTPException(status_code=503,
                            detail="Stripe is not configured on the server")
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="surgery not found")
    amount = _outstanding_balance(s)
    if amount is None or amount <= 0:
        raise HTTPException(status_code=422, detail="no outstanding balance")
    try:
        pay = svc.create_checkout_session(
            db, s, amount=amount,
            description="Surgery balance (patient self-service)",
            actor="patient:self-service",
        )
    except Exception as e:
        log.exception("stripe checkout create failed (patient)")
        raise HTTPException(status_code=502, detail=f"Stripe error: {e}")
    return {"checkout_url": pay.checkout_url, "payment_id": str(pay.id)}


# ─── Webhook receiver ───────────────────────────────────────────────

@router.post("/stripe/webhook")
async def stripe_webhook(
    request: Request,
    db: Session = Depends(get_db),
):
    """Stripe POSTs here for every event we subscribed to.
    Signature verification is mandatory.

    Handled events:
      checkout.session.completed  → mark paid, bump Surgery.amount_paid
      charge.refunded             → mark refunded, decrement amount_paid
      payment_intent.payment_failed → mark failed, store failure_reason
      checkout.session.expired    → mark expired (informational)
    """
    payload = await request.body()
    signature = request.headers.get("stripe-signature", "")

    try:
        event = svc.parse_webhook_event(payload, signature)
    except ValueError as e:
        log.warning("stripe webhook signature rejected: %s", e)
        raise HTTPException(status_code=400, detail="bad signature")

    event_id = event.get("id")
    event_type = event.get("type", "")
    obj = (event.get("data") or {}).get("object") or {}

    # Event-level dedup. Stripe redelivers on any non-2xx response and
    # routinely overlaps retries with the original delivery on slow
    # commits; without this, two concurrent _handle_session_completed
    # calls for the same event both pass the per-row "is paid?" guard
    # and both increment Surgery.amount_paid. INSERT into
    # processed_stripe_events first — PK collision rolls everything
    # back and we ack with 200 so Stripe stops retrying.
    # (Fable billing audit H2.)
    if event_id:
        from app.models.stripe_payment import ProcessedStripeEvent
        from sqlalchemy.exc import IntegrityError
        db.add(ProcessedStripeEvent(event_id=event_id, event_type=event_type))
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            log.info("stripe webhook event %s already processed, ack-only",
                     event_id)
            return {"received": True, "deduped": True}

    if event_type == "checkout.session.completed":
        _handle_session_completed(db, event_type, obj)
    elif event_type == "charge.refunded":
        _handle_refund(db, event_type, obj)
    elif event_type == "payment_intent.payment_failed":
        _handle_payment_failed(db, event_type, obj)
    elif event_type == "checkout.session.expired":
        _handle_session_expired(db, event_type, obj)
    else:
        log.info("stripe webhook ignored event %s", event_type)
        db.commit()  # Commit the ProcessedStripeEvent row for unknown events too

    return {"received": True}


# ─── Handlers ───────────────────────────────────────────────────────

def _handle_session_completed(db, event_type, obj):
    session_id = obj.get("id")
    # Delayed payment methods (ACH, klarna, afterpay) fire
    # checkout.session.completed with payment_status='unpaid' before
    # funds settle. Don't credit the surgery until payment_status is
    # 'paid' or 'no_payment_required'. The async_payment_succeeded /
    # async_payment_failed events handle the eventual settlement.
    # (Fable billing audit M1.)
    payment_status = (obj.get("payment_status") or "").lower()
    if payment_status not in ("paid", "no_payment_required"):
        log.info("stripe webhook %s — session %s payment_status=%s, "
                 "skipping until settled", event_type, session_id, payment_status)
        return
    pay = (db.query(SurgeryPayment)
             .filter(SurgeryPayment.stripe_checkout_session_id == session_id)
             .with_for_update()
             .first())
    if not pay:
        log.warning("stripe webhook %s — no SurgeryPayment for session %s",
                    event_type, session_id)
        return
    # Idempotency: Stripe retries on 5xx / timeout. Combined with the
    # event-level dedup above and the row lock here, two concurrent
    # deliveries can't both increment Surgery.amount_paid.
    if pay.status == "paid":
        log.info("stripe webhook %s — payment %s already paid, ignoring",
                 event_type, pay.id)
        return
    before = pay.status
    raw_amount = obj.get("amount_total")
    if raw_amount is None:
        # Defensive: Stripe should always include amount_total on a
        # paid session, but a null here used to raise TypeError → 500
        # → infinite retry loop. (Fable billing audit M6.)
        log.warning("stripe webhook %s — session %s missing amount_total",
                    event_type, session_id)
        return
    amount_paid = Decimal(raw_amount) / Decimal(100)
    # Sanity: warn loudly if Stripe charged a different amount than we
    # requested. Don't block (Stripe is the source of truth), but make
    # the divergence visible. (Fable billing audit M6.)
    if (pay.amount_requested is not None
            and Decimal(pay.amount_requested) != amount_paid):
        log.warning("stripe paid %s != requested %s on payment %s",
                    amount_paid, pay.amount_requested, pay.id)
    pay.amount_paid = amount_paid
    pay.status = "paid"
    pay.paid_at = datetime.utcnow()
    pay.stripe_payment_intent_id = obj.get("payment_intent")
    pay.last_event_payload = obj
    s = (db.query(Surgery)
            .filter(Surgery.id == pay.surgery_id)
            .with_for_update()
            .first())
    if s and pay.kind == "fmla_fee":
        s.fmla_fee_paid = True
        s.fmla_fee_paid_at = datetime.utcnow()
        s.fmla_fee_stripe_session_id = session_id
        has_blank = any(d.kind == "fmla_blank" for d in s.documents)
        if has_blank and not s.fmla_status:
            s.fmla_status = "submitted"
    elif s:
        s.amount_paid = (s.amount_paid or 0) + amount_paid
    db.add(SurgeryPaymentHistory(
        payment_id=pay.id, actor="stripe:webhook",
        event_type=event_type, before_status=before, after_status="paid",
        detail={"amount_paid": str(amount_paid), "kind": pay.kind},
    ))
    db.commit()

    # Receipt template is balance-specific; FMLA fees get no email here.
    if s and s.email and pay.kind == "patient_balance":
        from datetime import date as _date
        surgery_date = (s.scheduled_date.isoformat()
                          if isinstance(s.scheduled_date, _date) else "")
        send_patient_email(
            db,
            kind="stripe_payment_receipt",
            to_email=s.email,
            context={
                "patient_name": s.patient_name,
                "amount":       f"{amount_paid:.2f}",
                "surgery_date": surgery_date,
            },
            sent_by="system:stripe_webhook",
            surgery_id=s.id,
            chart_number=s.chart_number,
        )


def _handle_refund(db, event_type, obj):
    """Apply a charge.refunded webhook.

    Stripe's charge.refunded fires for partial refunds too, and
    `obj.amount_refunded` is the CUMULATIVE refunded amount on the
    charge — not the delta. The previous handler set
    pay.status='refunded' on a $10/$500 partial, then the
    "already refunded" idempotency guard dropped every subsequent
    refund event so the second/third partial refund never reached
    surgery.amount_paid. Patient balance silently stayed
    understated. (Fable billing audit C2.)

    Now: compute the delta between incoming and locally-recorded
    amount_refunded, decrement surgery.amount_paid by that delta,
    record cumulative on pay.amount_refunded. status flips to
    'partially_refunded' until cumulative refund equals amount_paid,
    then 'refunded'. Idempotent on equal cumulative.
    """
    pi = obj.get("payment_intent")
    pay = (db.query(SurgeryPayment)
             .filter(SurgeryPayment.stripe_payment_intent_id == pi)
             .with_for_update()
             .first())
    if not pay:
        log.warning("stripe webhook %s — no SurgeryPayment for PI %s",
                    event_type, pi)
        return

    cumulative_refunded = Decimal(obj.get("amount_refunded", 0)) / Decimal(100)
    already_recorded = Decimal(pay.amount_refunded or 0)

    # Idempotency: same cumulative refunded amount = duplicate delivery.
    if cumulative_refunded <= already_recorded:
        log.info("stripe webhook %s — payment %s cumulative refund %s "
                 "already recorded (have %s), skipping",
                 event_type, pay.id, cumulative_refunded, already_recorded)
        return

    delta = cumulative_refunded - already_recorded
    before = pay.status

    pay.amount_refunded = cumulative_refunded
    pay.last_event_payload = obj
    pay.refunded_at = datetime.utcnow()

    # Final-state status: fully refunded only when cumulative refund
    # equals the original amount_paid. Otherwise it's a partial.
    if cumulative_refunded >= Decimal(pay.amount_paid or 0):
        pay.status = "refunded"
    else:
        pay.status = "partially_refunded"

    s = (db.query(Surgery)
            .filter(Surgery.id == pay.surgery_id)
            .with_for_update()
            .first())
    if s:
        new_balance = (Decimal(s.amount_paid or 0) - delta)
        if new_balance < 0:
            log.warning(
                "stripe refund delta %s would drive surgery %s amount_paid "
                "below zero (was %s) — clamping; investigate accounting drift",
                delta, s.id, s.amount_paid)
            new_balance = Decimal(0)
        s.amount_paid = new_balance

    db.add(SurgeryPaymentHistory(
        payment_id=pay.id, actor="stripe:webhook",
        event_type=event_type, before_status=before, after_status=pay.status,
        detail={"amount_refunded_cumulative": str(cumulative_refunded),
                "delta": str(delta)},
    ))
    db.commit()


def _handle_payment_failed(db, event_type, obj):
    pi = obj.get("id")
    pay = (db.query(SurgeryPayment)
             .filter(SurgeryPayment.stripe_payment_intent_id == pi)
             .first())
    if not pay:
        return
    before = pay.status
    pay.status = "failed"
    pay.failed_at = datetime.utcnow()
    err = obj.get("last_payment_error") or {}
    pay.failure_reason = err.get("message") or err.get("code")
    pay.last_event_payload = obj
    db.add(SurgeryPaymentHistory(
        payment_id=pay.id, actor="stripe:webhook",
        event_type=event_type, before_status=before, after_status="failed",
        detail={"failure_reason": pay.failure_reason},
    ))
    db.commit()


def _handle_session_expired(db, event_type, obj):
    session_id = obj.get("id")
    pay = (db.query(SurgeryPayment)
             .filter(SurgeryPayment.stripe_checkout_session_id == session_id)
             .first())
    if not pay or pay.status != "requested":
        return
    before = pay.status
    pay.status = "expired"
    pay.last_event_payload = obj
    db.add(SurgeryPaymentHistory(
        payment_id=pay.id, actor="stripe:webhook",
        event_type=event_type, before_status=before, after_status="expired",
        detail={},
    ))
    db.commit()


# ─── Helpers ────────────────────────────────────────────────────────

def _outstanding_balance(s: Surgery):
    if s.patient_responsibility is None:
        return None
    return (s.patient_responsibility or 0) - (s.amount_paid or 0)


def _payment_dict(p: SurgeryPayment) -> dict:
    return {
        "id":                str(p.id),
        "status":            p.status,
        "amount_requested":  str(p.amount_requested),
        "amount_paid":       str(p.amount_paid),
        "amount_refunded":   str(p.amount_refunded),
        "currency":          p.currency,
        "description":       p.description,
        "checkout_url":      p.checkout_url,
        "requested_by":      p.requested_by,
        "requested_at":      p.requested_at.isoformat() if p.requested_at else None,
        "paid_at":           p.paid_at.isoformat() if p.paid_at else None,
        "refunded_at":       p.refunded_at.isoformat() if p.refunded_at else None,
        "failed_at":         p.failed_at.isoformat() if p.failed_at else None,
        "failure_reason":    p.failure_reason,
    }
