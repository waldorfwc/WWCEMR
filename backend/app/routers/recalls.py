"""Recalls API — patient recall queue, call logging, suppressions, dashboard.

All endpoints require recall:work (read/write) or recall:manage (admin ops).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy import desc, func, or_, and_
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models.patient_directory import PatientDirectory
from app.models.recall import RecallEntry, RecallSuppression, RecallCallLog, WWEVisit
from app.models.user import User
from app.routers.auth import get_current_user, require_permission
from app.services.ringcentral_client import client as rc_client


router = APIRouter(prefix="/recalls", tags=["recalls"])


# ─── pydantic ────────────────────────────────────────────────────────

class CallLogPayload(BaseModel):
    outcome: Optional[str] = None
    notes: Optional[str] = None
    duration_seconds: Optional[int] = None


class CallAttemptedPayload(BaseModel):
    """Logged when a user clicks the phone number to dial. No outcome yet —
    that's set later via /outcome."""
    pass


class OutcomePayload(BaseModel):
    outcome: str
    notes: Optional[str] = None


# Outcomes that move the patient to permanent suppression
PERMANENT_OUTCOMES = {
    "Declined recall":    "declined",
    "Do not call":        "do_not_call",
    "Patient deceased":   "deceased",
    "Left practice":      "left_practice",
}

# Outcomes that put a cooldown on the entry but keep them on the active list
COOLDOWN_OUTCOMES = {
    "Left voicemail":     timedelta(days=3),
    "No answer":          timedelta(days=1),
    "Pending callback":   timedelta(days=2),
}

# Outcome that flips entry to "completed" (they scheduled — no more calls)
COMPLETED_OUTCOMES = {"Scheduled"}

ALL_OUTCOMES = (
    list(PERMANENT_OUTCOMES.keys())
    + list(COOLDOWN_OUTCOMES.keys())
    + list(COMPLETED_OUTCOMES)
    + ["Wrong number"]   # neutral — leave on list
)


# Soft-claim TTL — how long an opened recall stays locked to one caller
# without an outcome before any other caller can pick it up.
CLAIM_TTL = timedelta(minutes=5)


