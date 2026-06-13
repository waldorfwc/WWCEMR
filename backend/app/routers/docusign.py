"""DocuSign Connect webhook receiver.

DocuSign POSTs a JSON payload here when an envelope event fires (sent /
delivered / completed / declined / voided). We look up the matching
SurgeryConsentEnvelope row by docusign_envelope_id, apply the new
status, and recompute the parent Surgery's consent_status.

HMAC verification: configure DOCUSIGN_WEBHOOK_SECRET in .env, set the
matching HMAC secret in DocuSign Connect's "HMAC Security" section, and
this endpoint will verify every payload's `X-DocuSign-Signature-1` header.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session, joinedload

from app.config import settings
from app.database import get_db
from app.models.surgery import Surgery, SurgeryConsentEnvelope
from app.services.docusign_envelopes import (
    _apply_status_to_row, reconcile_surgery_consent,
)

router = APIRouter(prefix="/docusign", tags=["docusign"])
log = logging.getLogger(__name__)


def _verify_hmac(raw_body: bytes, signature_header: Optional[str]) -> bool:
    secret = settings.docusign_webhook_secret
    if not secret:
        return True  # no secret configured → accept everything
    if not signature_header:
        return False
    digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(expected, signature_header)


def _find_envelope_row(db: Session, envelope_id: str) -> Optional[SurgeryConsentEnvelope]:
    return (db.query(SurgeryConsentEnvelope)
              .filter(SurgeryConsentEnvelope.docusign_envelope_id == envelope_id)
              .first())


@router.post("/webhook")
async def docusign_webhook(request: Request, db: Session = Depends(get_db)):
    raw = await request.body()
    sig = request.headers.get("X-DocuSign-Signature-1")
    if not _verify_hmac(raw, sig):
        log.warning("DocuSign webhook HMAC mismatch")
        raise HTTPException(status_code=401, detail="invalid signature")

    try:
        payload = json.loads(raw or b"{}")
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="invalid JSON payload")

    # Connect formats vary: aggregate puts data under data.envelopeSummary;
    # per-event uses envelopeStatus / envelopeId at the top level.
    data = payload.get("data") or {}
    summary = data.get("envelopeSummary") or {}
    envelope_id = (
        summary.get("envelopeId")
        or data.get("envelopeId")
        or payload.get("envelopeId")
    )
    if not envelope_id:
        log.info("DocuSign webhook with no envelopeId: %r", payload)
        return {"ok": True, "noted": "no envelopeId in payload"}

    row = _find_envelope_row(db, envelope_id)
    if not row:
        log.info("DocuSign webhook for unknown envelope %s", envelope_id)
        return {"ok": True, "noted": "envelope not tied to a surgery"}

    # Build a synthetic envelope dict from the webhook payload that
    # _apply_status_to_row understands.
    env = {
        "status": (summary.get("status") or data.get("envelopeStatus")
                   or payload.get("status") or row.status),
        "completedDateTime": (summary.get("completedDateTime")
                              or data.get("completedDateTime")
                              or payload.get("completedDateTime")),
        "declinedDateTime": (summary.get("declinedDateTime")
                             or data.get("declinedDateTime")
                             or payload.get("declinedDateTime")),
        "voidedDateTime": (summary.get("voidedDateTime")
                           or data.get("voidedDateTime")
                           or payload.get("voidedDateTime")),
    }
    _apply_status_to_row(row, env)

    # Re-evaluate the parent surgery's overall consent state
    surgery = (db.query(Surgery)
                 .options(joinedload(Surgery.consent_envelopes))
                 .filter(Surgery.id == row.surgery_id).first())
    if surgery:
        reconcile_surgery_consent(db, surgery)

    db.commit()
    return {
        "ok": True,
        "envelope_id": envelope_id,
        "applied_status": row.status,
        "consent_status": surgery.consent_status if surgery else None,
    }
