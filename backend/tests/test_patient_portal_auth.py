"""Portal auth helpers — code lifecycle + JWT TTL."""
from datetime import date, datetime, timedelta
from unittest.mock import patch

from app.models.surgery import Surgery
from app.services.patient_portal_auth import (
    issue_challenge, verify_code, issue_portal_token,
    verify_portal_token, compute_token_exp,
)


def _make_surgery(db, scheduled_date=None):
    s = Surgery(chart_number="1", patient_name="Pat",
                  cell_phone="+12405551234",
                  scheduled_date=scheduled_date,
                  status="new")
    db.add(s); db.commit(); db.refresh(s)
    return s


# ─── token TTL ──────────────────────────────────────────────────

def test_token_exp_uses_surgery_date_plus_30(db):
    s = _make_surgery(db, scheduled_date=date(2026, 7, 1))
    exp = compute_token_exp(s, now=datetime(2026, 5, 1))
    assert exp.date() == date(2026, 7, 31)   # 2026-07-01 + 30


def test_token_exp_falls_back_when_no_date(db):
    s = _make_surgery(db, scheduled_date=None)
    now = datetime(2026, 5, 1, 9, 0)
    exp = compute_token_exp(s, now=now)
    assert exp.date() == date(2026, 5, 31)   # today + 30


def test_token_exp_floors_at_today_plus_30(db):
    # Surgery already happened yesterday; sign-in for post-op.
    s = _make_surgery(db, scheduled_date=date(2026, 4, 30))
    now = datetime(2026, 5, 1, 9, 0)
    exp = compute_token_exp(s, now=now)
    # max(today, scheduled_date) + 30 = 2026-05-31
    assert exp.date() == date(2026, 5, 31)


# ─── challenge / verify cycle ────────────────────────────────────

def test_issue_challenge_creates_code_and_sms(db):
    s = _make_surgery(db)
    with patch("app.services.patient_portal_auth.send_sms",
                return_value=True) as mock_sms:
        challenge_token, code = issue_challenge(db, s)
    assert len(challenge_token) >= 32
    assert len(code) == 6 and code.isdigit()
    mock_sms.assert_called_once()
    # SMS body contains the code
    args, kwargs = mock_sms.call_args
    assert code in args[1]


def test_verify_code_success_marks_used(db):
    s = _make_surgery(db)
    with patch("app.services.patient_portal_auth.send_sms",
                return_value=True):
        challenge_token, code = issue_challenge(db, s)
    surgery_id = verify_code(db, challenge_token, code)
    assert surgery_id == s.id
    # Replay attempt should fail
    assert verify_code(db, challenge_token, code) is None


def test_verify_code_wrong_increments_fail_count(db):
    s = _make_surgery(db)
    with patch("app.services.patient_portal_auth.send_sms",
                return_value=True):
        challenge_token, _ = issue_challenge(db, s)
    assert verify_code(db, challenge_token, "000000") is None
    assert verify_code(db, challenge_token, "000000") is None
    assert verify_code(db, challenge_token, "000000") is None
    # 4th attempt — challenge dead
    assert verify_code(db, challenge_token, "000000") is None


def test_jwt_roundtrip(db):
    s = _make_surgery(db, scheduled_date=date(2026, 6, 1))
    token = issue_portal_token(s)
    assert verify_portal_token(token) == s.id
