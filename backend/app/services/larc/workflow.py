"""Shared LARC workflow helpers — milestone catalog, audit log helper,
buckets, and rule constants."""
from __future__ import annotations

from datetime import date as _date, datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.models.larc import (
    LarcAssignment, LarcAuditEvent, LarcCheckout, LarcDevice, LarcMilestone,
)


LOCATIONS = ["arlington", "white_plains", "brandywine"]
LOCATION_LABELS = {
    "arlington":   "Arlington",
    "white_plains": "White Plains",
    "brandywine":  "Brandywine",
}


# Devices within this many days of expiry are pulled off any active
# assignment and moved to unallocated (so we don't risk inserting expired
# product).
DEVICE_EXPIRY_HOLD_DAYS = 365

# Devices assigned to a patient but unused this long are reallocated
# (patient moves to Owed list).
ASSIGNMENT_REALLOCATE_AFTER_DAYS = 180

# Pharmacy-order target SLA — flag for follow-up when device hasn't
# arrived within this window.
PHARMACY_ORDER_SLA_DAYS = 14

# Checkout must be acknowledged within this many hours after the visit
# day; otherwise dashboard flags it.
CHECKOUT_ACK_WINDOW_HOURS = 24


# ── Milestone catalogs ─────────────────────────────────────────────

# In-stock flow (Liletta-style): device is on the shelf, no pharmacy step
IN_STOCK_MILESTONES = [
    ("benefits_verified",            "Benefits verified",                 3),
    ("patient_responsibility_modmed","Patient responsibility in ModMed",  2),
    ("patient_notified",             "Patient notified to schedule",      2),
    ("device_checked_out",           "Device checked out for insertion",  1),
    ("device_inserted",              "Device inserted",                   1),
    ("billed",                       "Insertion billed (claim # recorded)", 14),
]

# Pharmacy-order flow (Mirena/Skyla/Kyleena/Paragard/Nexplanon)
PHARMACY_ORDER_MILESTONES = [
    ("benefits_verified",            "Benefits verified",                 3),
    ("enrollment_sent",              "Enrollment form sent to patient",   2),
    ("enrollment_signed",            "Enrollment form signed",            7),
    ("request_faxed",                "Request faxed to pharmacy",         1),
    ("device_received",              "Device received from pharmacy",    14),
    ("patient_notified",             "Patient notified to schedule",      2),
    ("device_checked_out",           "Device checked out for insertion",  1),
    ("device_inserted",              "Device inserted",                   1),
    ("billed",                       "Insertion billed (claim # recorded)", 14),
]

# Office-procedure flow (NovaSure / Bensta) — single-use disposable
# consumed during a scheduled surgery. No DocuSign, no pharmacy, no
# patient self-service. Assigned at surgery scheduling, consumed at the
# procedure, billed after.
OFFICE_PROCEDURE_MILESTONES = [
    ("benefits_verified",  "Benefits verified",                   3),
    ("device_assigned",    "Device picked from inventory",        1),
    ("device_consumed",    "Device used during procedure",        1),
    ("billed",             "Procedure billed (claim # recorded)", 14),
]


# ── Dashboard buckets ──────────────────────────────────────────────

ALL_BUCKETS = [
    "outstanding",
    "incomplete",
    "new",
    "needs_benefits",
    "needs_enrollment",
    "needs_fax",
    "awaiting_receipt",
    "received_not_notified",
    "checked_out",
    "inserted_not_billed",
    "failed_replacement_unrequested",
    "failed_replacement_pending",
    "checkout_unacknowledged",
    "owed",
    "billed",
    # Office-procedure-specific buckets
    "op_needs_device",
    "op_device_assigned",
    "op_consumed_not_billed",
]


# ── Audit log helper ───────────────────────────────────────────────

