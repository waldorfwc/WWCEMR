"""Dashboard aggregate metrics.

All figures are derived from existing tables — no schema changes. Date
windows are computed in Python (`date.today() - timedelta(days=N)`) and
passed as parameters so the query is portable between SQLite (tests)
and PostgreSQL (production).
"""
from datetime import date, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.claim import Claim, ClaimStatus
from app.models.payment import Payment, PaymentType
from app.models.denial import Denial, DenialStatus

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

TERMINAL_STATUSES = (
    ClaimStatus.PAID, ClaimStatus.DENIED, ClaimStatus.WRITTEN_OFF,
    ClaimStatus.REVERSED,
)
RESOLVED_STATUSES = (
    ClaimStatus.PAID, ClaimStatus.WRITTEN_OFF, ClaimStatus.REVERSED,
)
INSURANCE_PAYMENT_TYPES = (
    PaymentType.INSURANCE_PAYMENT, PaymentType.PATIENT_PAYMENT,
)
# Timely filing horizon: Medicare is 365 days, many commercial payers are 90.
# "At risk" = within 7 days of a 90-day horizon from date of service.
TIMELY_FILING_HORIZON_DAYS = 90
TIMELY_FILING_ALERT_DAYS = 7


def _days_ago(n: int) -> date:
    return date.today() - timedelta(days=n)


def _collected_in_window(db: Session, start_offset: int, end_offset: int = 0) -> float:
    """Sum payments with payment_date in [today - start_offset, today - end_offset]."""
    start = _days_ago(start_offset)
    end = _days_ago(end_offset) if end_offset else date.today()
    q = db.query(func.coalesce(func.sum(Payment.amount), 0)).filter(
        Payment.payment_type.in_([t.value for t in INSURANCE_PAYMENT_TYPES]),
        Payment.payment_date >= start,
        Payment.payment_date <= end,
    )
    return float(q.scalar() or 0)


def _resolved_window(db: Session, days: int) -> dict:
    """Claims moved to a resolved status within the last `days` days."""
    since = _days_ago(days)
    count = db.query(func.count(Claim.id)).filter(
        Claim.status.in_([s.value for s in RESOLVED_STATUSES]),
        Claim.statement_date >= since,
    ).scalar() or 0
    collected = db.query(func.coalesce(func.sum(Claim.paid_amount), 0)).filter(
        Claim.status.in_([s.value for s in RESOLVED_STATUSES]),
        Claim.statement_date >= since,
    ).scalar() or 0
    return {"count": int(count), "collected": float(collected)}


@router.get("/summary")
def dashboard_summary(db: Session = Depends(get_db)):
    # Collected
    collected_30d = _collected_in_window(db, 30)
    collected_prior_30d = _collected_in_window(db, 60, 31)

    # Outstanding: sum of positive balance on non-terminal claims
    outstanding_q = db.query(
        func.coalesce(func.sum(Claim.balance), 0),
        func.count(Claim.id),
    ).filter(
        Claim.status.notin_([s.value for s in TERMINAL_STATUSES]),
        Claim.balance > 0,
    ).one()
    outstanding_total = float(outstanding_q[0] or 0)
    outstanding_count = int(outstanding_q[1] or 0)

    # Open claims (not terminal)
    open_claims = db.query(func.count(Claim.id)).filter(
        Claim.status.notin_([s.value for s in TERMINAL_STATUSES]),
    ).scalar() or 0

    # Submitted last 7d (using statement_date as submission proxy)
    submitted_7d_since = _days_ago(7)
    claims_submitted_7d = db.query(func.count(Claim.id)).filter(
        Claim.statement_date >= submitted_7d_since,
    ).scalar() or 0

    # Timely filing: un-submitted open claims whose DOS is within
    # TIMELY_FILING_ALERT_DAYS of the horizon.
    horizon_warn = _days_ago(TIMELY_FILING_HORIZON_DAYS - TIMELY_FILING_ALERT_DAYS)
    timely_filing_at_risk = db.query(func.count(Claim.id)).filter(
        Claim.status.notin_([s.value for s in TERMINAL_STATUSES]),
        Claim.date_of_service_from.isnot(None),
        Claim.date_of_service_from <= horizon_warn,
    ).scalar() or 0

    # Denials — Denial.status is an SAEnum of DenialStatus ('open', 'appealing', ...)
    denied_open = db.query(func.count(Denial.id)).filter(
        Denial.status == DenialStatus.OPEN.value,
    ).scalar() or 0
    denied_last_week = db.query(func.count(Denial.id)).filter(
        Denial.status == DenialStatus.OPEN.value,
        Denial.created_at >= _days_ago(7),
    ).scalar() or 0

    return {
        "collected_30d": collected_30d,
        "collected_prior_30d": collected_prior_30d,
        "outstanding_total": outstanding_total,
        "outstanding_count": outstanding_count,
        "open_claims": int(open_claims),
        "claims_submitted_7d": int(claims_submitted_7d),
        "timely_filing_at_risk_7d": int(timely_filing_at_risk),
        "resolved": {
            "30d": _resolved_window(db, 30),
            "60d": _resolved_window(db, 60),
            "90d": _resolved_window(db, 90),
        },
        "denied_open": int(denied_open),
        "denied_delta_7d": int(denied_last_week),
        "attention": {
            "timely_filing": int(timely_filing_at_risk),
            "eras_unposted": 0,
            "fax_failures": 0,
        },
    }