def _ensure_claim_available(e: RecallEntry, my_email: str) -> None:
    """Raise 409 if another user owns an unexpired claim on this recall."""
    if e.claimed_by and e.claimed_by != my_email and e.claimed_until \
            and e.claimed_until > datetime.utcnow():
        mins_left = max(1, int((e.claimed_until - datetime.utcnow()).total_seconds() // 60))
        raise HTTPException(
            status_code=409,
            detail=(f"{e.claimed_by} is currently working this recall "
                    f"(unlocks in ~{mins_left} min). Please pick a different patient."),
        )


def _take_claim(e: RecallEntry, my_email: str) -> None:
    e.claimed_by = my_email
    e.claimed_until = datetime.utcnow() + CLAIM_TTL


def _release_claim(e: RecallEntry, my_email: Optional[str]) -> None:
    """Clear the claim — only if it was ours, OR if it has expired."""
    if e.claimed_by is None:
        return
    if e.claimed_until and e.claimed_until <= datetime.utcnow():
        e.claimed_by = None
        e.claimed_until = None
        return
    if my_email and e.claimed_by == my_email:
        e.claimed_by = None
        e.claimed_until = None


def _entry_to_dict(e: RecallEntry, dob: Optional[date] = None) -> dict:
    # Treat an expired claim as no claim
    now = datetime.utcnow()
    has_active_claim = (e.claimed_until is not None and e.claimed_until > now)
    return {
        "id": str(e.id),
        "chart_number": e.chart_number,
        "patient_name": e.patient_name,
        "dob": str(e.dob or dob or "") or None,
        "primary_phone": e.primary_phone,
        "cell_phone": e.cell_phone,
        "email": e.email,
        "primary_insurance": e.primary_insurance,
        "primary_plan": e.primary_plan,
        "recall_type": e.recall_type,
        "priority": e.priority,
        "last_visit": str(e.last_visit) if e.last_visit else None,
        "recall_due": str(e.recall_due) if e.recall_due else None,
        "status": e.status,
        "attempts": e.attempts,
        "last_outcome": e.last_outcome,
        "last_attempt_at": str(e.last_attempt_at) if e.last_attempt_at else None,
        "last_worked_by": e.last_worked_by,
        "latest_comment": e.latest_comment,
        "cooldown_until": str(e.cooldown_until) if e.cooldown_until else None,
        "claimed_by": e.claimed_by if has_active_claim else None,
        "claimed_until": str(e.claimed_until) if has_active_claim else None,
    }


# ─── List + filter ───────────────────────────────────────────────────

@router.get("")
def list_recalls(
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_permission("recall:work")),
    search: Optional[str] = None,
    recall_type: Optional[str] = None,
    status: Optional[str] = "active",   # default to active queue
    sort: str = "recently_due_desc",
    page: int = 1,
    per_page: int = 50,
    include_cooldown: bool = False,
):
    """List recall entries. Filters: status, recall_type, search (name/chart#).
    Default is active queue, hiding entries currently on cooldown."""
    q = db.query(RecallEntry)
    if status and status != "all":
        q = q.filter(RecallEntry.status == status)
    if recall_type:
        q = q.filter(RecallEntry.recall_type == recall_type)
    if search:
        like = f"%{search}%"
        q = q.filter(or_(
            RecallEntry.patient_name.ilike(like),
            RecallEntry.chart_number.ilike(like),
            RecallEntry.cell_phone.ilike(like),
            RecallEntry.primary_phone.ilike(like),
        ))
    if not include_cooldown:
        now = datetime.utcnow()
        q = q.filter(
            or_(RecallEntry.cooldown_until.is_(None),
                RecallEntry.cooldown_until <= now)
        )

    total = q.count()

    if sort == "recently_due_desc":
        # Most recent last_visit first → patient who *just* tipped due is at
        # the top, conversion-likeliest. Falls through to recall_due tie-break.
        q = q.order_by(RecallEntry.last_visit.desc().nullslast(),
                       RecallEntry.recall_due.desc().nullslast())
    elif sort == "overdue_desc":
        # Oldest last_visit first (most overdue)
        q = q.order_by(RecallEntry.last_visit.asc().nullslast())
    elif sort == "name":
        q = q.order_by(RecallEntry.patient_name.asc())
    elif sort == "attempts_asc":
        q = q.order_by(RecallEntry.attempts.asc(), RecallEntry.last_visit.asc())
    elif sort == "recall_due":
        q = q.order_by(RecallEntry.recall_due.asc().nullslast())
    else:
        q = q.order_by(RecallEntry.patient_name.asc())

    rows = q.offset((page - 1) * per_page).limit(per_page).all()

    # Bulk-fetch DOB from patient_directory for any rows missing it on the entry
    chart_nums = [e.chart_number for e in rows if not e.dob]
    dir_dobs = {}
    if chart_nums:
        for r in db.query(PatientDirectory).filter(
            PatientDirectory.chart_number.in_(chart_nums)
        ).all():
            dir_dobs[r.chart_number] = r.dob

    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "recalls": [_entry_to_dict(e, dob=dir_dobs.get(e.chart_number)) for e in rows],
    }


# ─── Detail ──────────────────────────────────────────────────────────

