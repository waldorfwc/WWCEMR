"""Waystar SFTP sync writes ERAs to GCS so download_eob_report can find them."""
from unittest.mock import patch, MagicMock


def test_sync_eras_uploads_each_file_to_waystar_reports_prefix(client, db,
                                                                    monkeypatch):
    """The sync iterates over (filename, bytes) returned by the client and
    saves each into gs://wwc-app-docs/waystar-reports/{filename} via
    save_blob_with_key."""
    monkeypatch.setattr("app.config.settings.waystar_sftp_host", "sftp.example")

    captured_keys = []
    def _capture(*, key, body, content_type=None):
        captured_keys.append(key)
        return key

    # Fake SFTP returns 2 ERAs
    fake_client = MagicMock()
    fake_client.download_eras_sftp.return_value = [
        ("835_2026-05-01.era", b"ISA*00*..."),
        ("835_2026-05-02.era", b"ISA*00*..."),
    ]

    # Stub the ERA poster so we don't need a real claim graph
    fake_result = MagicMock(claims_posted=1, claims_unmatched=0)
    with patch("app.routers.waystar.get_waystar_client",
                return_value=fake_client), \
         patch("app.routers.waystar.save_blob_with_key",
                side_effect=_capture), \
         patch("app.services.era_poster.process_era_file",
                return_value=fake_result):
        r = client.post("/api/waystar/sync-eras")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["downloaded"] == 2
    assert captured_keys == [
        "waystar-reports/835_2026-05-01.era",
        "waystar-reports/835_2026-05-02.era",
    ]


def test_sync_eras_persists_to_gcs_even_when_poster_errors(client, db,
                                                                  monkeypatch):
    """If process_era_file blows up, the ERA bytes should already be in GCS
    so an operator can re-process via /eob-report/{filename} without
    re-running the SFTP fetch."""
    monkeypatch.setattr("app.config.settings.waystar_sftp_host", "sftp.example")

    captured = []
    def _capture(*, key, body, content_type=None):
        captured.append(key)
        return key

    fake_client = MagicMock()
    fake_client.download_eras_sftp.return_value = [
        ("835_busted.era", b"corrupt"),
    ]

    with patch("app.routers.waystar.get_waystar_client",
                return_value=fake_client), \
         patch("app.routers.waystar.save_blob_with_key",
                side_effect=_capture), \
         patch("app.services.era_poster.process_era_file",
                side_effect=ValueError("bad ERA")):
        r = client.post("/api/waystar/sync-eras")

    assert r.status_code == 200
    # The GCS write happened before the poster ran
    assert captured == ["waystar-reports/835_busted.era"]
    # The per-file result records the error
    rec = r.json()["results"][0]
    assert rec["status"] == "error"
    assert "bad ERA" in rec["error"]


def test_download_eras_sftp_returns_filename_and_bytes_tuples():
    """Sanity-check the client signature: not paths anymore."""
    from app.services.waystar_service import WaystarClient
    c = WaystarClient.__new__(WaystarClient)
    # Fake out _sftp_username / _sftp_password and the paramiko session
    with patch.object(c, "_sftp_username", return_value="u"), \
         patch.object(c, "_sftp_password", return_value="p"), \
         patch("paramiko.SSHClient") as mock_ssh, \
         patch("app.services.waystar_service.settings") as mock_settings:
        mock_settings.waystar_sftp_host = "h"
        mock_settings.waystar_sftp_port = 22

        sftp = MagicMock()
        sftp.listdir.return_value = ["a.era", "b.era", "skip.pdf"]
        def _getfo(remote, fileobj):
            fileobj.write(b"ERA-" + remote.encode())
        sftp.getfo.side_effect = _getfo
        ssh_inst = MagicMock()
        ssh_inst.open_sftp.return_value = sftp
        mock_ssh.return_value = ssh_inst

        out = c.download_eras_sftp(remote_dir="Out/835")

    assert len(out) == 2     # .pdf filtered out
    assert out[0][0] == "a.era"
    assert out[0][1] == b"ERA-Out/835/a.era"
    assert out[1][0] == "b.era"
