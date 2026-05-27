"""Surgery scheduling API.

Phase 1: dashboard counts, list (filtered + grouped by milestone),
detail. Block schedule + capacity + boarding slips + Klara drafter
arrive in subsequent phases.
"""
from __future__ import annotations

import os
from datetime import date as _date, datetime, time as _time, timedelta
from typing import Optional

from app.models.surgery import SurgeryFile

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy import or_, func, desc
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models.surgery import (
    Surgery, SurgeryMilestone, BlockSchedule, BlockDay, SurgerySlot,
    SurgeryBlackoutDay, SurgeryWaitlist,
)
from app.routers.auth import get_current_user, require_permission

router = APIRouter(prefix="/surgery", tags=["surgery"])


# ─── Behind-schedule helper ──────────────────────────────────────────

def _current_milestone(s: Surgery) -> Optional[SurgeryMilestone]:
    """First non-done, non-skipped milestone, ordered by position."""
    pending = [m for m in (s.milestones or [])
               if m.status not in ("done", "skipped", "not_applicable")]
    pending.sort(key=lambda m: m.position)
    return pending[0] if pending else None


def _milestone_age_days(m: SurgeryMilestone, ref: Optional[_date] = None) -> int:
    """Days since this milestone became eligible (started, or surgery's
    last update if not started)."""
    ref = ref or _date.today()
    base = m.started_at or m.surgery.updated_at or m.surgery.created_at
    if not base:
        return 0
    base_date = base.date() if hasattr(base, 'date') else base
    return max(0, (ref - base_date).days)


def _is_behind(s: Surgery, hours: int = 0) -> tuple[bool, int]:
    """Returns (is_behind, hours_overdue) for a surgery's current milestone."""
    m = _current_milestone(s)
    if not m or not m.expected_duration_days:
        return False, 0
    age_days = _milestone_age_days(m)
    overdue_days = age_days - m.expected_duration_days
    if overdue_days <= 0:
        return False, 0
    overdue_hours = overdue_days * 24
    return overdue_hours > hours, overdue_hours


# ─── Serializer ─────────────────────────────────────────────────────

def _surgery_dict(s: Surgery, *, include_milestones: bool = False,
                   today: Optional[_date] = None) -> dict:
    behind, hours_overdue = _is_behind(s)
    cur_m = _current_milestone(s)
    out = {
        "id": str(s.id),
        "surgery_number": s.surgery_number,
        "chart_number": s.chart_number,
        "patient_name": s.patient_name,
        "dob": str(s.dob) if s.dob else None,
        "age": _patient_age(s.dob),
        "phone": s.cell_phone or s.phone,
        "email": s.email,
        "address_street": s.address_street,
        "address_city": s.address_city,
        "address_state": s.address_state,
        "address_zip": s.address_zip,
        "primary_insurance": s.primary_insurance,
        "primary_member_id": s.primary_member_id,
        "surgeon_primary": s.surgeon_primary,
        "surgeon_secondary": s.surgeon_secondary,
        "procedures": s.procedures,
        "diagnoses": s.diagnoses,
        "is_robotic": bool(s.is_robotic),
        "procedure_classification": s.procedure_classification,
        "estimated_minutes": s.estimated_minutes,
        "eligible_facilities": s.eligible_facilities or [],
        "selected_facility": s.selected_facility,
        "scheduled_date": str(s.scheduled_date) if s.scheduled_date else None,
        "scheduled_start_time": str(s.scheduled_start_time) if s.scheduled_start_time else None,
        "reschedule_count": s.reschedule_count or 0,
        "last_rescheduled_at": s.last_rescheduled_at.isoformat() if s.last_rescheduled_at else None,
        "last_rescheduled_by": s.last_rescheduled_by,
        "scheduled_in_modmed_at": s.scheduled_in_modmed_at.isoformat() if s.scheduled_in_modmed_at else None,
        "scheduled_in_modmed_by": s.scheduled_in_modmed_by,
        "office_meds_pickup_confirmed_at": (
            s.office_meds_pickup_confirmed_at.isoformat()
            if s.office_meds_pickup_confirmed_at else None),
        "office_meds_pickup_confirmed_by": s.office_meds_pickup_confirmed_by,
        "preop_date": str(s.preop_date) if s.preop_date else None,
        "preop_needs_repeat": _preop_needs_repeat(s),
        "post_op_appt_date": str(s.post_op_appt_date) if s.post_op_appt_date else None,
        "post_op_appt_2nd_date": (
            str(s.post_op_appt_2nd_date) if s.post_op_appt_2nd_date else None),
        "post_op_schedule_required": _post_op_visits_serialized(s),
        "auth_status": s.auth_status,
        "auth_number": s.auth_number,
        "clearance_required": bool(s.clearance_required),
        "clearance_status": s.clearance_status,
        "consent_status": s.consent_status,
        "consent_doc_id": s.consent_doc_id,
        "consent_sent_at": s.consent_sent_at.isoformat() if s.consent_sent_at else None,
        "consent_signed_at": s.consent_signed_at.isoformat() if s.consent_signed_at else None,
        "assistant_surgeon_required": bool(s.assistant_surgeon_required),
        "assistant_surgeon_name": s.assistant_surgeon_name,
        "assistant_surgeon_office_phone": s.assistant_surgeon_office_phone,
        "assistant_surgeon_office_fax": s.assistant_surgeon_office_fax,
        "assistant_surgeon_office_notified_at": (
            s.assistant_surgeon_office_notified_at.isoformat()
            if s.assistant_surgeon_office_notified_at else None),
        "assistant_surgeon_office_notified_by": s.assistant_surgeon_office_notified_by,
        "assistant_surgeon_appt_date": (
            str(s.assistant_surgeon_appt_date) if s.assistant_surgeon_appt_date else None),
        "assistant_surgeon_appt_confirmed_at": (
            s.assistant_surgeon_appt_confirmed_at.isoformat()
            if s.assistant_surgeon_appt_confirmed_at else None),
        "assistant_surgeon_appt_confirmed_by": s.assistant_surgeon_appt_confirmed_by,
        "modmed_claim_number": s.modmed_claim_number,
        "billed_icd10_codes": s.billed_icd10_codes or [],
        "billed_cpt_codes": s.billed_cpt_codes or [],
        "billed_at": s.billed_at.isoformat() if s.billed_at else None,
        "billed_by": s.billed_by,
        "billing_ai_notes": s.billing_ai_notes,
        "consent_envelopes": [
            {
                "id": str(e.id),
                "template_id": str(e.template_id),
                "template_name": e.template.name if e.template else None,
                "is_supplemental": bool(e.template.is_supplemental) if e.template else False,
                "envelope_id": e.docusign_envelope_id,
                "status": e.status,
                "sent_at": e.sent_at.isoformat() if e.sent_at else None,
                "signed_at": e.signed_at.isoformat() if e.signed_at else None,
                "declined_at": e.declined_at.isoformat() if e.declined_at else None,
                "voided_at": e.voided_at.isoformat() if e.voided_at else None,
                "last_error": e.last_error,
            } for e in (s.consent_envelopes or [])
        ],
        "patient_responsibility": (str(s.patient_responsibility)
                                    if s.patient_responsibility is not None else None),
        "amount_paid": str(s.amount_paid) if s.amount_paid is not None else "0",
        "deductible": (str(s.deductible) if s.deductible is not None else None),
        "deductible_met": (str(s.deductible_met) if s.deductible_met is not None else None),
        "copay": (str(s.copay) if s.copay is not None else None),
        "coinsurance_pct": (str(s.coinsurance_pct) if s.coinsurance_pct is not None else None),
        "oop_max": (str(s.oop_max) if s.oop_max is not None else None),
        "oop_met": (str(s.oop_met) if s.oop_met is not None else None),
        "allowed_amount": (str(s.allowed_amount) if s.allowed_amount is not None else None),
        "status": s.status,
        "sub_flag": s.sub_flag,
        "is_urgent": bool(s.is_urgent),
        "current_milestone": cur_m.kind if cur_m else None,
        "current_milestone_title": cur_m.title if cur_m else None,
        "behind_schedule": behind,
        "hours_overdue": hours_overdue,
        "stuck": s.status == "in_progress" and behind,
        "buckets": sorted(_surgery_buckets(s, today)) if s.milestones is not None else [],
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
    }
    if include_milestones:
        out["milestones"] = [
            {
                "id": str(m.id),
                "kind": m.kind,
                "title": m.title,
                "position": m.position,
                "status": m.status,
                "started_at": m.started_at.isoformat() if m.started_at else None,
                "completed_at": m.completed_at.isoformat() if m.completed_at else None,
                "completed_by": m.completed_by,
                "notes": m.notes,
                "expected_duration_days": m.expected_duration_days,
                "data": m.data_json,
            }
            for m in (s.milestones or [])
        ]
    return out


# ─── Dashboard ──────────────────────────────────────────────────────

@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db),
              current_user: dict = Depends(require_permission("surgery:read"))):
    today = _date.today()
    thirty_days_ago = today - timedelta(days=30)

    n_completed_30d = db.query(func.count(Surgery.id)).filter(
        Surgery.status == "completed",
        Surgery.updated_at >= thirty_days_ago,
    ).scalar() or 0

    # Walk all non-terminal surgeries once, compute buckets in Python
    rows = (db.query(Surgery)
              .options(joinedload(Surgery.milestones))
              .filter(Surgery.status.in_(["new", "in_progress", "hold",
                                            "confirmed", "incomplete"]))
              .all())

    bucket_counts: dict[str, int] = {b: 0 for b in ALL_BUCKETS}
    critical = []
    todo = []
    stuck_count = 0
    for s in rows:
        for b in _surgery_buckets(s, today):
            bucket_counts[b] = bucket_counts.get(b, 0) + 1

        behind, hrs = _is_behind(s)
        if behind:
            stuck_count += 1
            cur_m = _current_milestone(s)
            item = {
                "surgery_id": str(s.id),
                "patient_name": s.patient_name,
                "chart_number": s.chart_number,
                "milestone": cur_m.title if cur_m else None,
                "hours_overdue": hrs,
            }
            if hrs > 48:
                critical.append(item)
            else:
                todo.append(item)

    critical.sort(key=lambda x: -x["hours_overdue"])
    todo.sort(key=lambda x: -x["hours_overdue"])

    # Next available date per facility — first BlockDay >= today where a
    # representative procedure for that facility still fits per capacity
    # rules. We probe with the typical case for each facility:
    #   medstar : robotic_180  (most common; if a 180 doesn't fit, scheduler
    #                            usually adds a CRMC/office case instead)
    #   crmc    : minor        (lowest bar — 6 slots per day)
    #   office  : office       (60-min default)
    from app.services.surgery_block_schedule import can_fit, DURATIONS
    FACILITY_PROBE = {"medstar": "robotic_180", "crmc": "minor", "office": "office"}
    next_slots: dict = {"medstar": None, "crmc": None, "office": None}
    upcoming = (db.query(BlockDay)
                  .options(joinedload(BlockDay.slots))
                  .filter(BlockDay.block_date >= today)
                  .order_by(BlockDay.block_date)
                  .limit(180).all())
    for bd in upcoming:
        if next_slots.get(bd.facility) is not None:
            continue
        probe_kind = FACILITY_PROBE.get(bd.facility)
        if not probe_kind:
            continue
        ok, _reason = can_fit(db, bd, probe_kind)
        if not ok:
            continue
        # Compute booked count for at-a-glance UX
        booked = len(bd.slots or [])
        next_slots[bd.facility] = {
            "block_day_id": str(bd.id),
            "block_date": str(bd.block_date),
            "weekday": bd.block_date.strftime("%A"),
            "block_window": f"{bd.start_time.strftime('%H:%M')}–{bd.end_time.strftime('%H:%M')}",
            "cases_already_booked": booked,
            "probe_kind": probe_kind,
        }

    # Release-alert flags — surface unbooked hospital days + under-booked
    # office days inline on the dashboard so the scheduler sees them
    # without waiting for the daily email.
    from app.services.surgery_release_alerts import (
        find_hospital_release_candidates, find_office_release_candidates,
    )
    hospital_unbooked = [
        {
            "block_day_id": str(bd.id),
            "facility": bd.facility,
            "block_date": str(bd.block_date),
            "hours": f"{bd.start_time.strftime('%H:%M')}-{bd.end_time.strftime('%H:%M')}",
            "alerted": bd.release_alert_sent_at is not None,
        }
        for bd in find_hospital_release_candidates(db)
    ]
    office_underbooked = [
        {
            "block_day_id": str(bd.id),
            "block_date": str(bd.block_date),
            "booked": len(bd.slots or []),
            "needed": 6,
            "alerted": bd.release_alert_sent_at is not None,
        }
        for bd in find_office_release_candidates(db)
    ]

    return {
        "buckets": bucket_counts,
        "completed_30d": n_completed_30d,
        "stuck_count": stuck_count,
        "critical_alerts": critical[:10],
        "todo": todo[:20],
        "next_slots": next_slots,
        "hospital_unbooked": hospital_unbooked,
        "office_underbooked": office_underbooked,
    }


