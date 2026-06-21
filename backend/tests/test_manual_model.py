from app.models.manual import ManualSection


def test_manual_section_columns_and_table(db):
    row = ManualSection(module="device_larc", slug="overview", title="Overview",
                        body_md="hi", sort_order=10, updated_by="system:seed")
    db.add(row); db.commit(); db.refresh(row)
    assert row.id is not None
    assert row.module == "device_larc"
    assert row.created_at is not None and row.updated_at is not None


def test_manual_section_unique_per_module(db):
    db.add(ManualSection(module="surgery", slug="overview", title="A", body_md=""))
    db.add(ManualSection(module="pellets", slug="overview", title="B", body_md=""))
    db.commit()  # same slug, different module -> OK
    from sqlalchemy.exc import IntegrityError
    db.add(ManualSection(module="surgery", slug="overview", title="dup", body_md=""))
    try:
        db.commit()
        assert False, "expected unique violation on (module, slug)"
    except IntegrityError:
        db.rollback()
