"""Active AR module — tracks unpaid claims for follow-up.

Standalone schema, no dependency on the messy historical claim/payment
tables. Linked to existing patients via `patient_external_id` (= chart #)
for chart context only.
"""
from __future__ import annotations

from datetime import datetime
from sqlalchemy import (
    Boolean, Column, String, Date, DateTime, Numeric, Integer, ForeignKey,
    JSON, Text, UniqueConstraint, Index,
)
from sqlalchemy.orm import relationship
from app.database import Base
from app.models.guid import GUID, new_uuid


class ActiveClaim(Base):
    """One unpaid claim being actively worked. Sourced from the Greenway
    'Unpaid Claims' export and refreshed by re-uploading the same report."""
    __tablename__ = "active_claims"
    __table_args__ = (
        UniqueConstraint(
            "claim_number", "insurance_priority",
            name="uq_active_claim_number_priority",
        ),
        Index("ix_active_claim_workflow_state", "workflow_state"),
        Index("ix_active_claim_payor", "payor_id"),
    )

    id = Column(GUID(), primary_key=True, default=new_uuid)
    # Optimistic locking — prevents two billers from racing payment posts.
    version_id = Column(Integer, default=1, nullable=False)
    __mapper_args__ = {"version_id_col": version_id}

    # Identity from the unpaid-claims export
    claim_number = Column(String(50), nullable=False, index=True)
    patient_external_id = Column(String(50), nullable=False, index=True)
    patient_name = Column(String(200))                 # denormalized for fast display
    dos = Column(Date, index=True)
    care_provider = Column(String(200))

    claim_state = Column(String(20))                   # 'Open' / 'Closed'
    claim_status = Column(String(40))                  # 'New/No EOB' / 'Paid Partial'

    # Money fields (from the export)
    claim_amount = Column(Numeric(12, 2))              # original billed
    line_balance = Column(Numeric(12, 2))
    insurance_balance = Column(Numeric(12, 2))         # what we're chasing
    total_charges = Column(Numeric(12, 2))

    # EOB-derived fields (manually entered from the EOB or auto-filled
    # from a 835 ERA later). Independent of claim_amount/insurance_balance
    # which come from the unpaid-claims export.
    allowed_amount = Column(Numeric(12, 2), nullable=True)
    contractual_adjustment = Column(Numeric(12, 2), nullable=True)
    copay = Column(Numeric(12, 2), nullable=True)
    deductible = Column(Numeric(12, 2), nullable=True)
    coinsurance = Column(Numeric(12, 2), nullable=True)
    patient_balance = Column(Numeric(12, 2), nullable=True)
    eob_notes = Column(Text, nullable=True)

    # Charge Analysis enrichment fields — populated from a Charge Analysis
    # XLS upload, aggregated per Visit ID. Optional; absent for claims
    # that haven't been enriched yet.
    procedure_codes = Column(Text, nullable=True)        # comma-separated CPTs
    procedure_modifiers = Column(Text, nullable=True)    # parallel to CPTs
    diagnosis_codes = Column(Text, nullable=True)        # comma-separated ICD-10
    billable_provider_npi = Column(String(20), nullable=True)
    rendering_provider_name_full = Column(String(200), nullable=True)
    rendering_provider_npi = Column(String(20), nullable=True)
    service_location = Column(String(200), nullable=True)
    patient_dob = Column(Date, nullable=True)
    secondary_insurance_company = Column(String(200), nullable=True)
    secondary_plan_name = Column(String(200), nullable=True)
    secondary_policy_number = Column(String(100), nullable=True)
    primary_plan_detail = Column(String(200), nullable=True)   # specific plan/group
    enriched_at = Column(DateTime, nullable=True)

    # Per-line service detail (JSON array). Each entry:
    #   {line, cpt, modifiers, units, charge, gross_charge, fee_schedule_charge, dx}
    service_lines_json = Column(Text, nullable=True)

    # Precomputed timely-filing deadline. Derived from (insurance_company, dos)
    # via timely_filing_info(). Persisted so the AR summary endpoint doesn't
    # have to load every open claim into Python and call the classifier per
    # row. Refreshed on import + when DOS/insurance change.
    # (Fable cross-cutting audit #13.)
    tf_deadline_date = Column(Date, nullable=True, index=True)
    tf_days_allowed = Column(Integer, nullable=True)

    # Insurance routing
    insurance_priority = Column(String(20), nullable=False)   # Primary / Secondary / Tertiary
    payor_id = Column(String(50))
    insurance_company = Column(String(200))
    plan_name = Column(String(200))
    policy_number = Column(String(100))
    practice_location = Column(String(200))

    # Workflow state — local to this module, never overwritten by re-upload
    workflow_state = Column(String(40), default="new", nullable=False)
    # values: new, in_progress, waiting_payer, waiting_patient, appealed,
    # paid, written_off, closed
    assigned_to = Column(String(120), nullable=True)   # user email

    # Money tracking (incremented as payments are allocated)
    paid_amount = Column(Numeric(12, 2), default=0, nullable=False)
    paid_in_full_at = Column(DateTime, nullable=True)
    written_off_at = Column(DateTime, nullable=True)
    written_off_amount = Column(Numeric(12, 2), nullable=True)
    written_off_reason = Column(String(200), nullable=True)

    # Waystar status sync (filled in later)
    last_status_check_at = Column(DateTime, nullable=True)
    last_status_response = Column(Text, nullable=True)        # JSON string

    # Timestamps
    imported_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_seen_in_export_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    notes = relationship(
        "ActiveClaimNote", back_populates="claim",
        cascade="all, delete-orphan", order_by="desc(ActiveClaimNote.created_at)",
    )
    allocations = relationship(
        "PaymentAllocation", back_populates="claim",
        cascade="all, delete-orphan",
    )
    documents = relationship(
        "ActiveClaimDocument", back_populates="claim",
        cascade="all, delete-orphan", order_by="desc(ActiveClaimDocument.uploaded_at)",
    )


