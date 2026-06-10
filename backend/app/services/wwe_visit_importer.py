"""Importer for historical Well-Woman Exam visits.

Reads Greenway PrimeSuite billing exports (XLS), one row per WWE-coded
charge, and inserts into wwe_visits. Idempotent on (chart_number,
visit_date, procedure_code) — re-running is safe.

Expected columns in the source file:
  - Patient ID                   → chart_number
  - Date: Service date of...     → visit_date
  - Procedure Code               → procedure_code (99384-7, 99394-7)

Future use: ModMed will export a similar shape; pass source='modmed'.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from app.utils.dt import now_utc_naive
from typing import Iterable

import pandas as pd
from sqlalchemy.orm import Session

from app.models.recall import WWEVisit, RecallEntry

log = logging.getLogger(__name__)


# WWE preventive-visit codes (new + established patient)
WWE_CODES = {99384, 99385, 99386, 99387, 99394, 99395, 99396, 99397}


# ModMed appointment-status text → our normalized status values.
# Anything not in this map is preserved verbatim (lower-cased) so we can
# add new statuses without code changes.
MODMED_STATUS_MAP = {
    "checked out":   "completed",
    "checked-out":   "completed",
    "completed":     "completed",
    "pending":       "scheduled",
    "scheduled":     "scheduled",
    "confirmed":     "scheduled",
    "cancelled":     "cancelled",
    "canceled":      "cancelled",
    "no show":       "noshow",
    "no-show":       "noshow",
    "noshow":        "noshow",
    "rescheduled":   "cancelled",  # original slot was cancelled
}


def _normalize_modmed_status(raw: str) -> str:
    if not raw:
        return "completed"
    return MODMED_STATUS_MAP.get(str(raw).strip().lower(), str(raw).strip().lower())


# Window after which a completed WWE no longer counts as "recent" — patient
# is due for a new exam and should appear on the recall list again.
RECALL_WINDOW_MONTHS = 13


def apply_recall_rules_from_wwe(db: Session) -> dict:
    """Walk every chart with WWE data and update recall_entries status:

      - If the patient has a future scheduled appointment (status='scheduled',
        is_future=True), their active recall rows flip to 'completed'.
      - If the latest *completed* visit is within RECALL_WINDOW_MONTHS,
        their active recall rows flip to 'completed'.
      - Otherwise: leave their status alone (they may still be 'active'
        and properly on the call list).

    Idempotent. Only touches rows whose status is 'active' (suppressed
    rows stay suppressed; already-completed rows stay completed).
    """
    from datetime import date, timedelta
    from calendar import monthrange
    from sqlalchemy import func

    today = date.today()
    cutoff = today - timedelta(days=int(RECALL_WINDOW_MONTHS * 30.4375))
    # Charts with any future scheduled WWE
    charts_with_future = {
        c for (c,) in db.query(WWEVisit.chart_number)
                         .filter(WWEVisit.is_future.is_(True),
                                 WWEVisit.status == "scheduled")
                         .distinct().all()
    }
    # Charts whose latest completed visit is within the window
    latest_completed = (
        db.query(WWEVisit.chart_number,
                  func.max(WWEVisit.visit_date).label("latest"))
          .filter(WWEVisit.status == "completed")
          .group_by(WWEVisit.chart_number).all()
    )
    charts_recently_seen = {
        c for c, latest in latest_completed if latest and latest >= cutoff
    }

    keep_off_recall = charts_with_future | charts_recently_seen
    if not keep_off_recall:
        return {"flipped_completed": 0, "future_count": 0, "recent_count": 0}

    # Flip active recall rows for those charts to completed
    actives = (db.query(RecallEntry)
                  .filter(RecallEntry.status == "active",
                          RecallEntry.chart_number.in_(keep_off_recall))
                  .all())
    for r in actives:
        r.status = "completed"
    db.commit()

    return {
        "flipped_completed": len(actives),
        "future_count": len(charts_with_future),
        "recent_count": len(charts_recently_seen),
    }


def import_xls(db: Session, path: str, source: str = "greenway",
               batch_size: int = 1000) -> dict:
    """Import a single Greenway XLS report. Returns counts."""
    df = pd.read_excel(path, engine="xlrd")
    df = df.rename(columns={
        "Patient ID": "chart_number",
        "Patient First Name": "first_name",
        "Patient Last Name": "last_name",
        "Date: Service date of the charge": "visit_date",
        "Procedure Code": "procedure_code",
    })
    return import_dataframe(db, df, source=source, batch_size=batch_size)


def import_modmed_xlsx(db: Session, path_or_buffer) -> dict:
    """Import a ModMed appointment-history report (XLSX).

    The report has both past completed visits and future scheduled
    appointments. We:
      - normalize Appointment Status into our completed/scheduled/cancelled/noshow
      - use the Appointment Type (WWE - Est / WWE - New) as the procedure
        code so the (chart, date, code) dedupe key still works
      - upsert: if a (chart, date, code, source='modmed') row already
        exists, update its status / is_future / last_seen_at — most
        recent report wins
    Returns a dict with counts.
    """
    df = pd.read_excel(path_or_buffer, engine="openpyxl")
    df = df.rename(columns={
        "Patient MRN": "chart_number",
        "Patient First Name": "first_name",
        "Patient Last Name": "last_name",
        "Patient DOB": "dob",
        "Patient Email Address": "email",
        "Patient Mobile Phone": "phone",
        "Appointment Date": "visit_date",
        "Appointment is in the Future?": "is_future_text",
        "Appointment Type": "appt_type",
        "New Patient Indicator": "new_patient",
        "Appointment Status": "appt_status",
        "Appointment Count": "appt_count",
    })

    rows: list[dict] = []
    skipped_bad_data = 0
    skipped_non_wwe = 0
    now = now_utc_naive()

    for _, r in df.iterrows():
        try:
            chart = str(r.get("chart_number") or "").strip()
            if not chart or chart.lower() == "nan":
                skipped_bad_data += 1
                continue

            visit_dt = r.get("visit_date")
            if pd.isna(visit_dt):
                skipped_bad_data += 1
                continue
            if isinstance(visit_dt, str):
                visit_dt = pd.to_datetime(visit_dt, errors="coerce")
            if pd.isna(visit_dt):
                skipped_bad_data += 1
                continue
            visit_date = visit_dt.date() if hasattr(visit_dt, "date") else visit_dt

            appt_type_raw = str(r.get("appt_type") or "").strip()
            if not appt_type_raw or "WWE" not in appt_type_raw.upper():
                skipped_non_wwe += 1
                continue
            # "WWE - Est" → "WWE-EST", "WWE - New" → "WWE-NEW"
            code = appt_type_raw.upper().replace(" ", "").replace("-", "-")
            # Collapse double-hyphens
            while "--" in code:
                code = code.replace("--", "-")

            status = _normalize_modmed_status(r.get("appt_status"))
            is_future_raw = str(r.get("is_future_text") or "").strip().lower()
            is_future = (is_future_raw == "yes")

            rows.append({
                "chart_number": chart,
                "visit_date": visit_date,
                "procedure_code": code,
                "source": "modmed",
                "status": status,
                "is_future": is_future,
                "last_seen_at": now,
            })
        except Exception as exc:
            log.warning("ModMed row skipped — %s: %s", exc, r.to_dict())
            skipped_bad_data += 1
            continue

    # Bulk-fetch existing ModMed rows for these charts so we can decide
    # insert-vs-update without per-row queries.
    existing_index: dict[tuple, WWEVisit] = {}
    if rows:
        chart_set = {r["chart_number"] for r in rows}
        for ev in (db.query(WWEVisit)
                     .filter(WWEVisit.source == "modmed",
                             WWEVisit.chart_number.in_(chart_set))
                     .all()):
            existing_index[(ev.chart_number, ev.visit_date, ev.procedure_code)] = ev

    inserted = 0
    updated = 0
    unchanged = 0
    seen_keys: set[tuple] = set()

    insert_batch: list[dict] = []
    for r in rows:
        key = (r["chart_number"], r["visit_date"], r["procedure_code"])
        if key in seen_keys:
            # Same key appeared twice in the same file — keep the last
            # one (overwrite earlier values in our buffers)
            continue
        seen_keys.add(key)

        ev = existing_index.get(key)
        if ev is None:
            insert_batch.append(r)
        else:
            changed = (
                ev.status != r["status"]
                or ev.is_future != r["is_future"]
            )
            ev.status = r["status"]
            ev.is_future = r["is_future"]
            ev.last_seen_at = now
            if changed:
                updated += 1
            else:
                unchanged += 1

    if insert_batch:
        db.bulk_insert_mappings(WWEVisit, insert_batch)
        inserted = len(insert_batch)
    db.commit()

    sweep = apply_recall_rules_from_wwe(db)

    return {
        "rows_in_file": len(df),
        "inserted": inserted,
        "updated": updated,
        "unchanged": unchanged,
        "skipped_non_wwe": skipped_non_wwe,
        "skipped_bad_data": skipped_bad_data,
        "recall_sweep": sweep,
    }


def import_dataframe(db: Session, df: pd.DataFrame, *, source: str,
                      batch_size: int = 1000) -> dict:
    """Import from a pre-shaped DataFrame with columns:
      chart_number, visit_date, procedure_code (last_name/first_name optional).
    """
    rows: list[dict] = []
    skipped_bad_data = 0
    skipped_non_wwe = 0

    for _, r in df.iterrows():
        try:
            chart = str(r["chart_number"]).strip()
            if not chart or chart.lower() == "nan":
                skipped_bad_data += 1
                continue

            visit_dt = r["visit_date"]
            if pd.isna(visit_dt):
                skipped_bad_data += 1
                continue
            if isinstance(visit_dt, str):
                visit_dt = pd.to_datetime(visit_dt, errors="coerce")
            if pd.isna(visit_dt):
                skipped_bad_data += 1
                continue
            visit_date = visit_dt.date() if hasattr(visit_dt, "date") else visit_dt

            code_raw = r.get("procedure_code")
            if pd.isna(code_raw):
                skipped_bad_data += 1
                continue
            try:
                code_int = int(code_raw)
            except (ValueError, TypeError):
                skipped_bad_data += 1
                continue
            if code_int not in WWE_CODES:
                skipped_non_wwe += 1
                continue

            rows.append({
                "chart_number": chart,
                "visit_date": visit_date,
                "procedure_code": str(code_int),
                "source": source,
            })
        except Exception as exc:
            log.warning("Row skipped — %s: %s", exc, r.to_dict())
            skipped_bad_data += 1
            continue

    # Bulk dedup against existing — single query covers everything
    existing = set()
    if rows:
        chart_set = {r["chart_number"] for r in rows}
        for c, d, code in (db.query(WWEVisit.chart_number,
                                      WWEVisit.visit_date,
                                      WWEVisit.procedure_code)
                              .filter(WWEVisit.chart_number.in_(chart_set))
                              .all()):
            existing.add((c, d, code))

    inserted = 0
    deduped = 0
    batch: list[dict] = []
    for r in rows:
        key = (r["chart_number"], r["visit_date"], r["procedure_code"])
        if key in existing:
            deduped += 1
            continue
        existing.add(key)  # protect against duplicates within the same file
        batch.append(r)
        if len(batch) >= batch_size:
            db.bulk_insert_mappings(WWEVisit, batch)
            db.commit()
            inserted += len(batch)
            batch = []
    if batch:
        db.bulk_insert_mappings(WWEVisit, batch)
        db.commit()
        inserted += len(batch)

    return {
        "rows_in_file": len(df),
        "inserted": inserted,
        "deduped": deduped,
        "skipped_non_wwe": skipped_non_wwe,
        "skipped_bad_data": skipped_bad_data,
    }
