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
