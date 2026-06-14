"""Surgery scheduling API.

Phase 1: dashboard counts, list (filtered + grouped by milestone),
detail. Block schedule + capacity + boarding slips + Klara drafter
arrive in subsequent phases.
"""
from __future__ import annotations

import logging
import os
from datetime import date as _date, datetime, time as _time, timedelta
from app.utils.dt import now_utc_naive
from decimal import Decimal
from typing import Annotated, Any, Optional

from app.models.surgery import SurgeryFile

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import or_, func, desc, text
from sqlalchemy.orm import Session, joinedload
from sqlalchemy.orm.exc import StaleDataError

from app.database import get_db
from app.models.surgery import (
    Surgery, BlockSchedule, BlockDay, SurgerySlot,
    SurgeryBlackoutDay, SurgeryWaitlist, SURGERY_URGENCY_VALUES,
    SURGERY_COMPLEXITY_VALUES, SURGERY_DURATION_SOURCES,
    SURGERY_STATUS_VALUES, SURGERY_FACILITY_VALUES, SURGERY_MAX_MINUTES,
)
from app.routers.auth import get_current_user
from app.permissions.catalog import Module, Tier
from app.permissions.dependencies import requires_tier, requires_super_admin
from app.services.audit_service import log_action
from app.services.surgery.slot_conflict import overlapping_slot
from app.services.surgery.blackout_conflict import is_date_blacked_out
from app.services.surgery.settings import cfg
from app.services.surgery import step_engine
from app.services.storage import save_blob, serve_blob, is_legacy_local_path

router = APIRouter(prefix="/surgery", tags=["surgery"])


def _commit_or_409(db: Session, surgery_id: str | None = None) -> None:
    """Commit, translating StaleDataError -> 409 with a clean message.

    Surgery rows are SQLAlchemy-versioned (Surgery.version_id is the
    version_id_col), so the ORM emits UPDATE ... WHERE version_id = :v
    and raises StaleDataError if 0 rows match — meaning another worker
    committed first. Catch that here and surface a clean 409 instead
    of a 500. The caller is expected to rollback its own session
    after re-raising."""
    try:
        db.commit()
    except StaleDataError:
        db.rollback()
        raise HTTPException(status_code=409,
            detail="This surgery was updated by another user while you were "
                   "editing — refresh and try again")


# ─── Money sanity ceiling ────────────────────────────────────────────
# Per project memory feedback_money_sanity_ceiling: any value >$50K in a
# money column is a column-shift / fat-finger artifact and must be
# rejected at the boundary, not silently stored where it contaminates
# reports. Apply to every Optional[float] money field on the surgery
# payloads (PATCH /surgeries/{id}, POST /surgeries/{id}/benefits) so
# Pydantic emits a 422 before the value reaches the row.
DollarAmount = Annotated[float, Field(ge=0, le=50_000)]
PercentAmount = Annotated[float, Field(ge=0, le=100)]


# ─── Behind-schedule helper ──────────────────────────────────────────

def _is_behind_steps(db: Session, s: Surgery) -> tuple[bool, int]:
    """Returns (is_behind, hours_overdue) for a surgery's current step,
    using the steps engine (config-driven expected days + grace)."""
    return step_engine.is_behind(
        s,
        expected_days=step_engine.expected_days_map(db, s),
        grace_hours=cfg(db, "critical_overdue_hours"),
    )


# ─── Serializer ─────────────────────────────────────────────────────

def _latest_file(s: Surgery, *, kind: str) -> Optional[dict]:
    """Return a {id, filename, download_url, uploaded_at, send_history}
    dict for the most recently uploaded SurgeryFile of the given kind,
    or None."""
    files = [f for f in (s.files or []) if f.kind == kind]
    if not files:
        return None
    latest = max(files, key=lambda f: f.uploaded_at)
    return {
        "id":           str(latest.id),
        "filename":     latest.filename,
        "uploaded_at":  latest.uploaded_at.isoformat() if latest.uploaded_at else None,
        "download_url": f"/api/surgery/{s.id}/files/{latest.id}/download",
        "send_history": latest.send_history or [],
    }


def _surgery_dict(db: Session, s: Surgery, *,
                   today: Optional[_date] = None) -> dict:
    behind, hours_overdue = _is_behind_steps(db, s)
    cur_step = step_engine.current_step(s)
    out = {
        "id": str(s.id),
        "surgery_number": s.surgery_number,
        "chart_number": s.chart_number,
        "patient_name": s.patient_name,
        "first_name": s.first_name,
        "last_name": s.last_name,
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
        "primary_payer_id": s.primary_payer_id,
        "secondary_insurance": s.secondary_insurance,
        "secondary_member_id": s.secondary_member_id,
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
        "preop_needs_repeat": _preop_needs_repeat(db, s),
        "lab_appointment_date": (
            str(s.lab_appointment_date) if s.lab_appointment_date else None),
        "lab_appointment_reported_at": (
            s.lab_appointment_reported_at.isoformat()
            if s.lab_appointment_reported_at else None),
        "lab_appointment_reported_by": s.lab_appointment_reported_by,
        "post_op_appt_date": str(s.post_op_appt_date) if s.post_op_appt_date else None,
        "post_op_appt_2nd_date": (
            str(s.post_op_appt_2nd_date) if s.post_op_appt_2nd_date else None),
        "post_op_appt_location":     s.post_op_appt_location,
        "post_op_appt_2nd_location": s.post_op_appt_2nd_location,
        "post_op_schedule_required": _post_op_visits_serialized(db, s),
        "auth_status": s.auth_status,
        "auth_number": s.auth_number,
        "clearance_required": bool(s.clearance_required),
        "clearance_status": s.clearance_status,
        "clearance_types": s.clearance_types or [],
        "cardiologist_name":  s.cardiologist_name,
        "cardiologist_phone": s.cardiologist_phone,
        "cardiologist_fax":   s.cardiologist_fax,
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
        # Step-card completion signals
        "benefits_verified_at":      (str(s.benefits_verified_at)
                                        if s.benefits_verified_at else None),
        "labs_sent_to_hospital":     bool(s.labs_sent_to_hospital),
        "device_required":           bool(s.device_required),
        "device_types":              s.device_types or [],
        "device_assigned":           bool(s.device_assigned),
        "payment_posted_to_billing": bool(s.payment_posted_to_billing),
        "calendar_invite_sent_at":   (s.calendar_invite_sent_at.isoformat()
                                        if s.calendar_invite_sent_at else None),
        "operative_report_status":   s.operative_report_status,
        "pathology_status":          s.pathology_status,
        "post_op_call_status":       s.post_op_call_status,
        "latest_boarding_slip": _latest_file(s, kind="boarding_slip"),
        "billing_ai_notes": s.billing_ai_notes,
        "consent_envelopes": [
            {
                "id": str(e.id),
                "template_id": str(e.template_id),
                "template_name": e.template.name if e.template else None,
                "is_supplemental": bool(e.template.is_supplemental) if e.template else False,
                # envelope_id is the display/debugging id. Prefer BoldSign
                # (the live provider per the BoldSign migration) and fall
                # back to DocuSign for legacy envelopes signed before the
                # cutover. Either may be None for envelopes that have only
                # been seeded but not yet sent.
                "envelope_id": (e.boldsign_envelope_id
                                  or e.docusign_envelope_id),
                # Expose both raw fields so any caller that needs the
                # provider-specific id explicitly can pick the right one.
                "boldsign_envelope_id": e.boldsign_envelope_id,
                "docusign_envelope_id": e.docusign_envelope_id,
                "provider": ("boldsign" if e.boldsign_envelope_id
                             else "docusign" if e.docusign_envelope_id
                             else None),
                "status": e.status,
                "sent_at": e.sent_at.isoformat() if e.sent_at else None,
                "signed_at": e.signed_at.isoformat() if e.signed_at else None,
                "declined_at": e.declined_at.isoformat() if e.declined_at else None,
                "voided_at": e.voided_at.isoformat() if e.voided_at else None,
                "last_error": e.last_error,
            } for e in (s.consent_envelopes or [])
        ],
        # Auto-Unresponsive sweep signals (audit #13)
        "last_patient_activity_at": (s.last_patient_activity_at.isoformat()
                                       if s.last_patient_activity_at else None),
        "auto_unresponsive_at":     (s.auto_unresponsive_at.isoformat()
                                       if s.auto_unresponsive_at else None),
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
        "secondary_deductible":      (str(s.secondary_deductible)      if s.secondary_deductible      is not None else None),
        "secondary_deductible_met":  (str(s.secondary_deductible_met)  if s.secondary_deductible_met  is not None else None),
        "secondary_copay":           (str(s.secondary_copay)           if s.secondary_copay           is not None else None),
        "secondary_coinsurance_pct": (str(s.secondary_coinsurance_pct) if s.secondary_coinsurance_pct is not None else None),
        "secondary_oop_max":         (str(s.secondary_oop_max)         if s.secondary_oop_max         is not None else None),
        "secondary_oop_met":         (str(s.secondary_oop_met)         if s.secondary_oop_met         is not None else None),
        "card_on_file":              bool(s.card_on_file),
        "status": s.status,
        "sub_flag": s.sub_flag,
        "is_urgent": s.urgency == "urgent",
        "urgency":   s.urgency,
        "complexity":       s.complexity,
        "duration_minutes": s.duration_minutes,
        "duration_source":  s.duration_source,
        "surgeon_email":    s.surgeon_email,
        "sms_consent":      bool(s.sms_consent),
        "sms_consented_at": s.sms_consented_at.isoformat() if s.sms_consented_at else None,
        "cell_phone":       s.cell_phone,
        "current_step": cur_step["key"] if cur_step else None,
        "current_step_title": cur_step["title"] if cur_step else None,
        # kept for one release so older frontend code doesn't break:
        "current_milestone": cur_step["key"] if cur_step else None,
        "current_milestone_title": cur_step["title"] if cur_step else None,
        "steps": step_engine.compute_steps(s, titles=step_engine.titles_map(db, s)),
        "behind_schedule": behind,
        "hours_overdue": hours_overdue,
        "stuck": s.status == "in_progress" and behind,
        "buckets": sorted(_surgery_buckets(db, s, today)),
        "google_calendar_event_id":    s.google_calendar_event_id,
        "google_calendar_sync_status": s.google_calendar_sync_status,
        "google_calendar_sync_error":  s.google_calendar_sync_error,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
    }
    return out


# ─── Dashboard ──────────────────────────────────────────────────────

@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db),
              current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.VIEW))):
    today = _date.today()
    thirty_days_ago = today - timedelta(days=cfg(db, "completed_window_days"))

    n_completed_30d = db.query(func.count(Surgery.id)).filter(
        Surgery.status == "completed",
        Surgery.updated_at >= thirty_days_ago,
    ).scalar() or 0

    # Walk all non-terminal surgeries once, compute buckets in Python
    rows = (db.query(Surgery)
              .filter(Surgery.status.in_(["new", "in_progress", "hold",
                                            "confirmed", "incomplete"]))
              .all())

    bucket_counts: dict[str, int] = {b: 0 for b in ALL_BUCKETS}
    critical = []
    todo = []
    stuck_count = 0
    for s in rows:
        for b in _surgery_buckets(db, s, today):
            bucket_counts[b] = bucket_counts.get(b, 0) + 1

        behind, hrs = _is_behind_steps(db, s)
        if behind:
            stuck_count += 1
            cur_step = step_engine.current_step(s)
            item = {
                "surgery_id": str(s.id),
                "patient_name": s.patient_name,
                "chart_number": s.chart_number,
                "milestone": cur_step["title"] if cur_step else None,
                "hours_overdue": hrs,
            }
            if hrs > cfg(db, "critical_overdue_hours"):
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
    from app.services.surgery.block_schedule import can_fit, DURATIONS
    FACILITY_PROBE = {"medstar": "robotic_180", "crmc": "minor", "office": "office"}
    next_slots: dict = {"medstar": None, "crmc": None, "office": None}
    upcoming = (db.query(BlockDay)
                  .options(joinedload(BlockDay.slots))
                  .filter(BlockDay.block_date >= today)
                  .order_by(BlockDay.block_date)
                  .limit(cfg(db, "schedule_horizon_days")).all())
    for bd in upcoming:
        if next_slots.get(bd.facility) is not None:
            continue
        probe_kind = FACILITY_PROBE.get(bd.facility)
        if not probe_kind:
            continue
        ok, _reason = can_fit(db, bd, probe_kind)
        if not ok:
            continue
        # Skip blackouts — without this the "next available date" card
        # would offer a date that's been marked unavailable (office-wide,
        # facility-scoped, or provider-scoped).
        if is_date_blacked_out(db, bd.block_date, bd.facility):
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

    # Booking horizon per facility: the latest BlockDay (today or later)
    # that has at least one case booked. Tells the scheduler at a glance
    # how far out we're currently filled.
    from app.models.surgery import SurgerySlot
    horizon_rows = (db.query(BlockDay.facility,
                              func.max(BlockDay.block_date).label("last_date"))
                      .join(SurgerySlot, SurgerySlot.block_day_id == BlockDay.id)
                      .filter(BlockDay.block_date >= today)
                      .group_by(BlockDay.facility)
                      .all())
    booked_through: dict = {"medstar": None, "crmc": None, "office": None}
    for facility, last_date in horizon_rows:
        if facility in booked_through and last_date:
            booked_through[facility] = {
                "block_date": str(last_date),
                "weekday":    last_date.strftime("%A"),
            }

    # Release-alert flags — surface unbooked hospital days + under-booked
    # office days inline on the dashboard so the scheduler sees them
    # without waiting for the daily email.
    from app.services.surgery.release_alerts import (
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

    response = {
        "buckets": bucket_counts,
        "completed_30d": n_completed_30d,
        "stuck_count": stuck_count,
        "critical_alerts": critical[:10],
        "todo": todo[:20],
        "next_slots": next_slots,
        "booked_through": booked_through,
        "hospital_unbooked": hospital_unbooked,
        "office_underbooked": office_underbooked,
    }
    from app.services.surgery.blackout_conflict import find_blocked_conflicts
    response["blocked_conflicts"] = find_blocked_conflicts(db)
    return response


# ─── Dashboard buckets (Phase 2.7) ─────────────────────────────────
# Each surgery can belong to multiple buckets simultaneously — these
# are workload counters, not a state machine. Tiles on the dashboard
# show the count per bucket; clicking a tile filters the list view.

ALL_BUCKETS = [
    "outstanding",
    "incomplete",
    "needs_benefits",
    "needs_prior_auth",
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


def _patient_age(dob: Optional[_date], today: Optional[_date] = None) -> Optional[int]:
    """Calculated age in years on `today`. Returns None if DOB missing."""
    if not dob:
        return None
    today = today or _date.today()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def _preop_needs_repeat(db: Session, s: Surgery) -> bool:
    """True if the pre-op visit is too old (>180 days from surgery date)."""
    if not s.preop_date or not s.scheduled_date:
        return False
    return (s.scheduled_date - s.preop_date).days > cfg(db, "preop_valid_days")


def _post_op_visits_serialized(db: Session, s: Surgery) -> list[dict]:
    """List of post-op visits the practice rules say this surgery needs.
    Pure-data — the frontend uses it to render the right number of date
    inputs on the post-op-appts milestone card."""
    from app.services.post_op_schedule import determine_post_op_schedule
    return [
        {"label": v.label, "days_post_op": v.days_post_op,
         "suggested_location": v.suggested_location,
         "location_locked": v.location_locked}
        for v in determine_post_op_schedule(s, db=db)
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


def _surgery_buckets(db: Session, s: Surgery, today: Optional[_date] = None) -> set[str]:
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

    # Bucket membership is computed from the steps engine (milestones are
    # retired and no longer written). A step that is done or n/a means that
    # piece of work is no longer outstanding.
    _step_state = {st["key"]: st["state"] for st in step_engine.compute_steps(s)}

    def step_done(key: str) -> bool:
        return _step_state.get(key) in ("done", "n/a")

    has_date = s.scheduled_date is not None
    days_until = (s.scheduled_date - today).days if has_date else None
    is_hospital = s.selected_facility in ("medstar", "crmc")

    if not step_done("benefits"):
        buckets.add("needs_benefits")
    if not step_done("prior_auth"):
        buckets.add("needs_prior_auth")
    if _assistant_surgeon_outstanding(s):
        buckets.add("needs_assistant_surgeon")

    # Unresponsive: pre-op visit happened 30+ days ago but the patient
    # hasn't picked a surgery date. Helps schedulers triage stalled patients.
    if (not has_date and s.preop_date
            and (today - s.preop_date).days >= cfg(db, "unresponsive_after_days")):
        buckets.add("unresponsive")

    if has_date:
        buckets.add("date_picked")
        if not step_done("consents"):
            buckets.add("needs_consent")
        if s.clearance_required and s.clearance_status not in (
                "received", "sent_to_hospital", "completed"):
            buckets.add("needs_clearance")
        from app.services.post_op_schedule import all_required_appts_filled
        if not all_required_appts_filled(s, db=db):
            buckets.add("needs_followup_appt")
        # Pre-op stale: exam/labs older than 180 days from surgery date
        if _preop_needs_repeat(db, s):
            buckets.add("needs_repeat_preop")

        # Labs alert: hospital surgery, within 7 days, labs not yet sent
        if (is_hospital and days_until is not None
                and 0 <= days_until <= cfg(db, "labs_alert_window_days")
                and not s.labs_sent_to_hospital):
            buckets.add("needs_labs")

        # Post-op rules — surgery is in the past
        if days_until is not None and days_until < 0:
            days_since = -days_until
            # Spoke-to-pt is the only "done" state for the post-op call
            if (s.post_op_call_status or "") != "Spoke to Pt.":
                buckets.add("needs_post_op_call")
            if (days_since >= cfg(db, "post_op_docs_alert_days")
                    and s.operative_report_status not in (
                        "completed", "received", "not_required")):
                buckets.add("needs_post_op_docs")
            if (s.operative_report_status in ("completed", "received")
                    and not s.payment_posted_to_billing):
                buckets.add("needs_billed")

    return buckets


# ─── Calendar (32-day pre-op readiness view) ───────────────────────

# Pre-op readiness gates on the step engine's PRE_OP_STEP_KEYS_* sets
# (see step_engine). The old milestone-kind set was retired with the
# steps cutover.


def _readiness_indicator(db: Session, s: Surgery) -> tuple[str, list[str], int]:
    """Returns (color, open_step_titles, critical_count).

    color is 'green' | 'yellow' | 'red':
      green   — every pre-op step is done / n/a
      yellow  — at least one pre-op step is still open (todo / in_progress)
      red     — the current step is overdue past the configured grace
    """
    pre_keys = (step_engine.PRE_OP_STEP_KEYS_OFFICE
                if s.selected_facility == "office"
                else step_engine.PRE_OP_STEP_KEYS_HOSPITAL)
    open_titles = [st["title"] for st in step_engine.compute_steps(s)
                   if st["key"] in pre_keys
                   and st["state"] in ("todo", "in_progress")]
    if not open_titles:
        return "green", [], 0
    behind, _hrs = _is_behind_steps(db, s)
    return ("red", open_titles, 1) if behind else ("yellow", open_titles, 0)


@router.get("/calendar")
def calendar(
    days: int = Query(7, ge=1, le=365),
    start_date: Optional[str] = Query(None,
        description="YYYY-MM-DD; if omitted defaults to today"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.VIEW)),
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
        color, open_titles, critical = _readiness_indicator(db, s)
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
            "is_urgent": s.urgency == "urgent",
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
    current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.VIEW)),
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
    q = db.query(Surgery)
    if status and status != "all":
        q = q.filter(Surgery.status == status)
    if facility:
        q = q.filter(Surgery.selected_facility == facility)
    if urgent_only:
        q = q.filter(Surgery.urgency == "urgent")
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
                if (step_engine.current_step(s) or {}).get("key") == milestone]
    if bucket:
        if bucket not in ALL_BUCKETS:
            raise HTTPException(status_code=422, detail=f"unknown bucket: {bucket}")
        today = _date.today()
        rows = [s for s in rows if bucket in _surgery_buckets(db, s, today)]
    if behind_only:
        rows = [s for s in rows if _is_behind_steps(db, s)[0]]
    if preop_needs_repeat is not None:
        rows = [s for s in rows if _preop_needs_repeat(db, s) == preop_needs_repeat]
    if age_min is not None:
        rows = [s for s in rows
                if _patient_age(s.dob) is not None and _patient_age(s.dob) >= age_min]
    if age_max is not None:
        rows = [s for s in rows
                if _patient_age(s.dob) is not None and _patient_age(s.dob) <= age_max]

    # Sort: urgent first, then most behind, then by created_at desc
    rows.sort(key=lambda s: (
        0 if s.urgency == "urgent" else 1,
        -_is_behind_steps(db, s)[1],
        -(s.created_at.timestamp() if s.created_at else 0),
    ))

    total = len(rows)
    paged = rows[(page - 1) * per_page : page * per_page]

    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "surgeries": [_surgery_dict(db, s) for s in paged],
    }


