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
from app.utils.dt import now_utc_naive

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models.larc import LarcEnrollmentEnvelope
from app.models.surgery import Surgery, SurgeryConsentEnvelope
from app.services import boldsign_envelopes as bs
from app.services import larc_enrollment_sender as larc_es

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

    The receiver is fail-CLOSED: if BOLDSIGN_WEBHOOK_SECRET is missing,
    every request is rejected. The previous "setup mode" let anyone on
    the public Internet POST status updates while the secret was unset,
    which is unsafe to keep in production. To re-enable the BoldSign
    Verify-dashboard convenience during initial setup, the operator
    must explicitly set BOLDSIGN_WEBHOOK_ALLOW_UNSIGNED=true alongside
    the unset secret — that opt-in flag does not exist in normal
    Cloud Run config."""
    body = await request.body()
    signature = request.headers.get("x-boldsign-signature", "")
    if not _webhook_secret():
        if os.environ.get("BOLDSIGN_WEBHOOK_ALLOW_UNSIGNED", "").lower() == "true":
            log.warning("BoldSign webhook in SETUP MODE — secret unset but "
                        "BOLDSIGN_WEBHOOK_ALLOW_UNSIGNED=true is set; "
                        "accepting unverified request")
            return {"received": True, "applied": False, "reason": "setup mode"}
        log.error("BoldSign webhook rejected — BOLDSIGN_WEBHOOK_SECRET is not set")
        raise HTTPException(status_code=503,
            detail="webhook not configured (secret missing)")
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

    # Dispatch: a BoldSign envelope id can belong to either a surgery
    # consent or a LARC pharmacy-enrollment envelope. Try LARC first —
    # the larc_enrollment_envelopes table is much smaller than the
    # surgery_consent_envelopes table, so lookup is cheap.
    larc_row = (db.query(LarcEnrollmentEnvelope)
                  .filter(LarcEnrollmentEnvelope.boldsign_envelope_id == doc_id)
                  .first())
    if larc_row is not None:
        before = larc_row.status
        try:
            after = larc_es.apply_webhook_event(db, larc_row, data)
            db.commit()
        except Exception as exc:
            log.exception("LARC webhook apply failed for %s", doc_id)
            raise HTTPException(status_code=500, detail=f"larc apply error: {exc}")
        log.info("BoldSign LARC webhook applied: documentId=%s status %s → %s",
                  doc_id, before, after)
        return {"received": True, "applied": True, "kind": "larc",
                "before_status": before, "after_status": after}

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

        # Patient activity (audit #13): patient finished signing.
        # Reset the auto-unresponsive clock if this represents a fresh
        # signature (avoid bumping on duplicate webhook redeliveries).
        if before != row.status and row.status == "signed":
            surgery.last_patient_activity_at = now_utc_naive()
            db.commit()

        # Surface the change to surgery@. Idempotent on the
        # (surgery_id, event_kind, envelope_id) tuple so a re-delivered
        # webhook can't re-email the practice. Only fire when the
        # transition crossed into a terminal state — _apply_status_to_row
        # itself guards against re-writes from out-of-order deliveries.
        if before != row.status and row.status in ("signed", "declined"):
            event_kind = ("consent_signed" if row.status == "signed"
                          else "consent_declined")
            try:
                from app.services.surgery_scheduler_notify import notify_scheduler
                extra = {"envelope_id": str(row.id),
                         "boldsign_envelope_id": row.boldsign_envelope_id}
                if row.status == "signed":
                    open_envs = [e for e in (surgery.consent_envelopes or [])
                                  if (e.status or "").lower() not in
                                  ("signed", "completed", "voided", "declined", "expired")]
                    extra["all_signed"] = len(open_envs) == 0
                else:
                    extra["decline_reason"] = (data.get("declineReason")
                                                 or data.get("DeclineReason"))
                notify_scheduler(db, event_kind=event_kind, surgery=surgery,
                                  event_id=f"{row.boldsign_envelope_id}:{row.status}",
                                  extra=extra)
            except Exception as e:
                log.warning("scheduler notify after consent webhook failed: %s", e)

    log.info("BoldSign webhook applied: documentId=%s status %s → %s",
              doc_id, before, row.status)
    return {"received": True, "applied": True, "kind": "surgery",
            "before_status": before, "after_status": row.status}
