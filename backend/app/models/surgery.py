"""Surgery scheduling models.

Tracks the full lifecycle of a surgery from order upload through post-op
billing, with milestone-based progress, multi-facility patient choice,
behind-schedule indicators, waitlist management, and cancellation flows.

Status state machine:
  new           — order received, all required fields populated
  in_progress   — at least one milestone past `benefits_determined` started
  confirmed     — patient picked a date and consent is signed
  completed     — post-op closed (op notes + path uploaded, billing done)
  hold          — patient asked to delay; not yet cancelled
  cancelled     — surgery is off (sub-reason on SurgeryCancellation row)
  unresponsive  — patient hasn't engaged for 180+ days

The "Stuck" status from the legacy Smartsheet is intentionally NOT a
storable state — it's computed: any in_progress surgery whose current
milestone is overdue by >0h shows as stuck on the dashboard.
"""
from __future__ import annotations

from datetime import datetime
from sqlalchemy import (
    Boolean, Column, Date, DateTime, ForeignKey, Index, Integer,
    JSON, Numeric, String, Text, Time, UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.database import Base
from app.models.guid import GUID, new_uuid

SURGERY_URGENCY_VALUES    = ("routine", "expedited", "urgent")
SURGERY_COMPLEXITY_VALUES = ("standard", "complex")
SURGERY_DURATION_SOURCES  = ("coordinator", "template", "order_extract")
SURGERY_STATUS_VALUES     = (
    "incomplete", "new", "in_progress", "confirmed",
    "completed", "cancelled", "hold", "unresponsive",
)
SURGERY_FACILITY_VALUES   = (
    "medstar", "crmc", "office", "wwc_office_white_plains",
)
# Sane upper bound on a single surgery slot (8 hours). Patient sees
# the value via estimated_minutes on the dashboard; reject anything
# absurd before it lands in PDFs and calendar invites.
SURGERY_MAX_MINUTES       = 480

# ─── Surgery (the main row) ──────────────────────────────────────────

class Surgery(Base):
    __tablename__ = "surgeries"
    __table_args__ = (
        Index("ix_surgery_chart", "chart_number"),
        Index("ix_surgery_status", "status"),
        Index("ix_surgery_scheduled_date", "scheduled_date"),
        Index("ix_surgery_smartsheet_row", "smartsheet_row_id"),
    )

    id = Column(GUID(), primary_key=True, default=new_uuid)
    # Optimistic concurrency: SQLAlchemy bumps this on every UPDATE and
    # raises StaleDataError if another session committed in between.
    version_id = Column(Integer, default=1, nullable=False)
    __mapper_args__ = {"version_id_col": version_id}

    # Per-surgery portal-token version. Patient JWTs embed this as the
    # `ptv` claim; require_portal_token / require_patient_token reject
    # tokens whose ptv doesn't match the current row. Bumped by
    # cancel_surgery and consent_reset so a cancelled patient's still-
    # outstanding token (valid up to scheduled_date + 30 days) can't
    # be used post-cancellation. (Fable portal audit H5-auth.)
    portal_token_version = Column(Integer, default=0, nullable=False,
                                    server_default="0")

    # External IDs
    smartsheet_row_id = Column(String(40), nullable=True)
    surgery_number = Column(String(40), nullable=True)   # SUR00304 etc.

    # Patient identity
    chart_number = Column(String(20), nullable=False)
    patient_name = Column(String(200), nullable=False)
    first_name = Column(String(100), nullable=True)
    last_name = Column(String(100), nullable=True)
    dob = Column(Date, nullable=True)
    sex = Column(String(20), nullable=True)

    # Contact
    address_street = Column(String(200), nullable=True)
    address_city = Column(String(100), nullable=True)
    address_state = Column(String(2), nullable=True)
    address_zip = Column(String(10), nullable=True)
    email = Column(String(200), nullable=True)
    phone = Column(String(40), nullable=True)
    cell_phone = Column(String(40), nullable=True)

    # SMS consent (Phase J). Patient must opt in before any transactional
    # SMS goes out. Defaults to False; flipped True by either the patient
    # (on /p/surgery/:id) or the coordinator (on SurgeryDetail).
    sms_consent       = Column(Boolean, default=False, nullable=False)
    sms_consented_at  = Column(DateTime, nullable=True)
    sms_consented_by  = Column(String(120), nullable=True)
    # 'patient:self-service' for patient-driven opt-in, staff email otherwise.

    # Insurance
    primary_insurance = Column(String(200), nullable=True)
    primary_member_id = Column(String(80), nullable=True)
    primary_group = Column(String(80), nullable=True)
    secondary_insurance = Column(String(200), nullable=True)
    secondary_member_id = Column(String(80), nullable=True)

    # Order info
    surgeon_primary = Column(String(200), nullable=True)
    surgeon_secondary = Column(String(200), nullable=True)
    procedures = Column(JSON, nullable=True)
    # [{"cpt": "58573", "description": "Total laparoscopic hysterectomy..."}, ...]
    diagnoses = Column(JSON, nullable=True)
    # [{"icd": "D25.1", "description": "Intramural leiomyoma of uterus"}, ...]
    anesthesia = Column(String(80), nullable=True)
    estimated_minutes = Column(Integer, nullable=True)
    is_robotic = Column(Boolean, default=False, nullable=False)
    # Auto-derived from CPT but admin-overridable
    procedure_classification = Column(String(20), nullable=True)
    # values: minor | major | office | robotic_180 | robotic_240
    # Waitlist urgency. `routine` is the default; `expedited` and `urgent`
    # surface in the waitlist sort + UI accent. Used only as a sort key —
    # it does not gate scheduling.
    urgency = Column(String(20), default="routine", nullable=False)
    # values: routine | expedited | urgent

    # Complexity tier — physician's internal determination, never patient-facing.
    # Used by _default_duration_for to pick among same-procedure_kind templates.
    complexity = Column(String(20), default="standard", nullable=False)
    # values: standard | complex

    # Allotted surgery duration. Coordinator's explicit set takes priority;
    # if null, the booking endpoints fall back to the procedure template or
    # the kind→minutes map. `duration_source` tracks where the value came
    # from for the audit trail (coordinator | template | order_extract).
    duration_minutes = Column(Integer, nullable=True)
    duration_source  = Column(String(30), nullable=True)
    # values: coordinator | template | order_extract | null

    # Email of the operating surgeon. Used by provider-scope blackout
    # conflict detection so PTO for surgeon A doesn't ground surgeon B's
    # surgeries. Single-surgeon today; this future-proofs.
    surgeon_email = Column(String(200), nullable=True)

    # Facility selection — multi-facility supported because some procedures
    # can be done at either MedStar OR CRMC, and the patient chooses.
    eligible_facilities = Column(JSON, nullable=False, default=list)
    # ['medstar', 'crmc', 'office']
    selected_facility = Column(String(20), nullable=True)
    # values: medstar | crmc | office

    # Assistant surgeon (e.g. Dr. Gillespie) — only fires the workflow when
    # assistant_surgeon_required is True. We notify their office and confirm
    # the patient has a pre-op appointment with them.
    assistant_surgeon_required = Column(Boolean, default=False, nullable=False)
    assistant_surgeon_name = Column(String(200), nullable=True)
    assistant_surgeon_office_phone = Column(String(40), nullable=True)
    assistant_surgeon_office_fax = Column(String(40), nullable=True)
    assistant_surgeon_office_notified_at = Column(DateTime, nullable=True)
    assistant_surgeon_office_notified_by = Column(String(120), nullable=True)
    assistant_surgeon_appt_date = Column(Date, nullable=True)
    assistant_surgeon_appt_confirmed_at = Column(DateTime, nullable=True)
    assistant_surgeon_appt_confirmed_by = Column(String(120), nullable=True)

    # Devices
    device_required = Column(Boolean, default=False, nullable=False)
    device_kind = Column(String(80), nullable=True)
    device_assigned = Column(Boolean, default=False, nullable=False)
    rep_required = Column(Boolean, default=False, nullable=False)
    rep_notified = Column(Boolean, default=False, nullable=False)
    rep_name = Column(String(200), nullable=True)
    special_equipment_notes = Column(Text, nullable=True)

    # Auth
    auth_status = Column(String(40), default="not_required", nullable=False)
    # values: not_required | required | sent_request | sent_records |
    #         peer_review | approved | denied | tbd | completed
    auth_number = Column(String(80), nullable=True)

    # Clearance
    clearance_required = Column(Boolean, default=False, nullable=False)
    clearance_status = Column(String(40), default="not_required", nullable=False)
    # values: not_required | required | request_sent | received |
    #         sent_to_hospital | completed
    cardiologist_name = Column(String(200), nullable=True)
    cardiologist_phone = Column(String(40), nullable=True)
    cardiologist_fax = Column(String(40), nullable=True)

    # Sterilization (state consent for Medicaid-MCO patients)
    sterilization_consent_required = Column(Boolean, default=False, nullable=False)
    sterilization_consent_status = Column(String(40), default="not_required", nullable=False)

    # Pre-op
    preop_test_status = Column(String(40), default="not_required", nullable=False)
    # values: not_required | required | received | completed
    preop_date = Column(Date, nullable=True)

    # Labs
    labs_required = Column(Boolean, default=False, nullable=False)
    labs_required_list = Column(Text, nullable=True)
    labs_sent_to_hospital = Column(Boolean, default=False, nullable=False)
    labs_sent_at = Column(DateTime, nullable=True)

    # Consent
    consent_status = Column(String(40), default="not_required", nullable=False)
    # values: not_required | required | sent | signed
    consent_doc_id = Column(String(80), nullable=True)        # DocuSign id (Phase 3)
    consent_sent_at = Column(DateTime, nullable=True)
    consent_signed_at = Column(DateTime, nullable=True)

    # Hospital posting
    hosp_posted_status = Column(String(40), default="not_needed", nullable=False)
    # values: not_needed_office | sent_to_hospital | confirmation_received |
    #         not_required | completed

    # Scheduling
    scheduled_date = Column(Date, nullable=True)
    scheduled_start_time = Column(Time, nullable=True)
    scheduled_in_modmed_at = Column(DateTime, nullable=True)
    scheduled_in_modmed_by = Column(String(120), nullable=True)
    calendar_invite_sent_at = Column(DateTime, nullable=True)
    # Office-procedure med pickup (does not apply to hospital surgeries)
    office_meds_pickup_confirmed_at = Column(DateTime, nullable=True)
    office_meds_pickup_confirmed_by = Column(String(120), nullable=True)
    # Rescheduling history (incremented on every successful reschedule)
    reschedule_count = Column(Integer, default=0, nullable=False)
    last_rescheduled_at = Column(DateTime, nullable=True)
    last_rescheduled_by = Column(String(80), nullable=True)   # 'patient:self-service' or staff email
    # Set when the user clicks "Mark hospital notified" on the blocked-day
    # conflict To-do (Phase C). When set, the conflict drops off the list.
    blocked_conflict_notified_at = Column(DateTime, nullable=True)
    blocked_conflict_notified_by = Column(String(120), nullable=True)
    # Patient portal — self-report milestone flags (P1 dashboard reads these,
    # P5 wires the CTAs that flip them).
    labs_self_reported              = Column(Boolean, default=False, nullable=False)
    labs_self_reported_at           = Column(DateTime, nullable=True)
    hospital_preop_self_reported    = Column(Boolean, default=False, nullable=False)
    hospital_preop_self_reported_at = Column(DateTime, nullable=True)

    # Patient portal — coordinator can let patient self-schedule even when
    # balance is unpaid (e.g. payment plan in flight, insurance under appeal).
    schedule_gate_override    = Column(Boolean, default=False, nullable=False)
    schedule_gate_override_at = Column(DateTime, nullable=True)
    schedule_gate_override_by = Column(String(120), nullable=True)

    # Persisted overrides for the Hospital Posting (boarding slip)
    # field editor — coordinator's tweaks survive page reloads and seed
    # the editor next time it's opened.
    boarding_slip_overrides = Column(JSON, nullable=True)

    # Pre-op labs — patient self-reports the date they got labs drawn
    # (4–7 days before surgery is the practice rule).
    lab_appointment_date = Column(Date, nullable=True)
    lab_appointment_reported_at = Column(DateTime, nullable=True)
    lab_appointment_reported_by = Column(String(40), nullable=True)
    # values: "patient" | "staff:<email>"

    # Post-op
    post_op_appt_date = Column(Date, nullable=True)
    post_op_appt_2nd_date = Column(Date, nullable=True)
    # Visit location: "office" | "telehealth" | None
    post_op_appt_location = Column(String(20), nullable=True)
    post_op_appt_2nd_location = Column(String(20), nullable=True)
    post_op_call_status = Column(String(40), nullable=True)
    operative_report_status = Column(String(20), default="not_received", nullable=False)
    # values: not_received | received | not_required | completed
    pathology_status = Column(String(20), default="none_expected", nullable=False)
    # values: none_expected | expected | received | not_required | completed

    # Financial
    benefits_verified_at = Column(Date, nullable=True)
    benefits_expires_on = Column(Date, nullable=True)
    deductible = Column(Numeric(10, 2), nullable=True)
    deductible_met = Column(Numeric(10, 2), nullable=True)
    copay = Column(Numeric(10, 2), nullable=True)
    coinsurance_pct = Column(Numeric(5, 2), nullable=True)   # 20.00 = 20%
    oop_max = Column(Numeric(10, 2), nullable=True)
    oop_met = Column(Numeric(10, 2), nullable=True)
    allowed_amount = Column(Numeric(10, 2), nullable=True)
    patient_responsibility = Column(Numeric(10, 2), nullable=True)
    amount_paid = Column(Numeric(10, 2), default=0, nullable=False)
    payment_posted_to_billing = Column(Boolean, default=False, nullable=False)
    # Secondary insurance benefit fields. Calculator runs primary first,
    # then secondary covers its share of what primary left.
    secondary_deductible       = Column(Numeric(10, 2), nullable=True)
    secondary_deductible_met   = Column(Numeric(10, 2), nullable=True)
    secondary_copay            = Column(Numeric(10, 2), nullable=True)
    secondary_coinsurance_pct  = Column(Numeric(5, 2),  nullable=True)
    secondary_oop_max          = Column(Numeric(10, 2), nullable=True)
    secondary_oop_met          = Column(Numeric(10, 2), nullable=True)
    # Card-on-file flag — staff metadata; means the patient has a card
    # saved with us (ModMed Pay or Stripe) that can be charged when the
    # final balance lands.
    card_on_file               = Column(Boolean, default=False, nullable=False)

    # Billing (Phase 3 — AI-suggested + ModMed claim tracking)
    modmed_claim_number = Column(String(80), nullable=True)
    billed_icd10_codes = Column(JSON, nullable=True)
    # list of {code, description}
    billed_cpt_codes = Column(JSON, nullable=True)
    # list of {code, modifier, pos, description, units}
    billed_at = Column(DateTime, nullable=True)
    billed_by = Column(String(120), nullable=True)
    billing_ai_notes = Column(Text, nullable=True)
    # Free-form rationale Claude returned alongside the codes

    # FMLA (rare)
    fmla_status = Column(String(40), nullable=True)
    # P5b self-service flow: patient pays $25 before office completes the form
    fmla_fee_paid           = Column(Boolean, default=False, nullable=False)
    fmla_fee_paid_at        = Column(DateTime, nullable=True)
    fmla_fee_stripe_session_id = Column(String(100), nullable=True)

    # Workflow state
    status = Column(String(20), default="new", nullable=False)
    # values: new | in_progress | confirmed | completed | hold | cancelled | unresponsive
    sub_flag = Column(String(40), nullable=True)
    # values: klara_sent | awaiting_clearance | awaiting_date |
    #         unpaid_balance | ready
    # Assets
    order_pdf_path = Column(String(500), nullable=True)
    notes = Column(Text, nullable=True)
    latest_comment = Column(Text, nullable=True)

    # Manager / escalation
    escalate_to_email = Column(String(200), nullable=True)

    # Patient self-service date picker — when True, patient can pick a date
    # even if their balance isn't $0 yet (e.g. payment plan in place).
    balance_override = Column(Boolean, default=False, nullable=False)
    balance_override_by = Column(String(200), nullable=True)
    balance_override_at = Column(DateTime, nullable=True)

    # Google Calendar sync tracking. event_id is what we PATCH/DELETE later.
    # sync_status: 'synced' | 'pending' | 'failed' | null (never tried).
    google_calendar_event_id    = Column(String(120), nullable=True)
    google_calendar_sync_status = Column(String(20),  nullable=True)
    google_calendar_sync_error  = Column(Text,        nullable=True)

    # Audit
    source = Column(String(20), nullable=True)         # smartsheet | upload | manual
    created_by = Column(String(200), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow, nullable=False)
    # Auto-unresponsive sweep tracking (audit finding #13). The sweep
    # uses last_patient_activity_at (bumped on portal pick/reschedule/
    # consent-signed/auth) so a late patient pick on day 31 resets the
    # 30-day clock instead of immediately auto-flipping the row.
    # auto_unresponsive_at records when the sweep actually performed
    # the transition so the dashboard can distinguish "system-marked"
    # from "staff-marked" unresponsive without an audit-log lookup.
    last_patient_activity_at = Column(DateTime, nullable=True)
    auto_unresponsive_at     = Column(DateTime, nullable=True)

    milestones = relationship("SurgeryMilestone", back_populates="surgery",
                              cascade="all, delete-orphan",
                              order_by="SurgeryMilestone.position")
    files = relationship("SurgeryFile", back_populates="surgery",
                         cascade="all, delete-orphan")
    notifications = relationship("SurgeryNotification", back_populates="surgery",
                                  cascade="all, delete-orphan")
    consent_envelopes = relationship("SurgeryConsentEnvelope", back_populates="surgery",
                                     cascade="all, delete-orphan")
    slots = relationship(
        "SurgerySlot",
        primaryjoin="Surgery.id == SurgerySlot.surgery_id",
        foreign_keys="SurgerySlot.surgery_id",
        viewonly=True,  # avoid cascade complications — SurgerySlot lifecycle
                        # is owned by BlockDay
        order_by="SurgerySlot.start_time",
    )
    payments = relationship(
        "SurgeryPayment",
        primaryjoin="Surgery.id == SurgeryPayment.surgery_id",
        foreign_keys="SurgeryPayment.surgery_id",
        cascade="all, delete-orphan",
        order_by="SurgeryPayment.requested_at.desc()",
    )
    documents = relationship(
        "SurgeryDocument",
        backref="surgery",
        cascade="all, delete-orphan",
        order_by="SurgeryDocument.uploaded_at.desc()",
    )


# ─── Milestones ──────────────────────────────────────────────────────

class SurgeryMilestone(Base):
    """One row per workflow step on a surgery. The full set is created
    when the surgery transitions out of `incomplete` to `new`.

    Hospital-based path (12 steps):
      1. benefits_determined
      2. prior_auth
      3. klara_scheduling
      4. patient_picks_date
      5. device_assigned        (optional, only if device_required)
      6. consent
      7. surgery_confirmed_hospital  (boarding slip)
      8. labs_to_hospital
      9. post_op_call
      10. op_notes
      11. path_report
      12. surgery_billed

    Office-based path (8 steps): same minus #7, #8, #10.
    """
    __tablename__ = "surgery_milestones"
    __table_args__ = (
        UniqueConstraint("surgery_id", "kind", name="uq_milestone_surgery_kind"),
        Index("ix_milestone_surgery", "surgery_id"),
        Index("ix_milestone_status", "status"),
    )

    id = Column(GUID(), primary_key=True, default=new_uuid)
    surgery_id = Column(GUID(), ForeignKey("surgeries.id", ondelete="CASCADE"),
                         nullable=False)

    kind = Column(String(40), nullable=False)
    title = Column(String(200), nullable=False)
    position = Column(Integer, nullable=False)

    status = Column(String(20), default="locked", nullable=False)
    # values: locked | pending | in_progress | done | skipped | not_applicable
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    completed_by = Column(String(200), nullable=True)
    notes = Column(Text, nullable=True)
    data_json = Column(JSON, nullable=True)

    # Behind-schedule timing — admin-tunable per milestone kind.
    expected_duration_days = Column(Integer, nullable=True)

    surgery = relationship("Surgery", back_populates="milestones")


# ─── Files (orders, prior auth, op notes, path reports, etc.) ──────

class SurgeryFile(Base):
    __tablename__ = "surgery_files"
    __table_args__ = (
        Index("ix_surgery_file_surgery", "surgery_id"),
        Index("ix_surgery_file_kind", "kind"),
    )

    id = Column(GUID(), primary_key=True, default=new_uuid)
    surgery_id = Column(GUID(), ForeignKey("surgeries.id", ondelete="CASCADE"),
                         nullable=False)
    kind = Column(String(40), nullable=False)
    # values: order | prior_auth | op_notes | path_report | consent |
    #         boarding_slip | clearance | other
    filename = Column(String(255), nullable=False)
    path = Column(String(500), nullable=False)
    mime_type = Column(String(80), nullable=True)
    size_bytes = Column(Integer, nullable=True)
    notes = Column(Text, nullable=True)
    uploaded_by = Column(String(200), nullable=True)
    uploaded_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    # Send log — list of {at, by, kind: fax|email, to, status, message_id?, error?}
    # appended each time this file is faxed or emailed.
    send_history = Column(JSON, nullable=True)

    surgery = relationship("Surgery", back_populates="files")


# ─── Notification log (Klara messages, calendar invites, etc.) ─────

class SurgeryNotification(Base):
    """Append-only log of every patient-facing touch (Klara messages,
    calendar invites, post-op check-ins, waitlist blasts). Replaces the
    timestamp-soup approach in the Smartsheet (EmailSentDate, Last
    Reminder Date, etc.) with full history."""
    __tablename__ = "surgery_notifications"
    __table_args__ = (
        Index("ix_surgery_notif_surgery", "surgery_id"),
        Index("ix_surgery_notif_kind", "kind"),
    )

    id = Column(GUID(), primary_key=True, default=new_uuid)
    surgery_id = Column(GUID(), ForeignKey("surgeries.id", ondelete="CASCADE"),
                         nullable=False)
    kind = Column(String(40), nullable=False)
    # values: klara_initial | klara_reminder | klara_post_op |
    #         calendar_invite | waitlist_blast | clearance_followup |
    #         payment_request
    sent_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    sent_by = Column(String(200), nullable=True)
    body_preview = Column(Text, nullable=True)

    surgery = relationship("Surgery", back_populates="notifications")


# ─── Block schedule (recurring + add-on surgery days) ───────────────

class BlockSchedule(Base):
    """Admin-managed recurring schedule of surgery days. BlockDays are
    materialized from these rows for the next ~60 days."""
    __tablename__ = "surgery_block_schedules"

    id = Column(GUID(), primary_key=True, default=new_uuid)
    facility = Column(String(20), nullable=False)
    # values: medstar | crmc | office

    recurrence_kind = Column(String(20), nullable=False)
    # values: weekly_nth (1st & 3rd Mon) | weekly | specific_dates
    weekday = Column(Integer, nullable=True)             # 0=Mon..6=Sun
    nth_in_month = Column(JSON, nullable=True)           # [1, 3]
    specific_dates = Column(JSON, nullable=True)         # ["2026-06-04", ...]

    start_time = Column(Time, nullable=False)
    end_time = Column(Time, nullable=False)

    block_kind = Column(String(20), nullable=False)
    # values: robotic_only | minor_only | major_only | mixed | office

    effective_from = Column(Date, nullable=False, default=lambda: datetime.utcnow().date())
    effective_through = Column(Date, nullable=True)

    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_by = Column(String(200), nullable=True)


class BlockDay(Base):
    """A specific date that has surgery slots available. Derived from
    BlockSchedule (recurring) or created ad-hoc as add-on days."""
    __tablename__ = "surgery_block_days"
    __table_args__ = (
        UniqueConstraint("facility", "block_date", name="uq_block_facility_date"),
        Index("ix_block_day_date", "block_date"),
    )

    id = Column(GUID(), primary_key=True, default=new_uuid)
    # Optimistic locking — prevents two schedulers from racing on capacity.
    version_id = Column(Integer, default=1, nullable=False)
    __mapper_args__ = {"version_id_col": version_id}
    facility = Column(String(20), nullable=False)
    block_date = Column(Date, nullable=False)
    block_kind = Column(String(20), nullable=False)
    start_time = Column(Time, nullable=False)
    end_time = Column(Time, nullable=False)
    is_addon = Column(Boolean, default=False, nullable=False)
    notes = Column(Text, nullable=True)
    # Set when a release-the-day alert has been sent for this block day,
    # so the daily sweep doesn't spam the same notification.
    release_alert_sent_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_by = Column(String(200), nullable=True)

    slots = relationship("SurgerySlot", back_populates="block_day",
                         cascade="all, delete-orphan",
                         order_by="SurgerySlot.start_time")


class SurgerySlot(Base):
    """A booked case on a block day."""
    __tablename__ = "surgery_slots"
    __table_args__ = (
        Index("ix_slot_block_day", "block_day_id"),
        Index("ix_slot_surgery", "surgery_id"),
    )

    id = Column(GUID(), primary_key=True, default=new_uuid)
    block_day_id = Column(GUID(),
                           ForeignKey("surgery_block_days.id", ondelete="CASCADE"),
                           nullable=False)
    surgery_id = Column(GUID(),
                         ForeignKey("surgeries.id", ondelete="SET NULL"),
                         nullable=True)
    start_time = Column(Time, nullable=False)
    duration_minutes = Column(Integer, nullable=False)
    procedure_kind = Column(String(20), nullable=False)
    # values: robotic_180 | robotic_240 | minor | major | office
    notes = Column(Text, nullable=True)

    block_day = relationship("BlockDay", back_populates="slots")


# ─── Waitlist + cancellation ────────────────────────────────────────

class SurgeryBlackoutDay(Base):
    """Days when surgeries cannot be scheduled — US holidays + provider PTO
    + facility/equipment outages. The block-day materializer skips these
    dates; the patient-facing date picker hides them.

    Scope drives who/where the blackout applies:
      office       — entire practice closed (e.g. holiday)
      provider     — one provider unavailable (PTO; owner_email is set)
      facility     — one hospital unavailable (e.g. MedStar block cancelled)
    """
    __tablename__ = "surgery_blackout_days"
    __table_args__ = (
        Index("ix_blackout_date", "blackout_date"),
        Index("ix_blackout_scope", "scope"),
    )

    id = Column(GUID(), primary_key=True, default=new_uuid)
    blackout_date = Column(Date, nullable=False)
    scope = Column(String(20), nullable=False)
    # values: office | provider | facility
    reason = Column(String(40), nullable=False)
    # values: holiday | pto | facility_closed | equipment_down | other
    label = Column(String(200), nullable=True)         # "Memorial Day", "Dr. Cooke vacation"
    owner_email = Column(String(200), nullable=True)   # provider PTO owner
    facility = Column(String(20), nullable=True)       # for scope=facility
    is_recurring = Column(Boolean, default=False, nullable=False)
    # Holidays auto-seeded for next 5 years are recurring=True; PTO is False
    notes = Column(Text, nullable=True)
    created_by = Column(String(200), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class SurgeryWaitlist(Base):
    """Patients who said yes to "let me know if an earlier slot opens"."""
    __tablename__ = "surgery_waitlists"
    __table_args__ = (
        Index("ix_waitlist_surgery", "surgery_id"),
    )

    id = Column(GUID(), primary_key=True, default=new_uuid)
    surgery_id = Column(GUID(),
                         ForeignKey("surgeries.id", ondelete="CASCADE"),
                         nullable=False)
    # Minimum advance notice the patient needs (days)
    advance_notice_days = Column(Integer, default=7, nullable=False)
    signed_up_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    removed_at = Column(DateTime, nullable=True)
    removed_reason = Column(String(40), nullable=True)
    # values: claimed_slot | cancelled | declined_offer


class SurgeryCancellation(Base):
    """Audit row created when a surgery is cancelled, hold-flagged, or
    marked unresponsive. The Surgery.status field reflects the latest
    state; this table preserves history."""
    __tablename__ = "surgery_cancellations"
    __table_args__ = (
        Index("ix_cancel_surgery", "surgery_id"),
    )

    id = Column(GUID(), primary_key=True, default=new_uuid)
    surgery_id = Column(GUID(),
                         ForeignKey("surgeries.id", ondelete="CASCADE"),
                         nullable=False)
    cancelled_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    cancelled_by = Column(String(200), nullable=False)
    reason = Column(String(40), nullable=False)
    # values: patient | anesthesia | hospital | medical | unresponsive | hold
    fee_required = Column(Boolean, default=False, nullable=False)
    fee_charged = Column(Boolean, default=False, nullable=False)
    fee_charged_at = Column(DateTime, nullable=True)
    fee_charged_by = Column(String(200), nullable=True)
    refund_required = Column(Boolean, default=False, nullable=False)
    refund_processed = Column(Boolean, default=False, nullable=False)
    notes = Column(Text, nullable=True)


class SurgerySchedulerNotice(Base):
    """Idempotency ledger for the surgery_scheduler_notify service. One
    row per (surgery, event_kind, event_id) the practice has been
    notified about. Lets the BoldSign webhook retry safely without
    re-emailing surgery@ on every redelivery."""
    __tablename__ = "surgery_scheduler_notices"
    __table_args__ = (
        Index("ix_surg_notice_surgery", "surgery_id"),
        UniqueConstraint("surgery_id", "event_kind", "event_id",
                          name="uq_surg_notice_dedup"),
    )

    id = Column(GUID(), primary_key=True, default=new_uuid)
    surgery_id = Column(GUID(),
                         ForeignKey("surgeries.id", ondelete="CASCADE"),
                         nullable=False)
    event_kind = Column(String(40), nullable=False)
    # Stable per-event id (BoldSign event id, or "{surgery_id}:{ISO ts}"
    # for portal actions where there's no external id).
    event_id = Column(String(120), nullable=False)
    sent_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    channels = Column(String(80), nullable=True)   # csv: "email,sms"
    detail = Column(Text, nullable=True)


class PatientAuthAttempt(Base):
    """Failed soft-auth attempts on the public patient date picker.
    Used to enforce a 3-fail / 15-minute lockout.

    surgery_id is nullable so an unmatched / DOB-only-match attempt
    can be logged without charging it against an arbitrary patient
    (Fable portal audit H1-router — otherwise anyone with a patient's
    DOB could lock that patient out with 3 garbage requests)."""
    __tablename__ = "surgery_patient_auth_attempts"
    __table_args__ = (
        Index("ix_pat_auth_surgery_time", "surgery_id", "attempted_at"),
        Index("ix_pat_auth_ip_time", "ip_address", "attempted_at"),
    )

    id = Column(GUID(), primary_key=True, default=new_uuid)
    surgery_id = Column(GUID(),
                         ForeignKey("surgeries.id", ondelete="CASCADE"),
                         nullable=True)
    attempted_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    success = Column(Boolean, default=False, nullable=False)
    ip_address = Column(String(45), nullable=True)


# ─── Per-surgery notes (timestamped log) ───────────────────────────

class SurgeryNote(Base):
    """A timestamped note attached to a surgery. Notes form an append-only
    log so the work history stays auditable — deletions are allowed (by the
    author or surgery:manage) but edits are not."""
    __tablename__ = "surgery_notes"
    __table_args__ = (
        Index("ix_surgery_note_surgery_time", "surgery_id", "created_at"),
    )

    id = Column(GUID(), primary_key=True, default=new_uuid)
    surgery_id = Column(GUID(), ForeignKey("surgeries.id", ondelete="CASCADE"),
                         nullable=False)
    content = Column(Text, nullable=False)
    created_by = Column(String(200), nullable=False)
    kind = Column(String(40), nullable=True)
    # Optional categorization for audit filtering — values established by
    # callers; current callers use: slot_scheduled |
    # slot_scheduled_by_coordinator | slot_duration_changed |
    # blocked_conflict_resolved | other
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


# ─── Saved filter presets (per-user, for the surgery list) ─────────

class SurgeryFilterPreset(Base):
    """A named filter preset on the surgery dashboard. Stored per user
    so each scheduler can keep their own working set of filters."""
    __tablename__ = "surgery_filter_presets"
    __table_args__ = (
        Index("ix_surgery_filter_owner", "owner_email"),
        UniqueConstraint("owner_email", "name", name="uq_filter_owner_name"),
    )

    id = Column(GUID(), primary_key=True, default=new_uuid)
    owner_email = Column(String(200), nullable=False)
    name = Column(String(120), nullable=False)
    filters_json = Column(JSON, nullable=False, default=dict)
    is_default = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow, nullable=False)


# ─── Consent Templates + Envelopes ───────────────────────────────────

class ConsentTemplate(Base):
    """A DocuSign template registered for a specific procedure (or
    supplemental form like Medicaid sterilization).

    Matching: at send-time, the matcher iterates surgery.procedures and
    finds the template whose `procedure_match` keywords (substring,
    case-insensitive) match the procedure name. `facility_match` and
    `insurance_match` further constrain matching when set.

    Templates marked `is_supplemental=True` are added in addition to the
    primary procedure-matched template (e.g. Medicaid HHS-687 attaches
    to any tubal/sterilization procedure when the patient has one of the
    listed Medicaid MCO insurances).
    """
    __tablename__ = "consent_templates"
    __table_args__ = (
        Index("ix_consent_template_supplemental", "is_supplemental"),
    )

    id = Column(GUID(), primary_key=True, default=new_uuid)
    name = Column(String(200), nullable=False)
    docusign_template_id = Column(String(80), nullable=True)
    boldsign_template_id = Column(String(80), nullable=True)
    # CPT-code primary match (most reliable). JSON list of CPT strings,
    # e.g. ["58300","58301"] for an IUD insert/remove consent. When set,
    # the matcher prefers CPT membership over the legacy substring keywords.
    cpt_codes = Column(JSON, nullable=False, default=list)
    # JSON list of substrings, e.g. ["d&c", "dilation", "dilatation"].
    # Used as a fallback when cpt_codes is empty, or to backstop edge cases
    # where the CPT isn't entered on the surgery row yet.
    procedure_match = Column(JSON, nullable=False, default=list)
    facility_match = Column(JSON, nullable=False, default=list)
    # JSON list of facility codes. Empty list = matches any facility.
    # Example: ["medstar","crmc"] for hospital-only templates.
    # JSON list of substrings (case-insensitive) matched against
    # surgery.primary_insurance. Empty list = matches any insurance.
    insurance_match = Column(JSON, nullable=False, default=list)
    is_supplemental = Column(Boolean, default=False, nullable=False)
    # Medicaid sterilization rule: must be signed >=30 days before surgery
    min_days_before_surgery = Column(Integer, nullable=True)
    notes = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow, nullable=False)


