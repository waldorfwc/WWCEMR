"""Import recall data from a multi-tab XLSX (the 'Patient List All' workbook).

Tabs processed:
  - PastWWE       → backfill last_visit on existing recall_entries
  - FutureWWE     → mark recall_entries as 'completed' (patient has upcoming appt)
  - UnScubscribed → add chart numbers to recall_suppressions (reason='unsubscribed')
  - DoNotCall     → add chart numbers to recall_suppressions (reason='do_not_call')

Other tabs (Patient Master, Email Failures, Bounced*, Sent Emails, Logs, etc.)
are skipped — they're either redundant with already-imported sources or
operational metadata not needed for the recall queue.
"""
from __future__ import annotations

import logging
import re
import warnings
from dataclasses import dataclass, field
from datetime import date, datetime
from difflib import SequenceMatcher
from typing import Dict, List, Optional

import pandas as pd
from sqlalchemy.orm import Session

from app.models.patient import Patient
from app.models.patient_directory import PatientDirectory
from app.models.recall import RecallEntry, RecallSuppression


log = logging.getLogger(__name__)
warnings.filterwarnings("ignore", message="Workbook contains no default style")


@dataclass
class XlsxImportResult:
    past_wwe_updated: int = 0
    future_wwe_marked: int = 0
    unsubscribed_added: int = 0
    do_not_call_added: int = 0
    skipped_no_match: int = 0
    errors: List[str] = field(default_factory=list)


def _parse_date(s) -> Optional[date]:
    if s is None or pd.isna(s):
        return None
    if isinstance(s, datetime):
        return s.date()
    if isinstance(s, date):
        return s
    s = str(s).strip()[:10]
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _str(v) -> Optional[str]:
    if v is None or pd.isna(v):
        return None
    s = str(v).strip()
    return s or None


def _resolve_chart_by_name_dob(db: Session, first: str, last: str,
                                dob: Optional[date]) -> Optional[str]:
    """Look up a chart number from PatientDirectory OR Patients using name + DOB.

    Both tables are queried — patient_directory comes from Phreesia
    demographic PDFs and is broader (~53k); patients comes from PrimeSuite
    exports (~24k) and may carry different name spellings. Matching either
    is fine since they share the chart number key."""
    if not first or not last:
        return None

    # First try the canonical directory
    q = db.query(PatientDirectory).filter(
        PatientDirectory.last_name.ilike(last),
        PatientDirectory.first_name.ilike(first),
    )
    if dob is not None:
        q = q.filter(PatientDirectory.dob == dob)
    row = q.first()
    if row:
        return row.chart_number

    # Fallback: legacy patients table (different spelling source)
    q2 = db.query(Patient).filter(
        Patient.last_name.ilike(last),
        Patient.first_name.ilike(first),
    )
    if dob is not None:
        q2 = q2.filter(Patient.date_of_birth == dob)
    row2 = q2.first()
    return row2.patient_id if row2 else None


def _resolve_chart_by_email(db: Session, email: str) -> Optional[str]:
    """Look up a chart number by exact email match across all 3 sources.

    Tries patient_directory → patients → recall_entries, in that order.
    Returns None if there's ambiguity (>1 match) or no hit."""
    if not email or "@" not in email:
        return None
    em = email.strip().lower()

    rows = db.query(PatientDirectory).filter(
        PatientDirectory.email.ilike(em)
    ).limit(2).all()
    if len(rows) == 1:
        return rows[0].chart_number

    rows = db.query(Patient).filter(Patient.email.ilike(em)).limit(2).all()
    if len(rows) == 1:
        return rows[0].patient_id

    rows = db.query(RecallEntry).filter(
        RecallEntry.email.ilike(em)
    ).limit(2).all()
    if len(rows) == 1:
        return rows[0].chart_number
    return None