@router.get("/{recall_id}")
def get_recall(recall_id: str, db: Session = Depends(get_db),
               current_user: dict = Depends(require_permission("recall:work"))):
    e = db.query(RecallEntry).filter(RecallEntry.id == recall_id).first()
    if not e:
        raise HTTPException(status_code=404, detail="recall not found")

    # Log the view event
    db.add(RecallCallLog(
        recall_entry_id=e.id, chart_number=e.chart_number,
        event_type="detail_viewed", user_email=current_user.get("email"),
    ))
    db.commit()

    dir_row = db.query(PatientDirectory).filter(
        PatientDirectory.chart_number == e.chart_number
    ).first()
    dob = dir_row.dob if dir_row else None

    # Pull call history
    logs = db.query(RecallCallLog).filter(
        RecallCallLog.recall_entry_id == e.id
    ).order_by(desc(RecallCallLog.occurred_at)).limit(50).all()

    # WWE history — past preventive visits + future scheduled appts.
    # Most-recent-first ordering. expected_next prefers an actual future
    # scheduled visit when present; falls back to latest completed + 13mo.
    wwe_rows = (db.query(WWEVisit)
                  .filter(WWEVisit.chart_number == e.chart_number)
                  .order_by(desc(WWEVisit.visit_date))
                  .all())
    completed_rows = [v for v in wwe_rows
                      if v.status == "completed" and not v.is_future]
    scheduled_rows = [v for v in wwe_rows
                      if v.is_future and v.status == "scheduled"]
    latest_wwe = completed_rows[0].visit_date if completed_rows else None
    next_scheduled = (sorted(scheduled_rows, key=lambda v: v.visit_date)[0]
                      if scheduled_rows else None)

    expected_next = None
    if next_scheduled:
        expected_next = next_scheduled.visit_date
    elif latest_wwe:
        # +13 months, calendar-aware (clamps Jan 31 + 13mo → next year's Feb)
        from calendar import monthrange
        y, m = latest_wwe.year, latest_wwe.month + 13
        while m > 12:
            y += 1
            m -= 12
        last_dom = monthrange(y, m)[1]
        from datetime import date as _date
        expected_next = _date(y, m, min(latest_wwe.day, last_dom))

    return {
        "recall": _entry_to_dict(e, dob=dob),
        "history": [
            {
                "id": str(l.id),
                "event_type": l.event_type,
                "user_email": l.user_email,
                "occurred_at": str(l.occurred_at),
                "outcome": l.outcome,
                "notes": l.notes,
                "duration_seconds": l.duration_seconds,
            }
            for l in logs
        ],
        "wwe_history": [
            {
                "visit_date": str(v.visit_date),
                "procedure_code": v.procedure_code,
                "source": v.source,
                "status": v.status,
                "is_future": bool(v.is_future),
            }
            for v in wwe_rows
        ],
        "wwe_total_visits": len(completed_rows),
        "wwe_latest_date": str(latest_wwe) if latest_wwe else None,
        "wwe_expected_next": str(expected_next) if expected_next else None,
        "wwe_next_scheduled": (
            {
                "visit_date": str(next_scheduled.visit_date),
                "procedure_code": next_scheduled.procedure_code,
                "source": next_scheduled.source,
            } if next_scheduled else None
        ),
    }


# ─── Claim / release ─────────────────────────────────────────────────
# Lightweight soft-lock so two callers don't work the same patient at
# once. Claim is taken when the detail drawer opens (and refreshed when
# the user dials). It auto-expires after CLAIM_TTL minutes — if a
# caller walks away or closes their browser, the row unlocks itself.

@router.post("/{recall_id}/claim")
def claim_recall(recall_id: str, db: Session = Depends(get_db),
                  current_user: dict = Depends(require_permission("recall:work"))):
    """Take or refresh a soft claim on this recall. Returns 409 if another
    user already owns an unexpired claim."""
    e = db.query(RecallEntry).filter(RecallEntry.id == recall_id).first()
    if not e:
        raise HTTPException(status_code=404, detail="recall not found")
    me = (current_user.get("email") or "").lower().strip()
    _ensure_claim_available(e, me)
    _take_claim(e, me)
    db.commit()
    return {"claimed_by": e.claimed_by, "claimed_until": str(e.claimed_until)}


@router.delete("/{recall_id}/claim", status_code=200)
def release_recall(recall_id: str, db: Session = Depends(get_db),
                    current_user: dict = Depends(require_permission("recall:work"))):
    """Release a claim. Only releases if it was ours (or already expired)."""
    e = db.query(RecallEntry).filter(RecallEntry.id == recall_id).first()
    if not e:
        raise HTTPException(status_code=404, detail="recall not found")
    me = (current_user.get("email") or "").lower().strip()
    _release_claim(e, me)
    db.commit()
    return {"ok": True}


# ─── Logging actions ─────────────────────────────────────────────────