def log_audit(
    db: Session, *,
    actor: str,
    action: str,
    device: Optional[LarcDevice] = None,
    assignment: Optional[LarcAssignment] = None,
    checkout: Optional[LarcCheckout] = None,
    detail: Optional[dict] = None,
    summary: Optional[str] = None,
) -> LarcAuditEvent:
    """Single source of truth for writing audit events. Every state
    change must call this. Caller is responsible for db.commit() so
    several mutations can land in a single transaction."""
    chart = None
    pname = None
    if assignment:
        chart = assignment.chart_number
        pname = assignment.patient_name
    event = LarcAuditEvent(
        actor=actor,
        action=action,
        device_id=device.id if device else None,
        assignment_id=assignment.id if assignment else None,
        checkout_id=checkout.id if checkout else None,
        chart_number=chart,
        patient_name=pname,
        detail=detail,
        summary=summary,
    )
    db.add(event)
    return event


# ── Milestone spawning ─────────────────────────────────────────────

def spawn_milestones(db: Session, assignment: LarcAssignment) -> None:
    """Create the milestone catalog for this assignment. Idempotent —
    won't dup if milestones already exist. Catalog chosen by source_flow."""
    if assignment.milestones:
        return
    if assignment.source_flow == "office_procedure":
        catalog = OFFICE_PROCEDURE_MILESTONES
    elif assignment.source_flow == "pharmacy_order":
        catalog = PHARMACY_ORDER_MILESTONES
    else:
        catalog = IN_STOCK_MILESTONES
    # Patient-owned devices are never billed by WWC — mark the billing step
    # N/A. Before a device is bound, a pharmacy_order flow is patient-owned by
    # definition.
    dev = assignment.device
    is_patient_owned = (dev.ownership == "patient_owned") if dev else (assignment.source_flow == "pharmacy_order")

    for pos, (kind, title, days) in enumerate(catalog, 1):
        status = "not_applicable" if (is_patient_owned and kind == "billed") else "pending"
        db.add(LarcMilestone(
            assignment_id=assignment.id,
            kind=kind, title=title, position=pos,
            status=status, expected_duration_days=days,
        ))


def assignment_buckets(a: LarcAssignment, today: Optional[_date] = None) -> set[str]:
    """Compute the workload buckets this assignment is in right now."""
    today = today or _date.today()
    out: set[str] = set()
    if a.status in ("billed", "cancelled"):
        return out
    if not a.is_active:
        out.add("owed")
        return out
    out.add("outstanding")

    by_kind = {m.kind: m for m in (a.milestones or [])}
    def done(kind: str) -> bool:
        m = by_kind.get(kind)
        return m is not None and m.status in ("done", "skipped", "not_applicable")

    if a.status == "incomplete":
        out.add("incomplete")
        return out

    if not done("benefits_verified"):
        out.add("needs_benefits")

    if a.source_flow == "office_procedure":
        # Simpler 4-step flow: benefits → assigned → consumed → billed
        if not done("device_assigned"):
            out.add("op_needs_device")
        elif not done("device_consumed"):
            out.add("op_device_assigned")
        elif not done("billed"):
            out.add("op_consumed_not_billed")
        return out

    if a.source_flow == "pharmacy_order":
        if not done("enrollment_signed"):
            out.add("needs_enrollment")
        if done("enrollment_signed") and not done("request_faxed"):
            out.add("needs_fax")
        if done("request_faxed") and not done("device_received"):
            out.add("awaiting_receipt")
        if done("device_received") and not done("patient_notified"):
            out.add("received_not_notified")

    # Ready-to-checkout / checked-out lane. The appointment-scheduling step
    # was removed, so once the patient has been notified (device on-hand) the
    # assignment is ready for checkout; it stays in this lane until insertion.
    if done("patient_notified") and not done("device_inserted"):
        out.add("checked_out")
    if done("device_inserted") and not done("billed"):
        out.add("inserted_not_billed")

    # Replacement chain
    if a.status == "failed_used":
        # Did the practice request a replacement (= sibling assignment created)?
        if a.replacement_assignment_id:
            out.add("failed_replacement_pending")
        else:
            out.add("failed_replacement_unrequested")

    return out
