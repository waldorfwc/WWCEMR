"""Manager behind-schedule sweep (audit #9).

Regression: the sweep used to drive entirely off SurgeryMilestone rows,
which were retired in the 2026-06 steps cutover. With no milestones the
old code returned current_milestone=None for every surgery and silently
skipped the whole digest. The fix reimplements candidate selection on the
steps engine, so a surgery with NO milestone rows but a stale current step
is picked up.
"""
from datetime import date, datetime, timedelta

from app.models.surgery import Surgery
from app.services.surgery import escalations


def _behind_hospital_surgery(db, **over):
    """A complete-info hospital surgery whose current step is `benefits`
    (benefits_verified_at=None) and whose updated_at is weeks old, so the
    steps engine flags it behind schedule. No SurgeryMilestone rows."""
    base = dict(
        chart_number="C100", patient_name="Behind, Pat",
        dob=date(1980, 1, 1), cell_phone="240-555-0100", email="p@x.c",
        address_street="1 St", address_city="Waldorf",
        address_state="MD", address_zip="20601",
        primary_insurance="Aetna", primary_member_id="M1",
        surgeon_primary="Dr. A",
        procedures=[{"cpt": "58571", "description": "TLH"}],
        diagnoses=[{"icd": "D25.9"}], estimated_minutes=180,
        eligible_facilities=["medstar"], selected_facility="medstar",
        preop_date=date(2026, 6, 1), auth_status="approved",
        status="in_progress",
        benefits_verified_at=None,            # → benefits step is the current todo
        updated_at=datetime.utcnow() - timedelta(days=21),
        created_at=datetime.utcnow() - timedelta(days=30),
    )
    base.update(over)
    s = Surgery(**base)
    db.add(s)
    db.commit()
    return s


def test_find_behind_surgeries_picks_up_milestone_free_surgery(db):
    # No SurgeryMilestone rows exist; the old code would skip this entirely.
    s = _behind_hospital_surgery(db)
    assert s.milestones == []          # confirm milestone-free precondition

    candidates = escalations.find_behind_surgeries(db)

    ids = [str(cs.id) for cs, _step, _hrs in candidates]
    assert str(s.id) in ids
    # The current step surfaced for the digest is the benefits step.
    chosen = next(c for c in candidates if str(c[0].id) == str(s.id))
    assert chosen[1]["key"] == "benefits"
    assert chosen[2] > 0               # hours_overdue


def test_on_schedule_surgery_not_picked_up(db):
    # Same surgery but freshly updated → not behind.
    _behind_hospital_surgery(
        db, chart_number="C101",
        updated_at=datetime.utcnow(), created_at=datetime.utcnow(),
    )
    candidates = escalations.find_behind_surgeries(db)
    assert all(c[0].chart_number != "C101" for c in candidates)


def test_completed_surgery_not_picked_up(db):
    # status not in the active set → never scanned.
    _behind_hospital_surgery(db, chart_number="C102", status="completed")
    candidates = escalations.find_behind_surgeries(db)
    assert all(c[0].chart_number != "C102" for c in candidates)


def test_sweep_is_idempotent_per_step(db):
    s = _behind_hospital_surgery(db, escalate_to_email=None)
    # Pre-mark the benefits step as already escalated.
    s.escalation_state = {"benefits": datetime.utcnow().isoformat()}
    db.commit()

    res = escalations.run_escalation_sweep(db)
    # Already escalated for this step → nobody notified, nothing marked.
    assert res["surgeries_escalated"] == 0
