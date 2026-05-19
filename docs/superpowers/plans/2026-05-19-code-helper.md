# Code Helper Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a new `/billing/code-helper` page that takes a clinical note (text or PDF), calls Claude Opus 4.7 once, and returns structured CPT codes (with modifiers + E&M-or-procedure justifications) + up to 4 ICD-10 codes, with payer-aware denial-list flagging and a persistent request history.

**Architecture:** FastAPI router + SQLAlchemy models + single Anthropic round-trip via tool-use. Reuses the existing AI-call pattern from `surgery_billing_ai.py` and `billing_doc_classify.py`. React page on Cloud Run frontend, server endpoints on Cloud Run backend. No new external dependencies — `anthropic` SDK is already in `requirements.txt`.

**Tech Stack:** FastAPI, SQLAlchemy, Pydantic v2, Anthropic Python SDK (`claude-opus-4-7`, tool-use), React 18 + TanStack Query + Tailwind, pytest + respx for mocking.

**Spec:** `docs/superpowers/specs/2026-05-19-code-helper-design.md`

---

## File structure

**New files:**

| Path | Responsibility |
|---|---|
| `backend/app/models/code_helper.py` | `CodeHelperRequest` + `CodeHelperDenial` ORM models |
| `backend/app/services/code_helper_ai.py` | Single function: `generate_codes(note_text \| note_pdf, payer, denial_list) → AIResult`. Builds prompt, invokes Anthropic tool-use, Pydantic-validates the tool input, returns a typed object. |
| `backend/app/services/code_helper_match.py` | Patient roster matching: `match_patient(db, name, dob) → MatchResult` (one_match \| ambiguous \| no_match). |
| `backend/app/routers/code_helper.py` | 9 endpoints under `/api/billing/code-helper`. |
| `backend/tests/test_code_helper_ai.py` | Unit tests for AI service + Pydantic validation. |
| `backend/tests/test_code_helper_match.py` | Unit tests for patient matching. |
| `backend/tests/test_code_helper_router.py` | Integration tests (TestClient + mocked Anthropic). |
| `frontend/src/pages/CodeHelper.jsx` | Main page: input panel + result panel + history table. |
| `frontend/src/pages/CodeHelperDenials.jsx` | Admin sub-page for the denial list. |

**Modified files:**

| Path | Change |
|---|---|
| `backend/app/database.py` | Import the new models in `init_db()` + add lightweight migrations registration for indexes (tables are auto-created). |
| `backend/app/main.py` | Mount the new router. |
| `frontend/src/App.jsx` | Add `/billing/code-helper` + `/billing/code-helper/denials` routes. |
| `frontend/src/components/layout/TopNav.jsx` | Add "Code Helper" link under Billing. |

---

## Conventions in this codebase (read once before starting)

- Tests use `backend/tests/conftest.py` — provides in-memory SQLite + `TestClient` + `get_db` override. Use the `client` and `db` fixtures.
- AI calls go through the `anthropic` SDK; key from `settings.anthropic_api_key` or `os.environ["ANTHROPIC_API_KEY"]`. Model: `claude-opus-4-7`.
- Audit log writes via `app.services.audit_service.log_action(db, action, resource, ...)` — call from the router after a successful AI generation.
- New columns to existing tables go through `_apply_lightweight_migrations` in `database.py`. Brand-new tables get auto-created by `Base.metadata.create_all()` — just import the model module in `init_db()`.
- All routes require `require_permission(...)` from `app.routers.auth` — read perm: `claim:read`, edit perm: `claim:edit`, delete perm: `user:manage`.
- Frontend uses TanStack Query (`useQuery`/`useMutation`) + axios via `import api from '../utils/api'` (baseURL `/api`).

---

### Task 1: SQLAlchemy models

**Files:**
- Create: `backend/app/models/code_helper.py`

- [ ] **Step 1: Create the model file**

```python
"""ORM models for the Code Helper feature.

See docs/superpowers/specs/2026-05-19-code-helper-design.md for the data
model rationale + per-CPT JSON shape.
"""
from datetime import datetime
from sqlalchemy import (
    Boolean, Column, Date, DateTime, ForeignKey, Index, Integer, JSON,
    String, Text,
)
from sqlalchemy.orm import relationship

from app.database import Base
from app.models.guid import GUID, new_uuid


class CodeHelperRequest(Base):
    """One row per AI code-generation call. AI output is kept verbatim
    so the audit log is reproducible."""
    __tablename__ = "code_helper_requests"
    __table_args__ = (
        Index("ix_code_helper_req_requested_at", "requested_at"),
        Index("ix_code_helper_req_patient",      "patient_id"),
        Index("ix_code_helper_req_requested_by", "requested_by"),
    )

    id            = Column(GUID(), primary_key=True, default=new_uuid)
    requested_at  = Column(DateTime, default=datetime.utcnow, nullable=False)
    requested_by  = Column(String(120), nullable=False)

    # Input — exactly one of (note_text, source_pdf_storage_filename) is set
    note_text                   = Column(Text, nullable=True)
    source_pdf_storage_filename = Column(String(255), nullable=True)
    payer_name                  = Column(String(120), nullable=True)

    # Patient (AI-extracted, user-editable; FK set when roster match is unambiguous)
    patient_name = Column(String(160), nullable=True)
    patient_dob  = Column(Date,          nullable=True)
    patient_id   = Column(String(20), ForeignKey("patients.patient_id"), nullable=True)

    # AI output, verbatim
    cpt_codes    = Column(JSON, default=list, nullable=False)
    icd10_codes  = Column(JSON, default=list, nullable=False)

    # Audit
    ai_model         = Column(String(60),  nullable=False)
    ai_input_tokens  = Column(Integer,     nullable=True)
    ai_output_tokens = Column(Integer,     nullable=True)
    error            = Column(Text,        nullable=True)


class CodeHelperDenial(Base):
    """Practice's persistent list of CPT/ICD codes that get denied by
    specific payers (or universally when payer_name is null)."""
    __tablename__ = "code_helper_denials"
    __table_args__ = (
        Index("ix_code_helper_denials_lookup",
              "code", "payer_name", "is_active"),
    )

    id          = Column(GUID(), primary_key=True, default=new_uuid)
    code        = Column(String(20), nullable=False)
    code_type   = Column(String(10), nullable=False)  # 'cpt' or 'icd10'
    payer_name  = Column(String(120), nullable=True)  # null = all payers
    reason      = Column(Text, nullable=True)
    is_active   = Column(Boolean, default=True, nullable=False)
    added_by    = Column(String(120), nullable=False)
    added_at    = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at  = Column(DateTime, default=datetime.utcnow,
                          onupdate=datetime.utcnow, nullable=False)
```

- [ ] **Step 2: Register the module so the tables get created**

Modify `backend/app/database.py` — locate `init_db()` and add `code_helper` to the imports list (alphabetical with the existing model modules):

```python
def init_db():
    from app.models import patient, claim, payment, denial, appeal, audit, document, patient_directory, clinical, payment_analysis, fax_log, practice_config, user, adjustment_code_reference, import_audit, groups, checklist, recall, training, google_sync, surgery, larc, billing_document, missing_charge, pellet, state_transition, idempotency, personal_task, code_helper  # noqa
    Base.metadata.create_all(bind=engine)
    ...
```

- [ ] **Step 3: Smoke-test the import**

Run:
```bash
cd backend
./venv/bin/python -c "from app.models.code_helper import CodeHelperRequest, CodeHelperDenial; print('OK', CodeHelperRequest.__tablename__, CodeHelperDenial.__tablename__)"
```
Expected output: `OK code_helper_requests code_helper_denials`

- [ ] **Step 4: Commit**

```bash
git add backend/app/models/code_helper.py backend/app/database.py
git commit -m "feat(code-helper): SQLAlchemy models for requests + denial list"
```

