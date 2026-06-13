"""Seed Surgery rows from the legacy Smartsheet workspace.

Pulls every row from the WWC Surgery Scheduling sheet and creates a
Surgery + milestone set per row, applying the agreed status remap and
column consolidation.

Idempotent on (smartsheet_row_id) — re-running updates rows in place,
preserving any post-seed edits except for fields that came from the
Smartsheet (those get refreshed from the latest sheet state).
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, date
from decimal import Decimal, InvalidOperation
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from app.models.surgery import Surgery

log = logging.getLogger(__name__)

SMARTSHEET_API = "https://api.smartsheet.com/2.0"
SHEET_ID = "2571134903314308"           # Active surgeries
COMPLETED_SHEET_ID = "894730247661444"  # Completed surgeries — large historical sheet


# ─── Status mapping (13 Smartsheet statuses → 6 base + sub-flags) ────

STATUS_MAP: dict[str, tuple[str, Optional[str]]] = {
    # smartsheet → (status, sub_flag)
    "Not Started":            ("new", None),
    "New":                    ("new", None),
    "Email Sent":             ("in_progress", "klara_sent"),
    "In Progress":            ("in_progress", None),
    "Awaiting Clearance":     ("in_progress", "awaiting_clearance"),
    "Awaiting Availability":  ("in_progress", "awaiting_date"),
    "Unable to pay":          ("in_progress", "unpaid_balance"),
    "Stuck":                  ("in_progress", None),     # computed, not stored
    "Scheduled":              ("confirmed", None),
    "Ready for Surgery":      ("confirmed", "ready"),
    "On Hold":                ("hold", None),
    "Canceled":               ("cancelled", None),
    "Completed":              ("completed", None),
}


# ─── Location → facility code ───────────────────────────────────────

FACILITY_MAP = {
    "Medstar Southern Maryland Hospital (MSMHC)": "medstar",
    "University of MD Charles Regional (UMCRMC)": "crmc",
    "White Plains Office": "office",
    "Virginia Health Center (VHC)": "office",   # VHC counts as an office facility
}


def _parse_facilities(loc_text: Optional[str]) -> tuple[list[str], Optional[str]]:
    """Parse the Location cell into (eligible_facilities, selected_facility).

    Smartsheet has either one location or comma-separated multi.
    Single-location rows have selected_facility set to that one.
    Multi-location rows leave selected_facility null until scheduled.
    """
    if not loc_text:
        return [], None
    parts = [p.strip() for p in str(loc_text).split(",") if p.strip()]
    eligibles = []
    for p in parts:
        if p in FACILITY_MAP:
            eligibles.append(FACILITY_MAP[p])
    selected = eligibles[0] if len(eligibles) == 1 else None
    return eligibles, selected


# ─── Surgery Type parsing ───────────────────────────────────────────

CPT_RE = re.compile(r"\[(\d{4,5})\]")


def _parse_surgery_type(text: Optional[str]) -> tuple[list[dict], bool]:
    """Extract procedures + CPTs from the 'Surgery Type' string.
    Returns (procedures_list, is_robotic).
    Surgery Type example: "Hysteroscopy D&C +/- Polypectomy [58558], Hysteroscopy Removal of Fibroid [58561]"
    """
    if not text:
        return [], False
    procedures = []
    is_robotic = "robotic" in text.lower()
    chunks = re.split(r",\s*(?=[A-Za-z])", str(text))
    for chunk in chunks:
        cpt_match = CPT_RE.search(chunk)
        cpt = cpt_match.group(1) if cpt_match else None
        descr = CPT_RE.sub("", chunk).strip(" -+/")
        if descr or cpt:
            procedures.append({"cpt": cpt, "description": descr})
    return procedures, is_robotic


# ─── Procedure classification (minor / major / robotic / office) ───

# Robotic: 58571–58575 (lap hyst with robot), 58545 (robotic myomectomy),
#          58552–58554 (robotic-assisted variants).
# Major: 49320 (diag lap), 58146 (abd myomectomy), 58660 (lap salping).
# Minor: 58558 (hyst D&C polypectomy), 58561 (hyst removal of fibroid),
#        58563 (endometrial ablation), 57522 (LEEP), 58356 (cryoabl).
# Anything else → "minor" by default; admin can override.

ROBOTIC_CPTS = {"58545", "58571", "58572", "58573", "58574", "58575"}
MAJOR_CPTS   = {"49320", "58146", "58660", "58662", "58550", "58552", "58553", "58554"}
MINOR_CPTS   = {"58558", "58561", "58563", "58555", "57522", "58356", "58100", "58120"}


def _classify_procedure(procedures: list[dict], is_robotic: bool, facility: Optional[str]) -> str:
    cpts = {p.get("cpt") for p in procedures if p.get("cpt")}
    if is_robotic or (cpts & ROBOTIC_CPTS):
        return "robotic_180"   # default; minutes column refines later
    if cpts & MAJOR_CPTS:
        return "major"
    if facility == "office":
        return "office"
    return "minor"


# ─── Money parsing ──────────────────────────────────────────────────

def _money(s) -> Optional[Decimal]:
    if s is None or s == "":
        return None
    try:
        return Decimal(str(s).replace("$", "").replace(",", "").strip())
    except (InvalidOperation, AttributeError):
        return None


def _date(s) -> Optional[date]:
    if s is None or s == "":
        return None
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


# ─── Auth status mapping ────────────────────────────────────────────

AUTH_MAP = {
    "Not Required":          "not_required",
    "Required":              "required",
    "Sent Request":          "sent_request",
    "Sent Medical Records":  "sent_records",
    "Peer-to-Peer Review":   "peer_review",
    "Approved":              "approved",
    "Denied":                "denied",
    "TBD":                   "tbd",
    "Completed":             "approved",
}


CLEARANCE_MAP = {
    "No":                                   ("not_required", False),
    "Yes":                                  ("required", True),
    "Request sent to Provider":             ("request_sent", True),
    "Clearance Rec'vd but not sent to hospital": ("received", True),
    "Received and sent to Hosp.":           ("sent_to_hospital", True),
    "Completed":                            ("completed", True),
}


PREOP_TEST_MAP = {
    "Not Required":   "not_required",
    "Required":       "required",
    "Lab/Test Rec'd": "received",
    "Completed":      "completed",
}


HOSP_POSTED_MAP = {
    "Not Needed (Office)":    "not_needed_office",
    "Sent to hospital":       "sent_to_hospital",
    "Confirmation Received":  "confirmation_received",
    "Not Required":           "not_required",
    "Completed":              "completed",
}


OP_REPORT_MAP = {
    "Not Required":  "not_required",
    "Not Received":  "not_received",
    "Completed":     "completed",
}


PATH_MAP = {
    "None expected":  "none_expected",
    "Yes":            "received",
    "No":             "expected",
    "Not Received":   "expected",
    "Not Required":   "not_required",
    "Completed":      "completed",
}


# ─── Milestone catalog (Phase 1) ───────────────────────────────────

# Hospital-based path
HOSPITAL_MILESTONES = [
    ("benefits_determined",    "Benefits Determination",              3),
    ("prior_auth",             "Prior auth received",                 5),
    ("patient_picks_date",     "Patient picks surgery date",         14),
    ("post_op_appts_scheduled","Post-op appointments scheduled",      7),
    ("device_assigned",        "Device ordered/assigned",             3),
    ("assistant_surgeon",      "Assistant surgeon coordinated",       5),
    ("consent",                "Consent",                              3),
    ("surgery_confirmed_hospital", "Surgery confirmed at hospital",   2),
    ("labs_to_hospital",       "Labs sent to hospital",               3),
    ("post_op_call",           "Spoke to patient post-op",            3),
    ("op_notes",               "Operative notes uploaded to ModMed",  7),
    ("path_report",            "Pathology report uploaded",          14),
    ("surgery_billed",         "Surgery billed",                      7),
]

# Office-based path (skip device, hospital confirmation, labs, op notes,
# assistant surgeon — office procedures don't use a co-surgeon)
OFFICE_MILESTONES = [
    ("benefits_determined",    "Benefits Determination",              3),
    ("prior_auth",             "Prior auth received",                 5),
    ("patient_picks_date",     "Patient picks procedure date",       14),
    ("post_op_appts_scheduled","Post-op appointments scheduled",      7),
    ("device_assigned",        "Device ordered/assigned",             3),
    ("consent",                "Consent",                              3),
    ("post_op_call",           "Spoke to patient post-op",            3),
    ("path_report",            "Pathology report uploaded",          14),
    ("surgery_billed",         "Surgery billed",                      7),
]


# ─── Smartsheet API ─────────────────────────────────────────────────

def _fetch_sheet(sheet_id: str = SHEET_ID) -> dict:
    token = os.environ.get("SMARTSHEET_TOKEN", "").strip()
    if not token:
        raise RuntimeError("SMARTSHEET_TOKEN not set")
    r = httpx.get(
        f"{SMARTSHEET_API}/sheets/{sheet_id}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=120,
    )
    if r.status_code != 200:
        raise RuntimeError(f"Smartsheet API error HTTP {r.status_code}: {r.text[:300]}")
    return r.json()


def _chart_from_combined(s) -> str:
    """Parse 'Traci Owens (12345)' → '12345'."""
    if not s:
        return ""
    m = re.search(r"\((\w+)\)\s*$", str(s))
    return m.group(1) if m else ""


def _name_from_combined(s) -> str:
    """Parse 'Traci Owens (12345)' → 'Traci Owens'."""
    if not s:
        return ""
    m = re.match(r"^(.+?)\s*\(\w+\)\s*$", str(s))
    return m.group(1).strip() if m else str(s).strip()


def _row_to_dict(row: dict, columns: dict[int, str]) -> dict:
    out = {"_row_id": row["id"]}
    for c in row.get("cells", []):
        col = columns.get(c["columnId"])
        if not col:
            continue
        v = c.get("displayValue") or c.get("value")
        if v not in (None, ""):
            out[col] = v
    return out


# ─── Importer ──────────────────────────────────────────────────────

def seed_from_smartsheet(
    db: Session,
    *,
    sheet_id: str = SHEET_ID,
    surgery_date_min: Optional[date] = None,
) -> dict:
    """Pull rows from a Smartsheet and create/update Surgery rows.

    surgery_date_min — when set, skip rows whose Surgery Date is before
    this date (or is missing entirely). Used by the Completed-sheet
    importer to only pull recent + upcoming surgeries.

    Disabled by default: surgeries are now created via the PDF order
    upload + manual create flows, with surgery_number and patient_
    directory populated by app.services.surgery_local_helpers. Re-enable
    by setting SMARTSHEET_ENABLED=true if you need to run a one-off
    backfill or sync.
    """
    if os.environ.get("SMARTSHEET_ENABLED", "false").strip().lower() != "true":
        raise RuntimeError(
            "Smartsheet sync is disabled (SMARTSHEET_ENABLED is not 'true'). "
            "Surgery creation is now upload/manual only.")
    sheet = _fetch_sheet(sheet_id)
    columns = {c["id"]: c["title"] for c in sheet["columns"]}

    inserted = updated = skipped = 0
    skipped_old = 0

    # Pre-fetch existing surgeries by smartsheet_row_id for upserts
    existing = {s.smartsheet_row_id: s
                for s in db.query(Surgery)
                            .filter(Surgery.smartsheet_row_id.isnot(None))
                            .all()}

    for ssrow in sheet.get("rows", []):
        row = _row_to_dict(ssrow, columns)
        smart_id = str(row["_row_id"])

        # Required fields. Some sheets use "PatientID", others use a
        # combined "Patient ID & Name" — fall back accordingly.
        chart = (str(row.get("PatientID") or "").strip()
                 or _chart_from_combined(row.get("Patient ID & Name")))
        name = (str(row.get("Patient Name") or "").strip()
                or _name_from_combined(row.get("Patient ID & Name")))
        if not chart or not name:
            skipped += 1
            continue

        # Date filter — Completed sheet pulls only recent + upcoming
        scheduled_dt = _date(row.get("Surgery Date"))
        if surgery_date_min is not None:
            if scheduled_dt is None or scheduled_dt < surgery_date_min:
                skipped_old += 1
                continue

        # Status is set below from the Smartsheet "Status" column — placeholder for now.
        status = "new"
        sub_flag = None

        # Facilities
        eligible, selected = _parse_facilities(row.get("Location"))

        # Procedures + CPT extraction
        procs, is_robotic = _parse_surgery_type(row.get("Surgery Type"))
        cpt_main = procs[0]["cpt"] if procs and procs[0].get("cpt") else None
        # Robotic forces facility = medstar
        if is_robotic:
            if "medstar" not in eligible:
                eligible = ["medstar"]
            selected = "medstar"

        classification = _classify_procedure(procs, is_robotic, selected)

        # Auth + clearance
        auth_status = AUTH_MAP.get(row.get("Auth") or "Not Required", "not_required")
        clearance_status, clearance_required = CLEARANCE_MAP.get(
            row.get("Clearance Req'd") or "No", ("not_required", False))

        # Money fields
        pat_resp = _money(row.get("Patient Resp."))
        paid = _money(row.get("Payment Made")) or Decimal("0")

        # Time
        try:
            est_min = int(str(row.get("Time Expected") or "").strip()) if row.get("Time Expected") else None
        except (TypeError, ValueError):
            est_min = None

        # Sterilization consent — combining the two columns (one for Medicaid plans)
        sc_required = (row.get("Sterilization Consent") in ("Required", "Completed", "Completed & Submitted")
                       or row.get("Sterilization Consent - Medicaid Plans") in ("Required", "Completed", "Completed & Submitted"))
        sc_status_raw = row.get("Sterilization Consent") or row.get("Sterilization Consent - Medicaid Plans") or "Not Required"
        sc_status_map = {
            "Not Required": "not_required",
            "Required": "required",
            "Completed & Submitted": "completed",
            "Confirmed Rec'vd by Insurance": "completed",
            "Completed": "completed",
        }
        sc_status = sc_status_map.get(sc_status_raw, "not_required")

        # Build the field dict (used for both insert and update)
        fields = dict(
            smartsheet_row_id=smart_id,
            surgery_number=row.get("SurgeryNumber"),
            chart_number=chart,
            patient_name=name,
            first_name=row.get("First Name"),
            last_name=row.get("Last Name"),
            dob=_date(row.get("DOB")),
            email=row.get("Email"),
            phone=row.get("Phone"),
            address_street=row.get("Address"),
            address_city=row.get("City"),
            address_state=row.get("State"),
            address_zip=str(row.get("Zip") or "")[:10] or None,

            primary_insurance=row.get("Primary Insurance"),
            primary_member_id=row.get("Primary Ins ID"),
            secondary_insurance=row.get("Secondary Insurance"),
            secondary_member_id=row.get("Secondary Ins ID"),

            surgeon_primary=row.get("Primary Surgeon"),
            surgeon_secondary=row.get("Secondary Surgeon"),
            procedures=procs,
            diagnoses=([{"icd": row.get("ICD-10"), "description": row.get("Diagnosis")}]
                       if row.get("ICD-10") or row.get("Diagnosis") else None),
            estimated_minutes=est_min,
            is_robotic=is_robotic,
            procedure_classification=classification,

            eligible_facilities=eligible,
            selected_facility=selected,

            auth_status=auth_status,
            auth_number=row.get("Auth No."),
            clearance_required=clearance_required,
            clearance_status=clearance_status,

            sterilization_consent_required=sc_required,
            sterilization_consent_status=sc_status,

            preop_test_status=PREOP_TEST_MAP.get(row.get("Pre-OP Test") or "Not Required", "not_required"),
            preop_date=_date(row.get("Pre-OP Date")),

            labs_sent_to_hospital=(row.get("Labs Sent?") == "Completed"),

            consent_status=("signed" if row.get("Consent Signed Date") else
                            ("sent" if row.get("Consent Sent Date") else "not_required")),
            consent_doc_id=row.get("Consent Doc ID"),
            consent_sent_at=(_date(row.get("Consent Sent Date"))
                             and datetime.combine(_date(row.get("Consent Sent Date")), datetime.min.time())),
            consent_signed_at=(_date(row.get("Consent Signed Date"))
                               and datetime.combine(_date(row.get("Consent Signed Date")), datetime.min.time())),

            hosp_posted_status=HOSP_POSTED_MAP.get(row.get("Hosp Posted?") or "Not Needed (Office)",
                                                     "not_needed_office"),

            scheduled_date=_date(row.get("Surgery Date")),
            scheduled_in_modmed_at=(_date(row.get("Scheduled in ModMed"))
                                     and datetime.combine(_date(row.get("Scheduled in ModMed")),
                                                          datetime.min.time())),
            calendar_invite_sent_at=(_date(row.get("Calendar Email"))
                                     and datetime.combine(_date(row.get("Calendar Email")),
                                                          datetime.min.time())),

            post_op_appt_date=_date(row.get("Post-Op Appt")),
            post_op_appt_2nd_date=_date(row.get("Post-Op Appt 2nd")),
            post_op_call_status=row.get("Post-Op Call"),
            operative_report_status=OP_REPORT_MAP.get(row.get("Operative Report") or "Not Received",
                                                       "not_received"),
            pathology_status=PATH_MAP.get(row.get("Pathology Rec'd") or "None expected",
                                           "none_expected"),

            benefits_verified_at=_date(row.get("Benefits")),
            benefits_expires_on=_date(row.get("Benefits Exp")),
            patient_responsibility=pat_resp,
            amount_paid=paid,
            payment_posted_to_billing=(row.get("Payment Posted-Billing") in (True, "true")),

            fmla_status=row.get("FMLA?"),

            status=status,
            sub_flag=sub_flag,
            urgency=("urgent" if row.get("Urgent") in (True, "true") else "routine"),

            notes=row.get("Special Surgery Instructions") or row.get("Comments"),
            latest_comment=row.get("Latest comment"),

            source="smartsheet",
        )

        if smart_id in existing:
            s = existing[smart_id]
            for k, v in fields.items():
                setattr(s, k, v)
            db.flush()
            updated += 1
        else:
            s = Surgery(**fields)
            db.add(s)
            db.flush()
            inserted += 1
        db.flush()
        # Status now comes straight from the Smartsheet "Status" column via
        # STATUS_MAP. (Milestones were retired 2026-06 — the step engine
        # drives live status; this disabled-by-default seed path no longer
        # reads/writes SurgeryMilestone.)
        derived_status, derived_sub = STATUS_MAP.get(
            row.get("Status") or "New", ("new", None))
        s.status = derived_status
        s.sub_flag = derived_sub

    db.commit()
    return {
        "sheet_id": sheet_id,
        "rows_in_sheet": len(sheet.get("rows", [])),
        "inserted": inserted,
        "updated": updated,
        "skipped_missing_fields": skipped,
        "skipped_old_dates": skipped_old,
    }


def seed_from_completed_sheet(db: Session, *, weeks_back: int = 6) -> dict:
    """Pull from the Completed sheet (894730247661444), filtered to
    surgeries with a Surgery Date >= today - weeks_back. This naturally
    includes future-dated surgeries too."""
    from datetime import timedelta
    cutoff = date.today() - timedelta(weeks=weeks_back)
    return seed_from_smartsheet(db, sheet_id=COMPLETED_SHEET_ID,
                                 surgery_date_min=cutoff)
