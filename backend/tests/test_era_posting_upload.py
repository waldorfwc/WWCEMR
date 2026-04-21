"""Upload/preview tests for ERA posting endpoint (supports multi-file)."""
from pathlib import Path
from app.services import import_sessions

FIXTURE = Path(__file__).parent / "fixtures" / "johns_hopkins_era.835"


def _upload_one(client):
    import_sessions._sessions.clear()
    with FIXTURE.open("rb") as f:
        return client.post(
            "/api/imports/era-posting",
            files=[("file", (FIXTURE.name, f, "application/octet-stream"))],
        )


def _upload_multi(client, count=3):
    import_sessions._sessions.clear()
    data = FIXTURE.read_bytes()
    return client.post(
        "/api/imports/era-posting",
        files=[("file", (f"era{i}.835", data, "application/octet-stream"))
               for i in range(count)],
    )


def test_upload_single_era_returns_preview(client, db):
    r = _upload_one(client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["totals"]["n_files"] == 1
    assert body["totals"]["n_unmatched"] == 18  # no claims seeded
    assert len(body["files"]) == 1
    assert body["files"][0]["check_number"] == "355174145"


def test_upload_multiple_eras_combined_preview(client, db):
    r = _upload_multi(client, count=3)
    assert r.status_code == 200
    body = r.json()
    assert body["totals"]["n_files"] == 3
    assert body["totals"]["n_unmatched"] == 54  # 18 * 3


def test_upload_rejects_non_era(client, db):
    r = client.post(
        "/api/imports/era-posting",
        files=[("file", ("x.pdf", b"%PDF-1.4\n%noteraly", "application/pdf"))],
    )
    assert r.status_code == 422


def test_upload_forbidden_for_clinical(clinical_client, db):
    r = _upload_one(clinical_client)
    assert r.status_code == 403