---

### Task 2: Pydantic schemas for AI tool input/output

**Files:**
- Create: `backend/app/services/code_helper_ai.py` (the schema-only portion)
- Test: `backend/tests/test_code_helper_ai.py`

The Pydantic schemas describe exactly what the AI tool-use call must return. They're also the validation gate before we trust the AI output.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_code_helper_ai.py`:
```python
"""Unit tests for Code Helper AI schema validation + payload assembly."""
import pytest
from pydantic import ValidationError

from app.services.code_helper_ai import (
    AICodingResult, CPTEntry, ICD10Entry,
)


def test_cpt_entry_em_mdm_valid():
    entry = CPTEntry(
        code="99214",
        modifiers=["25"],
        position=1,
        justification_type="e_m_mdm",
        justification={
            "problems_addressed": "Moderate",
            "data_reviewed":      "Limited",
            "risk":               "Moderate",
        },
    )
    assert entry.code == "99214"
    assert entry.modifiers == ["25"]
    assert entry.justification["risk"] == "Moderate"
    assert entry.time_minutes is None


def test_cpt_entry_em_time_valid():
    entry = CPTEntry(
        code="99215",
        modifiers=[],
        position=1,
        justification_type="e_m_time",
        justification="Spent 40 min in counseling about treatment options",
        time_minutes=40,
    )
    assert entry.time_minutes == 40


def test_cpt_entry_procedure_valid():
    entry = CPTEntry(
        code="11401",
        modifiers=[],
        position=2,
        justification_type="procedure",
        justification="Excision of 0.6cm benign skin lesion, left forearm.",
    )
    assert entry.justification.startswith("Excision")


def test_cpt_entry_rejects_unknown_justification_type():
    with pytest.raises(ValidationError):
        CPTEntry(
            code="99214", modifiers=[], position=1,
            justification_type="freestyle",
            justification="anything goes",
        )


def test_icd10_entry_valid():
    icd = ICD10Entry(code="E11.9", position=1,
                     description="Type 2 diabetes without complications")
    assert icd.position == 1


def test_icd10_position_must_be_1_to_4():
    with pytest.raises(ValidationError):
        ICD10Entry(code="E11.9", position=5, description="x")


def test_ai_coding_result_full():
    r = AICodingResult(
        patient_name="Smith, Jane",
        patient_dob="1985-03-12",
        cpt_codes=[CPTEntry(code="99214", modifiers=[], position=1,
                             justification_type="e_m_mdm",
                             justification={"problems_addressed":"Mod",
                                            "data_reviewed":"Ltd",
                                            "risk":"Mod"})],
        icd10_codes=[ICD10Entry(code="I10", position=1,
                                 description="Essential hypertension")],
    )
    assert r.patient_name == "Smith, Jane"
    assert len(r.cpt_codes) == 1
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd backend
./venv/bin/pytest tests/test_code_helper_ai.py -v
```
Expected: `ModuleNotFoundError: No module named 'app.services.code_helper_ai'`

- [ ] **Step 3: Write the schemas**

Create `backend/app/services/code_helper_ai.py`:
```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
./venv/bin/pytest tests/test_code_helper_ai.py -v
```
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/code_helper_ai.py backend/tests/test_code_helper_ai.py
git commit -m "feat(code-helper): Pydantic schemas for AI coding result"
```

---

### Task 3: Build the AI prompt + tool-use call

**Files:**
- Modify: `backend/app/services/code_helper_ai.py`
- Modify: `backend/tests/test_code_helper_ai.py`

- [ ] **Step 1: Write failing test for prompt-assembly + result extraction**

Append to `backend/tests/test_code_helper_ai.py`:
```python
from unittest.mock import MagicMock, patch
from app.services.code_helper_ai import (
    build_user_content, extract_tool_input, generate_codes,
)


def test_build_user_content_text_only():
    content = build_user_content(
        note_text="65yo F w/ T2DM, A1c 8.2, HTN.",
        note_pdf_b64=None,
        payer="Cigna",
        active_denials=[
            {"code": "97110", "code_type": "cpt", "payer_name": "Cigna",
             "reason": "not separately reimbursable"},
        ],
    )
    # Should be a list of content blocks (Anthropic API shape)
    assert isinstance(content, list)
    text_blob = " ".join(b.get("text", "") for b in content if b["type"] == "text")
    assert "T2DM" in text_blob
    assert "97110" in text_blob
    assert "Cigna" in text_blob


def test_build_user_content_pdf_attaches_document_block():
    content = build_user_content(
        note_text=None,
        note_pdf_b64="JVBERi0xLjQK",  # fake PDF header
        payer=None,
        active_denials=[],
    )
    types = [b["type"] for b in content]
    assert "document" in types


def test_extract_tool_input_happy_path():
    # Mock an Anthropic API Message response with one tool_use block
    fake_response = MagicMock()
    fake_response.content = [
        MagicMock(type="tool_use", name="submit_coding", input={
            "patient_name": "Jane Smith",
            "patient_dob":  "1985-03-12",
            "cpt_codes": [{
                "code": "99214", "modifiers": ["25"], "position": 1,
                "justification_type": "e_m_mdm",
                "justification": {"problems_addressed":"Mod",
                                   "data_reviewed":"Ltd","risk":"Mod"},
            }],
            "icd10_codes": [{"code":"I10","position":1,"description":"HTN"}],
        }),
    ]
    fake_response.usage = MagicMock(input_tokens=1200, output_tokens=400)

    result, usage = extract_tool_input(fake_response)
    assert result.patient_name == "Jane Smith"
    assert result.cpt_codes[0].code == "99214"
    assert usage["input_tokens"] == 1200
```

- [ ] **Step 2: Run to verify it fails**

```bash
./venv/bin/pytest tests/test_code_helper_ai.py -v
```
Expected: ImportError on `build_user_content`, `extract_tool_input`, `generate_codes`.

- [ ] **Step 3: Implement the prompt + extraction + top-level call**

Append to `backend/app/services/code_helper_ai.py`:
```python
import os
from typing import Any, Dict, Tuple

from app.config import settings


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