# ─── Upload + parse a new surgery order ─────────────────────────────

@router.post("/orders/upload", status_code=201)
async def upload_order(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.WORK)),
):
    """Upload a ModMed surgery-order PDF. Parses it via Claude, creates a
    Surgery row with status='incomplete' so the scheduler can review the
    extracted fields before flipping to 'new' and generating milestones."""
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=422, detail="Expected a PDF file")

    from app.services.surgery.order_parser import (
        parse_order_text, build_surgery_kwargs, extract_pdf_text_from_bytes,
    )

    contents = await file.read()
    try:
        text = extract_pdf_text_from_bytes(contents)
        if len(text) < 50:
            raise ValueError("PDF text content is empty — is this a scanned image?")
        parsed = parse_order_text(text)
        kwargs = build_surgery_kwargs(parsed)
    except Exception as exc:
        raise HTTPException(status_code=422,
                            detail=f"Could not parse this PDF: {exc}. "
                                   "Try manually creating the surgery instead.")

    pdf_key = save_blob(prefix="surgery-orders", body=contents,
                            filename=file.filename or "order.pdf")

    # Sanity-check minimum required fields
    if not kwargs.get("chart_number") or not kwargs.get("patient_name"):
        raise HTTPException(status_code=422,
                            detail="Parser couldn't extract patient identity. "
                                   "Try manually creating the surgery instead.")

    # Per-chart serialization (Fable surgery audit Low). Two parallel
    # upload_order calls with the same chart_number both used to pass
    # the "existing surgery?" check and create duplicate Surgery rows.
    # A Postgres advisory lock keyed on the chart number serializes
    # uploads per chart without needing a new DB constraint that would
    # fail to migrate over existing duplicates.
    import hashlib
    _lock_key = int(hashlib.sha1(
        kwargs["chart_number"].encode("utf-8")).hexdigest()[:8], 16)
    # 32-bit advisory key — held until the end of the transaction
    db.execute(text("SELECT pg_advisory_xact_lock(:k)"),
                 {"k": _lock_key & 0x7FFFFFFF})

    # If a demographics-only row exists for this chart (from the bulk
    # roster import), merge the parsed surgery fields into it rather than
    # creating a duplicate.
    existing = (db.query(Surgery)
                  .filter(Surgery.chart_number == kwargs["chart_number"],
                          Surgery.status.notin_(["cancelled", "completed"]))
                  .first())
    if existing:
        if existing.sub_flag == "candidate_imported":
            # Order PDF wins for surgery-specific fields; keep existing
            # demographics (phone/email/address) since they came from
            # ModMed and may be richer than the order header.
            _ORDER_FIELDS = {
                "patient_name", "first_name", "last_name", "dob",
                "primary_insurance", "primary_member_id",
                "secondary_insurance", "secondary_member_id",
                "procedures", "diagnoses",
                "eligible_facilities", "estimated_minutes",
                "is_robotic", "is_urgent",
                "surgeon_primary", "scheduled_at",
            }
            for k, v in kwargs.items():
                if k in _ORDER_FIELDS and v not in (None, "", [], {}):
                    setattr(existing, k, v)
            existing.order_pdf_path = pdf_key
            existing.sub_flag = None
            db.add(SurgeryFile(
                surgery_id=existing.id,
                kind="order",
                filename=file.filename or "order.pdf",
                path=pdf_key,
                mime_type="application/pdf",
                size_bytes=len(contents),
                uploaded_by=current_user.get("email"),
            ))
            from app.services.surgery.local_helpers import (
                upsert_patient_directory, maybe_assign_surgery_number,
            )
            maybe_assign_surgery_number(db, existing)
            upsert_patient_directory(db, existing)
            db.commit(); db.refresh(existing)
            return {
                "duplicate": False,
                "merged": True,
                "id": str(existing.id),
                "status": existing.status,
                "extracted": kwargs,
                "message": (f"Mapped surgery order onto existing demographics row for "
                            f"{existing.patient_name} (chart {existing.chart_number}). "
                            "Review the merged fields, then mark as 'new'."),
            }
        # Real duplicate — surface for the scheduler to decide
        return {
            "duplicate": True,
            "existing_id": str(existing.id),
            "existing_status": existing.status,
            "extracted": kwargs,
            "message": (f"Patient {kwargs['patient_name']} (chart {kwargs['chart_number']}) "
                        f"already has an open surgery (#{existing.surgery_number or existing.id}). "
                        "Open it to add this order, or confirm to create a new one."),
        }

    from app.services.surgery.local_helpers import (
        upsert_patient_directory, maybe_assign_surgery_number,
    )
    s = Surgery(
        **kwargs,
        order_pdf_path=pdf_key,
        created_by=current_user.get("email"),
    )
    db.add(s); db.flush()
    maybe_assign_surgery_number(db, s)
    upsert_patient_directory(db, s)
    # Also surface the order PDF in surgery_files so the detail page's
    # "Order PDF" card can link to it. order_pdf_path on the surgery row
    # is the source of truth; this row is a UI mirror.
    db.add(SurgeryFile(
        surgery_id=s.id,
        kind="order",
        filename=file.filename or "order.pdf",
        path=pdf_key,
        mime_type="application/pdf",
        size_bytes=len(contents),
        uploaded_by=current_user.get("email"),
    ))
    db.commit(); db.refresh(s)
    return {
        "duplicate": False,
        "id": str(s.id),
        "status": s.status,
        "extracted": kwargs,
        "message": (f"Created surgery for {s.patient_name} in 'incomplete' status. "
                    "Review the extracted fields, fill any gaps, then mark as 'new'."),
    }


# ─── Parse-only order extract (prefill manual intake, no DB write) ──

@router.post("/orders/extract")
async def extract_order(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.WORK)),
):
    """Parse a ModMed surgery-order PDF and return the extracted fields for
    prefilling the manual intake form. Runs the SAME parse as
    /orders/upload but writes NOTHING to the DB and does not reject on
    missing fields — partial extractions come back with a warning so the
    coordinator can fill the gaps by hand."""
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=422, detail="Expected a PDF file")

    from app.services.surgery.order_parser import (
        parse_order_text, build_surgery_kwargs, extract_pdf_text_from_bytes,
    )

    contents = await file.read()
    warnings: list[str] = []

    # Read the PDF text. If we can't read anything at all, there's nothing
    # to prefill — mirror upload_order's 422 (not a PDF / empty).
    try:
        text_body = extract_pdf_text_from_bytes(contents)
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Could not read this PDF: {exc}. "
                   "Create the surgery manually instead.")

    if len(text_body) < 50:
        # Likely a scanned image — flag it but don't hard-fail; the form
        # can still be filled by hand.
        warnings.append("PDF appears to be a scanned image — extraction may be incomplete.")

    # Parse + build kwargs. A partial/failed parse returns whatever we have
    # (possibly nothing) plus a warning rather than erroring.
    kwargs: dict = {}
    parsed: dict | None = None
    try:
        parsed = parse_order_text(text_body)
        kwargs = build_surgery_kwargs(parsed)
    except Exception as exc:
        warnings.append(f"Automatic extraction failed ({exc}); leave blank for manual entry.")

    # Map build_surgery_kwargs output → ManualSurgeryIn field names, keeping
    # only non-empty values and JSON-serializing dates.
    def _ser(v):
        if isinstance(v, (_date, datetime)):
            return v.strftime("%Y-%m-%d")
        return v

    # Most keys share names with ManualSurgeryIn; the parser doesn't emit
    # an is_urgent flag, so derive it from the parsed priority.
    KEYS = (
        "chart_number", "patient_name", "first_name", "last_name", "dob",
        "phone", "email",
        "address_street", "address_city", "address_state", "address_zip",
        "primary_insurance", "primary_member_id",
        "secondary_insurance", "secondary_member_id",
        "surgeon_primary", "surgery_name",
        "procedures", "diagnoses", "eligible_facilities",
        "estimated_minutes", "is_robotic", "is_urgent",
    )
    fields: dict = {}
    for k in KEYS:
        if k in kwargs:
            v = _ser(kwargs[k])
            if v not in (None, "", [], {}):
                fields[k] = v

    # Fields the parser produces but build_surgery_kwargs doesn't map 1:1 to
    # ManualSurgeryIn names — populate them explicitly, only when non-empty.
    parsed = parsed or {}

    # payer_id: from the parsed insurance (via build_surgery_kwargs), falling
    # back to the raw parsed insurance block.
    payer_id = kwargs.get("primary_payer_id")
    if payer_id in (None, "", [], {}):
        payer_id = ((parsed.get("insurance_primary") or {}).get("payer_id"))
    if payer_id not in (None, "", [], {}):
        fields["payer_id"] = _ser(payer_id)
        # Resolve payer_id → canonical picklist company via the configurable
        # map so the insurance dropdown prefills. The raw extracted company
        # (e.g. "BCBS Administrators PPO ONLY") never matches a dropdown
        # option; the mapped value (e.g. "Blue Cross & Blue Shield PPO")
        # does. If the payer ID isn't mapped, leave the raw company as-is.
        try:
            pid_map = cfg(db, "payer_id_insurance_map") or {}
            mapped = pid_map.get(str(payer_id))
            if mapped:
                fields["primary_insurance"] = mapped
        except Exception:
            pass

    # surgery_name: prefer the headline procedure_type, else the first
    # procedure's description.
    if "surgery_name" not in fields:
        surgery_name = parsed.get("procedure_type")
        if not surgery_name:
            procs = parsed.get("procedures") or []
            if procs and isinstance(procs[0], dict):
                surgery_name = procs[0].get("description")
        if surgery_name not in (None, "", [], {}):
            fields["surgery_name"] = surgery_name

    # preop_date: the date portion (YYYY-MM-DD) of the order's create date.
    ordered_at = parsed.get("ordered_at")
    if ordered_at:
        try:
            preop = str(ordered_at)[:10]
            # Validate it's a real date before surfacing it.
            datetime.strptime(preop, "%Y-%m-%d")
            fields["preop_date"] = preop
        except (ValueError, TypeError):
            pass

    # is_urgent isn't produced by build_surgery_kwargs — derive from the
    # parsed order priority when available.
    if "is_urgent" not in fields:
        try:
            if str((parsed or {}).get("priority") or "").strip().lower() in ("urgent", "stat", "emergent"):
                fields["is_urgent"] = True
        except Exception:
            pass

    return {"fields": fields, "warnings": warnings}


# ─── Manual create (no PDF) ─────────────────────────────────────────

class ManualSurgeryIn(BaseModel):
    """All fields required EXCEPT secondary insurance / member ID and notes —
    a patient may not have a secondary policy."""
    chart_number: str
    patient_name: str           # "Last, First"
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    dob: str
    phone: str
    email: str
    address_street: str
    address_city: str
    address_state: str
    address_zip: str
    primary_insurance: str
    primary_member_id: str
    payer_id: Optional[str] = None
    secondary_insurance: Optional[str] = None
    secondary_member_id: Optional[str] = None
    surgeon_primary: str
    assistant_surgeon_name: Optional[str] = None
    clearance_types: list[str] = []
    device_types: list[str] = []
    surgery_name: str           # display label for the procedure picked from the dropdown
    procedures: list[dict] = []  # [{cpt, description}]
    diagnoses: list[dict] = []
    eligible_facilities: list[str] = []
    estimated_minutes: int
    preop_date: str             # surgeon pre-op visit
    is_robotic: bool = False
    is_urgent: bool = False
    notes: Optional[str] = None


@router.post("/manual", status_code=201)
def create_manual(payload: ManualSurgeryIn,
                   db: Session = Depends(get_db),
                   current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.WORK))):
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

    try:
        dob = datetime.strptime(payload.dob[:10], "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=422, detail="dob must be YYYY-MM-DD")
    # DOB sanity: future dates and absurd ages corrupt age-based
    # routing (peds dose tables, patient-portal soft-auth keyed on DOB,
    # consent age gates). 1900..today is the OB/GYN-realistic envelope.
    # (Fable surgery audit Low.)
    if dob > _date.today() or dob.year < 1900:
        raise HTTPException(
            status_code=422,
            detail=f"dob {dob} is outside the acceptable range (1900-01-01..today)")
    try:
        preop_date = datetime.strptime(payload.preop_date[:10], "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=422, detail="preop_date must be YYYY-MM-DD")

    # Surgeon defaults to the practice surgeon when left blank.
    first_name = (payload.first_name or "").strip() or None
    last_name = (payload.last_name or "").strip() or None
    surgeon_primary = (payload.surgeon_primary or "").strip() or "Aryian Cooke, MD"

    # Name: prefer the split first/last when both are present, composing
    # "Last, First". Otherwise keep whatever patient_name the client sent.
    if first_name and last_name:
        patient_name = f"{last_name}, {first_name}"
    else:
        patient_name = (payload.patient_name or "").strip()

    # Intake multi-selects: strip + dedupe (preserve order); None when empty.
    def _clean_list(items):
        out, seen = [], set()
        for it in (items or []):
            s = (it or "").strip()
            if s and s not in seen:
                seen.add(s)
                out.append(s)
        return out or None

    clearance_types = _clean_list(payload.clearance_types)
    device_types = _clean_list(payload.device_types)

    def _non_none(items):
        # Entries that are actual selections, not the "None" sentinel
        # (case-insensitive). Used to decide whether a workflow flag arms.
        return [it for it in (items or []) if it.strip().lower() != "none"]

    assistant_raw = (payload.assistant_surgeon_name or "").strip()
    assistant_name = None if assistant_raw.lower() in ("", "none") else assistant_raw

    # patient_name and surgeon_primary are validated against the computed
    # values (composed name / defaulted surgeon), not the raw payload.
    required = [
        ("chart_number",       "Chart number"),
        ("phone",              "Phone"),
        ("email",              "Email"),
        ("address_street",     "Street address"),
        ("address_city",       "City"),
        ("address_state",      "State"),
        ("address_zip",        "ZIP code"),
        ("primary_insurance",  "Primary insurance"),
        ("primary_member_id",  "Primary member ID"),
        ("surgery_name",       "Surgery name"),
    ]
    for fname, label in required:
        if not (getattr(payload, fname) or "").strip():
            raise HTTPException(status_code=422, detail=f"{label} is required")
    if not patient_name:
        raise HTTPException(status_code=422, detail="Patient name is required")
    if not payload.estimated_minutes or payload.estimated_minutes <= 0:
        raise HTTPException(status_code=422, detail="Estimated minutes is required")
    if not payload.eligible_facilities:
        raise HTTPException(status_code=422, detail="At least one eligible facility is required")
    if not [p for p in payload.procedures if (p.get("cpt") or p.get("description"))]:
        raise HTTPException(status_code=422, detail="At least one procedure (CPT) is required")
    if not [d for d in payload.diagnoses if (d.get("icd") or d.get("description"))]:
        raise HTTPException(status_code=422, detail="At least one diagnosis (ICD-10) is required")

    s = Surgery(
        chart_number=payload.chart_number.strip(),
        patient_name=patient_name,
        first_name=first_name,
        last_name=last_name,
        dob=dob,
        phone=payload.phone,
        cell_phone=payload.phone,
        email=payload.email,
        address_street=payload.address_street.strip(),
        address_city=payload.address_city.strip(),
        address_state=payload.address_state.strip(),
        address_zip=payload.address_zip.strip(),
        primary_insurance=payload.primary_insurance,
        primary_member_id=payload.primary_member_id,
        primary_payer_id=payload.payer_id,
        secondary_insurance=payload.secondary_insurance,
        secondary_member_id=payload.secondary_member_id,
        surgeon_primary=surgeon_primary,
        procedures=payload.procedures or [],
        diagnoses=payload.diagnoses or [],
        eligible_facilities=eligible,
        selected_facility=selected,
        estimated_minutes=payload.estimated_minutes,
        is_robotic=payload.is_robotic,
        procedure_classification=classification,
        preop_date=preop_date,
        urgency=("urgent" if payload.is_urgent else "routine"),
        notes=payload.notes,
        status="incomplete",
        source="manual",
        created_by=current_user.get("email"),
    )

    # Assistant surgeon (dropdown that may be "None"). A real name arms
    # the assistant-surgeon workflow; "None"/blank leaves it disabled.
    if assistant_name:
        s.assistant_surgeon_name = assistant_name
        s.assistant_surgeon_required = True

    # Clearances: persist the selected list as-is. Only arm the clearance
    # workflow ("required" is the not-yet-cleared value the rest of the
    # app uses) when at least one real (non-"None") clearance is selected.
    if clearance_types:
        s.clearance_types = clearance_types
        if _non_none(clearance_types):
            s.clearance_required = True
            s.clearance_status = "required"

    # Devices: persist the selected list as-is. Only arm the device
    # workflow + set the back-compat device_kind (first real device) when
    # at least one non-"None" device is selected.
    if device_types:
        s.device_types = device_types
        real_devices = _non_none(device_types)
        if real_devices:
            s.device_required = True
            s.device_kind = real_devices[0]

    db.add(s); db.flush()
    from app.services.surgery.local_helpers import (
        upsert_patient_directory, maybe_assign_surgery_number,
    )
    maybe_assign_surgery_number(db, s)
    upsert_patient_directory(db, s)
    db.commit(); db.refresh(s)
    return _surgery_dict(db, s)


# ─── Picklists (must be declared BEFORE /{surgery_id} so it isn't eaten as a path-param) ─

@router.get("/picklists")
def get_picklists(current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.VIEW))):
    """Return the curated dropdown options for SurgeryDetail editing."""
    from app.services.surgery.picklists import all_picklists
    return all_picklists()


