"""Per-insurance CPT fee schedule + CCI / Multiple-Procedure-Reduction
edits used to estimate the allowed amount on a surgery."""
from datetime import datetime

from sqlalchemy import (
    Column, String, Text, DateTime, Numeric, Date,
    UniqueConstraint, Index,
)

from app.database import Base
from app.models.guid import GUID, new_uuid


class SurgeryFeeScheduleEntry(Base):
    """A single contracted allowed amount.

    Lookup key is (insurance_name, cpt_code). Insurance is stored as the
    display string the rest of the app uses for surgery.primary_insurance
    so a join isn't required for the calculator."""
    __tablename__ = "surgery_fee_schedule"
    __table_args__ = (
        UniqueConstraint("insurance_name", "cpt_code",
                         name="ux_fee_schedule_payer_cpt"),
        Index("ix_fee_schedule_insurance", "insurance_name"),
        Index("ix_fee_schedule_cpt", "cpt_code"),
    )

    id              = Column(GUID(), primary_key=True, default=new_uuid)
    insurance_name  = Column(String(120), nullable=False)
    cpt_code        = Column(String(10),  nullable=False)
    allowed_amount  = Column(Numeric(10, 2), nullable=False)
    notes           = Column(Text, nullable=True)
    effective_from  = Column(Date, nullable=True)

    created_at      = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at      = Column(DateTime, default=datetime.utcnow,
                              onupdate=datetime.utcnow, nullable=False)
    created_by      = Column(String(120), nullable=True)


class SurgeryCciEdit(Base):
    """Correct Coding Initiative / Multiple-Procedure-Reduction rule
    between a pair of CPTs.

    action:
      'blocked'    — the secondary CPT cannot be billed with the primary;
                     calculator drops it from the allowed total.
      'reduce_50'  — second procedure pays at 50% allowed (the standard
                     MPR rule). Applies even without an explicit row when
                     two CPTs are billed together; this table is for
                     overrides (e.g. payer-specific reductions of 25% or
                     no reduction at all when both should pay 100%).
      'allow_100'  — explicitly bypass the default MPR reduction.
    """
    __tablename__ = "surgery_cci_edits"
    __table_args__ = (
        UniqueConstraint("cpt_primary", "cpt_secondary",
                         name="ux_cci_pair"),
        Index("ix_cci_primary", "cpt_primary"),
        Index("ix_cci_secondary", "cpt_secondary"),
    )

    id              = Column(GUID(), primary_key=True, default=new_uuid)
    cpt_primary     = Column(String(10), nullable=False)
    cpt_secondary   = Column(String(10), nullable=False)
    action          = Column(String(20), nullable=False)
    notes           = Column(Text, nullable=True)

    created_at      = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_by      = Column(String(120), nullable=True)


CCI_ACTIONS = ("blocked", "reduce_50", "allow_100")
