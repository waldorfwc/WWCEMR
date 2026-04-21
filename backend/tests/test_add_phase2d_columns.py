"""Tests for Phase 2d column migration."""
from sqlalchemy import text


def _cols(db) -> set:
    return {row[1] for row in db.execute(text("PRAGMA table_info(claims)")).fetchall()}


def test_migration_adds_all_four_columns(db):
    from app.scripts.add_phase2d_columns import run
    # Drop the 4 columns from the pristine test DB if they slipped in via create_all
    for col in ("follow_up_date", "follow_up_reason",
                "last_submission_date", "claim_state"):
        try:
            db.execute(text(f"ALTER TABLE claims DROP COLUMN {col}"))
        except Exception:
            pass
    db.commit()
    before = _cols(db)
    for col in ("follow_up_date", "follow_up_reason",
                "last_submission_date", "claim_state"):
        assert col not in before, f"precondition failed: {col} still in schema"

    result = run(session=db)
    after = _cols(db)
    assert set(result["added"]) == {
        "follow_up_date", "follow_up_reason",
        "last_submission_date", "claim_state",
    }
    assert result["skipped"] == []
    for col in result["added"]:
        assert col in after


def test_migration_is_idempotent(db):
    from app.scripts.add_phase2d_columns import run
    run(session=db)           # add them
    second = run(session=db)  # re-run
    assert second["added"] == []
    assert set(second["skipped"]) == {
        "follow_up_date", "follow_up_reason",
        "last_submission_date", "claim_state",
    }