@router.post("/{recall_id}/call-attempted")
def log_call_attempted(recall_id: str, db: Session = Depends(get_db),
                        current_user: dict = Depends(require_permission("recall:work"))):
    """Legacy: fired when user clicked tel: link. Logs intent — outcome
    captured later via /outcome. Replaced by /dial in production but
    retained for fallback when RingOut is unavailable."""
    e = db.query(RecallEntry).filter(RecallEntry.id == recall_id).first()
    if not e:
        raise HTTPException(status_code=404, detail="recall not found")
    db.add(RecallCallLog(
        recall_entry_id=e.id, chart_number=e.chart_number,
        event_type="call_attempted", user_email=current_user.get("email"),
    ))
    e.attempts = (e.attempts or 0) + 1
    e.last_attempt_at = datetime.utcnow()
    e.last_worked_by = current_user.get("email")
    db.commit()
    return {"ok": True, "attempts": e.attempts}


@router.post("/{recall_id}/dial")
def dial(recall_id: str, db: Session = Depends(get_db),
         current_user: dict = Depends(require_permission("recall:work"))):
    """Initiate a RingCentral RingOut call for this recall.

    Flow:
      1. RC platform calls the staff member's RC extension first
      2. Once they answer, RC dials the patient's phone
      3. Patient sees the practice caller ID (RC_CALLER_ID env)
      4. We log call_attempted + the RC session id for later duration polling

    Requires:
      - The user has a ringcentral_user_id mapped (via Admin → Users)
      - The recall entry has a phone (cell preferred, else primary)
    """
    e = db.query(RecallEntry).filter(RecallEntry.id == recall_id).first()
    if not e:
        raise HTTPException(status_code=404, detail="recall not found")

    # Concurrent-caller guard: bail if another caller has an unexpired
    # claim. Refresh / take the claim so the dialing user is the owner.
    user_email = (current_user.get("email") or "").lower().strip()
    _ensure_claim_available(e, user_email)
    _take_claim(e, user_email)

    # Resolve calling user's RC extension + callback phone
    user = db.query(User).filter(User.email == user_email).first()
    if not user or not user.ringcentral_user_id:
        raise HTTPException(
            status_code=409,
            detail="Your account isn't mapped to a RingCentral extension. "
                   "Ask your admin to set ringcentral_user_id on your user.",
        )
    if not user.ringcentral_callback_number:
        raise HTTPException(
            status_code=409,
            detail="Your account doesn't have a callback phone configured. "
                   "Ask your admin to set ringcentral_callback_number "
                   "(the phone RC will ring first when you click dial).",
        )

    # Resolve patient phone
    phone = e.cell_phone or e.primary_phone
    if not phone:
        raise HTTPException(status_code=409,
                            detail="No phone number on file for this patient.")

    # Normalize to E.164 for RC API
    digits = "".join(c for c in phone if c.isdigit())
    if len(digits) == 10:
        e164 = f"+1{digits}"
    elif len(digits) == 11 and digits.startswith("1"):
        e164 = f"+{digits}"
    else:
        raise HTTPException(status_code=422,
                            detail=f"Phone {phone} doesn't look like a US number")

    # Guard: can't bridge a number to itself
    if user.ringcentral_callback_number == e164:
        raise HTTPException(
            status_code=409,
            detail=f"Patient phone is the same as your RingCentral callback number. "
                   f"You can't bridge a call to yourself.",
        )

    # Initiate RingOut
    try:
        rc = rc_client()
        result = rc.ring_out(
            from_ext_id=user.ringcentral_user_id,
            from_phone=user.ringcentral_callback_number,
            to_phone=e164,
        )
    except Exception as exc:
        # Log the failure and propagate
        db.add(RecallCallLog(
            recall_entry_id=e.id, chart_number=e.chart_number,
            event_type="dial_failed", user_email=user_email,
            notes=f"RingOut error: {exc}",
        ))
        db.commit()
        raise HTTPException(status_code=502, detail=f"RingCentral error: {exc}")

    # RC's response shape: {"id": "...", "status": {"callStatus": "...", ...}, "uri": "...", ...}
    rc_session_id = result.get("id")
    rc_status = (result.get("status") or {}).get("callStatus", "Unknown")

    # Log success
    db.add(RecallCallLog(
        recall_entry_id=e.id, chart_number=e.chart_number,
        event_type="call_attempted", user_email=user_email,
        notes=f"RingOut session {rc_session_id} — status {rc_status}",
    ))
    e.attempts = (e.attempts or 0) + 1
    e.last_attempt_at = datetime.utcnow()
    e.last_worked_by = user_email
    db.commit()

    return {
        "ok": True,
        "ringcentral_session_id": rc_session_id,
        "status": rc_status,
        "your_extension": user.ringcentral_extension,
        "patient_phone": e164,
        "message": f"RingCentral is calling extension {user.ringcentral_extension}. "
                   f"Pick up to be bridged to the patient.",
        "attempts": e.attempts,
    }


