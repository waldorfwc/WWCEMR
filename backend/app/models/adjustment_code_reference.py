"""Reference table for X12 CARC (Claim Adjustment Reason Codes) and
RARC (Remittance Advice Remark Codes), plus plain-English explanations
and how-to-fix guidance used by the denial-code lookup on the Denials
page.

Official verbiage is static (maintained by X12/WPC, refreshed quarterly).
Plain-English text and fix guidance are LLM-generated once at seed time
and editable afterwards.
"""
from __future__ import annotations

from sqlalchemy import Column, String, Text, DateTime, UniqueConstraint, Index, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime
import enum

from app.database import Base
from app.models.guid import GUID, new_uuid


class AdjustmentCodeType(str, enum.Enum):
    CARC = "CARC"  # Claim Adjustment Reason Code (X12 CARC list)
    RARC = "RARC"  # Remittance Advice Remark Code (X12 RARC list)


class AdjustmentCodeReference(Base):
    __tablename__ = "adjustment_code_references"

    id = Column(GUID(), primary_key=True, default=new_uuid)

    # "CARC" or "RARC"
    code_type = Column(String(4), nullable=False, index=True)
    # The code itself, e.g. "45", "197", "M86", "N290"
    code = Column(String(10), nullable=False, index=True)

    # Official X12/WPC verbiage — read-only, matches the published list.
    official_verbiage = Column(Text, nullable=False)

    # LLM-generated plain-English explanation ("what it means").
    plain_english = Column(Text, nullable=True)
    # LLM-generated fix guidance ("what to do about it at a billing desk").
    how_to_fix = Column(Text, nullable=True)

    # WWC-specific notes — billing team's practice knowledge for this code.
    # Current value only; full revision history in AdjustmentCodeNoteRevision.
    wwc_notes = Column(Text, nullable=True)
    wwc_notes_updated_by = Column(String(255), nullable=True)  # user email
    wwc_notes_updated_at = Column(DateTime, nullable=True)

    # Provenance: whether plain_english/how_to_fix were LLM-generated or
    # hand-edited by a user. Useful for later bulk-regeneration.
    enrichment_source = Column(String(20), nullable=True)  # "llm" | "manual"

    last_enriched_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    note_revisions = relationship(
        "AdjustmentCodeNoteRevision",
        back_populates="code_ref",
        cascade="all, delete-orphan",
        order_by="desc(AdjustmentCodeNoteRevision.saved_at)",
    )

    __table_args__ = (
        UniqueConstraint("code_type", "code", name="uq_code_type_code"),
        Index("ix_code_type_code", "code_type", "code"),
    )


class AdjustmentCodeComboCache(Base):
    """Cached LLM-synthesized explanations for a specific combo of
    group_code + CARC + RARC list. Populated on demand: first biller to
    hit a combo pays the LLM call; every biller after hits the cache.
    """
    __tablename__ = "adjustment_code_combo_cache"

    id = Column(GUID(), primary_key=True, default=new_uuid)
    # Canonical key: "CO|197|N20" or "CO|45|M86,N10" (RARCs sorted and joined)
    combo_key = Column(String(200), nullable=False, unique=True, index=True)
    group_code = Column(String(5), nullable=False)
    carc = Column(String(10), nullable=False)
    rarcs = Column(Text, nullable=False, default="")  # comma-joined, sorted

    plain_english = Column(Text, nullable=False)
    how_to_fix = Column(Text, nullable=False)
    model_used = Column(String(50), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)


class AdjustmentCodeNoteRevision(Base):
    """Append-only history of WWC-notes edits per code.

    Every save of `AdjustmentCodeReference.wwc_notes` inserts a new row
    here with the NEW body, the editor, and the timestamp. Nothing is
    ever updated/deleted — the latest row matches the live value on the
    reference table.
    """
    __tablename__ = "adjustment_code_note_revisions"

    id = Column(GUID(), primary_key=True, default=new_uuid)
    code_ref_id = Column(
        GUID(),
        ForeignKey("adjustment_code_references.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    body = Column(Text, nullable=False)
    saved_by = Column(String(255), nullable=False)  # user email
    saved_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    code_ref = relationship("AdjustmentCodeReference", back_populates="note_revisions")
