"""AI auto-classification for Insurance Documents.

Sends the first page of a PDF to Claude and asks for the best-fit
classification: paper_eob | patient_payment | insurance_letter | other.

If the Anthropic SDK is missing or the API key is unset, returns None
so the caller falls back to the uploader's manual pick.
"""
from __future__ import annotations

import base64
import logging
import os
from typing import Optional

log = logging.getLogger(__name__)


LABELS = {
    "paper_eob":         "ModMed EOB — explanation of benefits from an insurance payer",
    "patient_payment":   "Patient payment — check, money order, or copy of payment from the patient",
    "insurance_letter":  "Insurance letter — auth determination, request for records, refund request (NOT a denial)",
    "denial":            "Denial — a payer letter specifically denying a claim or appeal (often references denial codes, appeal-rights language, or 'we have denied your claim')",
    "other":             "Other — anything that doesn't fit the categories above",
}


def _first_page_pdf(file_bytes: bytes) -> Optional[bytes]:
    """Return a PDF containing only the first page of the input PDF.
    On failure (non-PDF, corrupted), returns the full input."""
    try:
        import io
        from pypdf import PdfReader, PdfWriter
        reader = PdfReader(io.BytesIO(file_bytes))
        if len(reader.pages) <= 1:
            return file_bytes
        writer = PdfWriter()
        writer.add_page(reader.pages[0])
        out = io.BytesIO()
        writer.write(out)
        return out.getvalue()
    except Exception:
        return file_bytes


def classify_pdf(file_bytes: bytes, mime_type: str = "application/pdf") -> Optional[str]:
    """Ask Claude to classify the document. Returns one of the LABELS
    keys, or None if the API is unavailable / unconfigured / unsure.

    Disabled by default — the first page of an EOB / patient payment is
    PHI (name, member ID, claim details). Only enable once we've
    confirmed a BAA covers the Anthropic API for our use. Set
    BILLING_AI_CLASSIFY_ENABLED=1 in env to opt in. (Fable intake
    audit #9.)
    """
    if (os.environ.get("BILLING_AI_CLASSIFY_ENABLED", "").strip().lower()
            not in {"1", "true", "yes", "on"}):
        log.info("Auto-classify skipped: BILLING_AI_CLASSIFY_ENABLED is not set")
        return None
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        log.info("Auto-classify skipped: ANTHROPIC_API_KEY not set")
        return None
    try:
        from anthropic import Anthropic
    except Exception as exc:
        log.warning("Anthropic SDK not installed: %s", exc)
        return None

    # Only PDFs work for vision-style content blocks; bail otherwise
    if not (mime_type or "").startswith("application/pdf"):
        return None

    first_page = _first_page_pdf(file_bytes) or file_bytes
    pdf_b64 = base64.standard_b64encode(first_page).decode("ascii")

    prompt = (
        "You are a medical-billing assistant. Classify the attached scanned "
        f"document into exactly one of these {len(LABELS)} categories:\n\n"
        + "\n".join(f"  - {k}: {v}" for k, v in LABELS.items())
        + "\n\nRespond with ONLY the category key (e.g. `paper_eob`), no other text."
    )

    try:
        client = Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-opus-4-7",
            max_tokens=20,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": pdf_b64,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }],
        )
        text = (msg.content[0].text if msg.content else "").strip().lower()
        # Normalize and verify
        text = text.replace("`", "").strip()
        if text in LABELS:
            return text
        # Best-effort fuzzy match if Claude returned extra text
        for k in LABELS:
            if k in text:
                return k
        log.info("Auto-classify: Claude returned unexpected label %r", text)
        return None
    except Exception as exc:
        log.warning("Auto-classify failed: %s", exc)
        return None
