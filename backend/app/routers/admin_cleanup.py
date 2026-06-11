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

from app.database import get_db
from app.permissions.dependencies import requires_super_admin
from app.models.larc import LarcAssignment, LarcDevice, LarcOwedPatient


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
