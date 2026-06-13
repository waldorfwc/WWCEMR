"""Dashboard behind-schedule runs on steps — new surgeries are alertable."""
from datetime import datetime, timedelta
from app.models.surgery import Surgery


def test_new_surgery_without_milestones_can_be_behind(db):
    """The cutover's point: a surgery with zero milestone rows is still
    alertable when its current step is old. _is_behind_steps is pure over
    the row, so a SimpleNamespace-like ORM row works."""
    from app.routers.surgery import _is_behind_steps
    from tests.test_step_engine import _hospital_surgery
    s = _hospital_surgery(benefits_verified_at=None,
                           updated_at=datetime.now() - timedelta(days=30))
    behind, hours = _is_behind_steps(db, s)
    assert behind and hours > 0


def test_serializer_emits_steps(db):
    """_surgery_dict includes the steps array + current_step fields."""
    from app.routers.surgery import _surgery_dict
    s = Surgery(chart_number="1", patient_name="Pat", selected_facility="medstar",
                status="new", eligible_facilities=["medstar"])
    db.add(s); db.flush()
    d = _surgery_dict(db, s)
    assert isinstance(d["steps"], list) and len(d["steps"]) in (12, 15)
    assert "current_step" in d and "current_step_title" in d
    assert "steps" in d