# ─── Dashboard buckets (Phase 2.7) ─────────────────────────────────
# Each surgery can belong to multiple buckets simultaneously — these
# are workload counters, not a state machine. Tiles on the dashboard
# show the count per bucket; clicking a tile filters the list view.

ALL_BUCKETS = [
    "outstanding",
    "incomplete",
    "needs_benefits",
    "needs_prior_auth",
    "needs_sched_msg",
    "unresponsive",
    "date_picked",
    "needs_consent",
    "needs_clearance",
    "needs_assistant_surgeon",
    "needs_labs",
    "needs_repeat_preop",
    "needs_followup_appt",
    "needs_post_op_call",
    "needs_post_op_docs",
    "needs_billed",
]


# Days since klara_scheduling was completed before a no-date-picked surgery
# is flagged as unresponsive
UNRESPONSIVE_AFTER_DAYS = 7

# Pre-op exams/labs are good for 180 days before surgery. After that they
# must be repeated.
PREOP_VALID_DAYS = 180


def _patient_age(dob: Optional[_date], today: Optional[_date] = None) -> Optional[int]:
    """Calculated age in years on `today`. Returns None if DOB missing."""
    if not dob:
        return None
    today = today or _date.today()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def _preop_needs_repeat(s: Surgery) -> bool:
    """True if the pre-op visit is too old (>180 days from surgery date)."""
    if not s.preop_date or not s.scheduled_date:
        return False
    return (s.scheduled_date - s.preop_date).days > PREOP_VALID_DAYS


def _post_op_visits_serialized(s: Surgery) -> list[dict]:
    """List of post-op visits the practice rules say this surgery needs.
    Pure-data — the frontend uses it to render the right number of date
    inputs on the post-op-appts milestone card."""
    from app.services.post_op_schedule import determine_post_op_schedule
    return [
        {"label": v.label, "days_post_op": v.days_post_op}
        for v in determine_post_op_schedule(s)
    ]


def _assistant_surgeon_outstanding(s: Surgery) -> bool:
    """True when an assistant surgeon is required AND either their office
    has not been notified yet OR the patient's appointment with them has
    not been confirmed scheduled. Drives the needs_assistant_surgeon bucket
    and gates the assistant_surgeon milestone auto-complete."""
    if not s.assistant_surgeon_required:
        return False
    return (s.assistant_surgeon_office_notified_at is None
            or s.assistant_surgeon_appt_confirmed_at is None)


def _surgery_buckets(s: Surgery, today: Optional[_date] = None) -> set[str]:
    """Return the set of dashboard buckets this surgery currently belongs to."""
    today = today or _date.today()
    buckets: set[str] = set()

    # Terminal states are out of scope for every bucket
    if s.status in ("cancelled", "completed", "unresponsive"):
        return buckets

    buckets.add("outstanding")

    if s.status == "incomplete":
        buckets.add("incomplete")
        # Incomplete surgeries don't have milestones — stop here
        return buckets

    by_kind = {m.kind: m for m in (s.milestones or [])}

    def is_done(kind: str) -> bool:
        m = by_kind.get(kind)
        return m is not None and m.status in ("done", "skipped", "not_applicable")

    has_date = s.scheduled_date is not None
    days_until = (s.scheduled_date - today).days if has_date else None
    is_hospital = s.selected_facility in ("medstar", "crmc")

    if not is_done("benefits_determined"):
        buckets.add("needs_benefits")
    if not is_done("prior_auth"):
        buckets.add("needs_prior_auth")
    if not is_done("klara_scheduling"):
        buckets.add("needs_sched_msg")
    if _assistant_surgeon_outstanding(s):
        buckets.add("needs_assistant_surgeon")

    # Unresponsive: Klara scheduling was sent but the patient hasn't picked
    # a date in the past 7+ days. Helps schedulers triage stalled outreach.
    if not has_date and is_done("klara_scheduling"):
        klara = by_kind.get("klara_scheduling")
        sent_at = klara.completed_at if klara else None
        if sent_at and (today - sent_at.date()).days >= UNRESPONSIVE_AFTER_DAYS:
            buckets.add("unresponsive")

    if has_date:
        buckets.add("date_picked")
        if not is_done("consent"):
            buckets.add("needs_consent")
        if s.clearance_required and s.clearance_status not in (
                "received", "sent_to_hospital", "completed"):
            buckets.add("needs_clearance")
        from app.services.post_op_schedule import all_required_appts_filled
        if not all_required_appts_filled(s):
            buckets.add("needs_followup_appt")
        # Pre-op stale: exam/labs older than 180 days from surgery date
        if _preop_needs_repeat(s):
            buckets.add("needs_repeat_preop")

        # Labs alert: hospital surgery, within 7 days, labs not yet sent
        if (is_hospital and days_until is not None
                and 0 <= days_until <= 7
                and not s.labs_sent_to_hospital):
            buckets.add("needs_labs")

        # Post-op rules — surgery is in the past
        if days_until is not None and days_until < 0:
            days_since = -days_until
            # Spoke-to-pt is the only "done" state for the post-op call
            if (s.post_op_call_status or "") != "Spoke to Pt.":
                buckets.add("needs_post_op_call")
            if (days_since >= 5
                    and s.operative_report_status not in (
                        "completed", "received", "not_required")):
                buckets.add("needs_post_op_docs")
            if (s.operative_report_status in ("completed", "received")
                    and not s.payment_posted_to_billing):
                buckets.add("needs_billed")

    return buckets


# ─── Calendar (32-day pre-op readiness view) ───────────────────────

# Milestone kinds that must be done before surgery day. Anything past
# `patient_picks_date` is post-op and doesn't gate readiness.
PRE_OP_MILESTONES = {
    "benefits_determined", "prior_auth", "klara_scheduling",
    "patient_picks_date", "device_assigned", "consent",
    "surgery_confirmed_hospital", "labs_to_hospital",
}


def _readiness_indicator(s: Surgery) -> tuple[str, list[str], int]:
    """Returns (color, open_milestone_titles, critical_count).

    color is 'green' | 'yellow' | 'red':
      green   — every pre-op milestone is done / N-A / skipped
      yellow  — at least one pre-op milestone is still open (pending or in_progress)
      red     — at least one pre-op milestone is overdue by >48h
    """
    open_titles: list[str] = []
    critical = 0
    for m in (s.milestones or []):
        if m.kind not in PRE_OP_MILESTONES:
            continue
        if m.status in ("done", "skipped", "not_applicable"):
            continue
        open_titles.append(m.title)
        if m.expected_duration_days:
            age = _milestone_age_days(m)
            if age - m.expected_duration_days > 2:    # >48h late
                critical += 1
    if not open_titles:
        return "green", [], 0
    if critical > 0:
        return "red", open_titles, critical
    return "yellow", open_titles, 0


@router.get("/calendar")
def calendar(
    days: int = Query(7, ge=1, le=365),
    start_date: Optional[str] = Query(None,
        description="YYYY-MM-DD; if omitted defaults to today"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_permission("surgery:read")),
):
    """Surgeries scheduled in a `days`-long window starting at `start_date`
    (defaults to today). The frontend uses days=7 for one-week pagination."""
    if start_date:
        try:
            start = datetime.strptime(start_date[:10], "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=422, detail="start_date must be YYYY-MM-DD")
    else:
        start = _date.today()
    end = start + timedelta(days=days - 1)   # inclusive end
    rows = (db.query(Surgery)
              .options(joinedload(Surgery.milestones))
              .filter(Surgery.scheduled_date >= start,
                      Surgery.scheduled_date <= end,
                      # 'incomplete' included so Calendly-imported stubs and
                      # other patients still being fleshed out still appear
                      Surgery.status.in_(["new", "in_progress", "confirmed", "incomplete"]))
              .order_by(Surgery.scheduled_date,
                        Surgery.scheduled_start_time.asc().nullslast(),
                        Surgery.patient_name)
              .all())

    out = []
    for s in rows:
        color, open_titles, critical = _readiness_indicator(s)
        primary_proc = ""
        procs = s.procedures or []
        if procs:
            primary_proc = procs[0].get("description") or ""
        out.append({
            "id": str(s.id),
            "patient_name": s.patient_name,
            "chart_number": s.chart_number,
            "scheduled_date": str(s.scheduled_date),
            "scheduled_start_time": (str(s.scheduled_start_time)[:5]
                                      if s.scheduled_start_time else None),
            "facility": s.selected_facility,
            "is_robotic": bool(s.is_robotic),
            "is_urgent": bool(s.is_urgent),
            "is_incomplete": s.status == "incomplete",
            "procedure": primary_proc,
            "estimated_minutes": s.estimated_minutes,
            "indicator": color,
            "open_milestones": open_titles,
            "critical_count": critical,
            "sub_flag": s.sub_flag,
        })

    return {
        "today": str(_date.today()),
        "start": str(start),
        "end": str(end),
        "days": days,
        "surgeries": out,
    }


# ─── List ───────────────────────────────────────────────────────────

VALID_GROUPINGS = ["milestone", "status", "facility"]


@router.get("")
def list_surgeries(
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_permission("surgery:read")),
    search: Optional[str] = None,
    status: Optional[str] = None,
    facility: Optional[str] = None,
    milestone: Optional[str] = None,
    bucket: Optional[str] = None,
    behind_only: bool = False,
    urgent_only: bool = False,
    # Phase 3 — expanded filters
    procedure_classification: Optional[str] = None,   # robotic_180 | robotic_240 | minor | major | office
    surgeon: Optional[str] = None,                    # substring of surgeon_primary
    primary_insurance: Optional[str] = None,          # substring of primary_insurance
    is_robotic: Optional[bool] = None,
    has_date: Optional[bool] = None,                  # True = scheduled_date set
    date_from: Optional[str] = None,                  # YYYY-MM-DD, scheduled_date >=
    date_to: Optional[str] = None,                    # YYYY-MM-DD, scheduled_date <=
    reschedule_count_min: Optional[int] = None,       # patients rescheduled N+ times
    preop_needs_repeat: Optional[bool] = None,        # pre-op >180d before surgery
    clearance_required: Optional[bool] = None,
    auth_status: Optional[str] = None,                # e.g. 'denied', 'peer_review'
    age_min: Optional[int] = None,
    age_max: Optional[int] = None,
    page: int = 1,
    per_page: int = 100,
):
    q = db.query(Surgery).options(joinedload(Surgery.milestones))
    if status and status != "all":
        q = q.filter(Surgery.status == status)
    if facility:
        q = q.filter(Surgery.selected_facility == facility)
    if urgent_only:
        q = q.filter(Surgery.is_urgent.is_(True))
    if search:
        like = f"%{search}%"
        q = q.filter(or_(
            Surgery.patient_name.ilike(like),
            Surgery.chart_number.ilike(like),
            Surgery.surgery_number.ilike(like),
        ))
    if procedure_classification:
        q = q.filter(Surgery.procedure_classification == procedure_classification)
    if surgeon:
        q = q.filter(Surgery.surgeon_primary.ilike(f"%{surgeon}%"))
    if primary_insurance:
        q = q.filter(Surgery.primary_insurance.ilike(f"%{primary_insurance}%"))
    if is_robotic is not None:
        q = q.filter(Surgery.is_robotic.is_(is_robotic))
    if has_date is True:
        q = q.filter(Surgery.scheduled_date.isnot(None))
    elif has_date is False:
        q = q.filter(Surgery.scheduled_date.is_(None))
    if date_from:
        try:
            df = datetime.strptime(date_from[:10], "%Y-%m-%d").date()
            q = q.filter(Surgery.scheduled_date >= df)
        except ValueError:
            raise HTTPException(status_code=422, detail="date_from must be YYYY-MM-DD")
    if date_to:
        try:
            dt = datetime.strptime(date_to[:10], "%Y-%m-%d").date()
            q = q.filter(Surgery.scheduled_date <= dt)
        except ValueError:
            raise HTTPException(status_code=422, detail="date_to must be YYYY-MM-DD")
    if reschedule_count_min is not None and reschedule_count_min > 0:
        q = q.filter(Surgery.reschedule_count >= reschedule_count_min)
    if clearance_required is not None:
        q = q.filter(Surgery.clearance_required.is_(clearance_required))
    if auth_status:
        q = q.filter(Surgery.auth_status == auth_status)

    rows = q.all()

    # Post-fetch filters (depend on computed values)
    if milestone:
        rows = [s for s in rows
                if _current_milestone(s) and _current_milestone(s).kind == milestone]
    if bucket:
        if bucket not in ALL_BUCKETS:
            raise HTTPException(status_code=422, detail=f"unknown bucket: {bucket}")
        today = _date.today()
        rows = [s for s in rows if bucket in _surgery_buckets(s, today)]
    if behind_only:
        rows = [s for s in rows if _is_behind(s)[0]]
    if preop_needs_repeat is not None:
        rows = [s for s in rows if _preop_needs_repeat(s) == preop_needs_repeat]
    if age_min is not None:
        rows = [s for s in rows
                if _patient_age(s.dob) is not None and _patient_age(s.dob) >= age_min]
    if age_max is not None:
        rows = [s for s in rows
                if _patient_age(s.dob) is not None and _patient_age(s.dob) <= age_max]

    # Sort: urgent first, then most behind, then by created_at desc
    rows.sort(key=lambda s: (
        0 if s.is_urgent else 1,
        -_is_behind(s)[1],
        -(s.created_at.timestamp() if s.created_at else 0),
    ))

    total = len(rows)
    paged = rows[(page - 1) * per_page : page * per_page]

    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "surgeries": [_surgery_dict(s) for s in paged],
    }