# Must be registered BEFORE the `/{surgery_id}` wildcard below — otherwise
# FastAPI matches `scheduler-alerts` as a surgery_id UUID and the wildcard
# 404s on the lookup. (Same applies to any future static GETs added to
# this router.)
@router.get("/scheduler-alerts")
def scheduler_alerts(db: Session = Depends(get_db),
                      current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.WORK))):
    """Surfaces actionable scheduling alerts for the surgery scheduler's
    checklist. Currently:
      - Office procedure days within 14 days that have <6 cases booked
        (Dr. Cooke's day isn't full — release the rest for clinic).
    """
    # Threshold lives in SurgeryConfig (`office_full_threshold`) with a
    # default of 6; the release_alerts service is the source of truth.
    from app.services.surgery.release_alerts import _cfg
    threshold = int(_cfg(db, "office_full_threshold"))
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
        if booked >= threshold:
            continue
        # Skip blackouts — staff shouldn't be told to fill a day that's
        # already marked unavailable.
        if is_date_blacked_out(db, bd.block_date, bd.facility):
            continue
        underbooked.append({
            "block_day_id": str(bd.id),
            "block_date":   str(bd.block_date),
            "weekday":      bd.block_date.strftime("%A"),
            "facility":     bd.facility,
            "booked":       booked,
            "threshold":    threshold,
            "open_slots":   threshold - booked,
            "days_out":     (bd.block_date - today).days,
            "alerted_at":   (bd.release_alert_sent_at.isoformat()
                              if bd.release_alert_sent_at else None),
        })
    return {
        "office_underbooked": underbooked,
        "threshold": threshold,
    }


# ─── Detail + edit + milestone advance ──────────────────────────────

@router.get("/{surgery_id}")
def get_surgery(surgery_id: str, db: Session = Depends(get_db),
                 current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.VIEW))):
    s = (db.query(Surgery)
           .filter(Surgery.id == surgery_id)
           .first())
    if not s:
        raise HTTPException(status_code=404, detail="surgery not found")
    # PHI access logging (Fable surgery audit H2). Every individual chart
    # read writes a row to the central audit log so an incident query
    # ("who looked at this patient?") can be answered.
    log_action(
        db,
        action="CHART_VIEW",
        resource_type="surgery",
        resource_id=str(s.id),
        patient_id=s.chart_number or None,
        user_id=(current_user.get("email") or "").lower() or None,
        user_name=current_user.get("name") or current_user.get("email"),
        description=f"Viewed surgery chart {s.surgery_number or s.id} ({s.patient_name})",
    )
    out = _surgery_dict(db, s)
    # Expose the booked slot so the frontend can offer duration inline edit (Phase D6)
    slot = (db.query(SurgerySlot)
              .filter(SurgerySlot.surgery_id == s.id)
              .order_by(SurgerySlot.start_time)
              .first())
    out["booked_slot_id"] = str(slot.id) if slot else None
    out["booked_duration_minutes"] = slot.duration_minutes if slot else None
    return out


class SurgeryPatch(BaseModel):
    """Fields a scheduler can edit on a surgery row directly."""
    chart_number: Optional[str] = None
    patient_name: Optional[str] = None
    first_name: Optional[str] = None
    middle_initial: Optional[str] = None
    last_name: Optional[str] = None
    dob: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address_street: Optional[str] = None
    address_city: Optional[str] = None
    address_state: Optional[str] = None
    address_zip: Optional[str] = None
    primary_insurance: Optional[str] = None
    primary_member_id: Optional[str] = None
    primary_payer_id: Optional[str] = None
    primary_group: Optional[str] = None
    secondary_insurance: Optional[str] = None
    secondary_member_id: Optional[str] = None
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
    clearance_types: Optional[list[str]] = None
    cardiologist_name: Optional[str] = None
    cardiologist_phone: Optional[str] = None
    cardiologist_fax: Optional[str] = None
    sterilization_consent_required: Optional[bool] = None
    sterilization_consent_status: Optional[str] = None
    deductible:             Optional[DollarAmount] = None
    copay:                  Optional[DollarAmount] = None
    allowed_amount:         Optional[DollarAmount] = None
    patient_responsibility: Optional[DollarAmount] = None
    amount_paid:            Optional[DollarAmount] = None
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
    urgency: Optional[str] = None
    # Phase F fields
    complexity:       Optional[str] = None
    duration_minutes: Optional[int] = None
    duration_source:  Optional[str] = None
    surgeon_email:    Optional[str] = None
    # Phase J4 — SMS opt-in
    sms_consent:      Optional[bool] = None
    cell_phone:       Optional[str]  = None
    # Pre-op lab appointment (patient typically reports this on portal,
    # but staff can backfill via this PATCH if patient called in)
    lab_appointment_date: Optional[str] = None
    # Pre-op visit with the surgeon (anchors the Unresponsive 30-day clock)
    preop_date: Optional[str] = None
    # Step-completion columns the milestone→steps cutover orphaned. These
    # feed step_engine._state() so staff can complete the step directly.
    # (Fable surgery audit #4, #13, #14, #16.)
    labs_sent_to_hospital:   Optional[bool] = None   # labs step
    post_op_call_status:     Optional[str]  = None    # welfare_fu step ("Spoke to Pt.")
    operative_report_status: Optional[str]  = None    # notes_reports step
    device_required:         Optional[bool] = None    # device step
    device_assigned:         Optional[bool] = None    # device step
    device_types:            Optional[list[str]] = None  # multi-select device list


# ─── Slot duration patch (Phase D3) ────────────────────────────────

class SlotPatch(BaseModel):
    duration_minutes: int
    override_reason: Optional[str] = None


@router.patch("/slots/{slot_id}")
def patch_slot(
    slot_id: str,
    payload: SlotPatch,
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.WORK)),
):
    from app.models.surgery import SurgeryNote
    slot = db.query(SurgerySlot).filter(SurgerySlot.id == slot_id).first()
    if not slot:
        raise HTTPException(status_code=404, detail="slot not found")

    new_dur = payload.duration_minutes
    if new_dur <= 0:
        raise HTTPException(status_code=422, detail="duration must be > 0")
    if not (payload.override_reason or "").strip():
        raise HTTPException(status_code=422, detail="override_reason required")

    # Hard ceiling — no surgery is ever >8 hours.
    if new_dur > 480:
        raise HTTPException(status_code=422,
                            detail="duration may not exceed 480 minutes (8h)")

    # Lock the BlockDay row for the duration of this transaction so two
    # concurrent duration edits can't both pass the overlap check.
    # Without this, T1 reads the existing slots, T2 reads the same set,
    # T1 commits an extended slot, then T2 commits an overlapping one —
    # patch_slot inherited the same TOCTOU window as the booking paths
    # before C1 was fixed. (Fable surgery audit M2.)
    bd = (db.query(BlockDay)
            .filter(BlockDay.id == slot.block_day_id)
            .with_for_update()
            .first())
    if bd:
        from datetime import datetime as _dt, timedelta as _td
        slot_start_dt = _dt.combine(bd.block_date, slot.start_time)
        block_end_dt  = _dt.combine(bd.block_date, bd.end_time)
        max_dur = int((block_end_dt - slot_start_dt).total_seconds() // 60)
        if max_dur > 0 and new_dur > max_dur:
            raise HTTPException(
                status_code=422,
                detail=f"duration would extend past block day end ({bd.end_time}); "
                       f"max from this slot is {max_dur} min")

    # Conflict check: ensure the new (start, new_duration) doesn't
    # overlap another slot. Evaluated *after* the row lock so we see any
    # slot the racing transaction just committed.
    conflict = overlapping_slot(db, slot.block_day_id, slot.start_time, new_dur,
                                exclude_slot_id=slot.id)
    if conflict:
        raise HTTPException(
            status_code=409,
            detail=f"new duration overlaps an existing slot at "
                   f"{conflict.start_time.strftime('%H:%M')} "
                   f"({conflict.duration_minutes} min)",
        )

    actor = current_user.get("email") or "system"
    old = slot.duration_minutes
    slot.duration_minutes = new_dur

    if slot.surgery_id:
        db.add(SurgeryNote(
            surgery_id=slot.surgery_id, created_by=actor,
            content=(f"Duration {old} → {new_dur} min. "
                     f"Reason: {payload.override_reason}"),
        ))
    db.commit()
    try:
        if slot.surgery_id:
            from app.services.google_calendar_sync import upsert_event_for_surgery
            from app.models.surgery import Surgery as _Surgery
            surgery = db.query(_Surgery).filter(_Surgery.id == slot.surgery_id).first()
            if surgery:
                upsert_event_for_surgery(db, surgery)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("calendar sync failed: %s", e)
    return {"ok": True, "slot_id": str(slot.id),
            "duration_minutes": slot.duration_minutes}


@router.patch("/{surgery_id}")
def patch_surgery(surgery_id: str, payload: SurgeryPatch,
                   db: Session = Depends(get_db),
                   current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.WORK))):
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="surgery not found")

    data = payload.model_dump(exclude_unset=True)

    if "urgency" in data:
        if data["urgency"] not in SURGERY_URGENCY_VALUES:
            raise HTTPException(status_code=422,
                                detail=f"unknown urgency: {data['urgency']}")

    if "complexity" in data:
        if data["complexity"] not in SURGERY_COMPLEXITY_VALUES:
            raise HTTPException(status_code=422,
                                detail=f"unknown complexity: {data['complexity']}")
    if "status" in data:
        if data["status"] not in SURGERY_STATUS_VALUES:
            raise HTTPException(status_code=422,
                                detail=f"unknown status: {data['status']}")
        # Block transitions that bypass dedicated pipelines. Going to
        # 'cancelled' must run cancel_surgery (SurgeryCancellation row,
        # BoldSign envelope void, slot release, audit log, calendar
        # delete). 'completed' is a post-op terminal state that no
        # caller in the codebase sets via PATCH today; keep it locked
        # behind a dedicated endpoint when one is built.
        if data["status"] in ("cancelled", "completed"):
            raise HTTPException(status_code=409,
                detail=("cannot set status via PATCH — use "
                        f"POST /surgery/{{id}}/cancel for 'cancelled'; "
                        "'completed' is not yet reachable via API"))
        # Hold/unresponsive transitions also need the cancellation
        # pipeline so the slot is released, BoldSign envelopes are
        # voided, and a SurgeryCancellation audit row is written. Going
        # via PATCH leaves the slot on the OR schedule. (Fable surgery
        # audit H5.)
        if data["status"] in ("hold", "unresponsive") and s.status != data["status"]:
            raise HTTPException(status_code=409,
                detail=("cannot set status via PATCH — use "
                        f"POST /surgery/{{id}}/cancel with the matching reason "
                        "so the slot is released and audit row is written"))
    if "selected_facility" in data and data["selected_facility"] is not None:
        if data["selected_facility"] not in SURGERY_FACILITY_VALUES:
            raise HTTPException(status_code=422,
                                detail=f"unknown facility: {data['selected_facility']}")
    if "estimated_minutes" in data and data["estimated_minutes"] is not None:
        m = data["estimated_minutes"]
        if m <= 0 or m > SURGERY_MAX_MINUTES:
            raise HTTPException(
                status_code=422,
                detail=f"estimated_minutes must be 1..{SURGERY_MAX_MINUTES}",
            )
    if "duration_minutes" in data:
        if data["duration_minutes"] is not None and data["duration_minutes"] <= 0:
            raise HTTPException(status_code=422, detail="duration_minutes must be > 0")
    if "duration_source" in data:
        if data["duration_source"] is not None and data["duration_source"] not in SURGERY_DURATION_SOURCES:
            raise HTTPException(status_code=422,
                                detail=f"unknown duration_source: {data['duration_source']}")
    if "surgeon_email" in data:
        em = (data["surgeon_email"] or "").strip().lower() or None
        data["surgeon_email"] = em

    # operative_report_status is a NOT NULL enum-ish column. Accept only the
    # canonical values the importer + step engine use; reject NULL so we never
    # violate the column constraint. (Fable surgery audit #14.)
    if "operative_report_status" in data:
        ors = data["operative_report_status"]
        if ors is None or str(ors).lower() not in (
                "not_received", "not_required", "received", "completed"):
            raise HTTPException(
                status_code=422,
                detail=("operative_report_status must be one of "
                        "not_received / not_required / received / completed"))
        data["operative_report_status"] = str(ors).lower()

    # Auto-attribute duration_source when only duration_minutes is patched
    if "duration_minutes" in data and "duration_source" not in data:
        data["duration_source"] = "coordinator"

    # DOB string → date
    if "dob" in data and data["dob"]:
        try:
            data["dob"] = datetime.strptime(data["dob"][:10], "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=422, detail="dob must be YYYY-MM-DD")
        # Same envelope as create_manual (Fable surgery audit Low).
        if data["dob"] > _date.today() or data["dob"].year < 1900:
            raise HTTPException(
                status_code=422,
                detail=(f"dob {data['dob']} is outside the acceptable "
                        f"range (1900-01-01..today)"))
    elif "dob" in data:
        data["dob"] = None

    # scheduled_date / scheduled_start_time are not editable via the
    # generic PATCH — the booking pipeline (coordinator_schedule,
    # waitlist_claim, patient_picks, self-schedule) is the single
    # writer because the Surgery row and its SurgerySlot must agree on
    # date / start / facility and the BlockDay row lock must arbitrate
    # concurrent bookings. A direct PATCH desynchronizes Surgery ↔
    # SurgerySlot and bypasses blackout / capacity / overlap checks.
    # (Fable surgery audit H6.)
    if data.get("scheduled_date") is not None or data.get("scheduled_start_time") is not None:
        raise HTTPException(
            status_code=409,
            detail=("scheduled_date / scheduled_start_time are not editable via PATCH — "
                    "use POST /surgery/{id}/schedule (coordinator), "
                    "the patient picker, or POST /surgery/{id}/cancel + reschedule. "
                    "PATCH bypasses the BlockDay capacity lock and blackout checks."))
    # Drop these keys so they can't slip through setattr below either.
    data.pop("scheduled_date", None)
    data.pop("scheduled_start_time", None)

    # (scheduled_date/scheduled_start_time blocked above — no PATCH path)

    # Money columns must flow through the ledgered payment endpoints,
    # never the generic PATCH. PATCH at WORK-tier had no audit, no
    # SurgeryPayment row, and let any staffer mutate amount_paid /
    # patient_responsibility to any value 0..$50k. (Fable surgery
    # audit H1.) The legitimate writers are
    # POST /surgery/{id}/payments/manual and the benefits calculator.
    if data.get("amount_paid") is not None or data.get("patient_responsibility") is not None:
        raise HTTPException(
            status_code=409,
            detail=("amount_paid / patient_responsibility are not editable via PATCH — "
                    "use POST /surgery/{id}/payments/manual to record a payment, "
                    "or POST /surgery/{id}/benefits to recalculate responsibility"))
    data.pop("amount_paid", None)
    data.pop("patient_responsibility", None)

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

    # Pre-op visit date (with the surgeon)
    if "preop_date" in data:
        if data["preop_date"]:
            try:
                data["preop_date"] = datetime.strptime(
                    data["preop_date"][:10], "%Y-%m-%d").date()
            except ValueError:
                raise HTTPException(status_code=422,
                                    detail="preop_date must be YYYY-MM-DD")
        else:
            data["preop_date"] = None

    # Lab appointment date — staff backfill of patient self-report
    if "lab_appointment_date" in data:
        from datetime import datetime as _dt
        if data["lab_appointment_date"]:
            try:
                data["lab_appointment_date"] = _dt.strptime(
                    data["lab_appointment_date"][:10], "%Y-%m-%d").date()
            except ValueError:
                raise HTTPException(status_code=422,
                                    detail="lab_appointment_date must be YYYY-MM-DD")
            s.lab_appointment_reported_at = _dt.utcnow()
            s.lab_appointment_reported_by = f"staff:{current_user.get('email') or 'system'}"
        else:
            data["lab_appointment_date"] = None
            s.lab_appointment_reported_at = None
            s.lab_appointment_reported_by = None

    # Convert legacy is_urgent shim → urgency enum
    if "is_urgent" in data:
        s.urgency = "urgent" if data.pop("is_urgent") else "routine"

    # SMS consent — stamp/clear audit fields alongside the boolean
    if "sms_consent" in data:
        from datetime import datetime as _dt
        if data["sms_consent"]:
            s.sms_consented_at = _dt.utcnow()
            s.sms_consented_by = current_user.get("email") or "system"
        else:
            s.sms_consented_at = None
            s.sms_consented_by = None
    if "cell_phone" in data:
        v = (data["cell_phone"] or "").strip()
        data["cell_phone"] = v or None

    # Capture before-state snapshot of the PHI / clinical / financial
    # fields the staff are about to change. Filtered to the keys actually
    # in the payload so we don't bloat audit rows with untouched columns.
    # (Fable surgery audit H2.)
    # If the staff edited first_name / last_name / middle_initial without
    # also setting patient_name explicitly, rebuild patient_name from the
    # post-update first+last so the displayed name stays consistent with
    # the structured fields. Format mirrors _format_patient_name in
    # candidate_import.py — "Last, First" with title-case.
    name_part_changed = any(
        k in data for k in ("first_name", "middle_initial", "last_name"))
    if name_part_changed and "patient_name" not in data:
        next_first = (data.get("first_name") if "first_name" in data
                       else s.first_name) or ""
        next_last  = (data.get("last_name")  if "last_name"  in data
                       else s.last_name)  or ""
        next_first = next_first.strip()
        next_last  = next_last.strip()
        if next_last and next_first:
            data["patient_name"] = f"{next_last.title()}, {next_first.title()}"
        elif next_last:
            data["patient_name"] = next_last.title()
        elif next_first:
            data["patient_name"] = next_first.title()

    _audit_field_set = {
        "patient_name", "first_name", "middle_initial", "last_name",
        "dob", "address", "city", "state", "zip_code",
        "cell_phone", "home_phone", "email",
        "primary_insurance", "insurance_member_id", "secondary_insurance",
        "diagnosis_primary", "diagnosis_secondary", "procedure_primary",
        "procedure_classification", "estimated_minutes", "duration_minutes",
        "surgeon_primary", "surgeon_email", "selected_facility",
        "auth_status", "auth_number", "clearance_status", "clearance_required",
        "fmla_required", "fmla_fee_paid", "status",
    }
    audit_diff = {}
    for k in (set(data.keys()) & _audit_field_set):
        before_val = getattr(s, k, None)
        new_val = data[k]
        if before_val != new_val:
            audit_diff[k] = {
                "before": (str(before_val) if before_val is not None else None),
                "after":  (str(new_val) if new_val is not None else None),
            }

    # Multi-select list fields (clearance_types / device_types) and the
    # assistant-surgeon name need the same derived-flag logic create_manual
    # applies — they can't go through the blind setattr loop. Pop them here so
    # the generic loop below doesn't double-apply (or clobber the flags).
    def _non_none(items):
        # Entries that are real selections, not the "None" sentinel
        # (case-insensitive). Mirrors create_manual's helper.
        return [it for it in (items or []) if (it or "").strip().lower() != "none"]

    if "clearance_types" in data:
        ctypes = data.pop("clearance_types") or []
        s.clearance_types = ctypes
        if _non_none(ctypes):
            s.clearance_required = True
            s.clearance_status = "required"
        else:
            s.clearance_required = False
            s.clearance_status = "not_required"

    if "device_types" in data:
        dtypes = data.pop("device_types") or []
        s.device_types = dtypes
        real_devices = _non_none(dtypes)
        if real_devices:
            s.device_required = True
            s.device_kind = real_devices[0]
        else:
            s.device_required = False
            s.device_kind = None

    if "assistant_surgeon_name" in data:
        a_raw = (data.pop("assistant_surgeon_name") or "").strip()
        if a_raw.lower() in ("", "none"):
            s.assistant_surgeon_name = None
            s.assistant_surgeon_required = False
        else:
            s.assistant_surgeon_name = a_raw
            s.assistant_surgeon_required = True

    # Apply (the generic patch loop sets auth_status, assistant_surgeon_required,
    # status, etc. directly on the Surgery row; the steps engine derives workflow
    # progress from those columns — milestones are retired).
    for k, v in data.items():
        setattr(s, k, v)

    _commit_or_409(db, surgery_id=surgery_id); db.refresh(s)

    # PHI audit (Fable surgery audit H2). Emit only when something
    # material actually changed, to keep the audit log signal-rich.
    if audit_diff:
        log_action(
            db,
            action="CHART_EDIT",
            resource_type="surgery",
            resource_id=str(s.id),
            patient_id=s.chart_number or None,
            user_id=(current_user.get("email") or "").lower() or None,
            user_name=current_user.get("name") or current_user.get("email"),
            description=(f"Edited surgery {s.surgery_number or s.id}: "
                         f"{', '.join(sorted(audit_diff.keys()))}"),
            old_values={k: v["before"] for k, v in audit_diff.items()},
            new_values={k: v["after"]  for k, v in audit_diff.items()},
        )

    # If the staff just changed anything that drives the calendar event
    # (date, start time, facility, surgeon), re-push so the event on the
    # surgeon's calendar matches the DB. Soft-fail; never block the PATCH
    # if the Google call hiccups.
    calendar_keys = {"scheduled_date", "scheduled_start_time",
                     "selected_facility", "surgeon_primary",
                     "surgeon_email", "estimated_minutes", "status"}
    # Resync whenever a calendar-relevant field changes. We used to require
    # an existing event_id, but that prevented un-cancellation (cancel clears
    # the id, so the restore PATCH couldn't recreate the event). upsert handles
    # both insert and update.
    if (data.keys() & calendar_keys) and (s.status or "").lower() != "cancelled":
        try:
            from app.services.google_calendar_sync import upsert_event_for_surgery
            upsert_event_for_surgery(db, s)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(
                "calendar resync after PATCH failed for %s: %s", s.id, e)

    return _surgery_dict(db, s)


# ─── Status transitions (cancel / hold / unresponsive) ──────────────

class CancelPayload(BaseModel):
    reason: str          # patient | anesthesia | hospital | medical | unresponsive | hold
    notes: Optional[str] = None
    fee_required: Optional[bool] = None    # caller can override system default
    fee_override_reason: Optional[str] = None    # required if fee_required is set


@router.post("/{surgery_id}/cancel")
def cancel_surgery(surgery_id: str, payload: CancelPayload,
                    db: Session = Depends(get_db),
                    current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.WORK))):
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
    # Refuse to cancel a surgery whose status is already terminal —
    # 'cancelled' is a no-op that would write a duplicate
    # SurgeryCancellation row, and 'completed' would wipe scheduled_date
    # and break post-op reporting. Use the dedicated patch / billing
    # paths if the case actually needs amendment.
    if s.status in ("cancelled", "completed"):
        raise HTTPException(status_code=409,
            detail=f"Surgery is already {s.status}; cannot cancel again. "
                   "If the prior cancellation was a mistake, use the "
                   "reopen path instead.")

    fee_required = False
    refund_required = False
    if payload.reason == "patient" and s.scheduled_date:
        days_to_surgery = (s.scheduled_date - _date.today()).days
        if 0 <= days_to_surgery <= 14:
            fee_required = True
    # If the caller overrides the system-determined fee (waive or impose),
    # require an explicit reason AND MANAGE tier so a WORK-tier user
    # can't silently waive the $351. (Fable surgery audit M4.)
    if payload.fee_required is not None and payload.fee_required != fee_required:
        if not (payload.fee_override_reason or "").strip():
            raise HTTPException(
                status_code=422,
                detail=("fee_override_reason is required when overriding the "
                        "system-determined cancellation fee"))
        from app.permissions.resolver import effective_tier
        actor_email = (current_user.get("email") or "").lower().strip()
        if effective_tier(db, actor_email, Module.SURGERY) < Tier.MANAGE:
            raise HTTPException(
                status_code=403,
                detail=("overriding the cancellation fee requires Tier.MANAGE "
                        "on the Surgery module"))
        fee_required = payload.fee_required
    if s.amount_paid and float(s.amount_paid) > 0:
        refund_required = True

    new_status = "hold" if payload.reason == "hold" else (
                  "unresponsive" if payload.reason == "unresponsive" else "cancelled")
    s.status = new_status

    # Revoke all outstanding patient portal / magic-link JWTs for this
    # surgery. Their ptv claim is now stale; require_portal_token and
    # require_patient_token will 401 them. (Fable portal audit H5-auth.)
    from app.services.patient_portal_auth import bump_portal_token_version
    bump_portal_token_version(db, s)

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
    _commit_or_409(db, surgery_id=surgery_id); db.refresh(s)
    try:
        from app.services.google_calendar_sync import delete_event_for_surgery
        delete_event_for_surgery(db, s)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("calendar sync failed: %s", e)

    # Void any still-live BoldSign consent envelopes so the patient
    # can't sign a consent that no longer applies. Skip terminal
    # statuses (signed/completed/voided/declined/expired) — those are
    # already settled and BoldSign rejects voids on completed docs.
    if payload.reason != "hold":
        from app.models.surgery import SurgeryNote
        from app.services.boldsign_envelopes import (
            void_envelope_row, BoldSignEnvelopeError,
        )
        TERMINAL = {"signed", "completed", "voided", "declined", "expired"}
        for env in (s.consent_envelopes or []):
            if (env.status or "").lower() in TERMINAL:
                continue
            try:
                void_envelope_row(
                    db, env,
                    reason=f"Surgery cancelled ({payload.reason})",
                )
            except (BoldSignEnvelopeError, Exception) as ve:
                # A live consent envelope outliving its cancelled surgery
                # is a real PHI/clinical risk — the patient could still
                # sign. Make the failure visible: chart note (so staff see
                # it on the surgery timeline) + central audit row tagged
                # FAILED so a daily monitor can find these.
                # (Fable surgery audit M5.)
                logging.getLogger(__name__).warning(
                    "BoldSign void failed for envelope %s: %s", env.id, ve)
                db.add(SurgeryNote(
                    surgery_id=s.id,
                    created_by="system:boldsign-void-failed",
                    content=(f"⚠ BoldSign envelope {env.id} void FAILED during "
                             f"cancellation ({payload.reason}). Patient may "
                             f"still be able to sign this envelope. Revoke "
                             f"manually in the BoldSign dashboard. Error: {ve}"),
                ))
                log_action(
                    db,
                    action="BOLDSIGN_VOID_FAILED",
                    resource_type="surgery_consent_envelope",
                    resource_id=str(env.id),
                    patient_id=s.chart_number or None,
                    user_id=(current_user.get("email") or "").lower() or None,
                    user_name=current_user.get("name") or current_user.get("email"),
                    description=(f"Failed to void BoldSign envelope {env.id} "
                                 f"on cancellation of surgery {s.id}"),
                    status="failure",
                    error_detail=str(ve)[:500],
                )

    # HIPAA audit row alongside the SurgeryCancellation table so the
    # central audit_logs view (used by /audit + reporting) sees who
    # cancelled what and when, not just the cancellation-specific
    # table.
    actor_email = (current_user.get("email") or "").lower().strip() or None
    log_action(
        db,
        action="CANCEL", resource_type="surgery",
        resource_id=str(s.id), patient_id=s.chart_number,
        user_id=actor_email, user_name=current_user.get("name") or actor_email,
        description=(
            f"Surgery {new_status} (reason: {payload.reason}, "
            f"fee_required: {fee_required}, refund_required: {refund_required})"
        ),
    )

    return {
        "id": str(s.id),
        "status": s.status,
        "fee_required": fee_required,
        "refund_required": refund_required,
        "freed_block_day_id": freed_block_day_id,
    }


