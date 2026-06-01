"""Waystar EOB report download — GCS via serve_blob."""
from unittest.mock import patch


def test_download_eob_report_via_serve_blob(client):
    from fastapi.responses import Response
    with patch("app.routers.waystar.serve_blob",
                return_value=Response(content=b"era data",
                                          media_type="text/plain")) as mock:
        r = client.get("/api/waystar/eob-report/test.era")
    assert r.status_code == 200, r.text
    _, kwargs = mock.call_args
    assert kwargs["gcs_object"] == "waystar-reports/test.era"
    assert kwargs["local_path"] is None


def test_download_eob_report_sanitizes_filename(client):
    """Path-traversal via %2F is blocked at the framework level (404 before
    our handler runs). FastAPI decodes %2F → '/' which makes the URL
    a two-segment path that doesn't match the {filename} route pattern,
    so the endpoint is never reached — a stronger guard than basename alone."""
    from fastapi.responses import Response
    with patch("app.routers.waystar.serve_blob",
                return_value=Response(content=b"x")) as mock:
        r = client.get("/api/waystar/eob-report/..%2Fevil.txt")
    # FastAPI returns 404 — route simply doesn't match. serve_blob never called.
    assert r.status_code == 404
    mock.assert_not_called()