# ─── Upload + parse a new surgery order ─────────────────────────────

@router.post("/orders/upload", status_code=201)
async def upload_order(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_permission("surgery:work")),
):
    """Upload a ModMed surgery-order PDF. Parses it via Claude, creates a
    Surgery row with status='incomplete' so the scheduler can review the
    extracted fields before flipping to 'new' and generating milestones."""
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=422, detail="Expected a PDF file")

    import os
    from app.services.surgery_order_parser import parse_order_text, build_surgery_kwargs, extract_pdf_text

    # Save the upload to disk so we can reference it later
    uploads_dir = "/Users/wwcclaudecode/Documents/wwc-era-project/backend/uploads/surgery_orders"
    os.makedirs(uploads_dir, exist_ok=True)
    contents = await file.read()
    safe_name = f"{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}-{file.filename}"
    save_path = os.path.join(uploads_dir, safe_name)
    with open(save_path, "wb") as f:
        f.write(contents)

    # Parse with Claude
    try:
        # Re-extract from disk so future re-parses can hit the same content
        text = extract_pdf_text(save_path)
        if len(text) < 50:
            raise ValueError("PDF text content is empty — is this a scanned image?")
        parsed = parse_order_text(text)
        kwargs = build_surgery_kwargs(parsed)
    except Exception as exc:
        raise HTTPException(status_code=422,
                            detail=f"Could not parse this PDF: {exc}. "
                                   "Try manually creating the surgery instead.")

    # Sanity-check minimum required fields
    if not kwargs.get("chart_number") or not kwargs.get("patient_name"):
        raise HTTPException(status_code=422,
                            detail="Parser couldn't extract patient identity. "
                                   "Try manually creating the surgery instead.")

    # Avoid duplicates: same chart + same procedures + status not cancelled
    existing = (db.query(Surgery)
                  .filter(Surgery.chart_number == kwargs["chart_number"],
                          Surgery.status.notin_(["cancelled", "completed"]))
                  .first())
    if existing:
        # Don't auto-merge; surface the existing row so the scheduler decides
        return {
            "duplicate": True,
            "existing_id": str(existing.id),
            "existing_status": existing.status,
            "extracted": kwargs,
            "message": (f"Patient {kwargs['patient_name']} (chart {kwargs['chart_number']}) "
                        f"already has an open surgery (#{existing.surgery_number or existing.id}). "
                        "Open it to add this order, or confirm to create a new one."),
        }

    s = Surgery(
        **kwargs,
        order_pdf_path=save_path,
        created_by=current_user.get("email"),
    )
    db.add(s); db.commit(); db.refresh(s)
    return {
        "duplicate": False,
        "id": str(s.id),
        "status": s.status,
        "extracted": kwargs,
        "message": (f"Created surgery for {s.patient_name} in 'incomplete' status. "
                    "Review the extracted fields, fill any gaps, then mark as 'new'."),
    }


# ─── Manual create (no PDF) ─────────────────────────────────────────

class ManualSurgeryIn(BaseModel):
    chart_number: str
    patient_name: str           # "Last, First"
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    dob: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    primary_insurance: Optional[str] = None
    primary_member_id: Optional[str] = None
    secondary_insurance: Optional[str] = None
    secondary_member_id: Optional[str] = None
    surgeon_primary: Optional[str] = None
    procedures: list[dict] = []  # [{cpt, description}]
    diagnoses: list[dict] = []
    eligible_facilities: list[str] = []
    estimated_minutes: Optional[int] = None
    is_robotic: bool = False
    is_urgent: bool = False
    notes: Optional[str] = None


