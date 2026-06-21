from app.models.manual import ManualSection
from app.services.manual_seed import seed_manuals, MANUAL_SEEDS


def test_seed_is_additive_only(db):
    db.add(ManualSection(module="device_larc", slug="overview",
                        title="Custom", body_md="PRACTICE EDIT", sort_order=10))
    db.commit()
    seed_manuals(db)
    overview = (db.query(ManualSection)
                  .filter_by(module="device_larc", slug="overview").one())
    assert overview.body_md == "PRACTICE EDIT"          # not clobbered
    n = db.query(ManualSection).filter_by(module="device_larc").count()
    assert n > 1


def test_every_registered_module_seeds_at_least_one(db):
    seed_manuals(db)
    for module in MANUAL_SEEDS:
        n = db.query(ManualSection).filter_by(module=module).count()
        assert n >= 1, f"{module} seeded nothing"
