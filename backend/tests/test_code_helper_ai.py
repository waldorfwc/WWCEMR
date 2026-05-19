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


# ---------------------------------------------------------------------------
# Task 3: prompt assembly + extraction
# ---------------------------------------------------------------------------
from unittest.mock import MagicMock
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
    # Mock an Anthropic API Message response with one tool_use block.
    # NOTE: MagicMock(name=...) sets the mock's internal repr-name, not an
    # attribute — so we must set .name explicitly after construction.
    tool_block = MagicMock(type="tool_use")
    tool_block.name = "submit_coding"
    tool_block.input = {
        "patient_name": "Jane Smith",
        "patient_dob":  "1985-03-12",
        "cpt_codes": [{
            "code": "99214", "modifiers": ["25"], "position": 1,
            "justification_type": "e_m_mdm",
            "justification": {"problems_addressed": "Mod",
                               "data_reviewed": "Ltd", "risk": "Mod"},
        }],
        "icd10_codes": [{"code": "I10", "position": 1, "description": "HTN"}],
    }
    fake_response = MagicMock()
    fake_response.content = [tool_block]
    fake_response.usage = MagicMock(input_tokens=1200, output_tokens=400)

    result, usage = extract_tool_input(fake_response)
    assert result.patient_name == "Jane Smith"
    assert result.cpt_codes[0].code == "99214"
    assert usage["input_tokens"] == 1200


def test_extract_tool_input_raises_when_no_tool_use_block():
    fake_response = MagicMock()
    fake_response.content = []
    fake_response.usage = MagicMock(input_tokens=10, output_tokens=0)
    with pytest.raises(RuntimeError, match="submit_coding"):
        extract_tool_input(fake_response)
