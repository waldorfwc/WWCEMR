"""Parse a uploaded ModMed surgery-order PDF into structured fields.

Strategy:
  1. Pull text out of the PDF with pdfplumber
  2. Send the text to Claude with a JSON-schema-ish prompt
  3. Validate + coerce the response
  4. Fall back to manual entry if anything fails

ModMed orders are remarkably consistent in their layout (see Wade,
Fowler, Arigbabu samples), so the parser is mostly stable. Variations
land in the free-text `Procedure(s)` and `Special OR Equipment` blocks,
which is exactly what an LLM is good at handling.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

import anthropic
import pdfplumber

from app.config import settings

log = logging.getLogger(__name__)


PARSE_MODEL = "claude-opus-4-7"  # high-accuracy structured extraction


SYSTEM_PROMPT = """You parse OB/GYN surgery-order PDFs from a ModMed/EMA practice into structured JSON.

Rules:
- Output ONLY a valid JSON object. No prose, no Markdown fences.
- If a field is not present in the order, use null (not an empty string).
- For procedures, return one entry per CPT-coded procedure listed; preserve order.
- "Robotic" detection: true iff the order says "Robot is required", "robotic", or includes a robotic CPT (58545, 58571–58575).
- "Eligible facilities" must be a list of any of: "medstar", "crmc", "office".
  - "MSMHC" / "MedStar Southern Maryland" → medstar
  - "CRMC" / "Charles Regional" / "Facility 1 (CRMC)" → crmc
  - "White Plains Office" / "Waldorf Office" → office
  - If multiple are listed (e.g. "CRMC, MSMHC"), include all.
- "estimated_minutes": parse "240 minutes" → 240. If not stated, null.
- "is_office_procedure": true when the document title contains "In-Office" OR the only eligible facility is "office".
- Extract clearance_required only if the order explicitly says clearance/medical clearance is needed (rarely stated; usually null).
- From the demographics header, pull the patient mailing "address" (street, city, state, zip into address_street/address_city/address_state/address_zip), the patient "phone" (cell phone), and the patient "email" when present.
- For insurance_primary, pull the insurance "company" name, the "member_id", and the "payer_id". The payer_id is the numeric electronic/claims Payer ID — often labeled "Payer ID", "Payer #", or shown in parentheses after the company name, e.g. "BCBS Administrators PPO ONLY (75191)" → company "BCBS Administrators PPO ONLY", payer_id "75191". When the company name has a trailing "(NNNNN)" numeric token, that number IS the payer_id; put it in payer_id and DROP it from company. It is distinct from the member/subscriber ID. Use null if not present.
- "procedure_type": the overall surgical procedure name/title as stated on the order (e.g. "Total Laparoscopic Hysterectomy"). This is the headline procedure, not a CPT-level line item.
- "ordered_at": the document's order/creation date — the date the order was written/generated.
"""


JSON_SHAPE_HINT = """Return JSON with this exact shape (use null for any missing value):