def _resolve_chart_by_phone(db: Session, phone: str) -> Optional[str]:
    """Look up a chart number by phone number — exact 10-digit match against
    recall_entries.cell_phone or primary_phone. Returns None on ambiguity."""
    if not phone:
        return None
    digits = "".join(c for c in str(phone) if c.isdigit())
    if len(digits) < 10:
        return None
    last10 = digits[-10:]
    # Match either cell_phone or primary_phone — check loose since formats vary
    formatted_a = f"{last10[:3]}-{last10[3:6]}-{last10[6:]}"
    formatted_b = f"({last10[:3]}) {last10[3:6]}-{last10[6:]}"
    rows = db.query(RecallEntry).filter(
        (RecallEntry.cell_phone.in_([formatted_a, formatted_b, last10])) |
        (RecallEntry.primary_phone.in_([formatted_a, formatted_b, last10]))
    ).limit(2).all()
    if len(rows) == 1:
        return rows[0].chart_number
    return None


def _suppress(db: Session, chart: str, reason: str, notes: str,
              already: set) -> bool:
    if chart in already:
        return False
    db.add(RecallSuppression(
        chart_number=chart, reason=reason, notes=notes,
        created_by="system:xlsx_import",
    ))
    already.add(chart)
    # Also mark any existing recall entries as suppressed
    for e in db.query(RecallEntry).filter_by(chart_number=chart).all():
        e.status = "suppressed"
    return True


@dataclass
class FormResponseImportResult:
    total_rows: int = 0
    suppressed_via_dob: int = 0
    suppressed_via_email: int = 0
    suppressed_via_phone: int = 0
    already_suppressed: int = 0
    unmatched: int = 0
    errors: List[str] = field(default_factory=list)


REASON_MAP = {
    "deceased":              "deceased",
    "no longer a patient":   "left_practice",
    "moved":                 "left_practice",
    "out of the area":       "left_practice",
    "out of area":           "left_practice",
    "left the practice":     "left_practice",
    "do not call":           "do_not_call",
    "decline":               "declined",
}


def _classify_reason(raw: Optional[str]) -> str:
    if not raw:
        return "unsubscribed"
    s = str(raw).lower()
    for needle, label in REASON_MAP.items():
        if needle in s:
            return label
    return "unsubscribed"


# ─── Fuzzy matching helpers ──────────────────────────────────────────

def _name_similar(a: str, b: str, threshold: float = 0.85) -> bool:
    """SequenceMatcher ratio — 0.85 ≈ 'one typo in a 7-char name'."""
    if not a or not b:
        return False
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio() >= threshold


def _normalize_gmail(email: str) -> str:
    """Gmail ignores dots in the local-part. Normalize for comparison."""
    if not email or "@" not in email:
        return email or ""
    local, _, domain = email.lower().strip().partition("@")
    if domain in ("gmail.com", "googlemail.com"):
        # Strip dots and any +tag
        local = local.replace(".", "").split("+", 1)[0]
    return f"{local}@{domain}"