# ─── Milestone helper ───────────────────────────────────────────────

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
                          current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.MANAGE))):
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
                            current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.MANAGE))):
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
    from app.services.surgery.block_schedule import materialize_block_days
    materialize_block_days(db)
    return {"id": str(bs.id), "facility": bs.facility}


@router.delete("/admin/block-schedules/{schedule_id}", status_code=204)
def delete_block_schedule(schedule_id: str, db: Session = Depends(get_db),
                            current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.MANAGE))):
    bs = db.query(BlockSchedule).filter(BlockSchedule.id == schedule_id).first()
    if not bs:
        raise HTTPException(status_code=404, detail="not found")
    db.delete(bs); db.commit()
    return None


@router.post("/admin/block-schedules/materialize")
def trigger_materialize(db: Session = Depends(get_db),
                          current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.MANAGE))):
    from app.services.surgery.block_schedule import materialize_block_days
    return materialize_block_days(db)


@router.post("/admin/run-escalations")
def trigger_escalations(db: Session = Depends(get_db),
                          current_user: dict = Depends(requires_super_admin())):
    """Manual trigger for the behind-schedule sweep. Primary runner is the
    surgery_escalations Cloud Run Job (registered in app/jobs/run.py).
    Super-admin only — coordinators shouldn't click this. (Fable note 6.)"""
    from app.services.surgery.escalations import run_escalation_sweep
    return run_escalation_sweep(db)


@router.post("/admin/run-release-sweep")
def trigger_release_sweep(db: Session = Depends(get_db),
                           current_user: dict = Depends(requires_super_admin())):
    """Manual trigger for the daily release-alert sweep. Primary runner
    is the surgery_release_sweep Cloud Run Job (registered in
    app/jobs/run.py). Super-admin only. (Fable note 6.)"""
    from app.services.surgery.release_alerts import run_release_sweep
    return run_release_sweep(db)


@router.post("/admin/block-days/{block_day_id}/mark-released")
def mark_block_day_released(block_day_id: str,
                              db: Session = Depends(get_db),
                              current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.WORK))):
    """The scheduler called the hospital and released this unbooked block day.
    Stamps release_alert_sent_at so it falls off the dashboard alert list."""
    bd = db.query(BlockDay).filter(BlockDay.id == block_day_id).first()
    if not bd:
        raise HTTPException(status_code=404, detail="block day not found")
    # Idempotent: a second call returns the original timestamp instead of
    # overwriting it (avoids racing two schedulers stamping conflicting times).
    if bd.release_alert_sent_at is None:
        bd.release_alert_sent_at = now_utc_naive()
        db.commit()
    return {"ok": True, "released_at": bd.release_alert_sent_at.isoformat()}


@router.get("/admin/block-days/{block_day_id}/availability")
def block_day_availability(
    block_day_id: str,
    surgery_id: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.VIEW)),
):
    """Return the list of available start times on a block day, computed
    server-side from the block day's start/end + the duration the booking
    would consume.

    If `surgery_id` is provided, the duration honors the same resolution
    logic as the booking endpoints (Surgery.duration_minutes → template →
    kind map). Otherwise duration is read from the matching procedure
    template for the block's kind.
    """
    from datetime import datetime, timedelta
    from app.routers.patient_surgery import _default_duration_for
    bd = db.query(BlockDay).filter(BlockDay.id == block_day_id).first()
    if not bd:
        raise HTTPException(status_code=404, detail="block day not found")

    surgery = None
    if surgery_id:
        surgery = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    duration = _default_duration_for(db, surgery, bd) if surgery else 60

    # Generate 30-minute increments from start_time up to end_time - duration.
    # The coordinator picks any of these slots (the patient sees only the
    # next available time per date — patient flow is a different endpoint).
    def _t_to_dt(t):
        return datetime.combine(bd.block_date, t)
    earliest = _t_to_dt(bd.start_time)
    latest   = _t_to_dt(bd.end_time) - timedelta(minutes=duration)
    candidates = []
    cur = earliest
    while cur <= latest:
        candidates.append(cur.time())
        cur += timedelta(minutes=30)

    # Filter out any that would overlap an existing slot.
    available = [t.strftime("%H:%M") for t in candidates
                  if overlapping_slot(db, bd.id, t, duration) is None]
    return {
        "block_day_id": str(bd.id),
        "block_date":   bd.block_date.isoformat(),
        "facility":     bd.facility,
        "duration_minutes": duration,
        "available_starts": available,
    }


# ─── Boarding slips ─────────────────────────────────────────────────

@router.post("/{surgery_id}/clearance/generate-form")
def generate_clearance_form(surgery_id: str,
                             db: Session = Depends(get_db),
                             current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.WORK))):
    """Generate a fillable cardiac/anesthesia clearance form for the
    cardiologist, save it as a SurgeryFile (visible on the patient
    portal too), and email the patient with the portal link."""
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="surgery not found")
    if not s.clearance_required:
        raise HTTPException(status_code=409,
                            detail="Clearance is not marked required for this surgery.")

    from app.services.surgery.clearance_form import generate_for_surgery
    try:
        f = generate_for_surgery(db, s, by_email=current_user.get("email") or "system")
    except Exception as exc:
        log = logging.getLogger(__name__)
        log.exception("clearance form generation failed")
        raise HTTPException(status_code=500,
                            detail=f"Clearance form generation failed: {exc}")

    # Patient email (soft-fail — don't break the staff workflow if SMTP is down)
    email_sent = False
    if s.email:
        try:
            from app.services.patient_email import send_patient_email
            portal_url = "https://gw.waldorfwomenscare.com/portal/login"
            html = f"""
            <p>Hello {s.patient_name or 'there'},</p>
            <p>Your pre-operative cardiac/anesthesia clearance form is ready.
            Please log in to your surgery portal to download it, bring it to
            your cardiologist, and upload the completed letter when it's
            signed.</p>
            <p><a href="{portal_url}">Open your surgery portal</a></p>
            <p>Thank you,<br/>Waldorf Women's Care</p>
            """
            rec = send_patient_email(
                db,
                kind=None,
                to_email=s.email,
                context={},
                sent_by=current_user.get("email") or "system",
                surgery_id=s.id,
                chart_number=s.chart_number,
                ad_hoc_subject="Pre-op clearance form ready",
                ad_hoc_html=html,
            )
            email_sent = (rec.status == "sent")
        except Exception:
            log = logging.getLogger(__name__)
            log.exception("clearance form email failed (soft-fail)")

    # Move the clearance_status forward to request_sent unless already further
    if s.clearance_status in (None, "", "not_required", "required"):
        s.clearance_status = "request_sent"
        db.commit()

    return {
        "id": str(f.id),
        "filename": f.filename,
        "size_bytes": f.size_bytes,
        "download_url": f"/api/surgery/{surgery_id}/files/{f.id}/download",
        "email_sent": email_sent,
    }


class BoardingSlipPayload(BaseModel):
    # Values may arrive as strings (text inputs) or numbers (date/time
    # pickers, the estimated_minutes number input). _translate_overrides
    # str()'s them before they reach the PDF generator.
    overrides: Optional[dict[str, Any]] = None


# User-friendly field keys → MedStar PDF field names.
_USER_TO_MEDSTAR = {
    "surgery_date":          "Surgery Date Requested",
    "start_time":            "Start Time",
    "primary_surgeon":       "AUTO_PhysicianName",
    "secondary_surgeon":     "Secondary Surgeon",
    "primary_cpt":           "Min Primary CPT Code",
    "secondary_cpt":         "Secondary CPT",
    "primary_description":   "AUTO_VisitPlans",
    "icd":                   "AUTO_VisitICD10",
    "diagnosis_description": "AUTO_VisitImpressions",
    "special_request":       "Other Special Equipment",
    "additional_notes":      "Additional Notes",
}

# User-friendly field keys → CRMC coord-map keys.
_USER_TO_CRMC = {
    "surgery_date":          "requested_date",
    "start_time":            "requested_time",
    "primary_description":   "procedure",
    "diagnosis_description": "diagnosis",
    "anesthesia":            "anesthesia",
    "icd":                   "icd",
    "primary_cpt":           "cpt",
    "special_request":       "special_request",
    "auth_number":           "auth_number",
}


# User-friendly keys that hold dates and should land on the printed form
# as MM/DD/YYYY (HTML date inputs send YYYY-MM-DD).
_DATE_OVERRIDE_KEYS = {"surgery_date"}


def _us_date_str(v) -> str:
    """Coerce YYYY-MM-DD → MM/DD/YYYY for PDF rendering. Leaves other
    strings untouched."""
    s = str(v)
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        try:
            from datetime import datetime as _dt
            d = _dt.strptime(s[:10], "%Y-%m-%d").date()
            return f"{d.month:02d}/{d.day:02d}/{d.year}"
        except ValueError:
            pass
    return s


def _translate_overrides(facility: str, user_overrides: dict) -> dict:
    """Translate user-friendly keys to the field names the generators
    expect. Unknown keys pass through unchanged so power users can also
    target raw PDF field names directly. estimated_minutes is split into
    Hrs / Est Time Needed for MedStar. Date fields are reformatted to
    MM/DD/YYYY so they print the way the practice wants."""
    if not user_overrides:
        return {}
    mapping = _USER_TO_MEDSTAR if facility == "medstar" else _USER_TO_CRMC
    out: dict[str, str] = {}
    for k, v in user_overrides.items():
        if v is None or v == "":
            continue
        # Special: estimated_minutes → MedStar splits into Hrs + Est Time
        if k == "estimated_minutes" and facility == "medstar":
            try:
                total = int(v)
                out["Hrs"] = str(total // 60)
                out["Est Time Needed"] = f"{total // 60}:{total % 60:02d}"
            except (TypeError, ValueError):
                pass
            continue
        pdf_key = mapping.get(k, k)
        out[pdf_key] = _us_date_str(v) if k in _DATE_OVERRIDE_KEYS else str(v)
    return out


@router.post("/{surgery_id}/boarding-slip")
def generate_boarding_slip(surgery_id: str,
                            payload: Optional[BoardingSlipPayload] = Body(default=None),
                            db: Session = Depends(get_db),
                            current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.WORK))):
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="surgery not found")
    if s.selected_facility not in ("medstar", "crmc"):
        raise HTTPException(status_code=409,
                            detail=f"Boarding slip not needed for facility {s.selected_facility}")
    user_overrides = (payload.overrides if payload else None) or {}
    overrides = _translate_overrides(s.selected_facility, user_overrides)
    # Persist the user-friendly overrides on the surgery so the editor
    # remembers them next time it opens.
    if user_overrides:
        # Strip falsy values so cleared fields don't ghost the next render.
        clean = {k: v for k, v in user_overrides.items() if v not in (None, "")}
        s.boarding_slip_overrides = clean or None
    from app.services.surgery.boarding_slip import generate_for_surgery
    try:
        f = generate_for_surgery(db, s,
                                  by_email=current_user.get("email") or "system",
                                  overrides=overrides)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Boarding slip generation failed: {exc}")
    return {
        "id": str(f.id),
        "filename": f.filename,
        "size_bytes": f.size_bytes,
        "download_url": f"/api/surgery/{surgery_id}/files/{f.id}/download",
    }


