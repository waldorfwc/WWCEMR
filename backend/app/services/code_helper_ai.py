"""Code Helper AI integration: Anthropic tool-use call + Pydantic schemas.

See docs/superpowers/specs/2026-05-19-code-helper-design.md for the spec.
"""
from __future__ import annotations

import os
from datetime import date
from typing import Any, Dict, List, Literal, Optional, Tuple, Union

from anthropic import Anthropic
from pydantic import BaseModel, Field, model_validator


JustificationType = Literal["e_m_mdm", "e_m_time", "procedure"]


class EMMDMJustification(BaseModel):
    """Structured 3-element MDM rationale for E&M (medical-decision-making)."""
    problems_addressed: str
    data_reviewed:      str
    risk:               str

    def __getitem__(self, key: str) -> str:
        try:
            return getattr(self, key)
        except AttributeError:
            raise KeyError(key)


class DenialFlag(BaseModel):
    payer:  str
    reason: str


class AlternativeCode(BaseModel):
    code:       str
    modifiers:  List[str] = Field(default_factory=list)
    rationale:  str


class CPTEntry(BaseModel):
    code:               str
    modifiers:          List[str] = Field(default_factory=list)
    position:           int = Field(ge=1, le=6)
    justification_type: JustificationType
    # E&M MDM => EMMDMJustification (object); e_m_time / procedure => str
    justification:      Union[EMMDMJustification, str]
    time_minutes:       Optional[int] = None
    denial_flag:        Optional[DenialFlag] = None
    alternative:        Optional[AlternativeCode] = None

    @model_validator(mode="after")
    def _check_justification_shape(self):
        if self.justification_type == "e_m_mdm":
            if not isinstance(self.justification, EMMDMJustification):
                raise ValueError("e_m_mdm requires a structured justification object")
        else:
            if not isinstance(self.justification, str):
                raise ValueError(f"{self.justification_type} requires a string justification")
        if self.justification_type == "e_m_time" and self.time_minutes is None:
            raise ValueError("e_m_time requires time_minutes")
        return self


class ICD10Entry(BaseModel):
    code:        str
    position:    int = Field(ge=1, le=4)
    description: str


class AICodingResult(BaseModel):
    """The structured payload returned by the AI tool-use call."""
    patient_name: Optional[str]  = None
    patient_dob:  Optional[date] = None
    cpt_codes:    List[CPTEntry]   = Field(default_factory=list, max_length=6)
    icd10_codes:  List[ICD10Entry] = Field(default_factory=list, max_length=4)


# ---------------------------------------------------------------------------
# Prompt assembly, extraction, and top-level generate call
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "You are an expert medical coder for a women's health practice. "
    "Given a clinical note plus a list of CPT/ICD-10 codes that the "
    "practice has seen denied by specific payers, return the most "
    "accurate codes the note supports. Use ICD-10 at the highest level "
    "of specificity the note documents — do not invent specificity that "
    "isn't present. For each CPT, choose the correct justification type "
    "(e_m_mdm, e_m_time, or procedure) and provide the structured "
    "rationale. If a suggested code is on the supplied denial list for "
    "the current payer, populate denial_flag and propose the next-best "
    "alternative that the note still supports. Also extract patient "
    "name and DOB if present in the note; leave them null if not."
)


