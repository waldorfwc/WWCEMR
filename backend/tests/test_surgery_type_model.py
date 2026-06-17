"""The SurgeryType model: columns, defaults, and that init_db creates the table."""
from app.models.surgery_type import SurgeryType


def test_surgery_type_defaults(db):
    t = SurgeryType(name="Diagnostic hysteroscopy",
                    cpts=[{"cpt": "58555", "description": "Diagnostic hysteroscopy"}])
    db.add(t); db.commit(); db.refresh(t)
    assert t.id is not None
    assert t.classification == "minor"          # default
    assert t.eligible_facilities == []          # default
    assert t.consent_template_ids == []         # default
    assert t.active is True                      # default
    assert t.sort_order == 0                     # default
    assert t.created_at is not None and t.updated_at is not None
    assert t.cpts == [{"cpt": "58555", "description": "Diagnostic hysteroscopy"}]
