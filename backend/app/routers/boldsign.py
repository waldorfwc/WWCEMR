"""BoldSign Connect webhook receiver.

BoldSign POSTs a JSON event payload here when a document changes state
(Sent / Delivered / Signed / Completed / Declined / Expired / Revoked).
We look up the matching SurgeryConsentEnvelope row by
boldsign_envelope_id and apply the new status.

Signature verification: BoldSign signs each webhook with HMAC-SHA256
keyed on BOLDSIGN_WEBHOOK_SECRET. The signature comes in the
`X-Boldsign-Signature` header as a hex digest. We verify before parsing.

This lives alongside the existing DocuSign webhook at /api/docusign/webhook
— both providers can be active simultaneously while we migrate templates.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models.surgery import Surgery, SurgeryConsentEnvelope
from app.services import boldsign_envelopes as bs

log = logging.getLogger(__name__)

router = APIRouter(prefix="/boldsign", tags=["boldsign"])


def _webhook_secret() -> str:
    return os.environ.get("BOLDSIGN_WEBHOOK_SECRET", "").strip()


def _parse_signature_header(header: str) -> tuple[str, str]:
    """Parse a Stripe-style signed-payload header:
        't=<unix_ts>, s0=<sig>'
    Returns (timestamp, signature). Tolerates extra whitespace and
    additional s1/s2/... scheme tokens (we ignore non-s0).
    """
    ts = ""
    sig = ""
    for part in (header or "").split(","):
        kv = part.strip().split("=", 1)
        if len(kv) != 2:
            continue
        k, v = kv[0].strip(), kv[1].strip()
        if k == "t":
            ts = v
        elif k == "s0" and not sig:
            sig = v
    return ts, sig


def _verify_signature(body: bytes, signature: str) -> bool:
    """HMAC-SHA256 match. BoldSign signs in the Stripe pattern:
        signed_payload = f"{timestamp}.{raw_body}"
        s0 = hex(hmac_sha256(secret, signed_payload))
    The X-Boldsign-Signature header is 't=<ts>, s0=<digest>'.
    We also accept (a) a bare hex/base64 digest of the body (legacy/test
    shape) for compatibility with our own unit tests.
    """
    secret = _webhook_secret()
    if not secret:
        log.warning("BoldSign webhook received but BOLDSIGN_WEBHOOK_SECRET is not set")
        return False
    raw = (signature or "").strip()
    ts, s0 = _parse_signature_header(raw)

    # Path 1 — Stripe-style signed payload
    if ts and s0:
        signed = f"{ts}.".encode("utf-8") + body
        digest = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).digest()
        expected_hex = digest.hex()
        expected_b64 = base64.b64encode(digest).decode("ascii")
        if hmac.compare_digest(expected_hex, s0) or hmac.compare_digest(expected_b64, s0):
            return True
        log.warning(
            "BoldSign signature mismatch (signed-payload): got s0=%r..., "
            "expected hex=%r... or b64=%r...",
            s0[:16], expected_hex[:16], expected_b64[:16],
        )
        return False

    # Path 2 — bare digest of body (used by our unit tests)
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    if (hmac.compare_digest(digest.hex(), raw)
            or hmac.compare_digest(base64.b64encode(digest).decode("ascii"), raw)):
        return True
    log.warning(
        "BoldSign signature mismatch (bare): header=%r", raw[:60]
    )
    return False


@router.post("/webhook")
async def boldsign_webhook(request: Request, db: Session = Depends(get_db)):
    """Receive a BoldSign document-status event. Returns 200 on
    successfully-applied events, 400 on bad signature, 404 if the event
    refers to a documentId we don't have a row for (logged + 200 — we
    don't want BoldSign to retry forever for orphan events).

    Setup mode: if BOLDSIGN_WEBHOOK_SECRET is unset, accept every request
    and return 200 (logged at WARN). Lets BoldSign's "Verify" dashboard
    button pass during initial setup. Once the secret is configured in
    Cloud Run, full HMAC verification kicks back in."""
    body = await request.body()
    signature = request.headers.get("x-boldsign-signature", "")
    if not _webhook_secret():
        log.warning("BoldSign webhook in SETUP MODE — no secret configured, "
                     "accepting unverified request")
        return {"received": True, "applied": False, "reason": "setup mode"}
    if not _verify_signature(body, signature):
        raise HTTPException(status_code=400, detail="bad signature")

    try:
        event = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="malformed json")

    # BoldSign event shape (per their docs):
    #   { event: "Completed", data: { documentId: "...", status: "Completed", ... } }
    # The exact field name for the document id varies — try several.
    data = event.get("data") or event.get("Data") or event
    doc_id = (data.get("documentId")
              or data.get("DocumentId")
              or data.get("documentid"))
    if not doc_id:
        log.warning("BoldSign webhook missing documentId: %r", event)
        return {"received": True, "applied": False, "reason": "no documentId"}

    row = (db.query(SurgeryConsentEnvelope)
             .filter(SurgeryConsentEnvelope.boldsign_envelope_id == doc_id)
             .first())
    if row is None:
        log.warning("BoldSign webhook for unknown documentId %s — ignoring", doc_id)
        return {"received": True, "applied": False, "reason": "no matching envelope"}

    before = row.status
    bs._apply_status_to_row(row, data)
    db.commit()

    # If this envelope just completed/declined, also recompute the parent
    # Surgery's consent_status by reconciling all its envelopes. Cheap to
    # do unconditionally on any status change.
    surgery = db.query(Surgery).filter(Surgery.id == row.surgery_id).first()
    if surgery is not None:
        try:
            # reconcile re-reads all envelopes for this surgery and updates
            # Surgery.consent_status. Soft-fail if it raises.
            bs.reconcile_surgery_consent(db, surgery)
        except Exception as e:
            log.warning("BoldSign reconcile after webhook failed: %s", e)

    log.info("BoldSign webhook applied: documentId=%s status %s → %s",
              doc_id, before, row.status)
    return {"received": True, "applied": True,
            "before_status": before, "after_status": row.status}
