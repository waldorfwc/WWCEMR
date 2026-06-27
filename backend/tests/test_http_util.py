"""The shared latin-1-safe Content-Disposition builder. A non-latin-1
character in a filename (e.g. U+202F narrow no-break space from scanners, or
an accented patient name) must never crash response encoding with a 500."""
from app.utils.http import content_disposition


def test_ascii_filename_roundtrips():
    cd = content_disposition("report.pdf", "attachment")
    cd.encode("latin-1")                       # must not raise
    assert 'filename="report.pdf"' in cd
    assert "attachment;" in cd


def test_nonlatin1_filename_is_latin1_safe():
    # U+202F (narrow no-break space) + an accented letter.
    cd = content_disposition("Reférral 1234.pdf", "inline")
    cd.encode("latin-1")                       # was UnicodeEncodeError -> 500
    assert "filename*=UTF-8''" in cd           # RFC 5987 carries the real name
    assert 'filename="' in cd                  # ascii fallback still present


def test_empty_filename_falls_back():
    cd = content_disposition("", "inline")
    cd.encode("latin-1")
    assert 'filename="document"' in cd


def test_quotes_and_newlines_stripped():
    cd = content_disposition('a"b\nc.pdf', "inline")
    cd.encode("latin-1")
    assert "\n" not in cd and "\r" not in cd
    # the embedded quote must not break out of the ascii filename="..." token
    assert 'filename="abc.pdf"' in cd
