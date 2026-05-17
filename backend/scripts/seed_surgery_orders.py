"""Batch-import a folder of ModMed surgery-order PDFs.

For each PDF:
  1. Extract text → Claude → structured fields (chart, name, DOB, procedures,
     ICD-10, surgeon, insurance, address, etc.).
  2. If a Surgery with the same chart_number already exists: ENRICH it
     (fill empty fields, append procedures if missing, attach the PDF as
     a SurgeryFile of kind='order'). Don't trample existing data.
  3. Else: create a new Surgery in 'incomplete' status so the scheduler
     can review the extracted fields before flipping it to 'new'.

  The folder lives outside the repo (under ~/Downloads). We COPY each PDF
  into backend/uploads/surgery_orders/ so the file is stored alongside
  the row and the import is self-contained.

Run with no flag = dry-run (shows what would happen, no Claude calls
beyond text extraction). Pass --apply to actually parse + persist.
Use --limit N to cap the number of PDFs (useful for testing).
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import traceback
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.database import SessionLocal, init_db
from app.models.surgery import Surgery, SurgeryFile
from app.services.surgery_order_parser import (
    extract_pdf_text, parse_order_text, parse_order_pdf_direct, build_surgery_kwargs,
)


UPLOADS_DIR = "/Users/wwcclaudecode/Documents/wwc-era-project/backend/uploads/surgery_orders"


def coalesce(existing, incoming):
    if existing is None or (isinstance(existing, str) and not existing.strip()):
        return incoming
    return existing


def enrich(s: Surgery, kwargs: dict) -> list[str]:
    """Fill empty fields on `s` from parser output. Procedures + diagnoses
    are appended only if currently empty (don't risk dup-merge). Returns
    list of field names that changed."""
    changes = []
    # Scalar fields — coalesce
    for f in (
        "patient_name", "first_name", "last_name", "dob",
        "phone", "cell_phone", "email",
        "address_street", "address_city", "address_state", "address_zip",
        "primary_insurance", "primary_member_id", "primary_group",
        "secondary_insurance",
        "surgeon_primary", "surgeon_secondary",
        "procedure_classification", "estimated_minutes",
    ):
        if f not in kwargs or kwargs[f] is None:
            continue
        cur = getattr(s, f, None)
        new = coalesce(cur, kwargs[f])
        if new != cur:
            setattr(s, f, new)
            changes.append(f)
    # is_robotic is bool — only set if currently False and parser says True
    if kwargs.get("is_robotic") and not s.is_robotic:
        s.is_robotic = True
        changes.append("is_robotic")
    # Procedures + diagnoses — only seed if missing
    if kwargs.get("procedures") and not s.procedures:
        s.procedures = kwargs["procedures"]
        changes.append("procedures")
    if kwargs.get("diagnoses") and not s.diagnoses:
        s.diagnoses = kwargs["diagnoses"]
        changes.append("diagnoses")
    # eligible_facilities — only seed if empty
    if kwargs.get("eligible_facilities") and not s.eligible_facilities:
        s.eligible_facilities = kwargs["eligible_facilities"]
        changes.append("eligible_facilities")
    return changes


def attach_pdf(db, s: Surgery, pdf_path: str, filename: str, by: str) -> SurgeryFile:
    """Copy the PDF into uploads/surgery_orders/ and create a SurgeryFile row."""
    os.makedirs(UPLOADS_DIR, exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    safe = f"{s.chart_number or 'noChart'}_{stamp}_{filename}"
    dest = os.path.join(UPLOADS_DIR, safe)
    shutil.copy2(pdf_path, dest)
    size = os.path.getsize(dest)
    row = SurgeryFile(
        surgery_id=s.id,
        kind="order",
        filename=filename,
        path=dest,
        mime_type="application/pdf",
        size_bytes=size,
        uploaded_by=by,
        notes="Auto-attached from order-batch-seed script.",
    )
    db.add(row)
    # Also set order_pdf_path on the surgery for backwards compat
    if not s.order_pdf_path:
        s.order_pdf_path = dest
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", help="Folder of .pdf order files")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--limit", type=int, default=None,
                     help="Only process the first N PDFs (testing)")
    args = ap.parse_args()

    folder = Path(args.folder)
    if not folder.is_dir():
        sys.exit(f"Not a directory: {folder}")

    pdfs = sorted([p for p in folder.glob("*.pdf") if p.is_file()])
    if args.limit:
        pdfs = pdfs[:args.limit]
    print(f"Found {len(pdfs)} PDFs")

    init_db()
    db = SessionLocal()
    try:
        n_enriched = 0
        n_skipped_no_match = 0
        n_text_only_failed = 0
        n_parse_failed = 0
        n_already_attached = 0

        for i, pdf in enumerate(pdfs, 1):
            label = pdf.name
            print(f"\n[{i}/{len(pdfs)}] {label}")

            # Try native text first; fall back to Claude PDF-direct for scanned PDFs
            try:
                text = extract_pdf_text(str(pdf))
            except Exception as exc:
                print(f"  ! pdfplumber error: {exc} — will try PDF-direct")
                text = ""

            use_pdf_direct = not text or len(text) < 50

            # Dry-run mode: just probe extraction, no Claude call
            if not args.apply:
                if use_pdf_direct:
                    print(f"  ↪ scanned PDF — would use Claude PDF-direct ({pdf.stat().st_size // 1024} KB)")
                else:
                    print(f"  ↪ native PDF — {len(text)} chars extracted")
                continue

            try:
                if use_pdf_direct:
                    print(f"  → sending PDF to Claude (vision)…")
                    parsed = parse_order_pdf_direct(str(pdf))
                else:
                    parsed = parse_order_text(text)
                kwargs = build_surgery_kwargs(parsed)
            except Exception as exc:
                print(f"  ✗ Claude parse failed: {exc}")
                n_parse_failed += 1
                continue

            chart = (kwargs.get("chart_number") or "").strip()
            name = kwargs.get("patient_name") or ""
            if not chart or not name:
                print(f"  ✗ Parser missing chart/name — chart={chart!r} name={name!r}")
                n_parse_failed += 1
                continue

            # Match by chart first, then by name (any status, since PDFs may
            # belong to past completed surgeries too)
            existing = (db.query(Surgery)
                          .filter(Surgery.chart_number == chart)
                          .first())
            match_kind = "chart" if existing else None
            if not existing and kwargs.get("first_name") and kwargs.get("last_name"):
                fn = kwargs["first_name"].lower()
                ln = kwargs["last_name"].lower()
                candidates = (db.query(Surgery)
                                .filter(Surgery.patient_name.ilike(f"%{ln}%"))
                                .all())
                candidates = [s for s in candidates
                              if fn in (s.patient_name or "").lower()
                                 and ln in (s.patient_name or "").lower()]
                # Tiebreak with DOB
                if len(candidates) > 1 and kwargs.get("dob"):
                    same_dob = [s for s in candidates if s.dob == kwargs["dob"]]
                    if same_dob:
                        candidates = same_dob
                if len(candidates) == 1:
                    existing = candidates[0]
                    match_kind = "name"

            if not existing:
                # User directive: skip unmatched PDFs (don't create new patients)
                print(f"  → no match for chart {chart!r} / {name!r}; skipping per --match-only")
                n_skipped_no_match += 1
                continue

            # Skip if same PDF is already attached (idempotency)
            already = (db.query(SurgeryFile)
                         .filter(SurgeryFile.surgery_id == existing.id,
                                 SurgeryFile.kind == "order",
                                 SurgeryFile.filename == pdf.name)
                         .first())
            if already:
                print(f"  → already attached to chart {chart} ({existing.patient_name}); skipping")
                n_already_attached += 1
                continue
            changes = enrich(existing, kwargs)
            attach_pdf(db, existing, str(pdf), pdf.name, by="system:order-seed")
            db.commit()
            print(f"  ✓ enriched by {match_kind} → chart {existing.chart_number} "
                   f"({existing.patient_name}) — {len(changes)} field(s)")
            n_enriched += 1

        print("\n── SUMMARY ──")
        print(f"  PDFs processed:       {len(pdfs)}")
        print(f"  Enriched existing:    {n_enriched}")
        print(f"  Skipped — no match:   {n_skipped_no_match}")
        print(f"  Already attached:     {n_already_attached}")
        print(f"  Text extract failed:  {n_text_only_failed}")
        print(f"  Claude parse failed:  {n_parse_failed}")
        if not args.apply:
            print("\n  (DRY RUN — re-run with --apply to actually parse + save.)")
    finally:
        db.close()


if __name__ == "__main__":
    main()
