"""ModMed "Pellet Insert" appointment-list importer.

Expected Excel columns (16):
  Patient MRN, Patient First Name, Patient Last Name, Patient DOB,
  Patient Mobile Phone, Patient Email Address, Appointment Date,
  Appointment Time, Appointment Type, Appointment Status, Payer,
  Primary Provider, Location, Active Card on File?, Patient Link,
  Appointment Count

Behavior:
  • Upsert each row keyed on (patient_mrn, appointment_date).
  • Patient demographics updated only when blank in DB OR the upload
    has a non-empty value (phone/email/payer always refresh).
  • visit_kind = 'initial' for first-ever pellet visit per patient,
    else 'repeat'.
  • Status mapping:
        Pending / Confirmed → status='in_progress'
        Checked In          → status='in_progress', scheduled_date=today
        Checked Out         → status='inserted', inserted_at=appt_date
                                + auto-complete 'inserted' milestone
  • Optional "cancel-missing" sweep: any existing in_progress visit
    whose scheduled_date is within the upload's date range AND whose
    (mrn, date) is NOT in the upload gets marked status='cancelled' with
    outcome='auto_cancelled_not_in_upload'. Billed/already-inserted
    visits are NEVER touched.
"""
from __future__ import annotations

import io
from datetime import date as _date, datetime
from app.utils.dt import now_utc_naive
from typing import Optional

import pandas as pd
from sqlalchemy.orm import Session

from app.models.pellet import (
    PelletAuditEvent, PelletPatient, PelletVisit, PelletVisitMilestone,
)
from app.services.pellet_workflow import spawn_milestones, default_price_for


LOCATION_MAP = {
    "white plains":      "white_plains",
    "white plains, md":  "white_plains",
    "brandywine":        "brandywine",
    "arlington":         "arlington",
}


def _to_str(v) -> Optional[str]:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip()
    return s or None


def _to_date(v):
    if v is None or pd.isna(v): return None
    try:
        return pd.to_datetime(v).date()
    except Exception:
        return None


def _map_location(s: Optional[str]) -> Optional[str]:
    if not s: return None
    return LOCATION_MAP.get(s.strip().lower())


def _map_status(modmed_status: str) -> tuple[str, bool]:
    """Returns (our_status, is_completed). 'completed' means the appt has
    actually happened — we set inserted_at and inserted milestone."""
    s = (modmed_status or "").strip().lower()
    if s == "checked out":
        return "inserted", True
    if s in ("pending", "confirmed", "checked in", "arrived"):
        return "in_progress", False
    return "in_progress", False


def parse_excel(file_bytes: bytes) -> list[dict]:
    df = pd.read_excel(io.BytesIO(file_bytes))
    required = {"Patient MRN", "Appointment Date"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"missing required columns: {sorted(missing)}")
    rows = []
    for _, r in df.iterrows():
        rows.append({
            "mrn":         _to_str(r.get("Patient MRN")),
            "first_name":  _to_str(r.get("Patient First Name")),
            "last_name":   _to_str(r.get("Patient Last Name")),
            "dob":         _to_date(r.get("Patient DOB")),
            "phone":       _to_str(r.get("Patient Mobile Phone")),
            "email":       _to_str(r.get("Patient Email Address")),
            "appt_date":   _to_date(r.get("Appointment Date")),
            "appt_time":   _to_str(r.get("Appointment Time")),
            "appt_type":   _to_str(r.get("Appointment Type")),
            "appt_status": _to_str(r.get("Appointment Status")),
            "payer":       _to_str(r.get("Payer")),
            "provider":    _to_str(r.get("Primary Provider")),
            "location":    _to_str(r.get("Location")),
            "patient_link": _to_str(r.get("Patient Link")),
        })
    return rows


