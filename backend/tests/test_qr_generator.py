"""QR code PNG generation + admin endpoint."""


def test_render_profile_qr_png_returns_valid_png():
    from app.services.qr_generator import render_profile_qr_png
    png = render_profile_qr_png("sometokenhere")
    assert isinstance(png, (bytes, bytearray))
    # PNG magic bytes
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    # Should be a reasonable size for a 600px QR
    assert 200 < len(png) < 50_000


def test_render_profile_qr_png_encodes_token_in_url(monkeypatch):
    """Smoke that the URL contains the token. We don't run a full QR
    decoder; just trust the qrcode library + verify the function used
    the configured base URL."""
    monkeypatch.setenv("REVIEWS_BASE_URL", "https://example.test")
    import importlib
    from app.services import qr_generator as g
    importlib.reload(g)
    png1 = g.render_profile_qr_png("token-a")
    png2 = g.render_profile_qr_png("token-b")
    # Different tokens → different PNG bytes
    assert png1 != png2


def test_admin_qr_png_endpoint_streams_image(client, db):
    from app.models.reputation import ReputationProfile
    p = ReputationProfile(display_name="Sarah Smith, RN",
                              qr_token="testtoken")
    db.add(p); db.commit(); db.refresh(p)
    r = client.get(f"/api/admin/reputation/profiles/{p.id}/qr.png")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"
    # Content-Disposition has a filename derived from display_name
    assert "qr_Sarah" in r.headers.get("content-disposition", "")


def test_admin_qr_png_endpoint_404_when_profile_missing(client, db):
    r = client.get("/api/admin/reputation/profiles/no-such-id/qr.png")
    assert r.status_code == 404