@router.get("/{surgery_id}/boarding-slip/prefill")
def boarding_slip_prefill(surgery_id: str,
                          db: Session = Depends(get_db),
                          current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.WORK))):
    """Returns the prefill values that would land on each field of the
    boarding slip, so the staff PDF editor can show them as initial
    values."""
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="surgery not found")
    if s.selected_facility not in ("medstar", "crmc"):
        raise HTTPException(status_code=409,
                            detail=f"Boarding slip not needed for facility {s.selected_facility}")

    procs = s.procedures or []
    diags = s.diagnoses or []
    primary = procs[0] if procs else {}
    secondary = procs[1] if len(procs) > 1 else {}
    primary_dx = diags[0] if diags else {}

    fields = {
        "surgery_date":      str(s.scheduled_date) if s.scheduled_date else "",
        "start_time":        (str(s.scheduled_start_time)[:5]
                                if s.scheduled_start_time else ""),
        "estimated_minutes": s.estimated_minutes or 0,
        "primary_surgeon":   s.surgeon_primary or "",
        "secondary_surgeon": s.surgeon_secondary or "",
        "primary_cpt":       primary.get("cpt") or "",
        "primary_description": primary.get("description") or "",
        "secondary_cpt":     secondary.get("cpt") or "",
        "secondary_description": secondary.get("description") or "",
        "icd":               primary_dx.get("icd") or "",
        "diagnosis_description": primary_dx.get("description") or "",
        "anesthesia":        s.anesthesia or "",
        "special_request":   s.special_equipment_notes or "",
        "auth_number":       s.auth_number or "",
        "additional_notes":  s.notes or "",
    }
    # Persisted overrides win — coordinator's last-saved edits seed the
    # form so they don't have to re-enter them every regeneration.
    saved = s.boarding_slip_overrides or {}
    for k, v in saved.items():
        if v not in (None, ""):
            fields[k] = v

    return {
        "facility": s.selected_facility,
        "fields": fields,
        "has_saved_overrides": bool(saved),
    }


class BoardingSlipSendPayload(BaseModel):
    kind: str                                  # "fax" | "email"
    to: str                                    # fax number OR email address
    subject: Optional[str] = None              # email-only
    message: Optional[str] = None              # cover sheet text / email body
    file_id: Optional[str] = None              # default: latest boarding slip


@router.post("/{surgery_id}/boarding-slip/send")
def send_boarding_slip(surgery_id: str,
                       payload: BoardingSlipSendPayload,
                       db: Session = Depends(get_db),
                       current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.WORK))):
    """Fax or email the latest boarding slip PDF to a hospital scheduler."""
    if payload.kind not in ("fax", "email"):
        raise HTTPException(status_code=422, detail="kind must be 'fax' or 'email'")
    to = (payload.to or "").strip()
    if not to:
        raise HTTPException(status_code=422, detail="destination ('to') is required")
    # PHI destination shape check — misdirected fax is the classic HIPAA
    # breach vector. (Fable surgery audit Low.)
    import re
    if payload.kind == "fax":
        digits = re.sub(r"\D", "", to)
        if len(digits) < 10:
            raise HTTPException(
                status_code=422,
                detail="fax number must contain at least 10 digits")
    else:  # email
        if "@" not in to or "." not in to.rsplit("@", 1)[-1]:
            raise HTTPException(
                status_code=422,
                detail="email destination must look like an email address")

    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="surgery not found")
    # HIPAA outbound audit: who sent a PHI document where. send_history
    # already records on the file, but the central audit log is what
    # incident queries hit. (Fable surgery audit Low + H2.)
    log_action(
        db,
        action="PHI_BOARDING_SLIP_SENT",
        resource_type="surgery",
        resource_id=str(s.id),
        patient_id=s.chart_number or None,
        user_id=(current_user.get("email") or "").lower() or None,
        user_name=current_user.get("name") or current_user.get("email"),
        description=(f"Sent boarding slip for surgery "
                     f"{s.surgery_number or s.id} via {payload.kind} to {to}"),
    )

    # Resolve which boarding slip to send
    q = db.query(SurgeryFile).filter(SurgeryFile.surgery_id == s.id,
                                      SurgeryFile.kind == "boarding_slip")
    f = (q.filter(SurgeryFile.id == payload.file_id).first()
         if payload.file_id else
         q.order_by(SurgeryFile.uploaded_at.desc()).first())
    if not f:
        raise HTTPException(status_code=404,
                            detail="No boarding slip has been generated yet. "
                                   "Click 'Generate' first.")

    # Pull bytes via the storage adapter (works on local + GCS)
    from app.services.storage import read_blob, is_legacy_local_path
    if is_legacy_local_path(f.path):
        raise HTTPException(status_code=410,
                            detail="This file is from before the cloud migration "
                                   "and is no longer available.")
    try:
        pdf_bytes = read_blob(f.path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Boarding slip file is missing.")

    facility_label = {"medstar": "MedStar Southern Maryland Hospital Center",
                       "crmc":    "University of Maryland Charles Regional Medical Center"}\
                     .get(s.selected_facility or "", s.selected_facility or "")
    actor = current_user.get("email") or "system"

    def _record_send(entry: dict):
        """Append the send event to SurgeryFile.send_history."""
        hist = list(f.send_history or [])
        hist.append(entry)
        f.send_history = hist
        # SQLAlchemy treats JSON columns as opaque blobs — explicitly mark
        # the attribute as modified so the change actually persists.
        from sqlalchemy.orm.attributes import flag_modified as _fm
        _fm(f, "send_history")

    if payload.kind == "fax":
        import tempfile, os as _os
        from app.services.fax_service import send_fax
        cover = (
            payload.message
            or f"Boarding slip for {s.patient_name or '—'} "
               f"(chart #{s.chart_number or '—'})."
        )
        tmp = tempfile.NamedTemporaryFile(prefix="boarding_slip_",
                                            suffix=".pdf", delete=False)
        try:
            tmp.write(pdf_bytes); tmp.flush(); tmp.close()
            result = send_fax(
                to_number=payload.to.strip(),
                file_path=tmp.name,
                cover_page_text=cover,
                patient_name=s.patient_name or "",
            )
        finally:
            try: _os.unlink(tmp.name)
            except OSError: pass
        if result.get("error"):
            _record_send({
                "at":     now_utc_naive().isoformat(),
                "by":     actor,
                "kind":   "fax",
                "to":     payload.to.strip(),
                "status": "failed",
                "error":  result["error"],
            })
            db.commit()
            raise HTTPException(status_code=502,
                                detail=f"Fax send failed: {result['error']}")
        _record_send({
            "at":         now_utc_naive().isoformat(),
            "by":         actor,
            "kind":       "fax",
            "to":         payload.to.strip(),
            "status":     "sent",
            "message_id": result.get("message_id"),
        })
        from app.models.surgery import SurgeryNote
        db.add(SurgeryNote(
            surgery_id=s.id, created_by=actor,
            content=f"Faxed boarding slip to {payload.to.strip()} "
                    f"(msg id {result.get('message_id') or '—'}).",
        ))
        # Mark the post_to_hospital step done — sending the boarding slip IS
        # the act of posting the surgery to the hospital. The milestone→steps
        # cutover orphaned this column (only ever cleared). (Fable audit #5.)
        s.calendar_invite_sent_at = now_utc_naive()
        db.commit()
        return {
            "ok": True, "kind": "fax",
            "to": payload.to.strip(),
            "message_id": result.get("message_id"),
            "send_history": f.send_history or [],
        }

    # --- email path ---
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.application import MIMEApplication
    from app.services.checklist_notifications import _smtp_settings
    cfg = _smtp_settings()
    if not (cfg["host"] and cfg["from"]):
        raise HTTPException(status_code=503,
                            detail="SMTP isn't configured on this server.")
    subject = (payload.subject or
               f"Boarding slip — {s.patient_name or 'patient'} — {facility_label}")
    body_text = (
        payload.message
        or f"Attached is the boarding slip for {s.patient_name or 'this patient'}"
           f" (chart #{s.chart_number or '—'}) at {facility_label}."
    )
    body_html = (
        f"<p>{body_text}</p>"
        f"<p style='color:#888;font-size:11px'>"
        f"Sent from Waldorf Women's Care · {actor.split('@')[0]}</p>"
    )
    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = cfg["from"]
    msg["To"] = payload.to.strip()
    msg.attach(MIMEText(body_html, "html"))
    attach = MIMEApplication(pdf_bytes, _subtype="pdf")
    attach.add_header("Content-Disposition", "attachment",
                        filename=f.filename or "boarding_slip.pdf")
    msg.attach(attach)
    try:
        with smtplib.SMTP(cfg["host"], cfg["port"]) as smtp:
            smtp.starttls()
            if cfg["user"] and cfg["password"]:
                smtp.login(cfg["user"], cfg["password"])
            smtp.sendmail(cfg["from"], [payload.to.strip()], msg.as_string())
    except Exception as exc:
        _record_send({
            "at":     now_utc_naive().isoformat(),
            "by":     actor,
            "kind":   "email",
            "to":     payload.to.strip(),
            "status": "failed",
            "error":  str(exc),
        })
        db.commit()
        raise HTTPException(status_code=502,
                            detail=f"Email send failed: {exc}")

    _record_send({
        "at":     now_utc_naive().isoformat(),
        "by":     actor,
        "kind":   "email",
        "to":     payload.to.strip(),
        "status": "sent",
    })
    from app.models.surgery import SurgeryNote
    db.add(SurgeryNote(
        surgery_id=s.id, created_by=actor,
        content=f"Emailed boarding slip to {payload.to.strip()}.",
    ))
    # Mark the post_to_hospital step done (see fax path above). (Fable audit #5.)
    s.calendar_invite_sent_at = now_utc_naive()
    db.commit()
    return {"ok": True, "kind": "email", "to": payload.to.strip(),
            "send_history": f.send_history or []}


@router.post("/candidates/bulk-import")
async def bulk_import_candidates(
    file: UploadFile = File(...),
    dry_run: bool = Query(True),
    auto_schedule: bool = Query(False,
        description=("When true, also book each row that has an "
                      "Appointment Type/Date/Time onto its matching "
                      "BlockDay — silent (no patient email/SMS, no "
                      "calendar sync). Use when the dates were already "
                      "confirmed with patients out-of-band.")),
    backfill_mode: bool = Query(False,
        description=("Backfill historical surgeries that already exist in "
                      "the real world. Bypasses capacity / overlap / "
                      "block-window / blackout guards on the booking "
                      "write, and re-attempts any prior import row stuck "
                      "in incomplete + candidate_imported. Implies "
                      "auto_schedule.")),
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.WORK)),
):
    """Upload a ModMed-style patient roster Excel and create Surgery rows
    in 'incomplete' status. Defaults to dry-run so the coordinator can
    preview the row breakdown before committing.

    Skips a chart number when an active (non-cancelled, non-completed)
    surgery already exists for that chart.

    When auto_schedule=true, also books each row's slot onto the matching
    BlockDay silently. Rows without a recognized Appointment Type or
    without a BlockDay for the day are reported in `schedule_error_rows`.
    """
    # backfill_mode mass force-books, bypassing capacity COUNT / blackout
    # guards — too blunt for a WORK-tier coordinator. Allow normal import
    # at WORK, but require MANAGE+ for backfill_mode=true. (audit #28)
    if backfill_mode:
        has_manage = (current_user.get("module_tier") or {}).get(
            "surgery", 0) >= int(Tier.MANAGE)
        if not has_manage:
            raise HTTPException(
                status_code=403,
                detail="backfill_mode=true requires Tier.MANAGE or higher.")

    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=422, detail="empty file")
    if len(contents) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413,
                            detail="file >25 MB; split it into smaller batches")

    from app.services.surgery.candidate_import import parse_excel, import_rows
    try:
        rows = parse_excel(contents)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=422,
                            detail=f"Could not parse Excel: {exc}")

    if not rows:
        return {"total": 0, "created": 0, "skipped": 0, "errors": 0,
                "created_rows": [], "skipped_rows": [], "error_rows": [],
                "dry_run": dry_run, "filename": file.filename}

    result = import_rows(db, rows, dry_run=dry_run,
                          by_email=current_user.get("email") or "system",
                          # backfill_mode implies auto_schedule (the whole
                          # point is to land the date/time from the file)
                          auto_schedule=auto_schedule or backfill_mode,
                          backfill_mode=backfill_mode)
    result["filename"] = file.filename
    return result


# ─── Klara message drafter ──────────────────────────────────────────

@router.get("/{surgery_id}/klara-draft/{kind}")
def klara_draft(surgery_id: str, kind: str,
                 db: Session = Depends(get_db),
                 current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.WORK))):
    """Generate a Klara message draft. kind ∈
    {initial_scheduling, date_reminder, post_op_check_in}."""
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="surgery not found")
    from app.services.surgery.klara_drafter import draft
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
                    current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.WORK))):
    """Record that staff copied the draft into Klara and sent it. Keeps the
    SurgeryNotification audit row (Klara has no API — messages are drafted
    for manual copy-paste)."""
    from app.models.surgery import SurgeryNotification

    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="surgery not found")

    db.add(SurgeryNotification(
        surgery_id=s.id,
        kind=payload.kind,
        sent_by=current_user.get("email"),
        body_preview=payload.body_preview,
    ))
    db.commit()
    return {"ok": True}


# ─── Portal-access invite ───────────────────────────────────────────

@router.post("/{surgery_id}/portal-access/send")
def send_portal_access(surgery_id: str,
                        db: Session = Depends(get_db),
                        current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.WORK))):
    """Email the patient a link to the portal login page along with the
    DOB + last-4-of-phone instructions. Records to PatientEmail history and
    drops a SurgeryNotification row so the Communication card surfaces it."""
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="surgery not found")
    if not s.email:
        raise HTTPException(status_code=422,
                            detail="No email on file for this patient. "
                                   "Use the Klara drafter to send by SMS instead.")

    portal_url = "https://gw.waldorfwomenscare.com/portal/login"
    first = (s.first_name or (s.patient_name or "").split(",")[-1].strip().split()[0]
             if s.patient_name else "there")
    html = f"""
    <p>Hello {first},</p>
    <p>You now have access to your Waldorf Women's Care surgery portal.
    From there you can review your benefits estimate, pay your patient
    responsibility, pick a surgery date, sign consent forms, and message
    your care team.</p>
    <p><a href="{portal_url}"
           style="background:#7c3aed;color:#fff;padding:10px 18px;
                  border-radius:8px;text-decoration:none;display:inline-block;">
        Open my surgery portal
    </a></p>
    <p>To log in you'll need:</p>
    <ul>
      <li>Your <strong>date of birth</strong></li>
      <li>The <strong>last 4 digits</strong> of the phone number we have on file</li>
    </ul>
    <p style="color:#6b7280;font-size:13px;font-style:italic;">
      Your surgery portal access will end <strong>30 days after your surgery date</strong>.
      Save any documents you'd like to keep before then.
    </p>
    <p>If anything doesn't work, call our office at 240-252-2140.</p>
    <p>Thank you,<br/>Waldorf Women's Care</p>
    """

    from app.services.patient_email import send_patient_email
    from app.models.surgery import SurgeryNotification
    rec = send_patient_email(
        db,
        kind=None,
        to_email=s.email,
        context={},
        sent_by=current_user.get("email") or "system",
        surgery_id=s.id,
        chart_number=s.chart_number,
        ad_hoc_subject="Your surgery portal access",
        ad_hoc_html=html,
    )
    db.add(SurgeryNotification(
        surgery_id=s.id,
        kind="portal_invite",
        sent_by=current_user.get("email"),
        body_preview=f"Portal invite emailed to {s.email}",
    ))
    db.commit()

    if rec.status != "sent":
        raise HTTPException(status_code=502,
                            detail=f"Email send failed: {rec.failure_reason or 'unknown'}")
    return {"ok": True, "sent_to": s.email}


@router.post("/{surgery_id}/files", status_code=201)
async def upload_file(
    surgery_id: str,
    file: UploadFile = File(...),
    kind: str = Query(..., description="order | prior_auth | op_notes | path_report | clearance | consent | fmla | other"),
    notes: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.WORK)),
):
    """Upload a file for a surgery. Auto-completes the matching milestone
    if there's an obvious mapping (prior_auth, op_notes, path_report)."""
    if kind not in ("order", "prior_auth", "op_notes", "path_report", "clearance",
                    "consent", "fmla", "other"):
        raise HTTPException(status_code=422, detail=f"unknown file kind: {kind}")

    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="surgery not found")

    contents = await file.read()
    key = save_blob(prefix="surgery-files", body=contents,
                    filename=file.filename or "upload")

    f_row = SurgeryFile(
        surgery_id=s.id,
        kind=kind,
        filename=file.filename,
        path=key,
        mime_type=file.content_type,
        size_bytes=len(contents),
        notes=notes,
        uploaded_by=current_user.get("email"),
    )
    db.add(f_row)

    # Auto-advance auth_status only on the happy path — i.e. when we
    # were waiting on the payer (sent_request / sent_records). Without
    # this guard, uploading the DENIAL letter (naturally filed as kind
    # 'prior_auth') would flip a 'denied' auth back to 'approved' —
    # surgery proceeds against a denied auth = uncompensated case.
    # 'required' / 'tbd' / 'peer_review' also stay as-is and need an
    # explicit decision via PATCH auth_status. (Fable surgery audit H3.)
    if kind == "prior_auth" and s.auth_status in ("sent_request", "sent_records"):
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
                current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.VIEW))):
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
                   current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.VIEW))):
    f = db.query(SurgeryFile).filter(
        SurgeryFile.id == file_id,
        SurgeryFile.surgery_id == surgery_id,
    ).first()
    if not f:
        raise HTTPException(status_code=404, detail="file not found")
    if is_legacy_local_path(f.path):
        raise HTTPException(status_code=410,
                              detail="This file is from before the cloud migration and is no longer available.")
    # PHI document download audit (Fable surgery audit H2). Path
    # reports, op notes, clearance letters, consent PDFs all count as
    # PHI; record the actor + filename + kind so a misuse report
    # query can find who pulled what.
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    log_action(
        db,
        action="CHART_DOCUMENT_DOWNLOAD",
        resource_type="surgery_file",
        resource_id=str(f.id),
        patient_id=(s.chart_number if s else None),
        user_id=(current_user.get("email") or "").lower() or None,
        user_name=current_user.get("name") or current_user.get("email"),
        description=f"Downloaded {f.kind} file {f.filename} for surgery {surgery_id}",
    )
    return serve_blob(
        local_path=None,
        gcs_object=f.path,
        media_type=f.mime_type or "application/octet-stream",
        filename=f.filename,
        disposition="attachment",
    )


@router.get("/admin/block-days")
def list_block_days(
    facility: Optional[str] = None,
    days: int = 60,
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.VIEW)),
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


# ─── Calendar day-detail + ad-hoc surgery day creation ─────────────

