"""Smartsheet → recall_entries seed importer.

Pulls all rows from the practice's "Recalls" sheet and idempotently upserts
into recall_entries + recall_suppressions. Designed to be re-runnable —
existing recall_entries are updated in place by chart_number+recall_type;
nothing is deleted.

DNC and Unsubscribe rows are routed to recall_suppressions and their
recall_entries (if any) are marked status='suppressed'.

Run from a script or admin endpoint. Reads SMARTSHEET_TOKEN from env.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Optional

import httpx
from sqlalchemy.orm import Session

from app.models.recall import RecallEntry, RecallSuppression


log = logging.getLogger(__name__)

SMARTSHEET_API = "https://api.smartsheet.com/2.0"
DEFAULT_SHEET_ID = "3602814938179460"


# Map Smartsheet column titles → our internal field names. Keep this loose
# (case-insensitive lookup) so column re-orders or minor renames don't
# break the import.
COL_MAP = {
    "Patient ID":           "chart_number",
    "Patient Name":         "patient_name",
    "Cell Phone":           "cell_phone",
    "Primary Phone":        "primary_phone",
    "Email":                "email",
    "Primary Insurance":    "primary_insurance",
    "Primary Plan":         "primary_plan",
    "Last Visit":           "last_visit",
    "Recall Status":        "recall_status",
    "Recall Due":           "recall_due",
    "Recall Create":        "recall_create",
    "RecallExpirationDate": "recall_expiration",
    "Recall Type":          "recall_type",
    "Worked By":            "last_worked_by",
    "Outcome":              "last_outcome",
    "DO NOT CALL":          "_dnc",
    "Unsubscribe":          "_unsubscribe",
    "Date Stamp":           "_last_attempt_date",
    "Latest Comment":       "latest_comment",
    "Priority":             "priority",
    "Attempts":             "attempts",
}


@dataclass
class ImportResult:
    sheet_id: str
    total_rows: int = 0
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    suppressions_added: int = 0
    errors: List[str] = field(default_factory=list)


def _auth_headers() -> dict:
    token = os.environ.get("SMARTSHEET_TOKEN", "").strip()
    if not token:
        raise RuntimeError("SMARTSHEET_TOKEN not set in environment")
    return {"Authorization": f"Bearer {token}"}


def _fetch_sheet(sheet_id: str) -> dict:
    """Pull the full sheet — Smartsheet returns up to 10k rows in a page."""
    url = f"{SMARTSHEET_API}/sheets/{sheet_id}"
    # `?include=` could fetch attachments, comments, etc. — we don't need any.
    r = httpx.get(url, headers=_auth_headers(), timeout=60)
    r.raise_for_status()
    return r.json()


def _row_to_dict(row: dict, columns_by_id: Dict[int, str]) -> Dict[str, Any]:
    """Flatten cells → {column_title: display_value_or_value}."""
    out: Dict[str, Any] = {}
    for cell in row.get("cells", []):
        col_id = cell.get("columnId")
        title = columns_by_id.get(col_id)
        if not title:
            continue
        val = cell.get("displayValue", cell.get("value"))
        if val is None or val == "" or val == "-":
            continue
        out[title] = val
    out["_smartsheet_row_id"] = str(row.get("id"))
    return out


def _parse_date(s: Any) -> Optional[date]:
    if s is None or s == "":
        return None
    if isinstance(s, date) and not isinstance(s, datetime):
        return s
    try:
        return datetime.fromisoformat(str(s).replace("Z", "")).date()
    except Exception:
        try:
            # Fallback for 'YYYY-MM-DD' that already parses cleanly
            return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
        except Exception:
            return None


def _parse_int(s: Any) -> Optional[int]:
    if s in (None, ""):
        return None
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return None


def _is_truthy(v: Any) -> bool:
    """Smartsheet booleans come as strings — handle a permissive truth set."""
    if v is None:
        return False
    s = str(v).strip().upper()
    return s in ("1", "TRUE", "YES", "Y", "DO NOT CALL", "DNC")


def _is_unsubscribed(v: Any) -> bool:
    if v is None:
        return False
    s = str(v).strip().upper()
    if s in ("1", "TRUE", "YES", "Y", "UNSUBSCRIBED"):
        return True
    return False


def _suppress(db: Session, chart_number: str, reason: str,
              notes: Optional[str] = None) -> bool:
    """Idempotent: add a chart to recall_suppressions if not already there.
    Returns True if a new row was added."""
    if not chart_number:
        return False
    existing = db.query(RecallSuppression).filter_by(chart_number=chart_number).first()
    if existing:
        return False
    db.add(RecallSuppression(
        chart_number=chart_number,
        reason=reason,
        notes=notes,
        created_by="system:smartsheet_seed",
    ))
    return True


def detect_sheet_shape(columns_by_id: Dict[int, str]) -> str:
    """Distinguish the active 'Recalls' sheet shape from the per-range
    'PatientList' sheets. Recalls has 'Patient Name' (combined); PatientList
    has 'First Name' + 'Last Name' separately.
    """
    titles = set(columns_by_id.values())
    if "First Name" in titles and "Last Name" in titles and "PatientID" in titles:
        return "patient_list"
    return "recalls"


def import_patient_list_sheet(db: Session, sheet_id: str) -> ImportResult:
    """Pull a per-range PatientList sheet (10k-20k, 20k-30k, etc.).

    Each row → a candidate WWE recall. DONOTCALLLIST / Unsubscribed routes
    to recall_suppressions and skips the active row. Existing entries
    matching (chart_number, recall_type='Est - Well-Woman Exam') get
    updated in place — natural dedupe across sheets.

    Disabled by default since the WWC team moved off Smartsheet — set
    SMARTSHEET_ENABLED=true to run a one-off import.
    """
    if os.environ.get("SMARTSHEET_ENABLED", "false").strip().lower() != "true":
        raise RuntimeError(
            "Smartsheet sync is disabled (SMARTSHEET_ENABLED is not 'true').")
    result = ImportResult(sheet_id=sheet_id)

    sheet = _fetch_sheet(sheet_id)
    columns_by_id = {c["id"]: c["title"] for c in sheet.get("columns", [])}
    rows = sheet.get("rows", [])
    result.total_rows = len(rows)

    suppressed_charts = {s.chart_number for s in db.query(RecallSuppression).all()}
    # Pre-load existing WWE recall entries by chart#
    existing_wwe: Dict[str, RecallEntry] = {}
    for e in db.query(RecallEntry).filter(
        RecallEntry.recall_type == "Est - Well-Woman Exam"
    ).all():
        existing_wwe[e.chart_number] = e

    for row in rows:
        try:
            d = _row_to_dict(row, columns_by_id)
            chart = (d.get("PatientID") or "").strip()
            if not chart:
                result.skipped += 1
                continue

            # Suppression branches first — flags can be 'TRUE', 'YES', '1', etc.
            dnc_raw = d.get("DONOTCALLLIST")
            unsub_raw = d.get("Unsubscribed")
            if dnc_raw and str(dnc_raw).strip().upper() in ("TRUE", "YES", "1", "DO NOT CALL"):
                if chart not in suppressed_charts:
                    if _suppress(db, chart, "do_not_call",
                                 notes=f"From PatientList sheet {sheet_id}"):
                        result.suppressions_added += 1
                        suppressed_charts.add(chart)
                # If they already have an active entry, mark it suppressed
                ex = existing_wwe.get(chart)
                if ex:
                    ex.status = "suppressed"
                continue
            if unsub_raw and str(unsub_raw).strip().upper() in ("TRUE", "YES", "1"):
                if chart not in suppressed_charts:
                    if _suppress(db, chart, "unsubscribed",
                                 notes=f"From PatientList sheet {sheet_id}"):
                        result.suppressions_added += 1
                        suppressed_charts.add(chart)
                ex = existing_wwe.get(chart)
                if ex:
                    ex.status = "suppressed"
                continue

            # Already-suppressed → skip (don't re-add)
            if chart in suppressed_charts:
                result.skipped += 1
                continue

            # Build/update the recall entry as a candidate WWE recall
            entry = existing_wwe.get(chart)
            if entry is None:
                entry = RecallEntry(
                    chart_number=chart,
                    recall_type="Est - Well-Woman Exam",
                    source="smartsheet:patient_list",
                )
                db.add(entry)
                existing_wwe[chart] = entry
                result.inserted += 1
            else:
                result.updated += 1

            first = (d.get("First Name") or "").strip()
            last = (d.get("Last Name") or "").strip()
            entry.patient_name = entry.patient_name or (
                f"{last}, {first}" if last and first else (last or first or None)
            )
            entry.dob = _parse_date(d.get("DOB"))
            entry.cell_phone = entry.cell_phone or d.get("PhoneCell")
            entry.primary_phone = entry.primary_phone or d.get("PhonePrimary")
            entry.email = entry.email or d.get("Email")
            entry.primary_insurance = entry.primary_insurance or d.get("InsPrimary")

            seen_wwe = _parse_date(d.get("SeenWWE"))
            if seen_wwe:
                # Patient was already seen for WWE — record as last_visit, mark completed
                entry.last_visit = seen_wwe
                entry.status = "completed"
            else:
                # Only set status to active if nothing more authoritative is set already
                if entry.status not in ("suppressed", "completed"):
                    entry.status = "active"

            entry.smartsheet_row_id = d.get("_smartsheet_row_id")

        except Exception as exc:
            result.errors.append(f"row {row.get('rowNumber')}: {exc}")

    db.commit()
    return result


REASON_MAP = {
    # Source phrase (lowercased substring) → suppression reason
    "deceased": "deceased",
    "no longer a patient": "left_practice",
    "moved": "left_practice",
    "out of the area": "left_practice",
    "out of area": "left_practice",
    "left the practice": "left_practice",
    "do not call": "do_not_call",
    "decline": "declined",
}


def _classify_reason(raw: Optional[str]) -> str:
    if not raw:
        return "unsubscribed"
    s = str(raw).lower()
    for needle, label in REASON_MAP.items():
        if needle in s:
            return label
    return "unsubscribed"


def _flexible_parse_date(s: Any) -> Optional[date]:
    """Parse 'MM/DD/YYYY', 'YYYY-MM-DD', or with trailing time stamps."""
    if not s:
        return None
    s = str(s).strip()
    if not s:
        return None
    # Strip time portion if present
    s10 = s[:10]
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(s10, fmt).date()
        except ValueError:
            continue
    return None


def import_unsubscribed_sheet(db: Session, sheet_id: str) -> ImportResult:
    """Import the *Recall Unsubscribed List* — name + DOB + reason.

    Match each row to a chart number via patient_directory (last_name +
    first_name + dob exact). Add to recall_suppressions with mapped reason
    and mark any matching recall_entries as status='suppressed'. Rows that
    can't be matched are noted in errors but the run continues.
    """
    from app.models.patient_directory import PatientDirectory

    result = ImportResult(sheet_id=sheet_id)
    sheet = _fetch_sheet(sheet_id)
    columns_by_id = {c["id"]: c["title"] for c in sheet.get("columns", [])}
    rows = sheet.get("rows", [])
    result.total_rows = len(rows)

    suppressed_charts = {s.chart_number for s in db.query(RecallSuppression).all()}

    for row in rows:
        try:
            d = _row_to_dict(row, columns_by_id)
            first = (d.get("First Name") or "").strip()
            last = (d.get("Last Name") or "").strip()
            dob = _flexible_parse_date(d.get("DOB"))
            reason_raw = d.get("Reason for Unsubscribe") or ""

            if not last or not first:
                result.skipped += 1
                continue

            # Match against patient_directory by (last, first, dob)
            q = db.query(PatientDirectory).filter(
                PatientDirectory.last_name.ilike(last),
                PatientDirectory.first_name.ilike(first),
            )
            if dob is not None:
                q = q.filter(PatientDirectory.dob == dob)
            match = q.first()

            if not match:
                # Couldn't match — log + skip
                result.errors.append(
                    f"row {row.get('rowNumber')}: no chart match for {last}, {first} dob={dob}"
                )
                result.skipped += 1
                continue

            chart = match.chart_number
            reason = _classify_reason(reason_raw)

            if chart in suppressed_charts:
                # Already suppressed — just confirm any active recall is marked suppressed
                pass
            else:
                db.add(RecallSuppression(
                    chart_number=chart,
                    reason=reason,
                    notes=f"From Unsubscribed sheet — '{reason_raw}' (row {row.get('rowNumber')})",
                    created_by="system:smartsheet_unsub",
                ))
                suppressed_charts.add(chart)
                result.suppressions_added += 1

            # Mark any existing recall entries as suppressed
            for e in db.query(RecallEntry).filter_by(chart_number=chart).all():
                e.status = "suppressed"

        except Exception as exc:
            result.errors.append(f"row {row.get('rowNumber')}: {exc}")

    db.commit()
    return result


def import_from_smartsheet(db: Session,
                            sheet_id: str = DEFAULT_SHEET_ID) -> ImportResult:
    result = ImportResult(sheet_id=sheet_id)

    sheet = _fetch_sheet(sheet_id)
    columns_by_id = {c["id"]: c["title"] for c in sheet.get("columns", [])}
    rows = sheet.get("rows", [])
    result.total_rows = len(rows)

    # Pre-load existing recall_entries and suppressions for fast upsert
    existing_entries: Dict[tuple, RecallEntry] = {}
    for e in db.query(RecallEntry).all():
        existing_entries[(e.chart_number, e.recall_type or "")] = e
    suppressed_charts = {s.chart_number for s in db.query(RecallSuppression).all()}

    for row in rows:
        try:
            d = _row_to_dict(row, columns_by_id)
            chart = (d.get("Patient ID") or "").strip()
            if not chart:
                result.skipped += 1
                continue

            # Suppression branches — these short-circuit and don't seed the
            # entry as active. We still record the chart in suppressions.
            dnc_raw = d.get("DO NOT CALL")
            unsub_raw = d.get("Unsubscribe")
            if dnc_raw and dnc_raw.strip().upper() == "DO NOT CALL":
                if chart not in suppressed_charts:
                    if _suppress(db, chart, "do_not_call",
                                 notes=f"From Smartsheet seed (row {row.get('rowNumber')})"):
                        result.suppressions_added += 1
                        suppressed_charts.add(chart)
                continue
            if _is_unsubscribed(unsub_raw):
                if chart not in suppressed_charts:
                    if _suppress(db, chart, "unsubscribed",
                                 notes=f"From Smartsheet seed (row {row.get('rowNumber')})"):
                        result.suppressions_added += 1
                        suppressed_charts.add(chart)
                continue

            recall_type = (d.get("Recall Type") or "").strip() or None
            key = (chart, recall_type or "")

            entry = existing_entries.get(key)
            if entry is None:
                entry = RecallEntry(chart_number=chart, recall_type=recall_type,
                                    source="smartsheet")
                db.add(entry)
                existing_entries[key] = entry
                result.inserted += 1
            else:
                result.updated += 1

            entry.patient_name = d.get("Patient Name")
            entry.cell_phone = d.get("Cell Phone")
            entry.primary_phone = d.get("Primary Phone")
            entry.email = d.get("Email")
            entry.primary_insurance = d.get("Primary Insurance")
            entry.primary_plan = d.get("Primary Plan")
            entry.last_visit = _parse_date(d.get("Last Visit"))
            entry.recall_due = _parse_date(d.get("Recall Due"))
            entry.recall_create = _parse_date(d.get("Recall Create"))
            entry.recall_expiration = _parse_date(d.get("RecallExpirationDate"))
            entry.recall_status = d.get("Recall Status")
            entry.priority = _parse_int(d.get("Priority"))
            entry.attempts = _parse_int(d.get("Attempts")) or 0
            entry.last_outcome = d.get("Outcome")
            entry.last_worked_by = d.get("Worked By")
            entry.latest_comment = d.get("Latest Comment")
            attempt_date = _parse_date(d.get("Date Stamp"))
            entry.last_attempt_at = (datetime.combine(attempt_date, datetime.min.time())
                                     if attempt_date else None)
            entry.smartsheet_row_id = d.get("_smartsheet_row_id")

            # If the Smartsheet "Recall Status" indicates suppression-like
            # state, mirror that locally so it drops off the active queue.
            rs = (entry.recall_status or "").strip().lower()
            if rs in ("suppressed", "completed", "closed", "deceased",
                      "left practice", "declined"):
                entry.status = "suppressed" if rs != "completed" else "completed"
            else:
                entry.status = "active"

        except Exception as exc:
            result.errors.append(f"row {row.get('rowNumber')}: {exc}")

    db.commit()
    return result
