"""Shared LARC fulfillment-path decision.

Single source of truth for whether a device request should be filled from
in-house stock, via a pharmacy enrollment form, or as an in-office
consumable. Used by both the surgery device-request sync and the manual
"Start LARC Process" intake so the two never drift.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.larc import LarcDevice, LarcDeviceType


def pick_source_flow(db: Session, dt: LarcDeviceType) -> str:
    """Decide the fulfillment path for a device type:
      - a matching device in stock          -> "in_stock"
      - else the type's default office flow  -> "office_procedure"
      - else                                 -> "pharmacy_order"
    """
    in_stock = (db.query(LarcDevice)
                  .filter(LarcDevice.device_type_id == dt.id,
                          LarcDevice.status == "unassigned")
                  .count())
    if in_stock > 0:
        return "in_stock"
    if dt.default_flow == "office_procedure":
        return "office_procedure"
    return "pharmacy_order"


def suggest_flow(db: Session, dt: LarcDeviceType) -> dict:
    """Advisory suggestion for the intake drawer.

    Returns the recommended flow plus the override set ``allowed_flows``:
      - always include the suggested flow
      - include "in_stock" when there is on-hand stock
      - include "pharmacy_order" only for non-consumable types
        (default_flow != office_procedure)
      - include "office_procedure" only when it is the suggestion
    Order is stable for display: in_stock, pharmacy_order, office_procedure.
    """
    in_stock_count = (db.query(LarcDevice)
                        .filter(LarcDevice.device_type_id == dt.id,
                                LarcDevice.status == "unassigned")
                        .count())
    suggested = pick_source_flow(db, dt)

    allowed: list[str] = []
    if in_stock_count > 0:
        allowed.append("in_stock")
    if dt.default_flow != "office_procedure":
        allowed.append("pharmacy_order")
    if suggested == "office_procedure":
        allowed.append("office_procedure")
    if suggested not in allowed:                      # safety net
        allowed.insert(0, suggested)

    order = ["in_stock", "pharmacy_order", "office_procedure"]
    allowed = [f for f in order if f in allowed]

    return {
        "suggested_flow": suggested,
        "in_stock_count": in_stock_count,
        "default_flow": dt.default_flow,
        "allowed_flows": allowed,
    }