def import_appointments(
    db: Session,
    rows: list[dict],
    *,
    actor: str = "system",
    cancel_missing: bool = False,
) -> dict:
    """Upsert each row + optionally cancel missing in-range visits.
    Returns a report dict for the UI."""
    report = {
        "total_rows": len(rows),
        "patients_added": 0,
        "patients_updated": 0,
        "visits_added": 0,
        "visits_updated": 0,
        "visits_marked_inserted": 0,
        "visits_cancelled_missing": 0,
        "skipped_no_mrn_or_date": 0,
        "errors": [],
        "cancelled_visits": [],
    }

    seen_keys: set = set()    # (mrn, appt_date)
    min_d: Optional[_date] = None
    max_d: Optional[_date] = None

    for r in rows:
        mrn = r["mrn"]
        dt = r["appt_date"]
        if not mrn or not dt:
            report["skipped_no_mrn_or_date"] += 1
            continue
        seen_keys.add((mrn, dt))
        if min_d is None or dt < min_d: min_d = dt
        if max_d is None or dt > max_d: max_d = dt

        # ── Upsert patient ──
        p = (db.query(PelletPatient)
               .filter(PelletPatient.chart_number == mrn).first())
        is_new_patient = False
        if not p:
            # Build the patient name from First Last (or MRN as fallback)
            if r["last_name"] and r["first_name"]:
                name = f"{r['last_name'].title()}, {r['first_name'].title()}"
            else:
                name = r["last_name"] or r["first_name"] or f"(MRN {mrn})"
            p = PelletPatient(
                chart_number=mrn,
                patient_name=name,
                patient_dob=r["dob"],
                patient_phone=r["phone"],
                patient_email=r["email"],
                primary_insurance=r["payer"],
                patient_type="new",     # initial visit → new patient pricing
                status="active",
                created_by=actor,
                notes=f"Auto-enrolled from ModMed upload {_date.today().isoformat()}",
            )
            db.add(p); db.flush()
            is_new_patient = True
            report["patients_added"] += 1
        else:
            # Refresh contact / payer info — overwrite when upload has a value
            changed = False
            for src_key, dst_attr in [("phone", "patient_phone"),
                                       ("email", "patient_email"),
                                       ("payer", "primary_insurance")]:
                v = r.get(src_key)
                if v and getattr(p, dst_attr) != v:
                    setattr(p, dst_attr, v); changed = True
            if r["dob"] and not p.patient_dob:
                p.patient_dob = r["dob"]; changed = True
            if changed:
                report["patients_updated"] += 1

        # ── Determine visit kind ──
        # First-ever pellet visit for this patient → initial; else repeat
        existing_count = (db.query(PelletVisit)
                            .filter(PelletVisit.patient_id == p.id).count())
        visit_kind = "initial" if existing_count == 0 else "repeat"

        # ── Upsert visit ──
        v = (db.query(PelletVisit)
               .filter(PelletVisit.patient_id == p.id,
                       PelletVisit.scheduled_date == dt).first())
        new_status, is_completed = _map_status(r["appt_status"] or "")

        if not v:
            v = PelletVisit(
                patient_id=p.id,
                visit_kind=visit_kind,
                status=new_status,
                scheduled_date=dt,
                location=_map_location(r["location"]),
                provider=r["provider"],
                modmed_link=r["patient_link"],
                price_amount=default_price_for(p.patient_type
                                                 if visit_kind == "initial"
                                                 else "established"),
                notes=f"Imported {r['appt_status']} from ModMed appt upload "
                      f"({r['appt_time'] or 'no time'})",
                created_by=actor,
            )
            db.add(v); db.flush()
            spawn_milestones(db, v, p.patient_type)
            db.flush()
            report["visits_added"] += 1
            if is_completed:
                _mark_visit_inserted(db, v, dt, actor)
                report["visits_marked_inserted"] += 1
            db.add(PelletAuditEvent(
                actor=actor, action="visit_imported",
                location=v.location,
                summary=f"Imported {visit_kind} visit on {dt} for {p.patient_name}",
                detail={"mrn": mrn, "modmed_status": r["appt_status"]},
            ))
        else:
            # Don't disturb already-billed visits
            if v.status == "billed":
                continue
            changed = False
            if v.location != _map_location(r["location"]) and _map_location(r["location"]):
                v.location = _map_location(r["location"]); changed = True
            if r["provider"] and v.provider != r["provider"]:
                v.provider = r["provider"]; changed = True
            if r["patient_link"] and v.modmed_link != r["patient_link"]:
                v.modmed_link = r["patient_link"]; changed = True

            # If the upload says it's now checked out (= inserted) and we
            # haven't marked it that way yet, do the transition
            if is_completed and v.status != "inserted":
                _mark_visit_inserted(db, v, dt, actor)
                changed = True
                report["visits_marked_inserted"] += 1
            elif (not is_completed) and v.status != new_status \
                 and v.status in ("in_progress",):
                v.status = new_status; changed = True

            if changed:
                report["visits_updated"] += 1

    # ── Optional sweep: cancel in-range in_progress visits not in upload ──
    if cancel_missing and min_d and max_d:
        candidates = (db.query(PelletVisit)
                        .filter(PelletVisit.scheduled_date >= min_d,
                                PelletVisit.scheduled_date <= max_d,
                                PelletVisit.status == "in_progress")
                        .all())
        for v in candidates:
            p = db.query(PelletPatient).filter(PelletPatient.id == v.patient_id).first()
            if not p: continue
            key = (p.chart_number, v.scheduled_date)
            if key in seen_keys:
                continue
            v.status = "cancelled"
            v.outcome = "auto_cancelled_not_in_upload"
            v.outcome_notes = (
                f"Visit not present in ModMed upload on "
                f"{_date.today().isoformat()} — assumed cancelled or no-show."
            )
            report["visits_cancelled_missing"] += 1
            report["cancelled_visits"].append({
                "patient_name": p.patient_name,
                "chart_number": p.chart_number,
                "scheduled_date": v.scheduled_date.isoformat(),
            })
            db.add(PelletAuditEvent(
                actor=actor, action="visit_auto_cancelled",
                location=v.location,
                summary=f"Auto-cancelled visit {v.scheduled_date} for {p.patient_name} "
                        f"(not in ModMed upload)",
                detail={"mrn": p.chart_number,
                        "upload_range": f"{min_d} → {max_d}"},
            ))

    return report


def _mark_visit_inserted(db: Session, v: PelletVisit, dt: _date, actor: str) -> None:
    """Mark a visit as completed — sets inserted_at, status, completes
    the 'inserted' milestone. Does NOT touch the dose card or billing."""
    v.status = "inserted"
    v.inserted_at = datetime.combine(dt, datetime.min.time())
    v.inserted_by = actor
    if not v.outcome:
        v.outcome = "perfect"
    # Auto-complete the 'inserted' milestone if present
    m = (db.query(PelletVisitMilestone)
           .filter(PelletVisitMilestone.visit_id == v.id,
                   PelletVisitMilestone.kind == "inserted").first())
    if m and m.status == "pending":
        m.status = "done"
        m.completed_at = now_utc_naive()
        m.completed_by = actor
    # Also mark the 'scheduled' one if not already
    sched = (db.query(PelletVisitMilestone)
               .filter(PelletVisitMilestone.visit_id == v.id,
                       PelletVisitMilestone.kind == "scheduled").first())
    if sched and sched.status == "pending":
        sched.status = "done"
        sched.completed_at = now_utc_naive()
        sched.completed_by = actor