@router.post("/{recall_id}/outcome")
def log_outcome(recall_id: str, payload: OutcomePayload,
                db: Session = Depends(get_db),
                current_user: dict = Depends(require_permission("recall:work"))):
    """Record the outcome of a call. May trigger suppression, cooldown, or
    completion depending on the outcome value."""
    e = db.query(RecallEntry).filter(RecallEntry.id == recall_id).first()
    if not e:
        raise HTTPException(status_code=404, detail="recall not found")
    if payload.outcome not in ALL_OUTCOMES:
        raise HTTPException(status_code=422,
                            detail=f"outcome must be one of {ALL_OUTCOMES}")

    user = current_user.get("email")
    e.last_outcome = payload.outcome
    e.last_attempt_at = datetime.utcnow()
    e.last_worked_by = user
    if payload.notes:
        e.latest_comment = payload.notes

    db.add(RecallCallLog(
        recall_entry_id=e.id, chart_number=e.chart_number,
        event_type="outcome_logged", user_email=user,
        outcome=payload.outcome, notes=payload.notes,
    ))

    # Apply outcome rules
    if payload.outcome in PERMANENT_OUTCOMES:
        # Suppress permanently — chart cannot be re-added
        reason = PERMANENT_OUTCOMES[payload.outcome]
        existing = db.query(RecallSuppression).filter_by(
            chart_number=e.chart_number).first()
        if not existing:
            db.add(RecallSuppression(
                chart_number=e.chart_number, reason=reason,
                notes=f"From recall outcome: '{payload.outcome}'. "
                      f"{payload.notes or ''}".strip(),
                created_by=user,
            ))
        # Mark all entries for this chart as suppressed
        for ee in db.query(RecallEntry).filter_by(chart_number=e.chart_number).all():
            ee.status = "suppressed"
    elif payload.outcome in COMPLETED_OUTCOMES:
        e.status = "completed"
    elif payload.outcome in COOLDOWN_OUTCOMES:
        e.cooldown_until = datetime.utcnow() + COOLDOWN_OUTCOMES[payload.outcome]

    # Outcome logged → release the claim so this row goes back to the queue
    # (or is now off the queue entirely if status flipped).
    _release_claim(e, (user or "").lower().strip())

    db.commit()
    db.refresh(e)
    return _entry_to_dict(e)


# ─── Dashboard ───────────────────────────────────────────────────────