class SurgeryConsentEnvelope(Base):
    """One DocuSign envelope sent for a surgery. A surgery may have
    several (one per matched template). Surgery.consent_status flips to
    'signed' only when every envelope row reaches status='signed'."""
    __tablename__ = "surgery_consent_envelopes"
    __table_args__ = (
        Index("ix_consent_env_surgery", "surgery_id"),
        Index("ix_consent_env_docusign", "docusign_envelope_id"),
        Index("ix_consent_env_boldsign", "boldsign_envelope_id"),
        UniqueConstraint("surgery_id", "template_id", name="uq_surgery_template"),
    )

    id = Column(GUID(), primary_key=True, default=new_uuid)
    surgery_id = Column(GUID(),
                         ForeignKey("surgeries.id", ondelete="CASCADE"),
                         nullable=False)
    template_id = Column(GUID(),
                          ForeignKey("consent_templates.id"),
                          nullable=False)
    docusign_envelope_id = Column(String(80), nullable=True, unique=True)
    boldsign_envelope_id = Column(String(80), nullable=True, unique=True)
    # values: pending | sent | delivered | signed | declined | voided | failed
    status = Column(String(20), default="pending", nullable=False)
    sent_at = Column(DateTime, nullable=True)
    signed_at = Column(DateTime, nullable=True)
    # When the PATIENT specifically completed their signing (vs the surgeon
    # or witness). Lets the portal show "Awaiting countersignature" instead
    # of "Awaiting your signature" once the patient's part is done.
    patient_signed_at = Column(DateTime, nullable=True)
    declined_at = Column(DateTime, nullable=True)
    voided_at = Column(DateTime, nullable=True)
    last_synced_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow, nullable=False)

    surgery = relationship("Surgery", back_populates="consent_envelopes")
    template = relationship("ConsentTemplate")


# ─── Patient-uploaded documents (P5 patient portal) ────────────────────

class SurgeryDocument(Base):
    """Patient-uploaded documents (clearance, EKG, FMLA, …)."""
    __tablename__ = "surgery_documents"
    __table_args__ = (
        Index("ix_surgery_documents_surgery_id", "surgery_id"),
    )

    id           = Column(GUID(), primary_key=True, default=new_uuid)
    surgery_id   = Column(GUID(),
                            ForeignKey("surgeries.id", ondelete="CASCADE"),
                            nullable=False)
    kind         = Column(String(40), nullable=False)
    filename     = Column(String(255), nullable=False)
    gcs_path     = Column(String(500), nullable=False)
    content_type = Column(String(100), nullable=True)
    size_bytes   = Column(Integer, nullable=True)
    uploaded_at  = Column(DateTime, default=datetime.utcnow, nullable=False)
    uploaded_by  = Column(String(120), nullable=False)
