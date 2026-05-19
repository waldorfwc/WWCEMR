"""Pellet inventory + receiving + transfer + disposal + counts.

Tracks Bio-Identical Hormone Pellets (Estradiol + Testosterone) ordered
from Qualgen. Inventory is by individual dose; each shipment is a lot
with an expiration date. Testosterone is DEA Schedule III, so every
state change writes one row to pellet_audit_events for perpetual
inventory compliance.

Three locations: White Plains (receives all orders), Brandywine, Arlington.
A single lot can be split across locations via PelletTransfer; per-location
balances live in PelletStock.

Disposals (dropped / broken / expired) go to biohazard — we eat the
loss, no manufacturer return.
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


HORMONES = ["estradiol", "testosterone"]

PELLET_LOCATIONS = ["white_plains", "brandywine", "arlington"]

# Disposal reasons — every disposal becomes a row in pellet_disposals
DISPOSAL_REASONS = ["dropped", "broken", "expired", "other"]

# Stock-balance row statuses
STOCK_STATUSES = ["active", "depleted", "quarantined"]


# ─── Dose-type catalog (10 SKUs) ────────────────────────────────────

class PelletDoseType(Base):
    """Catalog row per (hormone, dose_mg). Seeded on boot from
    pellet_seed.py; reorder threshold + qty are editable per row."""
    __tablename__ = "pellet_dose_types"
    __table_args__ = (
        UniqueConstraint("hormone", "dose_mg", name="uq_pellet_dose_unique"),
    )

    id                 = Column(GUID(), primary_key=True, default=new_uuid)
    hormone            = Column(String(20), nullable=False, index=True)   # estradiol | testosterone
    dose_mg            = Column(Numeric(8, 2), nullable=False)            # e.g. 12.5
    label              = Column(String(80),  nullable=False)              # "Estradiol 12.5mg"
    is_controlled      = Column(Boolean, default=False, nullable=False)   # testosterone=True
    reorder_threshold_packs = Column(Integer, nullable=True)              # in packs (global)
    reorder_qty_packs       = Column(Integer, nullable=True)              # in packs
    # Per-location threshold overrides — {"white_plains": 20, "brandywine": 5,
    # "arlington": 5}. When set, dashboard reorder alerts trigger per location
    # rather than on the global total. Unset locations fall back to no alert.
    reorder_thresholds_by_location = Column(JSON, nullable=True)
    pack_sizes         = Column(JSON, default=list)                       # [6, 12, 30]
    typical_cost_per_dose = Column(Numeric(8, 2), nullable=True)
    notes              = Column(Text, nullable=True)
    is_active          = Column(Boolean, default=True, nullable=False)
    created_at         = Column(DateTime, default=datetime.utcnow, nullable=False)


# ─── Lots (Qualgen shipment batches) ────────────────────────────────

class PelletLot(Base):
    """A Qualgen-issued lot. Created at receipt time; expiration is the
    Qualgen-printed date. doses_originally_received is immutable (audit);
    current per-location count lives in PelletStock."""
    __tablename__ = "pellet_lots"
    __table_args__ = (
        Index("ix_pellet_lot_dose", "dose_type_id"),
    )

    id                          = Column(GUID(), primary_key=True, default=new_uuid)
    dose_type_id                = Column(GUID(), ForeignKey("pellet_dose_types.id"),
                                          nullable=False, index=True)
    qualgen_lot_number          = Column(String(80), nullable=False, index=True)
    expiration_date             = Column(Date, nullable=False)
    doses_originally_received   = Column(Integer, nullable=False)
    packs_received              = Column(Integer, nullable=True)
    pack_size                   = Column(Integer, nullable=True)
    receipt_id                  = Column(GUID(), ForeignKey("pellet_receipts.id"),
                                          nullable=True, index=True)
    received_at                 = Column(DateTime, default=datetime.utcnow, nullable=False)
    received_by                 = Column(String(120), nullable=True)
    notes                       = Column(Text, nullable=True)
    # Acquisition cost — copied from the linked PelletOrderLine at
    # manifest-verify time. Drives disposal write-off valuation and the
    # future controlled-substance cost report.
    unit_cost                   = Column(Numeric(10, 2), nullable=True)   # per pack
    cost_per_dose               = Column(Numeric(10, 4), nullable=True)

    dose_type = relationship("PelletDoseType")
    stock_rows = relationship("PelletStock", cascade="all, delete-orphan",
                                back_populates="lot")


# ─── Per-location running balances ──────────────────────────────────

class PelletStock(Base):
    """Running balance of one lot at one location. Adjusted by receipt,
    transfer, disposal, and (Phase B) insertion. Daily count compares
    expected vs actual."""
    __tablename__ = "pellet_stock"
    __table_args__ = (
        UniqueConstraint("lot_id", "location", name="uq_pellet_stock_lot_loc"),
        Index("ix_pellet_stock_loc", "location"),
    )

    id        = Column(GUID(), primary_key=True, default=new_uuid)
    lot_id    = Column(GUID(), ForeignKey("pellet_lots.id"), nullable=False)
    location  = Column(String(40), nullable=False)
    doses_on_hand = Column(Integer, default=0, nullable=False)
    status    = Column(String(20), default="active", nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow,
                         onupdate=datetime.utcnow, nullable=False)

    lot = relationship("PelletLot", back_populates="stock_rows")


# ─── Orders (Qualgen purchase order, placed BEFORE the shipment) ────

ORDER_STATUSES = ["placed", "partially_received", "received", "cancelled"]

PAYMENT_METHODS = ["credit_card", "ach", "check", "wire", "other"]


class PelletOrder(Base):
    """A Qualgen purchase order. Placed BEFORE a shipment arrives — a
    receipt cannot be created without either a matching order or a
    `is_replacement` flag pointing at a damaged-pellet disposal.

    Order amendments after the first receipt are allowed but write an
    `order_amended` audit row so the paper trail stays intact (DEA
    perpetual-inventory expectation).
    """
    __tablename__ = "pellet_orders"

    id                      = Column(GUID(), primary_key=True, default=new_uuid)
    qualgen_order_number    = Column(String(80),  nullable=True)
    order_date              = Column(Date,        nullable=False)
    expected_delivery_date  = Column(Date,        nullable=True)
    placed_by               = Column(String(120), nullable=False)
    status                  = Column(String(30),  default="placed", nullable=False)
    # values: placed | partially_received | received | cancelled

    # Payment
    payment_method          = Column(String(40),  nullable=True)   # credit_card | ach | check | wire | other
    payment_confirmation    = Column(String(120), nullable=True)
    shipping_cost           = Column(Numeric(10, 2), default=0)
    tax                     = Column(Numeric(10, 2), default=0)

    # Replacement (manufacturer resending damaged pellets)
    is_replacement          = Column(Boolean,    default=False, nullable=False)
    replaces_disposal_id    = Column(GUID(), ForeignKey("pellet_disposals.id"),
                                       nullable=True)

    notes                   = Column(Text, nullable=True)
    created_at              = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at              = Column(DateTime, default=datetime.utcnow,
                                       onupdate=datetime.utcnow, nullable=False)

    lines      = relationship("PelletOrderLine",
                                cascade="all, delete-orphan",
                                back_populates="order",
                                order_by="PelletOrderLine.created_at")
    attachments = relationship("PelletOrderAttachment",
                                 cascade="all, delete-orphan",
                                 order_by="desc(PelletOrderAttachment.uploaded_at)")
    receipts    = relationship("PelletReceipt",
                                 primaryjoin="PelletReceipt.order_id == PelletOrder.id",
                                 foreign_keys="PelletReceipt.order_id")


class PelletOrderLine(Base):
    """One line of a Qualgen order. cost_per_dose = unit_cost / pack_size
    (computed on the fly; not stored). doses_ordered = pack_size * pack_count."""
    __tablename__ = "pellet_order_lines"
    __table_args__ = (
        Index("ix_pellet_order_line_order", "order_id"),
    )

    id              = Column(GUID(), primary_key=True, default=new_uuid)
    order_id        = Column(GUID(), ForeignKey("pellet_orders.id"), nullable=False)
    dose_type_id    = Column(GUID(), ForeignKey("pellet_dose_types.id"), nullable=False)
    pack_size       = Column(Integer, nullable=False)   # 6 | 12 | 30
    pack_count      = Column(Integer, nullable=False)
    unit_cost       = Column(Numeric(10, 2), nullable=False)   # cost per pack
    doses_received  = Column(Integer, default=0, nullable=False)
    notes           = Column(Text, nullable=True)
    created_at      = Column(DateTime, default=datetime.utcnow, nullable=False)

    order     = relationship("PelletOrder", back_populates="lines")
    dose_type = relationship("PelletDoseType")


class PelletOrderAttachment(Base):
    """Uploaded PDF invoice / receipt for an order. File lives on disk
    under uploads/pellet_orders/; we keep filename + content-type + size
    here for the listing UI and to drive the download endpoint."""
    __tablename__ = "pellet_order_attachments"
    __table_args__ = (
        Index("ix_pellet_order_attachment_order", "order_id"),
    )

    id            = Column(GUID(), primary_key=True, default=new_uuid)
    order_id      = Column(GUID(), ForeignKey("pellet_orders.id"), nullable=False)
    filename      = Column(String(255), nullable=False)
    content_type  = Column(String(80),  nullable=True)
    size_bytes    = Column(Integer, nullable=True)
    storage_path  = Column(Text, nullable=False)   # absolute path on disk
    uploaded_at   = Column(DateTime, default=datetime.utcnow, nullable=False)
    uploaded_by   = Column(String(120), nullable=False)


# ─── Receipt (Qualgen shipment header) ──────────────────────────────

class PelletReceipt(Base):
    """A Qualgen shipment containing one or more lots. Captures the
    manifest-verification step: nobody adds to inventory until they
    confirm the manifest matches what physically arrived.

    A receipt must reference either a PelletOrder (order_id) or be a
    `is_replacement` for a disposed lot (replaces_disposal_id). Enforced
    in the router, not at the schema level."""
    __tablename__ = "pellet_receipts"

    id                    = Column(GUID(), primary_key=True, default=new_uuid)
    qualgen_order_number  = Column(String(80), nullable=True)
    ordered_date          = Column(Date, nullable=True)
    received_date         = Column(Date, default=lambda: datetime.utcnow().date(),
                                    nullable=False)
    received_by           = Column(String(120), nullable=False)
    location              = Column(String(40), default="white_plains", nullable=False)
    manifest_verified     = Column(Boolean, default=False, nullable=False)
    manifest_verified_by  = Column(String(120), nullable=True)
    manifest_verified_at  = Column(DateTime, nullable=True)
    notes                 = Column(Text, nullable=True)
    created_at            = Column(DateTime, default=datetime.utcnow, nullable=False)

    # New: receipt → order link. Nullable because replacement receipts
    # skip the order and point at a disposal row instead.
    order_id              = Column(GUID(), ForeignKey("pellet_orders.id"), nullable=True)
    is_replacement        = Column(Boolean, default=False, nullable=False)
    replaces_disposal_id  = Column(GUID(), ForeignKey("pellet_disposals.id"),
                                     nullable=True)

    lots = relationship("PelletLot",
                         primaryjoin="PelletLot.receipt_id == PelletReceipt.id",
                         foreign_keys="PelletLot.receipt_id")
    attachments = relationship("PelletReceiptAttachment",
                                 cascade="all, delete-orphan",
                                 order_by="desc(PelletReceiptAttachment.uploaded_at)")


class PelletReceiptAttachment(Base):
    """Uploaded PDF (packing slip / shipping manifest) for a receipt.
    File lives on disk under uploads/pellet_receipts/."""
    __tablename__ = "pellet_receipt_attachments"
    __table_args__ = (
        Index("ix_pellet_receipt_attachment_receipt", "receipt_id"),
    )

    id            = Column(GUID(), primary_key=True, default=new_uuid)
    receipt_id    = Column(GUID(), ForeignKey("pellet_receipts.id"), nullable=False)
    filename      = Column(String(255), nullable=False)
    content_type  = Column(String(80),  nullable=True)
    size_bytes    = Column(Integer, nullable=True)
    storage_path  = Column(Text, nullable=False)
    uploaded_at   = Column(DateTime, default=datetime.utcnow, nullable=False)
    uploaded_by   = Column(String(120), nullable=False)


# ─── Inter-location transfers ───────────────────────────────────────

class PelletTransfer(Base):
    """A move of N doses from one location to another. Decrements
    'from' stock immediately; increments 'to' stock on confirmed
    receipt. Audited per the DEA Schedule III chain-of-custody rules."""
    __tablename__ = "pellet_transfers"

    id              = Column(GUID(), primary_key=True, default=new_uuid)
    lot_id          = Column(GUID(), ForeignKey("pellet_lots.id"), nullable=False)
    from_location   = Column(String(40), nullable=False)
    to_location     = Column(String(40), nullable=False)
    doses           = Column(Integer, nullable=False)
    sent_at         = Column(DateTime, default=datetime.utcnow, nullable=False)
    sent_by         = Column(String(120), nullable=False)
    # Courier handoff (middle leg of chain-of-custody). Sch III transfers
    # cannot be received until the courier has signed in here.
    courier_user           = Column(String(120), nullable=True)
    courier_picked_up_at   = Column(DateTime, nullable=True)
    courier_notes          = Column(Text, nullable=True)
    received_at     = Column(DateTime, nullable=True)
    received_by     = Column(String(120), nullable=True)
    status          = Column(String(20), default="packed", nullable=False)
    # values: packed | in_transit | received | cancelled
    notes           = Column(Text, nullable=True)

    lot = relationship("PelletLot")


# ─── Disposals (biohazard — dropped / broken / expired / other) ─────

class PelletDisposal(Base):
    """A single disposal event. For testosterone (Schedule III), a
    witness signature is REQUIRED — enforced in the router."""
    __tablename__ = "pellet_disposals"

    id            = Column(GUID(), primary_key=True, default=new_uuid)
    lot_id        = Column(GUID(), ForeignKey("pellet_lots.id"), nullable=False)
    location      = Column(String(40), nullable=False)
    doses         = Column(Integer, nullable=False)
    reason        = Column(String(30), nullable=False)   # dropped|broken|expired|other
    occurred_at   = Column(DateTime, default=datetime.utcnow, nullable=False)
    performed_by  = Column(String(120), nullable=False)
    witness_user  = Column(String(120), nullable=True)   # required for controlled
    notes         = Column(Text, nullable=True)

    lot = relationship("PelletLot")


# ─── Daily counts (DEA-grade) ───────────────────────────────────────

class PelletCount(Base):
    """A perpetual-inventory count. One per location per day is the
    expected cadence. When `is_witnessed=True` we require a second-user
    signature (DEA best practice for Schedule III)."""
    __tablename__ = "pellet_counts"

    id            = Column(GUID(), primary_key=True, default=new_uuid)
    location      = Column(String(40), nullable=False, index=True)
    started_at    = Column(DateTime, default=datetime.utcnow, nullable=False)
    started_by    = Column(String(120), nullable=False)
    finished_at   = Column(DateTime, nullable=True)
    finished_by   = Column(String(120), nullable=True)
    witness_user_start = Column(String(120), nullable=True)   # captured at start
    witness_user  = Column(String(120), nullable=True)        # signed at finish
    scope         = Column(String(30), default="all", nullable=False)
    # values: all | controlled_only
    status        = Column(String(20), default="in_progress", nullable=False)
    # values: in_progress | finished | cancelled
    notes         = Column(Text, nullable=True)

    lines = relationship("PelletCountLine", cascade="all, delete-orphan",
                          back_populates="count")
    attachments = relationship("PelletCountAttachment",
                                 cascade="all, delete-orphan",
                                 order_by="desc(PelletCountAttachment.generated_at)")


class PelletCountAttachment(Base):
    """Generated PDF of a finished daily count. One PDF is produced
    automatically when finish_count succeeds; admins can re-generate via
    the endpoint, which appends a new row (history kept)."""
    __tablename__ = "pellet_count_attachments"
    __table_args__ = (
        Index("ix_pellet_count_attachment_count", "count_id"),
    )

    id            = Column(GUID(), primary_key=True, default=new_uuid)
    count_id      = Column(GUID(), ForeignKey("pellet_counts.id"), nullable=False)
    filename      = Column(String(255), nullable=False)
    storage_path  = Column(Text, nullable=False)
    size_bytes    = Column(Integer, nullable=True)
    generated_at  = Column(DateTime, default=datetime.utcnow, nullable=False)
    generated_by  = Column(String(120), nullable=False)


class PelletCountLine(Base):
    """One row per lot×location at the time the count was started. The
    expected_doses snapshot is frozen at start; counted_doses is filled
    in during scanning. variance = counted - expected."""
    __tablename__ = "pellet_count_lines"
    __table_args__ = (
        Index("ix_pellet_count_line_count", "count_id"),
    )

    id              = Column(GUID(), primary_key=True, default=new_uuid)
    count_id        = Column(GUID(), ForeignKey("pellet_counts.id"), nullable=False)
    lot_id          = Column(GUID(), ForeignKey("pellet_lots.id"), nullable=False)
    expected_doses  = Column(Integer, nullable=False)
    counted_doses   = Column(Integer, nullable=True)
    counted_at      = Column(DateTime, nullable=True)
    counted_by      = Column(String(120), nullable=True)
    notes           = Column(Text, nullable=True)

    count = relationship("PelletCount", back_populates="lines")
    lot   = relationship("PelletLot")


# ─── Audit log (perpetual inventory record) ─────────────────────────

class PelletAuditEvent(Base):
    """One row per state change. Schedule III drugs require a permanent
    write-only audit trail; never delete from this table."""
    __tablename__ = "pellet_audit_events"
    __table_args__ = (
        Index("ix_pellet_audit_at", "at"),
        Index("ix_pellet_audit_lot", "lot_id"),
    )

    id           = Column(GUID(), primary_key=True, default=new_uuid)
    at           = Column(DateTime, default=datetime.utcnow, nullable=False)
    actor        = Column(String(120), nullable=False)
    action       = Column(String(60), nullable=False)
    # examples: receipt | manifest_verified | transfer_sent | transfer_received
    #         | disposal | count_started | count_finished | stock_adjusted
    dose_type_id = Column(GUID(), ForeignKey("pellet_dose_types.id"), nullable=True)
    lot_id       = Column(GUID(), ForeignKey("pellet_lots.id"), nullable=True)
    receipt_id   = Column(GUID(), ForeignKey("pellet_receipts.id"), nullable=True)
    transfer_id  = Column(GUID(), ForeignKey("pellet_transfers.id"), nullable=True)
    disposal_id  = Column(GUID(), ForeignKey("pellet_disposals.id"), nullable=True)
    count_id     = Column(GUID(), ForeignKey("pellet_counts.id"), nullable=True)
    location     = Column(String(40), nullable=True)
    delta_doses  = Column(Integer, nullable=True)
    summary      = Column(String(255), nullable=True)
    detail       = Column(JSON, nullable=True)


# ─── Patient-side workflow (Phase B) ────────────────────────────────

VISIT_KINDS = ["initial", "booster", "repeat"]
PATIENT_TYPES = ["new", "established"]

# Default pricing (Klara-message amount that goes into ModMed)
DEFAULT_PRICE_NEW = 500
DEFAULT_PRICE_ESTABLISHED = 400


class PelletPatient(Base):
    """A patient enrolled in the pellet program. One row per chart_number
    (we don't dedup on identity beyond chart). Holds prerequisite-tracking
    fields (mammogram + labs) since they apply to the patient, not the
    individual visit."""
    __tablename__ = "pellet_patients"
    __table_args__ = (
        UniqueConstraint("chart_number", name="uq_pellet_patient_chart"),
        Index("ix_pellet_patient_name", "patient_name"),
    )

    id              = Column(GUID(), primary_key=True, default=new_uuid)
    chart_number    = Column(String(40),  nullable=False)
    patient_name    = Column(String(160), nullable=False)
    patient_dob     = Column(Date, nullable=True)
    patient_email   = Column(String(255), nullable=True)
    patient_phone   = Column(String(40), nullable=True)
    primary_insurance = Column(String(160), nullable=True)
    patient_type    = Column(String(20), default="new", nullable=False)  # new|established
    status          = Column(String(20), default="active", nullable=False)
    # values: active | inactive | declined
    # Patient-level deep link into ModMed (Qlik redirect from the export).
    # Per-visit modmed_link still lives on PelletVisit for appointment links.
    modmed_link     = Column(Text, nullable=True)

    # Prerequisites — verified yes/no + date + result
    mammo_verified  = Column(Boolean, default=False, nullable=False)
    mammo_date      = Column(Date, nullable=True)
    mammo_result    = Column(String(40), nullable=True)    # BI-RADS 1/2, or freeform
    mammo_verified_by = Column(String(120), nullable=True)
    mammo_verified_at = Column(DateTime, nullable=True)

    labs_verified   = Column(Boolean, default=False, nullable=False)
    labs_date       = Column(Date, nullable=True)
    labs_fsh        = Column(String(40), nullable=True)    # numeric or "pending"
    labs_tsh        = Column(String(40), nullable=True)
    labs_estradiol  = Column(String(40), nullable=True)
    labs_verified_by = Column(String(120), nullable=True)
    labs_verified_at = Column(DateTime, nullable=True)

    # Per-patient recall interval (months). 3 or 4 is typical; some
    # patients are on a 6-month cadence. Drives the "Recall due" view.
    recall_interval_months = Column(Integer, default=4, nullable=False)

    # Preferred lab (single per patient — for ordering FSH/TSH/Estradiol).
    preferred_lab_name    = Column(String(200), nullable=True)
    preferred_lab_phone   = Column(String(40),  nullable=True)
    preferred_lab_address = Column(Text, nullable=True)

    # Preferred mammogram imaging facility (single per patient — same
    # imaging center used year over year for screening).
    preferred_mammo_facility_name    = Column(String(200), nullable=True)
    preferred_mammo_facility_phone   = Column(String(40),  nullable=True)
    preferred_mammo_facility_fax     = Column(String(40),  nullable=True)
    preferred_mammo_facility_address = Column(Text, nullable=True)

    notes           = Column(Text, nullable=True)
    created_at      = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_by      = Column(String(120), nullable=True)
    updated_at      = Column(DateTime, default=datetime.utcnow,
                              onupdate=datetime.utcnow, nullable=False)

    visits = relationship("PelletVisit",
                            back_populates="patient",
                            cascade="all, delete-orphan",
                            order_by="desc(PelletVisit.created_at)")
    mammos = relationship("PelletPatientMammo",
                            cascade="all, delete-orphan",
                            order_by="desc(PelletPatientMammo.mammo_date)")
    labs   = relationship("PelletPatientLab",
                            cascade="all, delete-orphan",
                            order_by="desc(PelletPatientLab.labs_date)")
    patient_notes = relationship("PelletPatientNote",
                                   cascade="all, delete-orphan",
                                   order_by="desc(PelletPatientNote.created_at)")


class PelletPatientMammo(Base):
    """Per-patient mammogram result history. The most recent verified row
    is also cached on PelletPatient.mammo_* for fast filtering."""
    __tablename__ = "pellet_patient_mammos"
    __table_args__ = (
        Index("ix_pellet_mammo_patient", "patient_id"),
    )

    id           = Column(GUID(), primary_key=True, default=new_uuid)
    patient_id   = Column(GUID(), ForeignKey("pellet_patients.id"),
                            nullable=False)
    mammo_date   = Column(Date, nullable=False)
    result       = Column(String(60), nullable=False)   # BI-RADS 1, 2, etc.
    facility_name    = Column(String(200), nullable=True)
    facility_phone   = Column(String(40),  nullable=True)
    facility_fax     = Column(String(40),  nullable=True)
    facility_address = Column(Text, nullable=True)
    notes        = Column(Text, nullable=True)
    verified_by  = Column(String(120), nullable=True)
    created_at   = Column(DateTime, default=datetime.utcnow, nullable=False)


class PelletPatientLab(Base):
    """Per-patient labs (FSH/TSH/Estradiol) history."""
    __tablename__ = "pellet_patient_labs"
    __table_args__ = (
        Index("ix_pellet_labs_patient", "patient_id"),
    )

    id           = Column(GUID(), primary_key=True, default=new_uuid)
    patient_id   = Column(GUID(), ForeignKey("pellet_patients.id"),
                            nullable=False)
    labs_date    = Column(Date, nullable=False)
    fsh          = Column(String(40), nullable=True)
    tsh          = Column(String(40), nullable=True)
    estradiol    = Column(String(40), nullable=True)
    notes        = Column(Text, nullable=True)
    verified_by  = Column(String(120), nullable=True)
    created_at   = Column(DateTime, default=datetime.utcnow, nullable=False)


class PelletPatientNote(Base):
    """Free-form patient-level note (user + timestamp stamped)."""
    __tablename__ = "pellet_patient_notes"
    __table_args__ = (
        Index("ix_pellet_note_patient", "patient_id"),
    )

    id           = Column(GUID(), primary_key=True, default=new_uuid)
    patient_id   = Column(GUID(), ForeignKey("pellet_patients.id"),
                            nullable=False)
    author       = Column(String(120), nullable=False)
    body         = Column(Text,        nullable=False)
    created_at   = Column(DateTime,    default=datetime.utcnow, nullable=False)


class PelletVisit(Base):
    """One row per planned insertion event (initial + each booster/repeat).
    Drives the milestone workflow."""
    __tablename__ = "pellet_visits"
    __table_args__ = (
        Index("ix_pellet_visit_patient", "patient_id"),
        Index("ix_pellet_visit_status", "status"),
    )

    id              = Column(GUID(), primary_key=True, default=new_uuid)
    patient_id      = Column(GUID(), ForeignKey("pellet_patients.id"),
                              nullable=False)
    visit_kind      = Column(String(20), default="initial", nullable=False)
    # initial | booster | repeat
    status          = Column(String(20), default="new", nullable=False)
    # new | in_progress | inserted | billed | cancelled | rescheduled

    # Pricing + payment
    price_amount    = Column(Numeric(8, 2), nullable=True)   # $500 / $400 typically
    payment_status  = Column(String(20), default="not_sent", nullable=False)
    # not_sent | sent | collected | refunded
    klara_sent_at   = Column(DateTime, nullable=True)
    klara_sent_by   = Column(String(120), nullable=True)
    payment_collected_at = Column(DateTime, nullable=True)
    payment_collected_by = Column(String(120), nullable=True)

    # Scheduling (ModMed-booked; we just track the date)
    scheduled_date  = Column(Date, nullable=True)
    location        = Column(String(40), nullable=True)   # white_plains | brandywine | arlington
    provider        = Column(String(120), nullable=True)
    # Deep link into ModMed (Patient Link column from the appt export)
    modmed_link     = Column(Text, nullable=True)
    # Source row id from a Smartsheet history backfill, if applicable.
    # Populated by scripts/pellet_smartsheet_history_import.py — used to
    # avoid double-importing on re-runs.
    smartsheet_row_id = Column(String(40), nullable=True, index=True)
    # The "Pellet Visit ID" column from the Smartsheet — the practice's
    # legacy visit identifier. Useful when cross-referencing the old
    # tracking spreadsheet.
    smartsheet_visit_id = Column(String(40), nullable=True)

    # Bag fill (Tattiana pre-fills based on dose card)
    bagged_at       = Column(DateTime, nullable=True)
    bagged_by       = Column(String(120), nullable=True)

    # Insertion event
    inserted_at     = Column(DateTime, nullable=True)
    inserted_by     = Column(String(120), nullable=True)
    outcome         = Column(String(40), nullable=True)
    # perfect | added | reduced | rescheduled | disposal | cancelled
    outcome_notes   = Column(Text, nullable=True)

    # Billing close-out
    claim_number    = Column(String(80), nullable=True)
    billed_at       = Column(DateTime, nullable=True)
    billed_by       = Column(String(120), nullable=True)

    created_at      = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_by      = Column(String(120), nullable=True)
    updated_at      = Column(DateTime, default=datetime.utcnow,
                              onupdate=datetime.utcnow, nullable=False)
    notes           = Column(Text, nullable=True)
    # Historical-import flag: True when this row was created to capture a
    # past visit from an old system. Historical visits never touch
    # PelletStock, never spawn milestones, never enter the daily-count
    # blocker query. The dose detail is free-form text only.
    is_historical   = Column(Boolean, default=False, nullable=False)

    patient = relationship("PelletPatient", back_populates="visits")
    doses = relationship("PelletVisitDose",
                          back_populates="visit",
                          cascade="all, delete-orphan",
                          order_by="PelletVisitDose.position")
    milestones = relationship("PelletVisitMilestone",
                                back_populates="visit",
                                cascade="all, delete-orphan",
                                order_by="PelletVisitMilestone.position")


class PelletVisitDose(Base):
    """One row per pellet on the visit's dose card. Linked to a specific
    lot once the bag is filled — that's how we trace chain-of-custody for
    Schedule III testosterone.

    status:
      planned   — on the dose card, not yet pulled
      pulled    — pulled from the box into the bag (decrements stock)
      added     — additional pellet pulled mid-procedure (Sch III audit)
      inserted  — actually placed in the patient (terminal)
      reduced   — bagged but not used, returned to stock
      returned  — patient rescheduled; full bag returns to stock
      disposed  — dropped/broken; goes to biohazard (PelletDisposal row)
    """
    __tablename__ = "pellet_visit_doses"
    __table_args__ = (
        Index("ix_pellet_visit_dose_visit", "visit_id"),
    )

    id            = Column(GUID(), primary_key=True, default=new_uuid)
    visit_id      = Column(GUID(), ForeignKey("pellet_visits.id"), nullable=False)
    dose_type_id  = Column(GUID(), ForeignKey("pellet_dose_types.id"), nullable=False)
    lot_id        = Column(GUID(), ForeignKey("pellet_lots.id"), nullable=True)
    quantity      = Column(Integer, default=1, nullable=False)
    position      = Column(Integer, default=0, nullable=False)
    status        = Column(String(20), default="planned", nullable=False)

    pulled_at     = Column(DateTime, nullable=True)
    pulled_by     = Column(String(120), nullable=True)
    resolved_at   = Column(DateTime, nullable=True)
    resolved_by   = Column(String(120), nullable=True)
    notes         = Column(Text, nullable=True)

    visit     = relationship("PelletVisit", back_populates="doses")
    dose_type = relationship("PelletDoseType")
    lot       = relationship("PelletLot")


class PelletVisitMilestone(Base):
    """Milestone catalog per visit. Catalog chosen by visit_kind +
    patient_type (see pellet_workflow.spawn_milestones)."""
    __tablename__ = "pellet_visit_milestones"
    __table_args__ = (
        Index("ix_pellet_visit_milestone_visit", "visit_id"),
    )

    id            = Column(GUID(), primary_key=True, default=new_uuid)
    visit_id      = Column(GUID(), ForeignKey("pellet_visits.id"), nullable=False)
    kind          = Column(String(60), nullable=False)
    title         = Column(String(200), nullable=False)
    position      = Column(Integer, nullable=False)
    status        = Column(String(20), default="pending", nullable=False)
    # pending | done | skipped | not_applicable
    completed_at  = Column(DateTime, nullable=True)
    completed_by  = Column(String(120), nullable=True)
    notes         = Column(Text, nullable=True)

    visit = relationship("PelletVisit", back_populates="milestones")


# ─── Curated mammogram facility catalog (within ~15 mi of Waldorf) ─

class PelletMammoFacility(Base):
    """A curated list of nearby mammogram imaging facilities. Used as
    the dropdown source on the Preferred mammogram facility editor.
    Seeded on boot; admins can add more via /pellets/mammo-facilities."""
    __tablename__ = "pellet_mammo_facilities"
    __table_args__ = (
        UniqueConstraint("name", name="uq_pellet_mammo_facility_name"),
    )

    id          = Column(GUID(), primary_key=True, default=new_uuid)
    name        = Column(String(200), nullable=False)
    phone       = Column(String(40),  nullable=True)
    fax         = Column(String(40),  nullable=True)
    address     = Column(Text, nullable=True)
    notes       = Column(Text, nullable=True)
    is_active   = Column(Boolean, default=True, nullable=False)
    sort_order  = Column(Integer, default=100, nullable=False)
    created_at  = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at  = Column(DateTime, default=datetime.utcnow,
                          onupdate=datetime.utcnow, nullable=False)


# ─── Filter presets ─────────────────────────────────────────────────

class PelletFilterPreset(Base):
    """Saved named filters for the pellet patient list (per user)."""
    __tablename__ = "pellet_filter_presets"
    __table_args__ = (
        Index("ix_pellet_filter_owner", "owner_email"),
        UniqueConstraint("owner_email", "name",
                         name="uq_pellet_filter_owner_name"),
    )

    id           = Column(GUID(), primary_key=True, default=new_uuid)
    owner_email  = Column(String(200), nullable=False)
    name         = Column(String(120), nullable=False)
    filters_json = Column(JSON, nullable=False, default=dict)
    is_default   = Column(Boolean, default=False, nullable=False)
    created_at   = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at   = Column(DateTime, default=datetime.utcnow,
                            onupdate=datetime.utcnow, nullable=False)


# ─── Editable manual (mirror LARC manual) ───────────────────────────

class PelletManualSection(Base):
    """A section of the Pellet operating manual. Editable in the UI;
    seeded on first boot from pellet_seed.py."""
    __tablename__ = "pellet_manual_sections"

    id            = Column(GUID(), primary_key=True, default=new_uuid)
    slug          = Column(String(80), nullable=False, unique=True)
    title         = Column(String(200), nullable=False)
    sort_order    = Column(Integer, default=100, nullable=False)
    body_md       = Column(Text, nullable=False)
    updated_by    = Column(String(120), nullable=True)
    updated_at    = Column(DateTime, default=datetime.utcnow,
                            onupdate=datetime.utcnow, nullable=False)
    created_at    = Column(DateTime, default=datetime.utcnow, nullable=False)
