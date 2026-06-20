"""Auto-allocate an in-stock device once benefits + payment are satisfied."""
from __future__ import annotations
from sqlalchemy.orm import Session
from sqlalchemy import update
from app.models.larc import LarcAssignment, LarcDevice
from app.services.larc.workflow import log_audit


def try_auto_allocate(db: Session, a: LarcAssignment) -> dict:
    if a.source_flow != "in_stock" or a.device_id:
        return {"allocated": False, "reason": "not_applicable"}
    if not (a.benefits_verified_at and a.patient_paid_at):
        return {"allocated": False, "reason": "gates_unmet"}

    dev = (db.query(LarcDevice)
             .filter(LarcDevice.device_type_id == a.device_type_id,
                     LarcDevice.status == "unassigned")
             .order_by(LarcDevice.expiration_date.asc().nullslast())
             .first())
    if not dev:
        a.needs_allocation_no_stock = True
        db.commit()
        return {"allocated": False, "reason": "no_stock"}

    # Atomic claim — mirrors allocate_device()'s conditional UPDATE so two
    # concurrent allocations can't both bind the same implantable device.
    claimed = db.execute(
        update(LarcDevice)
        .where(LarcDevice.id == dev.id, LarcDevice.status == "unassigned")
        .values(status="assigned")).rowcount
    if not claimed:
        return {"allocated": False, "reason": "race_lost"}
    db.refresh(dev)

    a.device_id = dev.id
    a.needs_allocation_no_stock = False
    log_audit(db, actor="system:auto_allocate", action="device_allocated",
              device=dev, assignment=a,
              summary=f"Auto-allocated device #{dev.our_id} on payment",
              detail={"device_our_id": dev.our_id})
    db.commit()
    return {"allocated": True, "device_id": str(dev.id)}
