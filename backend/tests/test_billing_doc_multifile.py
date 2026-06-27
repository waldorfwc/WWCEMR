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


def test_primary_and_detail_after_multifile(client, db, tmp_path, monkeypatch):
    monkeypatch.setenv("BILLING_DOCS_STORAGE_PATH", str(tmp_path))
    r = _upload(client, [("files", ("p.png", _PNG + b"PRIMARY", "image/png")),
                         ("files", ("e.png", _PNG + b"EXTRA", "image/png"))])
    assert r.status_code == 201, r.text
    doc_id = r.json()["id"]
    # primary download (the left ImageViewer path) still works
    rr = client.get(f"/api/billing/documents/{doc_id}/file")
    assert rr.status_code == 200, rr.text
    assert rr.content == _PNG + b"PRIMARY"
    # detail GET returns the files array
    det = client.get(f"/api/billing/documents/{doc_id}").json()
    assert det["file_count"] == 2
    assert det["files"][0]["download_url"] == f"/billing/documents/{doc_id}/file"


def test_content_disposition_handles_nonlatin1_filename():
    # The   narrow no-break space (and any non-latin-1 char) must not crash
    # the HTTP header encoding (was a 500 UnicodeEncodeError).
    from app.routers.billing_documents import _content_disposition
    cd = _content_disposition("EOB 12 345.pdf")
    cd.encode("latin-1")                      # must NOT raise
    assert "filename*=UTF-8''" in cd
    assert 'filename="EOB' in cd              # ascii fallback present


def test_download_with_nonlatin1_filename(client, db, tmp_path, monkeypatch):
    monkeypatch.setenv("BILLING_DOCS_STORAGE_PATH", str(tmp_path))
    r = _upload(client, [("files", ("EOB 1234.png", _PNG + b"NB", "image/png"))])
    assert r.status_code == 201, r.text
    doc_id = r.json()["id"]
    rr = client.get(f"/api/billing/documents/{doc_id}/file")
    assert rr.status_code == 200, rr.text     # was 500
    assert rr.content == _PNG + b"NB"