@router.get("/admin/block-dates")
def list_block_dates(
    start: str = Query(..., description="YYYY-MM-DD"),
    end:   str = Query(..., description="YYYY-MM-DD"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.VIEW)),
):
    """Light-weight: just the set of dates in [start, end] that have at
    least one BlockDay. Powers the calendar's grey-out behavior for
    days that aren't allocated as surgery days."""
    try:
        s = datetime.strptime(start[:10], "%Y-%m-%d").date()
        e = datetime.strptime(end[:10],   "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=422, detail="start/end must be YYYY-MM-DD")
    rows = (db.query(BlockDay.block_date)
              .filter(BlockDay.block_date >= s, BlockDay.block_date <= e)
              .distinct().all())
    return {"dates": sorted({str(r[0]) for r in rows})}


@router.get("/admin/calendar-day/{date_str}")
def calendar_day_detail(
    date_str: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.VIEW)),
):
    """One day's full schedule. Returns per-facility BlockDays with
    operational hours and the 30-minute grid filled in (booked slots
    show the patient; open slots are empty). Also returns any
    blackouts for that date and a list of unscheduled surgeries the
    user can drop into open slots."""
    try:
        d = datetime.strptime(date_str[:10], "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=422, detail="date must be YYYY-MM-DD")

    bds = (db.query(BlockDay)
             .options(joinedload(BlockDay.slots))
             .filter(BlockDay.block_date == d)
             .order_by(BlockDay.facility).all())

    blackouts = (db.query(SurgeryBlackoutDay)
                   .filter(SurgeryBlackoutDay.blackout_date == d)
                   .all())

    days_out = []
    booked_surgery_ids: list = []
    for bd in bds:
        for sl in bd.slots:
            if sl.surgery_id:
                booked_surgery_ids.append(sl.surgery_id)
    if booked_surgery_ids:
        booked = {str(s.id): s
                  for s in db.query(Surgery)
                              .filter(Surgery.id.in_(booked_surgery_ids)).all()}
    else:
        booked = {}

    for bd in bds:
        # Build the 30-min grid. Each row: {start_time, surgery_id?, patient_name?, ...}
        slots_by_minute: dict[int, dict] = {}
        for sl in bd.slots:
            mins = sl.start_time.hour * 60 + sl.start_time.minute
            sid = str(sl.surgery_id) if sl.surgery_id else None
            s = booked.get(sid) if sid else None
            slots_by_minute[mins] = {
                "slot_id":          str(sl.id),
                "surgery_id":       sid,
                "patient_name":     s.patient_name if s else None,
                "patient_chart":    s.chart_number if s else None,
                "duration_minutes": sl.duration_minutes,
                "procedure_kind":   sl.procedure_kind,
                "surgeon_email":    s.surgeon_email if s else None,
                "status":           s.status if s else None,
            }
        # 30-min increments covering bd.start_time -> bd.end_time
        grid = []
        cur = bd.start_time.hour * 60 + bd.start_time.minute
        end_min = bd.end_time.hour * 60 + bd.end_time.minute
        while cur < end_min:
            h, m = divmod(cur, 60)
            booking = slots_by_minute.get(cur)
            grid.append({
                "time":    f"{h:02d}:{m:02d}",
                "booking": booking,   # None = open slot
            })
            cur += 30
        days_out.append({
            "id":         str(bd.id),
            "facility":   bd.facility,
            "block_kind": bd.block_kind,
            "start_time": str(bd.start_time),
            "end_time":   str(bd.end_time),
            "is_addon":   bd.is_addon,
            "notes":      bd.notes,
            "grid":       grid,
        })

    # Unscheduled surgeries that could fit in this day's facilities
    facilities_with_blocks = {bd.facility for bd in bds}
    unscheduled = []
    if facilities_with_blocks:
        candidates = (db.query(Surgery)
                        .filter(Surgery.status.in_(("new", "in_progress", "confirmed")),
                                Surgery.scheduled_date.is_(None))
                        .order_by(Surgery.preop_date.asc().nullslast(),
                                  Surgery.created_at.asc()).all())
        for s in candidates:
            eligible = set(s.eligible_facilities or [])
            if eligible & facilities_with_blocks:
                unscheduled.append({
                    "id":            str(s.id),
                    "patient_name":  s.patient_name,
                    "chart_number":  s.chart_number,
                    "preop_date":    str(s.preop_date) if s.preop_date else None,
                    "eligible_facilities": list(eligible),
                    "selected_facility":   s.selected_facility,
                    "estimated_minutes":   s.estimated_minutes,
                    "procedure_classification": s.procedure_classification,
                    "status":              s.status,
                })

    return {
        "date":         str(d),
        "block_days":   days_out,
        "blackouts":    [
            {"id": str(b.id), "scope": b.scope, "reason": b.reason,
             "label": b.label, "owner_email": b.owner_email,
             "facility": b.facility, "notes": b.notes}
            for b in blackouts
        ],
        "unscheduled_surgeries": unscheduled,
    }


class BlockDayCreateIn(BaseModel):
    block_date:  str          # YYYY-MM-DD
    facility:    str          # medstar | crmc | office (etc.)
    block_kind:  str = "addon"   # 'addon' for ad-hoc; 'regular' for materialize
    start_time:  str          # HH:MM
    end_time:    str          # HH:MM
    notes:       Optional[str] = None


@router.post("/admin/block-days", status_code=201)
def create_block_day(payload: BlockDayCreateIn,
                       db: Session = Depends(get_db),
                       current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.MANAGE))):
    """Create a one-off (ad-hoc) BlockDay so coordinators can mark a
    date as a surgery day without editing the recurring BlockSchedule.
    Use for: vacation make-ups, extra hospital days, weekend cases."""
    if payload.facility not in ("medstar", "crmc", "office",
                                  "wwc_office_white_plains"):
        raise HTTPException(status_code=422, detail="invalid facility")
    try:
        bd_date = datetime.strptime(payload.block_date[:10], "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=422, detail="block_date must be YYYY-MM-DD")
    try:
        st = datetime.strptime(payload.start_time[:5], "%H:%M").time()
        et = datetime.strptime(payload.end_time[:5],   "%H:%M").time()
    except ValueError:
        raise HTTPException(status_code=422, detail="start_time/end_time must be HH:MM")
    if st >= et:
        raise HTTPException(status_code=422, detail="start_time must be before end_time")

    # Multiple block windows per day are allowed (morning + afternoon
    # blocks for the same facility, for example). Still reject the
    # exact same window twice — that'd be a coordinator mis-click,
    # not a feature.
    duplicate = (db.query(BlockDay)
                   .filter(BlockDay.facility == payload.facility,
                           BlockDay.block_date == bd_date,
                           BlockDay.start_time == st,
                           BlockDay.end_time == et).first())
    if duplicate:
        raise HTTPException(
            status_code=409,
            detail=f"A {payload.facility} block at {st}–{et} already "
                   f"exists on {bd_date}")
    # Reject windows that overlap an existing block window on the same
    # date+facility — having two windows is fine, but they should be
    # disjoint so booked slots can't overlap.
    same_day = (db.query(BlockDay)
                  .filter(BlockDay.facility == payload.facility,
                          BlockDay.block_date == bd_date).all())
    for other in same_day:
        if st < other.end_time and et > other.start_time:
            raise HTTPException(
                status_code=409,
                detail=(f"This window overlaps an existing {payload.facility} "
                        f"block {other.start_time}–{other.end_time} on "
                        f"{bd_date}. Adjust the times or delete the other "
                        "block first."))

    bd = BlockDay(
        facility=payload.facility,
        block_date=bd_date,
        block_kind=payload.block_kind or "addon",
        start_time=st,
        end_time=et,
        is_addon=(payload.block_kind != "regular"),
        notes=payload.notes,
        created_by=current_user.get("email"),
    )
    db.add(bd); db.commit(); db.refresh(bd)
    return {"id": str(bd.id), "block_date": str(bd.block_date),
            "facility": bd.facility,
            "start_time": str(bd.start_time), "end_time": str(bd.end_time)}


@router.delete("/admin/block-days/{block_day_id}", status_code=204)
def delete_block_day(block_day_id: str,
                       db: Session = Depends(get_db),
                       current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.MANAGE))):
    """Remove an ad-hoc BlockDay (or any unbooked BlockDay). Refuses
    if any SurgerySlot still references it — those surgeries would be
    left with a scheduled_date pointing at a deleted BlockDay. Use the
    surgery's reschedule / cancel flow to free the slots first, then
    re-delete the day.

    For *recurring* BlockDays materialized from a BlockSchedule, the
    next materialize sweep will re-create them; delete the underlying
    BlockSchedule first if you want the deletion to stick."""
    bd = (db.query(BlockDay)
            .options(joinedload(BlockDay.slots))
            .filter(BlockDay.id == block_day_id).first())
    if not bd:
        raise HTTPException(status_code=404, detail="BlockDay not found")
    if bd.slots:
        n = len(bd.slots)
        raise HTTPException(status_code=409,
            detail=(f"This BlockDay has {n} booked slot(s). Cancel or "
                    "reschedule those surgeries first, then delete the day."))

    from app.services.audit_service import log_action
    log_action(
        db,
        action="SURGERY_BLOCK_DAY_DELETED",
        resource_type="surgery_block_day",
        resource_id=str(bd.id),
        user_name=current_user.get("email") or "system",
        description=(f"Deleted {bd.facility} BlockDay {bd.block_date} "
                     f"({bd.start_time}-{bd.end_time}, kind={bd.block_kind})"),
        new_values={
            "facility":   bd.facility,
            "block_date": str(bd.block_date),
            "block_kind": bd.block_kind,
            "start_time": str(bd.start_time),
            "end_time":   str(bd.end_time),
            "is_addon":   bool(bd.is_addon),
            "notes":      bd.notes,
        },
    )
    db.delete(bd); db.commit()
    return None


# ─── Blackout days (US holidays + PTO) ──────────────────────────────

class BlackoutIn(BaseModel):
    blackout_date: str         # YYYY-MM-DD
    scope: str                 # office | provider | facility
    reason: str                # holiday | pto | facility_closed | equipment_down | other
    label: Optional[str] = None
    owner_email: Optional[str] = None
    facility: Optional[str] = None
    notes: Optional[str] = None
    # Partial-day window. Both null = whole-day blackout (legacy
    # default). Both set = partial. Times in HH:MM, must land on a
    # 30-minute boundary, and start < end.
    start_time: Optional[str] = None    # HH:MM
    end_time: Optional[str] = None      # HH:MM


def _parse_30min_time(label: str, value: Optional[str]):
    """Parse 'HH:MM' on a 30-minute boundary. Returns a `time` or None."""
    if value is None or value == "":
        return None
    try:
        hh, mm = value.split(":", 1)
        h = int(hh)
        m = int(mm)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=422, detail=f"{label} must be HH:MM")
    if not (0 <= h <= 23 and m in (0, 30)):
        raise HTTPException(
            status_code=422,
            detail=f"{label} must land on a 30-minute boundary (HH:00 or HH:30)")
    from datetime import time as _time
    return _time(hour=h, minute=m)


@router.get("/admin/blackouts")
def list_blackouts(
    days: int = 365,
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.VIEW)),
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
            "start_time": b.start_time.strftime("%H:%M") if b.start_time else None,
            "end_time":   b.end_time.strftime("%H:%M")   if b.end_time   else None,
            "is_whole_day": b.is_whole_day,
        }
        for b in rows
    ]}


@router.post("/admin/blackouts", status_code=201)
def create_blackout(payload: BlackoutIn, db: Session = Depends(get_db),
                     current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.MANAGE))):
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

    # Partial-day window: both must be set together; both null = whole-day.
    st = _parse_30min_time("start_time", payload.start_time)
    et = _parse_30min_time("end_time", payload.end_time)
    if (st is None) != (et is None):
        raise HTTPException(
            status_code=422,
            detail="start_time and end_time must both be provided for a "
                   "partial-day blackout, or both omitted for a whole-day blackout")
    if st is not None and st >= et:
        raise HTTPException(status_code=422,
                            detail="start_time must be before end_time")

    row = SurgeryBlackoutDay(
        blackout_date=bd,
        scope=payload.scope,
        reason=payload.reason,
        label=payload.label,
        owner_email=payload.owner_email,
        facility=payload.facility,
        notes=payload.notes,
        start_time=st,
        end_time=et,
        created_by=current_user.get("email"),
    )
    db.add(row); db.commit(); db.refresh(row)
    return {"id": str(row.id)}


@router.delete("/admin/blackouts/{blackout_id}", status_code=204)
def delete_blackout(blackout_id: str, db: Session = Depends(get_db),
                     current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.MANAGE))):
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
                        current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.WORK))):
    from app.services.surgery.block_schedule import book_slot, CapacityViolation
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
                     current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.WORK))):
    """Return upcoming block days that can fit this surgery's procedure
    classification. Same logic as the patient-facing slot list; intended
    for the scheduler-side "Pick date" modal."""
    from app.services.surgery.date_picker import (
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
                         current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.WORK))):
    """Scheduler picks (or reschedules) a date on a patient's behalf.
    Same rule set as the patient-facing flow except:
      - No 14-day reschedule lockout (staff can always reschedule)
      - Stamps last_rescheduled_by with the staff email
    """
    from app.services.surgery.date_picker import pick_or_reschedule, DatePickerError

    s = (db.query(Surgery)
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
        "surgery": _surgery_dict(db, s),
    }


# ─── Scheduler workflow toggles ────────────────────────────────────

class ToggleConfirmPayload(BaseModel):
    confirmed: bool = True


@router.post("/{surgery_id}/modmed-scheduled")
def toggle_modmed_scheduled(
    surgery_id: str,
    payload: ToggleConfirmPayload = ToggleConfirmPayload(),
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.WORK)),
):
    """Mark the appointment as added to (or removed from) the ModMed schedule."""
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="surgery not found")
    if payload.confirmed:
        s.scheduled_in_modmed_at = now_utc_naive()
        s.scheduled_in_modmed_by = current_user.get("email") or "system"
    else:
        s.scheduled_in_modmed_at = None
        s.scheduled_in_modmed_by = None
    db.commit(); db.refresh(s)
    return _surgery_dict(db, s)


@router.post("/{surgery_id}/office-meds-pickup")
def toggle_office_meds_pickup(
    surgery_id: str,
    payload: ToggleConfirmPayload = ToggleConfirmPayload(),
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.WORK)),
):
    """Mark that the office-procedure patient has confirmed picking up their meds."""
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="surgery not found")
    if s.selected_facility != "office":
        raise HTTPException(status_code=409,
                            detail="Med pickup only applies to office procedures.")
    if payload.confirmed:
        s.office_meds_pickup_confirmed_at = now_utc_naive()
        s.office_meds_pickup_confirmed_by = current_user.get("email") or "system"
    else:
        s.office_meds_pickup_confirmed_at = None
        s.office_meds_pickup_confirmed_by = None
    db.commit(); db.refresh(s)
    return _surgery_dict(db, s)


# ─── Coordinator schedule endpoint (Phase D2) ───────────────────────

class CoordinatorScheduleIn(BaseModel):
    block_day_id: str
    start_time: str
    duration_minutes: Optional[int] = None
    override_reason: Optional[str] = None


@router.post("/{surgery_id}/schedule")
def coordinator_schedule(
    surgery_id: str,
    payload: CoordinatorScheduleIn,
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.WORK)),
):
    from app.models.surgery import SurgeryNote
    from app.routers.patient_surgery import _parse_hhmm, _default_duration_for
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="surgery not found")
    bd = db.query(BlockDay).filter(BlockDay.id == payload.block_day_id).first()
    if not bd:
        raise HTTPException(status_code=404, detail="block day not found")
    start = _parse_hhmm(payload.start_time)
    default = _default_duration_for(db, s, bd)
    duration = payload.duration_minutes or default

    # Pass slot's window so a partial-day blackout only blocks if it
    # actually overlaps this slot, not the whole day.
    end_min = start.hour * 60 + start.minute + duration
    from datetime import time as _t
    end_t = _t(end_min // 60 % 24, end_min % 60)
    blackout = is_date_blacked_out(
        db, bd.block_date,
        s.selected_facility or bd.facility,
        s.surgeon_email,
        start_time=start, end_time=end_t,
    )
    if blackout:
        raise HTTPException(
            status_code=409,
            detail=f"that date is blocked: {blackout.label or blackout.reason} "
                   f"({blackout.scope})",
        )

    # If >10% off the template default, require an override reason.
    threshold = default * 0.10
    if abs(duration - default) > threshold and not (payload.override_reason or "").strip():
        raise HTTPException(status_code=422,
                            detail="override_reason required: duration differs >10% from template default")

    actor = current_user.get("email") or "system"
    # Route through book_slot so we get: BlockDay row lock (closes the
    # TOCTOU race on overlap), can_fit capacity check, block-window
    # guard, prior-slot release, and a single writer of
    # scheduled_date/scheduled_start_time/selected_facility/status.
    # (Fable surgery audit C1.)
    from app.services.surgery.block_schedule import book_slot, CapacityViolation
    # procedure_kind must be the *surgery's* classification (robotic_180,
    # robotic_240, minor, major, office) — NOT the block day's kind
    # ("robotic_only", "mixed", etc.). Passing bd.block_kind here caused
    # can_fit() to fall through to the catchall "MedStar block doesn't
    # accept robotic_only cases" error message.
    try:
        slot = book_slot(
            db, block_day_id=str(bd.id), surgery_id=str(s.id),
            start_time=start, duration_minutes=duration,
            procedure_kind=s.procedure_classification or "minor",
        )
    except CapacityViolation as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    audit_body = (f"Coordinator scheduled {bd.block_date} {start.strftime('%H:%M')} "
                  f"({duration} min, template default {default} min) at {bd.facility}.")
    if payload.override_reason:
        audit_body += f" Override reason: {payload.override_reason}"
    db.add(SurgeryNote(
        surgery_id=s.id, created_by=actor,
        content=audit_body,
    ))
    db.commit()
    try:
        from app.services.google_calendar_sync import upsert_event_for_surgery
        upsert_event_for_surgery(db, s)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("calendar sync failed: %s", e)
    try:
        from app.routers.patient_surgery import _send_surgery_confirmation_email
        _send_surgery_confirmation_email(db, s, slot, sent_by=actor)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("confirmation email failed: %s", e)
    return {"ok": True, "slot_id": str(slot.id),
            "start_time": start.strftime("%H:%M"),
            "duration_minutes": duration,
            "template_default": default}


# ─── Surgery notes (timestamped log) ───────────────────────────────

class SurgeryNoteIn(BaseModel):
    content: str


@router.get("/{surgery_id}/notes")
def list_surgery_notes(
    surgery_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.VIEW)),
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
    current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.WORK)),
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
    current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.WORK)),
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
    has_manage = (current_user.get("module_tier") or {}).get("surgery", 0) >= int(Tier.MANAGE)
    if n.created_by.lower() != email and not has_manage:
        raise HTTPException(status_code=403,
                            detail="Only the author or a surgery manager can delete this note.")
    db.delete(n); db.commit()
    return None


@router.post("/{surgery_id}/blocked-conflict/resolve")
def resolve_blocked_conflict(
    surgery_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.WORK)),
):
    """Mark that the hospital has been notified of a blackout-day conflict.
    Stamps blocked_conflict_notified_at so the surgery is excluded from
    subsequent find_blocked_conflicts() calls and the dashboard alert list."""
    from app.models.surgery import SurgeryNote
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="surgery not found")
    actor = current_user.get("email") or "system"
    s.blocked_conflict_notified_at = now_utc_naive()
    s.blocked_conflict_notified_by = actor

    # Audit trail
    db.add(SurgeryNote(
        surgery_id=s.id,
        created_by=actor,
        content=f"Marked hospital notified of conflict on {s.scheduled_date}.",
    ))
    db.commit()
    return {"ok": True, "notified_at": s.blocked_conflict_notified_at.isoformat()}