@router.post("/manual", status_code=201)
def create_manual(payload: ManualSurgeryIn,
                   db: Session = Depends(get_db),
                   current_user: dict = Depends(require_permission("surgery:work"))):
    """Create a surgery row from manual data entry. Used when no PDF
    order is available (e.g. patient was already in the schedule from
    ModMed but never had an order generated)."""
    eligible = [f for f in payload.eligible_facilities
                if f in ("medstar", "crmc", "office")]
    if payload.is_robotic and "medstar" not in eligible:
        eligible = ["medstar"]
    selected = eligible[0] if len(eligible) == 1 else None

    # Procedure classification
    cpts = {(p.get("cpt") or "").strip() for p in payload.procedures if p.get("cpt")}
    ROBOTIC = {"58545", "58571", "58572", "58573", "58574", "58575"}
    MAJOR   = {"49320", "58146", "58660", "58662", "58550", "58552", "58553", "58554"}
    if payload.is_robotic or (cpts & ROBOTIC):
        classification = "robotic_240" if (payload.estimated_minutes or 0) >= 240 else "robotic_180"
    elif cpts & MAJOR:
        classification = "major"
    elif selected == "office":
        classification = "office"
    else:
        classification = "minor"

    dob = None
    if payload.dob:
        try:
            dob = datetime.strptime(payload.dob[:10], "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=422, detail="dob must be YYYY-MM-DD")

    s = Surgery(
        chart_number=payload.chart_number.strip(),
        patient_name=payload.patient_name.strip(),
        first_name=payload.first_name,
        last_name=payload.last_name,
        dob=dob,
        phone=payload.phone,
        cell_phone=payload.phone,
        email=payload.email,
        primary_insurance=payload.primary_insurance,
        primary_member_id=payload.primary_member_id,
        secondary_insurance=payload.secondary_insurance,
        secondary_member_id=payload.secondary_member_id,
        surgeon_primary=payload.surgeon_primary,
        procedures=payload.procedures or [],
        diagnoses=payload.diagnoses or [],
        eligible_facilities=eligible,
        selected_facility=selected,
        estimated_minutes=payload.estimated_minutes,
        is_robotic=payload.is_robotic,
        procedure_classification=classification,
        is_urgent=payload.is_urgent,
        notes=payload.notes,
        status="incomplete",
        source="manual",
        created_by=current_user.get("email"),
    )
    db.add(s); db.commit(); db.refresh(s)
    return _surgery_dict(s, include_milestones=True)


# ─── Picklists (must be declared BEFORE /{surgery_id} so it isn't eaten as a path-param) ─

@router.get("/picklists")
def get_picklists(current_user: dict = Depends(require_permission("surgery:read"))):
    """Return the curated dropdown options for SurgeryDetail editing."""
    from app.services.surgery_picklists import all_picklists
    return all_picklists()


# ─── Detail + edit + milestone advance ──────────────────────────────

@router.get("/{surgery_id}")
def get_surgery(surgery_id: str, db: Session = Depends(get_db),
                 current_user: dict = Depends(require_permission("surgery:read"))):
    s = (db.query(Surgery)
           .options(joinedload(Surgery.milestones))
           .filter(Surgery.id == surgery_id)
           .first())
    if not s:
        raise HTTPException(status_code=404, detail="surgery not found")
    return _surgery_dict(s, include_milestones=True)


class SurgeryPatch(BaseModel):
    """Fields a scheduler can edit on a surgery row directly."""
    chart_number: Optional[str] = None
    patient_name: Optional[str] = None
    dob: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address_street: Optional[str] = None
    address_city: Optional[str] = None
    address_state: Optional[str] = None
    address_zip: Optional[str] = None
    primary_insurance: Optional[str] = None
    primary_member_id: Optional[str] = None
    primary_group: Optional[str] = None
    secondary_insurance: Optional[str] = None
    surgeon_primary: Optional[str] = None
    surgeon_secondary: Optional[str] = None
    procedures: Optional[list[dict]] = None
    diagnoses: Optional[list[dict]] = None
    is_robotic: Optional[bool] = None
    estimated_minutes: Optional[int] = None
    procedure_classification: Optional[str] = None
    eligible_facilities: Optional[list[str]] = None
    selected_facility: Optional[str] = None
    auth_status: Optional[str] = None
    auth_number: Optional[str] = None
    clearance_required: Optional[bool] = None
    clearance_status: Optional[str] = None
    cardiologist_name: Optional[str] = None
    cardiologist_phone: Optional[str] = None
    cardiologist_fax: Optional[str] = None
    sterilization_consent_required: Optional[bool] = None
    sterilization_consent_status: Optional[str] = None
    deductible: Optional[float] = None
    copay: Optional[float] = None
    allowed_amount: Optional[float] = None
    patient_responsibility: Optional[float] = None
    amount_paid: Optional[float] = None
    is_urgent: Optional[bool] = None
    notes: Optional[str] = None
    latest_comment: Optional[str] = None
    escalate_to_email: Optional[str] = None
    # Scheduling overrides
    scheduled_date: Optional[str] = None
    scheduled_start_time: Optional[str] = None
    # Assistant surgeon
    assistant_surgeon_required: Optional[bool] = None
    assistant_surgeon_name: Optional[str] = None
    assistant_surgeon_office_phone: Optional[str] = None
    assistant_surgeon_office_fax: Optional[str] = None
    assistant_surgeon_appt_date: Optional[str] = None
    # Billing
    modmed_claim_number: Optional[str] = None
    # Status transitions
    status: Optional[str] = None
    sub_flag: Optional[str] = None


@router.patch("/{surgery_id}")
def patch_surgery(surgery_id: str, payload: SurgeryPatch,
                   db: Session = Depends(get_db),
                   current_user: dict = Depends(require_permission("surgery:work"))):
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="surgery not found")

    data = payload.model_dump(exclude_unset=True)

    # DOB string → date
    if "dob" in data and data["dob"]:
        try:
            data["dob"] = datetime.strptime(data["dob"][:10], "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=422, detail="dob must be YYYY-MM-DD")
    elif "dob" in data:
        data["dob"] = None

    # scheduled_date string → date  (manual override path)
    if "scheduled_date" in data and data["scheduled_date"]:
        try:
            data["scheduled_date"] = datetime.strptime(data["scheduled_date"][:10], "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=422, detail="scheduled_date must be YYYY-MM-DD")
    elif "scheduled_date" in data:
        data["scheduled_date"] = None

    # scheduled_start_time string ("HH:MM") → time
    if "scheduled_start_time" in data and data["scheduled_start_time"]:
        try:
            parts = data["scheduled_start_time"].split(":")
            data["scheduled_start_time"] = _time(int(parts[0]), int(parts[1]))
        except (ValueError, IndexError):
            raise HTTPException(status_code=422, detail="scheduled_start_time must be HH:MM")
    elif "scheduled_start_time" in data:
        data["scheduled_start_time"] = None

    # Assistant surgeon appt date string → date
    if "assistant_surgeon_appt_date" in data and data["assistant_surgeon_appt_date"]:
        try:
            data["assistant_surgeon_appt_date"] = datetime.strptime(
                data["assistant_surgeon_appt_date"][:10], "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=422,
                                detail="assistant_surgeon_appt_date must be YYYY-MM-DD")
    elif "assistant_surgeon_appt_date" in data:
        data["assistant_surgeon_appt_date"] = None

    # Apply
    for k, v in data.items():
        setattr(s, k, v)

    # Auto-derive: when transitioning incomplete → new, ensure milestones exist
    if data.get("status") == "new" and not s.milestones:
        _spawn_milestones(db, s)

    # Auto-mark prior_auth done if auth_status moves to a terminal value AND
    # the corresponding milestone exists
    if "auth_status" in data and data["auth_status"] in ("approved", "not_required", "completed"):
        m = next((m for m in s.milestones if m.kind == "prior_auth"), None)
        if m and m.status not in ("done", "skipped"):
            m.status = "done"
            m.completed_at = datetime.utcnow()
            m.completed_by = current_user.get("email") or "system"

    # Assistant-surgeon milestone auto-transition based on the flag and
    # whether both office-notified + appt-confirmed are filled.
    if "assistant_surgeon_required" in data:
        m = next((mm for mm in s.milestones if mm.kind == "assistant_surgeon"), None)
        if m:
            if not s.assistant_surgeon_required:
                # Not required → mark not_applicable
                if m.status not in ("done", "not_applicable", "skipped"):
                    m.status = "not_applicable"
                    m.completed_at = datetime.utcnow()
                    m.completed_by = current_user.get("email") or "system"
            else:
                # Newly required (or re-required) → reopen if it was N/A
                if m.status == "not_applicable":
                    m.status = "pending"
                    m.completed_at = None

    db.commit(); db.refresh(s)
    return _surgery_dict(s, include_milestones=True)


class MilestoneAction(BaseModel):
    notes: Optional[str] = None
    data: Optional[dict] = None


@router.post("/{surgery_id}/milestones/{kind}/{action}")
def milestone_action(
    surgery_id: str, kind: str, action: str,
    payload: MilestoneAction = MilestoneAction(),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_permission("surgery:work")),
):
    """action ∈ {start, done, skip, reopen, not_applicable}.
    `start` flips locked/pending → in_progress and stamps started_at.
    `done` flips → done and stamps completed_at + completed_by.
    `skip` flips → skipped (with notes).
    `reopen` flips back to pending and clears completion stamps.
    `not_applicable` flips → not_applicable (e.g. labs not needed for this case).
    """
    if action not in ("start", "done", "skip", "reopen", "not_applicable"):
        raise HTTPException(status_code=422, detail=f"unknown action: {action}")

    s = (db.query(Surgery)
           .options(joinedload(Surgery.milestones))
           .filter(Surgery.id == surgery_id)
           .first())
    if not s:
        raise HTTPException(status_code=404, detail="surgery not found")
    m = next((m for m in s.milestones if m.kind == kind), None)
    if not m:
        raise HTTPException(status_code=404,
                            detail=f"surgery doesn't have milestone {kind}")

    me = current_user.get("email") or "system"
    now = datetime.utcnow()
    prev_status = m.status

    if action == "start":
        m.status = "in_progress"
        m.started_at = m.started_at or now
    elif action == "done":
        m.status = "done"
        m.completed_at = now
        m.completed_by = me
        # First milestone done? auto-bump status from new → in_progress
        if s.status == "new":
            s.status = "in_progress"
    elif action == "skip":
        m.status = "skipped"
        m.completed_at = now
        m.completed_by = me
    elif action == "reopen":
        m.status = "pending"
        m.completed_at = None
        m.completed_by = None
        m.started_at = None   # reset timing so age-since-eligible re-clocks
    elif action == "not_applicable":
        m.status = "not_applicable"

    if payload.notes is not None:
        m.notes = payload.notes
    if payload.data is not None:
        m.data_json = payload.data

    # Cross-module state-transition audit (compliance/forensics).
    from app.services.state_audit import log_state_transition
    log_state_transition(db,
        entity_type="surgery_milestone",
        entity_id=m.id,
        action=f"milestone_{action}",
        actor=me,
        before=prev_status,
        after=m.status,
        summary=f"{m.kind}: {prev_status} → {m.status}",
        detail={"surgery_id": str(s.id), "milestone_kind": m.kind, "action": action})

    db.commit(); db.refresh(m)
    db.refresh(s)
    return _surgery_dict(s, include_milestones=True)


# ─── Status transitions (cancel / hold / unresponsive) ──────────────

class CancelPayload(BaseModel):
    reason: str          # patient | anesthesia | hospital | medical | unresponsive | hold
    notes: Optional[str] = None
    fee_required: Optional[bool] = None    # caller can override system default


@router.post("/{surgery_id}/cancel")
def cancel_surgery(surgery_id: str, payload: CancelPayload,
                    db: Session = Depends(get_db),
                    current_user: dict = Depends(require_permission("surgery:cancel"))):
    """Cancel / hold / mark unresponsive. Fee logic: $351 only when
    reason=patient AND surgery is within 14 days of scheduled_date.
    Anesthesia/hospital/medical cancellations never charge a fee."""
    from app.models.surgery import SurgeryCancellation

    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="surgery not found")
    if payload.reason not in ("patient", "anesthesia", "hospital", "medical",
                                "unresponsive", "hold"):
        raise HTTPException(status_code=422, detail="invalid reason")

    fee_required = False
    refund_required = False
    if payload.reason == "patient" and s.scheduled_date:
        days_to_surgery = (s.scheduled_date - _date.today()).days
        if 0 <= days_to_surgery <= 14:
            fee_required = True
    if payload.fee_required is not None:
        fee_required = payload.fee_required
    if s.amount_paid and float(s.amount_paid) > 0:
        refund_required = True

    new_status = "hold" if payload.reason == "hold" else (
                  "unresponsive" if payload.reason == "unresponsive" else "cancelled")
    s.status = new_status

    # Free the booked slot so the time becomes available for waitlisters.
    # Capture the freed block_day_id so the frontend can chain into the
    # waitlist-matches drawer on success.
    freed_block_day_id = None
    held_slot = (db.query(SurgerySlot)
                   .filter(SurgerySlot.surgery_id == s.id).first())
    if held_slot:
        freed_block_day_id = str(held_slot.block_day_id)
        db.delete(held_slot)
    if payload.reason != "hold":
        # Hold preserves the date so the patient can resume; cancel/un-
        # responsive frees it. Clear scheduled_date when cancelled.
        s.scheduled_date = None
        s.scheduled_start_time = None

    db.add(SurgeryCancellation(
        surgery_id=s.id,
        cancelled_by=current_user.get("email") or "system",
        reason=payload.reason,
        fee_required=fee_required,
        refund_required=refund_required,
        notes=payload.notes,
    ))
    db.commit(); db.refresh(s)
    return {
        "id": str(s.id),
        "status": s.status,
        "fee_required": fee_required,
        "refund_required": refund_required,
        "freed_block_day_id": freed_block_day_id,
    }


# ─── Milestone helper ───────────────────────────────────────────────

def _spawn_milestones(db: Session, s: Surgery) -> None:
    """Create the milestone set for this surgery if missing. Catalog
    matches the seed importer."""
    from app.services.surgery_smartsheet_seed import (
        HOSPITAL_MILESTONES, OFFICE_MILESTONES,
    )
    from app.models.surgery import SurgeryMilestone

    catalog = OFFICE_MILESTONES if s.selected_facility == "office" else HOSPITAL_MILESTONES
    for pos, (kind, title, expected_days) in enumerate(catalog, 1):
        # Conditional milestones default to not_applicable when the
        # corresponding flag is off — surfaces only when the workflow
        # actually needs them.
        if kind == "assistant_surgeon" and not s.assistant_surgeon_required:
            initial_status = "not_applicable"
        else:
            initial_status = "pending"
        db.add(SurgeryMilestone(
            surgery_id=s.id,
            kind=kind, title=title, position=pos,
            status=initial_status, expected_duration_days=expected_days,
        ))


# ─── Block schedule admin (Phase 1.7) ───────────────────────────────

class BlockScheduleIn(BaseModel):
    facility: str
    recurrence_kind: str       # weekly | weekly_nth | specific_dates
    weekday: Optional[int] = None
    nth_in_month: Optional[list[int]] = None
    specific_dates: Optional[list[str]] = None
    start_time: str
    end_time: str
    block_kind: str            # robotic_only | minor_only | major_only | mixed | office
    effective_from: Optional[str] = None
    effective_through: Optional[str] = None
    notes: Optional[str] = None


def _parse_time(s: str) -> Optional[_time]:
    if not s:
        return None
    h, m, *_extra = s.split(":")
    return _time(int(h), int(m))


@router.get("/admin/block-schedules")
def list_block_schedules(db: Session = Depends(get_db),
                          current_user: dict = Depends(require_permission("surgery:manage"))):
    rows = db.query(BlockSchedule).order_by(BlockSchedule.facility,
                                              BlockSchedule.weekday).all()
    return {"schedules": [
        {
            "id": str(r.id),
            "facility": r.facility,
            "recurrence_kind": r.recurrence_kind,
            "weekday": r.weekday,
            "nth_in_month": r.nth_in_month,
            "specific_dates": r.specific_dates,
            "start_time": str(r.start_time),
            "end_time": str(r.end_time),
            "block_kind": r.block_kind,
            "effective_from": str(r.effective_from),
            "effective_through": str(r.effective_through) if r.effective_through else None,
            "notes": r.notes,
        }
        for r in rows
    ]}


@router.post("/admin/block-schedules", status_code=201)
def create_block_schedule(payload: BlockScheduleIn, db: Session = Depends(get_db),
                            current_user: dict = Depends(require_permission("surgery:manage"))):
    if payload.facility not in ("medstar", "crmc", "office"):
        raise HTTPException(status_code=422, detail="facility must be medstar/crmc/office")
    if payload.recurrence_kind not in ("weekly", "weekly_nth", "specific_dates"):
        raise HTTPException(status_code=422, detail="invalid recurrence_kind")
    if payload.recurrence_kind in ("weekly", "weekly_nth") and payload.weekday is None:
        raise HTTPException(status_code=422, detail="weekday required for weekly schedules")
    if payload.recurrence_kind == "weekly_nth" and not payload.nth_in_month:
        raise HTTPException(status_code=422, detail="nth_in_month required for weekly_nth")
    if payload.recurrence_kind == "specific_dates" and not payload.specific_dates:
        raise HTTPException(status_code=422, detail="specific_dates required")

    eff_from = (datetime.strptime(payload.effective_from, "%Y-%m-%d").date()
                if payload.effective_from else _date.today())
    eff_through = (datetime.strptime(payload.effective_through, "%Y-%m-%d").date()
                   if payload.effective_through else None)

    bs = BlockSchedule(
        facility=payload.facility,
        recurrence_kind=payload.recurrence_kind,
        weekday=payload.weekday,
        nth_in_month=payload.nth_in_month,
        specific_dates=payload.specific_dates,
        start_time=_parse_time(payload.start_time),
        end_time=_parse_time(payload.end_time),
        block_kind=payload.block_kind,
        effective_from=eff_from,
        effective_through=eff_through,
        notes=payload.notes,
        created_by=current_user.get("email"),
    )
    db.add(bs); db.commit(); db.refresh(bs)
    # Auto-rematerialize for the next 90 days
    from app.services.surgery_block_schedule import materialize_block_days
    materialize_block_days(db)
    return {"id": str(bs.id), "facility": bs.facility}


