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


# ── Payer-ID split out of the company name (deterministic post-process) ──

def test_validate_and_coerce_splits_payer_id_from_company():
    from app.services.surgery.order_parser import _validate_and_coerce
    out = _validate_and_coerce({
        "patient": {"first_name": "Pat", "last_name": "Doe"},
        "insurance_primary": {
            "company": "BCBS Administrators PPO ONLY (75191)",
            "member_id": "M1",
            # no explicit payer_id — must be lifted from the parenthetical
            "payer_id": None,
        },
    })
    ins = out["insurance_primary"]
    assert ins["payer_id"] == "75191"
    assert ins["company"] == "BCBS Administrators PPO ONLY"


def test_validate_and_coerce_keeps_existing_payer_id():
    from app.services.surgery.order_parser import _validate_and_coerce
    out = _validate_and_coerce({
        "patient": {"first_name": "Pat", "last_name": "Doe"},
        "insurance_primary": {
            "company": "BCBS Administrators PPO ONLY (75191)",
            "payer_id": "99999",  # already populated → keep, but still strip
        },
    })
    ins = out["insurance_primary"]
    assert ins["payer_id"] == "99999"
    assert ins["company"] == "BCBS Administrators PPO ONLY"


def test_validate_and_coerce_splits_alphanumeric_payer_id():
    from app.services.surgery.order_parser import _validate_and_coerce
    out = _validate_and_coerce({
        "patient": {"first_name": "Pat", "last_name": "Doe"},
        "insurance_primary": {
            "company": "Aetna Better Health of Maryland (128MD)",
            "member_id": "M1",
            "payer_id": None,
        },
    })
    ins = out["insurance_primary"]
    assert ins["payer_id"] == "128MD"
    assert ins["company"] == "Aetna Better Health of Maryland"


def test_validate_and_coerce_skips_plan_type_token():
    # (MCO) is a plan-type token, not a payer ID — must be denylisted so the
    # company keeps it and no payer_id is set.
    from app.services.surgery.order_parser import _validate_and_coerce
    out = _validate_and_coerce({
        "patient": {"first_name": "Pat", "last_name": "Doe"},
        "insurance_primary": {
            "company": "Something Health Plan (MCO)",
            "payer_id": None,
        },
    })
    ins = out["insurance_primary"]
    assert ins["payer_id"] is None
    assert ins["company"] == "Something Health Plan (MCO)"


def _stub_company_with_payer(company, payer_id):
    """Stub parse_order_text to return the post-split insurance block (the
    parenthetical-payer-id split is covered by the _validate_and_coerce unit
    tests above; here we exercise the endpoint's map resolution). The real
    build_surgery_kwargs maps the payer_id + company through unchanged."""
    return (
        patch("app.services.surgery.order_parser.extract_pdf_text_from_bytes",
              return_value="X" * 200),
        patch("app.services.surgery.order_parser.parse_order_text",
              return_value={
                  "patient": {"first_name": "Pat", "last_name": "Doe"},
                  "insurance_primary": {"company": company, "member_id": "M1",
                                        "payer_id": payer_id},
                  "procedures": [],
              }),
    )


def test_extract_resolves_payer_id_to_company_via_map(client, db):
    # post-split block (company stripped, payer_id set); default map resolves
    # the canonical picklist company.
    p1, p2 = _stub_company_with_payer("BCBS Administrators PPO ONLY", "75191")
    with p1, p2:
        r = client.post(
            "/api/surgery/orders/extract",
            files={"file": ("order.pdf", b"%PDF-1.4 stub", "application/pdf")},
        )
    assert r.status_code == 200, r.text
    fields = r.json()["fields"]
    assert fields["payer_id"] == "75191"
    # raw company stripped + resolved to the canonical picklist value
    assert fields["primary_insurance"] == "Blue Cross & Blue Shield PPO"


def test_extract_resolves_alphanumeric_payer_id_case_insensitively(client, db):
    # lowercase "128md" must uppercase-resolve against the (uppercase) map
    # to the seeded MCO company.
    p1, p2 = _stub_company_with_payer("Aetna Better Health of Maryland", "128md")
    with p1, p2:
        r = client.post(
            "/api/surgery/orders/extract",
            files={"file": ("order.pdf", b"%PDF-1.4 stub", "application/pdf")},
        )
    assert r.status_code == 200, r.text
    fields = r.json()["fields"]
    assert fields["payer_id"] == "128MD"
    assert fields["primary_insurance"] == "Aetna Better Health (MCO)"


def test_extract_unmapped_payer_id_keeps_raw_company(client, db):
    p1, p2 = _stub_company_with_payer("Some Obscure Plan", "12345")
    with p1, p2:
        r = client.post(
            "/api/surgery/orders/extract",
            files={"file": ("order.pdf", b"%PDF-1.4 stub", "application/pdf")},
        )
    assert r.status_code == 200, r.text
    fields = r.json()["fields"]
    assert fields["payer_id"] == "12345"
    # not in the map → raw (stripped) company is preserved, no crash
    assert fields["primary_insurance"] == "Some Obscure Plan"