# ─── Post-op appointments ──────────────────────────────────────────

class PostOpApptsPayload(BaseModel):
    first_date: Optional[str] = None       # YYYY-MM-DD or null/blank to clear
    second_date: Optional[str] = None
    first_location: Optional[str] = None   # "office" | "telehealth" | null
    second_location: Optional[str] = None


@router.get("/{surgery_id}/post-op-schedule")
def get_post_op_schedule(
    surgery_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.VIEW)),
):
    """Return the post-op visits this surgery's procedures require, plus
    suggested dates relative to scheduled_date. Used by the frontend to
    pre-populate the date pickers."""
    from app.services.post_op_schedule import determine_post_op_schedule
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="surgery not found")
    visits = determine_post_op_schedule(s, db=db)
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
    current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.WORK)),
):
    """Record the patient's post-op appointment dates. Auto-completes the
    post_op_appts_scheduled milestone once every required appt has a date."""
    from app.services.post_op_schedule import all_required_appts_filled

    s = (db.query(Surgery)
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

    def _normalize_location(v):
        if v is None or v == "":
            return None
        v = v.strip().lower()
        if v not in ("office", "telehealth"):
            raise HTTPException(status_code=422,
                                detail=f"invalid location: {v}")
        return v
    first_loc  = _normalize_location(payload.first_location)
    second_loc = _normalize_location(payload.second_location)

    # Safety net: clinically-required in-person visits can't be saved
    # as telehealth even if a stale frontend tries to.
    from app.services.post_op_schedule import determine_post_op_schedule
    rule_visits = determine_post_op_schedule(s, db=db)
    if len(rule_visits) > 0 and rule_visits[0].location_locked and first_loc == "telehealth":
        raise HTTPException(
            status_code=422,
            detail=f"{rule_visits[0].label} must be in-person")
    if len(rule_visits) > 1 and rule_visits[1].location_locked and second_loc == "telehealth":
        raise HTTPException(
            status_code=422,
            detail=f"{rule_visits[1].label} must be in-person")

    s.post_op_appt_location     = first_loc
    s.post_op_appt_2nd_location = second_loc

    db.commit(); db.refresh(s)
    return _surgery_dict(db, s)


# ─── Assistant surgeon coordination ────────────────────────────────

@router.post("/{surgery_id}/assistant-surgeon/notify-office")
def assistant_surgeon_notify_office(
    surgery_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.WORK)),
):
    """Mark the assistant surgeon's office as notified.
    Closes the assistant_surgeon milestone if the appt is also confirmed."""
    s = (db.query(Surgery)
           .filter(Surgery.id == surgery_id).first())
    if not s:
        raise HTTPException(status_code=404, detail="surgery not found")
    if not s.assistant_surgeon_required:
        raise HTTPException(status_code=409,
                            detail="Assistant surgeon is not required for this surgery.")
    by = current_user.get("email") or "system"
    s.assistant_surgeon_office_notified_at = now_utc_naive()
    s.assistant_surgeon_office_notified_by = by
    db.commit(); db.refresh(s)
    return _surgery_dict(db, s)


class AssistantApptConfirm(BaseModel):
    appt_date: Optional[str] = None   # YYYY-MM-DD (optional — clears if blank)


@router.post("/{surgery_id}/assistant-surgeon/confirm-appt")
def assistant_surgeon_confirm_appt(
    surgery_id: str,
    payload: AssistantApptConfirm = AssistantApptConfirm(),
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.WORK)),
):
    """Confirm the patient has scheduled an appointment with the assistant
    surgeon. Optional appt_date records the date; absence still counts as
    confirmed (some practices just want a yes/no)."""
    s = (db.query(Surgery)
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
    s.assistant_surgeon_appt_confirmed_at = now_utc_naive()
    s.assistant_surgeon_appt_confirmed_by = by
    db.commit(); db.refresh(s)
    return _surgery_dict(db, s)


@router.post("/{surgery_id}/assistant-surgeon/reset")
def assistant_surgeon_reset(
    surgery_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.WORK)),
):
    """Clear notified / appt confirmation (e.g. patient missed the appt and
    needs to reschedule). Reopens the milestone if it was done."""
    s = (db.query(Surgery)
           .filter(Surgery.id == surgery_id).first())
    if not s:
        raise HTTPException(status_code=404, detail="surgery not found")
    s.assistant_surgeon_office_notified_at = None
    s.assistant_surgeon_office_notified_by = None
    s.assistant_surgeon_appt_confirmed_at = None
    s.assistant_surgeon_appt_confirmed_by = None
    s.assistant_surgeon_appt_date = None
    db.commit(); db.refresh(s)
    return _surgery_dict(db, s)


# ─── AI billing-code suggestion (Phase 3) ───────────────────────────

@router.post("/{surgery_id}/suggest-billing-codes")
def suggest_billing_codes(surgery_id: str,
                            db: Session = Depends(get_db),
                            current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.WORK))):
    """Read the surgery's op note + path report, ask Claude for ICD-10 /
    CPT / modifier / POS codes, and auto-save them on the Surgery row.
    If any CPT uses modifier 22, a justification letter PDF is generated
    and saved as a SurgeryFile."""
    from app.services.surgery.billing_ai import (
        suggest_and_save_billing, BillingAIError,
    )

    s = (db.query(Surgery)
           .options(joinedload(Surgery.files))
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
        "surgery": _surgery_dict(db, s),
    }


# ─── Benefits calculator (Phase 2.9) ────────────────────────────────

class BenefitsPayload(BaseModel):
    """All fields optional. Missing values → treated as $0 / 0%.
    The calculator runs on whatever's provided; staff can save partial
    inputs and refine later."""
    deductible:      Optional[DollarAmount]  = None    # annual plan deductible
    deductible_met:  Optional[DollarAmount]  = None    # patient progress
    copay:           Optional[DollarAmount]  = None    # fixed copay
    coinsurance_pct: Optional[PercentAmount] = None    # 20.0 = 20%
    oop_max:         Optional[DollarAmount]  = None    # annual OOP max
    oop_met:         Optional[DollarAmount]  = None
    allowed_amount:  Optional[DollarAmount]  = None    # insurance-allowed
    # Secondary insurance — when present, runs a second pass that reduces
    # the primary responsibility by what secondary covers.
    secondary_deductible:      Optional[DollarAmount]  = None
    secondary_deductible_met:  Optional[DollarAmount]  = None
    secondary_copay:           Optional[DollarAmount]  = None
    secondary_coinsurance_pct: Optional[PercentAmount] = None
    secondary_oop_max:         Optional[DollarAmount]  = None
    secondary_oop_met:         Optional[DollarAmount]  = None
    # Card-on-file metadata
    card_on_file: Optional[bool] = None
    save: bool = True   # set False to preview without persisting


# Coordinators historically typed sentinel strings ("No Secondary", "None",
# "N/A") in the secondary_insurance column to mean "no policy". Anything in
# this set should NOT trigger the secondary-payer math; the empty string is
# the genuine empty case.
_NO_SECONDARY_SENTINELS = {
    "", "no secondary", "none", "n/a", "na", "no", "-", "--",
}


def _has_real_secondary(secondary_insurance: Optional[str]) -> bool:
    return (secondary_insurance or "").strip().lower() not in _NO_SECONDARY_SENTINELS


def _one_payer_share(*, base: float, deductible: float, deductible_met: float,
                       copay: float, coinsurance_pct: float,
                       oop_max: float, oop_met: float) -> dict:
    """Run the standard payer math against `base` (= allowed for primary;
    = post-primary patient share for secondary). Returns the payer's own
    breakdown (their deductible portion, coinsurance portion, copay)
    *plus* the resulting patient-owed amount on that pass."""
    deductible_remaining = max(0.0, deductible - deductible_met)
    oop_remaining = max(0.0, oop_max - oop_met) if oop_max > 0 else float("inf")

    deductible_portion = min(base, deductible_remaining)
    after_deductible   = base - deductible_portion
    coins_rate         = coinsurance_pct / 100.0
    coinsurance_portion = round(after_deductible * coins_rate, 2)
    raw                = deductible_portion + coinsurance_portion + copay
    capped             = round(min(raw, oop_remaining), 2)

    return {
        "deductible_remaining": round(deductible_remaining, 2),
        "deductible_portion":   round(deductible_portion, 2),
        "after_deductible":     round(after_deductible, 2),
        "coinsurance_portion":  coinsurance_portion,
        "copay_portion":        round(copay, 2),
        "raw":                  round(raw, 2),
        "patient_owed":         capped,
        "capped_by_oop_max":    raw > oop_remaining,
        "oop_remaining":        (round(oop_remaining, 2) if oop_remaining != float("inf") else None),
    }


def _calc_patient_responsibility(*, allowed_amount: float,
                                   deductible: float, deductible_met: float,
                                   copay: float, coinsurance_pct: float,
                                   oop_max: float, oop_met: float,
                                   secondary_deductible: float = 0,
                                   secondary_deductible_met: float = 0,
                                   secondary_copay: float = 0,
                                   secondary_coinsurance_pct: float = 0,
                                   secondary_oop_max: float = 0,
                                   secondary_oop_met: float = 0,
                                   has_secondary: bool = False) -> dict:
    """Two-stage health-plan math.

    Stage 1 (primary): patient owes deductible_portion + coinsurance + copay
    (capped at primary OOP-max remaining).

    Stage 2 (secondary, if present): the secondary payer applies its own
    deductible / coinsurance / copay against the patient's stage-1 share;
    whatever secondary doesn't cover is the final patient responsibility.
    """
    primary = _one_payer_share(
        base=allowed_amount,
        deductible=deductible, deductible_met=deductible_met,
        copay=copay, coinsurance_pct=coinsurance_pct,
        oop_max=oop_max, oop_met=oop_met)

    if has_secondary:
        secondary = _one_payer_share(
            base=primary["patient_owed"],
            deductible=secondary_deductible,
            deductible_met=secondary_deductible_met,
            copay=secondary_copay,
            coinsurance_pct=secondary_coinsurance_pct,
            oop_max=secondary_oop_max, oop_met=secondary_oop_met)
        final = secondary["patient_owed"]
    else:
        secondary = None
        final = primary["patient_owed"]

    return {
        # Stage-1 fields kept at the top level for backwards compatibility
        # with the frontend's live calc + the PDF.
        "deductible_remaining":   primary["deductible_remaining"],
        "deductible_portion":     primary["deductible_portion"],
        "after_deductible":       primary["after_deductible"],
        "coinsurance_portion":    primary["coinsurance_portion"],
        "copay_portion":          primary["copay_portion"],
        "oop_remaining":          primary["oop_remaining"],
        "raw_responsibility":     primary["raw"],
        "primary_patient_owed":   primary["patient_owed"],
        "capped_by_oop_max":      primary["capped_by_oop_max"],
        "secondary":              secondary,
        "patient_responsibility": final,
    }


@router.post("/{surgery_id}/benefits")
def benefits_endpoint(surgery_id: str, payload: BenefitsPayload,
                       db: Session = Depends(get_db),
                       current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.WORK))):
    """Calculate (and optionally save) the patient's surgery responsibility
    from insurance benefit inputs. When save=True, also marks the
    benefits_determined milestone as done."""
    s = (db.query(Surgery)
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

    has_secondary = _has_real_secondary(s.secondary_insurance)

    breakdown = _calc_patient_responsibility(
        allowed_amount=_g("allowed_amount"),
        deductible=_g("deductible"),
        deductible_met=_g("deductible_met"),
        copay=_g("copay"),
        coinsurance_pct=_g("coinsurance_pct"),
        oop_max=_g("oop_max"),
        oop_met=_g("oop_met"),
        secondary_deductible=_g("secondary_deductible"),
        secondary_deductible_met=_g("secondary_deductible_met"),
        secondary_copay=_g("secondary_copay"),
        secondary_coinsurance_pct=_g("secondary_coinsurance_pct"),
        secondary_oop_max=_g("secondary_oop_max"),
        secondary_oop_met=_g("secondary_oop_met"),
        has_secondary=has_secondary,
    )

    pdf_file_id = None
    if payload.save:
        # Persist whatever inputs were provided
        for field in ("deductible", "deductible_met", "copay",
                       "coinsurance_pct", "oop_max", "oop_met", "allowed_amount",
                       "secondary_deductible", "secondary_deductible_met",
                       "secondary_copay", "secondary_coinsurance_pct",
                       "secondary_oop_max", "secondary_oop_met"):
            v = getattr(payload, field, None)
            if v is not None:
                setattr(s, field, v)
        if payload.card_on_file is not None:
            s.card_on_file = payload.card_on_file
        s.patient_responsibility = breakdown["patient_responsibility"]
        s.benefits_verified_at = _date.today()

        _commit_or_409(db, surgery_id=surgery_id); db.refresh(s)

        # Generate the patient-facing PDF estimate and attach it
        try:
            from app.services.surgery.benefits_pdf import generate_and_attach
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


# ─── Manual payment offsets (ModPay / Check / Cash / Other) ─────────

_MANUAL_PAYMENT_METHODS = {"modpay", "check", "cash", "other"}


class ManualPaymentPayload(BaseModel):
    method: str           # modpay | check | cash | other
    # Field(gt=0, le=99_999.99) also rejects NaN/Inf cleanly (both compare
    # False against numbers), so we get a 422 instead of crashing on
    # JSON-encode or Numeric(10,2) overflow downstream.
    amount: float = Field(gt=0, le=99_999.99)
    note:   Optional[str] = None


@router.post("/{surgery_id}/payments/manual", status_code=201)
def record_manual_payment(surgery_id: str, payload: ManualPaymentPayload,
                            db: Session = Depends(get_db),
                            current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.WORK))):
    """Record a payment that happened outside Stripe (ModMed Pay swipe,
    check, cash, etc.). Bumps surgery.amount_paid and adds a SurgeryPayment
    row (status='paid', kind='manual_offset') so it appears in the
    Payment Status history."""
    method = (payload.method or "").strip().lower()
    if method not in _MANUAL_PAYMENT_METHODS:
        raise HTTPException(status_code=422,
                            detail=f"method must be one of {sorted(_MANUAL_PAYMENT_METHODS)}")
    amt = round(float(payload.amount or 0), 2)
    if amt <= 0:
        raise HTTPException(status_code=422, detail="amount must be > 0")

    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="surgery not found")

    from app.models.stripe_payment import SurgeryPayment
    from app.models.surgery import SurgeryNote

    # Idempotency window — a double-click on "Record" used to create two
    # SurgeryPayment rows and double-bump amount_paid. Reject a second
    # manual-offset for the same surgery + amount within 60s. The window
    # is narrow enough that legitimate same-amount cash entries (e.g.
    # two patients paying their copay within the same minute on
    # different surgeries) are unaffected — different surgery_id.
    # (Fable surgery audit H4.)
    cutoff = now_utc_naive() - timedelta(seconds=60)
    recent = (db.query(SurgeryPayment)
                .filter(SurgeryPayment.surgery_id == s.id,
                        SurgeryPayment.kind == "manual_offset",
                        SurgeryPayment.amount_paid == Decimal(str(amt)),
                        SurgeryPayment.paid_at >= cutoff)
                .first())
    if recent:
        raise HTTPException(
            status_code=409,
            detail=(f"a manual payment of ${amt:.2f} was just recorded on this "
                    f"surgery {int((now_utc_naive() - recent.paid_at).total_seconds())}s ago "
                    f"(id {recent.id}). If this is intentional, wait 60s and re-submit."))

    pretty = {"modpay": "ModMed Pay", "check": "Check",
              "cash": "Cash", "other": "Other"}[method]
    description = f"Manual payment · {pretty}" + (f" — {payload.note}" if payload.note else "")

    pay = SurgeryPayment(
        surgery_id=s.id,
        amount_requested=Decimal(str(amt)),
        amount_paid=Decimal(str(amt)),
        currency="usd",
        status="paid",
        kind="manual_offset",
        description=description,
        requested_by=current_user.get("email") or "system",
        paid_at=now_utc_naive(),
    )
    db.add(pay)

    prior = Decimal(str(s.amount_paid or 0))
    s.amount_paid = prior + Decimal(str(amt))

    db.add(SurgeryNote(
        surgery_id=s.id,
        created_by=current_user.get("email"),
        content=(f"Manual payment recorded: ${amt:.2f} via {pretty}"
                  + (f" — {payload.note}" if payload.note else "")
                  + f". New amount paid: ${float(s.amount_paid):.2f}."),
    ))
    db.commit(); db.refresh(s); db.refresh(pay)

    log_action(
        db,
        action="PAYMENT_RECORDED",
        resource_type="surgery_payment",
        resource_id=str(pay.id),
        patient_id=s.chart_number or None,
        user_id=(current_user.get("email") or "").lower() or None,
        user_name=current_user.get("name") or current_user.get("email"),
        description=f"Manual payment ${amt:.2f} via {pretty} on surgery {s.id}",
    )

    outstanding = float(s.patient_responsibility or 0) - float(s.amount_paid or 0)
    return {
        "payment_id":   str(pay.id),
        "amount_paid":  float(s.amount_paid or 0),
        "outstanding":  round(outstanding, 2),
    }


class ManualPaymentVoidPayload(BaseModel):
    reason: str


@router.post("/{surgery_id}/payments/{payment_id}/void")
def void_manual_payment(surgery_id: str, payment_id: str,
                          payload: ManualPaymentVoidPayload,
                          db: Session = Depends(get_db),
                          current_user: dict = Depends(requires_super_admin())):
    """Void a previously-recorded manual offset (ModMed Pay / check / cash /
    other). Super-admin only. Soft-marks the SurgeryPayment row as voided,
    rolls amount_paid back, and writes both a SurgeryNote and a
    SurgeryPaymentHistory entry. This is the legitimate refund path for
    manual entries — Stripe-backed payments must be refunded through Stripe.
    """
    from app.models.stripe_payment import SurgeryPayment, SurgeryPaymentHistory
    from app.models.surgery import SurgeryNote
    reason = (payload.reason or "").strip()
    if not reason:
        raise HTTPException(status_code=422, detail="reason is required")

    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="surgery not found")
    pay = (db.query(SurgeryPayment)
             .filter(SurgeryPayment.id == payment_id,
                     SurgeryPayment.surgery_id == s.id).first())
    if pay is None:
        raise HTTPException(status_code=404, detail="payment not found")
    if pay.kind != "manual_offset":
        raise HTTPException(status_code=409,
                            detail="only manual_offset payments can be voided here — "
                                   "Stripe payments must be refunded through Stripe")
    if pay.status == "voided":
        raise HTTPException(status_code=409, detail="payment is already voided")

    before_status = pay.status
    amt = Decimal(str(pay.amount_paid or 0))
    pay.status = "voided"
    pay.amount_refunded = (pay.amount_refunded or Decimal(0)) + amt
    pay.refunded_at = now_utc_naive()
    s.amount_paid = Decimal(str(s.amount_paid or 0)) - amt

    db.add(SurgeryPaymentHistory(
        payment_id=pay.id,
        actor=current_user.get("email") or "system",
        event_type="admin.manual_offset_voided",
        before_status=before_status,
        after_status="voided",
        detail={"reason": reason, "amount_voided": str(amt)},
    ))
    db.add(SurgeryNote(
        surgery_id=s.id,
        created_by=current_user.get("email") or "system",
        content=(f"Manual payment voided: ${float(amt):.2f} (payment {pay.id}). "
                 f"Reason: {reason}. "
                 f"New amount paid: ${float(s.amount_paid):.2f}."),
    ))
    db.commit(); db.refresh(s); db.refresh(pay)

    log_action(
        db,
        action="PAYMENT_VOIDED",
        resource_type="surgery_payment",
        resource_id=str(pay.id),
        patient_id=s.chart_number or None,
        user_id=(current_user.get("email") or "").lower() or None,
        user_name=current_user.get("name") or current_user.get("email"),
        description=f"Voided manual payment ${float(amt):.2f} on surgery {s.id}: {reason}",
    )

    return {
        "payment_id":   str(pay.id),
        "status":       pay.status,
        "amount_voided": float(amt),
        "amount_paid":  float(s.amount_paid or 0),
    }