@router.delete("/admin/block-schedules/{schedule_id}", status_code=204)
def delete_block_schedule(schedule_id: str, db: Session = Depends(get_db),
                            current_user: dict = Depends(require_permission("surgery:manage"))):
    bs = db.query(BlockSchedule).filter(BlockSchedule.id == schedule_id).first()
    if not bs:
        raise HTTPException(status_code=404, detail="not found")
    db.delete(bs); db.commit()
    return None


@router.post("/admin/block-schedules/materialize")
def trigger_materialize(db: Session = Depends(get_db),
                          current_user: dict = Depends(require_permission("surgery:manage"))):
    from app.services.surgery_block_schedule import materialize_block_days
    return materialize_block_days(db)


@router.post("/admin/run-escalations")
def trigger_escalations(db: Session = Depends(get_db),
                          current_user: dict = Depends(require_permission("surgery:manage"))):
    """Manually fire the behind-schedule sweep."""
    from app.services.surgery_escalations import run_escalation_sweep
    return run_escalation_sweep(db)


@router.post("/admin/run-release-sweep")
def trigger_release_sweep(db: Session = Depends(get_db),
                           current_user: dict = Depends(require_permission("surgery:manage"))):
    """Manually fire the daily release-alert sweep."""
    from app.services.surgery_release_alerts import run_release_sweep
    return run_release_sweep(db)


@router.get("/scheduler-alerts")
def scheduler_alerts(db: Session = Depends(get_db),
                      current_user: dict = Depends(require_permission("surgery:work"))):
    """Surfaces actionable scheduling alerts for the surgery scheduler's
    checklist. Currently:
      - Office procedure days within 14 days that have <6 cases booked
        (Dr. Cooke's day isn't full — release the rest for clinic).
    """
    from app.services.surgery_release_alerts import OFFICE_FULL_THRESHOLD
    today = _date.today()
    horizon = today + timedelta(days=14)
    rows = (db.query(BlockDay)
              .options(joinedload(BlockDay.slots))
              .filter(BlockDay.facility == "office",
                      BlockDay.block_date >= today,
                      BlockDay.block_date <= horizon)
              .order_by(BlockDay.block_date)
              .all())
    underbooked = []
    for bd in rows:
        booked = len(bd.slots or [])
        if booked >= OFFICE_FULL_THRESHOLD:
            continue
        underbooked.append({
            "block_day_id": str(bd.id),
            "block_date":   str(bd.block_date),
            "weekday":      bd.block_date.strftime("%A"),
            "facility":     bd.facility,
            "booked":       booked,
            "threshold":    OFFICE_FULL_THRESHOLD,
            "open_slots":   OFFICE_FULL_THRESHOLD - booked,
            "days_out":     (bd.block_date - today).days,
            "alerted_at":   (bd.release_alert_sent_at.isoformat()
                              if bd.release_alert_sent_at else None),
        })
    return {
        "office_underbooked": underbooked,
        "threshold": OFFICE_FULL_THRESHOLD,
    }


@router.post("/admin/block-days/{block_day_id}/mark-released")
def mark_block_day_released(block_day_id: str,
                              db: Session = Depends(get_db),
                              current_user: dict = Depends(require_permission("surgery:work"))):
    """The scheduler called the hospital and released this unbooked block day.
    Stamps release_alert_sent_at so it falls off the dashboard alert list."""
    bd = db.query(BlockDay).filter(BlockDay.id == block_day_id).first()
    if not bd:
        raise HTTPException(status_code=404, detail="block day not found")
    # Idempotent: a second call returns the original timestamp instead of
    # overwriting it (avoids racing two schedulers stamping conflicting times).
    if bd.release_alert_sent_at is None:
        bd.release_alert_sent_at = datetime.utcnow()
        db.commit()
    return {"ok": True, "released_at": bd.release_alert_sent_at.isoformat()}


# ─── Boarding slips ─────────────────────────────────────────────────

@router.post("/{surgery_id}/boarding-slip")
def generate_boarding_slip(surgery_id: str,
                            db: Session = Depends(get_db),
                            current_user: dict = Depends(require_permission("surgery:work"))):
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="surgery not found")
    if s.selected_facility not in ("medstar", "crmc"):
        raise HTTPException(status_code=409,
                            detail=f"Boarding slip not needed for facility {s.selected_facility}")
    from app.services.surgery_boarding_slip import generate_for_surgery
    try:
        f = generate_for_surgery(db, s, by_email=current_user.get("email") or "system")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Boarding slip generation failed: {exc}")
    return {
        "id": str(f.id),
        "filename": f.filename,
        "size_bytes": f.size_bytes,
        "download_url": f"/api/surgery/{surgery_id}/files/{f.id}/download",
    }


# ─── Klara message drafter ──────────────────────────────────────────

@router.get("/{surgery_id}/klara-draft/{kind}")
def klara_draft(surgery_id: str, kind: str,
                 db: Session = Depends(get_db),
                 current_user: dict = Depends(require_permission("surgery:work"))):
    """Generate a Klara message draft. kind ∈
    {initial_scheduling, date_reminder, post_op_check_in}."""
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="surgery not found")
    from app.services.surgery_klara_drafter import draft
    try:
        return draft(kind, s)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


class KlaraSendNote(BaseModel):
    kind: str
    body_preview: Optional[str] = None


@router.post("/{surgery_id}/klara-sent")
def log_klara_sent(surgery_id: str, payload: KlaraSendNote,
                    db: Session = Depends(get_db),
                    current_user: dict = Depends(require_permission("surgery:work"))):
    """Record that staff copied the draft into Klara and sent it. Adds
    a SurgeryNotification row + bumps the klara_scheduling milestone if
    this is the initial outreach."""
    from app.models.surgery import SurgeryNotification, SurgeryMilestone

    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="surgery not found")

    db.add(SurgeryNotification(
        surgery_id=s.id,
        kind=payload.kind,
        sent_by=current_user.get("email"),
        body_preview=payload.body_preview,
    ))

    if payload.kind == "klara_initial":
        m = (db.query(SurgeryMilestone)
               .filter(SurgeryMilestone.surgery_id == s.id,
                       SurgeryMilestone.kind == "klara_scheduling")
               .first())
        if m and m.status not in ("done", "skipped"):
            m.status = "done"
            m.completed_at = datetime.utcnow()
            m.completed_by = current_user.get("email")
            s.sub_flag = "klara_sent"

    db.commit()
    return {"ok": True}


@router.post("/{surgery_id}/files", status_code=201)
async def upload_file(
    surgery_id: str,
    file: UploadFile = File(...),
    kind: str = Query(..., description="prior_auth | op_notes | path_report | clearance | consent | fmla | other"),
    notes: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_permission("surgery:work")),
):
    """Upload a file for a surgery. Auto-completes the matching milestone
    if there's an obvious mapping (prior_auth, op_notes, path_report)."""
    if kind not in ("prior_auth", "op_notes", "path_report", "clearance",
                    "consent", "fmla", "other"):
        raise HTTPException(status_code=422, detail=f"unknown file kind: {kind}")

    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="surgery not found")

    uploads_dir = "/Users/wwcclaudecode/Documents/wwc-era-project/backend/uploads/surgery_files"
    os.makedirs(uploads_dir, exist_ok=True)
    contents = await file.read()
    safe_name = f"{s.chart_number}_{kind}_{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}-{file.filename}"
    save_path = os.path.join(uploads_dir, safe_name)
    with open(save_path, "wb") as f:
        f.write(contents)

    f_row = SurgeryFile(
        surgery_id=s.id,
        kind=kind,
        filename=file.filename,
        path=save_path,
        mime_type=file.content_type,
        size_bytes=len(contents),
        notes=notes,
        uploaded_by=current_user.get("email"),
    )
    db.add(f_row)

    # Auto-complete the obvious milestone
    milestone_kind_map = {
        "prior_auth":  "prior_auth",
        "op_notes":    "op_notes",
        "path_report": "path_report",
    }
    target = milestone_kind_map.get(kind)
    if target:
        m = next((m for m in s.milestones if m.kind == target), None)
        if m and m.status not in ("done", "skipped"):
            m.status = "done"
            m.completed_at = datetime.utcnow()
            m.completed_by = current_user.get("email")

    # When prior auth is uploaded, also mark auth_status if not already terminal
    if kind == "prior_auth" and s.auth_status not in ("approved", "not_required", "completed"):
        s.auth_status = "approved"

    db.commit(); db.refresh(f_row)
    return {
        "id": str(f_row.id),
        "kind": f_row.kind,
        "filename": f_row.filename,
        "size_bytes": f_row.size_bytes,
        "uploaded_at": f_row.uploaded_at.isoformat(),
        "download_url": f"/api/surgery/{surgery_id}/files/{f_row.id}/download",
    }


@router.get("/{surgery_id}/files")
def list_files(surgery_id: str, db: Session = Depends(get_db),
                current_user: dict = Depends(require_permission("surgery:read"))):
    rows = (db.query(SurgeryFile)
              .filter(SurgeryFile.surgery_id == surgery_id)
              .order_by(SurgeryFile.uploaded_at.desc())
              .all())
    return {"files": [
        {
            "id": str(f.id),
            "kind": f.kind,
            "filename": f.filename,
            "mime_type": f.mime_type,
            "size_bytes": f.size_bytes,
            "uploaded_at": f.uploaded_at.isoformat(),
            "uploaded_by": f.uploaded_by,
            "notes": f.notes,
            "download_url": f"/api/surgery/{surgery_id}/files/{f.id}/download",
        }
        for f in rows
    ]}


@router.get("/{surgery_id}/files/{file_id}/download")
def download_file(surgery_id: str, file_id: str,
                   db: Session = Depends(get_db),
                   current_user: dict = Depends(require_permission("surgery:read"))):
    from fastapi.responses import FileResponse
    f = db.query(SurgeryFile).filter(
        SurgeryFile.id == file_id,
        SurgeryFile.surgery_id == surgery_id,
    ).first()
    if not f:
        raise HTTPException(status_code=404, detail="file not found")
    if not os.path.exists(f.path):
        raise HTTPException(status_code=404, detail="file path missing on disk")
    return FileResponse(f.path, filename=f.filename, media_type=f.mime_type or "application/octet-stream")


@router.get("/admin/block-days")
def list_block_days(
    facility: Optional[str] = None,
    days: int = 60,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_permission("surgery:read")),
):
    today = _date.today()
    end = today + timedelta(days=days)
    q = (db.query(BlockDay)
           .options(joinedload(BlockDay.slots))
           .filter(BlockDay.block_date >= today, BlockDay.block_date <= end))
    if facility:
        q = q.filter(BlockDay.facility == facility)
    rows = q.order_by(BlockDay.block_date, BlockDay.facility).all()
    out = []
    for bd in rows:
        slots = [
            {
                "id": str(sl.id),
                "surgery_id": str(sl.surgery_id) if sl.surgery_id else None,
                "start_time": str(sl.start_time),
                "duration_minutes": sl.duration_minutes,
                "procedure_kind": sl.procedure_kind,
            }
            for sl in (bd.slots or [])
        ]
        # Fill in patient names for each slot
        chart_ids = [sl["surgery_id"] for sl in slots if sl["surgery_id"]]
        if chart_ids:
            patients = {str(s.id): s.patient_name
                        for s in db.query(Surgery)
                                     .filter(Surgery.id.in_(chart_ids)).all()}
            for sl in slots:
                if sl["surgery_id"]:
                    sl["patient_name"] = patients.get(sl["surgery_id"])
        out.append({
            "id": str(bd.id),
            "facility": bd.facility,
            "block_date": str(bd.block_date),
            "block_kind": bd.block_kind,
            "start_time": str(bd.start_time),
            "end_time": str(bd.end_time),
            "is_addon": bd.is_addon,
            "notes": bd.notes,
            "slots": slots,
        })
    return {"days": out}


