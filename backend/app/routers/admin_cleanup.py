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
from app.models.surgery import Surgery, BlockDay, SurgeryMilestone, SurgeryConsentEnvelope
from app.models.pellet import PelletPatient
from app.utils.dt import now_utc_naive
from app.services.audit_service import log_action


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


@router.post("/skip-retired-milestones")
def skip_retired_milestones(
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_super_admin()),
):
    """Sweep every SurgeryMilestone whose `kind` isn't in the current
    catalog and mark it `skipped` with an audit note. Used when a
    milestone step is retired from the workflow but old surgery rows
    still carry the now-orphaned milestone row — those keep sitting in
    `pending` forever and show up as Critical Alerts on the dashboard.

    Today's exact trigger: klara_scheduling was removed from
    HOSPITAL_MILESTONES + OFFICE_MILESTONES when the practice stopped
    pasting drafts into Klara. 10+ surgeries had pending klara_scheduling
    rows from before that change.
    """
    from app.services.surgery.smartsheet_seed import (
        HOSPITAL_MILESTONES, OFFICE_MILESTONES,
    )
    valid_kinds = {kind for kind, _, _ in HOSPITAL_MILESTONES + OFFICE_MILESTONES}

    rows = (db.query(SurgeryMilestone)
              .filter(SurgeryMilestone.kind.notin_(valid_kinds),
                      SurgeryMilestone.status.in_(("pending", "in_progress", "locked")))
              .all())
    actor = (current_user or {}).get("email") or "admin-cleanup"
    by_kind: dict[str, int] = {}
    note_suffix = f"[auto-skipped {_date.today()}] kind not in current catalog."
    for m in rows:
        by_kind[m.kind] = by_kind.get(m.kind, 0) + 1
        m.status = "skipped"
        m.completed_at = now_utc_naive()
        m.completed_by = actor
        m.notes = (m.notes + "\n" + note_suffix) if m.notes else note_suffix
    db.commit()
    return {
        "skipped": len(rows),
        "by_kind": by_kind,
        "actor": actor,
    }


@router.post("/fix-imported-confirmed-status")
def fix_imported_confirmed_status(
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_super_admin()),
):
    """One-shot backfill: bump any candidate_imported surgery that
    actually has a scheduled_date + scheduled_start_time but status is
    still 'incomplete' to 'confirmed'. Pre-fix bulk imports left a few
    rows in this state because book_slot only auto-confirms from 'new'
    or 'in_progress', not from 'incomplete'."""
    rows = (db.query(Surgery)
              .filter(Surgery.status == "incomplete",
                      Surgery.scheduled_date.isnot(None),
                      Surgery.scheduled_start_time.isnot(None))
              .all())
    out = []
    for s in rows:
        s.status = "confirmed"
        out.append({
            "chart_number":  s.chart_number,
            "patient_name":  s.patient_name,
            "scheduled":     f"{s.scheduled_date} {s.scheduled_start_time}",
        })
    db.commit()
    return {"fixed": len(out), "rows": out}


@router.post("/backfill-imported-procedure-classification")
def backfill_imported_procedure_classification(
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_super_admin()),
):
    """One-shot: for every Surgery with sub_flag='candidate_imported' that
    has selected_facility set but no procedure_classification / no
    eligible_facilities, derive both from the procedure label (Modmed
    Appointment Type) and stamp them so the SurgeryDetail date-picker
    modal can list available slots. Mirrors the post-fix importer.
    """
    from app.services.surgery.candidate_import import APPT_TYPE_MAP

    rows = (db.query(Surgery)
              .filter(Surgery.sub_flag == "candidate_imported")
              .all())
    out: list[dict] = []
    for s in rows:
        if s.procedure_classification and s.eligible_facilities:
            continue
        label = ""
        if s.procedures and isinstance(s.procedures, list) and s.procedures:
            label = (s.procedures[0] or {}).get("name", "") or ""
        info = APPT_TYPE_MAP.get(label.strip().lower())
        if not info:
            continue
        facility, procedure_kind, duration = info
        if not s.procedure_classification:
            s.procedure_classification = procedure_kind
        if not s.eligible_facilities:
            s.eligible_facilities = [facility]
        if not s.selected_facility:
            s.selected_facility = facility
        if not s.duration_minutes:
            s.duration_minutes = duration
        s.is_robotic = procedure_kind in ("robotic_180", "robotic_240")
        out.append({
            "chart_number": s.chart_number,
            "patient_name": s.patient_name,
            "set_procedure_classification": procedure_kind,
            "set_eligible_facilities":       [facility],
            "set_is_robotic":                s.is_robotic,
        })
    db.commit()
    return {"fixed": len(out), "rows": out}


