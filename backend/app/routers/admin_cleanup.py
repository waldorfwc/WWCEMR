"""One-off admin cleanup endpoints.

Currently: identifying and removing test-patient rows accumulated during
development / audit runs. Super-admin only.

Test-pattern detection:
- last_name starts with "zz" (alphabetical sentinel used by QA)
- first OR last name equals one of: audit, run, test, demo, sample

The endpoint is GET to audit (no writes), DELETE to actually clean up
(requires `confirm=true` query param). Soft-deletes LarcAssignment rows,
nulls out purchasing-patient fields on LarcDevice rows (the device itself
may be real inventory even when the original purchasing patient was a
test), and hard-deletes LarcOwedPatient rows.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from datetime import date as _date, datetime, time as _time

from app.database import get_db
from app.permissions.dependencies import requires_super_admin
from app.models.larc import LarcAssignment, LarcDevice, LarcOwedPatient
from app.models.surgery import Surgery, BlockDay
from app.models.pellet import PelletPatient


router = APIRouter(prefix="/admin/cleanup", tags=["admin-cleanup"])


# Sentinel prefix QA used to push test rows to the end of alphabetical lists.
_TEST_NAME_PREFIXES = ("zz",)
# Names commonly typed during development / audit runs.
_TEST_EXACT = ("audit", "run", "test", "demo", "sample", "dummy", "fake",
                "testpatient", "walkthru", "walkthru2", "uitest", "testcard",
                "verify", "reserve", "reception")
# Charts in this prefix range are reserved for QA/test (never minted for
# real patients).
_TEST_CHART_PREFIXES = ("zzz-", "zzz_", "wwc-recv-test")


def _is_test_name(col):
    """Build an OR expression: column matches any test name pattern (case-insensitive)."""
    lower = func.lower(func.coalesce(col, ""))
    conds = [lower.like(f"{pfx}%") for pfx in _TEST_NAME_PREFIXES]
    conds += [lower == name for name in _TEST_EXACT]
    return or_(*conds)


def _is_test_chart(col):
    """SQL expression: chart_number starts with one of the QA-reserved prefixes."""
    lower = func.lower(func.coalesce(col, ""))
    return or_(*[lower.like(f"{pfx}%") for pfx in _TEST_CHART_PREFIXES])


def _is_test(col):
    """Back-compat alias — name-only test match. Use _is_test_name / _is_test_chart."""
    return _is_test_name(col)


def _assignment_match(a: LarcAssignment):
    return or_(
        _is_test_name(a.patient_first_name),
        _is_test_name(a.patient_last_name),
        _is_test_name(a.patient_name),
        _is_test_chart(a.chart_number),
    )


def _scan(db: Session) -> dict[str, Any]:
    """Returns counts + sample rows from each affected table."""
    assignments = (db.query(LarcAssignment)
                     .filter(LarcAssignment.deleted_at.is_(None))
                     .filter(_assignment_match(LarcAssignment))
                     .order_by(LarcAssignment.created_at.desc())
                     .all())
    devices = (db.query(LarcDevice)
                 .filter(or_(
                     _is_test_name(LarcDevice.purchasing_patient_name),
                     _is_test_chart(LarcDevice.purchasing_patient_chart),
                 ))
                 .order_by(LarcDevice.created_at.desc())
                 .all())
    owed = (db.query(LarcOwedPatient)
              .filter(or_(
                  _is_test_name(LarcOwedPatient.patient_name),
                  _is_test_chart(LarcOwedPatient.chart_number),
              ))
              .all())

    # Surgery + PelletPatient — scan only, no soft-delete column to use.
    # We surface scope and let the operator decide whether to act manually.
    surgeries = (db.query(Surgery)
                   .filter(or_(
                       _is_test_name(Surgery.patient_name),
                       _is_test_chart(Surgery.chart_number),
                   ))
                   .order_by(Surgery.created_at.desc())
                   .all())
    pellet_patients = (db.query(PelletPatient)
                         .filter(or_(
                             _is_test_name(PelletPatient.patient_name),
                             _is_test_chart(PelletPatient.chart_number),
                         ))
                         .all())

    return {
        "assignments": {
            "count": len(assignments),
            "samples": [
                {
                    "id": str(a.id),
                    "chart_number": a.chart_number,
                    "patient_name": a.patient_name,
                    "first_name": a.patient_first_name,
                    "last_name":  a.patient_last_name,
                    "status":     a.status,
                    "created_at": a.created_at.isoformat() if a.created_at else None,
                }
                for a in assignments[:50]
            ],
        },
        "devices": {
            "count": len(devices),
            "samples": [
                {
                    "id": str(d.id),
                    "our_id": d.our_id,
                    "purchasing_patient_name": d.purchasing_patient_name,
                    "purchasing_patient_chart": d.purchasing_patient_chart,
                    "status": d.status,
                }
                for d in devices[:50]
            ],
        },
        "owed": {
            "count": len(owed),
            "samples": [
                {
                    "id": str(o.id),
                    "chart_number": o.chart_number,
                    "patient_name": o.patient_name,
                }
                for o in owed[:50]
            ],
        },
        "surgeries": {
            "count": len(surgeries),
            "deletable_via_endpoint": False,  # no SoftDeleteMixin; manual decision required
            "samples": [
                {
                    "id": str(s.id),
                    "chart_number": s.chart_number,
                    "patient_name": s.patient_name,
                    "status": s.status,
                    "created_at": s.created_at.isoformat() if s.created_at else None,
                }
                for s in surgeries[:50]
            ],
        },
        "pellet_patients": {
            "count": len(pellet_patients),
            "deletable_via_endpoint": False,
            "samples": [
                {
                    "id": str(p.id),
                    "chart_number": p.chart_number,
                    "patient_name": p.patient_name,
                }
                for p in pellet_patients[:50]
            ],
        },
    }


@router.get("/test-patients")
def audit_test_patients(db: Session = Depends(get_db),
                         current_user: dict = Depends(requires_super_admin())):
    """Read-only — show what would be cleaned. Does not modify anything."""
    return _scan(db)


@router.delete("/test-patients")
def delete_test_patients(
    confirm: bool = Query(False, description="Must be true to actually delete"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_super_admin()),
):
    """Clean up identified test-patient rows.

    Requires ?confirm=true. Soft-deletes assignments, nulls out the
    purchasing-patient fields on devices (keeps the physical device row
    since it may be real inventory), and hard-deletes owed-list rows.
    Returns the action counts.
    """
    if not confirm:
        raise HTTPException(status_code=400,
                            detail="missing ?confirm=true — call GET first to audit")

    actor = (current_user or {}).get("email") or "admin-cleanup"

    # Soft-delete assignments via the SoftDeleteMixin helper
    assignments = (db.query(LarcAssignment)
                     .filter(LarcAssignment.deleted_at.is_(None))
                     .filter(_assignment_match(LarcAssignment))
                     .all())
    for a in assignments:
        a.soft_delete(by_email=actor)

    # Null out the purchasing-patient association on devices that point at
    # a test patient. The device itself stays — it may be sittable inventory.
    devices = (db.query(LarcDevice)
                 .filter(or_(
                     _is_test_name(LarcDevice.purchasing_patient_name),
                     _is_test_chart(LarcDevice.purchasing_patient_chart),
                 ))
                 .all())
    for d in devices:
        d.purchasing_patient_name = None
        d.purchasing_patient_chart = None

    # Hard-delete owed rows (they're derived; no audit value)
    owed = (db.query(LarcOwedPatient)
              .filter(or_(
                  _is_test_name(LarcOwedPatient.patient_name),
                  _is_test_chart(LarcOwedPatient.chart_number),
              ))
              .all())
    owed_ids = [str(o.id) for o in owed]
    for o in owed:
        db.delete(o)

    db.commit()

    return {
        "actor": actor,
        "assignments_soft_deleted": len(assignments),
        "devices_cleared_purchasing_patient": len(devices),
        "owed_hard_deleted": len(owed),
        "owed_ids": owed_ids[:200],
    }


# ─── Silent surgery scheduling ──────────────────────────────────────
# One-off admin path that books slots without firing the patient
# confirmation email/SMS or syncing to Google Calendar. Used when the
# scheduler has already coordinated dates with patients directly
# (e.g. phone call) and just needs the system to reflect what was
# agreed without a duplicate notification going out.

from pydantic import BaseModel


class _SilentScheduleItem(BaseModel):
    chart_number: str
    start_time:   str        # "HH:MM" 24-hour
    duration_minutes: int = 180


class _SilentScheduleIn(BaseModel):
    block_date:     str      # YYYY-MM-DD
    facility:       str      # "medstar" / "crmc" / "office" etc.
    procedure_kind: str = "robotic_180"
    items:          list[_SilentScheduleItem]
    dry_run:        bool = False


def _hhmm(s: str) -> _time:
    h, m = s.split(":")[:2]
    return _time(int(h), int(m))


@router.post("/silent-schedule")
def silent_schedule(payload: _SilentScheduleIn,
                     db: Session = Depends(get_db),
                     current_user: dict = Depends(requires_super_admin())):
    """Book one or more surgeries onto an existing BlockDay without firing
    the patient confirmation email/SMS or syncing to Google Calendar.
    Uses the same book_slot() the UI does so capacity / overlap / block-
    window guards still run.

    Set dry_run=true to validate input + lookups without writing.
    """
    block_date = _date.fromisoformat(payload.block_date)
    bd = (db.query(BlockDay)
            .filter(BlockDay.block_date == block_date,
                    BlockDay.facility == payload.facility)
            .first())
    if not bd:
        raise HTTPException(status_code=404,
                            detail=(f"No BlockDay for {payload.block_date} at "
                                    f"{payload.facility}. Create one first or check the facility slug."))

    results: list[dict] = []
    actor = (current_user or {}).get("email") or "admin-cleanup"

    for it in payload.items:
        entry: dict = {"chart_number": it.chart_number,
                        "start_time":   it.start_time,
                        "duration_minutes": it.duration_minutes}
        surgery = (db.query(Surgery)
                     .filter(Surgery.chart_number == it.chart_number)
                     .order_by(Surgery.created_at.desc())
                     .first())
        if not surgery:
            entry["status"] = "not_found"
            results.append(entry); continue
        entry["surgery_id"]   = str(surgery.id)
        entry["patient_name"] = surgery.patient_name
        entry["prior_scheduled_date"] = (str(surgery.scheduled_date)
                                          if surgery.scheduled_date else None)
        entry["prior_scheduled_start_time"] = (str(surgery.scheduled_start_time)
                                                if surgery.scheduled_start_time else None)

        if payload.dry_run:
            entry["status"] = "would_schedule"
            results.append(entry); continue

        try:
            from app.services.surgery.block_schedule import (
                book_slot, CapacityViolation,
            )
            slot = book_slot(
                db, block_day_id=str(bd.id), surgery_id=str(surgery.id),
                start_time=_hhmm(it.start_time),
                duration_minutes=it.duration_minutes,
                procedure_kind=payload.procedure_kind,
            )
        except CapacityViolation as exc:
            entry["status"] = "capacity_violation"
            entry["error"]  = str(exc); results.append(entry); continue
        except Exception as exc:
            entry["status"] = "error"
            entry["error"]  = str(exc); results.append(entry); continue

        # Audit note on the surgery
        from app.models.surgery import SurgeryNote
        db.add(SurgeryNote(
            surgery_id=surgery.id,
            created_by=actor,
            content=(f"Silent-scheduled {bd.block_date} {it.start_time} "
                     f"({it.duration_minutes} min) at {bd.facility}. "
                     f"Confirmation email/SMS suppressed — coordinator "
                     f"confirmed directly."),
        ))
        db.commit()
        entry["status"]   = "scheduled"
        entry["slot_id"]  = str(slot.id)
        results.append(entry)

    return {
        "block_day_id":  str(bd.id),
        "block_date":    str(bd.block_date),
        "facility":      bd.facility,
        "actor":         actor,
        "dry_run":       payload.dry_run,
        "results":       results,
    }