# ─── Blackout days (US holidays + PTO) ──────────────────────────────

class BlackoutIn(BaseModel):
    blackout_date: str         # YYYY-MM-DD
    scope: str                 # office | provider | facility
    reason: str                # holiday | pto | facility_closed | equipment_down | other
    label: Optional[str] = None
    owner_email: Optional[str] = None
    facility: Optional[str] = None
    notes: Optional[str] = None


@router.get("/admin/blackouts")
def list_blackouts(
    days: int = 365,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_permission("surgery:read")),
):
    today = _date.today()
    end = today + timedelta(days=days)
    rows = (db.query(SurgeryBlackoutDay)
              .filter(SurgeryBlackoutDay.blackout_date >= today,
                      SurgeryBlackoutDay.blackout_date <= end)
              .order_by(SurgeryBlackoutDay.blackout_date).all())
    return {"blackouts": [
        {
            "id": str(b.id),
            "blackout_date": str(b.blackout_date),
            "scope": b.scope,
            "reason": b.reason,
            "label": b.label,
            "owner_email": b.owner_email,
            "facility": b.facility,
            "is_recurring": bool(b.is_recurring),
            "notes": b.notes,
        }
        for b in rows
    ]}


@router.post("/admin/blackouts", status_code=201)
def create_blackout(payload: BlackoutIn, db: Session = Depends(get_db),
                     current_user: dict = Depends(require_permission("surgery:manage"))):
    if payload.scope not in ("office", "provider", "facility"):
        raise HTTPException(status_code=422, detail="invalid scope")
    if payload.scope == "provider" and not payload.owner_email:
        raise HTTPException(status_code=422, detail="owner_email required for provider scope")
    if payload.scope == "facility" and not payload.facility:
        raise HTTPException(status_code=422, detail="facility required for facility scope")

    try:
        bd = datetime.strptime(payload.blackout_date[:10], "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=422, detail="blackout_date must be YYYY-MM-DD")

    row = SurgeryBlackoutDay(
        blackout_date=bd,
        scope=payload.scope,
        reason=payload.reason,
        label=payload.label,
        owner_email=payload.owner_email,
        facility=payload.facility,
        notes=payload.notes,
        created_by=current_user.get("email"),
    )
    db.add(row); db.commit(); db.refresh(row)
    return {"id": str(row.id)}


@router.delete("/admin/blackouts/{blackout_id}", status_code=204)
def delete_blackout(blackout_id: str, db: Session = Depends(get_db),
                     current_user: dict = Depends(require_permission("surgery:manage"))):
    row = db.query(SurgeryBlackoutDay).filter(SurgeryBlackoutDay.id == blackout_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    db.delete(row); db.commit()
    return None


# ─── Booking a slot ─────────────────────────────────────────────────

class BookSlotIn(BaseModel):
    block_day_id: str
    start_time: str            # HH:MM
    duration_minutes: int
    procedure_kind: str        # robotic_180 | robotic_240 | minor | major | office


@router.post("/{surgery_id}/book-slot")
def book_slot_endpoint(surgery_id: str, payload: BookSlotIn,
                        db: Session = Depends(get_db),
                        current_user: dict = Depends(require_permission("surgery:work"))):
    from app.services.surgery_block_schedule import book_slot, CapacityViolation
    try:
        slot = book_slot(
            db,
            block_day_id=payload.block_day_id,
            surgery_id=surgery_id,
            start_time=_parse_time(payload.start_time),
            duration_minutes=payload.duration_minutes,
            procedure_kind=payload.procedure_kind,
        )
    except CapacityViolation as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    return {
        "ok": True,
        "slot_id": str(slot.id),
        "surgery_id": str(slot.surgery_id),
        "block_day_id": str(slot.block_day_id),
    }


# ─── Scheduler-side date picker (mirror of patient flow) ────────────

@router.get("/{surgery_id}/available-slots")
def available_slots(surgery_id: str, days_ahead: int = 180,
                     db: Session = Depends(get_db),
                     current_user: dict = Depends(require_permission("surgery:work"))):
    """Return upcoming block days that can fit this surgery's procedure
    classification. Same logic as the patient-facing slot list; intended
    for the scheduler-side "Pick date" modal."""
    from app.services.surgery_date_picker import (
        available_slots_for_surgery, DatePickerError,
    )

    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="surgery not found")
    try:
        slots = available_slots_for_surgery(db, s, days_ahead=days_ahead)
    except DatePickerError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {
        "days": [
            {
                "block_day_id": sl.block_day_id,
                "facility": sl.facility,
                "block_date": str(sl.block_date),
                "weekday": sl.block_date.strftime("%A"),
                "proposed_start_time": sl.proposed_start_time,
                "duration_minutes": sl.duration_minutes,
                "block_window": sl.block_window,
                "cases_already_booked": sl.cases_already_booked,
            } for sl in slots
        ],
        "procedure_kind": s.procedure_classification,
        "duration_minutes": (slots[0].duration_minutes if slots else None),
        "current_block_day_id": (
            str(db.query(SurgerySlot.block_day_id)
                  .filter(SurgerySlot.surgery_id == s.id).scalar())
            if s.scheduled_date else None
        ),
    }


class SchedulerPickIn(BaseModel):
    block_day_id: str


@router.post("/{surgery_id}/pick-date")
def scheduler_pick_date(surgery_id: str, payload: SchedulerPickIn,
                         db: Session = Depends(get_db),
                         current_user: dict = Depends(require_permission("surgery:work"))):
    """Scheduler picks (or reschedules) a date on a patient's behalf.
    Same rule set as the patient-facing flow except:
      - No 14-day reschedule lockout (staff can always reschedule)
      - Stamps last_rescheduled_by with the staff email
    """
    from app.services.surgery_date_picker import pick_or_reschedule, DatePickerError

    s = (db.query(Surgery)
           .options(joinedload(Surgery.milestones))
           .filter(Surgery.id == surgery_id).first())
    if not s:
        raise HTTPException(status_code=404, detail="surgery not found")
    try:
        result = pick_or_reschedule(
            db, s,
            block_day_id=payload.block_day_id,
            picked_by=current_user.get("email") or "staff",
        )
    except DatePickerError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {
        "ok": True,
        **result,
        "surgery": _surgery_dict(s, include_milestones=True),
    }


# ─── Scheduler workflow toggles ────────────────────────────────────

class ToggleConfirmPayload(BaseModel):
    confirmed: bool = True


@router.post("/{surgery_id}/modmed-scheduled")
def toggle_modmed_scheduled(
    surgery_id: str,
    payload: ToggleConfirmPayload = ToggleConfirmPayload(),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_permission("surgery:work")),
):
    """Mark the appointment as added to (or removed from) the ModMed schedule."""
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="surgery not found")
    if payload.confirmed:
        s.scheduled_in_modmed_at = datetime.utcnow()
        s.scheduled_in_modmed_by = current_user.get("email") or "system"
    else:
        s.scheduled_in_modmed_at = None
        s.scheduled_in_modmed_by = None
    db.commit(); db.refresh(s)
    return _surgery_dict(s, include_milestones=True)


@router.post("/{surgery_id}/office-meds-pickup")
def toggle_office_meds_pickup(
    surgery_id: str,
    payload: ToggleConfirmPayload = ToggleConfirmPayload(),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_permission("surgery:work")),
):
    """Mark that the office-procedure patient has confirmed picking up their meds."""
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="surgery not found")
    if s.selected_facility != "office":
        raise HTTPException(status_code=409,
                            detail="Med pickup only applies to office procedures.")
    if payload.confirmed:
        s.office_meds_pickup_confirmed_at = datetime.utcnow()
        s.office_meds_pickup_confirmed_by = current_user.get("email") or "system"
    else:
        s.office_meds_pickup_confirmed_at = None
        s.office_meds_pickup_confirmed_by = None
    db.commit(); db.refresh(s)
    return _surgery_dict(s, include_milestones=True)


# ─── Surgery notes (timestamped log) ───────────────────────────────

class SurgeryNoteIn(BaseModel):
    content: str


@router.get("/{surgery_id}/notes")
def list_surgery_notes(
    surgery_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_permission("surgery:read")),
):
    from app.models.surgery import SurgeryNote
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="surgery not found")
    rows = (db.query(SurgeryNote)
              .filter(SurgeryNote.surgery_id == surgery_id)
              .order_by(SurgeryNote.created_at.desc()).all())
    return [
        {
            "id": str(n.id),
            "content": n.content,
            "created_by": n.created_by,
            "created_at": n.created_at.isoformat() if n.created_at else None,
        }
        for n in rows
    ]


@router.post("/{surgery_id}/notes", status_code=201)
def add_surgery_note(
    surgery_id: str,
    payload: SurgeryNoteIn,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_permission("surgery:work")),
):
    from app.models.surgery import SurgeryNote
    if not payload.content or not payload.content.strip():
        raise HTTPException(status_code=422, detail="note cannot be empty")
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="surgery not found")
    n = SurgeryNote(
        surgery_id=surgery_id,
        content=payload.content.strip(),
        created_by=current_user.get("email") or "system",
    )
    db.add(n); db.commit(); db.refresh(n)
    return {
        "id": str(n.id),
        "content": n.content,
        "created_by": n.created_by,
        "created_at": n.created_at.isoformat(),
    }


@router.delete("/{surgery_id}/notes/{note_id}", status_code=204)
def delete_surgery_note(
    surgery_id: str,
    note_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_permission("surgery:work")),
):
    """Delete a note. Author may delete their own; surgery:manage role may
    delete any."""
    from app.models.surgery import SurgeryNote
    n = (db.query(SurgeryNote)
           .filter(SurgeryNote.id == note_id,
                   SurgeryNote.surgery_id == surgery_id).first())
    if not n:
        raise HTTPException(status_code=404, detail="note not found")
    email = (current_user.get("email") or "").lower()
    has_manage = "surgery:manage" in (current_user.get("effective_permissions")
                                         or current_user.get("permissions") or [])
    if n.created_by.lower() != email and not has_manage:
        raise HTTPException(status_code=403,
                            detail="Only the author or a surgery manager can delete this note.")
    db.delete(n); db.commit()
    return None


# ─── Post-op appointments ──────────────────────────────────────────

class PostOpApptsPayload(BaseModel):
    first_date: Optional[str] = None       # YYYY-MM-DD or null/blank to clear
    second_date: Optional[str] = None


@router.get("/{surgery_id}/post-op-schedule")
def get_post_op_schedule(
    surgery_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_permission("surgery:read")),
):
    """Return the post-op visits this surgery's procedures require, plus
    suggested dates relative to scheduled_date. Used by the frontend to
    pre-populate the date pickers."""
    from app.services.post_op_schedule import determine_post_op_schedule
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="surgery not found")
    visits = determine_post_op_schedule(s)
    base = s.scheduled_date
    return {
        "scheduled_date": str(base) if base else None,
        "visits": [
            {
                "label": v.label,
                "days_post_op": v.days_post_op,
                "suggested_date": str(base + timedelta(days=v.days_post_op)) if base else None,
            } for v in visits
        ],
        "current_first": str(s.post_op_appt_date) if s.post_op_appt_date else None,
        "current_second": str(s.post_op_appt_2nd_date) if s.post_op_appt_2nd_date else None,
    }


