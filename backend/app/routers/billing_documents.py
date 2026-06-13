"""Insurance Documents — upload, classify, assign, view, work.

Visibility rules (enforced in list + detail endpoints):
  • Admin (Insurance Docs:Manage) sees everything.
  • Uploader always sees their own uploads.
  • Unassigned docs (empty assigned_to list) are visible to anyone with
    Insurance Docs:View.
  • Assigned docs are visible only to their assignees + admins.

Every read or mutation writes one row to billing_document_access.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from app.utils.dt import now_utc_naive
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models.billing_document import (
    BillingDocument, BillingDocumentAccess, BillingDocumentNote,
    CLASSIFICATIONS, STATUSES,
)
from app.routers.auth import get_current_user
from app.permissions.catalog import Module, Tier
from app.permissions.dependencies import requires_tier
from app.permissions.resolver import effective_tier
from app.services.billing_doc import storage as storage
from app.services.billing_doc import classify as classifier
from app.services.audit_service import log_action


router = APIRouter(prefix="/billing/documents", tags=["billing-documents"])


def _is_admin(db: Session, user: dict) -> bool:
    """Caller has Insurance Documents Manage tier (or higher) — i.e. can
    see/edit every doc regardless of assignment. Resolved by direct
    query on every call rather than relying on a dict injection that
    only fires on specific Depends() shapes. (Fable design review
    note 7.)"""
    email = (user.get("email") or "").lower().strip()
    return effective_tier(db, email, Module.INSURANCE_DOCS) >= Tier.MANAGE


def _visible_to(db: Session, d: BillingDocument, user: dict) -> bool:
    if _is_admin(db, user):
        return True
    me = (user.get("email") or "").lower()
    if d.uploaded_by and d.uploaded_by.lower() == me:
        return True
    assigned = [a.lower() for a in (d.assigned_to or [])]
    if not assigned:
        return True
    return me in assigned


def _log_access(db: Session, doc: BillingDocument, actor: str,
                 action: str, detail: Optional[dict] = None) -> None:
    db.add(BillingDocumentAccess(
        document_id=doc.id, actor=actor, action=action, detail=detail,
    ))


def _classification_valid(v: str) -> bool:
    return v in dict(CLASSIFICATIONS)


def _doc_dict(d: BillingDocument, include_notes: bool = False,
              include_access: bool = False) -> dict:
    out = {
        "id": str(d.id),
        "original_filename":  d.original_filename,
        "file_size_bytes":    d.file_size_bytes,
        "page_count":         d.page_count,
        "mime_type":          d.mime_type,
        "classification":     d.classification,
        "classification_label": dict(CLASSIFICATIONS).get(d.classification, d.classification),
        "status":             d.status,
        "uploaded_by":        d.uploaded_by,
        "uploaded_at":        d.uploaded_at.isoformat() if d.uploaded_at else None,
        "assigned_to":        list(d.assigned_to or []),
        "worked_by":          d.worked_by,
        "worked_at":          d.worked_at.isoformat() if d.worked_at else None,
    }
    if include_notes:
        out["notes"] = [
            {"id": str(n.id), "author": n.author,
             "body": n.body, "created_at": n.created_at.isoformat()}
            for n in (d.notes_rel or [])
        ]
    if include_access:
        out["access_log"] = [
            {"id": str(a.id), "actor": a.actor, "action": a.action,
             "at": a.at.isoformat(), "detail": a.detail}
            for a in (d.access_log or [])
        ]
    return out


# ─── Picklists ──────────────────────────────────────────────────────

@router.get("/picklists")
def picklists(current_user: dict = Depends(requires_tier(Module.INSURANCE_DOCS, Tier.VIEW))):
    return {
        "classifications": [{"v": k, "l": v} for k, v in CLASSIFICATIONS],
        "statuses":        [{"v": k, "l": v} for k, v in STATUSES],
    }


# ─── Upload ─────────────────────────────────────────────────────────

_MAX_UPLOAD_BYTES = 50 * 1024 * 1024
# Extension → (canonical mime, magic-byte prefixes). Anything not in here
# is rejected at the boundary, including the .html / .svg / .exe variants
# the browser is happy to render inline. (Fable intake audit #3.)
_ALLOWED_FILE_TYPES: dict[str, tuple[str, tuple[bytes, ...]]] = {
    "pdf":  ("application/pdf",  (b"%PDF",)),
    "png":  ("image/png",        (b"\x89PNG\r\n\x1a\n",)),
    "jpg":  ("image/jpeg",       (b"\xff\xd8\xff",)),
    "jpeg": ("image/jpeg",       (b"\xff\xd8\xff",)),
    "tif":  ("image/tiff",       (b"II*\x00", b"MM\x00*")),
    "tiff": ("image/tiff",       (b"II*\x00", b"MM\x00*")),
}


def _validated_mime(filename: str, body: bytes) -> str:
    """Reject files whose extension isn't on the allowlist OR whose magic
    bytes don't match the extension. Returns the canonical mime to store
    on the row — never the client-supplied content-type. (Fable #3.)"""
    ext = (filename or "").rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""
    entry = _ALLOWED_FILE_TYPES.get(ext)
    if not entry:
        raise HTTPException(
            status_code=415,
            detail=(f"unsupported file type {ext!r}. Allowed: "
                    f"{', '.join(sorted(_ALLOWED_FILE_TYPES))}"))
    canonical_mime, magic_prefixes = entry
    if not any(body.startswith(p) for p in magic_prefixes):
        raise HTTPException(
            status_code=415,
            detail=f"file contents don't match the .{ext} extension")
    return canonical_mime


@router.post("", status_code=201)
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    classification: str = Form("other"),
    auto_classify: bool = Form(True),
    assigned_to: str = Form(""),     # comma-separated email list
    force: bool = Form(False),       # bypass duplicate check
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.INSURANCE_DOCS, Tier.VIEW)),
):
    """Upload a scanned document. Anyone with Insurance Docs:View can upload.
    If `auto_classify=true` (default) AND the uploader leaves classification
    at the default 'other', we ask Claude to suggest a better label.

    Duplicate detection: we SHA-256 the uploaded bytes and refuse if a
    document with the same hash already exists, returning 409 with the
    existing doc's metadata. Pass force=true to upload anyway."""
    if not _classification_valid(classification):
        raise HTTPException(status_code=422,
                            detail=f"unknown classification: {classification}")

    # Reject the request before reading the body if Content-Length already
    # exceeds the cap — a 5GB POST would otherwise be buffered into memory
    # before the post-read size check fires, OOM-killing the container.
    # (Fable intake audit #3.)
    cl = request.headers.get("content-length")
    if cl is not None:
        try:
            if int(cl) > _MAX_UPLOAD_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"file >{_MAX_UPLOAD_BYTES // (1024 * 1024)}MB; split it up")
        except ValueError:
            pass

    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=422, detail="empty file")
    if len(contents) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"file >{_MAX_UPLOAD_BYTES // (1024 * 1024)}MB; split it up")

    # Allowlist extension + magic bytes; replaces client-supplied mime.
    canonical_mime = _validated_mime(file.filename or "", contents)

    # Hash first so we can short-circuit dup uploads before writing to disk.
    content_hash = hashlib.sha256(contents).hexdigest()
    if not force:
        existing = (db.query(BillingDocument)
                      .filter(BillingDocument.content_hash == content_hash)
                      .filter(BillingDocument.deleted_at.is_(None))
                      .order_by(BillingDocument.uploaded_at.desc())
                      .first())
        if existing:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "duplicate",
                    "message": "A document with identical contents already exists.",
                    "existing": {
                        "id": str(existing.id),
                        "original_filename": existing.original_filename,
                        "uploaded_by": existing.uploaded_by,
                        "uploaded_at": existing.uploaded_at.isoformat()
                                         if existing.uploaded_at else None,
                        "classification": existing.classification,
                        "status": existing.status,
                    },
                },
            )

    try:
        storage_name, size = storage.save(contents, file.filename or "upload.pdf")
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    pages = storage.page_count_pdf(contents) if canonical_mime == "application/pdf" else None

    # AI auto-classify — only override when uploader didn't pick a specific
    # category (i.e. left it at 'other'). Safe no-op if Claude isn't configured.
    ai_suggested = None
    if auto_classify and classification == "other":
        ai_suggested = classifier.classify_pdf(contents, canonical_mime)
        if ai_suggested:
            classification = ai_suggested

    assignees = [a.strip().lower() for a in (assigned_to or "").split(",") if a.strip()]

    d = BillingDocument(
        original_filename=file.filename or "upload.pdf",
        storage_filename=storage_name,
        file_size_bytes=size,
        page_count=pages,
        mime_type=canonical_mime,
        content_hash=content_hash,
        classification=classification,
        # STATUSES = ('new','in_progress','worked'). Earlier versions of
        # this endpoint set 'open' which is unrecognized — those rows
        # were hidden from the default Insurance Docs view (filter
        # defaults to [new, in_progress]) and rendered with no tone.
        status="new",
        uploaded_by=current_user.get("email") or "system",
        assigned_to=assignees,
    )
    db.add(d); db.flush()
    _log_access(db, d, current_user.get("email") or "system", "uploaded",
                {"filename": d.original_filename, "size": size,
                 "classification": classification,
                 "ai_classified": bool(ai_suggested),
                 "assigned_to": assignees})
    try:
        db.commit(); db.refresh(d)
    except IntegrityError:
        # Race: another upload with the same content_hash committed
        # between our app-level check above and our commit. Roll back
        # and surface the existing row. Backed by the partial unique
        # index ix_billing_documents_content_hash_unique. (Fable #7.)
        db.rollback()
        if force:
            raise
        existing = (db.query(BillingDocument)
                      .filter(BillingDocument.content_hash == content_hash)
                      .filter(BillingDocument.deleted_at.is_(None))
                      .order_by(BillingDocument.uploaded_at.desc())
                      .first())
        if not existing:
            raise
        raise HTTPException(
            status_code=409,
            detail={
                "error": "duplicate",
                "message": ("Another upload of identical contents arrived "
                            "at the same time."),
                "existing": {"id": str(existing.id)},
            },
        )
    out = _doc_dict(d)
    out["ai_classified"] = bool(ai_suggested)
    return out


