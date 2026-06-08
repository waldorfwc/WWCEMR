"""LARC (Long-Acting Reversible Contraceptive) device inventory + tracking.

Devices flow: order → benefits → (optional pharmacy order) → received →
checkout for insertion → inserted → billed. Each device is a physical
object the practice owns and must account for ($300–$1,100 per unit).

Two flows:
  - in_stock: practice keeps Liletta on hand; patient gets one off the shelf
  - pharmacy_order: Mirena/Skyla/Kyleena/Paragard/Nexplanon — ordered
    through the patient's prescription benefit, ships to the practice

Audit is per-row in larc_audit_events — every state change writes one entry.
"""
from __future__ import annotations

from datetime import datetime
from sqlalchemy import (
    Boolean, Column, Date, DateTime, ForeignKey, Index, Integer,
    JSON, Numeric, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.database import Base
from app.models.guid import GUID, new_uuid


# ─── Device-type catalog (Liletta, Mirena, Skyla, etc.) ─────────────

LARC_OWNERSHIP_VALUES = ("patient_owned", "wwc_owned", "wwc_claimed")
# Devices in these ownership buckets get billed to insurance.
LARC_BILLABLE_OWNERSHIPS = ("wwc_owned", "wwc_claimed")


class LarcDeviceType(Base):
    """Catalog of LARC device types the practice handles. Stays editable
    so new manufacturers / brands can be added without code changes."""
    __tablename__ = "larc_device_types"

    id = Column(GUID(), primary_key=True, default=new_uuid)
    name = Column(String(80), nullable=False, unique=True)         # "Liletta"
    manufacturer = Column(String(120), nullable=True)              # "Medicines360"
    # category: 'larc' = patient-assigned contraceptive (full LARC flow);
    #           'office_procedure' = single-use device consumed during a
    #                                surgery (NovaSure, Bensta)
    category = Column(String(20), default="larc", nullable=False)
    # workflow: 'in_stock' = practice keeps on hand; 'pharmacy_order' = patient-specific order
    default_flow = Column(String(20), default="pharmacy_order", nullable=False)
    typical_cost = Column(Numeric(10, 2), nullable=True)           # last-known unit cost
    reorder_threshold = Column(Integer, nullable=True)             # in_stock only
    reorder_quantity = Column(Integer, nullable=True)              # how many to order when below threshold
    # Bayer devices (Mirena/Skyla/Kyleena) share an enrollment form template
    enrollment_form_template = Column(String(200), nullable=True)  # docusign_template_id or path
    notes = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow, nullable=False)


# ─── Pharmacy directory (configurable) ──────────────────────────────

class LarcPharmacy(Base):
    """Pharmacy / specialty-pharmacy directory. Which pharmacy a device
    is ordered through depends on the patient's prescription plan, so we
    keep a flexible list with fax numbers + notes."""
    __tablename__ = "larc_pharmacies"

    id = Column(GUID(), primary_key=True, default=new_uuid)
    name = Column(String(200), nullable=False)
    fax = Column(String(40), nullable=True)
    phone = Column(String(40), nullable=True)
    address = Column(String(300), nullable=True)
    accepts_insurance = Column(JSON, nullable=True)   # list of insurance keywords
    # Device types this pharmacy can ship (e.g. ["Mirena","Skyla","Kyleena"]
    # for a CVS Bayer pharmacy, ["Paragard"] for Biologics by McKesson).
    # Empty / null means "serves any device" (legacy rows).
    device_names = Column(JSON, nullable=True)
    # Device types where this pharmacy is the default pick on new
    # assignments. One pharmacy per device-name max, but enforcement is
    # at the API layer rather than via a DB constraint.
    default_for_devices = Column(JSON, nullable=True)
    notes = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


# ─── Physical device inventory ──────────────────────────────────────