@router.post("/{surgery_id}/post-op-appts")
def save_post_op_appts(
    surgery_id: str,
    payload: PostOpApptsPayload,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_permission("surgery:work")),
):
    """Record the patient's post-op appointment dates. Auto-completes the
    post_op_appts_scheduled milestone once every required appt has a date."""
    from app.services.post_op_schedule import all_required_appts_filled

    s = (db.query(Surgery)
           .options(joinedload(Surgery.milestones))
           .filter(Surgery.id == surgery_id).first())
    if not s:
        raise HTTPException(status_code=404, detail="surgery not found")

    def _parse_date(v):
        if not v:
            return None
        try:
            return datetime.strptime(v[:10], "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=422, detail=f"invalid date: {v}")

    s.post_op_appt_date = _parse_date(payload.first_date)
    s.post_op_appt_2nd_date = _parse_date(payload.second_date)

    # Auto-close the milestone when all required appts are filled
    m = next((mm for mm in s.milestones if mm.kind == "post_op_appts_scheduled"), None)
    if m:
        if all_required_appts_filled(s):
            if m.status != "done":
                m.status = "done"
                m.completed_at = datetime.utcnow()
                m.completed_by = current_user.get("email") or "system"
        else:
            # If dates were cleared, reopen the milestone
            if m.status == "done":
                m.status = "in_progress"
                m.completed_at = None
                m.completed_by = None

    db.commit(); db.refresh(s)
    return _surgery_dict(s, include_milestones=True)


# ─── Assistant surgeon coordination ────────────────────────────────

def _maybe_complete_assistant_milestone(db: Session, s: Surgery, by: str) -> None:
    """Close the assistant_surgeon milestone when both office notified AND
    patient appt confirmed."""
    if not s.assistant_surgeon_required:
        return
    if (s.assistant_surgeon_office_notified_at is None
            or s.assistant_surgeon_appt_confirmed_at is None):
        return
    m = next((mm for mm in s.milestones if mm.kind == "assistant_surgeon"), None)
    if m and m.status not in ("done", "skipped"):
        m.status = "done"
        m.completed_at = datetime.utcnow()
        m.completed_by = by


@router.post("/{surgery_id}/assistant-surgeon/notify-office")
def assistant_surgeon_notify_office(
    surgery_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_permission("surgery:work")),
):
    """Mark the assistant surgeon's office as notified.
    Closes the assistant_surgeon milestone if the appt is also confirmed."""
    s = (db.query(Surgery)
           .options(joinedload(Surgery.milestones))
           .filter(Surgery.id == surgery_id).first())
    if not s:
        raise HTTPException(status_code=404, detail="surgery not found")
    if not s.assistant_surgeon_required:
        raise HTTPException(status_code=409,
                            detail="Assistant surgeon is not required for this surgery.")
    by = current_user.get("email") or "system"
    s.assistant_surgeon_office_notified_at = datetime.utcnow()
    s.assistant_surgeon_office_notified_by = by
    _maybe_complete_assistant_milestone(db, s, by)
    db.commit(); db.refresh(s)
    return _surgery_dict(s, include_milestones=True)


class AssistantApptConfirm(BaseModel):
    appt_date: Optional[str] = None   # YYYY-MM-DD (optional — clears if blank)


@router.post("/{surgery_id}/assistant-surgeon/confirm-appt")
def assistant_surgeon_confirm_appt(
    surgery_id: str,
    payload: AssistantApptConfirm = AssistantApptConfirm(),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_permission("surgery:work")),
):
    """Confirm the patient has scheduled an appointment with the assistant
    surgeon. Optional appt_date records the date; absence still counts as
    confirmed (some practices just want a yes/no)."""
    s = (db.query(Surgery)
           .options(joinedload(Surgery.milestones))
           .filter(Surgery.id == surgery_id).first())
    if not s:
        raise HTTPException(status_code=404, detail="surgery not found")
    if not s.assistant_surgeon_required:
        raise HTTPException(status_code=409,
                            detail="Assistant surgeon is not required for this surgery.")
    by = current_user.get("email") or "system"
    if payload.appt_date:
        try:
            s.assistant_surgeon_appt_date = datetime.strptime(
                payload.appt_date[:10], "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=422, detail="appt_date must be YYYY-MM-DD")
    s.assistant_surgeon_appt_confirmed_at = datetime.utcnow()
    s.assistant_surgeon_appt_confirmed_by = by
    _maybe_complete_assistant_milestone(db, s, by)
    db.commit(); db.refresh(s)
    return _surgery_dict(s, include_milestones=True)


@router.post("/{surgery_id}/assistant-surgeon/reset")
def assistant_surgeon_reset(
    surgery_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_permission("surgery:work")),
):
    """Clear notified / appt confirmation (e.g. patient missed the appt and
    needs to reschedule). Reopens the milestone if it was done."""
    s = (db.query(Surgery)
           .options(joinedload(Surgery.milestones))
           .filter(Surgery.id == surgery_id).first())
    if not s:
        raise HTTPException(status_code=404, detail="surgery not found")
    s.assistant_surgeon_office_notified_at = None
    s.assistant_surgeon_office_notified_by = None
    s.assistant_surgeon_appt_confirmed_at = None
    s.assistant_surgeon_appt_confirmed_by = None
    s.assistant_surgeon_appt_date = None
    m = next((mm for mm in s.milestones if mm.kind == "assistant_surgeon"), None)
    if m and m.status == "done":
        m.status = "in_progress"
        m.completed_at = None
        m.completed_by = None
    db.commit(); db.refresh(s)
    return _surgery_dict(s, include_milestones=True)


# ─── AI billing-code suggestion (Phase 3) ───────────────────────────

@router.post("/{surgery_id}/suggest-billing-codes")
def suggest_billing_codes(surgery_id: str,
                            db: Session = Depends(get_db),
                            current_user: dict = Depends(require_permission("surgery:work"))):
    """Read the surgery's op note + path report, ask Claude for ICD-10 /
    CPT / modifier / POS codes, and auto-save them on the Surgery row.
    If any CPT uses modifier 22, a justification letter PDF is generated
    and saved as a SurgeryFile."""
    from app.services.surgery_billing_ai import (
        suggest_and_save_billing, BillingAIError,
    )

    s = (db.query(Surgery)
           .options(joinedload(Surgery.files), joinedload(Surgery.milestones))
           .filter(Surgery.id == surgery_id).first())
    if not s:
        raise HTTPException(status_code=404, detail="surgery not found")
    try:
        result = suggest_and_save_billing(
            db, s, saved_by=current_user.get("email") or "system",
        )
    except BillingAIError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {
        **result,
        "surgery": _surgery_dict(s, include_milestones=True),
    }


# ─── Benefits calculator (Phase 2.9) ────────────────────────────────

class BenefitsPayload(BaseModel):
    """All fields optional. Missing values → treated as $0 / 0%.
    The calculator runs on whatever's provided; staff can save partial
    inputs and refine later."""
    deductible: Optional[float] = None         # annual plan deductible
    deductible_met: Optional[float] = None     # how much patient has paid toward it
    copay: Optional[float] = None              # fixed copay for the visit
    coinsurance_pct: Optional[float] = None    # 20.0 = 20%
    oop_max: Optional[float] = None            # annual out-of-pocket max
    oop_met: Optional[float] = None
    allowed_amount: Optional[float] = None     # insurance-allowed for this surgery
    save: bool = True   # set False to preview without persisting


def _calc_patient_responsibility(*, allowed_amount: float, deductible: float,
                                   deductible_met: float, copay: float,
                                   coinsurance_pct: float,
                                   oop_max: float, oop_met: float) -> dict:
    """Standard health-plan math. Returns a breakdown for the UI."""
    deductible_remaining = max(0.0, deductible - deductible_met)
    oop_remaining = max(0.0, oop_max - oop_met) if oop_max > 0 else float("inf")

    # Patient pays toward deductible first
    deductible_portion = min(allowed_amount, deductible_remaining)
    after_deductible = allowed_amount - deductible_portion

    # Coinsurance applies to the post-deductible amount
    coins_rate = coinsurance_pct / 100.0
    coinsurance_portion = round(after_deductible * coins_rate, 2)

    raw_responsibility = deductible_portion + coinsurance_portion + copay
    capped_responsibility = round(min(raw_responsibility, oop_remaining), 2)

    return {
        "deductible_remaining": round(deductible_remaining, 2),
        "deductible_portion": round(deductible_portion, 2),
        "after_deductible": round(after_deductible, 2),
        "coinsurance_portion": coinsurance_portion,
        "copay_portion": round(copay, 2),
        "oop_remaining": (round(oop_remaining, 2) if oop_remaining != float("inf") else None),
        "raw_responsibility": round(raw_responsibility, 2),
        "patient_responsibility": capped_responsibility,
        "capped_by_oop_max": raw_responsibility > oop_remaining,
    }


@router.post("/{surgery_id}/benefits")
def benefits_endpoint(surgery_id: str, payload: BenefitsPayload,
                       db: Session = Depends(get_db),
                       current_user: dict = Depends(require_permission("surgery:work"))):
    """Calculate (and optionally save) the patient's surgery responsibility
    from insurance benefit inputs. When save=True, also marks the
    benefits_determined milestone as done."""
    s = (db.query(Surgery)
           .options(joinedload(Surgery.milestones))
           .filter(Surgery.id == surgery_id).first())
    if not s:
        raise HTTPException(status_code=404, detail="surgery not found")

    # Coalesce inputs: payload wins, then existing surgery value, then 0
    def _g(field: str, attr: Optional[str] = None) -> float:
        v = getattr(payload, field, None)
        if v is not None:
            return float(v)
        existing = getattr(s, attr or field)
        return float(existing or 0)

    breakdown = _calc_patient_responsibility(
        allowed_amount=_g("allowed_amount"),
        deductible=_g("deductible"),
        deductible_met=_g("deductible_met"),
        copay=_g("copay"),
        coinsurance_pct=_g("coinsurance_pct"),
        oop_max=_g("oop_max"),
        oop_met=_g("oop_met"),
    )

    pdf_file_id = None
    if payload.save:
        # Persist whatever inputs were provided
        for field in ("deductible", "deductible_met", "copay",
                       "coinsurance_pct", "oop_max", "oop_met", "allowed_amount"):
            v = getattr(payload, field, None)
            if v is not None:
                setattr(s, field, v)
        s.patient_responsibility = breakdown["patient_responsibility"]
        s.benefits_verified_at = _date.today()

        # Auto-advance the benefits milestone
        m = next((m for m in s.milestones if m.kind == "benefits_determined"), None)
        if m and m.status not in ("done", "skipped"):
            m.status = "done"
            m.completed_at = datetime.utcnow()
            m.completed_by = current_user.get("email") or "system"
        db.commit(); db.refresh(s)

        # Generate the patient-facing PDF estimate and attach it
        try:
            from app.services.surgery_benefits_pdf import generate_and_attach
            pdf_file = generate_and_attach(
                db, s, breakdown, by_email=current_user.get("email") or "system")
            pdf_file_id = str(pdf_file.id)
        except Exception as exc:
            # Don't block the save if PDF generation fails — log and continue.
            import logging
            logging.getLogger(__name__).warning(
                "Benefits PDF generation failed for surgery %s: %s", s.id, exc)

    return {
        "breakdown": breakdown,
        "saved": payload.save,
        "patient_responsibility": breakdown["patient_responsibility"],
        "pdf_file_id": pdf_file_id,
        "pdf_download_url": (f"/api/surgery/{s.id}/files/{pdf_file_id}/download"
                              if pdf_file_id else None),
    }


# ─── Consent transitions (Phase 2.8) ────────────────────────────────

class ConsentTransitionPayload(BaseModel):
    notes: Optional[str] = None