class _UnbookIn(BaseModel):
    surgery_ids: list[str]


@router.post("/unbook-surgeries")
def unbook_surgeries(payload: _UnbookIn,
                      db: Session = Depends(get_db),
                      current_user: dict = Depends(requires_super_admin())):
    """Drop the SurgerySlot and clear scheduled_date / start_time on each
    surgery in the payload, returning it to a schedulable state. Used
    to roll back backfill_mode runs that blindly doublebooked another
    patient's slot."""
    from app.models.surgery import SurgerySlot
    out: list[dict] = []
    for sid in payload.surgery_ids:
        s = db.query(Surgery).filter(Surgery.id == sid).first()
        if not s:
            out.append({"surgery_id": sid, "result": "not_found"})
            continue
        slots = db.query(SurgerySlot).filter(SurgerySlot.surgery_id == sid).all()
        for sl in slots:
            db.delete(sl)
        prior_date = str(s.scheduled_date) if s.scheduled_date else None
        prior_time = (str(s.scheduled_start_time)[:5]
                       if s.scheduled_start_time else None)
        s.scheduled_date = None
        s.scheduled_start_time = None
        if s.status == "confirmed":
            s.status = "incomplete"
        out.append({
            "surgery_id":   sid,
            "patient_name": s.patient_name,
            "rolled_back_from": f"{prior_date} {prior_time}".strip(),
            "slots_deleted":    len(slots),
        })
    db.commit()
    return {"unbooked": sum(1 for o in out if o.get("slots_deleted", 0) > 0),
            "rows": out}


@router.post("/delete-orphan-slots")
def delete_orphan_slots(
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_super_admin()),
):
    """Hard-delete any SurgerySlot with surgery_id IS NULL.

    Background: SurgerySlot.surgery_id has ON DELETE SET NULL, so
    deleting a Surgery row (raw SQL cleanup, legacy admin paths, etc.)
    leaves the slot row in place with a NULL surgery_id — but the slot
    keeps consuming BlockDay capacity in can_fit(). Result: a day
    appears full but nobody can see what's on it. Caught during the
    06/12 import audit (06/22 MedStar had 2 orphan robotic slots that
    blocked Penn / Harris Wilson / Johnson).
    """
    from app.models.surgery import SurgerySlot
    rows = (db.query(SurgerySlot)
              .filter(SurgerySlot.surgery_id.is_(None))
              .all())
    out = [
        {
            "slot_id":          str(r.id),
            "block_day_id":     str(r.block_day_id),
            "start_time":       str(r.start_time)[:5],
            "duration_minutes": r.duration_minutes,
            "procedure_kind":   r.procedure_kind,
        }
        for r in rows
    ]
    for r in rows:
        db.delete(r)
    db.commit()
    return {"deleted": len(out), "rows": out}


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


@router.get("/docusign-open-count")
def docusign_open_count(db: Session = Depends(get_db),
                        current_user: dict = Depends(requires_super_admin())):
    """Post-deploy sanity check: count legacy DocuSign consent envelopes that
    are still open (not in a terminal state). Should be 0 — confirms no legacy
    envelopes were stranded by the DocuSign rip-out."""
    _TERMINAL = ("signed", "completed", "declined", "voided")
    count = (db.query(func.count(SurgeryConsentEnvelope.id))
               .filter(SurgeryConsentEnvelope.docusign_envelope_id.isnot(None))
               .filter(~SurgeryConsentEnvelope.status.in_(_TERMINAL))
               .scalar())
    return {"open_docusign_envelopes": int(count or 0)}


