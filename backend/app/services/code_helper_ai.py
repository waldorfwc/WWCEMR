"""Code Helper AI integration: Anthropic tool-use call + Pydantic schemas.

See docs/superpowers/specs/2026-05-19-code-helper-design.md for the spec.
"""
from __future__ import annotations

from datetime import date
from typing import List, Literal, Optional, Union

from pydantic import BaseModel, Field, model_validator


JustificationType = Literal["e_m_mdm", "e_m_time", "procedure"]


class EMMDMJustification(BaseModel):
    """Structured 3-element MDM rationale for E&M (medical-decision-making)."""
    problems_addressed: str
    data_reviewed:      str
    risk:               str

    def __getitem__(self, key: str) -> str:
        return getattr(self, key)


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
