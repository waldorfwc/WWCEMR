"""Surgery → Device Tracking bridge.

When a surgery that requires a LARC / office-procedure device is
*scheduled*, this service auto-creates a request in Device Tracking — a
`LarcAssignment` linked back to the surgery — recording who requested it
(the provider), what device, and when.

The source_flow is auto-picked from inventory:
  - a matching device in stock          → "in_stock"  (allocate-existing)
  - else the type's default office flow  → "office_procedure"
  - else                                 → "pharmacy_order"

The coordinator still confirms the final allocate / send in Device
Tracking; this just lands the request in the right flow.

`sync_surgery_device_requests` is **soft-fail and idempotent** — it never
raises into the scheduling flow, and re-running it for the same surgery
won't create duplicates.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.models.larc import LarcAssignment, LarcDeviceType
from app.services.larc.source_flow import pick_source_flow
from app.services.larc.workflow import log_audit

log = logging.getLogger(__name__)


def sync_surgery_device_requests(db: Session, surgery,
                                 actor_email: Optional[str] = None) -> dict:
    """Create linked LARC device requests for a scheduled surgery.

    Returns {"created": [ids], "skipped_existing": n, "unmatched": [names]}.
    Soft-fails: any per-device error is logged and skipped rather than
    raised, so a bridge problem can never block the scheduling flow.
    """
    created: list[str] = []
    skipped_existing = 0
    unmatched: list[str] = []

    actor = actor_email or "system:surgery-schedule"

    for raw_name in (surgery.device_types or []):
        name = (raw_name or "").strip()
        if not name or name.lower() == "none":
            continue
        try:
            dt = (db.query(LarcDeviceType)
                    .filter(LarcDeviceType.is_active.is_(True),
                            LarcDeviceType.name.ilike(name))
                    .first())
            if dt is None:
                log.info("surgery device request: no active device type for %r "
                         "(surgery %s)", name, surgery.id)
                unmatched.append(name)
                continue

            # Idempotency: an active assignment already linked to this
            # surgery for this device type means the request exists.
            existing = (db.query(LarcAssignment)
                          .filter(LarcAssignment.is_active.is_(True),
                                  LarcAssignment.linked_surgery_id == surgery.id,
                                  LarcAssignment.device_type_id == dt.id)
                          .first())
            if existing is not None:
                skipped_existing += 1
                continue

            source_flow = pick_source_flow(db, dt)

            a = LarcAssignment(
                chart_number=surgery.chart_number,
                patient_name=surgery.patient_name,
                patient_first_name=surgery.first_name,
                patient_last_name=surgery.last_name,
                patient_dob=surgery.dob,
                primary_insurance=surgery.primary_insurance,
                device_type_id=dt.id,
                source_flow=source_flow,
                linked_surgery_id=surgery.id,
                requested_by_provider=surgery.surgeon_primary,
                created_by=actor,
                status="new",
                is_active=True,
                notes=(f"Auto-created from scheduled surgery "
                       f"{surgery.surgery_number or surgery.id}."),
            )
            db.add(a)
            db.flush()

            log_audit(
                db,
                actor=actor,
                action="created_from_surgery",
                assignment=a,
                summary=(f"Device request for {dt.name} auto-created from "
                         f"scheduled surgery {surgery.surgery_number or surgery.id} "
                         f"(provider {surgery.surgeon_primary or 'unknown'})"),
                detail={
                    "source_flow": source_flow,
                    "linked_surgery_id": str(surgery.id),
                    "device_type": dt.name,
                    "requested_by_provider": surgery.surgeon_primary,
                },
            )
            created.append(str(a.id))
        except Exception:  # soft-fail per device
            log.exception("surgery device request failed for %r (surgery %s)",
                          name, surgery.id)
            continue

    if created:
        db.commit()

    return {
        "created": created,
        "skipped_existing": skipped_existing,
        "unmatched": unmatched,
    }
