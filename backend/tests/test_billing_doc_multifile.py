"""Multiple files under one Insurance Documents row."""

_PNG = b"\x89PNG\r\n\x1a\n"


def _upload(client, files, **form):
    return client.post("/api/billing/documents", files=files,
                       data={"classification": "paper_eob",
                             "auto_classify": "false", **form})


def test_multi_file_upload_one_row(client, db, tmp_path, monkeypatch):
    monkeypatch.setenv("BILLING_DOCS_STORAGE_PATH", str(tmp_path))
    r = _upload(client, [("files", ("a.png", _PNG + b"AAA", "image/png")),
                         ("files", ("b.png", _PNG + b"BBB", "image/png"))])
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["file_count"] == 2
    assert body["files"][0]["is_primary"] is True
    assert body["files"][0]["original_filename"] == "a.png"
    assert body["files"][1]["original_filename"] == "b.png"
    # the extra file downloads
    rr = client.get("/api" + body["files"][1]["download_url"])
    assert rr.status_code == 200 and rr.content == _PNG + b"BBB"


def test_single_file_still_works(client, db, tmp_path, monkeypatch):
    monkeypatch.setenv("BILLING_DOCS_STORAGE_PATH", str(tmp_path))
    r = _upload(client, [("files", ("x.png", _PNG + b"X", "image/png"))])
    assert r.status_code == 201, r.text
    assert r.json()["file_count"] == 1


def test_add_files_to_existing_row(client, db, tmp_path, monkeypatch):
    monkeypatch.setenv("BILLING_DOCS_STORAGE_PATH", str(tmp_path))
    doc_id = _upload(client, [("files", ("x.png", _PNG + b"1", "image/png"))]).json()["id"]
    r = client.post(f"/api/billing/documents/{doc_id}/files",
                    files=[("files", ("y.png", _PNG + b"2", "image/png")),
                           ("files", ("z.png", _PNG + b"3", "image/png"))])
    assert r.status_code == 201, r.text
    assert r.json()["file_count"] == 3


def test_duplicate_extra_file_skipped_within_row(client, db, tmp_path, monkeypatch):
    monkeypatch.setenv("BILLING_DOCS_STORAGE_PATH", str(tmp_path))
    same = _PNG + b"DUP"
    r = _upload(client, [("files", ("a.png", same, "image/png")),
                         ("files", ("a-copy.png", same, "image/png"))])
    assert r.status_code == 201, r.text
    assert r.json()["file_count"] == 1   # exact-content dup not attached twice