# ─── List ───────────────────────────────────────────────────────────

@router.get("")
def list_documents(
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.INSURANCE_DOCS, Tier.VIEW)),
    status: Optional[str] = None,
    classification: Optional[str] = None,
    assigned_to_me: bool = False,
    unassigned_only: bool = False,
    page: int = Query(1, ge=1),
    per_page: int = Query(100, ge=1, le=500),
):
    q = db.query(BillingDocument).filter(BillingDocument.not_deleted())
    if status:
        # Accept either a single status ('new') or comma-separated list
        # ('new,in_progress') so callers can filter to multiple states.
        wanted = [s.strip() for s in status.split(",") if s.strip()]
        if len(wanted) == 1:
            q = q.filter(BillingDocument.status == wanted[0])
        elif wanted:
            q = q.filter(BillingDocument.status.in_(wanted))
    if classification:
        q = q.filter(BillingDocument.classification == classification)
    rows = q.order_by(BillingDocument.uploaded_at.desc()).all()
    # Filter by visibility in Python (assigned_to is JSON)
    me = (current_user.get("email") or "").lower()
    visible = [d for d in rows if _visible_to(db, d, current_user)]
    if assigned_to_me:
        visible = [d for d in visible if me in [a.lower() for a in (d.assigned_to or [])]]
    if unassigned_only:
        visible = [d for d in visible if not (d.assigned_to or [])]
    total = len(visible)
    paged = visible[(page - 1) * per_page : page * per_page]
    return {"total": total, "page": page, "per_page": per_page,
            "documents": [_doc_dict(d) for d in paged]}


