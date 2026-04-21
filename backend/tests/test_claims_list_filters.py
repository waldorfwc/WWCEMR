"""Tests for GET /api/claims list filtering on Phase 2d fields."""
from datetime import date, timedelta
from decimal import Decimal
from app.models.claim import Claim, ClaimStatus


def _seed(db, *, claim_number, claim_state=None, follow_up_date=None, status=ClaimStatus.PENDING):
    c = Claim(
        claim_number=claim_number,
        status=status, balance=Decimal("0"),
        billed_amount=Decimal("100"),
        claim_state=claim_state,
        follow_up_date=follow_up_date,
    )
    db.add(c)


def test_list_claims_filter_state_open(client, db):
    _seed(db, claim_number="A", claim_state="Open")
    _seed(db, claim_number="B", claim_state="Closed")
    _seed(db, claim_number="C", claim_state=None)
    db.commit()
    r = client.get("/api/claims", params={"state": "open", "per_page": 100})
    assert r.status_code == 200
    nums = {c["claim_number"] for c in r.json()["claims"]}
    assert nums == {"A"}


def test_list_claims_filter_state_closed(client, db):
    _seed(db, claim_number="A", claim_state="Open")
    _seed(db, claim_number="B", claim_state="Closed")
    db.commit()
    r = client.get("/api/claims", params={"state": "closed", "per_page": 100})
    assert {c["claim_number"] for c in r.json()["claims"]} == {"B"}


def test_list_claims_filter_has_followup_true(client, db):
    today = date.today()
    _seed(db, claim_number="OVERDUE", claim_state="Open",
          follow_up_date=today - timedelta(days=3))
    _seed(db, claim_number="TODAY", claim_state="Open", follow_up_date=today)
    _seed(db, claim_number="FUTURE", claim_state="Open",
          follow_up_date=today + timedelta(days=10))
    _seed(db, claim_number="NO_DATE", claim_state="Open", follow_up_date=None)
    _seed(db, claim_number="OVERDUE_CLOSED", claim_state="Closed",
          follow_up_date=today - timedelta(days=3))
    db.commit()

    r = client.get("/api/claims", params={"has_followup": "true", "per_page": 100})
    nums = {c["claim_number"] for c in r.json()["claims"]}
    # has_followup=true → Open state + follow_up_date <= today (overdue OR due today)
    assert nums == {"OVERDUE", "TODAY"}


def test_claim_response_includes_new_fields(client, db):
    _seed(db, claim_number="X", claim_state="Open",
          follow_up_date=date(2026, 3, 15))
    db.commit()
    # List response
    r = client.get("/api/claims", params={"search": "X"})
    claim = r.json()["claims"][0]
    assert "claim_state" in claim
    assert "follow_up_date" in claim
    assert "follow_up_reason" in claim
    assert "last_submission_date" in claim
    assert claim["claim_state"] == "Open"
    assert claim["follow_up_date"] == "2026-03-15"