@router.get("/billing-doc-duplicate-hashes")
def billing_doc_duplicate_hashes(db: Session = Depends(get_db),
                                 current_user: dict = Depends(requires_super_admin())):
    """Read-only diagnostic: find LIVE (non-deleted) billing_documents that
    share a content_hash. These are the genuine duplicates that block the
    partial unique index ix_billing_documents_content_hash_unique even after
    it was scoped to live rows (they come from `force=true` 'upload anyway').
    Returns the duplicate groups so an operator can decide what to merge/delete.
    Nothing is mutated."""
    from app.models.billing_document import BillingDocument
    dup_hashes = (db.query(BillingDocument.content_hash,
                           func.count(BillingDocument.id).label("n"))
                    .filter(BillingDocument.content_hash.isnot(None))
                    .filter(BillingDocument.deleted_at.is_(None))
                    .group_by(BillingDocument.content_hash)
                    .having(func.count(BillingDocument.id) > 1)
                    .all())
    groups = []
    for content_hash, n in dup_hashes:
        rows = (db.query(BillingDocument)
                  .filter(BillingDocument.content_hash == content_hash)
                  .filter(BillingDocument.deleted_at.is_(None))
                  .order_by(BillingDocument.uploaded_at.asc())
                  .all())
        groups.append({
            "content_hash": content_hash,
            "count": int(n),
            "documents": [{
                "id": str(d.id),
                "original_filename": d.original_filename,
                "classification": d.classification,
                "status": d.status,
                "uploaded_by": d.uploaded_by,
                "uploaded_at": d.uploaded_at.isoformat() if d.uploaded_at else None,
            } for d in rows],
        })
    return {
        "live_duplicate_hash_groups": len(groups),
        "total_redundant_docs": sum(g["count"] - 1 for g in groups),
        "groups": groups,
    }


# Status progression — used to pick the most-worked row as the keeper.
_BD_STATUS_RANK = {"worked": 3, "in_progress": 2, "new": 1}


def _bd_canonical_keeper(rows):
    """Pick the row to KEEP from a same-content_hash group. Rule (deterministic):
    most-progressed status, then most access-log/notes history, then earliest
    uploaded, then lowest id — so the choice is stable and favors the row a
    human has actually worked."""
    def sort_key(d):
        return (
            -_BD_STATUS_RANK.get(d.status, 0),
            -(len(d.access_log or []) + len(d.notes_rel or [])),
            d.uploaded_at or datetime.max,
            str(d.id),
        )
    return sorted(rows, key=sort_key)[0]


@router.post("/billing-doc-dedup")
def billing_doc_dedup(dry_run: bool = Query(True),
                      db: Session = Depends(get_db),
                      current_user: dict = Depends(requires_super_admin())):
    """Soft-delete redundant LIVE duplicate billing_documents so the partial
    unique index on content_hash can build. For each content_hash with >1 live
    row, KEEP one canonical row (see _bd_canonical_keeper) and soft-delete the
    rest. DRY RUN BY DEFAULT — pass ?dry_run=false to actually delete.
    Nothing is hard-deleted; soft-deleted rows remain recoverable."""
    from app.models.billing_document import BillingDocument
    actor = current_user.get("email") or "system"
    dup_hashes = (db.query(BillingDocument.content_hash)
                    .filter(BillingDocument.content_hash.isnot(None))
                    .filter(BillingDocument.deleted_at.is_(None))
                    .group_by(BillingDocument.content_hash)
                    .having(func.count(BillingDocument.id) > 1)
                    .all())
    planned = []
    deleted = 0
    for (content_hash,) in dup_hashes:
        rows = (db.query(BillingDocument)
                  .filter(BillingDocument.content_hash == content_hash)
                  .filter(BillingDocument.deleted_at.is_(None))
                  .all())
        keeper = _bd_canonical_keeper(rows)
        redundant = [d for d in rows if d.id != keeper.id]
        planned.append({
            "content_hash": content_hash,
            "keep": {"id": str(keeper.id), "filename": keeper.original_filename,
                     "status": keeper.status,
                     "uploaded_at": keeper.uploaded_at.isoformat() if keeper.uploaded_at else None},
            "soft_delete": [{"id": str(d.id), "filename": d.original_filename,
                             "status": d.status,
                             "uploaded_at": d.uploaded_at.isoformat() if d.uploaded_at else None}
                            for d in redundant],
        })
        if not dry_run:
            for d in redundant:
                d.soft_delete(by_email=f"dedup:{actor}")
                deleted += 1
    if not dry_run and deleted:
        db.commit()
        log_action(db, "DELETE", "billing_documents",
                   resource_id=None, user_name=actor,
                   description=f"billing-doc dedup: soft-deleted {deleted} redundant duplicate(s)")
    return {
        "dry_run": dry_run,
        "groups": len(planned),
        "would_soft_delete" if dry_run else "soft_deleted":
            sum(len(p["soft_delete"]) for p in planned),
        "plan": planned,
    }
