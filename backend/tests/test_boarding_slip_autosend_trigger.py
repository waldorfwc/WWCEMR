"""Manual trigger endpoint for the boarding-slip auto-email sweep."""


def test_trigger_returns_sweep_result(client, db):
    # Default config has auto-email disabled → sweep reports it skipped.
    r = client.post("/api/surgery/admin/run-boarding-slip-autosend")
    assert r.status_code == 200, r.text
    body = r.json()
    # Either disabled (default) or a counts dict — both are valid shapes.
    assert "skipped" in body or "sent" in body