@router.get("/dashboard/stats")
def dashboard_stats(db: Session = Depends(get_db),
                    current_user: dict = Depends(require_permission("recall:work"))):
    """Top-of-page mini dashboard metrics."""
    today = date.today()
    week_ago = today - timedelta(days=7)
    month_ago = today - timedelta(days=30)

    n_active = db.query(func.count(RecallEntry.id)).filter(
        RecallEntry.status == "active"
    ).scalar() or 0
    n_suppressed = db.query(func.count(RecallEntry.id)).filter(
        RecallEntry.status == "suppressed"
    ).scalar() or 0
    n_completed = db.query(func.count(RecallEntry.id)).filter(
        RecallEntry.status == "completed"
    ).scalar() or 0

    # Calls (any event type) made in window
    calls_today = db.query(func.count(RecallCallLog.id)).filter(
        RecallCallLog.event_type == "call_attempted",
        func.date(RecallCallLog.occurred_at) == today,
    ).scalar() or 0
    calls_week = db.query(func.count(RecallCallLog.id)).filter(
        RecallCallLog.event_type == "call_attempted",
        func.date(RecallCallLog.occurred_at) >= week_ago,
    ).scalar() or 0

    # Outcomes logged this week
    outcomes_week = db.query(
        RecallCallLog.outcome, func.count(RecallCallLog.id)
    ).filter(
        RecallCallLog.event_type == "outcome_logged",
        func.date(RecallCallLog.occurred_at) >= week_ago,
    ).group_by(RecallCallLog.outcome).all()

    # Top callers this week (by # call_attempted events)
    top_callers = db.query(
        RecallCallLog.user_email, func.count(RecallCallLog.id)
    ).filter(
        RecallCallLog.event_type == "call_attempted",
        func.date(RecallCallLog.occurred_at) >= week_ago,
        RecallCallLog.user_email.isnot(None),
    ).group_by(RecallCallLog.user_email).order_by(
        func.count(RecallCallLog.id).desc()
    ).limit(5).all()

    # Aging — patients whose last_visit is X+ months ago
    n_overdue_24mo = db.query(func.count(RecallEntry.id)).filter(
        RecallEntry.status == "active",
        RecallEntry.last_visit.isnot(None),
        RecallEntry.last_visit < (today - timedelta(days=730)),
    ).scalar() or 0

    return {
        "queue": {
            "active": n_active,
            "suppressed": n_suppressed,
            "completed": n_completed,
            "overdue_24mo": n_overdue_24mo,
        },
        "calls": {
            "today": calls_today,
            "this_week": calls_week,
        },
        "outcomes_this_week": [
            {"outcome": o or "(no outcome)", "count": n} for o, n in outcomes_week
        ],
        "top_callers_this_week": [
            {"user": e, "calls": n} for e, n in top_callers
        ],
    }


# ─── Outcomes catalog ────────────────────────────────────────────────

@router.get("/outcomes/catalog")
def outcomes_catalog(current_user: dict = Depends(require_permission("recall:work"))):
    return {
        "outcomes": [
            {
                "value": o,
                "permanent_suppression": o in PERMANENT_OUTCOMES,
                "completes_recall": o in COMPLETED_OUTCOMES,
                "cooldown_days": COOLDOWN_OUTCOMES.get(o).days
                                  if o in COOLDOWN_OUTCOMES else None,
            }
            for o in ALL_OUTCOMES
        ]
    }


# ─── ModMed WWE-report upload ────────────────────────────────────────
# Daily drop of the ModMed appointment-history report. Idempotent —
# upserts on (chart, date, code, source=modmed) and re-runs the recall
# sweep so any new "scheduled" appts immediately drop their patients
# off the active recall list.

@router.post("/imports/modmed-wwe")
async def import_modmed_wwe(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_permission("recall:manage")),
):
    if not file.filename.lower().endswith((".xlsx", ".xls")):
        raise HTTPException(status_code=422, detail="Expected an .xlsx or .xls file")

    import io
    from app.services.wwe_visit_importer import import_modmed_xlsx

    contents = await file.read()
    try:
        result = import_modmed_xlsx(db, io.BytesIO(contents))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Import failed: {exc}")

    return {
        "filename": file.filename,
        "uploaded_by": current_user.get("email"),
        **result,
    }


@router.get("/imports/wwe-summary")
def wwe_import_summary(
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_permission("recall:work")),
):
    """Status snapshot of the WWE history corpus — used by the import
    page to show the operator what's already loaded."""
    from app.models.recall import WWEVisit
    from sqlalchemy import func

    by_source = (db.query(WWEVisit.source, func.count(WWEVisit.id))
                    .group_by(WWEVisit.source).all())
    last_modmed_import = (db.query(func.max(WWEVisit.last_seen_at))
                            .filter(WWEVisit.source == "modmed").scalar())
    future_count = (db.query(func.count(WWEVisit.id))
                       .filter(WWEVisit.is_future.is_(True),
                               WWEVisit.status == "scheduled").scalar())
    return {
        "totals_by_source": {s: n for s, n in by_source},
        "last_modmed_import": str(last_modmed_import) if last_modmed_import else None,
        "scheduled_future": future_count,
    }
