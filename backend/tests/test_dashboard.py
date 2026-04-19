"""Tests for GET /api/dashboard/summary."""


def test_dashboard_summary_empty_db_returns_zeros(client):
    r = client.get("/api/dashboard/summary")
    assert r.status_code == 200
    data = r.json()

    assert data["collected_30d"] == 0
    assert data["collected_prior_30d"] == 0
    assert data["outstanding_total"] == 0
    assert data["outstanding_count"] == 0
    assert data["open_claims"] == 0
    assert data["claims_submitted_7d"] == 0
    assert data["timely_filing_at_risk_7d"] == 0
    assert data["denied_open"] == 0
    assert data["denied_delta_7d"] == 0

    assert data["resolved"] == {
        "30d": {"count": 0, "collected": 0},
        "60d": {"count": 0, "collected": 0},
        "90d": {"count": 0, "collected": 0},
    }
    assert data["attention"] == {
        "timely_filing": 0,
        "eras_unposted": 0,
        "fax_failures": 0,
    }


def test_dashboard_summary_shape_is_complete(client):
    """Contract test: every documented top-level key is present."""
    r = client.get("/api/dashboard/summary")
    assert r.status_code == 200
    expected_keys = {
        "collected_30d", "collected_prior_30d",
        "outstanding_total", "outstanding_count",
        "open_claims", "claims_submitted_7d",
        "timely_filing_at_risk_7d",
        "resolved", "denied_open", "denied_delta_7d",
        "attention",
    }
    assert expected_keys.issubset(r.json().keys())
