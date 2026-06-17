"""seed_surgery_types: one-time idempotent seed from picklists.PROCEDURES,
classifying each entry via MAJOR_CPTS."""
from app.models.surgery_type import SurgeryType
from app.services.surgery.picklists import PROCEDURES
from app.services.surgery.surgery_type_seed import seed_surgery_types


def test_seed_populates_once_and_is_idempotent(db):
    n = seed_surgery_types(db)
    assert n == len(PROCEDURES)
    assert db.query(SurgeryType).count() == len(PROCEDURES)
    # Second call seeds nothing (table non-empty).
    assert seed_surgery_types(db) == 0
    assert db.query(SurgeryType).count() == len(PROCEDURES)


def test_seed_classification_and_shape(db):
    seed_surgery_types(db)
    major = db.query(SurgeryType).filter(SurgeryType.cpts.isnot(None)).all()
    by_cpt = {t.cpts[0]["cpt"]: t for t in major}
    # 49320 Diagnostic laparoscopy is in MAJOR_CPTS → major.
    assert by_cpt["49320"].classification == "major"
    # 58558 is not in MAJOR_CPTS → minor.
    assert by_cpt["58558"].classification == "minor"
    # Each seeded type has a single-CPT row mirroring the source entry.
    t = by_cpt["58558"]
    assert t.cpts == [{"cpt": "58558", "description": t.name}]
    assert t.eligible_facilities == [] and t.consent_template_ids == [] and t.active is True
