"""Merge a duplicate pellet lot into a canonical one.

Used by verify_manifest (live: a freshly-verified lot merges into the
pre-existing canonical for its number+strength+office) and by the one-time
dedup migration. Re-points all 6 FK references, sums stock per location,
carries forward fields, deletes src, and writes a `lot_merged` audit.

Stock increments use an atomic SQL UPDATE (not Python +=) so two concurrent
verifies of the same lot can't lose an update. The caller commits.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.models.pellet import (
    PelletLot, PelletStock, PelletVisitDose, PelletAuditEvent,
    PelletTransfer, PelletDisposal, PelletCountLine,
)

# Placeholder expiration the Smartsheet import uses for unknown-exp lots.
UNKNOWN_EXP = date(2099, 12, 31)

_FK_MODELS = (PelletVisitDose, PelletAuditEvent, PelletTransfer,
              PelletDisposal, PelletCountLine)


def merge_lot(db: Session, *, src: PelletLot, dst: PelletLot,
              actor: str = "system:lot-dedup") -> dict:
    if str(src.id) == str(dst.id):
        return {"merged": False, "reason": "same lot"}

    moved = 0
    for s in db.query(PelletStock).filter(PelletStock.lot_id == src.id).all():
        dst_row = (db.query(PelletStock)
                     .filter(PelletStock.lot_id == dst.id,
                             PelletStock.location == s.location).first())
        if dst_row is None:
            dst_row = PelletStock(lot_id=dst.id, location=s.location, doses_on_hand=0)
            db.add(dst_row); db.flush()
        db.query(PelletStock).filter(PelletStock.id == dst_row.id).update(
            {"doses_on_hand": PelletStock.doses_on_hand + s.doses_on_hand},
            synchronize_session=False)
        moved += s.doses_on_hand
        db.delete(s)
    db.flush()

    for model in _FK_MODELS:
        db.query(model).filter(model.lot_id == src.id).update(
            {"lot_id": dst.id}, synchronize_session=False)
    db.flush()

    if dst.expiration_date == UNKNOWN_EXP and src.expiration_date != UNKNOWN_EXP:
        dst.expiration_date = src.expiration_date
    dst.doses_originally_received = ((dst.doses_originally_received or 0)
                                     + (src.doses_originally_received or 0))
    if dst.receipt_id is None and src.receipt_id is not None:
        dst.receipt_id = src.receipt_id
    if dst.unit_cost is None and src.unit_cost is not None:
        dst.unit_cost = src.unit_cost
    if dst.cost_per_dose is None and src.cost_per_dose is not None:
        dst.cost_per_dose = src.cost_per_dose
    if dst.location is None and src.location is not None:
        dst.location = src.location

    src_id, src_num, src_rcpt = str(src.id), src.qualgen_lot_number, src.receipt_id
    db.add(PelletAuditEvent(
        actor=actor, action="lot_merged",
        lot_id=dst.id, dose_type_id=dst.dose_type_id, location=dst.location,
        summary=(f"Merged duplicate lot {src_num} ({src_id[:8]}) into "
                 f"canonical {dst.qualgen_lot_number} ({str(dst.id)[:8]})"),
        detail={"canonical_lot_id": str(dst.id), "merged_lot_id": src_id,
                "merged_stock_doses": moved,
                "merged_receipt_id": str(src_rcpt) if src_rcpt else None}))
    db.flush()
    db.expire(src)
    db.delete(src)
    db.flush()
    return {"merged": True, "moved_doses": moved, "src": src_id, "dst": str(dst.id)}
