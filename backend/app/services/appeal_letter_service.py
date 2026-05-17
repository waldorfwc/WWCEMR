"""Appeal letter rendering: token substitution + optional Claude rewrite + PDF."""
from __future__ import annotations

import json
import os
import re
from datetime import date, datetime
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.config import settings
from app.models.active_ar import ActiveClaim, ActiveClaimNote
from app.models.appeal_letters import AppealLetter, PayerAddress
from app.models.practice_config import PracticeConfig
from app.services.appeal_templates import (
    LEVEL_LABEL, get_template_body, make_subject, TEMPLATE_TYPES,
)


# ---------- token rendering ----------

def _claim_tokens(claim: ActiveClaim, practice: dict, signer: dict, level: int,
                  recipient_name: str, recipient_address: str,
                  additional_verbiage: Optional[str]) -> Dict[str, str]:
    today = date.today().strftime("%B %d, %Y")
    level_label = LEVEL_LABEL.get(level, LEVEL_LABEL[1])
    level_label_inline = level_label.lower()

    # Aggregate denial codes + service line CPTs/dx from charge enrichment
    cpts = claim.procedure_codes or "—"
    dxs = claim.diagnosis_codes or "—"
    denial_codes = "—"
    if claim.eob_notes:
        # Try to extract codes from EOB notes (e.g. "CO-50, PR-3")
        m = re.findall(r"\b([A-Z]{2,3}-?\d+)\b", claim.eob_notes)
        if m:
            denial_codes = ", ".join(sorted(set(m)))

    sig_creds = signer.get("credentials") or ""
    sig_creds_line = f", {sig_creds}" if sig_creds else ""

    return {
        "patient_name":          claim.patient_name or "—",
        "patient_dob":           str(claim.patient_dob) if claim.patient_dob else "—",
        "patient_chart_number":  claim.patient_external_id or "—",
        "claim_number":          claim.claim_number,
        "dos":                   str(claim.dos) if claim.dos else "—",
        "billed_amount":         f"${float(claim.total_charges or claim.claim_amount or 0):,.2f}",
        "insurance_balance":     f"${float(claim.insurance_balance or 0):,.2f}",
        "insurance_company":     claim.insurance_company or "—",
        "policy_number":         claim.policy_number or "—",
        "group_number":          "—",   # not yet captured
        "plan_name":             claim.plan_name or "—",
        "cpt_codes":             cpts,
        "diagnosis_codes":       dxs,
        "denial_codes":          denial_codes,
        "today_date":            today,
        "level":                 str(level),
        "level_label":           level_label,
        "level_label_inline":    level_label_inline,
        "practice_name":         practice.get("name", ""),
        "practice_address":      practice.get("address", ""),
        "practice_phone":        practice.get("phone", ""),
        "practice_npi":          practice.get("npi", ""),
        "practice_tax_id":       practice.get("tax_id", ""),
        "recipient_name":        recipient_name,
        "recipient_address":     recipient_address,
        "signer_name":           signer.get("name", ""),
        "signer_credentials":    sig_creds,
        "signer_credentials_line": sig_creds_line,
        "signer_title":          signer.get("title", ""),
        "additional_verbiage":   additional_verbiage or "",
    }


def render_body(template_str: str, tokens: Dict[str, str]) -> str:
    out = template_str
    for k, v in tokens.items():
        out = out.replace(f"{{{{{k}}}}}", str(v))
    return out


# ---------- Claude AI assist ----------

def _claude_rewrite(rendered_body: str, claim: ActiveClaim, template_type: str,
                    level: int) -> Optional[str]:
    """Send the rendered template + claim context to Claude Haiku for a more
    tailored argument paragraph. Returns improved body or None on failure."""
    api_key = getattr(settings, "anthropic_api_key", None) or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        from anthropic import Anthropic
    except Exception:
        return None

    # Build a compact context summary for Claude
    notes_summary = "(no notes)"
    if hasattr(claim, "notes") and claim.notes:
        recent = claim.notes[:5]
        notes_summary = "\n".join(f"  - {n.action_type}: {n.note}" for n in recent)

    service_lines_summary = ""
    if claim.service_lines_json:
        try:
            lines = json.loads(claim.service_lines_json)
            service_lines_summary = "\n".join(
                f"  Line {ln['line']}: CPT {ln.get('cpt')} mod={ln.get('modifiers') or '—'} "
                f"dx={ln.get('dx') or '—'} charge=${ln.get('charge') or 0:.2f}"
                for ln in lines
            )
        except Exception:
            pass

    prompt = f"""You are a billing specialist drafting an insurance appeal letter for a women's health practice (OB/GYN). Below is a template-rendered draft. Rewrite ONLY the argument body section (between the salutation "To Whom It May Concern:" and the closing "Sincerely," — do not change the header block, recipient block, signature block, or enclosures section).

The rewrite should:
- Be specific to this claim (use the actual CPTs, dx codes, denial codes, service line detail)
- Be assertive but professional — not aggressive
- Reference relevant standards (NCCI, LCDs, ACOG guidelines for OB/GYN where applicable)
- Stay 4-6 paragraphs
- End with a clear request to overturn the denial and process for payment

CLAIM CONTEXT:
  Template type: {template_type}
  Appeal level: {level}
  Patient: {claim.patient_name}, DOB {claim.patient_dob}
  DOS: {claim.dos}
  Payer: {claim.insurance_company}
  Billed: ${float(claim.total_charges or 0):,.2f}
  CPTs: {claim.procedure_codes}
  Dx: {claim.diagnosis_codes}
  EOB notes: {claim.eob_notes or '(none)'}
  Recent activity log (most recent first):
{notes_summary}
  Service lines:
{service_lines_summary}

CURRENT TEMPLATE-RENDERED BODY (rewrite the middle argument section only):
{rendered_body}

Return the FULL letter with the argument body rewritten. Preserve all other sections verbatim."""

    try:
        client = Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text
    except Exception:
        return None