class LarcDevice(Base):
    """One physical LARC device the practice owns or holds. The
    'our_id' is the practice-minted label; manufacturer_lot is what's
    printed on the box and matters for FDA recalls.

    Status flow:
      received     — just arrived (from pharmacy) or just stocked
      unassigned   — in storage, not tied to a patient
      assigned     — tied to a patient (active assignment exists)
      checked_out  — MA pulled from cabinet for insertion
      inserted     — successfully inserted; awaiting billing close-out
      defective    — failed insertion, used, awaiting return to manufacturer
      returned     — shipped back to manufacturer for replacement
      lost         — manual mark by manager
      expired      — past expiration date
      billed       — terminal: claim # recorded, drops off the active list
    """
    __tablename__ = "larc_devices"
    __table_args__ = (
        Index("ix_larc_device_status", "status"),
        Index("ix_larc_device_type", "device_type_id"),
        Index("ix_larc_device_location", "location"),
        Index("ix_larc_device_expires", "expiration_date"),
        UniqueConstraint("our_id", name="uq_larc_device_our_id"),
    )

    id = Column(GUID(), primary_key=True, default=new_uuid)

    # Practice-minted ID for staff to reference (label printed for the cabinet)
    our_id = Column(String(40), nullable=False)
    # Manufacturer info — lot + serial — matters for FDA recalls
    manufacturer_lot = Column(String(80), nullable=True)
    manufacturer_serial = Column(String(80), nullable=True)

    device_type_id = Column(GUID(), ForeignKey("larc_device_types.id"), nullable=False)

    # Sourcing & cost
    purchase_date = Column(Date, nullable=True)
    purchase_price = Column(Numeric(10, 2), nullable=True)   # per-unit cost paid
    expiration_date = Column(Date, nullable=True)

    # Location: 'arlington' | 'white_plains' | 'brandywine' (configurable)
    location = Column(String(40), nullable=False, default="white_plains")

    status = Column(String(20), default="unassigned", nullable=False)
    # Ownership: who paid for the device and who can be billed for it.
    #   patient_owned — patient (or their insurance) purchased through their
    #                   own pharmacy benefit. WWC does NOT bill insurance.
    #   wwc_owned     — WWC bought the device. WWC bills insurance.
    #   wwc_claimed   — originally patient-owned but unused within a year /
    #                   declined; WWC claimed it. Treated like wwc_owned for
    #                   billing purposes.
    ownership = Column(String(20), default="wwc_owned", nullable=False)
    # If status='defective' or 'returned', this points to the new replacement device
    replacement_device_id = Column(GUID(), ForeignKey("larc_devices.id"), nullable=True)
    # If this device IS a replacement, point back to the original
    replaces_device_id = Column(GUID(), ForeignKey("larc_devices.id"), nullable=True)

    # Purchasing patient (when patient_owned or wwc_claimed) — the patient
    # whose insurance/wallet paid for the device. Can differ from the
    # patient the device is later assigned to.
    purchasing_patient_chart = Column(String(40), nullable=True)
    purchasing_patient_name  = Column(String(200), nullable=True)

    notes = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow, nullable=False)

    device_type = relationship("LarcDeviceType")
    assignments = relationship("LarcAssignment", back_populates="device",
                                cascade="all, delete-orphan")


# ─── Patient assignment (the workflow row) ──────────────────────────

