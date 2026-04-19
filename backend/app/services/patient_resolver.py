"""
Patient resolver — builds an authoritative chart_number -> (name, dob) mapping
by extracting text from Phreesia Demographic PDFs, then matches intake documents
(which only have name+DOB from folder paths) back to chart numbers.
"""

import os
import re
import glob
from datetime import date, datetime
from typing import Optional, Tuple, List, Dict
from dataclasses import dataclass

import pdfplumber
from sqlalchemy.orm import Session

from app.models.patient_directory import PatientDirectory, IntakeDocument
from app.config import settings


# ── Patient field extraction (multiple formats) ───────────────────────────────

# Phreesia format
_PHREESIA_NAME = re.compile(r"Patient Name\s+([A-Za-z][A-Za-z' \-\.]+?)(?:\s{2,}|$)", re.MULTILINE)
_PHREESIA_DOB = re.compile(r"Patient Date of Birth\s+(\d{2}/\d{2}/\d{4})")

# Progress Note / Chart Note format: "Patient Name: Tina Johnson"
_PROGRESS_NAME = re.compile(r"Patient Name:\s*([A-Za-z][A-Za-z' \-\.]+?)(?:\s{2,}|\s*Visit|\s*Date|$)", re.MULTILINE)
_PROGRESS_DOB_MONTH = re.compile(
    r"(?:Birthdate|Date of Birth|Birth Date):\s*([A-Z][a-z]+ \d{1,2},?\s*\d{4})"
)
_PROGRESS_DOB_NUMERIC = re.compile(r"(?:Birthdate|Date of Birth|Birth Date):\s*(\d{1,2}/\d{1,2}/\d{4})")

# Lab format: "Name: Johnson, Tina" and "DOB: 04/06/1963"
_LAB_NAME_REVERSED = re.compile(r"(?:Patient )?Name:\s*([A-Za-z][A-Za-z' \-]+?),\s*([A-Za-z][A-Za-z' \-]+)")
_GENERIC_DOB = re.compile(r"DOB:?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})")
_GENERIC_DOB_MONTH = re.compile(r"DOB:?\s*([A-Z][a-z]+ \d{1,2},?\s*\d{4})")

# Other fields
_GENDER_RE = re.compile(r"(?:Patient )?(?:Gender|Sex):?\s*(Male|Female|[MF])\b", re.IGNORECASE)
_ADDRESS_RE = re.compile(r"Street Address\s+(.+?)(?:\s{2,}|\n)")
_PHONE_RE = re.compile(r"(?:Home|Cell) Phone (?:Number\s+)?(\d{3}[-\.\s]?\d{3}[-\.\s]?\d{4})")

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}


def _parse_dob(raw: str) -> Optional[date]:
    """Parse MM/DD/YYYY, M/D/YY, or 'Month DD, YYYY' into a date."""
    raw = raw.strip()
    # Month name format
    m = re.match(r"([A-Za-z]+)\s+(\d{1,2}),?\s*(\d{4})", raw)
    if m:
        month_name = m.group(1).lower()
        if month_name in _MONTHS:
            try:
                return date(int(m.group(3)), _MONTHS[month_name], int(m.group(2)))
            except ValueError:
                return None
    # Numeric format
    parts = re.split(r"[/-]", raw)
    if len(parts) != 3:
        return None
    try:
        mo, d, y = int(parts[0]), int(parts[1]), int(parts[2])
        if y < 100:  # 2-digit year
            y = 1900 + y if y > 30 else 2000 + y
        return date(y, mo, d)
    except (ValueError, IndexError):
        return None


def extract_demographics(pdf_path: str) -> Optional[Dict]:
    """
    Extract name + DOB from any PDF that contains readable text.
    Tries multiple format patterns: Phreesia, Progress Note, Lab, generic.
    """
    try:
        with pdfplumber.open(pdf_path) as pdf:
            if not pdf.pages:
                return None
            text = pdf.pages[0].extract_text() or ""
    except Exception:
        return None

    if not text or len(text) < 40:
        return None

    full_name = None
    dob = None

    # ── Try Phreesia format (most structured) ─────────────────────────────
    name_match = _PHREESIA_NAME.search(text)
    if name_match:
        full_name = name_match.group(1).strip()
    dob_match = _PHREESIA_DOB.search(text)
    if dob_match:
        dob = _parse_dob(dob_match.group(1))

    # ── Try Progress Note format ─────────────────────────────────────────
    if not full_name:
        m = _PROGRESS_NAME.search(text)
        if m:
            full_name = m.group(1).strip()
    if not dob:
        m = _PROGRESS_DOB_MONTH.search(text) or _PROGRESS_DOB_NUMERIC.search(text)
        if m:
            dob = _parse_dob(m.group(1))

    # ── Try Lab format (reversed name: "Last, First") ────────────────────
    if not full_name:
        m = _LAB_NAME_REVERSED.search(text)
        if m:
            last, first = m.group(1).strip(), m.group(2).strip()
            full_name = f"{first} {last}"

    # ── Generic DOB fallback ─────────────────────────────────────────────
    if not dob:
        m = _GENERIC_DOB.search(text) or _GENERIC_DOB_MONTH.search(text)
        if m:
            dob = _parse_dob(m.group(1))

    if not full_name or not dob:
        return None

    # Clean up the name — strip common artifacts
    full_name = re.sub(r"\s+", " ", full_name).strip()
    full_name = re.sub(r"[,\.]+$", "", full_name)

    gender_match = _GENDER_RE.search(text)
    address_match = _ADDRESS_RE.search(text)
    phone_match = _PHONE_RE.search(text)

    parts = full_name.split()
    first = parts[0] if parts else None
    last = parts[-1] if len(parts) > 1 else None
    middle = " ".join(parts[1:-1]) if len(parts) > 2 else None

    return {
        "full_name": full_name,
        "first_name": first,
        "middle_name": middle,
        "last_name": last,
        "dob": dob,
        "gender": gender_match.group(1) if gender_match else None,
        "address": address_match.group(1).strip() if address_match else None,
        "phone": phone_match.group(1) if phone_match else None,
    }