class ActiveClaimNote(Base):
    """Append-only follow-up log per claim."""
    __tablename__ = "active_claim_notes"

    id = Column(GUID(), primary_key=True, default=new_uuid)
    active_claim_id = Column(GUID(), ForeignKey("active_claims.id", ondelete="CASCADE"),
                             nullable=False, index=True)
    user = Column(String(120))
    action_type = Column(String(40), default="note", nullable=False)
    # values: note, status_check, phone_call, fax_sent, appeal_submitted,
    # paid, written_off, reassigned, status_changed, payment_applied, other
    note = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    claim = relationship("ActiveClaim", back_populates="notes")


class InsurancePayment(Base):
    """A check or EFT received from a payer. Total amount is allocated
    across one or more ActiveClaim records via PaymentAllocation."""
    __tablename__ = "insurance_payments"

    id = Column(GUID(), primary_key=True, default=new_uuid)
    check_number = Column(String(50), index=True)
    check_date = Column(Date)
    payer_name = Column(String(200), nullable=False)
    total_amount = Column(Numeric(12, 2), nullable=False)
    payment_method = Column(String(40))                # Check, EFT, ACH, etc.
    notes = Column(Text)
    posted_by = Column(String(120))
    posted_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    allocations = relationship(
        "PaymentAllocation", back_populates="payment",
        cascade="all, delete-orphan",
    )

    @property
    def allocated_total(self) -> float:
        return float(sum(a.amount_applied or 0 for a in self.allocations))

    @property
    def unallocated(self) -> float:
        return float(self.total_amount or 0) - self.allocated_total


class PaymentAllocation(Base):
    """Splits an InsurancePayment across one or more ActiveClaim records."""
    __tablename__ = "payment_allocations"

    id = Column(GUID(), primary_key=True, default=new_uuid)
    payment_id = Column(GUID(), ForeignKey("insurance_payments.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    active_claim_id = Column(GUID(), ForeignKey("active_claims.id", ondelete="CASCADE"),
                             nullable=False, index=True)
    amount_applied = Column(Numeric(12, 2), nullable=False)
    allocation_note = Column(String(200), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    payment = relationship("InsurancePayment", back_populates="allocations")
    claim = relationship("ActiveClaim", back_populates="allocations")


class ActiveClaimDocument(Base):
    """Files attached to an active claim — EOBs, denial letters, appeal
    submissions, payer correspondence, etc."""
    __tablename__ = "active_claim_documents"

    id = Column(GUID(), primary_key=True, default=new_uuid)
    active_claim_id = Column(GUID(), ForeignKey("active_claims.id", ondelete="CASCADE"),
                             nullable=False, index=True)

    document_type = Column(String(40), default="Other", nullable=False)
    # values: 'EOB', 'Denial Letter', 'Appeal', 'Correspondence',
    #         'Medical Records', 'Insurance Card', 'Other'
    filename = Column(String(255), nullable=False)
    content_type = Column(String(100))
    file_size = Column(Integer)
    file_path = Column(String(500), nullable=False)         # absolute path on disk
    description = Column(Text, nullable=True)

    uploaded_by = Column(String(120))
    uploaded_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    claim = relationship("ActiveClaim", back_populates="documents")


class ActiveARFilterPreset(Base):
    """A named filter preset on the Active AR queue. Stored per user so
    each biller keeps their own working set (e.g. 'BCBS 60-90d unassigned',
    'My past-TF claims')."""
    __tablename__ = "active_ar_filter_presets"
    __table_args__ = (
        Index("ix_active_ar_filter_owner", "owner_email"),
        UniqueConstraint("owner_email", "name",
                         name="uq_active_ar_filter_owner_name"),
    )

    id           = Column(GUID(), primary_key=True, default=new_uuid)
    owner_email  = Column(String(200), nullable=False)
    name         = Column(String(120), nullable=False)
    filters_json = Column(JSON, nullable=False, default=dict)
    is_default   = Column(Boolean, default=False, nullable=False)
    created_at   = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at   = Column(DateTime, default=datetime.utcnow,
                          onupdate=datetime.utcnow, nullable=False)