_TOOL: Dict[str, Any] = {
    "name": "submit_coding",
    "description": "Submit the suggested medical codes.",
    "input_schema": {
        "type": "object",
        "properties": {
            "patient_name": {"type": ["string", "null"]},
            "patient_dob":  {"type": ["string", "null"],
                              "description": "YYYY-MM-DD"},
            "cpt_codes": {
                "type": "array",
                "maxItems": 6,
                "items": {
                    "type": "object",
                    "properties": {
                        "code":               {"type": "string"},
                        "modifiers":          {"type": "array",
                                               "items": {"type": "string"}},
                        "position":           {"type": "integer",
                                               "minimum": 1, "maximum": 6},
                        "justification_type": {"type": "string",
                                               "enum": ["e_m_mdm", "e_m_time", "procedure"]},
                        "justification":      {"description": "Object for e_m_mdm, string otherwise"},
                        "time_minutes":       {"type": ["integer", "null"]},
                        "denial_flag": {
                            "type": ["object", "null"],
                            "properties": {
                                "payer":  {"type": "string"},
                                "reason": {"type": "string"},
                            },
                        },
                        "alternative": {
                            "type": ["object", "null"],
                            "properties": {
                                "code":      {"type": "string"},
                                "modifiers": {"type": "array",
                                              "items": {"type": "string"}},
                                "rationale": {"type": "string"},
                            },
                        },
                    },
                    "required": ["code", "position",
                                 "justification_type", "justification"],
                },
            },
            "icd10_codes": {
                "type": "array",
                "maxItems": 4,
                "items": {
                    "type": "object",
                    "properties": {
                        "code":        {"type": "string"},
                        "position":    {"type": "integer",
                                        "minimum": 1, "maximum": 4},
                        "description": {"type": "string"},
                    },
                    "required": ["code", "position", "description"],
                },
            },
        },
        "required": ["cpt_codes", "icd10_codes"],
    },
}


def build_user_content(
    *,
    note_text: Optional[str],
    note_pdf_b64: Optional[str],
    payer: Optional[str],
    active_denials: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Assemble the user-message content blocks (Anthropic API shape)."""
    blocks: List[Dict[str, Any]] = []
    if note_pdf_b64:
        blocks.append({
            "type": "document",
            "source": {"type": "base64",
                       "media_type": "application/pdf",
                       "data": note_pdf_b64},
        })
    if note_text:
        blocks.append({"type": "text",
                       "text": f"CLINICAL NOTE:\n{note_text}"})

    payer_label = payer or "(no payer specified)"
    denial_lines = []
    for d in active_denials:
        scope = d.get("payer_name") or "ALL PAYERS"
        denial_lines.append(
            f"  - {d['code_type'].upper()} {d['code']} (denied by {scope})"
            + (f": {d['reason']}" if d.get("reason") else "")
        )
    denials_blob = (
        "\n".join(denial_lines) if denial_lines else "  (none on file)"
    )

    blocks.append({
        "type": "text",
        "text": (
            f"\nCURRENT PAYER: {payer_label}\n"
            f"\nKNOWN DENIED CODES (active list, filtered to relevant payer):\n"
            f"{denials_blob}\n"
            f"\nReturn the coding via the submit_coding tool. ICD-10 codes "
            f"MUST be at the highest level of specificity the note supports."
        ),
    })
    return blocks


def extract_tool_input(message: Any) -> Tuple[AICodingResult, Dict[str, int]]:
    """Find the submit_coding tool_use block, validate via Pydantic, and
    return (result, usage dict)."""
    tool_block = next(
        (b for b in message.content
         if getattr(b, "type", None) == "tool_use"
         and getattr(b, "name", None) == "submit_coding"),
        None,
    )
    if tool_block is None:
        raise RuntimeError("AI response did not invoke submit_coding")
    result = AICodingResult.model_validate(tool_block.input)
    usage = {
        "input_tokens":  getattr(message.usage, "input_tokens", None),
        "output_tokens": getattr(message.usage, "output_tokens", None),
    }
    return result, usage


def generate_codes(
    *,
    note_text: Optional[str],
    note_pdf_b64: Optional[str],
    payer: Optional[str],
    active_denials: List[Dict[str, Any]],
    model: str = "claude-opus-4-7",
) -> Tuple[AICodingResult, Dict[str, int], str]:
    """Make the Anthropic call. Returns (result, usage, model_used)."""
    from app.config import settings

    api_key = (getattr(settings, "anthropic_api_key", None)
               or os.environ.get("ANTHROPIC_API_KEY"))
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not configured")

    client = Anthropic(api_key=api_key)
    msg = client.messages.create(
        model=model,
        max_tokens=2000,
        system=_SYSTEM_PROMPT,
        tools=[_TOOL],
        tool_choice={"type": "tool", "name": "submit_coding"},
        messages=[{"role": "user",
                   "content": build_user_content(
                       note_text=note_text,
                       note_pdf_b64=note_pdf_b64,
                       payer=payer,
                       active_denials=active_denials,
                   )}],
    )
    result, usage = extract_tool_input(msg)
    return result, usage, model
