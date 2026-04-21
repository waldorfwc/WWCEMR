"""Verify Waystar SFTP sync routes downloaded ERAs through process_era_file."""
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from app.models.claim import Claim, ClaimStatus, InsuranceOrder, EraFile as EraFileModel
from app.models.patient import Patient
from app.models.payment import Payment

FIXTURE = Path(__file__).parent / "fixtures" / "johns_hopkins_era.835"


def test_sync_eras_sftp_posts_matched_claims(client, db, tmp_path, monkeypatch):
    # Pre-link a patient + claim so one ERA CLP01 matches.
    p = Patient(patient_id="45740", first_name="A", last_name="B")
    db.add(p); db.commit(); db.refresh(p)
    db.add(Claim(
        claim_number="V1", patient_id=p.id,
        patient_control_number="216059P45740",
        billed_amount=Decimal("253.76"),
        insurance_order=InsuranceOrder.PRIMARY,
        status=ClaimStatus.PENDING, balance=Decimal("253.76"),
    ))
    db.commit()

    # Copy the ERA fixture into a temp path so the sync can "read" it.
    sftp_copy = tmp_path / "sftp_downloaded.835"
    sftp_copy.write_bytes(FIXTURE.read_bytes())

    # Configure settings so the endpoint doesn't 400 on missing SFTP host.
    monkeypatch.setattr("app.config.settings.waystar_sftp_host", "dummy")

    # Patch the client factory to return a stub that "downloads" our local file.
    class _StubClient:
        def download_eras_sftp(self, remote_dir: str):
            return [str(sftp_copy)]

    with patch("app.routers.waystar.get_waystar_client", return_value=_StubClient()):
        r = client.post("/api/waystar/sync-eras")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["downloaded"] == 1
    assert len(body["results"]) == 1
    first = body["results"][0]
    assert first["status"] == "imported"
    assert first["claims_posted"] >= 1

    assert db.query(EraFileModel).count() == 1
    assert db.query(Payment).count() >= 1