# ─── Detail ─────────────────────────────────────────────────────────

def _load(db: Session, doc_id: str) -> BillingDocument:
    d = (db.query(BillingDocument)
           .options(joinedload(BillingDocument.notes_rel),
                    joinedload(BillingDocument.access_log))
           .filter(BillingDocument.id == doc_id,
                   BillingDocument.not_deleted()).first())
    if not d:
        raise HTTPException(status_code=404, detail="document not found")
    return d


@router.get("/{doc_id}")
def get_document(doc_id: str,
                  db: Session = Depends(get_db),
                  current_user: dict = Depends(requires_tier(Module.INSURANCE_DOCS, Tier.VIEW))):
    d = _load(db, doc_id)
    if not _visible_to(db, d, current_user):
        raise HTTPException(status_code=403, detail="not authorized for this document")
    _log_access(db, d, current_user.get("email") or "system", "viewed")
    db.commit(); db.refresh(d)
    return _doc_dict(d, include_notes=True, include_access=True)


# ─── File stream (for inline PDF viewer) ────────────────────────────

@router.get("/{doc_id}/file")
def get_document_file(doc_id: str,
                       db: Session = Depends(get_db),
                       current_user: dict = Depends(requires_tier(Module.INSURANCE_DOCS, Tier.VIEW))):
    d = _load(db, doc_id)
    if not _visible_to(db, d, current_user):
        raise HTTPException(status_code=403, detail="not authorized")
    try:
        fh = storage.open_for_read(d.storage_filename)
        body = fh.read()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="file missing on disk")
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    finally:
        try:
            fh.close()
        except Exception:
            pass

    _log_access(db, d, current_user.get("email") or "system", "downloaded")
    db.commit()
    # original_filename can contain " or \n that breaks the header;
    # strip those characters. nosniff blocks browsers from interpreting
    # a stored doc as anything other than its declared mime type — the
    # upload allowlist already guarantees the mime is safe. (Fable #3.)
    safe_name = (d.original_filename or "document").replace('"', '').replace("\n", "").replace("\r", "")
    return Response(
        content=body,
        media_type=d.mime_type or "application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{safe_name}"',
            "Content-Length": str(len(body)),
            "X-Content-Type-Options": "nosniff",
        },
    )