_TOOL = {
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
        scope = d["payer_name"] or "ALL PAYERS"
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


def extract_tool_input(message) -> Tuple[AICodingResult, Dict[str, int]]:
    """Find the submit_coding tool_use block, validate via Pydantic, and
    return (result, usage dict)."""
    tool_block = next(
        (b for b in message.content if getattr(b, "type", None) == "tool_use"
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
    from anthropic import Anthropic   # lazy import per existing pattern

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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
./venv/bin/pytest tests/test_code_helper_ai.py -v
```
Expected: all 10 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/code_helper_ai.py backend/tests/test_code_helper_ai.py
git commit -m "feat(code-helper): Anthropic tool-use prompt + extraction"
```

---

### Task 4: Patient roster matching service

**Files:**
- Create: `backend/app/services/code_helper_match.py`
- Test: `backend/tests/test_code_helper_match.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_code_helper_match.py`:
```python
"""Unit tests for patient roster matching helper."""
from datetime import date
import pytest

from app.models.patient import Patient
from app.services.code_helper_match import match_patient, MatchKind


def _seed(db, *patients):
    for p in patients:
        db.add(p)
    db.commit()


def test_match_patient_exact_one(db):
    _seed(db,
        Patient(patient_id="P001", first_name="Jane",  last_name="Smith",
                 dob=date(1985, 3, 12)),
        Patient(patient_id="P002", first_name="Other", last_name="Jones",
                 dob=date(1985, 3, 12)),
    )
    r = match_patient(db, name="Jane Smith", dob=date(1985, 3, 12))
    assert r.kind == MatchKind.ONE
    assert r.patient_id == "P001"


def test_match_patient_no_match(db):
    _seed(db, Patient(patient_id="P001", first_name="Jane",
                       last_name="Smith", dob=date(1985, 3, 12)))
    r = match_patient(db, name="Nobody Here", dob=date(1990, 1, 1))
    assert r.kind == MatchKind.NONE
    assert r.patient_id is None


def test_match_patient_ambiguous(db):
    _seed(db,
        Patient(patient_id="P001", first_name="Jane",  last_name="Smith",
                 dob=date(1985, 3, 12)),
        Patient(patient_id="P002", first_name="Janet", last_name="Smith",
                 dob=date(1985, 3, 12)),
    )
    r = match_patient(db, name="Smith", dob=date(1985, 3, 12))
    assert r.kind == MatchKind.AMBIGUOUS
    assert r.patient_id is None
    assert set(r.candidates) == {"P001", "P002"}


def test_match_patient_lastname_only_works(db):
    _seed(db, Patient(patient_id="P001", first_name="Jane",
                       last_name="Smith", dob=date(1985, 3, 12)))
    r = match_patient(db, name="Smith", dob=date(1985, 3, 12))
    assert r.kind == MatchKind.ONE


def test_match_patient_none_when_no_dob(db):
    _seed(db, Patient(patient_id="P001", first_name="Jane",
                       last_name="Smith", dob=date(1985, 3, 12)))
    r = match_patient(db, name="Jane Smith", dob=None)
    assert r.kind == MatchKind.NONE
```

- [ ] **Step 2: Run to verify failure**

```bash
./venv/bin/pytest tests/test_code_helper_match.py -v
```
Expected: ModuleNotFoundError on `app.services.code_helper_match`.

- [ ] **Step 3: Implement**

Create `backend/app/services/code_helper_match.py`:
```python
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
          .filter(Patient.dob == dob)
          .all()
    )
    ids = [r[0] for r in rows]
    if len(ids) == 1:
        return MatchResult(kind=MatchKind.ONE, patient_id=ids[0])
    if not ids:
        return MatchResult(kind=MatchKind.NONE)
    return MatchResult(kind=MatchKind.AMBIGUOUS, candidates=ids)
```

- [ ] **Step 4: Run tests**

```bash
./venv/bin/pytest tests/test_code_helper_match.py -v
```
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/code_helper_match.py backend/tests/test_code_helper_match.py
git commit -m "feat(code-helper): patient roster matching by last-name + dob"
```

---

### Task 5: Router — list/create/get denial entries

Build the denial-list endpoints first because the request-creation endpoint needs to read them at AI-call time.

**Files:**
- Create: `backend/app/routers/code_helper.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/test_code_helper_router.py`

- [ ] **Step 1: Write failing tests for the denial endpoints**

Create `backend/tests/test_code_helper_router.py`:
```python
"""Integration tests for the Code Helper router."""
from datetime import date
import pytest


def test_create_denial(client):
    res = client.post("/api/billing/code-helper/denials", json={
        "code": "97110", "code_type": "cpt",
        "payer_name": "Cigna", "reason": "not separately reimbursable",
    })
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["code"] == "97110"
    assert body["is_active"] is True
    assert body["added_by"]   # the test user from conftest


def test_list_denials_returns_active_only_by_default(client):
    client.post("/api/billing/code-helper/denials", json={
        "code": "A1", "code_type": "cpt", "payer_name": None,
    })
    r2 = client.post("/api/billing/code-helper/denials", json={
        "code": "A2", "code_type": "cpt", "payer_name": None,
    })
    # deactivate one
    did = r2.json()["id"]
    client.patch(f"/api/billing/code-helper/denials/{did}",
                  json={"is_active": False})

    res = client.get("/api/billing/code-helper/denials")
    body = res.json()
    codes = sorted(d["code"] for d in body["denials"])
    assert codes == ["A1"]

    # include inactive on demand
    res2 = client.get("/api/billing/code-helper/denials?active=false")
    assert len(res2.json()["denials"]) == 2


def test_list_denials_filter_by_payer(client):
    client.post("/api/billing/code-helper/denials", json={
        "code": "B1", "code_type": "cpt", "payer_name": "Cigna",
    })
    client.post("/api/billing/code-helper/denials", json={
        "code": "B2", "code_type": "cpt", "payer_name": "Aetna",
    })
    client.post("/api/billing/code-helper/denials", json={
        "code": "B3", "code_type": "cpt", "payer_name": None,
    })
    res = client.get("/api/billing/code-helper/denials?payer=Cigna")
    codes = sorted(d["code"] for d in res.json()["denials"])
    # Cigna-tagged + universal
    assert codes == ["B1", "B3"]
```

You'll need a `client` fixture — add this to `conftest.py` if not already present (it likely is). Verify by checking `backend/tests/conftest.py`.

- [ ] **Step 2: Run to verify failure**

```bash
./venv/bin/pytest tests/test_code_helper_router.py -v
```
Expected: 404s (router not mounted yet).

- [ ] **Step 3: Implement the router with denial endpoints**

Create `backend/app/routers/code_helper.py`:
```python
"""Code Helper feature router — AI-assisted CPT + ICD-10 coding.

Endpoint group: /api/billing/code-helper
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.code_helper import CodeHelperDenial, CodeHelperRequest
from app.routers.auth import get_current_user
from app.services.permissions import require_permission


router = APIRouter(prefix="/billing/code-helper", tags=["code-helper"])


# ─── Denial list ─────────────────────────────────────────────────────

class DenialIn(BaseModel):
    code:       str
    code_type:  str   # 'cpt' or 'icd10'
    payer_name: Optional[str] = None
    reason:     Optional[str] = None


class DenialPatch(BaseModel):
    code:       Optional[str] = None
    code_type:  Optional[str] = None
    payer_name: Optional[str] = None
    reason:     Optional[str] = None
    is_active:  Optional[bool] = None


def _denial_dict(d: CodeHelperDenial) -> dict:
    return {
        "id":         str(d.id),
        "code":       d.code,
        "code_type":  d.code_type,
        "payer_name": d.payer_name,
        "reason":     d.reason,
        "is_active":  d.is_active,
        "added_by":   d.added_by,
        "added_at":   d.added_at.isoformat() if d.added_at else None,
    }


@router.get("/denials")
def list_denials(
    db: Session = Depends(get_db),
    payer:  Optional[str]  = None,
    active: Optional[bool] = True,
    _user = Depends(require_permission("claim:read")),
):
    q = db.query(CodeHelperDenial)
    if active is True:
        q = q.filter(CodeHelperDenial.is_active.is_(True))
    if payer:
        # Matching payer OR universal (null payer)
        q = q.filter(
            or_(CodeHelperDenial.payer_name == payer,
                CodeHelperDenial.payer_name.is_(None))
        )
    rows = q.order_by(CodeHelperDenial.added_at.desc()).all()
    return {"denials": [_denial_dict(d) for d in rows]}


@router.post("/denials", status_code=201)
def create_denial(
    payload: DenialIn,
    db: Session = Depends(get_db),
    user = Depends(require_permission("claim:edit")),
):
    if payload.code_type not in ("cpt", "icd10"):
        raise HTTPException(422, "code_type must be 'cpt' or 'icd10'")
    d = CodeHelperDenial(
        code=payload.code.strip(),
        code_type=payload.code_type,
        payer_name=(payload.payer_name.strip() if payload.payer_name else None),
        reason=payload.reason,
        added_by=user.get("email") or "system",
    )
    db.add(d); db.commit(); db.refresh(d)
    return _denial_dict(d)


@router.patch("/denials/{denial_id}")
def patch_denial(
    denial_id: str, payload: DenialPatch,
    db: Session = Depends(get_db),
    _user = Depends(require_permission("claim:edit")),
):
    d = db.query(CodeHelperDenial).filter(CodeHelperDenial.id == denial_id).first()
    if not d:
        raise HTTPException(404, "not found")
    data = payload.model_dump(exclude_unset=True)
    if "code_type" in data and data["code_type"] not in ("cpt", "icd10"):
        raise HTTPException(422, "code_type must be 'cpt' or 'icd10'")
    for k, v in data.items():
        setattr(d, k, v)
    d.updated_at = datetime.utcnow()
    db.commit(); db.refresh(d)
    return _denial_dict(d)


@router.delete("/denials/{denial_id}", status_code=204)
def delete_denial(
    denial_id: str,
    db: Session = Depends(get_db),
    _user = Depends(require_permission("user:manage")),
):
    d = db.query(CodeHelperDenial).filter(CodeHelperDenial.id == denial_id).first()
    if not d:
        raise HTTPException(404, "not found")
    db.delete(d); db.commit()
```

- [ ] **Step 4: Mount the router in main.py**

Edit `backend/app/main.py`. Find the long line that imports routers, append `code_helper`:
```python
from app.routers import waystar, ar, documents, intake, chart, fax, auth, dashboard, fax_batch, admin_users, admin_groups, service_lines, claim_adjustments, service_line_adjustments, charge_imports, claim_id_bootstrap, era_posting, adjustment_codes, transaction_detail_imports, active_ar, active_ar_filter_presets, bank_recon, checklist, recalls, recall_filter_presets, training, surgery, patient_surgery, docusign as docusign_router, consent_templates, surgery_filter_presets, larc, pellet, billing_documents, missing_charges, personal_tasks, code_helper
```

Find the cluster where other billing routers are included (near `bank_recon.router` and `billing_documents.router`), and add:
```python
app.include_router(code_helper.router, prefix="/api")
```

- [ ] **Step 5: Run tests**

```bash
./venv/bin/pytest tests/test_code_helper_router.py -v -k denial
```
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/app/routers/code_helper.py backend/app/main.py backend/tests/test_code_helper_router.py
git commit -m "feat(code-helper): denial-list CRUD endpoints"
```

---

### Task 6: Router — POST /requests with text input

This is the main AI-calling endpoint. Text path first; PDF in the next task.

**Files:**
- Modify: `backend/app/routers/code_helper.py`
- Modify: `backend/tests/test_code_helper_router.py`

- [ ] **Step 1: Write failing tests with mocked Anthropic**

Append to `backend/tests/test_code_helper_router.py`:
```python
from unittest.mock import patch, MagicMock


def _fake_ai_response(*, name="Smith, Jane", dob="1985-03-12",
                      cpt_code="99214", icd_code="I10",
                      input_tokens=1200, output_tokens=400):
    resp = MagicMock()
    resp.content = [MagicMock(type="tool_use", name="submit_coding", input={
        "patient_name": name, "patient_dob": dob,
        "cpt_codes": [{
            "code": cpt_code, "modifiers": ["25"], "position": 1,
            "justification_type": "e_m_mdm",
            "justification": {"problems_addressed":"Mod",
                               "data_reviewed":"Ltd","risk":"Mod"},
        }],
        "icd10_codes": [{"code": icd_code, "position": 1,
                          "description": "Essential hypertension"}],
    })]
    resp.usage = MagicMock(input_tokens=input_tokens, output_tokens=output_tokens)
    return resp


def test_create_request_text_input(client):
    with patch("app.services.code_helper_ai.Anthropic") as M:
        M.return_value.messages.create.return_value = _fake_ai_response()
        res = client.post("/api/billing/code-helper/requests",
                           data={"note_text": "65yo F w/ HTN.",
                                  "payer_name": "Cigna"})
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["payer_name"]   == "Cigna"
    assert body["patient_name"] == "Smith, Jane"
    assert body["cpt_codes"][0]["code"] == "99214"
    assert body["icd10_codes"][0]["code"] == "I10"
    assert body["ai_model"] == "claude-opus-4-7"
    assert body["ai_input_tokens"]  == 1200


def test_create_request_includes_denials_in_prompt(client):
    client.post("/api/billing/code-helper/denials", json={
        "code": "97110", "code_type": "cpt", "payer_name": "Cigna",
    })
    captured = {}
    def fake_create(**kw):
        captured["messages"] = kw["messages"]
        return _fake_ai_response()
    with patch("app.services.code_helper_ai.Anthropic") as M:
        M.return_value.messages.create.side_effect = fake_create
        res = client.post("/api/billing/code-helper/requests",
                           data={"note_text": "PT note", "payer_name": "Cigna"})
    assert res.status_code == 201
    user_blocks = captured["messages"][0]["content"]
    text = " ".join(b.get("text","") for b in user_blocks if b["type"]=="text")
    assert "97110" in text
    assert "Cigna" in text


def test_create_request_missing_note_returns_422(client):
    res = client.post("/api/billing/code-helper/requests", data={})
    assert res.status_code == 422
```

- [ ] **Step 2: Run to verify failure**

```bash
./venv/bin/pytest tests/test_code_helper_router.py -v -k "request and text"
```
Expected: 404 or AttributeError.

- [ ] **Step 3: Implement the request endpoint**

Append to `backend/app/routers/code_helper.py`:
```python
from fastapi import File, Form, UploadFile

from app.services.code_helper_ai import generate_codes
from app.services.code_helper_match import match_patient, MatchKind
from app.services.audit_service import log_action


def _serialize_request(r: CodeHelperRequest) -> dict:
    return {
        "id":           str(r.id),
        "requested_at": r.requested_at.isoformat() if r.requested_at else None,
        "requested_by": r.requested_by,
        "note_text":    r.note_text,
        "source_pdf_storage_filename": r.source_pdf_storage_filename,
        "payer_name":   r.payer_name,
        "patient_name": r.patient_name,
        "patient_dob":  r.patient_dob.isoformat() if r.patient_dob else None,
        "patient_id":   r.patient_id,
        "cpt_codes":    r.cpt_codes,
        "icd10_codes":  r.icd10_codes,
        "ai_model":     r.ai_model,
        "ai_input_tokens":  r.ai_input_tokens,
        "ai_output_tokens": r.ai_output_tokens,
        "error":        r.error,
    }


@router.post("/requests", status_code=201)
def create_request(
    note_text:  Optional[str]        = Form(None),
    note_pdf:   Optional[UploadFile] = File(None),
    payer_name: Optional[str]        = Form(None),
    db: Session = Depends(get_db),
    user = Depends(require_permission("claim:edit")),
):
    if not note_text and not note_pdf:
        raise HTTPException(422, "Provide note_text or note_pdf")

    # Read PDF body (deferred — Task 7 wires this up; for now reject):
    note_pdf_b64 = None
    if note_pdf is not None:
        raise HTTPException(422, "PDF upload not yet supported in this build")

    # Pull the active, payer-relevant denials.
    q = db.query(CodeHelperDenial).filter(CodeHelperDenial.is_active.is_(True))
    if payer_name:
        q = q.filter(or_(CodeHelperDenial.payer_name == payer_name,
                          CodeHelperDenial.payer_name.is_(None)))
    else:
        q = q.filter(CodeHelperDenial.payer_name.is_(None))
    active_denials = [
        {"code": d.code, "code_type": d.code_type,
         "payer_name": d.payer_name, "reason": d.reason}
        for d in q.all()
    ]

    try:
        ai_result, usage, model = generate_codes(
            note_text=note_text, note_pdf_b64=note_pdf_b64,
            payer=payer_name, active_denials=active_denials,
        )
    except RuntimeError as e:
        # AI call failed — save the row with the error and 502 out.
        row = CodeHelperRequest(
            requested_by=user.get("email") or "system",
            note_text=note_text, payer_name=payer_name,
            cpt_codes=[], icd10_codes=[],
            ai_model="claude-opus-4-7",
            error=str(e),
        )
        db.add(row); db.commit(); db.refresh(row)
        raise HTTPException(502, f"AI call failed: {e}")

    match = match_patient(db, name=ai_result.patient_name,
                           dob=ai_result.patient_dob)

    row = CodeHelperRequest(
        requested_by=user.get("email") or "system",
        note_text=note_text, payer_name=payer_name,
        patient_name=ai_result.patient_name,
        patient_dob=ai_result.patient_dob,
        patient_id=(match.patient_id if match.kind == MatchKind.ONE else None),
        cpt_codes=[c.model_dump(mode="json") for c in ai_result.cpt_codes],
        icd10_codes=[i.model_dump(mode="json") for i in ai_result.icd10_codes],
        ai_model=model,
        ai_input_tokens=usage.get("input_tokens"),
        ai_output_tokens=usage.get("output_tokens"),
    )
    db.add(row); db.commit(); db.refresh(row)

    log_action(db, action="code_helper_generated",
                resource="code_helper_request",
                resource_id=str(row.id),
                current_user=user,
                patient_id=row.patient_id,
                description=(f"Generated codes for {row.patient_name or '?'} "
                              f"(payer={row.payer_name or '—'}, "
                              f"cpts={len(row.cpt_codes)})"))
    db.commit()

    return _serialize_request(row)
```

- [ ] **Step 4: Run tests**

```bash
./venv/bin/pytest tests/test_code_helper_router.py -v -k "request and text"
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/code_helper.py backend/tests/test_code_helper_router.py
git commit -m "feat(code-helper): POST /requests with text input + AI call"
```

---

### Task 7: Router — PDF upload path

**Files:**
- Modify: `backend/app/routers/code_helper.py`
- Modify: `backend/tests/test_code_helper_router.py`

- [ ] **Step 1: Write failing test**

Append to `backend/tests/test_code_helper_router.py`:
```python
import base64
from io import BytesIO


def _tiny_valid_pdf_bytes() -> bytes:
    """Minimal one-page PDF header. Real PDFs are bigger; this is just
    enough for the upload to be accepted and forwarded to the mocked AI."""
    return b"%PDF-1.4\n%fake\n%%EOF\n"


def test_create_request_pdf_input(client):
    pdf_bytes = _tiny_valid_pdf_bytes()
    captured = {}
    def fake_create(**kw):
        captured["messages"] = kw["messages"]
        return _fake_ai_response()
    with patch("app.services.code_helper_ai.Anthropic") as M:
        M.return_value.messages.create.side_effect = fake_create
        res = client.post(
            "/api/billing/code-helper/requests",
            data={"payer_name": "Aetna"},
            files={"note_pdf": ("clinical-note.pdf", pdf_bytes, "application/pdf")},
        )
    assert res.status_code == 201, res.text
    assert res.json()["payer_name"] == "Aetna"
    # PDF should have produced a document content block
    types = [b["type"] for b in captured["messages"][0]["content"]]
    assert "document" in types


def test_create_request_pdf_too_large_returns_422(client):
    too_big = b"%PDF-1.4\n" + b"A" * (10 * 1024 * 1024 + 1)  # 10 MB + 1 byte
    res = client.post(
        "/api/billing/code-helper/requests",
        data={"payer_name": "Cigna"},
        files={"note_pdf": ("big.pdf", too_big, "application/pdf")},
    )
    assert res.status_code == 422
    assert "too large" in res.text.lower() or "10" in res.text
```

- [ ] **Step 2: Run to verify failure**

```bash
./venv/bin/pytest tests/test_code_helper_router.py -v -k pdf
```
Expected: the first test fails with the deliberate 422 from Task 6; the second passes (already 422'd).

- [ ] **Step 3: Replace the PDF-rejection block with actual handling**

In `backend/app/routers/code_helper.py`, replace:
```python
    # Read PDF body (deferred — Task 7 wires this up; for now reject):
    note_pdf_b64 = None
    if note_pdf is not None:
        raise HTTPException(422, "PDF upload not yet supported in this build")
```
with:
```python
    note_pdf_b64 = None
    if note_pdf is not None:
        body = note_pdf.file.read()
        if len(body) > 10 * 1024 * 1024:
            raise HTTPException(422, "PDF too large (>10 MB)")
        if not body.startswith(b"%PDF"):
            raise HTTPException(422, "Not a valid PDF (missing %PDF header)")
        import base64
        note_pdf_b64 = base64.b64encode(body).decode("ascii")
```

- [ ] **Step 4: Run tests**

```bash
./venv/bin/pytest tests/test_code_helper_router.py -v -k pdf
```
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/code_helper.py backend/tests/test_code_helper_router.py
git commit -m "feat(code-helper): accept PDF uploads (10MB cap, magic-byte check)"
```

---

### Task 8: Router — GET list, GET one, PATCH, DELETE

**Files:**
- Modify: `backend/app/routers/code_helper.py`
- Modify: `backend/tests/test_code_helper_router.py`

- [ ] **Step 1: Write failing tests**

Append to `backend/tests/test_code_helper_router.py`:
```python
def _make_request_row(client):
    with patch("app.services.code_helper_ai.Anthropic") as M:
        M.return_value.messages.create.return_value = _fake_ai_response()
        return client.post("/api/billing/code-helper/requests",
                            data={"note_text": "x"}).json()


def test_list_requests_paginates(client):
    for _ in range(3):
        _make_request_row(client)
    res = client.get("/api/billing/code-helper/requests?page=1&per_page=2")
    body = res.json()
    assert body["total"] == 3
    assert len(body["requests"]) == 2


def test_get_one_request(client):
    row = _make_request_row(client)
    res = client.get(f"/api/billing/code-helper/requests/{row['id']}")
    assert res.status_code == 200
    assert res.json()["id"] == row["id"]


def test_patch_request_updates_patient(client):
    row = _make_request_row(client)
    res = client.patch(f"/api/billing/code-helper/requests/{row['id']}",
                        json={"patient_name": "Override Name",
                               "patient_dob":  "1970-01-01"})
    assert res.status_code == 200
    assert res.json()["patient_name"] == "Override Name"
    assert res.json()["patient_dob"]  == "1970-01-01"


def test_patch_request_rejects_disallowed_fields(client):
    row = _make_request_row(client)
    res = client.patch(f"/api/billing/code-helper/requests/{row['id']}",
                        json={"cpt_codes": []})
    # PATCH ignores unrecognized fields silently — verify cpt_codes unchanged
    assert res.status_code == 200
    body = res.json()
    assert body["cpt_codes"] == row["cpt_codes"]


def test_delete_request(client):
    row = _make_request_row(client)
    res = client.delete(f"/api/billing/code-helper/requests/{row['id']}")
    assert res.status_code == 204
    res2 = client.get(f"/api/billing/code-helper/requests/{row['id']}")
    assert res2.status_code == 404
```

- [ ] **Step 2: Run to verify failure**

```bash
./venv/bin/pytest tests/test_code_helper_router.py -v -k "list_requests or get_one or patch_request or delete_request"
```
Expected: 404s (endpoints don't exist).

- [ ] **Step 3: Add the endpoints**

Append to `backend/app/routers/code_helper.py`:
```python
from datetime import date as _date


class RequestPatch(BaseModel):
    patient_name: Optional[str]   = None
    patient_dob:  Optional[_date] = None
    patient_id:   Optional[str]   = None


@router.get("/requests")
def list_requests(
    db: Session = Depends(get_db),
    page:     int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    patient_id: Optional[str] = None,
    payer:      Optional[str] = None,
    _user = Depends(require_permission("claim:read")),
):
    q = db.query(CodeHelperRequest)
    if patient_id:
        q = q.filter(CodeHelperRequest.patient_id == patient_id)
    if payer:
        q = q.filter(CodeHelperRequest.payer_name == payer)
    total = q.count()
    rows = (q.order_by(CodeHelperRequest.requested_at.desc())
             .offset((page - 1) * per_page).limit(per_page).all())
    return {"total": total, "page": page, "per_page": per_page,
            "requests": [_serialize_request(r) for r in rows]}


@router.get("/requests/{request_id}")
def get_request(
    request_id: str,
    db: Session = Depends(get_db),
    _user = Depends(require_permission("claim:read")),
):
    r = db.query(CodeHelperRequest).filter(CodeHelperRequest.id == request_id).first()
    if not r:
        raise HTTPException(404, "not found")
    return _serialize_request(r)


@router.patch("/requests/{request_id}")
def patch_request(
    request_id: str, payload: RequestPatch,
    db: Session = Depends(get_db),
    _user = Depends(require_permission("claim:edit")),
):
    r = db.query(CodeHelperRequest).filter(CodeHelperRequest.id == request_id).first()
    if not r:
        raise HTTPException(404, "not found")
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(r, k, v)
    db.commit(); db.refresh(r)
    return _serialize_request(r)


@router.delete("/requests/{request_id}", status_code=204)
def delete_request(
    request_id: str,
    db: Session = Depends(get_db),
    _user = Depends(require_permission("user:manage")),
):
    r = db.query(CodeHelperRequest).filter(CodeHelperRequest.id == request_id).first()
    if not r:
        raise HTTPException(404, "not found")
    db.delete(r); db.commit()
```

- [ ] **Step 4: Run all tests**

```bash
./venv/bin/pytest tests/test_code_helper_router.py -v
```
Expected: all tests in the file pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/code_helper.py backend/tests/test_code_helper_router.py
git commit -m "feat(code-helper): list/get/patch/delete request endpoints"
```

---

### Task 9: Frontend — Code Helper page (input + result + history)

**Files:**
- Create: `frontend/src/pages/CodeHelper.jsx`
- Modify: `frontend/src/App.jsx`
- Modify: `frontend/src/components/layout/TopNav.jsx`

- [ ] **Step 1: Write the page component**

Create `frontend/src/pages/CodeHelper.jsx`:
```jsx
import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Upload, FileText, Loader2, AlertTriangle, CheckCircle2,
  ChevronRight, Wand2, Save, X,
} from 'lucide-react'
import api, { fmt } from '../utils/api'
import { Link } from 'react-router-dom'


export default function CodeHelper() {
  const qc = useQueryClient()
  const [mode, setMode] = useState('text')              // 'text' | 'pdf'
  const [noteText, setNoteText] = useState('')
  const [pdfFile, setPdfFile] = useState(null)
  const [payer, setPayer] = useState('')
  const [draft, setDraft] = useState(null)               // AI result before save
  const [editName, setEditName] = useState('')
  const [editDob, setEditDob] = useState('')

  const { data: history } = useQuery({
    queryKey: ['code-helper-requests'],
    queryFn: () => api.get('/billing/code-helper/requests').then(r => r.data),
  })

  const generate = useMutation({
    mutationFn: async () => {
      const fd = new FormData()
      if (mode === 'text') fd.append('note_text', noteText)
      else if (pdfFile)    fd.append('note_pdf', pdfFile)
      if (payer) fd.append('payer_name', payer)
      const res = await api.post('/billing/code-helper/requests', fd, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      return res.data
    },
    onSuccess: (data) => {
      setDraft(data)
      setEditName(data.patient_name || '')
      setEditDob(data.patient_dob   || '')
      qc.invalidateQueries({ queryKey: ['code-helper-requests'] })
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Generation failed'),
  })

  const savePatient = useMutation({
    mutationFn: () =>
      api.patch(`/billing/code-helper/requests/${draft.id}`, {
        patient_name: editName, patient_dob: editDob || null,
      }).then(r => r.data),
    onSuccess: (data) => {
      setDraft(data)
      qc.invalidateQueries({ queryKey: ['code-helper-requests'] })
    },
  })

  return (
    <div>
      <div className="flex items-baseline justify-between mb-4">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
            <Wand2 size={22} className="text-plum-700" />
            Code Helper
          </h1>
          <p className="text-sm text-gray-500 mt-0.5">
            AI-assisted CPT + ICD-10 generation from a clinical note.
          </p>
        </div>
        <Link to="/billing/code-helper/denials"
              className="btn-secondary text-sm flex items-center gap-1">
          Manage denial list <ChevronRight size={13} />
        </Link>
      </div>

      {/* INPUT PANEL */}
      <div className="card mb-4">
        <div className="flex gap-2 mb-3">
          <button onClick={() => setMode('text')}
                  className={`text-sm px-3 py-1 rounded ${mode === 'text' ? 'bg-plum-700 text-white' : 'bg-gray-100'}`}>
            Paste note
          </button>
          <button onClick={() => setMode('pdf')}
                  className={`text-sm px-3 py-1 rounded ${mode === 'pdf' ? 'bg-plum-700 text-white' : 'bg-gray-100'}`}>
            Upload PDF
          </button>
        </div>

        {mode === 'text' ? (
          <textarea
            className="input text-sm w-full min-h-[160px] font-mono"
            placeholder="Paste the clinical note here…"
            value={noteText}
            onChange={e => setNoteText(e.target.value)}
          />
        ) : (
          <div>
            <input type="file" accept="application/pdf"
                    onChange={e => setPdfFile(e.target.files?.[0] || null)}
                    className="text-[12px]" />
            {pdfFile && (
              <div className="text-[11px] text-gray-500 mt-1">
                {pdfFile.name} — {(pdfFile.size / 1024).toFixed(0)} KB
              </div>
            )}
          </div>
        )}

        <div className="flex items-center gap-2 mt-3">
          <label className="text-[10px] uppercase text-gray-500">Payer</label>
          <input className="input text-sm" placeholder="Cigna / Aetna / …"
                  value={payer} onChange={e => setPayer(e.target.value)} />
          <button
            className="btn-primary text-sm flex items-center gap-1 ml-auto"
            disabled={generate.isPending || (mode === 'text' ? !noteText : !pdfFile)}
            onClick={() => generate.mutate()}
          >
            {generate.isPending
              ? <><Loader2 size={13} className="animate-spin" /> Calling Claude…</>
              : <><Wand2 size={13} /> Generate codes</>}
          </button>
        </div>
      </div>

      {/* RESULT PANEL */}
      {draft && (
        <div className="card mb-4 border-plum-200">
          <h2 className="font-serif font-semibold text-ink text-[15px] mb-2 flex items-center gap-2">
            <CheckCircle2 size={14} className="text-green-700" />
            AI suggestion
          </h2>

          {/* patient strip */}
          <div className="flex items-end gap-2 mb-3 text-sm">
            <div>
              <label className="text-[10px] uppercase text-gray-500 block">Patient</label>
              <input className="input text-sm" value={editName}
                      onChange={e => setEditName(e.target.value)} />
            </div>
            <div>
              <label className="text-[10px] uppercase text-gray-500 block">DOB</label>
              <input className="input text-sm" type="date" value={editDob}
                      onChange={e => setEditDob(e.target.value)} />
            </div>
            <div className="text-[11px] text-gray-600">
              {draft.patient_id
                ? <>✓ matched chart {draft.patient_id}</>
                : <>no chart match — saves without link</>}
            </div>
            <button className="btn-secondary text-xs ml-auto"
                    onClick={() => savePatient.mutate()}
                    disabled={savePatient.isPending}>
              <Save size={11} className="inline" /> Save patient
            </button>
          </div>

          {/* CPTs */}
          <h3 className="text-[12px] uppercase text-gray-500 mt-3 mb-1">CPT codes</h3>
          <div className="space-y-2">
            {draft.cpt_codes.map((c, i) => (
              <CPTCard key={i} entry={c} />
            ))}
          </div>

          {/* ICD-10 */}
          <h3 className="text-[12px] uppercase text-gray-500 mt-3 mb-1">ICD-10</h3>
          <div className="flex flex-wrap gap-2">
            {draft.icd10_codes.map((d, i) => (
              <span key={i} className="text-[12px] bg-gray-100 px-2 py-1 rounded">
                <strong>Pos {d.position}</strong> · <code>{d.code}</code> — {d.description}
              </span>
            ))}
          </div>

          <button className="text-xs text-muted hover:underline mt-3"
                  onClick={() => setDraft(null)}>
            <X size={11} className="inline" /> Discard this draft
          </button>
        </div>
      )}

      {/* HISTORY */}
      <div className="card !p-0 overflow-hidden">
        <h2 className="font-serif font-semibold text-ink text-[15px] p-3 border-b border-border-subtle">
          History
        </h2>
        <table className="w-full text-sm">
          <thead className="bg-plum-50 text-[11px] uppercase">
            <tr>
              <th className="table-th">Patient</th>
              <th className="table-th">DOB</th>
              <th className="table-th">Date</th>
              <th className="table-th">Payer</th>
              <th className="table-th">CPT</th>
              <th className="table-th">ICD-10</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {(history?.requests || []).map(r => (
              <tr key={r.id} className="hover:bg-plum-50/40 cursor-pointer"
                  onClick={() => setDraft(r)}>
                <td className="table-td">{r.patient_name || '—'}</td>
                <td className="table-td text-[11px]">{r.patient_dob ? fmt.date(r.patient_dob) : '—'}</td>
                <td className="table-td text-[11px]">{r.requested_at ? fmt.date(r.requested_at.slice(0, 10)) : '—'}</td>
                <td className="table-td text-[11px]">{r.payer_name || '—'}</td>
                <td className="table-td text-[11px]">
                  {(r.cpt_codes || []).map(c => c.code).join(', ') || '—'}
                </td>
                <td className="table-td text-[11px]">
                  {(r.icd10_codes || []).map(c => c.code).join(', ') || '—'}
                </td>
              </tr>
            ))}
            {!(history?.requests || []).length && (
              <tr><td colSpan={6} className="table-td text-center text-gray-400 italic py-6">
                No requests yet.
              </td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}


function CPTCard({ entry }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="border border-border-subtle rounded p-2 text-sm">
      <div className="flex items-center gap-2">
        <code className="font-semibold">{entry.code}</code>
        {(entry.modifiers || []).map(m => (
          <code key={m} className="text-[10px] bg-gray-100 px-1 rounded">-{m}</code>
        ))}
        <span className="text-[10px] text-gray-500">Pos {entry.position}</span>
        <span className="text-[10px] uppercase bg-plum-50 text-plum-700 px-1.5 py-0.5 rounded">
          {entry.justification_type.replace(/_/g, ' ')}
        </span>
        <button className="text-[11px] text-plum-700 hover:underline ml-auto"
                onClick={() => setOpen(o => !o)}>
          {open ? 'Hide' : '▶ View'} justification
        </button>
      </div>
      {entry.denial_flag && (
        <div className="mt-2 text-[11px] bg-amber-50 border border-amber-300 rounded p-2 text-amber-900">
          <AlertTriangle size={11} className="inline mr-1" />
          Likely denied by <strong>{entry.denial_flag.payer}</strong>: {entry.denial_flag.reason}
          {entry.alternative && (
            <div className="mt-1">
              Alternative: <code>{entry.alternative.code}</code>
              {entry.alternative.modifiers?.length ? ' -' + entry.alternative.modifiers.join('-') : ''}
              — {entry.alternative.rationale}
            </div>
          )}
        </div>
      )}
      {open && (
        <div className="mt-2 text-[12px] text-gray-700 bg-gray-50 rounded p-2">
          {typeof entry.justification === 'string' ? (
            <p>{entry.justification}</p>
          ) : (
            <div className="grid grid-cols-3 gap-2">
              <div><strong>Problems:</strong> {entry.justification.problems_addressed}</div>
              <div><strong>Data:</strong> {entry.justification.data_reviewed}</div>
              <div><strong>Risk:</strong> {entry.justification.risk}</div>
            </div>
          )}
          {entry.justification_type === 'e_m_time' && entry.time_minutes != null && (
            <div className="mt-1 text-[11px] text-gray-500">Time documented: {entry.time_minutes} min</div>
          )}
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Add the routes to App.jsx**

In `frontend/src/App.jsx`:
- Add to imports near the other page imports:
  ```jsx
  import CodeHelper from './pages/CodeHelper'
  import CodeHelperDenials from './pages/CodeHelperDenials'
  ```
- Add to the `<Routes>` block alongside other billing routes:
  ```jsx
  <Route path="/billing/code-helper"         element={<CodeHelper />} />
  <Route path="/billing/code-helper/denials" element={<CodeHelperDenials />} />
  ```

- [ ] **Step 3: Add the TopNav link**

In `frontend/src/components/layout/TopNav.jsx`, find where the Billing-area links are rendered (search for `'/billing'` or `Bank Recon`). Add adjacent entry:
```jsx
{ to: '/billing/code-helper', label: 'Code Helper' }
```
Match the exact array/object shape of the existing links — keep the styling unchanged.

- [ ] **Step 4: Smoke-test in the browser locally**

```bash
# terminal 1
cd backend
DATABASE_URL="postgresql+psycopg2://wwcclaudecode@localhost:5432/wwc_app_dev" \
  ./venv/bin/uvicorn app.main:app --reload --port 8000

# terminal 2
cd frontend && npm run dev
```

Open http://localhost:3000/billing/code-helper. Paste a fake note like "65yo F, T2DM, HTN, A1c 8.2". Set payer "Cigna". Click Generate. Verify spinner appears, then a result panel with at least one CPT card and ICD-10 chips. Click the CPT's "View justification" expander.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/CodeHelper.jsx frontend/src/App.jsx frontend/src/components/layout/TopNav.jsx
git commit -m "feat(code-helper): React page — input panel, result panel, history table"
```

---

### Task 10: Frontend — denial-list admin sub-page

**Files:**
- Create: `frontend/src/pages/CodeHelperDenials.jsx`

- [ ] **Step 1: Write the component**

Create `frontend/src/pages/CodeHelperDenials.jsx`:
```jsx
import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Trash2, ChevronLeft } from 'lucide-react'
import { Link } from 'react-router-dom'
import api, { fmt } from '../utils/api'


export default function CodeHelperDenials() {
  const qc = useQueryClient()
  const [showInactive, setShowInactive] = useState(false)
  const [adding, setAdding] = useState(false)
  const [form, setForm] = useState({
    code: '', code_type: 'cpt', payer_name: '', reason: '',
  })

  const { data } = useQuery({
    queryKey: ['code-helper-denials', showInactive],
    queryFn: () => api.get('/billing/code-helper/denials',
                            { params: { active: showInactive ? 'false' : 'true' } })
                       .then(r => r.data),
  })

  const create = useMutation({
    mutationFn: () => api.post('/billing/code-helper/denials', {
      code: form.code.trim(),
      code_type: form.code_type,
      payer_name: form.payer_name.trim() || null,
      reason: form.reason.trim() || null,
    }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['code-helper-denials'] })
      setAdding(false)
      setForm({ code: '', code_type: 'cpt', payer_name: '', reason: '' })
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Add failed'),
  })

  const toggleActive = useMutation({
    mutationFn: (d) => api.patch(`/billing/code-helper/denials/${d.id}`,
                                    { is_active: !d.is_active }).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['code-helper-denials'] }),
  })

  const del = useMutation({
    mutationFn: (id) => api.delete(`/billing/code-helper/denials/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['code-helper-denials'] }),
  })

  return (
    <div>
      <Link to="/billing/code-helper"
            className="text-sm text-plum-700 hover:underline inline-flex items-center gap-1 mb-3">
        <ChevronLeft size={13} /> Back to Code Helper
      </Link>
      <div className="flex items-baseline justify-between mb-4">
        <h1 className="text-2xl font-bold text-gray-900">Denial list</h1>
        <div className="flex items-center gap-2">
          <label className="text-[11px] text-gray-500">
            <input type="checkbox" checked={showInactive}
                    onChange={e => setShowInactive(e.target.checked)} />
            {' '}Show inactive
          </label>
          <button className="btn-primary text-sm flex items-center gap-1"
                  onClick={() => setAdding(true)}>
            <Plus size={12} /> Add denial
          </button>
        </div>
      </div>

      {adding && (
        <div className="card mb-3">
          <h2 className="text-sm font-semibold mb-2">New denial entry</h2>
          <div className="grid grid-cols-4 gap-2 text-sm">
            <input className="input" placeholder="Code (e.g. 97110)"
                    value={form.code} onChange={e => setForm({...form, code: e.target.value})} />
            <select className="input" value={form.code_type}
                     onChange={e => setForm({...form, code_type: e.target.value})}>
              <option value="cpt">CPT</option>
              <option value="icd10">ICD-10</option>
            </select>
            <input className="input" placeholder="Payer (blank = all)"
                    value={form.payer_name}
                    onChange={e => setForm({...form, payer_name: e.target.value})} />
            <input className="input" placeholder="Reason (optional)"
                    value={form.reason}
                    onChange={e => setForm({...form, reason: e.target.value})} />
          </div>
          <div className="flex gap-2 mt-3 justify-end">
            <button className="text-sm text-muted" onClick={() => setAdding(false)}>Cancel</button>
            <button className="btn-primary text-sm"
                    disabled={!form.code || create.isPending}
                    onClick={() => create.mutate()}>
              {create.isPending ? 'Adding…' : 'Add'}
            </button>
          </div>
        </div>
      )}

      <div className="card !p-0 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-plum-50 text-[11px] uppercase">
            <tr>
              <th className="table-th">Code</th>
              <th className="table-th">Type</th>
              <th className="table-th">Payer</th>
              <th className="table-th">Reason</th>
              <th className="table-th">Added</th>
              <th className="table-th">Active</th>
              <th className="table-th"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {(data?.denials || []).map(d => (
              <tr key={d.id} className={!d.is_active ? 'opacity-50' : ''}>
                <td className="table-td"><code>{d.code}</code></td>
                <td className="table-td text-[11px] uppercase">{d.code_type}</td>
                <td className="table-td text-[11px]">{d.payer_name || <em>all</em>}</td>
                <td className="table-td text-[11px]">{d.reason || '—'}</td>
                <td className="table-td text-[11px]">
                  {fmt.date(d.added_at.slice(0, 10))} · {d.added_by?.split('@')[0]}
                </td>
                <td className="table-td">
                  <input type="checkbox" checked={d.is_active}
                          onChange={() => toggleActive.mutate(d)} />
                </td>
                <td className="table-td">
                  <button className="text-red-600 hover:bg-red-50 p-1 rounded"
                          title="Delete"
                          onClick={() => window.confirm('Delete this entry?') && del.mutate(d.id)}>
                    <Trash2 size={12} />
                  </button>
                </td>
              </tr>
            ))}
            {!(data?.denials || []).length && (
              <tr><td colSpan={7} className="table-td text-center text-gray-400 italic py-6">
                No denial entries yet.
              </td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Manual browser smoke-test**

With the dev servers still running from Task 9, open http://localhost:3000/billing/code-helper/denials. Click **+ Add denial**, enter code `97110`, type `cpt`, payer `Cigna`, reason `not separately reimbursable`. Click Add. Verify the row appears. Toggle active off — row goes faded. Toggle on. Try Delete — confirm dialog appears, then row disappears.

- [ ] **Step 3: Verify integration with Generate flow**

Add another denial: code `99211`, payer `Cigna`. Go back to `/billing/code-helper`. Paste a note that warrants a 99211 (e.g., "Brief BP check, nurse visit, no provider"). Set payer Cigna. Generate. Verify the result CPT carries a `denial_flag` banner (the AI prompt now includes the new denial).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/CodeHelperDenials.jsx
git commit -m "feat(code-helper): denial-list admin sub-page"
```

---

### Task 11: Deploy to Cloud Run

**Files:**
- (No code changes — image build + roll)

- [ ] **Step 1: Build a new backend image tag**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
gcloud builds submit backend/ \
  --tag=us-east4-docker.pkg.dev/wwc-solutions/app/backend:v6 \
  --project=wwc-solutions --region=us-east4
```
Expected: SUCCESS, image pushed.

- [ ] **Step 2: Build a new frontend image tag**

```bash
gcloud builds submit frontend/ \
  --tag=us-east4-docker.pkg.dev/wwc-solutions/app/frontend:v3 \
  --project=wwc-solutions --region=us-east4
```
Expected: SUCCESS.

- [ ] **Step 3: Roll the Cloud Run services**

```bash
gcloud run services update backend \
  --region=us-east4 --project=wwc-solutions \
  --image=us-east4-docker.pkg.dev/wwc-solutions/app/backend:v6

gcloud run services update frontend \
  --region=us-east4 --project=wwc-solutions \
  --image=us-east4-docker.pkg.dev/wwc-solutions/app/frontend:v3
```
Expected: each `services update` reports a new revision serving 100% traffic.

- [ ] **Step 4: Roll the Cloud Run Jobs to v6 too**

```bash
for job in $(gcloud run jobs list --region=us-east4 --project=wwc-solutions --format="value(metadata.name)"); do
  gcloud run jobs update "$job" --region=us-east4 --project=wwc-solutions \
    --image=us-east4-docker.pkg.dev/wwc-solutions/app/backend:v6 --quiet
done
```
Expected: each job's image gets updated.

- [ ] **Step 5: Verify in prod**

```bash
curl -sI -m 15 https://gw.waldorfwomenscare.com/billing/code-helper | head -3
```
Expected: `HTTP/2 200`.

Then open https://gw.waldorfwomenscare.com/billing/code-helper in a browser, log in, paste a note, generate codes, save. Verify the row appears in the history table and the row click opens the details.

- [ ] **Step 6: Commit-and-tag deploy record (optional)**

The code changes themselves are already committed in previous tasks. If you'd like a release tag:
```bash
git tag -a code-helper-v1 -m "Code Helper feature initial release"
```

---

## Self-review notes

(Performed after writing the plan. Issues found and fixed inline.)

- **Spec coverage**: ✓ Schema (Task 1), AI service (Tasks 2–3), patient matcher (Task 4), denial CRUD (Task 5), POST/PDF/list/get/patch/delete (Tasks 6–8), UI page + denial admin + nav (Tasks 9–10), deploy (Task 11).
- **Placeholder scan**: ✓ Every code step has full code; no TODOs.
- **Type consistency**: ✓ `CPTEntry`, `ICD10Entry`, `AICodingResult`, `MatchResult.kind`, `CodeHelperRequest`, `CodeHelperDenial` referenced consistently across tasks.
- **Modifier validation**: spec mentions a "small built-in list of valid 2-char modifiers" — the plan does not enforce this. Decision: defer to v2; UI shows whatever the AI returns, billing staff catches obvious garbage. Add to "Open questions" in the spec if revisited.
