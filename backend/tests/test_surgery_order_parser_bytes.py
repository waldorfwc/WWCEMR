"""Surgery order parser — bytes-based extraction and Claude direct-PDF call."""
import io
from unittest.mock import patch, MagicMock


def _build_tiny_pdf(text: str) -> bytes:
    """Make a real one-page PDF with the given text via reportlab. Used so
    pdfplumber has something to extract."""
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import letter
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.drawString(72, 720, text)
    c.showPage(); c.save()
    return buf.getvalue()


def test_extract_pdf_text_from_bytes_reads_in_memory_pdf():
    from app.services.surgery.order_parser import extract_pdf_text_from_bytes
    body = _build_tiny_pdf("Hello from the surgery order parser test")
    out = extract_pdf_text_from_bytes(body)
    assert "Hello from the surgery order parser test" in out


def test_extract_pdf_text_path_wrapper_still_works(tmp_path):
    from app.services.surgery.order_parser import extract_pdf_text
    p = tmp_path / "order.pdf"
    p.write_bytes(_build_tiny_pdf("Backward-compat wrapper"))
    assert "Backward-compat wrapper" in extract_pdf_text(str(p))


def test_parse_order_pdf_bytes_direct_sends_base64_to_claude():
    """The scanned-image fallback should base64-encode the bytes and send
    them as a document content block. We mock the Anthropic client."""
    from app.services.surgery.order_parser import parse_order_pdf_bytes_direct

    # Validator requires patient.last_name + first_name and chart_number
    fake_message = MagicMock()
    fake_message.content = [MagicMock(text=(
        '{"chart_number":"123",'
        ' "patient":{"first_name":"Pat","last_name":"Doe"},'
        ' "procedures":[]}'
    ))]
    fake_client = MagicMock()
    fake_client.messages.create.return_value = fake_message

    with patch("app.services.surgery.order_parser.anthropic.Anthropic",
                return_value=fake_client), \
         patch("app.services.surgery.order_parser.settings.anthropic_api_key",
                "fake-key"):
        out = parse_order_pdf_bytes_direct(b"%PDF-1.4\nfake scanned bytes")

    assert out["chart_number"] == "123"
    assert out["patient"]["last_name"] == "Doe"
    # Verify the document content block was built from our bytes
    call_kwargs = fake_client.messages.create.call_args.kwargs
    doc_block = call_kwargs["messages"][0]["content"][0]
    assert doc_block["type"] == "document"
    assert doc_block["source"]["type"] == "base64"
    assert doc_block["source"]["media_type"] == "application/pdf"
    import base64
    assert base64.standard_b64decode(doc_block["source"]["data"]) == \
        b"%PDF-1.4\nfake scanned bytes"
