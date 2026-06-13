"""Steps engine: catalogs, completion, current step, behind-schedule."""
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from app.services.surgery.step_engine import (
    HOSPITAL_STEPS, OFFICE_STEPS, compute_steps, current_step, is_behind,
    _entered_at, DEFAULT_EXPECTED_DAYS_HOSPITAL,
)


def _hospital_surgery(**over):
    base = dict(
        selected_facility="medstar", chart_number="123", patient_name="X, Y",
        dob=date(1980, 1, 1), cell_phone="1", phone=None, email="a@b.c",
        address_street="s", address_city="c", address_state="MD",
        address_zip="20601", primary_insurance="i", primary_member_id="m",
        surgeon_primary="Dr", procedures=[{"cpt": "58571", "description": "TLH"}],
        diagnoses=[{"icd": "D25.9"}], estimated_minutes=180,
        eligible_facilities=["medstar"], preop_date=date(2026, 6, 1),
        auth_status="approved", clearance_required=False,
        clearance_status=None, assistant_surgeon_required=False,
        assistant_surgeon_name=None, benefits_verified_at=None,
        patient_responsibility=0, amount_paid=0, consent_status=None,
        scheduled_date=None, post_op_appt_date=None, device_required=False,
        device_assigned=False, assistant_surgeon_office_notified_at=None,
        assistant_surgeon_appt_confirmed_at=None, calendar_invite_sent_at=None,
        scheduled_in_modmed_at=None, labs_sent_to_hospital=False,
        post_op_call_status=None, operative_report_status=None,
        pathology_status="none_expected",
        payment_posted_to_billing=False, billed_at=None,
        updated_at=datetime(2026, 6, 1), created_at=datetime(2026, 5, 1),
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_catalog_sizes():
    assert len(HOSPITAL_STEPS) == 15
    assert len(OFFICE_STEPS) == 12


def test_every_step_has_default_expected_days():
    assert set(DEFAULT_EXPECTED_DAYS_HOSPITAL) == {st.key for st in HOSPITAL_STEPS}


def test_complete_surgery_info_is_done():
    steps = compute_steps(_hospital_surgery())
    assert steps[0]["key"] == "surgery_info"
    assert steps[0]["state"] == "done"


def test_missing_chart_number_is_todo():
    steps = compute_steps(_hospital_surgery(chart_number=None))
    assert steps[0]["state"] == "todo"


def test_payment_done_when_no_responsibility():
    steps = {s["key"]: s for s in compute_steps(_hospital_surgery())}
    assert steps["payment"]["state"] == "done"


def test_payment_todo_until_paid():
    s = _hospital_surgery(patient_responsibility=500, amount_paid=100)
    steps = {x["key"]: x for x in compute_steps(s)}
    assert steps["payment"]["state"] == "todo"


def test_select_dates_in_progress_with_one_of_two():
    s = _hospital_surgery(scheduled_date=date(2026, 7, 1))
    steps = {x["key"]: x for x in compute_steps(s)}
    assert steps["select_dates"]["state"] == "in_progress"


def test_device_na_unless_required_done_when_assigned():
    s1 = _hospital_surgery()
    assert {x["key"]: x for x in compute_steps(s1)}["device"]["state"] == "n/a"
    s2 = _hospital_surgery(device_required=True, device_assigned=True)
    assert {x["key"]: x for x in compute_steps(s2)}["device"]["state"] == "done"


def test_office_catalog_used_for_office():
    s = _hospital_surgery(selected_facility="office",
                           eligible_facilities=["office"])
    assert len(compute_steps(s)) == 12


def test_current_step_is_first_open_applicable():
    s = _hospital_surgery(benefits_verified_at=None)
    cur = current_step(s)
    assert cur["key"] == "benefits"


def test_is_behind_uses_expected_days():
    s = _hospital_surgery(updated_at=datetime.now() - timedelta(days=10))
    behind, hrs = is_behind(s, expected_days={"benefits": 3}, grace_hours=48)
    assert behind and hrs > 0
    behind2, _ = is_behind(s, expected_days={"benefits": 30}, grace_hours=48)
    assert not behind2


# --- #3: bill step reads billed_at, not payment_posted_to_billing ---

def test_bill_done_when_billed_at_set():
    s = _hospital_surgery(billed_at=datetime(2026, 6, 10),
                          payment_posted_to_billing=False)
    steps = {x["key"]: x for x in compute_steps(s)}
    assert steps["bill"]["state"] == "done"


def test_bill_todo_when_only_legacy_flag_set():
    # The dead payment_posted_to_billing flag must NOT mark bill done.
    s = _hospital_surgery(billed_at=None, payment_posted_to_billing=True)
    steps = {x["key"]: x for x in compute_steps(s)}
    assert steps["bill"]["state"] == "todo"


# --- #6: path_report (office) reads pathology_status ---

def _office_surgery(**over):
    over.setdefault("selected_facility", "office")
    over.setdefault("eligible_facilities", ["office"])
    return _hospital_surgery(**over)


def test_path_report_done_when_pathology_received():
    s = _office_surgery(pathology_status="received")
    steps = {x["key"]: x for x in compute_steps(s)}
    assert steps["path_report"]["state"] == "done"
    s2 = _office_surgery(pathology_status="completed")
    steps2 = {x["key"]: x for x in compute_steps(s2)}
    assert steps2["path_report"]["state"] == "done"


def test_path_report_na_when_not_required():
    s = _office_surgery(pathology_status="not_required")
    steps = {x["key"]: x for x in compute_steps(s)}
    assert steps["path_report"]["state"] == "n/a"


def test_path_report_todo_when_expected():
    s = _office_surgery(pathology_status="expected")
    steps = {x["key"]: x for x in compute_steps(s)}
    assert steps["path_report"]["state"] == "todo"


# --- #15: _entered_at anchors on the most recent completion signal ---

def test_entered_at_not_anchored_to_stale_early_stamp():
    """A surgery whose current step is late in the flow must NOT be
    anchored to a weeks-old benefits_verified_at stamp. The only stamped
    step here is benefits (old); modmed_appt (current step's predecessor)
    has a recent stamp, and the surgery was recently worked (updated_at).
    """
    old = datetime(2026, 5, 1)            # weeks ago
    recent = datetime(2026, 6, 11)        # ~yesterday
    # Drive the surgery all the way to the bill step: every earlier step
    # done/n-a. benefits has an OLD stamp; modmed_appt has a RECENT stamp.
    s = _hospital_surgery(
        benefits_verified_at=date(2026, 5, 1),       # stale early stamp
        scheduled_date=date(2026, 5, 15),
        post_op_appt_date=date(2026, 5, 20),
        consent_status="signed",
        calendar_invite_sent_at=old,                 # post_to_hospital
        scheduled_in_modmed_at=recent,               # modmed_appt (recent)
        labs_sent_to_hospital=True,
        post_op_call_status="Spoke to Pt.",
        operative_report_status="completed",         # notes_reports
        billed_at=None,                              # current step = bill
        updated_at=recent,
        created_at=old,
    )
    assert current_step(s)["key"] == "bill"
    anchor = _entered_at(s)
    # Must be the recent modmed stamp, never the stale benefits date.
    assert anchor == recent
    # And with a normal expected-days window it is not flagged overdue.
    behind, _ = is_behind(s, grace_hours=48)
    assert not behind