# ─── Consent transitions (Phase 2.8) ────────────────────────────────

class ConsentTransitionPayload(BaseModel):
    notes: Optional[str] = None


@router.post("/{surgery_id}/consent/sent")
def consent_mark_sent(surgery_id: str, payload: ConsentTransitionPayload = ConsentTransitionPayload(),
                       db: Session = Depends(get_db),
                       current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.WORK))):
    """Mark that consent has been sent to the patient (paper or DocuSign).
    Sets consent_status='sent', stamps consent_sent_at, moves the consent
    milestone to 'in_progress' (still pending the signature)."""
    s = (db.query(Surgery)
           .filter(Surgery.id == surgery_id).first())
    if not s:
        raise HTTPException(status_code=404, detail="surgery not found")

    s.consent_status = "sent"
    s.consent_sent_at = now_utc_naive()
    _commit_or_409(db, surgery_id=surgery_id); db.refresh(s)
    return _surgery_dict(db, s)


@router.get("/{surgery_id}/consent/template-matches")
def consent_template_matches(surgery_id: str,
                              db: Session = Depends(get_db),
                              current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.VIEW))):
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
                "boldsign_template_id": m.template.boldsign_template_id,
                "docusign_template_id": m.template.docusign_template_id,
                "matched_procedure": m.matched_procedure,
                "is_supplemental": m.is_supplemental,
                "warning": m.warning,
            } for m in matches
        ],
        "unmatched_procedures": unmatched_procedures(db, s),
    }


@router.post("/{surgery_id}/consent/boldsign-send")
def send_consent_via_boldsign(
    surgery_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.WORK)),
):
    """Send all matching BoldSign consent envelopes for this surgery."""
    from app.services import boldsign_envelopes as bs
    from app.models.surgery import SurgeryConsentEnvelope
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="surgery not found")
    actor = current_user.get("email") or "system"
    try:
        result = bs.send_consent_envelopes(db, s, sent_by=actor)
    except bs.BoldSignEnvelopeError as e:
        raise HTTPException(status_code=502, detail=str(e))
    # Fetch the freshly-written envelope rows so we can return their DB ids
    sent_ids = {item["envelope_id"] for item in result.get("sent", [])}
    rows = (
        db.query(SurgeryConsentEnvelope)
        .filter(
            SurgeryConsentEnvelope.surgery_id == s.id,
            SurgeryConsentEnvelope.boldsign_envelope_id.in_(sent_ids),
        )
        .all()
    ) if sent_ids else []
    return {
        "sent_count": len(rows),
        "envelopes": [{
            "id":                    str(r.id),
            "boldsign_envelope_id":  r.boldsign_envelope_id,
            "consent_template_id":   str(r.template_id) if r.template_id else None,
            "status":                r.status,
        } for r in rows],
    }


@router.post("/admin/consent/boldsign-sync/{surgery_id}")
def sync_boldsign_envelopes(
    surgery_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.WORK)),
):
    """Force-reconcile BoldSign envelope statuses for one surgery. Useful
    if a webhook was missed."""
    from app.services import boldsign_envelopes as bs
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="surgery not found")
    try:
        out = bs.sync_surgery_envelopes(db, s)
    except bs.BoldSignEnvelopeError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return out


@router.post("/{surgery_id}/consent/signed")
def consent_mark_signed(surgery_id: str, payload: ConsentTransitionPayload = ConsentTransitionPayload(),
                         db: Session = Depends(get_db),
                         current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.WORK))):
    """Mark that the patient has signed the consent. Stamps consent_signed_at,
    flips status='signed', closes the consent milestone.

    Requires at least one uploaded SurgeryFile of kind='consent' as
    evidence — the BoldSign pipeline is the preferred path; this manual
    route is for paper consents scanned and uploaded. Without the
    evidence requirement any WORK-tier user could flip consent to
    'signed' on any chart with no audit trail. (Fable surgery audit M3.)
    """
    s = (db.query(Surgery)
           .filter(Surgery.id == surgery_id).first())
    if not s:
        raise HTTPException(status_code=404, detail="surgery not found")

    has_consent_file = (db.query(SurgeryFile)
                          .filter(SurgeryFile.surgery_id == s.id,
                                  SurgeryFile.kind == "consent")
                          .first())
    if not has_consent_file:
        raise HTTPException(
            status_code=409,
            detail=("cannot mark consent signed without an uploaded consent "
                    "document — upload the scanned signed consent to "
                    f"/api/surgery/{s.id}/files (kind=consent) first, or use "
                    "the BoldSign envelope flow"))

    s.consent_status = "signed"
    s.consent_signed_at = now_utc_naive()
    _commit_or_409(db, surgery_id=surgery_id); db.refresh(s)
    # Central audit so a misuse query can find who flipped consent on
    # which chart and tie it back to the SurgeryFile that justified it.
    log_action(
        db,
        action="CONSENT_MARKED_SIGNED",
        resource_type="surgery",
        resource_id=str(s.id),
        patient_id=s.chart_number or None,
        user_id=(current_user.get("email") or "").lower() or None,
        user_name=current_user.get("name") or current_user.get("email"),
        description=(f"Marked consent signed on surgery "
                     f"{s.surgery_number or s.id} (evidence file "
                     f"{has_consent_file.id})"),
    )
    return _surgery_dict(db, s)


@router.post("/{surgery_id}/consent/reset")
def consent_reset(surgery_id: str,
                    db: Session = Depends(get_db),
                    current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.MANAGE))):
    """Revoke every live BoldSign envelope, delete all envelope rows, and
    return the surgery's consent state to 'not_required'. Used by staff to
    re-issue consents from scratch (e.g. after a template was updated, or
    the patient signed under the wrong template by mistake).
    """
    from app.models.surgery import SurgeryConsentEnvelope
    from app.services.boldsign_envelopes import (
        void_envelope_row, BoldSignEnvelopeError,
    )
    s = (db.query(Surgery)
           .filter(Surgery.id == surgery_id).first())
    if not s:
        raise HTTPException(status_code=404, detail="surgery not found")

    revoked: list[str] = []
    revoke_errors: list[str] = []
    rows = list(db.query(SurgeryConsentEnvelope)
                  .filter(SurgeryConsentEnvelope.surgery_id == s.id).all())
    for row in rows:
        if (row.boldsign_envelope_id
                and (row.status or "").lower() not in ("voided", "revoked",
                                                        "declined", "expired",
                                                        "failed")):
            try:
                void_envelope_row(db, row, reason="Reset by practice")
                revoked.append(row.boldsign_envelope_id)
            except BoldSignEnvelopeError as e:
                # Keep going — we still want to clear the DB even if a
                # stale envelope can't be revoked at BoldSign.
                revoke_errors.append(f"{row.boldsign_envelope_id[:8]}…: {e}")

    # Wipe every envelope row regardless of whether the BoldSign revoke
    # succeeded — staff is explicitly asking for a clean slate.
    for row in rows:
        db.delete(row)

    s.consent_status     = "not_required"
    s.consent_sent_at    = None
    s.consent_signed_at  = None
    s.consent_doc_id     = None

    # Revoke patient JWTs so a stale token can't continue acting on the
    # surgery after the consent state has been wiped. (Fable portal
    # audit H5-auth.)
    from app.services.patient_portal_auth import bump_portal_token_version
    bump_portal_token_version(db, s)

    db.commit(); db.refresh(s)
    return {
        "ok": True,
        "revoked_envelopes": revoked,
        "deleted_rows": len(rows),
        "revoke_errors": revoke_errors,
    }


# ─── Waitlist (Phase 2) ─────────────────────────────────────────────

class WaitlistJoinIn(BaseModel):
    advance_notice_days: int = 7


@router.post("/{surgery_id}/waitlist", status_code=201)
def waitlist_join(surgery_id: str, payload: WaitlistJoinIn,
                    db: Session = Depends(get_db),
                    current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.WORK))):
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
                     current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.WORK))):
    """Remove the surgery from the active waitlist."""
    w = (db.query(SurgeryWaitlist)
           .filter(SurgeryWaitlist.surgery_id == surgery_id,
                   SurgeryWaitlist.removed_at.is_(None))
           .first())
    if not w:
        raise HTTPException(status_code=404, detail="not on the waitlist")
    w.removed_at = now_utc_naive()
    w.removed_reason = reason or "manual"
    db.commit()
    return {"ok": True}


@router.get("/admin/waitlist")
def waitlist_list(facility: Optional[str] = None,
                   procedure_kind: Optional[str] = None,
                   db: Session = Depends(get_db),
                   current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.VIEW))):
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
            "procedure_name": (s.procedures[0].get("name") if s.procedures else None),
            "procedure_descriptions": [
                p.get("description") for p in (s.procedures or []) if p.get("description")
            ],
            "facility": (s.selected_facility
                         or (s.eligible_facilities[0]
                             if s.eligible_facilities else None)),
            "urgency": s.urgency,
            "eligible_facilities": s.eligible_facilities or [],
        })
    return {"waitlist": out}


@router.get("/admin/waitlist-matches")
def waitlist_matches(block_day_id: str,
                      db: Session = Depends(get_db),
                      current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.WORK))):
    """Find waitlisters who could realistically fill the given block day."""
    from app.services.surgery.waitlist import find_matches, klara_blast_text
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
                    current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.WORK))):
    """A waitlisted patient confirmed they want the freed slot — book it
    and remove them from the waitlist."""
    from app.services.surgery.block_schedule import book_slot, CapacityViolation, DURATIONS

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
    # Validate procedure_kind against the known DURATIONS map (Fable
    # surgery audit M6). Without this any string would fall through and
    # silently get a 60-minute default — a robotic_240 mistyped as
    # "robotic240" would book a 60-min slot for a 4-hour case.
    if proc_kind not in DURATIONS:
        raise HTTPException(
            status_code=422,
            detail=(f"procedure_kind {proc_kind!r} not recognized — "
                    f"must be one of {sorted(DURATIONS.keys())}"))
    duration = DURATIONS[proc_kind]

    # (Blackout pre-check removed — book_slot now runs the same check
    # under the row lock, and crucially passes the slot's actual
    # start/end so a partial-day blackout only blocks an overlapping
    # window, not the whole day. The pre-check was a legacy any-time
    # check that over-blocked for partial-day. Fable surgery audit M6
    # was the reason this check originally existed; book_slot's
    # built-in check is its successor.)

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

    # book_slot writes scheduled_date / scheduled_start_time /
    # selected_facility / status — Fable's C2 finding misattributed the
    # gap to this endpoint, but the real missing pieces below are the
    # SurgeryNote audit row, calendar sync, and confirmation email that
    # coordinator_schedule's path produces. Without them a waitlist-
    # claimed surgery has no audit trail and no patient confirmation.

    # Remove from waitlist
    w.removed_at = now_utc_naive()
    w.removed_reason = "claimed_slot"

    from app.models.surgery import SurgeryNote
    actor = current_user.get("email") or "system"
    db.add(SurgeryNote(
        surgery_id=s.id, created_by=actor,
        content=(f"Waitlist claim: booked {bd.block_date} "
                 f"{h:02d}:{m:02d} ({duration} min) at {bd.facility}."),
    ))

    db.commit()

    # Mirror coordinator_schedule's side-effects so the patient gets a
    # confirmation and the Google calendar reflects the booking.
    try:
        from app.services.google_calendar_sync import upsert_event_for_surgery
        upsert_event_for_surgery(db, s)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("calendar sync failed: %s", e)
    try:
        from app.routers.patient_surgery import _send_surgery_confirmation_email
        _send_surgery_confirmation_email(db, s, slot, sent_by=actor)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("confirmation email failed: %s", e)

    return {
        "ok": True,
        "scheduled_date": str(bd.block_date),
        "scheduled_start_time": f"{h:02d}:{m:02d}",
        "facility": bd.facility,
    }


# ─── I7: Ad-hoc patient email composer ──────────────────────────────

class PatientEmailIn(BaseModel):
    subject: str
    body_html: str       # HTML allowed; will be rendered as-is into the template wrapper
    to_email: Optional[str] = None
    # If null, uses Surgery.email. Override lets staff send to a guardian etc.


@router.post("/{surgery_id}/send-patient-email")
def send_ad_hoc_patient_email(
    surgery_id: str,
    payload: PatientEmailIn,
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.WORK)),
):
    """Compose-and-send an ad-hoc email to the patient. Uses the
    generic_patient_message template kind — subject + body are merged into
    the template's wrapper (signature, greeting) via {{subject}} + {{body}}
    placeholders. Recipient defaults to surgery.email but can be overridden
    on the payload (e.g. send to a guardian)."""
    from app.services.patient_email import send_patient_email
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="surgery not found")
    actor = current_user.get("email") or "system"
    to = (payload.to_email or s.email or "").strip()
    if not to:
        raise HTTPException(status_code=422,
                            detail="recipient email required (and Surgery.email is empty)")
    if "@" not in to or "." not in to.rsplit("@", 1)[-1]:
        raise HTTPException(
            status_code=422,
            detail="recipient must look like an email address")
    # PHI outbound audit (Fable surgery audit Low + H2). Misdirected
    # ad-hoc emails are a HIPAA breach vector; the central audit log
    # makes the destination queryable.
    log_action(
        db,
        action="PHI_PATIENT_EMAIL_SENT",
        resource_type="surgery",
        resource_id=str(s.id),
        patient_id=s.chart_number or None,
        user_id=(actor or "").lower() or None,
        user_name=current_user.get("name") or actor,
        description=(f"Sent ad-hoc patient email for surgery "
                     f"{s.surgery_number or s.id} to {to} "
                     f"(subject: {payload.subject[:80]!r})"),
    )
    if not payload.subject.strip() or not payload.body_html.strip():
        raise HTTPException(status_code=422,
                            detail="subject and body_html are required")
    row = send_patient_email(
        db, kind="generic_patient_message",
        to_email=to,
        context={
            "patient_name": s.patient_name,
            "subject":      payload.subject.strip(),
            "body":         payload.body_html,
            "sender_name":  current_user.get("name") or actor,
        },
        sent_by=actor,
        surgery_id=s.id,
        chart_number=s.chart_number,
    )
    return {
        "id":      str(row.id),
        "status":  row.status,
        "to":      row.to_email,
        "sent_at": row.sent_at.isoformat() if row.sent_at else None,
        "failure_reason": row.failure_reason,
    }


class PatientSmsIn(BaseModel):
    body: str
    to_phone: Optional[str] = None


@router.post("/{surgery_id}/send-patient-sms")
def send_ad_hoc_patient_sms(
    surgery_id: str,
    payload: PatientSmsIn,
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.WORK)),
):
    """Compose-and-send an ad-hoc SMS to the patient. Uses the
    sms_generic_message template's wrapper (adds opt-out language)
    via the {{message}} placeholder. Gated on Surgery.sms_consent."""
    from app.services.patient_sms import send_patient_sms, build_sms_context
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="surgery not found")
    if not payload.body.strip():
        raise HTTPException(status_code=422, detail="body is required")
    actor = current_user.get("email") or "system"
    row = send_patient_sms(
        db, kind="sms_generic_message",
        surgery=s,
        to_phone=payload.to_phone,
        context=build_sms_context(s, message=payload.body.strip()),
        sent_by=actor,
    )
    return {
        "id":      str(row.id),
        "status":  row.status,
        "to":      row.to_phone,
        "sent_at": row.sent_at.isoformat() if row.sent_at else None,
        "failure_reason": row.failure_reason,
    }


@router.get("/{surgery_id}/patient-emails")
def list_patient_emails(
    surgery_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.VIEW)),
):
    """Audit history of patient emails for this surgery."""
    from app.models.patient_email import PatientEmail
    rows = (db.query(PatientEmail)
              .filter(PatientEmail.surgery_id == surgery_id)
              .order_by(PatientEmail.sent_at.desc()).all())
    return {
        "emails": [{
            "id":               str(r.id),
            "to_email":         r.to_email,
            "template_kind":    r.template_kind,
            "rendered_subject": r.rendered_subject,
            "status":           r.status,
            "failure_reason":   r.failure_reason,
            "sent_at":          r.sent_at.isoformat() if r.sent_at else None,
            "sent_by":          r.sent_by,
        } for r in rows],
    }


@router.get("/{surgery_id}/patient-sms")
def list_patient_sms(
    surgery_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.VIEW)),
):
    """Audit history of patient SMS messages for this surgery."""
    from app.models.patient_sms import PatientSms
    rows = (db.query(PatientSms)
              .filter(PatientSms.surgery_id == surgery_id)
              .order_by(PatientSms.sent_at.desc()).all())
    return {
        "messages": [{
            "id":             str(r.id),
            "to_phone":       r.to_phone,
            "template_kind":  r.template_kind,
            "rendered_body":  r.rendered_body,
            "segments":       r.segments,
            "status":         r.status,
            "failure_reason": r.failure_reason,
            "sent_at":        r.sent_at.isoformat() if r.sent_at else None,
            "sent_by":        r.sent_by,
        } for r in rows],
    }


# ─── Admin: manual reminder trigger ─────────────────────────────────

@router.post("/admin/reminders/run-now")
def run_reminders_now(
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.MANAGE)),
):
    from app.services.surgery.reminders import run_reminder_sweep
    return run_reminder_sweep(db)


# ─── Coordinator: schedule-gate override ────────────────────────────

class ScheduleGateOverridePayload(BaseModel):
    enabled: bool


@router.patch("/{surgery_id}/schedule-gate-override")
def patch_schedule_gate_override(
    surgery_id: str,
    payload: ScheduleGateOverridePayload,
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.WORK)),
):
    """Flip schedule_gate_override so a patient can self-schedule without paying."""
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="surgery not found")
    s.schedule_gate_override = payload.enabled
    s.schedule_gate_override_at = now_utc_naive()
    s.schedule_gate_override_by = current_user.get("email") or "system"
    db.commit()
    return {
        "ok": True,
        "schedule_gate_override": s.schedule_gate_override,
        "schedule_gate_override_at": s.schedule_gate_override_at.isoformat(),
        "schedule_gate_override_by": s.schedule_gate_override_by,
    }
