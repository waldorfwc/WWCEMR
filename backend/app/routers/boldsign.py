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


def _verify_signature(body: bytes, signature: str) -> bool:
    """HMAC-SHA256 hex digest match. BoldSign uses the raw request body
    as the message; the secret is configured in their Dashboard at
    webhook-endpoint setup time."""
    secret = _webhook_secret()
    if not secret:
        log.warning("BoldSign webhook received but BOLDSIGN_WEBHOOK_SECRET is not set")
        return False
    expected = hmac.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, (signature or "").strip())


@router.post("/webhook")
async def boldsign_webhook(request: Request, db: Session = Depends(get_db)):
    """Receive a BoldSign document-status event. Returns 200 on
    successfully-applied events, 400 on bad signature, 404 if the event
    refers to a documentId we don't have a row for (logged + 200 — we
    don't want BoldSign to retry forever for orphan events)."""
    body = await request.body()
    signature = request.headers.get("x-boldsign-signature", "")
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