@router.post("/{surgery_id}/consent/sent")
def consent_mark_sent(surgery_id: str, payload: ConsentTransitionPayload = ConsentTransitionPayload(),
                       db: Session = Depends(get_db),
                       current_user: dict = Depends(require_permission("surgery:work"))):
    """Mark that consent has been sent to the patient (paper or DocuSign).
    Sets consent_status='sent', stamps consent_sent_at, moves the consent
    milestone to 'in_progress' (still pending the signature)."""
    s = (db.query(Surgery)
           .options(joinedload(Surgery.milestones))
           .filter(Surgery.id == surgery_id).first())
    if not s:
        raise HTTPException(status_code=404, detail="surgery not found")

    s.consent_status = "sent"
    s.consent_sent_at = datetime.utcnow()
    m = next((m for m in s.milestones if m.kind == "consent"), None)
    if m and m.status not in ("done", "skipped"):
        m.status = "in_progress"
        m.started_at = m.started_at or datetime.utcnow()
        if payload.notes:
            m.notes = payload.notes
    db.commit(); db.refresh(s)
    return _surgery_dict(s, include_milestones=True)


@router.get("/{surgery_id}/consent/template-matches")
def consent_template_matches(surgery_id: str,
                              db: Session = Depends(get_db),
                              current_user: dict = Depends(require_permission("surgery:read"))):
    """Preview which templates would be sent for this surgery, without
    actually sending. Useful for the UI to show staff what they're about
    to commit to before they click Send."""
    from app.services.consent_template_matcher import (
        match_templates_for_surgery, unmatched_procedures,
    )
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="surgery not found")
    matches = match_templates_for_surgery(db, s)
    return {
        "matches": [
            {
                "template_id": str(m.template.id),
                "template_name": m.template.name,
                "docusign_template_id": m.template.docusign_template_id,
                "matched_procedure": m.matched_procedure,
                "is_supplemental": m.is_supplemental,
                "warning": m.warning,
            } for m in matches
        ],
        "unmatched_procedures": unmatched_procedures(db, s),
    }


class DocuSignSendPayload(BaseModel):
    ignore_warnings: bool = False


@router.post("/{surgery_id}/consent/docusign-send")
def consent_docusign_send(surgery_id: str,
                          payload: DocuSignSendPayload = DocuSignSendPayload(),
                          db: Session = Depends(get_db),
                          current_user: dict = Depends(require_permission("surgery:work"))):
    """Send all matched consent envelopes for this surgery (one per template).

    The matcher resolves: one primary template per procedure, plus any
    supplemental templates (Medicaid sterilization, etc.) whose insurance
    + procedure + facility match. If any matched template fails its
    min_days_before_surgery rule, the send is blocked unless
    `ignore_warnings=true` is passed.
    """
    from app.services.docusign_envelopes import (
        send_consent_envelopes, DocuSignEnvelopeError,
    )

    s = (db.query(Surgery)
           .options(joinedload(Surgery.milestones),
                     joinedload(Surgery.consent_envelopes))
           .filter(Surgery.id == surgery_id).first())
    if not s:
        raise HTTPException(status_code=404, detail="surgery not found")

    try:
        result = send_consent_envelopes(
            db, s,
            sent_by=current_user.get("email") or "system",
            ignore_warnings=payload.ignore_warnings,
        )
    except DocuSignEnvelopeError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        **result,
        "surgery": _surgery_dict(s, include_milestones=True),
    }


@router.post("/{surgery_id}/consent/docusign-sync")
def consent_docusign_sync(surgery_id: str,
                          db: Session = Depends(get_db),
                          current_user: dict = Depends(require_permission("surgery:work"))):
    """Pull the latest status for every envelope on this surgery and
    reconcile Surgery.consent_status. Manual fallback when Connect
    webhooks aren't wired up."""
    from app.services.docusign_envelopes import (
        sync_surgery_envelopes, DocuSignEnvelopeError,
    )

    s = (db.query(Surgery)
           .options(joinedload(Surgery.milestones),
                     joinedload(Surgery.consent_envelopes))
           .filter(Surgery.id == surgery_id).first())
    if not s:
        raise HTTPException(status_code=404, detail="surgery not found")
    if not s.consent_envelopes:
        raise HTTPException(status_code=400, detail="no envelopes on file for this surgery")

    try:
        result = sync_surgery_envelopes(db, s)
    except DocuSignEnvelopeError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {
        **result,
        "surgery": _surgery_dict(s, include_milestones=True),
    }


@router.post("/{surgery_id}/consent/signed")
def consent_mark_signed(surgery_id: str, payload: ConsentTransitionPayload = ConsentTransitionPayload(),
                         db: Session = Depends(get_db),
                         current_user: dict = Depends(require_permission("surgery:work"))):
    """Mark that the patient has signed the consent. Stamps consent_signed_at,
    flips status='signed', closes the consent milestone."""
    s = (db.query(Surgery)
           .options(joinedload(Surgery.milestones))
           .filter(Surgery.id == surgery_id).first())
    if not s:
        raise HTTPException(status_code=404, detail="surgery not found")

    s.consent_status = "signed"
    s.consent_signed_at = datetime.utcnow()
    m = next((m for m in s.milestones if m.kind == "consent"), None)
    if m and m.status not in ("done", "skipped"):
        m.status = "done"
        m.completed_at = datetime.utcnow()
        m.completed_by = current_user.get("email") or "system"
        if payload.notes:
            m.notes = payload.notes
    db.commit(); db.refresh(s)
    return _surgery_dict(s, include_milestones=True)


# ─── Waitlist (Phase 2) ─────────────────────────────────────────────

class WaitlistJoinIn(BaseModel):
    advance_notice_days: int = 7


@router.post("/{surgery_id}/waitlist", status_code=201)
def waitlist_join(surgery_id: str, payload: WaitlistJoinIn,
                    db: Session = Depends(get_db),
                    current_user: dict = Depends(require_permission("surgery:work"))):
    """Add a surgery to the waitlist (or update its advance_notice_days)."""
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="surgery not found")
    if payload.advance_notice_days < 0 or payload.advance_notice_days > 90:
        raise HTTPException(status_code=422, detail="advance_notice_days must be 0–90")

    # Reuse an existing un-removed row if there is one
    w = (db.query(SurgeryWaitlist)
           .filter(SurgeryWaitlist.surgery_id == s.id,
                   SurgeryWaitlist.removed_at.is_(None))
           .first())
    if w:
        w.advance_notice_days = payload.advance_notice_days
    else:
        w = SurgeryWaitlist(
            surgery_id=s.id,
            advance_notice_days=payload.advance_notice_days,
        )
        db.add(w)
    db.commit(); db.refresh(w)
    return {
        "id": str(w.id),
        "surgery_id": str(w.surgery_id),
        "advance_notice_days": w.advance_notice_days,
        "signed_up_at": w.signed_up_at.isoformat() if w.signed_up_at else None,
    }


@router.delete("/{surgery_id}/waitlist", status_code=200)
def waitlist_remove(surgery_id: str,
                     reason: Optional[str] = Query(None),
                     db: Session = Depends(get_db),
                     current_user: dict = Depends(require_permission("surgery:work"))):
    """Remove the surgery from the active waitlist."""
    w = (db.query(SurgeryWaitlist)
           .filter(SurgeryWaitlist.surgery_id == surgery_id,
                   SurgeryWaitlist.removed_at.is_(None))
           .first())
    if not w:
        raise HTTPException(status_code=404, detail="not on the waitlist")
    w.removed_at = datetime.utcnow()
    w.removed_reason = reason or "manual"
    db.commit()
    return {"ok": True}


@router.get("/admin/waitlist")
def waitlist_list(facility: Optional[str] = None,
                   procedure_kind: Optional[str] = None,
                   db: Session = Depends(get_db),
                   current_user: dict = Depends(require_permission("surgery:read"))):
    """Show every active waitlister, optionally filtered to a facility
    or procedure kind. Used by /surgery/admin/waitlist page."""
    rows = (db.query(SurgeryWaitlist, Surgery)
              .join(Surgery, SurgeryWaitlist.surgery_id == Surgery.id)
              .filter(SurgeryWaitlist.removed_at.is_(None),
                      Surgery.scheduled_date.is_(None),
                      Surgery.status.in_(["new", "in_progress", "hold"]))
              .order_by(SurgeryWaitlist.signed_up_at.asc())
              .all())
    out = []
    for w, s in rows:
        if facility and facility not in (s.eligible_facilities or []):
            continue
        if procedure_kind and s.procedure_classification != procedure_kind:
            continue
        out.append({
            "waitlist_id": str(w.id),
            "surgery_id": str(s.id),
            "patient_name": s.patient_name,
            "chart_number": s.chart_number,
            "phone": s.cell_phone or s.phone,
            "advance_notice_days": w.advance_notice_days,
            "signed_up_at": w.signed_up_at.isoformat() if w.signed_up_at else None,
            "procedure_classification": s.procedure_classification,
            "procedure_descriptions": [
                p.get("description") for p in (s.procedures or []) if p.get("description")
            ],
            "eligible_facilities": s.eligible_facilities or [],
        })
    return {"waitlist": out}


@router.get("/admin/waitlist-matches")
def waitlist_matches(block_day_id: str,
                      db: Session = Depends(get_db),
                      current_user: dict = Depends(require_permission("surgery:work"))):
    """Find waitlisters who could realistically fill the given block day."""
    from app.services.surgery_waitlist import find_matches, klara_blast_text
    matches = find_matches(db, block_day_id=block_day_id)

    bd = db.query(BlockDay).filter(BlockDay.id == block_day_id).first()
    if not bd:
        raise HTTPException(status_code=404, detail="block day not found")
    blast = klara_blast_text(bd.facility, bd.block_date)

    return {
        "block_day": {
            "id": str(bd.id),
            "facility": bd.facility,
            "block_date": str(bd.block_date),
            "block_kind": bd.block_kind,
        },
        "matches": matches,
        "klara_blast": blast,
    }


class WaitlistClaimIn(BaseModel):
    block_day_id: str
    procedure_kind: Optional[str] = None


@router.post("/admin/waitlist/{waitlist_id}/claim")
def waitlist_claim(waitlist_id: str, payload: WaitlistClaimIn,
                    db: Session = Depends(get_db),
                    current_user: dict = Depends(require_permission("surgery:work"))):
    """A waitlisted patient confirmed they want the freed slot — book it
    and remove them from the waitlist."""
    from app.services.surgery_block_schedule import book_slot, CapacityViolation, DURATIONS

    w = (db.query(SurgeryWaitlist)
           .filter(SurgeryWaitlist.id == waitlist_id,
                   SurgeryWaitlist.removed_at.is_(None))
           .first())
    if not w:
        raise HTTPException(status_code=404, detail="waitlist row not found or already claimed")
    s = db.query(Surgery).filter(Surgery.id == w.surgery_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="surgery not found")

    bd = db.query(BlockDay).filter(BlockDay.id == payload.block_day_id).first()
    if not bd:
        raise HTTPException(status_code=404, detail="block day not found")

    proc_kind = payload.procedure_kind or s.procedure_classification or "minor"
    duration = DURATIONS.get(proc_kind, s.estimated_minutes or 60)

    # Determine start time as next gap
    existing = sorted(bd.slots or [], key=lambda x: x.start_time)
    block_start_min = bd.start_time.hour * 60 + bd.start_time.minute
    cursor = block_start_min
    for sl in existing:
        sl_start = sl.start_time.hour * 60 + sl.start_time.minute
        cursor = max(cursor, sl_start + sl.duration_minutes)
    h, m = divmod(cursor, 60)

    try:
        slot = book_slot(db, block_day_id=str(bd.id), surgery_id=str(s.id),
                          start_time=_time(h, m),
                          duration_minutes=duration,
                          procedure_kind=proc_kind)
    except CapacityViolation as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    # Advance milestone, remove from waitlist
    m_row = next((m for m in s.milestones if m.kind == "patient_picks_date"), None)
    if m_row and m_row.status not in ("done", "skipped"):
        m_row.status = "done"
        m_row.completed_at = datetime.utcnow()
        m_row.completed_by = current_user.get("email") or "system:waitlist-claim"

    w.removed_at = datetime.utcnow()
    w.removed_reason = "claimed_slot"

    db.commit()
    return {
        "ok": True,
        "scheduled_date": str(bd.block_date),
        "scheduled_start_time": f"{h:02d}:{m:02d}",
        "facility": bd.facility,
    }
