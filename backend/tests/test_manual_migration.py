from sqlalchemy import text
from app.models.manual import ManualSection
from app.database import _migrate_manuals_to_unified


def _make_old_table(db, name):
    db.execute(text(f"""
        CREATE TABLE IF NOT EXISTS {name} (
            id VARCHAR(36), slug VARCHAR(80), title VARCHAR(200), body_md TEXT,
            sort_order INTEGER, created_at TIMESTAMP, updated_at TIMESTAMP,
            updated_by VARCHAR(200))"""))
    db.commit()


def test_migration_copies_edits_and_is_idempotent(db):
    _make_old_table(db, "larc_manual_sections")
    db.execute(text("""INSERT INTO larc_manual_sections
        (id, slug, title, body_md, sort_order, created_at, updated_at, updated_by)
        VALUES ('x1','overview','Overview','EDITED BODY',10,
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 'staff@wwc.com')"""))
    db.commit()

    _migrate_manuals_to_unified(db)

    rows = db.query(ManualSection).filter_by(module="device_larc").all()
    assert len(rows) == 1
    assert rows[0].slug == "overview" and rows[0].body_md == "EDITED BODY"
    assert rows[0].updated_by == "staff@wwc.com"

    _migrate_manuals_to_unified(db)
    rows2 = db.query(ManualSection).filter_by(module="device_larc").all()
    assert len(rows2) == 1 and rows2[0].body_md == "EDITED BODY"