# ---------- payer-address lookup ----------

def find_payer_address(db: Session, insurance_company: str) -> Optional[PayerAddress]:
    """Fuzzy match insurance_company against PayerAddress.payer_name."""
    if not insurance_company:
        return None
    # Try exact match first
    exact = db.query(PayerAddress).filter(PayerAddress.payer_name == insurance_company).first()
    if exact:
        return exact
    # Then ilike — pick the best-keyword match
    s = insurance_company.lower()
    candidates = db.query(PayerAddress).all()
    best = None
    best_score = 0
    for p in candidates:
        keywords = (p.payer_name or "").lower().split()
        score = sum(1 for k in keywords if k in s)
        if score > best_score:
            best_score = score
            best = p
    return best if best_score > 0 else None


def format_payer_address(p: PayerAddress) -> str:
    """Multi-line address string for the recipient block."""
    if not p:
        return ""
    parts = []
    if p.appeals_dept_name:
        parts.append(p.appeals_dept_name)
    parts.append(p.address_line_1 or "")
    if p.address_line_2:
        parts.append(p.address_line_2)
    parts.append(f"{p.city or ''}, {p.state or ''} {p.zip_code or ''}".strip())
    return "\n".join(p for p in parts if p.strip())


# ---------- practice config lookup ----------

def get_practice_info(db: Session) -> dict:
    cfg = db.query(PracticeConfig).first()
    if not cfg:
        return {
            "name": "WWC Gynecology & Aesthetics",
            "address": "Maryland",
            "phone": "—",
            "npi": "—",
            "tax_id": "—",
        }
    addr_parts = []
    if cfg.address_line_1: addr_parts.append(cfg.address_line_1)
    if getattr(cfg, "address_line_2", None): addr_parts.append(cfg.address_line_2)
    city_state_zip = " ".join(filter(None, [
        getattr(cfg, "city", None),
        getattr(cfg, "state", None),
        getattr(cfg, "zip_code", None),
    ]))
    if city_state_zip: addr_parts.append(city_state_zip)
    return {
        "name":   getattr(cfg, "practice_name", "WWC Gynecology & Aesthetics"),
        "address": "\n".join(addr_parts) or "Maryland",
        "phone":  getattr(cfg, "phone", "") or "",
        "npi":    getattr(cfg, "billing_npi", "") or "",
        "tax_id": getattr(cfg, "tax_id", "") or "",
    }


def get_default_signer(db: Session) -> dict:
    cfg = db.query(PracticeConfig).first()
    if cfg and getattr(cfg, "appeal_signer_name", None):
        return {
            "name":         cfg.appeal_signer_name,
            "credentials":  getattr(cfg, "appeal_signer_credentials", "") or "",
            "title":        getattr(cfg, "appeal_signer_title", "") or "Practice Manager",
        }
    return {"name": "[Practice Manager]", "credentials": "", "title": "Practice Manager"}


# ---------- top-level entry point ----------

def draft_appeal_letter(
    db: Session, claim: ActiveClaim,
    template_type: str, level: int = 1,
    additional_verbiage: Optional[str] = None,
    signer_override: Optional[dict] = None,
    use_ai: bool = True,
) -> dict:
    """Render an appeal letter draft (subject + body + recipient block).

    Returns a dict with rendered fields, ready to be saved as an AppealLetter.
    """
    practice = get_practice_info(db)
    signer = signer_override or get_default_signer(db)

    payer = find_payer_address(db, claim.insurance_company or "")
    recipient_name = (payer.payer_name if payer else (claim.insurance_company or "Appeals Department"))
    recipient_address = format_payer_address(payer) if payer else "[Address not on file — fill in]"
    recipient_fax = payer.appeals_fax if payer else None

    tokens = _claim_tokens(
        claim, practice, signer, level,
        recipient_name, recipient_address, additional_verbiage,
    )
    template_body = get_template_body(template_type, level)
    rendered = render_body(template_body, tokens)

    used_ai = False
    if use_ai:
        ai = _claude_rewrite(rendered, claim, template_type, level)
        if ai:
            rendered = ai
            used_ai = True

    return {
        "subject":             make_subject(template_type, level, claim.claim_number, str(claim.dos), claim.patient_name or "—"),
        "body":                rendered,
        "recipient_name":      recipient_name,
        "recipient_address":   recipient_address,
        "recipient_fax":       recipient_fax,
        "signer_name":         signer.get("name", ""),
        "signer_credentials":  signer.get("credentials", ""),
        "signer_title":        signer.get("title", ""),
        "additional_verbiage": additional_verbiage,
        "used_ai":             used_ai,
        "template_type":       template_type,
        "level":               level,
    }