class LarcAssignment(Base):
    """Ties a physical LARC device to a specific patient and drives the
    workflow milestones. Only ONE active assignment per device at a time
    (enforced by the partial unique constraint below).
    """
    __tablename__ = "larc_assignments"
    __table_args__ = (
        Index("ix_larc_assignment_device", "device_id"),
        Index("ix_larc_assignment_chart", "chart_number"),
        Index("ix_larc_assignment_status", "status"),
        Index("ix_larc_assignment_active", "device_id", "is_active"),
    )

    id = Column(GUID(), primary_key=True, default=new_uuid)
    # Optimistic locking — prevents two MAs from racing on a single assignment.
    version_id = Column(Integer, default=1, nullable=False)
    __mapper_args__ = {"version_id_col": version_id}
    # For pharmacy-order assignments, device_id is NULL until the device
    # is received from the pharmacy — but we still need to know what
    # device type was ordered (to pick the enrollment form template +
    # match the right LarcDeviceType.enrollment_form_template). Stored
    # directly so the sender can resolve before device_id is set.
    device_id = Column(GUID(), ForeignKey("larc_devices.id"), nullable=True)
    device_type_id = Column(GUID(), ForeignKey("larc_device_types.id"),
                             nullable=True)

    # Patient identity — chart number is the durable key; name is denormalised
    # so the dashboard can render without joining ModMed.
    chart_number = Column(String(40), nullable=False)
    patient_name = Column(String(200), nullable=False)
    patient_dob = Column(Date, nullable=True)
    patient_email = Column(String(200), nullable=True)
    patient_phone = Column(String(40), nullable=True)

    # Insurance / pharmacy info
    primary_insurance = Column(String(200), nullable=True)
    pharmacy_id = Column(GUID(), ForeignKey("larc_pharmacies.id"), nullable=True)

    # Surgery-module link — set when an office-procedure device is picked
    # at scheduling time. Lets the surgery detail page show the linked
    # device + its status.
    linked_surgery_id = Column(GUID(), nullable=True)

    # Source flow + workflow state
    source_flow = Column(String(20), default="in_stock", nullable=False)
    # values: in_stock | pharmacy_order | office_procedure

    status = Column(String(40), default="new", nullable=False)
    # high-level: new | in_progress | inserted | failed_unused | failed_used |
    #             patient_no_show | patient_canceled | office_canceled | other |
    #             owed | billed | cancelled
    sub_flag = Column(String(40), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    # When the patient is moved to the Owed list (device reallocated), this
    # row stays around for history; is_active flips to False.

    # Financials
    patient_responsibility = Column(Numeric(10, 2), nullable=True)
    patient_responsibility_in_modmed_at = Column(DateTime, nullable=True)
    patient_responsibility_in_modmed_by = Column(String(200), nullable=True)

    # Benefits calculator inputs (parallel to Surgery's BenefitsPanel)
    allowed_amount   = Column(Numeric(10, 2), nullable=True)
    deductible       = Column(Numeric(10, 2), nullable=True)
    deductible_met   = Column(Numeric(10, 2), nullable=True)
    copay            = Column(Numeric(10, 2), nullable=True)
    coinsurance_pct  = Column(Numeric(5, 2),  nullable=True)
    oop_max          = Column(Numeric(10, 2), nullable=True)
    oop_met          = Column(Numeric(10, 2), nullable=True)
    benefits_verified_at = Column(Date, nullable=True)
    claim_number = Column(String(80), nullable=True)
    billed_at = Column(DateTime, nullable=True)
    billed_by = Column(String(200), nullable=True)

    # Pharmacy-order flow timestamps
    enrollment_sent_at = Column(DateTime, nullable=True)
    enrollment_signed_at = Column(DateTime, nullable=True)
    request_faxed_at = Column(DateTime, nullable=True)
    expected_received_by = Column(Date, nullable=True)
    device_received_at = Column(DateTime, nullable=True)

    # Patient notification + scheduling
    patient_notified_at = Column(DateTime, nullable=True)
    appt_scheduled_at = Column(DateTime, nullable=True)
    appt_date = Column(Date, nullable=True)

    # Insertion outcome
    inserted_at = Column(DateTime, nullable=True)
    inserted_by = Column(String(200), nullable=True)
    failure_reason = Column(String(40), nullable=True)
    failure_notes = Column(Text, nullable=True)

    # If a defective device was replaced, point at the replacement assignment
    replacement_assignment_id = Column(GUID(), ForeignKey("larc_assignments.id"), nullable=True)
    replaces_assignment_id = Column(GUID(), ForeignKey("larc_assignments.id"), nullable=True)

    # Inserting provider (per-assignment override for the BoldSign enrollment
    # envelope's Provider role). Falls back to the practice-wide settings
    # when blank. Three fields because BoldSign forms need both human display
    # ("Dr. Aryian Cooke") and NPI on the signature line.
    inserting_provider_email = Column(String(200), nullable=True)
    inserting_provider_name  = Column(String(200), nullable=True)
    inserting_provider_npi   = Column(String(20),  nullable=True)

    # Advanced Practice Provider (APP) — printed on enrollment forms that
    # list both a prescribing physician and a supervising/collaborating
    # APP (Bayer + Nexplanon forms have separate APP fields). Falls back
    # to PracticeConfig app_name/app_npi when blank.
    app_email = Column(String(200), nullable=True)
    app_name  = Column(String(200), nullable=True)
    app_npi   = Column(String(20),  nullable=True)

    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_by = Column(String(200), nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow, nullable=False)

    device = relationship("LarcDevice", back_populates="assignments",
                          foreign_keys=[device_id])
    pharmacy = relationship("LarcPharmacy")
    milestones = relationship("LarcMilestone", back_populates="assignment",
                              cascade="all, delete-orphan",
                              order_by="LarcMilestone.position")
    checkouts = relationship("LarcCheckout", back_populates="assignment",
                              cascade="all, delete-orphan")


# ─── Milestones (per assignment) ────────────────────────────────────

class LarcMilestone(Base):
    """One workflow step on a LARC assignment. Mirrors the surgery
    milestone pattern."""
    __tablename__ = "larc_milestones"
    __table_args__ = (
        Index("ix_larc_milestone_assignment", "assignment_id"),
        UniqueConstraint("assignment_id", "kind", name="uq_larc_milestone_kind"),
    )

    id = Column(GUID(), primary_key=True, default=new_uuid)
    assignment_id = Column(GUID(),
                            ForeignKey("larc_assignments.id", ondelete="CASCADE"),
                            nullable=False)
    kind = Column(String(60), nullable=False)
    title = Column(String(200), nullable=False)
    position = Column(Integer, nullable=False)
    status = Column(String(20), default="pending", nullable=False)
    # pending | in_progress | done | skipped | not_applicable
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    completed_by = Column(String(200), nullable=True)
    notes = Column(Text, nullable=True)
    expected_duration_days = Column(Integer, nullable=True)

    assignment = relationship("LarcAssignment", back_populates="milestones")


# ─── Checkout events (MA pulls device from cabinet) ─────────────────

class LarcCheckout(Base):
    """A single check-out event. Records who pulled the device, who
    they handed it to (for return tracking when storage is locked), and
    the outcome of the visit (inserted / failed / no-show / etc.).

    If outcome=='failed_used', the device is presumed defective and
    needs to be returned to the manufacturer for replacement.
    """
    __tablename__ = "larc_checkouts"
    __table_args__ = (
        Index("ix_larc_checkout_device", "device_id"),
        Index("ix_larc_checkout_assignment", "assignment_id"),
        Index("ix_larc_checkout_status", "outcome"),
    )

    id = Column(GUID(), primary_key=True, default=new_uuid)
    device_id = Column(GUID(), ForeignKey("larc_devices.id"), nullable=False)
    assignment_id = Column(GUID(), ForeignKey("larc_assignments.id"), nullable=False)

    requested_by = Column(String(200), nullable=False)   # MA email
    requested_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    # Hybrid approval: 'auto' for the common path, 'manager' when flagged
    approval_kind = Column(String(20), default="auto", nullable=False)
    approval_status = Column(String(20), default="pending", nullable=False)
    # pending | approved | denied
    approved_by = Column(String(200), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    denial_reason = Column(Text, nullable=True)

    # Whom the MA handed the device to (often a provider) — for return chain
    given_to = Column(String(200), nullable=True)

    # Outcome — set after the visit
    outcome = Column(String(40), nullable=True)
    # values: inserted | failed_unused | failed_used | patient_no_show |
    #         patient_canceled | office_canceled | lost | other
    outcome_notes = Column(Text, nullable=True)
    outcome_loss_value = Column(Numeric(10, 2), nullable=True)
    acknowledged_at = Column(DateTime, nullable=True)
    acknowledged_by = Column(String(200), nullable=True)

    # Replacement chain: if this checkout failed_used, the replacement
    # checkout points back to this one
    replaces_checkout_id = Column(GUID(), ForeignKey("larc_checkouts.id"), nullable=True)

    assignment = relationship("LarcAssignment", back_populates="checkouts",
                               foreign_keys=[assignment_id])


# ─── Owed list (patients waiting for a reallocated device) ──────────

class LarcOwedPatient(Base):
    """When a device assigned to Patient A is reallocated (after 6 months
    unused OR within 365 days of expiry), Patient A goes on the Owed
    list until the original device's expiration date. If they come back,
    we re-allocate a fresh device + re-verify benefits."""
    __tablename__ = "larc_owed_patients"
    __table_args__ = (
        Index("ix_larc_owed_chart", "chart_number"),
    )

    id = Column(GUID(), primary_key=True, default=new_uuid)
    chart_number = Column(String(40), nullable=False)
    patient_name = Column(String(200), nullable=False)
    original_assignment_id = Column(GUID(), ForeignKey("larc_assignments.id"), nullable=False)
    original_device_type_id = Column(GUID(), ForeignKey("larc_device_types.id"), nullable=False)
    owed_since = Column(DateTime, default=datetime.utcnow, nullable=False)
    # The latest the patient can still claim the original device (typically
    # the original device's expiration date)
    expires_at = Column(Date, nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    resolved_by = Column(String(200), nullable=True)
    resolution = Column(String(40), nullable=True)   # 'reallocated' | 'declined' | 'expired'
    notes = Column(Text, nullable=True)


# ─── Audit log (every device interaction) ───────────────────────────

class LarcManualSection(Base):
    """Editable LARC operating-procedures manual. Each section is one
    markdown body; managers can edit / reorder / add sections in-app.
    Seeded on first boot with the initial practice rules."""
    __tablename__ = "larc_manual_sections"
    __table_args__ = (
        Index("ix_larc_manual_sort", "sort_order"),
    )

    id = Column(GUID(), primary_key=True, default=new_uuid)
    slug = Column(String(80), nullable=False, unique=True)   # for TOC anchors
    title = Column(String(200), nullable=False)
    body_md = Column(Text, nullable=False, default="")
    sort_order = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow, nullable=False)
    updated_by = Column(String(200), nullable=True)


class LarcInventoryCount(Base):
    """Physical inventory reconciliation session. Staff scans every
    device in a location (or all), and the system compares the scanned
    set to the expected on-hand set. Mismatches surface for resolution."""
    __tablename__ = "larc_inventory_counts"
    __table_args__ = (
        Index("ix_larc_inv_count_started", "started_at"),
    )

    id = Column(GUID(), primary_key=True, default=new_uuid)
    started_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    started_by = Column(String(200), nullable=False)
    finished_at = Column(DateTime, nullable=True)
    finished_by = Column(String(200), nullable=True)
    # Filter scope: 'all' or one of the three locations
    scope_location = Column(String(40), nullable=True)
    # JSON list of device ids the user scanned/counted
    scanned_device_ids = Column(JSON, nullable=False, default=list)
    # JSON list of device ids the system EXPECTED to find (snapshot at start)
    expected_device_ids = Column(JSON, nullable=False, default=list)
    notes = Column(Text, nullable=True)
    # 'in_progress' | 'reconciled' | 'cancelled'
    status = Column(String(20), default="in_progress", nullable=False)


class LarcAuditEvent(Base):
    """Per-row audit log. Every state change on a LARC device, assignment,
    checkout, or owed-patient writes one of these. Filterable on the
    audit page by actor / device / patient / system."""
    __tablename__ = "larc_audit_events"
    __table_args__ = (
        Index("ix_larc_audit_time", "occurred_at"),
        Index("ix_larc_audit_actor", "actor"),
        Index("ix_larc_audit_device", "device_id"),
        Index("ix_larc_audit_chart", "chart_number"),
        Index("ix_larc_audit_action", "action"),
    )

    id = Column(GUID(), primary_key=True, default=new_uuid)
    occurred_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    actor = Column(String(200), nullable=False)   # user email or 'system:<job>'
    action = Column(String(60), nullable=False)
    # Examples: device_added, device_assigned, device_checked_out, checkout_approved,
    #           checkout_denied, insertion_recorded, defective_marked, replacement_received,
    #           billed, device_reallocated, owed_added, owed_resolved, expired
    device_id = Column(GUID(), ForeignKey("larc_devices.id"), nullable=True)
    assignment_id = Column(GUID(), ForeignKey("larc_assignments.id"), nullable=True)
    checkout_id = Column(GUID(), ForeignKey("larc_checkouts.id"), nullable=True)
    chart_number = Column(String(40), nullable=True)
    patient_name = Column(String(200), nullable=True)
    # Free-form payload for context (before/after snapshots, notes)
    detail = Column(JSON, nullable=True)
    summary = Column(Text, nullable=True)   # one-line human description


# ─── LARC enrollment envelope (BoldSign pharmacy-order forms) ──────

class LarcEnrollmentEnvelope(Base):
    """One BoldSign envelope sent for a pharmacy-order LARC assignment.

    Tracks the three-signer flow (Receptionist → Patient → Provider) and
    the downstream auto-fax to the dispensing pharmacy. One row per
    (assignment, template) pair — uniqueness lets `assignment.id` be
    re-used if a form has to be voided and re-sent under a different
    template (e.g., assignment switched from Mirena to Paragard)."""
    __tablename__ = "larc_enrollment_envelopes"
    __table_args__ = (
        Index("ix_larc_envelope_assignment", "assignment_id"),
        Index("ix_larc_envelope_boldsign", "boldsign_envelope_id"),
        Index("ix_larc_envelope_status", "status"),
    )

    id = Column(GUID(), primary_key=True, default=new_uuid)
    assignment_id = Column(GUID(),
                            ForeignKey("larc_assignments.id", ondelete="CASCADE"),
                            nullable=False)
    # The device-type-level template ID we sent against (denormalised so a
    # later change to LarcDeviceType.enrollment_form_template doesn't
    # rewrite history).
    boldsign_template_id = Column(String(80), nullable=False)
    boldsign_envelope_id = Column(String(80), nullable=True, unique=True)

    # values: pending | sent | partially_signed | signed | declined |
    #         voided | failed | faxed | fax_failed
    status = Column(String(20), default="pending", nullable=False)
    sent_at      = Column(DateTime, nullable=True)
    receptionist_signed_at = Column(DateTime, nullable=True)
    patient_signed_at      = Column(DateTime, nullable=True)
    provider_signed_at     = Column(DateTime, nullable=True)
    signed_at    = Column(DateTime, nullable=True)  # all three done
    declined_at  = Column(DateTime, nullable=True)
    voided_at    = Column(DateTime, nullable=True)

    # Auto-fax step (after all signers complete)
    faxed_at         = Column(DateTime, nullable=True)
    fax_message_id   = Column(String(80), nullable=True)
    fax_status       = Column(String(40), nullable=True)  # Queued | Sent | SendingFailed
    fax_to           = Column(String(40), nullable=True)
    fax_attempts     = Column(Integer, default=0, nullable=False)
    last_fax_error   = Column(Text, nullable=True)

    # Bookkeeping
    sent_by      = Column(String(200), nullable=True)
    last_synced_at = Column(DateTime, nullable=True)
    last_error   = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow, nullable=False)

    assignment = relationship("LarcAssignment")
