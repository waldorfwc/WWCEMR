"""Tests that the legacy ERA auto-import path is disabled in favor of Phase 2c."""
from pathlib import Path
import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "johns_hopkins_era.835"


def test_legacy_import_era_file_raises_not_implemented():
    from app.services.era_import_service import import_era_file
    with pytest.raises(NotImplementedError) as exc:
        import_era_file(None, None, "/tmp/x.835")
    assert "era-posting" in str(exc.value).lower()


def test_legacy_helpers_still_importable():
    """Phase 2c reuses _determine_claim_status, _create_denials, etc."""
    from app.services.era_import_service import (
        _determine_claim_status, _has_real_denials, _create_denials,
        SKIP_DENIAL_CODES, CONTRACTUAL_CODES,
    )
    assert callable(_determine_claim_status)
    assert callable(_create_denials)
    assert "45" in SKIP_DENIAL_CODES


def test_legacy_imports_upload_era_returns_410(client, db):
    with FIXTURE.open("rb") as f:
        r = client.post(
            "/api/imports/upload",
            files={"file": (FIXTURE.name, f, "application/octet-stream")},
        )
    assert r.status_code == 410, r.text
    detail = r.json()["detail"]
    # Works whether detail is a dict or plain str
    s = detail if isinstance(detail, str) else (detail.get("message") or str(detail))
    assert "era-posting" in s.lower()