def fuzzy_match_chart(db: Session, first: str, last: str,
                       dob: Optional[date], email: Optional[str],
                       phone: Optional[str]) -> Optional[tuple]:
    """Try harder to find a chart number when exact matching failed.

    Returns (chart_number, match_path) for matches with HIGH confidence
    only. Conservative — refuses to match if multiple candidates tie.
    """
    # Pass A: DOB exact + same last name + first name starts-with-or-fuzzy.
    # Catches initials ("K Held" vs "Karen Held") and 1-typo last names.
    # Searches BOTH patient_directory and patients.
    if dob and last:
        # patient_directory branch
        candidates_pd = db.query(PatientDirectory).filter(
            PatientDirectory.dob == dob
        ).all()
        candidates_p = db.query(Patient).filter(
            Patient.date_of_birth == dob
        ).all()
        # Normalize: tuple of (chart_number, last_name, first_name)
        pool = [(c.chart_number, c.last_name, c.first_name) for c in candidates_pd]
        pool += [(c.patient_id, c.last_name, c.first_name) for c in candidates_p]

        # Dedupe by chart_number
        seen = set()
        unique_pool = []
        for tup in pool:
            if tup[0] in seen: continue
            seen.add(tup[0])
            unique_pool.append(tup)

        if len(unique_pool) == 1:
            chart, cl, cf = unique_pool[0]
            if _name_similar(cl or "", last, 0.7):
                return (chart, "dob_unique")
        elif len(unique_pool) > 1:
            tight = []
            for chart, cl, cf in unique_pool:
                cl = (cl or "").lower().strip()
                cf = (cf or "").lower().strip()
                fl = last.lower().strip()
                ff = first.lower().strip() if first else ""
                if _name_similar(cl, fl, 0.85):
                    if not ff:
                        tight.append(chart)
                    elif cf.startswith(ff[:1]) and (
                        cf.startswith(ff) or _name_similar(cf, ff, 0.8)
                    ):
                        tight.append(chart)
            tight = list({c for c in tight})
            if len(tight) == 1:
                return (tight[0], "dob+name_fuzzy")

    # Pass B: name exact + DOB year exact (catches MM/DD vs DD/MM swaps)
    if dob and first and last:
        cand_pd = db.query(PatientDirectory).filter(
            PatientDirectory.last_name.ilike(last),
            PatientDirectory.first_name.ilike(first),
        ).all()
        cand_p = db.query(Patient).filter(
            Patient.last_name.ilike(last),
            Patient.first_name.ilike(first),
        ).all()
        pool = [(c.chart_number, c.dob.year if c.dob else None) for c in cand_pd]
        pool += [(c.patient_id, c.date_of_birth.year if c.date_of_birth else None) for c in cand_p]
        same_year_charts = list({chart for chart, yr in pool if yr == dob.year})
        if len(same_year_charts) == 1:
            return (same_year_charts[0], "name+year")

    # Pass C: gmail-normalized email across both tables
    if email:
        norm = _normalize_gmail(email)
        all_pd = db.query(PatientDirectory).filter(
            PatientDirectory.email.isnot(None)
        ).all()
        all_p = db.query(Patient).filter(Patient.email.isnot(None)).all()
        hits = []
        for p in all_pd:
            if _normalize_gmail(p.email or "") == norm:
                hits.append(p.chart_number)
        for p in all_p:
            if _normalize_gmail(p.email or "") == norm:
                hits.append(p.patient_id)
        hits = list(set(hits))
        if len(hits) == 1:
            return (hits[0], "email_normalized")

    return None


def import_form_responses_xlsx(db: Session, path: str,
                                sheet_name: str = "Form Responses",
                                ) -> FormResponseImportResult:
    """Import an unsubscribe-form-responses XLSX. Multi-stage matching:
        1) name (first + last) + DOB
        2) email
        3) phone (if file has it)
    Patients matched anywhere → added to recall_suppressions.

    Handles the column-shift quirk in this specific file where the
    'Email' header column actually contains DOB, and 'Unnamed: 4'
    contains the real email."""
    result = FormResponseImportResult()
    df = pd.read_excel(path, sheet_name=sheet_name, dtype=str)
    result.total_rows = len(df)

    # Detect column-shift quirk: if 'Email' values look like dates and
    # 'Unnamed: 4' looks like emails, swap their meanings
    cols = list(df.columns)
    email_col = "Email" if "Email" in cols else None
    real_email_col = email_col
    real_dob_col = "DOB" if "DOB" in cols else None
    if email_col and "Unnamed: 4" in cols:
        # Sniff a few values
        sample = df[email_col].dropna().head(5).astype(str).tolist()
        if sample and all(("-" in s and ":" in s) or s[:4].isdigit() for s in sample):
            # Email column actually holds DOB
            real_dob_col = email_col
            real_email_col = "Unnamed: 4"
    phone_col = next((c for c in cols if c.lower() in ("phone", "phone number", "cell phone")), None)

    suppressed = {s.chart_number for s in db.query(RecallSuppression).all()}

    for idx, row in df.iterrows():
        try:
            first = _str(row.get("First Name")) or ""
            last = _str(row.get("Last Name")) or ""
            dob = _parse_date(row.get(real_dob_col)) if real_dob_col else None
            email = _str(row.get(real_email_col)) if real_email_col else None
            phone = _str(row.get(phone_col)) if phone_col else None
            reason_raw = _str(row.get("Reason"))

            chart = None
            matched_via = None
            if first and last:
                chart = _resolve_chart_by_name_dob(db, first, last, dob)
                if chart: matched_via = "dob"
            if not chart and email:
                chart = _resolve_chart_by_email(db, email)
                if chart: matched_via = "email"
            if not chart and phone:
                chart = _resolve_chart_by_phone(db, phone)
                if chart: matched_via = "phone"

            if not chart:
                result.unmatched += 1
                continue

            if chart in suppressed:
                result.already_suppressed += 1
                continue

            reason = _classify_reason(reason_raw)
            db.add(RecallSuppression(
                chart_number=chart,
                reason=reason,
                notes=f"From XLSX Form Responses (matched via {matched_via}) — '{reason_raw or ''}'",
                created_by="system:form_responses",
            ))
            suppressed.add(chart)
            for e in db.query(RecallEntry).filter_by(chart_number=chart).all():
                e.status = "suppressed"

            if matched_via == "dob": result.suppressed_via_dob += 1
            elif matched_via == "email": result.suppressed_via_email += 1
            elif matched_via == "phone": result.suppressed_via_phone += 1

        except Exception as exc:
            result.errors.append(f"row {idx}: {exc}")

    db.commit()
    return result


