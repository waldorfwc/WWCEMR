"""Intake doc path → GCS key mapping."""
from unittest.mock import patch

from app.routers.intake import _intake_gcs_object


def test_empty_path_returns_empty():
    assert _intake_gcs_object("") == ""
    assert _intake_gcs_object(None) == ""


def test_legacy_external_drive_path():
    p = "/Volumes/OWC External/IntakeArchive/1975/04/04/Jane Doe 04-04-1975/Foo/file.pdf"
    assert _intake_gcs_object(p) == "intake/1975/04/04/Jane Doe 04-04-1975/Foo/file.pdf"


def test_legacy_intake_archive_substring():
    p = "/anywhere/IntakeArchive/2000/01/02/x.pdf"
    assert _intake_gcs_object(p) == "intake/2000/01/02/x.pdf"


def test_wwc_intake_docs_resolves_via_archive_lookup():
    """The new Mac Mini Downloads path needs the archive folder resolved."""
    p = ("/Users/wwcclaudecode/Downloads/wwc_intake_docs/1975/04/04/"
         "Latanya Pelt 04-04-1975/Practice Agreements/file.pdf")
    with patch("app.routers.intake._intake_archives_for_year",
                return_value=("1975-20260417T074958Z-3-001",)):
        got = _intake_gcs_object(p)
    assert got == ("intake/1975-20260417T074958Z-3-001/1975/04/04/"
                      "Latanya Pelt 04-04-1975/Practice Agreements/file.pdf")


def test_wwc_intake_docs_returns_empty_when_no_archive_matches():
    p = "/Users/wwcclaudecode/Downloads/wwc_intake_docs/1980/01/01/X.pdf"
    with patch("app.routers.intake._intake_archives_for_year",
                return_value=()):
        assert _intake_gcs_object(p) == ""


def test_wwc_intake_docs_multi_archive_probes_until_match(monkeypatch):
    """Multiple archives for the same year: probe each via blob.exists()."""
    p = ("/Users/wwcclaudecode/Downloads/wwc_intake_docs/1069/03/15/"
         "Pat X 03-15-1069/Cat/file.pdf")
    monkeypatch.setattr("app.routers.intake._intake_archives_for_year",
                          lambda y: ("1069-archiveA", "1069-archiveB"))

    # First archive: blob doesn't exist; second: it does
    class FakeBlob:
        def __init__(self, name): self.name = name
        def exists(self): return "archiveB" in self.name

    class FakeBucket:
        def blob(self, name): return FakeBlob(name)

    class FakeClient:
        def bucket(self, _): return FakeBucket()

    import sys, types
    fake = types.ModuleType("google.cloud.storage")
    fake.Client = lambda: FakeClient()
    monkeypatch.setitem(sys.modules, "google.cloud.storage", fake)
    # google.cloud.storage is accessed via `from google.cloud import storage`
    # so we also patch the inner module path.
    import google.cloud as gc  # noqa
    monkeypatch.setattr("google.cloud.storage", fake, raising=False)

    got = _intake_gcs_object(p)
    assert "1069-archiveB" in got
    assert got.endswith("/Cat/file.pdf")


def test_unrecognized_path_returns_empty():
    assert _intake_gcs_object("/random/place/file.pdf") == ""