# ─── Patch (classify, assign, work, reopen) ─────────────────────────

class DocumentPatch(BaseModel):
    classification:    Optional[str] = None
    assigned_to:       Optional[list[str]] = None    # full replacement
    status:            Optional[str] = None          # 'open' | 'worked'
    original_filename: Optional[str] = None          # rename (display name only)


@router.patch("/{doc_id}")
def patch_document(doc_id: str, payload: DocumentPatch,
                    db: Session = Depends(get_db),
                    current_user: dict = Depends(requires_tier(Module.INSURANCE_DOCS, Tier.WORK))):
    d = _load(db, doc_id)
    if not _visible_to(db, d, current_user):
        raise HTTPException(status_code=403, detail="not authorized")
    actor = current_user.get("email") or "system"
    data = payload.model_dump(exclude_unset=True)

    if "classification" in data:
        if not _classification_valid(data["classification"]):
            raise HTTPException(status_code=422,
                                detail=f"unknown classification: {data['classification']}")
        before = d.classification
        d.classification = data["classification"]
        _log_access(db, d, actor, "classified",
                    {"from": before, "to": d.classification})

    if "assigned_to" in data:
        normalized = [a.strip().lower() for a in (data["assigned_to"] or []) if a.strip()]
        before = list(d.assigned_to or [])
        d.assigned_to = normalized
        _log_access(db, d, actor,
                    "unassigned" if not normalized else "assigned",
                    {"from": before, "to": normalized})

    if "status" in data:
        if data["status"] not in dict(STATUSES):
            raise HTTPException(status_code=422,
                                detail=f"unknown status: {data['status']}")
        before = d.status
        d.status = data["status"]
        if d.status == "worked":
            d.worked_by = actor
            d.worked_at = now_utc_naive()
            _log_access(db, d, actor, "worked", {"from": before})
        else:
            d.worked_by = None
            d.worked_at = None
            _log_access(db, d, actor, "reopened", {"from": before})

    if "original_filename" in data:
        new_name = (data["original_filename"] or "").strip()
        if not new_name:
            raise HTTPException(status_code=422, detail="filename cannot be empty")
        if len(new_name) > 255:
            raise HTTPException(status_code=422, detail="filename too long (255 max)")
        before = d.original_filename
        d.original_filename = new_name
        _log_access(db, d, actor, "renamed", {"from": before, "to": new_name})

    db.commit(); db.refresh(d)
    return _doc_dict(d, include_notes=True, include_access=True)


