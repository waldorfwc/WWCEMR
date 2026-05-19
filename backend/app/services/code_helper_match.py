"""Match an AI-extracted patient name + DOB to the patients roster."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.patient import Patient


class MatchKind(str, Enum):
    ONE       = "one"
    AMBIGUOUS = "ambiguous"
    NONE      = "none"


@dataclass
class MatchResult:
    kind: MatchKind
    patient_id: Optional[str] = None
    candidates: List[str]     = field(default_factory=list)


def _last_name_token(full_name: str) -> str:
    """Return the last whitespace-separated token, lowercased.
    Works for both 'Smith, Jane' and 'Jane Smith' (caller normalizes if needed)."""
    s = (full_name or "").strip()
    if not s:
        return ""
    if "," in s:
        return s.split(",", 1)[0].strip().lower()
    return s.split()[-1].lower()


def match_patient(
    db: Session, *, name: Optional[str], dob: Optional[date],
) -> MatchResult:
    """Match by (last_name, dob). DOB is required — without it we can't
    safely match. Returns ONE / AMBIGUOUS / NONE."""
    if not name or not dob:
        return MatchResult(kind=MatchKind.NONE)
    last = _last_name_token(name)
    if not last:
        return MatchResult(kind=MatchKind.NONE)

    rows = (
        db.query(Patient.patient_id)
          .filter(func.lower(Patient.last_name) == last)
          .filter(Patient.date_of_birth == dob)
          .all()
    )
    ids = [r[0] for r in rows]
    if len(ids) == 1:
        return MatchResult(kind=MatchKind.ONE, patient_id=ids[0])
    if not ids:
        return MatchResult(kind=MatchKind.NONE)
    return MatchResult(kind=MatchKind.AMBIGUOUS, candidates=ids)
