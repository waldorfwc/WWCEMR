"""API for the CARC/RARC reference lookup used by the Denials page."""
from __future__ import annotations

import re
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session


# Billers commonly write codes prefixed with the group code (e.g. "CO-96",
# "PR 45", "oa22"). Normalize that to the raw code before searching, and
# use the detected group code as an additional hint.
_PREFIX_RE = re.compile(r"^(CO|PR|OA|PI|CR)[\s\-_]?(.+)$", re.IGNORECASE)


def _normalize_query(raw: str) -> tuple[str, str | None]:
    """Return (search_term, inferred_code_type_hint).

    inferred_code_type_hint is 'CARC' if the code part is purely numeric,
    'RARC' if it starts with a letter (M/MA/N are the common prefixes),
    None if we can't tell (free-text search).
    """
    if not raw:
        return "", None
    s = raw.strip()
    m = _PREFIX_RE.match(s)
    if m:
        s = m.group(2).strip()
    # If what remains looks like a code, hint the type
    if re.fullmatch(r"\d+", s):
        return s, "CARC"
    if re.fullmatch(r"[A-Za-z]+\d*", s):
        return s, "RARC"
    return s, None

from app.database import get_db
from app.models.adjustment_code_reference import (
    AdjustmentCodeComboCache, AdjustmentCodeNoteRevision,
    AdjustmentCodeReference, AdjustmentCodeType,
)
from app.routers.auth import get_current_user
from app.services.adjustment_code_enricher import (
    ENRICHMENT_MODEL, enrich_code, synthesize_combo,
)


router = APIRouter(prefix="/adjustment-codes", tags=["adjustment-codes"])

NOTES_MAX_LEN = 20_000


def _to_dict(row: AdjustmentCodeReference) -> dict:
    return {
        "id": str(row.id),
        "code_type": row.code_type,
        "code": row.code,
        "official_verbiage": row.official_verbiage,
        "plain_english": row.plain_english,
        "how_to_fix": row.how_to_fix,
        "wwc_notes": row.wwc_notes,
        "wwc_notes_updated_by": row.wwc_notes_updated_by,
        "wwc_notes_updated_at": row.wwc_notes_updated_at.isoformat() if row.wwc_notes_updated_at else None,
        "enrichment_source": row.enrichment_source,
        "last_enriched_at": row.last_enriched_at.isoformat() if row.last_enriched_at else None,
    }


def _lookup(db: Session, code_type: str, code: str) -> AdjustmentCodeReference:
    ct = code_type.upper()
    if ct not in ("CARC", "RARC"):
        raise HTTPException(status_code=422, detail="code_type must be CARC or RARC")
    row = db.query(AdjustmentCodeReference).filter(
        AdjustmentCodeReference.code_type == ct,
        AdjustmentCodeReference.code == code,
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="code not found")
    return row


@router.get("")
def list_codes(
    q: Optional[str] = Query(None, description="Substring match on code or verbiage"),
    code_type: Optional[str] = Query(None, description="Filter to CARC or RARC"),
    codes: Optional[str] = Query(None, description="Comma-separated codes to fetch"),
    per_page: int = Query(50, le=200),
    db: Session = Depends(get_db),
):
    query = db.query(AdjustmentCodeReference)
    if code_type:
        ct = code_type.upper()
        if ct not in ("CARC", "RARC"):
            raise HTTPException(status_code=422, detail="code_type must be CARC or RARC")
        query = query.filter(AdjustmentCodeReference.code_type == ct)
    if codes:
        wanted = [c.strip() for c in codes.split(",") if c.strip()]
        query = query.filter(AdjustmentCodeReference.code.in_(wanted))
    if q:
        search_term, _hint = _normalize_query(q)
        if search_term:
            like = f"%{search_term}%"
            query = query.filter(or_(
                AdjustmentCodeReference.code.ilike(like),
                AdjustmentCodeReference.official_verbiage.ilike(like),
            ))
    rows = query.order_by(
        AdjustmentCodeReference.code_type, AdjustmentCodeReference.code
    ).limit(per_page).all()
    return {"total": len(rows), "items": [_to_dict(r) for r in rows]}


@router.get("/{code_type}/{code}")
def get_code(code_type: str, code: str, db: Session = Depends(get_db)):
    return _to_dict(_lookup(db, code_type, code))


@router.post("/{code_type}/{code}/regenerate")
def regenerate_code(
    code_type: str, code: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    row = _lookup(db, code_type, code)
    try:
        enr = enrich_code(row.code_type, row.code, row.official_verbiage)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"enrichment failed: {exc}")

    row.plain_english = enr.plain_english
    row.how_to_fix = enr.how_to_fix
    row.enrichment_source = "llm"
    row.last_enriched_at = datetime.utcnow()
    db.commit()
    return _to_dict(row)