def fuzzy_rematch_form_responses(db: Session, path: str,
                                   sheet_name: str = "Form Responses",
                                   ) -> FormResponseImportResult:
    """Second-pass importer using fuzzy matching for rows that exact match
    couldn't resolve. Only suppresses matches that pass conservative
    confidence checks (see fuzzy_match_chart)."""
    result = FormResponseImportResult()
    df = pd.read_excel(path, sheet_name=sheet_name, dtype=str)
    result.total_rows = len(df)

    cols = list(df.columns)
    email_col = "Email" if "Email" in cols else None
    real_email_col = email_col
    real_dob_col = "DOB" if "DOB" in cols else None
    if email_col and "Unnamed: 4" in cols:
        sample = df[email_col].dropna().head(5).astype(str).tolist()
        if sample and all(("-" in s and ":" in s) or s[:4].isdigit() for s in sample):
            real_dob_col = email_col
            real_email_col = "Unnamed: 4"

    suppressed = {s.chart_number for s in db.query(RecallSuppression).all()}

    matched_paths: Dict[str, int] = {}

    for idx, row in df.iterrows():
        try:
            first = _str(row.get("First Name")) or ""
            last = _str(row.get("Last Name")) or ""
            dob = _parse_date(row.get(real_dob_col)) if real_dob_col else None
            email = _str(row.get(real_email_col)) if real_email_col else None
            reason_raw = _str(row.get("Reason"))

            # First do exact passes (cheap) — skip if already suppressed
            chart = None
            if first and last:
                chart = _resolve_chart_by_name_dob(db, first, last, dob)
            if not chart and email:
                chart = _resolve_chart_by_email(db, email)

            if chart:
                # Already covered by exact match — skip (this is a re-run pass)
                if chart in suppressed:
                    result.already_suppressed += 1
                continue

            # Fuzzy fallback
            fz = fuzzy_match_chart(db, first, last, dob, email, None)
            if not fz:
                result.unmatched += 1
                continue

            chart, path_label = fz
            matched_paths[path_label] = matched_paths.get(path_label, 0) + 1

            if chart in suppressed:
                result.already_suppressed += 1
                continue

            reason = _classify_reason(reason_raw)
            db.add(RecallSuppression(
                chart_number=chart,
                reason=reason,
                notes=f"Fuzzy-matched ({path_label}) — '{reason_raw or ''}'",
                created_by="system:fuzzy_match",
            ))
            suppressed.add(chart)
            for e in db.query(RecallEntry).filter_by(chart_number=chart).all():
                e.status = "suppressed"

            if path_label.startswith("dob"):
                result.suppressed_via_dob += 1
            elif "email" in path_label:
                result.suppressed_via_email += 1
        except Exception as exc:
            result.errors.append(f"row {idx}: {exc}")

    db.commit()
    # Stash a breakdown of paths used
    result.errors.append(f"PATHS_USED: {matched_paths}")
    return result


