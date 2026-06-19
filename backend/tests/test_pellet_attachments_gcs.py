"""Pellet attachments — uploads + downloads via storage adapter."""
from unittest.mock import patch
from datetime import datetime


def _seed_pellet_order(db):
    from app.models.pellet import PelletOrder
    from datetime import date
    o = PelletOrder(
        qualgen_order_number=f"QG-{datetime.utcnow().timestamp():.0f}",
        placed_by="tester@example.com",
        order_date=date.today(),
        status="placed",
    )
    db.add(o); db.commit(); db.refresh(o)
    return o


def _seed_pellet_receipt(db, order):
    from app.models.pellet import PelletReceipt
    r = PelletReceipt(
        order_id=order.id,
        qualgen_order_number=order.qualgen_order_number,
        received_by="tester@example.com",
    )
    db.add(r); db.commit(); db.refresh(r)
    return r


def test_pellet_order_attachment_upload_stores_gcs_key(client, db):
    """Upload a fake PDF, verify the DB row has a GCS key (no leading '/')."""
    o = _seed_pellet_order(db)
    with patch("app.routers.pellet.save_blob",
                return_value="pellet-attachments/abc123.pdf") as mock:
        r = client.post(
            f"/api/pellets/orders/{o.id}/attachments",
            files={"file": ("invoice.pdf", b"%PDF-1.4 x",
                              "application/pdf")},
        )
    assert r.status_code == 201, r.text
    from app.models.pellet import PelletOrderAttachment
    att = (db.query(PelletOrderAttachment)
             .filter(PelletOrderAttachment.order_id == o.id).first())
    assert att.storage_path == "pellet-attachments/abc123.pdf"
    mock.assert_called_once()
    _, kwargs = mock.call_args
    assert kwargs["prefix"] == "pellet-attachments"
    assert kwargs["filename"] == "invoice.pdf"


def test_pellet_order_attachment_download_via_serve_blob(client, db):
    """Row with a GCS key → serve_blob streams from bucket."""
    o = _seed_pellet_order(db)
    from app.models.pellet import PelletOrderAttachment
    att = PelletOrderAttachment(
        order_id=o.id, filename="x.pdf",
        storage_path="pellet-attachments/exists.pdf",
        content_type="application/pdf",
        size_bytes=12, uploaded_by="tester@example.com",
    )
    db.add(att); db.commit(); db.refresh(att)
    from fastapi.responses import Response
    with patch("app.routers.pellet.serve_blob",
                return_value=Response(content=b"%PDF-1.4 ok",
                                          media_type="application/pdf")) as mock:
        r = client.get(f"/api/pellets/orders/{o.id}/attachments/{att.id}")
    assert r.status_code == 200
    _, kwargs = mock.call_args
    assert kwargs["gcs_object"] == "pellet-attachments/exists.pdf"
    assert kwargs["local_path"] is None


def test_pellet_order_attachment_download_legacy_path_returns_410(client, db):
    """Pre-migration local path → 410, NOT crash."""
    o = _seed_pellet_order(db)
    from app.models.pellet import PelletOrderAttachment
    att = PelletOrderAttachment(
        order_id=o.id, filename="x.pdf",
        storage_path="/Users/wwcclaudecode/Documents/wwc-era-project/backend/uploads/pellet_orders/old.pdf",
        content_type="application/pdf",
        size_bytes=12, uploaded_by="tester@example.com",
    )
    db.add(att); db.commit(); db.refresh(att)
    r = client.get(f"/api/pellets/orders/{o.id}/attachments/{att.id}")
    assert r.status_code == 410


def test_pellet_receipt_attachment_upload_stores_gcs_key(client, db):
    o = _seed_pellet_order(db)
    rcpt = _seed_pellet_receipt(db, o)
    with patch("app.routers.pellet.save_blob",
                return_value="pellet-attachments/rcpt.pdf"):
        r = client.post(
            f"/api/pellets/receipts/{rcpt.id}/attachments",
            files={"file": ("packing.pdf", b"%PDF-1.4 r",
                              "application/pdf")},
        )
    assert r.status_code == 201, r.text
    from app.models.pellet import PelletReceiptAttachment
    att = (db.query(PelletReceiptAttachment)
             .filter(PelletReceiptAttachment.receipt_id == rcpt.id).first())
    assert att.storage_path == "pellet-attachments/rcpt.pdf"


def test_pellet_receipt_attachment_download_legacy_path_returns_410(client, db):
    o = _seed_pellet_order(db)
    rcpt = _seed_pellet_receipt(db, o)
    from app.models.pellet import PelletReceiptAttachment
    att = PelletReceiptAttachment(
        receipt_id=rcpt.id, filename="x.pdf",
        storage_path="/Users/.../old.pdf",
        content_type="application/pdf",
        size_bytes=12, uploaded_by="tester@example.com",
    )
    db.add(att); db.commit(); db.refresh(att)
    r = client.get(f"/api/pellets/receipts/{rcpt.id}/attachments/{att.id}")
    assert r.status_code == 410


def test_count_pdf_generator_returns_bytes_and_filename(db):
    """generate_count_pdf is now: (bytes, filename) — no path, no size."""
    from app.models.pellet import PelletCount
    c = PelletCount(location="QGEN", status="finished",
                       started_at=datetime.utcnow(),
                       started_by="tester@example.com",
                       finished_at=datetime.utcnow(),
                       finished_by="tester@example.com",
                       witness_user="witness@example.com")
    db.add(c); db.commit(); db.refresh(c)
    from app.services.pellet.count_pdf import generate_count_pdf
    body, fname = generate_count_pdf(db, c)
    assert isinstance(body, (bytes, bytearray))
    assert body[:4] == b"%PDF"
    assert fname.endswith(".pdf")
    assert fname.startswith("pellet-count_QGEN_")
