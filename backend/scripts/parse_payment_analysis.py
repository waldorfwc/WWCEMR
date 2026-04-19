#!/usr/bin/env python3
"""Parse PrimeSuite Payment Analysis PDF into SQLite database."""

import re
import sys
import os
from datetime import datetime
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pdfplumber
from app.database import SessionLocal, init_db
from app.models.payment_analysis import PaymentAnalysis

PDF_PATH = "/Users/wwcclaudecode/Downloads/Payment Analysis YR2026.pdf"
BATCH_SIZE = 1000


def parse_amount(raw):
    if not raw:
        return Decimal("0.00")
    raw = str(raw).strip()
    negative = False
    if raw.startswith("(") and raw.endswith(")"):
        negative = True
        raw = raw[1:-1]
    if raw.startswith("-"):
        negative = not negative
        raw = raw[1:]
    raw = raw.replace("$", "").replace(",", "").strip()
    try:
        val = Decimal(raw)
    except Exception:
        return Decimal("0.00")
    return -val if negative else val


def parse_date(raw):
    if not raw:
        return None
    raw = raw.strip()
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


# Payment header: "Insurance Payment Check ($114.91) 21085"
# or: "Patient Payment Credit Card ($49.17) 58386"
PAYMENT_HEADER_RE = re.compile(
    r"(Insurance Payment|Patient Payment)\s+"
    r"(Check|EFT|Credit Card)\s+"
    r"(\(?[\$]?[\d,]+\.\d{2}\)?)\s+"
    r"(\d+)\s*$"
)

# Posting date header: "Posting Date: 04/16/2026"
POSTING_DATE_RE = re.compile(r"Posting Date:\s+(\d{2}/\d{2}/\d{4})")

# Allocation line: "04/16/2026 11/19/2025 Insurance Payment [0.00]; Check [Medstar] Aryian Cooke MD $0.00"
# Format: date date description provider amount
ALLOCATION_RE = re.compile(
    r"^(\d{2}/\d{2}/\d{4})\s+"       # Allocation posting date
    r"(\d{2}/\d{2}/\d{4})?\s*"       # Service date (optional)
    r"(.+?)\s+"                       # Description
    r"(\(?[\$]?[\d,]+\.\d{2}\)?)\s*$" # Amount at end
)

# Provider patterns at end of description
PROVIDER_RE = re.compile(r"^(.*?)\s{2,}((?:Aryian|Dr\.|[A-Z][a-z]+)\s+(?:Cooke|[A-Z][a-z]+)(?:\s+MD)?)\s*$")
CREDIT_RE = re.compile(r"^(.*?)\s{2,}(Prepay Credit|Service Line Transfer)\s*$")


def extract_provider(desc):
    m = PROVIDER_RE.match(desc)
    if m:
        return m.group(1).strip(), m.group(2).strip(), None
    m = CREDIT_RE.match(desc)
    if m:
        return m.group(1).strip(), None, m.group(2).strip()
    return desc.strip(), None, None


def main():
    print("Initializing database...")
    init_db()
    db = SessionLocal()

    # Clear existing
    db.query(PaymentAnalysis).delete()
    db.commit()

    print(f"Opening PDF: {PDF_PATH}")
    pdf = pdfplumber.open(PDF_PATH)
    total_pages = len(pdf.pages)
    print(f"Total pages: {total_pages}")

    records = []
    total_inserted = 0
    total_insurance = Decimal("0.00")
    total_patient = Decimal("0.00")
    unique_patients = set()

    current_posting_date = None
    current_header = None

    for page_num in range(total_pages):
        if (page_num + 1) % 100 == 0:
            print(f"  Page {page_num + 1}/{total_pages}... ({total_inserted + len(records)} records)")

        page = pdf.pages[page_num]
        text = page.extract_text()
        if not text:
            continue

        for line in text.split("\n"):
            line = line.strip()
            if not line:
                continue

            # Check for posting date
            pd_match = POSTING_DATE_RE.search(line)
            if pd_match:
                current_posting_date = parse_date(pd_match.group(1))
                current_header = None
                continue

            # Check for payment header
            hm = PAYMENT_HEADER_RE.search(line)
            if hm:
                current_header = {
                    "payment_source": hm.group(1),
                    "payment_method": hm.group(2),
                    "payment_amount": parse_amount(hm.group(3)),
                    "patient_id": hm.group(4),
                }
                continue

            # Check for allocation line
            if current_header:
                am = ALLOCATION_RE.match(line)
                if am:
                    raw_desc = am.group(3).strip()
                    desc, provider, credit_cat = extract_provider(raw_desc)
                    service_date = parse_date(am.group(2)) if am.group(2) else None

                    rec = PaymentAnalysis(
                        patient_id=current_header["patient_id"],
                        posting_date=current_posting_date,
                        payment_source=current_header["payment_source"],
                        payment_method=current_header["payment_method"],
                        payment_amount=current_header["payment_amount"],
                        service_date=service_date,
                        description=desc,
                        provider=provider,
                        credit_category=credit_cat,
                        allocation_amount=parse_amount(am.group(4)),
                        raw_line=line,
                    )
                    records.append(rec)

                    unique_patients.add(current_header["patient_id"])
                    if current_header["payment_source"] == "Insurance Payment":
                        total_insurance += parse_amount(am.group(4))
                    else:
                        total_patient += parse_amount(am.group(4))

                    if len(records) >= BATCH_SIZE:
                        db.bulk_save_objects(records)
                        db.commit()
                        total_inserted += len(records)
                        records = []

    if records:
        db.bulk_save_objects(records)
        db.commit()
        total_inserted += len(records)

    pdf.close()
    db.close()

    print(f"\n{'='*60}")
    print("PAYMENT ANALYSIS IMPORT SUMMARY")
    print(f"{'='*60}")
    print(f"Total records:            {total_inserted:,}")
    print(f"Total insurance payments: ${total_insurance:,.2f}")
    print(f"Total patient payments:   ${total_patient:,.2f}")
    print(f"Unique patients:          {len(unique_patients):,}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
