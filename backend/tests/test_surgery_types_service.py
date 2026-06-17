"""SurgeryType service: list/create/update/soft-delete/reorder + validation."""
import pytest
from fastapi import HTTPException

from app.models.surgery import ConsentTemplate
from app.models.surgery_type import SurgeryType
from app.services.surgery import surgery_types as svc


def _tmpl(db, name="Hysteroscopy Consent"):
    t = ConsentTemplate(name=name, cpt_codes=["58558"], procedure_match=[],
                        facility_match=[], insurance_match=[])
    db.add(t); db.commit(); db.refresh(t)
    return t


def test_create_validates_and_persists(db):
    tmpl = _tmpl(db)
    row = svc.create_type(db, {
        "name": "Hysteroscopy with D&C",
        "cpts": [{"cpt": "58558", "description": "Hysteroscopy with D&C"}],
        "classification": "minor",
        "eligible_facilities": ["medstar", "office"],
        "consent_template_ids": [str(tmpl.id), "not-a-real-id"],
    })
    assert row.id is not None
    assert row.consent_template_ids == [str(tmpl.id)]          # unknown id dropped
    assert row.eligible_facilities == ["medstar", "office"]


def test_create_rejects_bad_input(db):
    with pytest.raises(HTTPException) as e1:
        svc.create_type(db, {"name": "", "cpts": [{"cpt": "1", "description": "x"}]})
    assert e1.value.status_code == 422
    with pytest.raises(HTTPException):                          # empty cpts
        svc.create_type(db, {"name": "X", "cpts": []})
    with pytest.raises(HTTPException):                          # bad classification
        svc.create_type(db, {"name": "X", "cpts": [{"cpt": "1", "description": "x"}],
                             "classification": "huge"})
    with pytest.raises(HTTPException):                          # bad facility
        svc.create_type(db, {"name": "X", "cpts": [{"cpt": "1", "description": "x"}],
                             "eligible_facilities": ["mars"]})


def test_list_excludes_inactive_by_default(db):
    a = svc.create_type(db, {"name": "A", "cpts": [{"cpt": "1", "description": "a"}]})
    svc.create_type(db, {"name": "B", "cpts": [{"cpt": "2", "description": "b"}]})
    svc.set_active(db, str(a.id), False)
    assert [t.name for t in svc.list_types(db)] == ["B"]
    assert {t.name for t in svc.list_types(db, include_inactive=True)} == {"A", "B"}


def test_update_and_reorder(db):
    a = svc.create_type(db, {"name": "A", "cpts": [{"cpt": "1", "description": "a"}]})
    b = svc.create_type(db, {"name": "B", "cpts": [{"cpt": "2", "description": "b"}]})
    svc.update_type(db, str(a.id), {"name": "A2",
                                    "cpts": [{"cpt": "1", "description": "a"}],
                                    "classification": "major"})
    assert db.get(SurgeryType, a.id).name == "A2"
    assert db.get(SurgeryType, a.id).classification == "major"
    svc.reorder(db, [str(b.id), str(a.id)])
    assert [t.name for t in svc.list_types(db)] == ["B", "A2"]


def test_as_picklist_shape(db):
    svc.create_type(db, {"name": "A", "cpts": [{"cpt": "1", "description": "a"}],
                         "classification": "office", "eligible_facilities": ["office"]})
    pl = svc.as_picklist(svc.list_types(db))
    assert pl[0].keys() >= {"id", "name", "cpts", "classification",
                            "eligible_facilities", "consent_template_ids"}
    assert pl[0]["classification"] == "office"