def import_xlsx(db: Session, path: str) -> XlsxImportResult:
    result = XlsxImportResult()
    xls = pd.ExcelFile(path)
    sheets = set(xls.sheet_names)

    suppressed = {s.chart_number for s in db.query(RecallSuppression).all()}

    # ─── PastWWE — backfill last_visit ─────────────────────────────────
    if "PastWWE" in sheets:
        df = pd.read_excel(path, sheet_name="PastWWE", dtype=str)
        # Pre-load existing recall entries by chart for fast updates
        entries_by_chart: Dict[str, List[RecallEntry]] = {}
        for e in db.query(RecallEntry).all():
            entries_by_chart.setdefault(e.chart_number, []).append(e)

        for _, row in df.iterrows():
            chart = _str(row.get("PatientID"))
            dt = _parse_date(row.get("PastWWE"))
            if not chart or not dt:
                continue
            entries = entries_by_chart.get(chart)
            if not entries:
                continue
            for e in entries:
                # Only set if missing or older than the new value
                if e.last_visit is None or e.last_visit < dt:
                    e.last_visit = dt
                    result.past_wwe_updated += 1
        db.commit()

    # ─── FutureWWE — mark as completed (has upcoming appt) ─────────────
    if "FutureWWE" in sheets:
        df = pd.read_excel(path, sheet_name="FutureWWE", dtype=str)
        for _, row in df.iterrows():
            chart = _str(row.get("PatientID"))
            if not chart:
                continue
            entries = db.query(RecallEntry).filter_by(chart_number=chart).all()
            if not entries:
                continue
            for e in entries:
                if e.status == "active":
                    e.status = "completed"
                    appt_type = _str(row.get("Appt Type")) or "scheduled"
                    e.latest_comment = (
                        (e.latest_comment or "")
                        + f"\n[XLSX import] Future {appt_type} on file."
                    ).strip()
                    result.future_wwe_marked += 1
        db.commit()

    # ─── UnScubscribed (sic) — suppress ────────────────────────────────
    if "UnScubscribed" in sheets:
        df = pd.read_excel(path, sheet_name="UnScubscribed", dtype=str)
        for _, row in df.iterrows():
            first = _str(row.get("First Name")) or ""
            last = _str(row.get("Last Name")) or ""
            dob = _parse_date(row.get("DOB"))
            chart = _resolve_chart_by_name_dob(db, first, last, dob)
            if not chart:
                result.skipped_no_match += 1
                result.errors.append(
                    f"UnScubscribed: no chart for {last}, {first} dob={dob}"
                )
                continue
            if _suppress(db, chart, "unsubscribed",
                         notes=f"From XLSX UnScubscribed tab",
                         already=suppressed):
                result.unsubscribed_added += 1
        db.commit()

    # ─── DoNotCall — suppress ──────────────────────────────────────────
    if "DoNotCall" in sheets:
        df = pd.read_excel(path, sheet_name="DoNotCall", dtype=str)
        for _, row in df.iterrows():
            chart = _str(row.get("PatientID"))
            # PatientID may be present; if missing, try name+DOB lookup
            if not chart:
                first = _str(row.get("First Name")) or ""
                last = _str(row.get("Last Name")) or ""
                dob = _parse_date(row.get("DOB"))
                chart = _resolve_chart_by_name_dob(db, first, last, dob)
            if not chart:
                result.skipped_no_match += 1
                result.errors.append(
                    f"DoNotCall: no chart for "
                    f"{_str(row.get('Last Name'))}, {_str(row.get('First Name'))}"
                )
                continue
            if _suppress(db, chart, "do_not_call",
                         notes=f"From XLSX DoNotCall tab",
                         already=suppressed):
                result.do_not_call_added += 1
        db.commit()

    return result
