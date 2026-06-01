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
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.stripe_payment import (
    StripeCustomer, SurgeryPayment, SurgeryPaymentHistory,
    SURGERY_PAYMENT_STATUSES,
)
from app.models.surgery import Surgery
from app.routers.auth import require_permission
from app.routers.patient_surgery import require_patient_token
from app.services import stripe_payments as svc
from app.services.patient_email import send_patient_email

log = logging.getLogger(__name__)

router = APIRouter(tags=["stripe-payments"])


# ─── Coordinator: request a payment ─────────────────────────────────

class RequestPaymentIn(BaseModel):
    amount: Optional[Decimal] = None
    description: Optional[str] = None


@router.post("/surgery/{surgery_id}/request-payment")
def request_payment(
    surgery_id: str,
    payload: RequestPaymentIn,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_permission("surgery:work")),
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

    return _payment_dict(pay)


# ─── Coordinator: list payments for a surgery ───────────────────────

@router.get("/surgery/{surgery_id}/payments")
def list_payments(
    surgery_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_permission("claim:read")),
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
    amount: Optional[Decimal] = None
    reason: Optional[str] = None


@router.post("/surgery/payments/{payment_id}/refund")
def refund(
    payment_id: str,
    payload: RefundIn,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_permission("user:manage")),
):
    p = db.query(SurgeryPayment).filter(SurgeryPayment.id == payment_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="payment not found")
    if p.status not in ("paid",):
        raise HTTPException(status_code=409,
                            detail=f"can only refund a paid payment (current: {p.status})")
    actor = current_user.get("email") or "system"
    try:
        ref = svc.refund_payment(db, p, amount=payload.amount)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Stripe refund error: {e}")
    db.add(SurgeryPaymentHistory(
        payment_id=p.id, actor=actor,
        event_type="admin.refund_initiated",
        before_status=p.status, after_status=p.status,
        detail={"refund_id": ref.get("id"),
                "amount": str(payload.amount) if payload.amount else "full",
                "reason": payload.reason},
    ))
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

    event_type = event.get("type", "")
    obj = (event.get("data") or {}).get("object") or {}

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

    return {"received": True}


# ─── Handlers ───────────────────────────────────────────────────────

def _handle_session_completed(db, event_type, obj):
    session_id = obj.get("id")
    pay = (db.query(SurgeryPayment)
             .filter(SurgeryPayment.stripe_checkout_session_id == session_id)
             .first())
    if not pay:
        log.warning("stripe webhook %s — no SurgeryPayment for session %s",
                    event_type, session_id)
        return
    before = pay.status
    amount_paid = Decimal(obj.get("amount_total", 0)) / Decimal(100)
    pay.amount_paid = amount_paid
    pay.status = "paid"
    pay.paid_at = datetime.utcnow()
    pay.stripe_payment_intent_id = obj.get("payment_intent")
    pay.last_event_payload = obj
    # Route by payment kind: surgery-balance payments bump amount_paid +
    # send a receipt; FMLA processing fees set the FMLA flags instead.
    s = db.query(Surgery).filter(Surgery.id == pay.surgery_id).first()
    if s and pay.kind == "fmla_fee":
        s.fmla_fee_paid = True
        s.fmla_fee_paid_at = datetime.utcnow()
        s.fmla_fee_stripe_session_id = session_id
        # Auto-flip status if the patient already uploaded the blank form.
        has_blank = any(d.kind == "fmla_blank" for d in (s.documents or []))
        if has_blank and (s.fmla_status or "") in ("", None):
            s.fmla_status = "submitted"
    elif s:
        s.amount_paid = (s.amount_paid or 0) + amount_paid
    db.add(SurgeryPaymentHistory(
        payment_id=pay.id, actor="stripe:webhook",
        event_type=event_type, before_status=before, after_status="paid",
        detail={"amount_paid": str(amount_paid), "kind": pay.kind},
    ))
    db.commit()

    # Auto-send a receipt email — only for patient-balance payments.
    # FMLA fees skip this since the template is balance-specific.
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
    pi = obj.get("payment_intent")
    pay = (db.query(SurgeryPayment)
             .filter(SurgeryPayment.stripe_payment_intent_id == pi)
             .first())
    if not pay:
        log.warning("stripe webhook %s — no SurgeryPayment for PI %s",
                    event_type, pi)
        return
    before = pay.status
    refunded = Decimal(obj.get("amount_refunded", 0)) / Decimal(100)
    pay.amount_refunded = refunded
    pay.status = "refunded"
    pay.refunded_at = datetime.utcnow()
    pay.last_event_payload = obj
    s = db.query(Surgery).filter(Surgery.id == pay.surgery_id).first()
    if s:
        s.amount_paid = max(Decimal(0), (s.amount_paid or 0) - refunded)
    db.add(SurgeryPaymentHistory(
        payment_id=pay.id, actor="stripe:webhook",
        event_type=event_type, before_status=before, after_status="refunded",
        detail={"amount_refunded": str(refunded)},
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
