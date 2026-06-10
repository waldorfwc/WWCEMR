"""AI-driven billing-code suggestion for surgeries.

Reads the saved operative-note + pathology-report PDFs, sends their text
to Claude with a structured ICD-10 / CPT / modifier / POS prompt, and
auto-saves the returned codes to the Surgery row.

If any CPT comes back with modifier "22" (increased procedural services),
we additionally generate a justification letter PDF and save it as a
SurgeryFile of kind="modifier_22_letter" so billing can attach it to
the claim submission.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime
from app.utils.dt import now_utc_naive
from pathlib import Path
from typing import Optional

import pdfplumber
from sqlalchemy.orm import Session

from app.config import settings
from app.models.surgery import Surgery, SurgeryFile

log = logging.getLogger(__name__)


class BillingAIError(Exception):
    pass


def _read_pdf_text(path: str) -> str:
    if not path or not Path(path).exists():
        return ""
    try:
        with pdfplumber.open(path) as pdf:
            pages = [p.extract_text() or "" for p in pdf.pages]
        return "\n\n".join(pages).strip()
    except Exception as exc:
        log.warning("Could not extract text from %s: %s", path, exc)
        return ""


def _collect_report_text(s: Surgery) -> tuple[str, str]:
    """Returns (op_note_text, path_report_text). Empty strings if missing."""
    op_text = ""
    path_text = ""
    for f in (s.files or []):
        if f.kind == "op_notes" and not op_text:
            op_text = _read_pdf_text(f.path)
        elif f.kind == "path_report" and not path_text:
            path_text = _read_pdf_text(f.path)
    return op_text, path_text


def _build_prompt(s: Surgery, op_text: str, path_text: str) -> str:
    procs = s.procedures or []
    proc_summary = ", ".join(p.get("description", "") for p in procs if isinstance(p, dict)) or "(unspecified)"
    payer = s.primary_insurance or "(unknown payer)"

    op_block = op_text or "(no operative note on file)"
    path_block = path_text or "(no pathology report on file)"

    return f"""You are a certified medical biller for a women's health (OB/GYN) practice. Generate the billing codes that should be submitted on this surgery's insurance claim, based on the operative note and pathology report.

PATIENT-SCHEDULED PROCEDURES (what was ordered): {proc_summary}
PRIMARY PAYER: {payer}
FACILITY: {s.selected_facility or '(unspecified)'}

OPERATIVE NOTE:
{op_block}

PATHOLOGY REPORT:
{path_block}

INSTRUCTIONS:
- Code based on what was ACTUALLY DONE in the operative note, not just what was scheduled.
- Pull ICD-10 diagnosis codes from BOTH the op note's preop/postop diagnoses AND any new diagnoses confirmed by pathology (e.g. fibroid → leiomyoma after path).
- For each CPT, include modifier(s) and Place of Service (POS) code.
- Common POS: 22 = on-campus outpatient hospital, 24 = ASC, 11 = office.
- Use modifier 22 (increased procedural services) ONLY when the operative note clearly documents significantly greater complexity/time/effort than typical (large fibroids, dense adhesions, extensive lysis, much longer than typical op time, etc.). If you use 22, populate "rationale_22" with a brief justification grounded in what the op note says.
- Use modifier 51 (multiple procedures) on the lower-RVU CPT when 2+ surgical CPTs are billed in the same session.
- Use modifier 59 when a procedure normally bundled is independently billable here.
- Use bilateral modifier 50 when applicable.

OUTPUT FORMAT — return ONLY a single JSON object, no prose, matching this schema:
{{
  "icd10": [
    {{"code": "N80.0", "description": "Endometriosis of uterus"}}
  ],
  "cpt": [
    {{"code": "58571", "modifier": "22", "pos": "22", "units": 1, "description": "Laparoscopy w/ total hysterectomy", "rationale_22": "Op note documents 4h 22min operative time (typical 2h) due to dense adhesions"}}
  ],
  "notes": "Brief rationale for the overall coding choice — 1-3 sentences."
}}

Return the JSON only — no markdown fences, no commentary."""


def _call_claude(prompt: str) -> dict:
    api_key = getattr(settings, "anthropic_api_key", None) or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise BillingAIError("ANTHROPIC_API_KEY not configured.")
    try:
        from anthropic import Anthropic
    except Exception as exc:
        raise BillingAIError(f"Anthropic SDK not installed: {exc}")

    client = Anthropic(api_key=api_key)
    msg = client.messages.create(
        model="claude-opus-4-7",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )
    text = msg.content[0].text if msg.content else ""

    # Strip markdown fences if Claude wrapped them despite the instruction
    text = re.sub(r"^```(?:json)?\s*", "", text.strip())
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise BillingAIError(f"Claude returned non-JSON: {text[:400]}…") from exc


def suggest_and_save_billing(db: Session, s: Surgery, *, saved_by: str) -> dict:
    """Pulls op + path reports, asks Claude for codes, persists them on the
    Surgery row, and (if modifier 22 was used) writes a justification letter
    to the file system as a SurgeryFile."""
    op_text, path_text = _collect_report_text(s)
    if not op_text and not path_text:
        raise BillingAIError(
            "No operative note or pathology report has been uploaded yet. "
            "Upload them first, then click Suggest codes again."
        )

    prompt = _build_prompt(s, op_text, path_text)
    payload = _call_claude(prompt)

    icd10 = payload.get("icd10") or []
    cpts = payload.get("cpt") or []

    s.billed_icd10_codes = icd10
    s.billed_cpt_codes = cpts
    s.billed_at = now_utc_naive()
    s.billed_by = saved_by
    s.billing_ai_notes = payload.get("notes") or None
    db.commit()

    # If any modifier-22 CPT, write the justification letter.
    mod22 = [c for c in cpts if (c.get("modifier") or "").strip() == "22"]
    if mod22:
        try:
            from app.services.modifier_22_letter import generate_modifier_22_letter
            generate_modifier_22_letter(db, s, mod22)
        except Exception as exc:
            log.exception("Modifier-22 letter generation failed: %s", exc)
            # Don't fail the whole suggestion if letter PDF gen fails

    return {
        "icd10": icd10,
        "cpt": cpts,
        "ai_notes": s.billing_ai_notes,
        "modifier_22_letter_written": bool(mod22),
    }