def build_patient_directory(db: Session, documents_dir: str = None) -> Dict:
    """
    Walk the PrimeSuite document archive and build chart_number -> (name, dob)
    mapping by reading Phreesia Demographic PDFs (and Billing Facesheets as fallback).
    """
    docs_dir = documents_dir or settings.documents_dir
    if not os.path.isdir(docs_dir):
        return {"error": f"Directory not found: {docs_dir}"}

    # Clear existing directory
    db.query(PatientDirectory).delete()
    db.commit()

    stats = {"total_charts": 0, "resolved": 0, "no_demographics": 0, "errors": 0}
    batch = []

    for chart_number in sorted(os.listdir(docs_dir)):
        chart_dir = os.path.join(docs_dir, chart_number)
        if not os.path.isdir(chart_dir):
            continue
        stats["total_charts"] += 1

        # Candidate doc types ranked by how reliably they contain name+DOB
        # Phreesia is cleanest; newer Progress Notes and Lab reports usually work too.
        type_priorities = [
            "Phreesia Demographic",
            "Billing Facesheet",
            "Demographics Sheet",
            "Progress Note",
            "Inbound Electronic Lab",
            "Lab Results",
            "History and Physical",
            "Procedure Note",
            "Phreesia Clinicals",
            "Orders Note",
            "Chart Note",
            "Imaging Ultrasound",
            "Imaging Mammography",
            "Pathology Report",
            "Pathology Results",
        ]

        candidates = []
        for t in type_priorities:
            # Prefer newer files — sort reverse so recent come first
            matches = sorted(glob.glob(os.path.join(chart_dir, f"{t}*.pdf")), reverse=True)
            candidates.extend(matches[:3])  # Take up to 3 newest of each type

        if not candidates:
            stats["no_demographics"] += 1
            continue

        # Try candidates in order until one yields data
        info = None
        used_file = None
        for c in candidates:
            try:
                info = extract_demographics(c)
                if info and info.get("full_name") and info.get("dob"):
                    used_file = c
                    break
            except Exception:
                stats["errors"] += 1
                continue

        if not info:
            stats["no_demographics"] += 1
            continue

        batch.append(PatientDirectory(
            chart_number=chart_number,
            patient_name=info["full_name"],
            first_name=info["first_name"],
            last_name=info["last_name"],
            middle_name=info["middle_name"],
            dob=info["dob"],
            gender=info.get("gender"),
            address=info.get("address"),
            phone=info.get("phone"),
            source_file=os.path.basename(used_file) if used_file else None,
        ))
        stats["resolved"] += 1

        if len(batch) >= 200:
            db.bulk_save_objects(batch)
            db.commit()
            batch = []

    if batch:
        db.bulk_save_objects(batch)
        db.commit()

    return stats


# ── Intake document indexing ──────────────────────────────────────────────────

_PATIENT_FOLDER_RE = re.compile(r"^(.+?)\s+(\d{2})-(\d{2})-(\d{4})$")