# ─── Delete (admin only) ────────────────────────────────────────────

@router.delete("/{doc_id}", status_code=204)
def delete_document(doc_id: str,
                     db: Session = Depends(get_db),
                     current_user: dict = Depends(requires_tier(Module.INSURANCE_DOCS, Tier.MANAGE))):
    """Soft-delete the document row. The file on disk and the
    billing_document_access trail stay intact so the question 'who
    viewed this document before it was deleted' remains answerable.
    Restore via POST /{id}/restore. (Fable design review note 13.)
    """
    d = _load(db, doc_id)
    if d.is_deleted:
        # Already deleted — return 204 idempotently.
        return None
    log_action(
        db,
        action="DELETE",
        resource_type="billing_document",
        resource_id=str(d.id),
        actor=current_user,
        description=(f"Soft-deleted billing document '{d.original_filename}' "
                     f"({d.storage_filename})"),
        defer_commit=True,
    )
    d.soft_delete(by_email=current_user.get("email"))
    db.commit()


@router.post("/{doc_id}/restore")
def restore_document(doc_id: str,
                      db: Session = Depends(get_db),
                      current_user: dict = Depends(requires_tier(Module.INSURANCE_DOCS, Tier.MANAGE))):
    """Reverse a soft-delete of a billing document."""
    d = db.query(BillingDocument).filter(BillingDocument.id == doc_id).first()
    if not d:
        raise HTTPException(status_code=404, detail="document not found")
    if not d.is_deleted:
        return {"restored": False, "id": str(d.id), "reason": "not deleted"}
    log_action(
        db, action="RESTORE", resource_type="billing_document",
        resource_id=str(d.id), actor=current_user,
        description=f"Restored billing document '{d.original_filename}'",
        defer_commit=True,
    )
    d.restore()
    db.commit()
    return {"restored": True, "id": str(d.id)}
    return


# ─── Notes ──────────────────────────────────────────────────────────

class NoteIn(BaseModel):
    body: str


@router.post("/{doc_id}/notes", status_code=201)
def add_note(doc_id: str, payload: NoteIn,
              db: Session = Depends(get_db),
              current_user: dict = Depends(requires_tier(Module.INSURANCE_DOCS, Tier.WORK))):
    d = _load(db, doc_id)
    if not _visible_to(db, d, current_user):
        raise HTTPException(status_code=403, detail="not authorized")
    if not payload.body.strip():
        raise HTTPException(status_code=422, detail="note body required")
    actor = current_user.get("email") or "system"
    note = BillingDocumentNote(
        document_id=d.id, author=actor, body=payload.body.strip(),
    )
    db.add(note)
    _log_access(db, d, actor, "note_added", {"preview": payload.body.strip()[:80]})
    db.commit(); db.refresh(note)
    return {"id": str(note.id), "author": note.author,
            "body": note.body, "created_at": note.created_at.isoformat()}


# ─── Workforce picklist (who can be assigned) ───────────────────────

@router.get("/workforce/assignable")
def assignable_users(db: Session = Depends(get_db),
                       current_user: dict = Depends(requires_tier(Module.INSURANCE_DOCS, Tier.VIEW))):
    """Return the list of users that can be assigned to a document.
    Anyone active with Insurance Docs:View is fair game."""
    from app.models.user import User
    users = (db.query(User)
               .filter(User.is_active.is_(True))
               .order_by(User.email).all())
    return [
        {"email": u.email, "name": u.display_name or u.email}
        for u in users
    ]