@router.put("/{code_type}/{code}/notes")
def save_notes(
    code_type: str, code: str,
    body: str = Body(..., embed=True, description="New notes body (plain text / markdown)"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Save a new WWC-notes revision for this code.

    Every save appends a row to `adjustment_code_note_revisions` and
    updates the live value on `adjustment_code_references`. Saving the
    same body as the current value is a no-op.
    """
    row = _lookup(db, code_type, code)
    new_body = (body or "").strip()
    if len(new_body) > NOTES_MAX_LEN:
        raise HTTPException(
            status_code=422,
            detail=f"notes body exceeds {NOTES_MAX_LEN} chars",
        )

    current = (row.wwc_notes or "").strip()
    if new_body == current:
        return _to_dict(row)

    editor = current_user.get("email") or "unknown"
    # Treat empty submission as a delete, still recorded in history.
    row.wwc_notes = new_body or None
    row.wwc_notes_updated_by = editor
    row.wwc_notes_updated_at = datetime.utcnow()

    db.add(AdjustmentCodeNoteRevision(
        code_ref_id=row.id,
        body=new_body,
        saved_by=editor,
        saved_at=row.wwc_notes_updated_at,
    ))
    db.commit()
    return _to_dict(row)


VALID_GROUPS = {"CO", "PR", "OA", "PI", "CR", "PI"}


@router.post("/synthesize")
def synthesize(
    payload: dict = Body(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Combined explanation for an EOB codeset (group + CARC + RARCs).

    Body:  { "group_code": "CO", "carc": "197", "rarcs": ["N20"] }
    Returns: { plain_english, how_to_fix, from_cache, group_code, carc, rarcs }
    """
    group_code = (payload.get("group_code") or "").upper().strip()
    carc = (payload.get("carc") or "").strip()
    rarcs_raw = payload.get("rarcs") or []
    if not isinstance(rarcs_raw, list):
        raise HTTPException(status_code=422, detail="'rarcs' must be a list")
    rarcs = sorted({str(r).strip() for r in rarcs_raw if str(r).strip()})

    if not group_code or group_code not in VALID_GROUPS:
        raise HTTPException(status_code=422, detail=f"group_code must be one of {sorted(VALID_GROUPS)}")
    if not carc:
        raise HTTPException(status_code=422, detail="carc is required")

    combo_key = f"{group_code}|{carc}|{','.join(rarcs)}"

    cached = db.query(AdjustmentCodeComboCache).filter_by(combo_key=combo_key).first()
    if cached:
        return {
            "group_code": cached.group_code,
            "carc": cached.carc,
            "rarcs": cached.rarcs.split(",") if cached.rarcs else [],
            "plain_english": cached.plain_english,
            "how_to_fix": cached.how_to_fix,
            "from_cache": True,
        }

    # Resolve official verbiage for each code
    carc_row = db.query(AdjustmentCodeReference).filter_by(
        code_type="CARC", code=carc,
    ).first()
    if not carc_row:
        raise HTTPException(status_code=404, detail=f"CARC {carc} not in reference table — seed it first")

    rarc_items: list[tuple[str, str]] = []
    for rc in rarcs:
        row = db.query(AdjustmentCodeReference).filter_by(
            code_type="RARC", code=rc,
        ).first()
        if row:
            rarc_items.append((rc, row.official_verbiage))
        else:
            rarc_items.append((rc, f"(code {rc} not yet in our reference table)"))

    try:
        enr = synthesize_combo(group_code, carc, carc_row.official_verbiage, rarc_items)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"synthesis failed: {exc}")

    db.add(AdjustmentCodeComboCache(
        combo_key=combo_key,
        group_code=group_code,
        carc=carc,
        rarcs=",".join(rarcs),
        plain_english=enr.plain_english,
        how_to_fix=enr.how_to_fix,
        model_used=ENRICHMENT_MODEL,
    ))
    db.commit()

    return {
        "group_code": group_code,
        "carc": carc,
        "rarcs": rarcs,
        "plain_english": enr.plain_english,
        "how_to_fix": enr.how_to_fix,
        "from_cache": False,
    }


@router.get("/{code_type}/{code}/notes/history")
def notes_history(code_type: str, code: str, db: Session = Depends(get_db)):
    row = _lookup(db, code_type, code)
    revs = (db.query(AdjustmentCodeNoteRevision)
            .filter(AdjustmentCodeNoteRevision.code_ref_id == row.id)
            .order_by(AdjustmentCodeNoteRevision.saved_at.desc())
            .all())
    return {
        "code_type": row.code_type,
        "code": row.code,
        "revisions": [
            {
                "id": str(r.id),
                "body": r.body,
                "saved_by": r.saved_by,
                "saved_at": r.saved_at.isoformat() if r.saved_at else None,
            }
            for r in revs
        ],
    }