def _parse_patient_folder(folder_name: str) -> Optional[Tuple[str, date]]:
    """Parse '{Name} MM-DD-YYYY' into (name, dob)."""
    m = _PATIENT_FOLDER_RE.match(folder_name.strip())
    if not m:
        return None
    name = m.group(1).strip()
    try:
        dob = date(int(m.group(4)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None
    return (name, dob)


def _extract_doc_year(category: str) -> Optional[int]:
    """Extract year from category name like '2025 - ID&Insurance'."""
    m = re.match(r"^(\d{4})\s*[-–]", category)
    return int(m.group(1)) if m else None


def index_intake_documents(db: Session, intake_dir: str) -> Dict:
    """
    Walk an extracted intake archive directory and index every file.
    Expected structure: {intake_dir}/{yob}/{mm}/{dd}/{Name MM-DD-YYYY}/{category}/{file}
    """
    if not os.path.isdir(intake_dir):
        return {"error": f"Intake directory not found: {intake_dir}"}

    # Clear existing intake documents
    db.query(IntakeDocument).delete()
    db.commit()

    stats = {"total_files": 0, "indexed": 0, "skipped": 0}
    batch = []

    for root, dirs, files in os.walk(intake_dir):
        for fname in files:
            stats["total_files"] += 1
            if fname.startswith("."):
                continue

            full_path = os.path.join(root, fname)
            rel_path = os.path.relpath(full_path, intake_dir)
            parts = rel_path.split(os.sep)

            # Find the patient folder — should match "{Name} MM-DD-YYYY"
            patient_folder = None
            patient_folder_idx = -1
            for i, p in enumerate(parts):
                if _PATIENT_FOLDER_RE.match(p.strip()):
                    patient_folder = p.strip()
                    patient_folder_idx = i
                    break

            if not patient_folder:
                stats["skipped"] += 1
                continue

            parsed = _parse_patient_folder(patient_folder)
            if not parsed:
                stats["skipped"] += 1
                continue
            name, dob = parsed

            # Category = folder immediately after patient folder, if present
            category = None
            if patient_folder_idx + 1 < len(parts) - 1:
                category = parts[patient_folder_idx + 1]
            doc_year = _extract_doc_year(category) if category else None

            try:
                size_kb = os.path.getsize(full_path) // 1024
            except OSError:
                size_kb = 0

            ext = os.path.splitext(fname)[1].lower().lstrip(".")

            batch.append(IntakeDocument(
                patient_name_raw=name,
                dob=dob,
                doc_category=category,
                doc_year=doc_year,
                filename=fname,
                file_path=full_path,
                file_size_kb=size_kb,
                file_type=ext,
                match_confidence="pending",
            ))
            stats["indexed"] += 1

            if len(batch) >= 200:
                db.bulk_save_objects(batch)
                db.commit()
                batch = []

    if batch:
        db.bulk_save_objects(batch)
        db.commit()

    return stats


# ── Matching ──────────────────────────────────────────────────────────────────

def _normalize_name(name: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    if not name:
        return ""
    n = re.sub(r"[^a-z\s]", " ", name.lower())
    return " ".join(n.split())


def _name_tokens(name: str) -> set:
    return set(_normalize_name(name).split())


def _name_match_score(a: str, b: str) -> float:
    """
    0.0 - 1.0 score based on token overlap.
    Handles 'Aneka Donelson' vs 'Aneka Hylton Donelson' (both contain Aneka, Donelson).
    """
    ta, tb = _name_tokens(a), _name_tokens(b)
    if not ta or not tb:
        return 0.0
    if ta == tb:
        return 1.0
    # Jaccard similarity
    return len(ta & tb) / len(ta | tb)


def match_intake_to_charts(db: Session) -> Dict:
    """
    For each intake document, find the best-matching chart number
    from patient_directory. Store match result + confidence.
    """
    directory = db.query(PatientDirectory).filter(PatientDirectory.dob.isnot(None)).all()
    if not directory:
        return {"error": "Patient directory is empty — run /api/intake/build-directory first"}

    # Index directory by DOB
    by_dob: Dict[date, List[PatientDirectory]] = {}
    for p in directory:
        by_dob.setdefault(p.dob, []).append(p)

    intake_docs = db.query(IntakeDocument).all()

    stats = {
        "total": len(intake_docs),
        "exact": 0,
        "fuzzy_high": 0,
        "fuzzy_low": 0,
        "dob_no_name": 0,
        "unmatched": 0,
    }

    for doc in intake_docs:
        candidates = by_dob.get(doc.dob, [])
        if not candidates:
            doc.match_confidence = "unmatched"
            doc.match_score = 0.0
            doc.matched_chart_number = None
            stats["unmatched"] += 1
            continue

        best_score = 0.0
        best_chart = None
        for cand in candidates:
            score = _name_match_score(doc.patient_name_raw, cand.patient_name or "")
            if score > best_score:
                best_score = score
                best_chart = cand.chart_number

        doc.matched_chart_number = best_chart
        doc.match_score = round(best_score, 3)

        if best_score >= 0.99:
            doc.match_confidence = "exact"
            stats["exact"] += 1
        elif best_score >= 0.75:
            doc.match_confidence = "fuzzy_high"
            stats["fuzzy_high"] += 1
        elif best_score >= 0.5:
            doc.match_confidence = "fuzzy_low"
            stats["fuzzy_low"] += 1
        else:
            # DOB matched but name is very different — flag for review
            doc.match_confidence = "dob_no_name"
            stats["dob_no_name"] += 1

    db.commit()
    return stats
