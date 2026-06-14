"""Parse-only order extract endpoint (Task C).

POST /surgery/orders/extract parses a PDF and returns prefill fields for
the manual intake form WITHOUT writing a Surgery row. The parser
functions are stubbed so the test never calls Claude/pdfplumber.
"""
from unittest.mock import patch

from app.models.surgery import Surgery


def _stub_parsers():
    """Patch the parser functions the endpoint imports so no external
    (Claude/pdfplumber) call happens. Returns a context-manager tuple."""
    return (
        patch("app.services.surgery.order_parser.extract_pdf_text_from_bytes",
              return_value="X" * 200),
        patch("app.services.surgery.order_parser.parse_order_text",
              return_value={
                  "patient": {"first_name": "Pat", "last_name": "Doe"},
                  "insurance_primary": {"company": "Aetna", "member_id": "A555",
                                        "payer_id": "60054"},
                  "procedure_type": "Total Laparoscopic Hysterectomy",
                  "procedures": [{"cpt": "58558", "description": "Hysteroscopy"}],
                  "ordered_at": "2026-05-05T21:24:00",
              }),
        patch("app.services.surgery.order_parser.build_surgery_kwargs",
              return_value={
                  "chart_number": "C900",
                  "patient_name": "Doe, Pat",
                  "first_name": "Pat",
                  "last_name": "Doe",
                  "dob": __import__("datetime").date(1985, 3, 2),
                  "phone": "(240) 555-0100",
                  "email": "pat@example.com",
                  "address_street": "1 Main St",
                  "address_city": "Waldorf",
                  "address_state": "MD",
                  "address_zip": "20601",
                  "primary_insurance": "Aetna",
                  "primary_member_id": "A555",
                  "primary_payer_id": "60054",
                  "surgeon_primary": "Aryian Cooke",
                  "procedures": [{"cpt": "58558", "description": "Hysteroscopy"}],
                  "diagnoses": [{"icd": "N84.0", "description": "Polyp"}],
                  "eligible_facilities": ["office"],
                  "estimated_minutes": 60,
                  "is_robotic": False,
                  # empty/None values must be dropped from the response
                  "secondary_insurance": None,
                  "selected_facility": None,
              }),
    )


def test_extract_returns_fields_and_creates_no_surgery(client, db):
    before = db.query(Surgery).count()
    p1, p2, p3 = _stub_parsers()
    with p1, p2, p3:
        r = client.post(
            "/api/surgery/orders/extract",
            files={"file": ("order.pdf", b"%PDF-1.4 stub", "application/pdf")},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    fields = body["fields"]
    assert fields["chart_number"] == "C900"
    assert fields["patient_name"] == "Doe, Pat"
    assert fields["dob"] == "1985-03-02"          # date → "YYYY-MM-DD"
    assert fields["primary_insurance"] == "Aetna"
    assert fields["estimated_minutes"] == 60
    assert fields["is_robotic"] is False
    # Newly-surfaced demographics + insurance + procedure fields
    assert fields["phone"] == "(240) 555-0100"
    assert fields["email"] == "pat@example.com"
    assert fields["address_street"] == "1 Main St"
    assert fields["address_city"] == "Waldorf"
    assert fields["address_state"] == "MD"
    assert fields["address_zip"] == "20601"
    assert fields["payer_id"] == "60054"
    assert fields["surgery_name"] == "Total Laparoscopic Hysterectomy"
    # preop_date = date portion of ordered_at (the order create date)
    assert fields["preop_date"] == "2026-05-05"
    # None / non-ManualSurgeryIn keys dropped
    assert "secondary_insurance" not in fields
    assert "selected_facility" not in fields
    assert body["warnings"] == []
    # No Surgery row written
    assert db.query(Surgery).count() == before


def test_extract_scanned_image_warns_but_returns_200(client, db):
    before = db.query(Surgery).count()
    with patch("app.services.surgery.order_parser.extract_pdf_text_from_bytes",
               return_value="short"), \
         patch("app.services.surgery.order_parser.parse_order_text",
               side_effect=ValueError("not enough text")):
        r = client.post(
            "/api/surgery/orders/extract",
            files={"file": ("scan.pdf", b"%PDF-1.4 scan", "application/pdf")},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["fields"] == {}
    assert any("scanned image" in w.lower() for w in body["warnings"])
    assert db.query(Surgery).count() == before


def test_extract_rejects_non_pdf(client):
    r = client.post(
        "/api/surgery/orders/extract",
        files={"file": ("order.txt", b"not a pdf", "text/plain")},
    )
    assert r.status_code == 422