{
  "patient": {
    "last_name": "...",
    "first_name": "...",
    "middle_initial": null,
    "dob": "YYYY-MM-DD",
    "sex": "Female",
    "mrn": "...",
    "phone": "...",
    "email": "..." ,
    "address_street": "...",
    "address_city": "...",
    "address_state": "MD",
    "address_zip": "..."
  },
  "insurance_primary": {
    "subscriber_name": "...",
    "relationship": "Self",
    "company": "...",
    "member_id": "...",
    "payer_id": "...",  // numeric Payer ID, e.g. "75191"; if shown as "Company (75191)", set payer_id "75191" and company "Company"
    "group": "..."
  },
  "diagnoses": [
    {"icd": "D25.1", "description": "Intramural leiomyoma of uterus"}
  ],
  "procedures": [
    {"cpt": "58573", "description": "Total laparoscopic hysterectomy..."}
  ],
  "procedure_type": "Total Laparoscopic Hysterectomy",
  "surgeon_primary": "Aryian Cooke",
  "anesthesia": "general",
  "estimated_minutes": 240,
  "is_robotic": true,
  "is_office_procedure": false,
  "eligible_facilities": ["medstar"],
  "special_equipment": "Robot is required",
  "labs_required": ["CBC", "Serum pregnancy test"],
  "clearance_required": null,
  "consent_status_text": "surgical consent needs to be signed",
  "post_op_followup_weeks": 2,
  "priority": "normal",
  "ordered_by": "Aryian Cooke",
  "ordered_at": "2026-05-05T21:24:00"
}
"""


def extract_pdf_text_from_bytes(body: bytes) -> str:
    """Extract text from a ModMed surgery-order PDF given its raw bytes."""
    import io
    out = []
    with pdfplumber.open(io.BytesIO(body)) as pdf:
        for page in pdf.pages:
            t = page.extract_text() or ""
            if t.strip():
                out.append(t)
    return "\n\n".join(out)


def extract_pdf_text(path: str) -> str:
    """Path-based wrapper around extract_pdf_text_from_bytes. Kept for any
    callers that still have a filesystem path; new code should pass bytes."""
    with open(path, "rb") as f:
        return extract_pdf_text_from_bytes(f.read())


def parse_order_pdf_bytes_direct(body: bytes) -> dict:
    """Send the PDF directly to Claude (no pdfplumber text extraction) for
    scanned image-only PDFs where text extraction returns nothing. Claude's
    document content block accepts PDFs natively (≤32 MB / ≤100 pages) and
    OCRs scanned content visually."""
    import base64
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not configured")
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    pdf_b64 = base64.standard_b64encode(body).decode("utf-8")
    user_prompt = (
        "Parse this surgery order PDF. Return JSON exactly matching the shape below.\n\n"
        f"{JSON_SHAPE_HINT}\n"
    )
    resp = client.messages.create(
        model=PARSE_MODEL,
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": pdf_b64,
                    },
                },
                {"type": "text", "text": user_prompt},
            ],
        }],
    )
    raw = resp.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Claude returned non-JSON: {exc}\n\nRaw: {raw[:500]}")
    return _validate_and_coerce(data)


def parse_order_pdf_direct(pdf_path: str) -> dict:
    """Path-based wrapper around parse_order_pdf_bytes_direct."""
    with open(pdf_path, "rb") as f:
        return parse_order_pdf_bytes_direct(f.read())


def parse_order_pdf(path: str) -> dict:
    """Parse a single PDF order. Returns the structured dict (or raises)."""
    text = extract_pdf_text(path)
    if not text or len(text) < 100:
        raise ValueError("PDF appears empty or unreadable")
    return parse_order_text(text)


def parse_order_text(text: str) -> dict:
    """Send the order text to Claude, return the validated dict."""
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not configured")

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    user_prompt = (
        "Parse this surgery order. Return JSON exactly matching the shape below.\n\n"
        f"{JSON_SHAPE_HINT}\n\n"
        "=== ORDER TEXT ===\n"
        f"{text}\n"
        "=== END ===\n"
    )
    resp = client.messages.create(
        model=PARSE_MODEL,
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    raw = resp.content[0].text.strip()
    # Strip code fences if Claude included them
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Claude returned non-JSON: {exc}\n\nRaw: {raw[:500]}")

    return _validate_and_coerce(data)


def _validate_and_coerce(data: dict) -> dict:
    """Light validation + type coercion on the LLM output."""
    if not isinstance(data, dict):
        raise ValueError("Top-level result is not an object")

    p = data.get("patient") or {}
    if not (p.get("last_name") and p.get("first_name")):
        raise ValueError("patient last/first name missing")

    eligibles = data.get("eligible_facilities") or []
    if not isinstance(eligibles, list):
        raise ValueError("eligible_facilities must be a list")
    valid = {"medstar", "crmc", "office"}
    eligibles = [f for f in eligibles if f in valid]
    data["eligible_facilities"] = eligibles

    procs = data.get("procedures") or []
    if not isinstance(procs, list):
        procs = []
    # Normalize each procedure
    cleaned = []
    for p in procs:
        if not isinstance(p, dict):
            continue
        cleaned.append({
            "cpt": str(p.get("cpt")) if p.get("cpt") else None,
            "description": (p.get("description") or "").strip() or None,
        })
    data["procedures"] = cleaned

    dxs = data.get("diagnoses") or []
    cleaned_dxs = []
    for d in dxs:
        if not isinstance(d, dict):
            continue
        cleaned_dxs.append({
            "icd": (d.get("icd") or "").strip() or None,
            "description": (d.get("description") or "").strip() or None,
        })
    data["diagnoses"] = cleaned_dxs

    # estimated_minutes — coerce to int
    em = data.get("estimated_minutes")
    if isinstance(em, str):
        m = re.search(r"\d+", em)
        data["estimated_minutes"] = int(m.group()) if m else None
    elif em is not None:
        data["estimated_minutes"] = int(em)

    # bool coerce
    for k in ("is_robotic", "is_office_procedure"):
        if k in data:
            data[k] = bool(data[k])

    # insurance_primary: split a trailing/parenthetical payer ID out of the
    # company name. Surgery-order PDFs render the company as e.g.
    # "BCBS Administrators PPO ONLY (75191)" where 75191 is the electronic
    # payer ID. The dropdown company never matches that string, so we lift
    # the (NNNNN) into payer_id (only if not already set) and strip it from
    # the company so the name can be resolved against the picklist.
    ins = data.get("insurance_primary")
    if isinstance(ins, dict):
        company = ins.get("company")
        if isinstance(company, str) and company:
            m = re.search(r"\(\s*(\d{3,6})\s*\)", company)
            if m:
                if not ins.get("payer_id"):
                    ins["payer_id"] = m.group(1)
                stripped = re.sub(r"\s*\(\s*\d{3,6}\s*\)\s*", " ", company).strip()
                ins["company"] = stripped or None

    return data


# ─── Surgery row creation from parsed order ───────────────────────

def build_surgery_kwargs(parsed: dict) -> dict:
    """Convert the parsed-order dict into kwargs ready to construct a
    Surgery row. Returns a dict that the caller can splat into Surgery()."""
    p = parsed.get("patient", {}) or {}
    ins = parsed.get("insurance_primary", {}) or {}
    procs = parsed.get("procedures", []) or []
    eligible = parsed.get("eligible_facilities", []) or []

    last = (p.get("last_name") or "").strip()
    first = (p.get("first_name") or "").strip()
    mi = (p.get("middle_initial") or "").strip()

    name = f"{last}, {first}"
    if mi:
        name += f" {mi}"

    # Selected facility: only set when there's exactly one option
    selected_facility = eligible[0] if len(eligible) == 1 else None

    # Robotic forces medstar
    if parsed.get("is_robotic"):
        if "medstar" not in eligible:
            eligible = ["medstar"]
        selected_facility = "medstar"

    # Procedure classification
    classification = _classify(procs, parsed.get("is_robotic"), selected_facility,
                                parsed.get("estimated_minutes"))

    return dict(
        chart_number=str(p.get("mrn") or "").strip() or None,
        patient_name=name,
        first_name=first or None,
        last_name=last or None,
        dob=_iso_to_date(p.get("dob")),
        sex=p.get("sex"),
        email=p.get("email"),
        phone=_normalize_phone(p.get("phone")),
        cell_phone=_normalize_phone(p.get("phone")),
        address_street=p.get("address_street"),
        address_city=p.get("address_city"),
        address_state=p.get("address_state"),
        address_zip=p.get("address_zip"),

        primary_insurance=ins.get("company"),
        primary_member_id=ins.get("member_id"),
        primary_payer_id=ins.get("payer_id"),
        primary_group=ins.get("group"),

        surgeon_primary=parsed.get("surgeon_primary"),
        anesthesia=parsed.get("anesthesia"),
        estimated_minutes=parsed.get("estimated_minutes"),
        is_robotic=bool(parsed.get("is_robotic")),
        procedure_classification=classification,
        procedures=procs,
        diagnoses=parsed.get("diagnoses"),
        special_equipment_notes=parsed.get("special_equipment"),

        eligible_facilities=eligible,
        selected_facility=selected_facility,

        labs_required=bool(parsed.get("labs_required")),
        labs_required_list=", ".join(parsed.get("labs_required") or []) or None,

        clearance_required=bool(parsed.get("clearance_required")),
        clearance_status="required" if parsed.get("clearance_required") else "not_required",

        notes=parsed.get("special_equipment") or parsed.get("consent_status_text"),

        status="incomplete",   # caller will flip to "new" after admin reviews
        source="upload",
    )


# ─── Helpers ──────────────────────────────────────────────────────

ROBOTIC_CPTS = {"58545", "58571", "58572", "58573", "58574", "58575"}
MAJOR_CPTS   = {"49320", "58146", "58660", "58662", "58550", "58552", "58553", "58554"}
MINOR_CPTS   = {"58558", "58561", "58563", "58555", "57522", "58356", "58100", "58120"}


def _classify(procs: list, is_robotic: bool, facility: Optional[str],
              est_min: Optional[int]) -> str:
    cpts = {(p.get("cpt") or "").strip() for p in procs if p.get("cpt")}
    if is_robotic or (cpts & ROBOTIC_CPTS):
        # Use the estimated time to pick 180 vs 240
        if est_min and est_min >= 240:
            return "robotic_240"
        return "robotic_180"
    if cpts & MAJOR_CPTS:
        return "major"
    if facility == "office":
        return "office"
    return "minor"


def _iso_to_date(s):
    if not s:
        return None
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _normalize_phone(raw):
    if not raw:
        return None
    digits = "".join(c for c in str(raw) if c.isdigit())
    if len(digits) == 10:
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"({digits[1:4]}) {digits[4:7]}-{digits[7:]}"
    return str(raw)
