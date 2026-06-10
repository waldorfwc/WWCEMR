"""BAI v2 generator — bank-CSV imports + BAI2 file outputs."""
from __future__ import annotations

from datetime import datetime
from app.utils.dt import now_utc_naive
from sqlalchemy import (
    Column, String, Date, DateTime, Numeric, Integer, ForeignKey, Text,
    UniqueConstraint, Index,
)
from sqlalchemy.orm import relationship
from app.database import Base
from app.models.guid import GUID, new_uuid


class Bai2Import(Base):
    """One CSV upload → one generated BAI2 file. The file lives on disk and
    can be re-downloaded forever via /imports/{id}/download."""
    __tablename__ = "bai2_imports"

    id = Column(GUID(), primary_key=True, default=new_uuid)

    # Source
    csv_filename = Column(String(255))
    csv_path     = Column(String(500))

    # Bank context (from the upload form; persists for filename/header)
    bank_name = Column(String(50))            # e.g. "PNC"
    account_last_4 = Column(String(4))        # e.g. "4567"
    account_full = Column(String(40), nullable=True)  # full acct # for BAI2 03 record

    # Generated file
    bai2_filename = Column(String(255))       # "PNC x4567 2026.03.10 - 2026.03.17.bai"
    bai2_path = Column(String(500))

    # Date range covered (min/max transaction date in this file)
    date_range_start = Column(Date)
    date_range_end = Column(Date)

    # Counts
    csv_row_count = Column(Integer, default=0)
    transactions_included = Column(Integer, default=0)
    skipped_withdrawal = Column(Integer, default=0)
    skipped_modmed = Column(Integer, default=0)
    skipped_stripe = Column(Integer, default=0)
    skipped_zero = Column(Integer, default=0)
    skipped_duplicate_in_file = Column(Integer, default=0)    # dups within this CSV
    skipped_prior_imports = Column(Integer, default=0)        # already in DB from prior

    total_amount = Column(Numeric(14, 2))

    notes = Column(Text, nullable=True)
    generated_at = Column(DateTime, default=now_utc_naive, nullable=False, index=True)
    generated_by = Column(String(120))

    transactions = relationship(
        "Bai2Transaction", back_populates="import_",
        cascade="all, delete-orphan", lazy="dynamic",
    )


class Bai2Transaction(Base):
    """One retained transaction from a Bai2Import. dedup_key is unique across
    the entire table — the importer skips any row whose key matches an
    existing record (cross-import dedup)."""
    __tablename__ = "bai2_transactions"
    __table_args__ = (
        Index("ix_bai2_txn_import", "import_id"),
        Index("ix_bai2_txn_date", "transaction_date"),
    )

    id = Column(GUID(), primary_key=True, default=new_uuid)
    import_id = Column(GUID(), ForeignKey("bai2_imports.id", ondelete="CASCADE"),
                       nullable=False)

    transaction_date = Column(Date)
    description = Column(Text)              # original CSV description
    formatted_text = Column(Text)           # what appears in BAI2 16-record
    amount = Column(Numeric(12, 2))
    last_4 = Column(String(4), nullable=True)
    method = Column(String(20))             # ACH / CHECK / WIRE
    bai_type_code = Column(String(10))      # 195 / 165 / 252

    dedup_key = Column(String(64), nullable=False, unique=True)

    created_at = Column(DateTime, default=now_utc_naive, nullable=False)

    import_ = relationship("Bai2Import", back_populates="transactions")
