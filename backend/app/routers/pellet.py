"""Pellet inventory router — Phase A (inventory + receiving + transfers
+ disposal + daily count + audit + manual).

Patient-side workflow (Phase B) is intentionally out of scope here.

DEA compliance:
  • Testosterone is Schedule III. Disposals + finished counts of
    controlled stock require a witness_user; routes enforce this.
  • Audit log is write-only (no DELETE endpoint exposed).
"""
from __future__ import annotations

from datetime import date as _date, datetime, timedelta
from app.utils.dt import now_utc_naive
from decimal import Decimal
from typing import Annotated, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field


# Quantity bounds at the API boundary (Fable audit #4).
# Pydantic rejects out-of-range ints with 422 before the value can
# corrupt stock arithmetic. No individual dose-level operation in
# Schedule III workflow should ever cross these caps; if it does,
# operators should split it into multiple receipts/transfers/etc.
DoseQty   = Annotated[int, Field(gt=0,  le=999)]      # 1..999 doses
CountQty  = Annotated[int, Field(ge=0,  le=9999)]    # 0..9999 doses (count snapshot can be large but bounded)
PackSize  = Annotated[int, Field(gt=0,  le=99)]       # 1..99 pellets per pack
PackCount = Annotated[int, Field(gt=0,  le=999)]      # 1..999 packs per receipt
# Money: rejects NaN/inf (Pydantic float defaults allow them; Fable
# audit #15 noted Decimal(str(NaN)) poisons grand_total arithmetic).
MoneyAmt  = Annotated[float, Field(ge=0, le=10_000, allow_inf_nan=False)]
from sqlalchemy import desc, func, or_, update
from sqlalchemy.orm import Session, joinedload, selectinload

from app.database import get_db
from app.models.pellet import (
    PelletAuditEvent, PelletCount, PelletCountAttachment, PelletCountLine,
    PelletDisposal,
    PelletDoseType, PelletFilterPreset, PelletLot, PelletMammoFacility,
    PelletManualSection,
    PelletOrder, PelletOrderAttachment, PelletOrderLine,
    PelletPatient, PelletPatientLab, PelletPatientMammo, PelletPatientNote,
    PelletReceipt, PelletReceiptAttachment, PelletStock, PelletTransfer,
    PelletVisit, PelletVisitDose, PelletVisitMilestone,
    DISPOSAL_REASONS, ORDER_STATUSES, PAYMENT_METHODS, PELLET_LOCATIONS,
    PATIENT_TYPES, VISIT_KINDS,
)
from app.models.pellet_portal import PelletActivity
from fastapi import UploadFile, File, Form
from app.routers.auth import get_current_user
from app.permissions.catalog import Module, Tier
from app.permissions.dependencies import requires_super_admin, requires_tier
from app.permissions.resolver import effective_tier
from app.services.pellet.workflow import (
    spawn_milestones, default_price_for, patient_buckets,
)
from app.services.pellet import appt_import, dose_suggest
from app.services.pellet.settings import PELLET_SETTINGS_DEFAULTS, cfg
from app.models.pellet_config import PelletConfig
from app.services.storage import save_blob, serve_blob, is_legacy_local_path


router = APIRouter(prefix="/pellets", tags=["pellets"])


LOCATION_LABELS = {
    "white_plains": "White Plains",
    "brandywine":   "Brandywine",
    "arlington":    "Arlington",
}


# ─── Helpers ────────────────────────────────────────────────────────

def _audit(db: Session, *, actor: str, action: str,
            dose_type_id: Optional[str] = None,
            lot_id: Optional[str] = None,
            receipt_id: Optional[str] = None,
            transfer_id: Optional[str] = None,
            disposal_id: Optional[str] = None,
            count_id: Optional[str] = None,
            location: Optional[str] = None,
            delta_doses: Optional[int] = None,
            summary: Optional[str] = None,
            detail: Optional[dict] = None) -> PelletAuditEvent:
    e = PelletAuditEvent(
        actor=actor, action=action,
        dose_type_id=dose_type_id, lot_id=lot_id, receipt_id=receipt_id,
        transfer_id=transfer_id, disposal_id=disposal_id, count_id=count_id,
        location=location, delta_doses=delta_doses,
        summary=summary, detail=detail,
    )
    db.add(e)
    return e


def _earliest_lot_with_stock(db: Session, dose_type_id, qty: int,
                                location: str):
    """FIFO lot picker: earliest-expiring lot at `location` with ≥ qty
    doses on hand. Returns (PelletLot, PelletStock) or None."""
    pair = (db.query(PelletLot, PelletStock)
              .join(PelletStock, PelletStock.lot_id == PelletLot.id)
              .filter(PelletLot.dose_type_id == dose_type_id,
                      PelletStock.location == location,
                      PelletStock.doses_on_hand >= qty,
                      PelletStock.status == "active")
              .order_by(PelletLot.expiration_date.asc().nullslast(),
                        PelletLot.received_at.asc())
              .first())
    return pair


def _specific_lot_with_stock(db: Session, lot_id, dose_type_id, qty: int,
                              location: str):
    """Validate a caller-specified lot for use as a proposed dose. Raises
    HTTPException with a specific reason on any failure; returns
    (PelletLot, PelletStock) on success."""
    lot = db.query(PelletLot).filter(PelletLot.id == lot_id).first()
    if lot is None:
        raise HTTPException(status_code=422, detail=f"lot {lot_id} not found")
    if str(lot.dose_type_id) != str(dose_type_id):
        raise HTTPException(
            status_code=422,
            detail=(f"lot {lot.qualgen_lot_number} does not match the "
                    "requested dose type"))
    stock = (db.query(PelletStock)
                .filter(PelletStock.lot_id == lot.id,
                        PelletStock.location == location)
                .first())
    if stock is None or stock.status != "active":
        raise HTTPException(
            status_code=409,
            detail=(f"lot {lot.qualgen_lot_number} has no active stock at "
                    f"{location}"))
    if stock.doses_on_hand < qty:
        raise HTTPException(
            status_code=409,
            detail=(f"lot {lot.qualgen_lot_number} only has "
                    f"{stock.doses_on_hand} dose(s) at {location} (need {qty})"))
    return lot, stock


# Dose statuses that mean "the provider has finalized" — only managers
# can edit these post-confirmation.
CONFIRMED_DOSE_STATUSES = {"inserted", "added", "reduced", "returned", "disposed"}


def _is_admin(db: Session, user: dict) -> bool:
    """Caller has Pellets:Manage (or Super Admin). Resolved by direct
    query rather than depending on a dict injection from requires_tier.
    (Fable design review note 7.)"""
    email = (user.get("email") or "").lower().strip()
    return effective_tier(db, email, Module.PELLETS) >= Tier.MANAGE


def _require_visit_location(v: PelletVisit) -> str:
    """Visit location is required for any stock-mutating operation. Older
    rows that lack a location surface a loud 422 instead of being silently
    credited to White Plains."""
    loc = v.location if v else None
    if loc not in PELLET_LOCATIONS:
        raise HTTPException(
            status_code=422,
            detail=(f"visit {v.id if v else '?'} has no valid location — "
                    f"set one via PATCH /pellets/visits/{{id}} before performing "
                    f"any stock-affecting action."))
    return loc


def _assert_is_pdf(contents: bytes, filename: Optional[str]) -> None:
    """Magic-byte check before persisting an attachment. Fable audit #16:
    upload_*_attachment used to trust the .pdf extension and the client-
    supplied content_type only, so any byte stream renamed to .pdf
    would land in storage and be served back with content-type
    application/pdf. PDFs always start with the four bytes %PDF.
    """
    if not contents or contents[:4] != b"%PDF":
        raise HTTPException(
            status_code=422,
            detail=f"{filename or 'upload'} is not a valid PDF "
                   "(missing %PDF header) — try re-exporting from your PDF reader")


def _validate_witness(db: Session, witness_user: Optional[str],
                       actor_email: str, *, controlled: bool) -> str:
    """Validate a controlled-substance witness against the User table.

    Fable audit #6: witness_user used to be free-text — any string that
    differed from the actor would pass "two-person verification". This
    helper makes the witness a real, active user account different from
    the actor; Schedule III control becomes meaningful.

    Returns the canonical (User.email) lower-case identifier on success.
    Raises 422 with a precise reason on failure.

    For non-controlled flows (controlled=False), an empty/None witness is
    silently allowed and returned unchanged — non-controlled disposals
    don't legally require a witness.
    """
    from app.models.user import User as _User
    raw = (witness_user or "").strip()
    if not controlled and not raw:
        return raw
    if not raw:
        raise HTTPException(status_code=422,
                            detail="witness_user required for controlled (Schedule III)")
    if raw.lower() == (actor_email or "").lower():
        raise HTTPException(status_code=422,
                            detail="witness must be a different user than the actor")
    u = (db.query(_User)
           .filter(func.lower(_User.email) == raw.lower()).first())
    if not u:
        raise HTTPException(status_code=422,
                            detail=f"witness '{raw}' is not a known user account")
    if not u.is_active:
        raise HTTPException(status_code=422,
                            detail=f"witness '{u.email}' is no longer active — pick another witness")
    return u.email


def _get_or_create_stock(db: Session, lot_id, location: str) -> PelletStock:
    s = (db.query(PelletStock)
           .filter(PelletStock.lot_id == lot_id,
                   PelletStock.location == location)
           .first())
    if s:
        return s
    s = PelletStock(lot_id=lot_id, location=location, doses_on_hand=0)
    db.add(s); db.flush()
    return s


def _adjust_stock(db: Session, stock: PelletStock, delta: int) -> None:
    """Atomically apply `delta` to PelletStock.doses_on_hand.

    delta > 0 → unconditional increment (receive / return / verify).
    delta < 0 → conditional decrement that succeeds only if the current
                balance is >= |delta|. Two concurrent decrements that
                each pass an in-process `>= qty` check are serialized
                by PostgreSQL row-level locking; the second one's UPDATE
                affects 0 rows and we surface a clean 409 instead of
                silently driving Schedule III stock negative.

    Uses a SQL UPDATE expressed against `PelletStock.doses_on_hand`
    rather than mutating `stock.doses_on_hand` in Python — that's what
    closes the TOCTOU window. Caller's session sees the updated value
    after `db.refresh(stock)` (called here on success). The DB-side
    CHECK (doses_on_hand >= 0) constraint added by the lightweight
    migration is the last line of defense if anyone bypasses this
    helper.
    """
    if delta == 0:
        return
    if delta > 0:
        n = (db.query(PelletStock)
               .filter(PelletStock.id == stock.id)
               .update(
                   {"doses_on_hand": PelletStock.doses_on_hand + delta},
                   synchronize_session=False))
        if n != 1:
            raise HTTPException(status_code=500,
                detail="stock row vanished during adjust")
    else:
        qty = -delta
        n = (db.query(PelletStock)
               .filter(PelletStock.id == stock.id,
                       PelletStock.doses_on_hand >= qty)
               .update(
                   {"doses_on_hand": PelletStock.doses_on_hand - qty},
                   synchronize_session=False))
        if n != 1:
            # Either the row was deleted (won't happen — stocks are
            # never deleted) or the balance dropped below qty between
            # our read and our update. Surface a clean 409 so the
            # caller can retry against fresh state.
            raise HTTPException(status_code=409,
                detail=(f"Insufficient stock at this location "
                        f"(need {qty}). Another user may have just pulled "
                        "doses — refresh and try again."))
    db.refresh(stock)


def _dose_type_dict(t: PelletDoseType, on_hand_packs: Optional[int] = None,
                     on_hand_doses: Optional[int] = None) -> dict:
    return {
        "id": str(t.id),
        "hormone": t.hormone,
        "dose_mg": float(t.dose_mg),
        "label":   t.label,
        "is_controlled": bool(t.is_controlled),
        "reorder_threshold_packs": t.reorder_threshold_packs,
        "reorder_qty_packs":       t.reorder_qty_packs,
        "reorder_thresholds_by_location": t.reorder_thresholds_by_location or None,
        "pack_sizes": t.pack_sizes or [],
        "typical_cost_per_dose": float(t.typical_cost_per_dose)
                                    if t.typical_cost_per_dose is not None else None,
        "notes": t.notes,
        "is_active": bool(t.is_active),
        "on_hand_packs": on_hand_packs,
        "on_hand_doses": on_hand_doses,
    }


def _lot_dict(l: PelletLot, balances: Optional[dict] = None) -> dict:
    out = {
        "id": str(l.id),
        "dose_type_id":             str(l.dose_type_id),
        "dose_type_label":          l.dose_type.label if l.dose_type else None,
        "hormone":                  l.dose_type.hormone if l.dose_type else None,
        "is_controlled":            bool(l.dose_type.is_controlled) if l.dose_type else False,
        "qualgen_lot_number":       l.qualgen_lot_number,
        "expiration_date":          str(l.expiration_date),
        "doses_originally_received": l.doses_originally_received,
        "packs_received":           l.packs_received,
        "pack_size":                l.pack_size,
        "received_at":              l.received_at.isoformat() if l.received_at else None,
        "received_by":              l.received_by,
        "notes":                    l.notes,
    }
    if balances is not None:
        out["balances"] = balances    # {"white_plains": 50, "brandywine": 0, ...}
        out["total_on_hand"] = sum(balances.values())
    return out


# ─── Picklists / catalog ────────────────────────────────────────────

@router.get("/picklists")
def picklists(current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.VIEW))):
    return {
        "locations": [{"v": k, "l": v} for k, v in LOCATION_LABELS.items()],
        "disposal_reasons": [
            {"v": "dropped",  "l": "Dropped on floor"},
            {"v": "broken",   "l": "Broken / damaged"},
            {"v": "expired",  "l": "Past expiration date"},
            {"v": "other",    "l": "Other (notes required)"},
        ],
    }


@router.get("/dose-types")
def list_dose_types(db: Session = Depends(get_db),
                      current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.VIEW))):
    rows = (db.query(PelletDoseType)
              .order_by(PelletDoseType.hormone, PelletDoseType.dose_mg).all())
    # Bulk-load on-hand doses per type
    sums = dict(
        db.query(PelletLot.dose_type_id, func.coalesce(func.sum(PelletStock.doses_on_hand), 0))
          .join(PelletStock, PelletStock.lot_id == PelletLot.id)
          .group_by(PelletLot.dose_type_id).all()
    )
    out = []
    for t in rows:
        doses = int(sums.get(t.id, 0) or 0)
        packs = None
        # Estimate on-hand packs using the smallest pack size for consistent comparison
        if t.pack_sizes:
            min_pack = min(t.pack_sizes)
            if min_pack:
                packs = doses // min_pack
        out.append(_dose_type_dict(t, on_hand_packs=packs, on_hand_doses=doses))
    return out


class DoseTypeIn(BaseModel):
    hormone: Literal["estradiol", "testosterone"]
    dose_mg: float = Field(gt=0, le=1000)
    label:   Optional[str] = None              # auto-generated from hormone+dose if blank
    reorder_threshold_packs: Optional[int] = Field(default=None, ge=0)
    reorder_qty_packs:       Optional[int] = Field(default=None, ge=0)
    typical_cost_per_dose:   Optional[float] = Field(default=None, ge=0)
    pack_sizes:              Optional[list[int]] = None
    notes:                   Optional[str] = None
    is_active:               bool = True


@router.post("/dose-types", status_code=201)
def create_dose_type(payload: DoseTypeIn,
                     override_reason: Optional[str] = None,
                     db: Session = Depends(get_db),
                     current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.MANAGE))):
    """Add a new pellet dose type. testosterone is DEA Schedule III →
    is_controlled auto-set. Unique on (hormone, dose_mg)."""
    from app.services.pellet.lock import ensure_unlocked_or_override
    ensure_unlocked_or_override(db, current_user=current_user,
                                override_reason=override_reason,
                                action_label="dose-type create")
    dose_val = round(float(payload.dose_mg), 2)
    existing = (db.query(PelletDoseType)
                  .filter(PelletDoseType.hormone == payload.hormone,
                          PelletDoseType.dose_mg == dose_val).first())
    if existing:
        raise HTTPException(status_code=409,
                            detail=f"A {payload.hormone} {dose_val:g}mg dose already exists.")
    # Trim/validate pack sizes to positive ints.
    packs = [int(x) for x in (payload.pack_sizes or []) if int(x) > 0]
    label = (payload.label or "").strip() or f"{payload.hormone.title()} {dose_val:g}mg"
    t = PelletDoseType(
        hormone=payload.hormone,
        dose_mg=dose_val,
        label=label,
        is_controlled=(payload.hormone == "testosterone"),
        reorder_threshold_packs=payload.reorder_threshold_packs,
        reorder_qty_packs=payload.reorder_qty_packs,
        typical_cost_per_dose=payload.typical_cost_per_dose,
        pack_sizes=packs,
        notes=(payload.notes or None),
        is_active=bool(payload.is_active),
    )
    db.add(t); db.commit(); db.refresh(t)
    _audit(db, actor=(current_user.get("email") or "system"),
           action="dose_type_created",
           summary=f"Created dose type {t.label}",
           detail={"hormone": t.hormone, "dose_mg": float(t.dose_mg),
                   "is_controlled": t.is_controlled})
    db.commit()
    return _dose_type_dict(t)


class DoseTypePatch(BaseModel):
    reorder_threshold_packs: Optional[int] = None
    reorder_qty_packs:       Optional[int] = None
    reorder_thresholds_by_location: Optional[dict] = None
    typical_cost_per_dose:   Optional[float] = None
    pack_sizes:              Optional[list[int]] = None
    notes:                   Optional[str] = None
    is_active:               Optional[bool] = None


@router.patch("/dose-types/{type_id}")
def patch_dose_type(type_id: str, payload: DoseTypePatch,
                     override_reason: Optional[str] = None,
                     db: Session = Depends(get_db),
                     current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.MANAGE))):
    from app.services.pellet.lock import ensure_unlocked_or_override
    ensure_unlocked_or_override(db, current_user=current_user,
                                  override_reason=override_reason,
                                  action_label="dose-type edit")
    t = db.query(PelletDoseType).filter(PelletDoseType.id == type_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="dose type not found")
    data = payload.model_dump(exclude_unset=True)
    # Validate per-location thresholds — keys must be valid locations + values must be ints
    if "reorder_thresholds_by_location" in data:
        per_loc = data["reorder_thresholds_by_location"]
        if per_loc is not None:
            if not isinstance(per_loc, dict):
                raise HTTPException(status_code=422,
                                    detail="reorder_thresholds_by_location must be an object")
            clean = {}
            for k, v in per_loc.items():
                if k not in PELLET_LOCATIONS:
                    raise HTTPException(status_code=422,
                                        detail=f"unknown location '{k}' in reorder_thresholds_by_location")
                try:
                    iv = int(v)
                except (TypeError, ValueError):
                    raise HTTPException(status_code=422,
                                        detail=f"threshold for '{k}' must be an integer")
                if iv < 0:
                    raise HTTPException(status_code=422,
                                        detail=f"threshold for '{k}' must be ≥ 0")
                clean[k] = iv
            data["reorder_thresholds_by_location"] = clean or None
    for k, v in data.items():
        setattr(t, k, v)
    _audit(db, actor=current_user.get("email") or "system",
           action="dose_type_edited", dose_type_id=t.id,
           summary=f"Edited {t.label}", detail={"changed": list(data.keys())})
    db.commit(); db.refresh(t)
    return _dose_type_dict(t)


# ─── Dashboard ──────────────────────────────────────────────────────

STALE_PACKED_HOURS = 4      # packed but no courier pickup
STALE_IN_TRANSIT_HOURS = 24 # courier has it but no destination receive


def _transfer_dashboard_entry(t) -> dict:
    """Build the per-transfer payload for the dashboard, including
    chain-of-custody fields and a stale flag."""
    now = now_utc_naive()
    if t.status == "packed":
        anchor = t.sent_at
        hours_in_state = int((now - anchor).total_seconds() // 3600) if anchor else 0
        is_stale = hours_in_state >= STALE_PACKED_HOURS
    elif t.status == "in_transit":
        anchor = t.courier_picked_up_at or t.sent_at
        hours_in_state = int((now - anchor).total_seconds() // 3600) if anchor else 0
        is_stale = hours_in_state >= STALE_IN_TRANSIT_HOURS
    else:
        hours_in_state = 0
        is_stale = False
    label = (t.lot.dose_type.label
              if t.lot and t.lot.dose_type else None)
    return {
        "id": str(t.id),
        "lot_id": str(t.lot_id),
        "dose_label": label,
        "is_controlled": bool(t.lot.dose_type.is_controlled)
                            if (t.lot and t.lot.dose_type) else False,
        "from_location": t.from_location,
        "to_location": t.to_location,
        "doses": t.doses,
        "status": t.status,
        "sent_at": t.sent_at.isoformat() if t.sent_at else None,
        "sent_by": t.sent_by,
        "courier_user": t.courier_user,
        "courier_picked_up_at": (t.courier_picked_up_at.isoformat()
                                    if t.courier_picked_up_at else None),
        "hours_in_state": hours_in_state,
        "is_stale": is_stale,
        # Back-compat field
        "hours_in_transit": int((now - t.sent_at).total_seconds() // 3600)
                                if t.sent_at else 0,
    }


def _count_blockers_by_location(db: Session) -> dict:
    """Single query — per-location count of visits with Proposed doses at or
    before today. Returns {location: count, total: N, locations: {...}}.
    Used by the dashboard to surface a "Daily count blocked" banner without
    making 4 separate pre-check calls.
    """
    today = _date.today()
    proposed_statuses = ["planned", "pulled"]
    excluded_visit_statuses = ["cancelled", "billed"]
    visit_ids_by_loc: dict[str, set] = {loc: set() for loc in PELLET_LOCATIONS}
    rows = (db.query(PelletVisit.id, PelletVisit.location)
              .join(PelletVisitDose, PelletVisitDose.visit_id == PelletVisit.id)
              .filter(PelletVisit.scheduled_date.isnot(None),
                      PelletVisit.scheduled_date <= today,
                      PelletVisit.status.notin_(excluded_visit_statuses),
                      PelletVisit.is_historical.is_(False),
                      PelletVisitDose.status.in_(proposed_statuses))
              .distinct().all())
    for vid, loc in rows:
        if loc in visit_ids_by_loc:
            visit_ids_by_loc[loc].add(vid)
        else:
            visit_ids_by_loc.setdefault("(unset)", set()).add(vid)
    # total is the count of *distinct* blocking visits (Fable audit #18:
    # a visit with multiple proposed doses used to inflate the banner).
    distinct_visit_ids = set().union(*visit_ids_by_loc.values())
    return {
        "total": len(distinct_visit_ids),
        "locations": {loc: len(ids) for loc, ids in visit_ids_by_loc.items()},
    }


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db),
                current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.VIEW))):
    today = _date.today()

    # On-hand by (hormone, location)
    on_hand_rows = (db.query(PelletDoseType.hormone,
                              PelletStock.location,
                              func.coalesce(func.sum(PelletStock.doses_on_hand), 0))
                      .join(PelletLot, PelletLot.dose_type_id == PelletDoseType.id)
                      .join(PelletStock, PelletStock.lot_id == PelletLot.id)
                      .group_by(PelletDoseType.hormone, PelletStock.location)
                      .all())
    on_hand = {h: {loc: 0 for loc in PELLET_LOCATIONS} for h in ("estradiol", "testosterone")}
    for hormone, loc, doses in on_hand_rows:
        if hormone in on_hand and loc in on_hand[hormone]:
            on_hand[hormone][loc] = int(doses or 0)

    # Reorder alerts. Two paths:
    #   • Per-location override set: one alert per below-threshold location.
    #   • Otherwise: legacy global path — alert when total ≤ threshold.
    reorder = []
    types = db.query(PelletDoseType).filter(PelletDoseType.is_active.is_(True)).all()
    # Single bulk pull of per-(dose_type, location) on-hand balances — avoids
    # one SUM query per dose type inside the loop below (was 1-2 N queries).
    balance_rows = (db.query(PelletLot.dose_type_id,
                              PelletStock.location,
                              func.coalesce(func.sum(PelletStock.doses_on_hand), 0))
                      .join(PelletStock, PelletStock.lot_id == PelletLot.id)
                      .group_by(PelletLot.dose_type_id, PelletStock.location)
                      .all())
    balances_by_type: dict = {}
    for type_id, loc, doses in balance_rows:
        balances_by_type.setdefault(str(type_id), {})[loc] = int(doses or 0)

    for t in types:
        per_loc = t.reorder_thresholds_by_location or None
        min_pack = min(t.pack_sizes) if t.pack_sizes else 6
        type_balances = balances_by_type.get(str(t.id), {})
        if per_loc:
            for loc, thresh_packs in per_loc.items():
                if thresh_packs is None:
                    continue
                doses = type_balances.get(loc, 0)
                packs = doses // (min_pack or 6) if (min_pack or 6) else 0
                if packs <= int(thresh_packs):
                    reorder.append({
                        "dose_type_id":     str(t.id),
                        "label":            t.label,
                        "is_controlled":    bool(t.is_controlled),
                        "location":         loc,
                        "on_hand_doses":    doses,
                        "on_hand_packs":    packs,
                        "threshold_packs":  int(thresh_packs),
                        "order_qty_packs":  t.reorder_qty_packs,
                    })
            continue

        if not t.reorder_threshold_packs and t.reorder_threshold_packs != 0:
            continue
        # Legacy global threshold — sum the per-location balances we already have
        doses = sum(type_balances.values())
        packs = int(doses) // (min_pack or 6) if (min_pack or 6) else 0
        if packs <= (t.reorder_threshold_packs or 0):
            reorder.append({
                "dose_type_id":   str(t.id),
                "label":          t.label,
                "is_controlled":  bool(t.is_controlled),
                "location":       None,
                "on_hand_doses":  int(doses),
                "on_hand_packs":  packs,
                "threshold_packs": t.reorder_threshold_packs,
                "order_qty_packs": t.reorder_qty_packs,
            })

    # Expiring soon — within 90 days
    horizon = today + timedelta(days=90)
    expiring_lots = (db.query(PelletLot)
                       .options(joinedload(PelletLot.dose_type),
                                joinedload(PelletLot.stock_rows))
                       .filter(PelletLot.expiration_date <= horizon)
                       .order_by(PelletLot.expiration_date).limit(30).all())
    expiring = []
    for l in expiring_lots:
        balances = {s.location: s.doses_on_hand for s in (l.stock_rows or [])}
        total = sum(balances.values())
        if total <= 0:
            continue
        expiring.append({
            "lot_id":            str(l.id),
            "qualgen_lot":       l.qualgen_lot_number,
            "label":             l.dose_type.label if l.dose_type else None,
            "expiration_date":   str(l.expiration_date),
            "days_to_expiry":    (l.expiration_date - today).days,
            "doses_on_hand":     total,
        })

    # Open transfers — packed (awaiting courier pickup) + in_transit
    open_transfers = (db.query(PelletTransfer)
                        .options(joinedload(PelletTransfer.lot)
                                    .joinedload(PelletLot.dose_type))
                        .filter(PelletTransfer.status.in_(["packed", "in_transit"]))
                        .order_by(PelletTransfer.sent_at).all())

    # Open counts
    open_counts = (db.query(PelletCount)
                     .filter(PelletCount.status == "in_progress")
                     .order_by(PelletCount.started_at).all())
    # Single GROUP BY for all open counts' uncounted lines — avoids one
    # COUNT query per open count below.
    open_count_ids = [c.id for c in open_counts]
    remaining_by_count: dict = {}
    if open_count_ids:
        remaining_rows = (db.query(PelletCountLine.count_id,
                                    func.count(PelletCountLine.id))
                            .filter(PelletCountLine.count_id.in_(open_count_ids),
                                    PelletCountLine.counted_at.is_(None))
                            .group_by(PelletCountLine.count_id).all())
        remaining_by_count = {cid: int(n) for (cid, n) in remaining_rows}

    # Open orders (placed / partially_received) — flag late ones
    open_orders = (db.query(PelletOrder)
                      .options(joinedload(PelletOrder.lines))
                      .filter(PelletOrder.status.in_(["placed", "partially_received"]))
                      .order_by(PelletOrder.expected_delivery_date.asc().nullslast(),
                                PelletOrder.order_date.asc()).all())
    late_orders = []
    in_transit_orders = []
    for o in open_orders:
        is_late = bool(o.expected_delivery_date and o.expected_delivery_date < today)
        days = ((o.expected_delivery_date - today).days
                  if o.expected_delivery_date else None)
        entry = {
            "id":                     str(o.id),
            "qualgen_order_number":   o.qualgen_order_number,
            "order_date":             str(o.order_date) if o.order_date else None,
            "expected_delivery_date": str(o.expected_delivery_date)
                                          if o.expected_delivery_date else None,
            "days_until":             days,
            "status":                 o.status,
            "placed_by":              o.placed_by,
            "is_replacement":         bool(o.is_replacement),
            "doses_ordered":          sum((l.pack_size or 0) * (l.pack_count or 0)
                                            for l in (o.lines or [])),
        }
        (late_orders if is_late else in_transit_orders).append(entry)

    return {
        "today": str(today),
        "on_hand_by_hormone_location": on_hand,
        "reorder_alerts": reorder,
        "expiring_soon": expiring,
        "open_transfers": [
            _transfer_dashboard_entry(t) for t in open_transfers
        ],
        "transfers_awaiting_pickup": [
            _transfer_dashboard_entry(t) for t in open_transfers if t.status == "packed"
        ],
        "transfers_in_transit": [
            _transfer_dashboard_entry(t) for t in open_transfers if t.status == "in_transit"
        ],
        "open_counts": [
            {
                "id": str(c.id),
                "location": c.location,
                "started_at": c.started_at.isoformat(),
                "started_by": c.started_by,
                "lines_remaining": remaining_by_count.get(c.id, 0),
            }
            for c in open_counts
        ],
        "open_orders":         in_transit_orders,
        "late_orders":         late_orders,
        "count_blockers_by_location": _count_blockers_by_location(db),
    }


# ─── Lots: list / get ───────────────────────────────────────────────

@router.get("/lots")
def list_lots(
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.VIEW)),
    dose_type_id: Optional[str] = None,
    hormone: Optional[str] = None,
    location: Optional[str] = None,
    in_stock_only: bool = True,
    search: Optional[str] = None,
):
    q = (db.query(PelletLot)
           .options(joinedload(PelletLot.dose_type),
                    joinedload(PelletLot.stock_rows)))
    if dose_type_id:
        q = q.filter(PelletLot.dose_type_id == dose_type_id)
    if hormone:
        q = q.join(PelletDoseType).filter(PelletDoseType.hormone == hormone)
    if search:
        q = q.filter(PelletLot.qualgen_lot_number.ilike(f"%{search}%"))
    rows = q.order_by(desc(PelletLot.received_at)).all()
    out = []
    for l in rows:
        balances = {s.location: s.doses_on_hand for s in (l.stock_rows or [])}
        total = sum(balances.values())
        if in_stock_only and total <= 0:
            continue
        if location and balances.get(location, 0) <= 0:
            continue
        out.append(_lot_dict(l, balances=balances))
    return {"total": len(out), "lots": out}


def _collect_lots_for_export(db: Session, *,
                              hormone: Optional[str] = None,
                              location: Optional[str] = None,
                              search: Optional[str] = None,
                              in_stock_only: bool = True) -> list[dict]:
    """Replicates the list_lots filtering — kept separate so exports stay
    consistent with the on-screen card."""
    q = (db.query(PelletLot)
           .options(joinedload(PelletLot.dose_type),
                    joinedload(PelletLot.stock_rows)))
    if hormone:
        q = q.join(PelletDoseType).filter(PelletDoseType.hormone == hormone)
    if search:
        q = q.filter(PelletLot.qualgen_lot_number.ilike(f"%{search}%"))
    rows = q.order_by(desc(PelletLot.received_at)).all()
    out = []
    for l in rows:
        balances = {s.location: s.doses_on_hand for s in (l.stock_rows or [])}
        total = sum(balances.values())
        if in_stock_only and total <= 0:
            continue
        if location and balances.get(location, 0) <= 0:
            continue
        out.append(_lot_dict(l, balances=balances))
    return out


@router.get("/lots/export.xlsx")
def export_lots_xlsx(
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.VIEW)),
    hormone: Optional[str] = None,
    location: Optional[str] = None,
    in_stock_only: bool = True,
    search: Optional[str] = None,
):
    """Pellet inventory as an Excel workbook (one row per lot, grouped by
    dose type, with per-location balances + grand total)."""
    from fastapi.responses import Response
    from app.services.pellet.inventory_export import build_xlsx
    rows = _collect_lots_for_export(db, hormone=hormone, location=location,
                                      search=search, in_stock_only=in_stock_only)
    meta = {"hormone": hormone, "location": location, "search": search,
            "in_stock_only": "yes" if in_stock_only else "no"}
    by = current_user.get("email") or "system"
    xlsx = build_xlsx(rows, filters_meta=meta, generated_by=by)
    _audit(db, actor=by, action="inventory_export",
           detail={"format": "xlsx", "row_count": len(rows), "filters": meta},
           summary=(f"Exported pellet inventory ({len(rows)} lots) as xlsx"))
    db.commit()
    fname = f"pellet-inventory-{_date.today().isoformat()}.xlsx"
    return Response(
        content=xlsx,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.get("/lots/export.pdf")
def export_lots_pdf(
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.VIEW)),
    hormone: Optional[str] = None,
    location: Optional[str] = None,
    in_stock_only: bool = True,
    search: Optional[str] = None,
):
    """Pellet inventory as a print-friendly PDF (landscape letter)."""
    from fastapi.responses import Response
    from app.services.pellet.inventory_export import build_pdf
    rows = _collect_lots_for_export(db, hormone=hormone, location=location,
                                      search=search, in_stock_only=in_stock_only)
    meta = {"hormone": hormone, "location": location, "search": search,
            "in_stock_only": "yes" if in_stock_only else "no"}
    by = current_user.get("email") or "system"
    pdf = build_pdf(rows, filters_meta=meta, generated_by=by)
    _audit(db, actor=by, action="inventory_export",
           detail={"format": "pdf", "row_count": len(rows), "filters": meta},
           summary=(f"Exported pellet inventory ({len(rows)} lots) as pdf"))
    db.commit()
    fname = f"pellet-inventory-{_date.today().isoformat()}.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{fname}"'},
    )


@router.get("/lots/{lot_id}")
def get_lot(lot_id: str,
             db: Session = Depends(get_db),
             current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.VIEW))):
    l = (db.query(PelletLot)
           .options(joinedload(PelletLot.dose_type),
                    joinedload(PelletLot.stock_rows))
           .filter(PelletLot.id == lot_id).first())
    if not l:
        raise HTTPException(status_code=404, detail="lot not found")
    balances = {s.location: s.doses_on_hand for s in (l.stock_rows or [])}
    return _lot_dict(l, balances=balances)


class LotPatchIn(BaseModel):
    qualgen_lot_number: Optional[str] = None
    expiration_date:    Optional[str] = None
    notes:              Optional[str] = None
    reason:             str               # required for audit


@router.patch("/lots/{lot_id}")
def patch_lot(lot_id: str, payload: LotPatchIn,
                override_reason: Optional[str] = None,
                db: Session = Depends(get_db),
                current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.MANAGE))):
    """Edit lot identification — Qualgen lot #, expiration, notes. Used to
    correct placeholder ('made-up') lot numbers after the fact. A reason is
    required for the audit trail. Blocked while the lot is part of an
    in-progress daily count."""
    from app.services.pellet.lock import ensure_unlocked_or_override
    ensure_unlocked_or_override(db, current_user=current_user,
                                  override_reason=override_reason,
                                  action_label="lot edit")
    if not (payload.reason or "").strip():
        raise HTTPException(status_code=422, detail="reason is required")
    l = (db.query(PelletLot).options(joinedload(PelletLot.dose_type))
           .filter(PelletLot.id == lot_id).first())
    if not l:
        raise HTTPException(status_code=404, detail="lot not found")
    locked = (db.query(PelletCountLine)
                .join(PelletCount, PelletCount.id == PelletCountLine.count_id)
                .filter(PelletCountLine.lot_id == l.id,
                        PelletCount.status == "in_progress").first())
    if locked:
        raise HTTPException(status_code=409,
                            detail="lot is part of an in-progress count — finish that count first")

    before = {
        "qualgen_lot_number": l.qualgen_lot_number,
        "expiration_date":    str(l.expiration_date) if l.expiration_date else None,
        "notes":              l.notes,
    }
    changed = {}
    if payload.qualgen_lot_number is not None:
        new = payload.qualgen_lot_number.strip()
        if new and new != l.qualgen_lot_number:
            changed["qualgen_lot_number"] = (l.qualgen_lot_number, new)
            l.qualgen_lot_number = new
    if payload.expiration_date is not None:
        new = _parse_date(payload.expiration_date, "expiration_date")
        if new and new != l.expiration_date:
            changed["expiration_date"] = (str(l.expiration_date) if l.expiration_date else None,
                                            str(new))
            l.expiration_date = new
    if payload.notes is not None and (payload.notes or None) != l.notes:
        changed["notes"] = (l.notes, payload.notes or None)
        l.notes = payload.notes or None

    if not changed:
        return _lot_dict(l, balances={s.location: s.doses_on_hand for s in (l.stock_rows or [])})

    by = current_user.get("email") or "system"
    _audit(db, actor=by, action="lot_edited",
            lot_id=l.id, dose_type_id=l.dose_type_id,
            summary=(f"Edited lot {(before['qualgen_lot_number'] or '?')}: "
                    + ", ".join([f"{k}: {a} → {b}" for k, (a, b) in changed.items()])
                    + f" (reason: {payload.reason.strip()})"),
            detail={"before": before,
                    "changed": {k: {"from": a, "to": b} for k, (a, b) in changed.items()},
                    "reason": payload.reason.strip()})
    db.commit(); db.refresh(l)
    return _lot_dict(l, balances={s.location: s.doses_on_hand for s in (l.stock_rows or [])})


# ─── Orders (Qualgen purchase orders, placed BEFORE shipment) ──────


def _order_dict(o: PelletOrder, *, include_lines: bool = True,
                  include_attachments: bool = True) -> dict:
    lines_payload = []
    line_total_sum = Decimal("0")
    doses_total = 0
    if include_lines:
        for l in (o.lines or []):
            unit_cost = Decimal(l.unit_cost or 0)
            doses = (l.pack_size or 0) * (l.pack_count or 0)
            line_total = unit_cost * Decimal(l.pack_count or 0)
            cost_per_dose = (unit_cost / Decimal(l.pack_size)) if l.pack_size else None
            line_total_sum += line_total
            doses_total += doses
            lines_payload.append({
                "id":             str(l.id),
                "dose_type_id":   str(l.dose_type_id),
                "dose_label":     l.dose_type.label if l.dose_type else None,
                "is_controlled":  bool(l.dose_type.is_controlled) if l.dose_type else False,
                "pack_size":      l.pack_size,
                "pack_count":     l.pack_count,
                "doses_ordered":  doses,
                "doses_received": int(l.doses_received or 0),
                "unit_cost":      float(unit_cost),
                "line_total":     float(line_total),
                "cost_per_dose":  float(cost_per_dose) if cost_per_dose is not None else None,
                "notes":          l.notes,
            })
    shipping = Decimal(o.shipping_cost or 0)
    tax = Decimal(o.tax or 0)
    grand_total = line_total_sum + shipping + tax

    today = _date.today()
    overdue = bool(o.expected_delivery_date and o.status not in ("received", "cancelled")
                    and o.expected_delivery_date < today)

    out = {
        "id":                     str(o.id),
        "qualgen_order_number":   o.qualgen_order_number,
        "order_date":             str(o.order_date) if o.order_date else None,
        "expected_delivery_date": str(o.expected_delivery_date) if o.expected_delivery_date else None,
        "placed_by":              o.placed_by,
        "status":                 o.status,
        "payment_method":         o.payment_method,
        "payment_confirmation":   o.payment_confirmation,
        "shipping_cost":          float(shipping),
        "tax":                    float(tax),
        "is_replacement":         bool(o.is_replacement),
        "replaces_disposal_id":   str(o.replaces_disposal_id) if o.replaces_disposal_id else None,
        "notes":                  o.notes,
        "created_at":             o.created_at.isoformat() if o.created_at else None,
        "updated_at":              o.updated_at.isoformat() if o.updated_at else None,
        "lines_subtotal":         float(line_total_sum),
        "grand_total":            float(grand_total),
        "doses_total":            doses_total,
        "is_overdue":             overdue,
    }
    if include_lines:
        out["lines"] = lines_payload
    if include_attachments:
        out["attachments"] = [
            {
                "id":           str(a.id),
                "filename":     a.filename,
                "content_type": a.content_type,
                "size_bytes":   a.size_bytes,
                "uploaded_at":  a.uploaded_at.isoformat() if a.uploaded_at else None,
                "uploaded_by":  a.uploaded_by,
            }
            for a in (o.attachments or [])
        ]
    # Receipts already linked to this order (for status badges in the UI)
    out["receipts"] = [
        {
            "id":                  str(r.id),
            "received_date":       str(r.received_date) if r.received_date else None,
            "manifest_verified":   bool(r.manifest_verified),
            "manifest_verified_at": r.manifest_verified_at.isoformat()
                                      if r.manifest_verified_at else None,
            "attachments": [
                {
                    "id":           str(a.id),
                    "filename":     a.filename,
                    "size_bytes":   a.size_bytes,
                    "uploaded_at":  a.uploaded_at.isoformat() if a.uploaded_at else None,
                }
                for a in (r.attachments or [])
            ],
        }
        for r in (o.receipts or [])
    ]
    return out


class OrderLineIn(BaseModel):
    dose_type_id: str
    pack_size:    PackSize
    pack_count:   PackCount
    unit_cost:    MoneyAmt
    notes:        Optional[str] = None


class OrderIn(BaseModel):
    qualgen_order_number:   Optional[str] = None
    order_date:             Optional[str] = None    # default today
    expected_delivery_date: Optional[str] = None    # default order_date + 4 business days
    payment_method:         Optional[str] = None
    payment_confirmation:   Optional[str] = None
    shipping_cost:          MoneyAmt = 0
    tax:                    MoneyAmt = 0
    is_replacement:         bool = False
    replaces_disposal_id:   Optional[str] = None
    notes:                  Optional[str] = None
    lines:                  list[OrderLineIn]


def _add_business_days(d: _date, n: int) -> _date:
    """Add n business days (Mon–Fri), skipping weekends. n must be >= 0."""
    out = d
    added = 0
    while added < n:
        out = out + timedelta(days=1)
        if out.weekday() < 5:  # 0=Mon, 4=Fri
            added += 1
    return out


@router.get("/orders/reorder-prefill")
def reorder_prefill(db: Session = Depends(get_db),
                      current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.WORK))):
    """Build a starter order from the dashboard's reorder alerts. UI calls
    this and pre-fills the "Place order" form with one line per
    below-threshold dose, using the configured reorder_qty_packs."""
    types = db.query(PelletDoseType).filter(PelletDoseType.is_active.is_(True)).all()
    lines = []
    for t in types:
        if t.reorder_threshold_packs is None:
            continue
        doses = (db.query(func.coalesce(func.sum(PelletStock.doses_on_hand), 0))
                   .join(PelletLot, PelletLot.id == PelletStock.lot_id)
                   .filter(PelletLot.dose_type_id == t.id).scalar() or 0)
        min_pack = min(t.pack_sizes) if t.pack_sizes else 6
        packs = int(doses) // (min_pack or 6) if (min_pack or 6) else 0
        if packs <= (t.reorder_threshold_packs or 0):
            lines.append({
                "dose_type_id":  str(t.id),
                "dose_label":    t.label,
                "pack_size":     min_pack or 6,
                "pack_count":    t.reorder_qty_packs or 6,
                "unit_cost":     float(t.typical_cost_per_dose * (min_pack or 6))
                                    if t.typical_cost_per_dose else 0,
                "is_controlled": bool(t.is_controlled),
            })
    return {"lines": lines}


@router.post("/orders", status_code=201)
def create_order(payload: OrderIn,
                   db: Session = Depends(get_db),
                   current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.WORK))):
    if not payload.lines:
        raise HTTPException(status_code=422, detail="at least one line required")
    if payload.payment_method and payload.payment_method not in PAYMENT_METHODS:
        raise HTTPException(status_code=422,
                            detail=f"payment_method must be one of: {', '.join(PAYMENT_METHODS)}")

    order_date = _parse_date(payload.order_date, "order_date") or _date.today()
    expected = (_parse_date(payload.expected_delivery_date, "expected_delivery_date")
                  or _add_business_days(order_date, 4))

    if payload.is_replacement and not payload.replaces_disposal_id:
        raise HTTPException(status_code=422,
                            detail="replacement orders must reference replaces_disposal_id")
    if payload.replaces_disposal_id:
        disp = db.query(PelletDisposal).filter(
            PelletDisposal.id == payload.replaces_disposal_id).first()
        if not disp:
            raise HTTPException(status_code=404, detail="disposal not found")

    by = current_user.get("email") or "system"
    o = PelletOrder(
        qualgen_order_number=(payload.qualgen_order_number or "").strip() or None,
        order_date=order_date,
        expected_delivery_date=expected,
        placed_by=by,
        status="placed",
        payment_method=payload.payment_method,
        payment_confirmation=(payload.payment_confirmation or "").strip() or None,
        shipping_cost=Decimal(str(payload.shipping_cost or 0)),
        tax=Decimal(str(payload.tax or 0)),
        is_replacement=bool(payload.is_replacement),
        replaces_disposal_id=payload.replaces_disposal_id,
        notes=payload.notes,
    )
    db.add(o); db.flush()

    for line in payload.lines:
        dt = db.query(PelletDoseType).filter(PelletDoseType.id == line.dose_type_id).first()
        if not dt:
            raise HTTPException(status_code=404,
                                detail=f"dose_type {line.dose_type_id} not found")
        if line.pack_count <= 0 or line.pack_size <= 0:
            raise HTTPException(status_code=422,
                                detail="pack_size and pack_count must be > 0")
        db.add(PelletOrderLine(
            order_id=o.id,
            dose_type_id=dt.id,
            pack_size=line.pack_size,
            pack_count=line.pack_count,
            unit_cost=Decimal(str(line.unit_cost or 0)),
            notes=line.notes,
        ))

    _audit(db, actor=by, action="order_placed",
           summary=f"Order placed (Qualgen #{o.qualgen_order_number or '?'}, "
                   f"{len(payload.lines)} line(s))"
                   + (" REPLACEMENT" if o.is_replacement else ""),
           detail={"order_id": str(o.id), "qualgen_order_number": o.qualgen_order_number,
                   "expected_delivery_date": str(expected),
                   "is_replacement": o.is_replacement})
    db.commit()
    db.refresh(o)
    return _order_dict(o)


@router.get("/orders")
def list_orders(db: Session = Depends(get_db),
                  current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.VIEW)),
                  limit: int = 5,
                  include_cancelled: bool = False):
    q = (db.query(PelletOrder)
            .options(joinedload(PelletOrder.lines).joinedload(PelletOrderLine.dose_type),
                     joinedload(PelletOrder.attachments),
                     joinedload(PelletOrder.receipts)))
    if not include_cancelled:
        q = q.filter(PelletOrder.status != "cancelled")
    rows = q.order_by(desc(PelletOrder.order_date),
                        desc(PelletOrder.created_at)).limit(max(1, min(limit, 100))).all()
    return {"orders": [_order_dict(o) for o in rows]}


@router.get("/orders/open")
def list_open_orders(db: Session = Depends(get_db),
                       current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.VIEW))):
    """Orders that can be received against — status placed/partially_received.
    Used by the Receive Shipment form's order picker."""
    rows = (db.query(PelletOrder)
              .options(joinedload(PelletOrder.lines).joinedload(PelletOrderLine.dose_type))
              .filter(PelletOrder.status.in_(["placed", "partially_received"]))
              .order_by(PelletOrder.order_date).all())
    return {"orders": [_order_dict(o, include_attachments=False) for o in rows]}


@router.get("/orders/{order_id}")
def get_order(order_id: str,
                db: Session = Depends(get_db),
                current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.VIEW))):
    o = (db.query(PelletOrder)
            .options(joinedload(PelletOrder.lines).joinedload(PelletOrderLine.dose_type),
                     joinedload(PelletOrder.attachments),
                     joinedload(PelletOrder.receipts))
            .filter(PelletOrder.id == order_id).first())
    if not o:
        raise HTTPException(status_code=404, detail="order not found")
    return _order_dict(o)


class OrderPatchIn(BaseModel):
    qualgen_order_number:   Optional[str] = None
    order_date:             Optional[str] = None
    expected_delivery_date: Optional[str] = None
    payment_method:         Optional[str] = None
    payment_confirmation:   Optional[str] = None
    shipping_cost:          Optional[MoneyAmt] = None
    tax:                    Optional[MoneyAmt] = None
    notes:                  Optional[str] = None
    lines:                  Optional[list[OrderLineIn]] = None


@router.patch("/orders/{order_id}")
def patch_order(order_id: str, payload: OrderPatchIn,
                  db: Session = Depends(get_db),
                  current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.WORK))):
    o = db.query(PelletOrder).filter(PelletOrder.id == order_id).first()
    if not o:
        raise HTTPException(status_code=404, detail="order not found")
    if o.status == "received":
        raise HTTPException(status_code=409,
                            detail="cannot edit a fully-received order")
    if o.status == "cancelled":
        raise HTTPException(status_code=409, detail="cannot edit a cancelled order")

    has_receipts = bool(o.receipts)
    by = current_user.get("email") or "system"
    before = {
        "qualgen_order_number":   o.qualgen_order_number,
        "order_date":             str(o.order_date) if o.order_date else None,
        "expected_delivery_date": str(o.expected_delivery_date) if o.expected_delivery_date else None,
        "shipping_cost":          float(o.shipping_cost or 0),
        "tax":                    float(o.tax or 0),
        "lines":                  [{"dose_type_id": str(l.dose_type_id),
                                     "pack_size": l.pack_size,
                                     "pack_count": l.pack_count,
                                     "unit_cost": float(l.unit_cost or 0)}
                                    for l in (o.lines or [])],
    }

    if payload.qualgen_order_number is not None:
        o.qualgen_order_number = payload.qualgen_order_number.strip() or None
    if payload.order_date is not None:
        o.order_date = _parse_date(payload.order_date, "order_date")
    if payload.expected_delivery_date is not None:
        o.expected_delivery_date = _parse_date(payload.expected_delivery_date,
                                                 "expected_delivery_date")
    if payload.payment_method is not None:
        if payload.payment_method and payload.payment_method not in PAYMENT_METHODS:
            raise HTTPException(status_code=422,
                                detail=f"payment_method must be one of: {', '.join(PAYMENT_METHODS)}")
        o.payment_method = payload.payment_method or None
    if payload.payment_confirmation is not None:
        o.payment_confirmation = payload.payment_confirmation.strip() or None
    if payload.shipping_cost is not None:
        o.shipping_cost = Decimal(str(payload.shipping_cost))
    if payload.tax is not None:
        o.tax = Decimal(str(payload.tax))
    if payload.notes is not None:
        o.notes = payload.notes or None

    if payload.lines is not None:
        # Replace lines wholesale. Validate first.
        for line in payload.lines:
            dt = db.query(PelletDoseType).filter(PelletDoseType.id == line.dose_type_id).first()
            if not dt:
                raise HTTPException(status_code=404,
                                    detail=f"dose_type {line.dose_type_id} not found")
            if line.pack_count <= 0 or line.pack_size <= 0:
                raise HTTPException(status_code=422,
                                    detail="pack_size and pack_count must be > 0")
        # Clear and recreate
        for old in list(o.lines):
            db.delete(old)
        db.flush()
        for line in payload.lines:
            db.add(PelletOrderLine(
                order_id=o.id,
                dose_type_id=line.dose_type_id,
                pack_size=line.pack_size,
                pack_count=line.pack_count,
                unit_cost=Decimal(str(line.unit_cost or 0)),
                notes=line.notes,
            ))

    action = "order_amended" if has_receipts else "order_edited"
    _audit(db, actor=by, action=action,
           summary=f"Order {o.qualgen_order_number or o.id} {action.replace('_', ' ')}",
           detail={"order_id": str(o.id), "before": before,
                    "had_receipts": has_receipts})

    db.commit(); db.refresh(o)
    return _order_dict(o)


@router.post("/orders/{order_id}/cancel")
def cancel_order(order_id: str,
                   db: Session = Depends(get_db),
                   current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.WORK))):
    o = db.query(PelletOrder).filter(PelletOrder.id == order_id).first()
    if not o:
        raise HTTPException(status_code=404, detail="order not found")
    if o.receipts:
        raise HTTPException(status_code=409,
                            detail="cannot cancel an order that has receipts")
    if o.status == "cancelled":
        raise HTTPException(status_code=409, detail="order already cancelled")
    o.status = "cancelled"
    by = current_user.get("email") or "system"
    _audit(db, actor=by, action="order_cancelled",
           summary=f"Cancelled order {o.qualgen_order_number or o.id}",
           detail={"order_id": str(o.id)})
    db.commit(); db.refresh(o)
    return _order_dict(o)


@router.post("/orders/{order_id}/attachments", status_code=201)
async def upload_order_attachment(order_id: str,
                                    file: UploadFile = File(...),
                                    db: Session = Depends(get_db),
                                    current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.WORK))):
    """Upload a PDF invoice / receipt for this order."""
    o = db.query(PelletOrder).filter(PelletOrder.id == order_id).first()
    if not o:
        raise HTTPException(status_code=404, detail="order not found")
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=422, detail="expected a PDF file")

    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=422, detail="empty upload")
    if len(contents) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="file too large (25MB max)")
    _assert_is_pdf(contents, file.filename)

    key = save_blob(prefix="pellet-attachments", body=contents,
                    filename=file.filename or "upload.pdf")

    by = current_user.get("email") or "system"
    att = PelletOrderAttachment(
        order_id=o.id,
        filename=file.filename,
        content_type=file.content_type or "application/pdf",
        size_bytes=len(contents),
        storage_path=key,
        uploaded_by=by,
    )
    db.add(att); db.flush()
    _audit(db, actor=by, action="order_attachment_uploaded",
           summary=f"Uploaded {file.filename} to order {o.qualgen_order_number or o.id}",
           detail={"order_id": str(o.id), "filename": file.filename,
                    "size_bytes": len(contents)})
    db.commit(); db.refresh(att)
    return {
        "id":           str(att.id),
        "filename":     att.filename,
        "content_type": att.content_type,
        "size_bytes":   att.size_bytes,
        "uploaded_at":  att.uploaded_at.isoformat(),
        "uploaded_by":  att.uploaded_by,
    }


@router.get("/orders/{order_id}/attachments/{att_id}")
def download_order_attachment(order_id: str, att_id: str,
                                db: Session = Depends(get_db),
                                current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.VIEW))):
    att = (db.query(PelletOrderAttachment)
             .filter(PelletOrderAttachment.id == att_id,
                     PelletOrderAttachment.order_id == order_id).first())
    if not att:
        raise HTTPException(status_code=404, detail="attachment not found")
    if is_legacy_local_path(att.storage_path):
        raise HTTPException(status_code=410,
                              detail="This file is from before the cloud migration and is no longer available.")
    return serve_blob(
        local_path=None,
        gcs_object=att.storage_path,
        media_type=att.content_type or "application/pdf",
        filename=att.filename,
        disposition="attachment",
    )


@router.delete("/orders/{order_id}/attachments/{att_id}")
def delete_order_attachment(order_id: str, att_id: str,
                              db: Session = Depends(get_db),
                              current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.MANAGE))):
    att = (db.query(PelletOrderAttachment)
             .filter(PelletOrderAttachment.id == att_id,
                     PelletOrderAttachment.order_id == order_id).first())
    if not att:
        raise HTTPException(status_code=404, detail="attachment not found")
    # NB: we do not delete the underlying blob — orphans are cheap and
    # the audit trail is preserved.
    by = current_user.get("email") or "system"
    fname = att.filename
    db.delete(att)
    _audit(db, actor=by, action="order_attachment_deleted",
           summary=f"Deleted attachment {fname} from order {order_id}",
           detail={"order_id": order_id, "filename": fname})
    db.commit()
    return {"ok": True}


# ─── Receive a shipment ─────────────────────────────────────────────

class LotIn(BaseModel):
    dose_type_id:       str
    qualgen_lot_number: str
    expiration_date:    str   # YYYY-MM-DD
    pack_size:          Optional[PackSize]  = None
    packs_received:     Optional[PackCount] = None
    # 1..9999 — a real receipt must include at least one dose; the
    # upper bound is generous (a giant order is still bounded).
    doses_received:     Annotated[int, Field(gt=0, le=9999)]
    notes:              Optional[str] = None


class ReceiptIn(BaseModel):
    qualgen_order_number: Optional[str] = None
    ordered_date:         Optional[str] = None
    received_date:        Optional[str] = None
    location:             str = "white_plains"
    lots:                 list[LotIn]
    notes:                Optional[str] = None
    # A receipt must reference exactly one of:
    #   (a) an existing open PelletOrder (the normal flow)
    #   (b) a damaged-pellet replacement (is_replacement + replaces_disposal_id)
    #   (c) "unscheduled" / found-in-cabinet: the practice physically has
    #       a lot in hand that was never logged against an order. The
    #       receipt still creates the PelletLot + PelletStock rows so
    #       the lot becomes usable; the notes field must explain why.
    order_id:             Optional[str] = None
    is_replacement:       bool = False
    replaces_disposal_id: Optional[str] = None
    is_unscheduled:       bool = False


def _parse_date(s: Optional[str], field: str) -> Optional[_date]:
    if not s:
        return None
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=422, detail=f"{field} must be YYYY-MM-DD")


@router.post("/receipts", status_code=201)
def create_receipt(payload: ReceiptIn,
                     db: Session = Depends(get_db),
                     current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.WORK))):
    """Create a receipt + lots in one shot. Stock is NOT incremented until
    the receipt is manifest-verified. The receiver and verifier should
    typically be two different people (enforced for testosterone)."""
    if payload.location not in PELLET_LOCATIONS:
        raise HTTPException(status_code=422, detail="invalid location")
    if not payload.lots:
        raise HTTPException(status_code=422, detail="at least one lot required")

    # Gating: a receipt must either reference an open order OR be a
    # damaged-pellet replacement that points at a disposal row.
    order: Optional[PelletOrder] = None
    disposal: Optional[PelletDisposal] = None
    if payload.order_id:
        order = (db.query(PelletOrder)
                    .options(joinedload(PelletOrder.lines))
                    .filter(PelletOrder.id == payload.order_id).first())
        if not order:
            raise HTTPException(status_code=404, detail="order not found")
        if order.status not in ("placed", "partially_received"):
            raise HTTPException(status_code=409,
                                detail=f"order is {order.status}; cannot receive against it")
    elif payload.is_replacement:
        if not payload.replaces_disposal_id:
            raise HTTPException(status_code=422,
                                detail="replacement receipts require replaces_disposal_id")
        disposal = (db.query(PelletDisposal)
                       .filter(PelletDisposal.id == payload.replaces_disposal_id).first())
        if not disposal:
            raise HTTPException(status_code=404, detail="disposal not found")
    elif payload.is_unscheduled:
        # Found-in-cabinet path. The lot exists physically but was never
        # logged against a PelletOrder; we still need a paper trail so
        # require an explanation in notes. The current_user becomes the
        # receiver and a manifest verifier must still sign off before
        # stock is incremented (same gate as the regular flow).
        if not (payload.notes or "").strip():
            raise HTTPException(status_code=422,
                detail="unscheduled receipts require a notes field explaining "
                       "why the lot is being recorded without an order")
    else:
        raise HTTPException(status_code=422,
                            detail="a receipt must reference an order_id, OR be "
                                   "marked is_replacement=true with replaces_disposal_id "
                                   "set, OR be marked is_unscheduled=true with a "
                                   "notes explanation")

    by = current_user.get("email") or "system"
    r = PelletReceipt(
        qualgen_order_number=(payload.qualgen_order_number
                                or (order.qualgen_order_number if order else None)),
        ordered_date=(_parse_date(payload.ordered_date, "ordered_date")
                        or (order.order_date if order else None)),
        received_date=_parse_date(payload.received_date, "received_date") or _date.today(),
        received_by=by,
        location=payload.location,
        notes=payload.notes,
        order_id=(order.id if order else None),
        is_replacement=bool(payload.is_replacement),
        replaces_disposal_id=(disposal.id if disposal else None),
    )
    # Dedup: a receipt with the same Qualgen order # received on the same
    # day at the same location is almost certainly a double-submit. The
    # check has to live in app code because historical data already
    # contains a duplicate that pre-dates this guard. (Fable audit #10.)
    if r.qualgen_order_number:
        existing = (db.query(PelletReceipt)
                      .filter(PelletReceipt.qualgen_order_number == r.qualgen_order_number,
                              PelletReceipt.received_date == r.received_date,
                              PelletReceipt.location == r.location)
                      .first())
        if existing:
            raise HTTPException(
                status_code=409,
                detail=f"a receipt for Qualgen order {r.qualgen_order_number} "
                       f"received on {r.received_date} at {r.location} already "
                       f"exists (id {existing.id})")
    db.add(r); db.flush()

    created_lots = []
    for lot in payload.lots:
        dt = db.query(PelletDoseType).filter(PelletDoseType.id == lot.dose_type_id).first()
        if not dt:
            raise HTTPException(status_code=404,
                                detail=f"dose_type {lot.dose_type_id} not found")
        # Cross-check: when pack_size and packs_received are both set,
        # they must multiply to doses_received. Catches a mistyped
        # dose count before it lands in inventory (Fable audit #4).
        if (lot.pack_size and lot.packs_received and
                lot.pack_size * lot.packs_received != lot.doses_received):
            raise HTTPException(status_code=422,
                detail=(f"lot {lot.qualgen_lot_number}: "
                        f"pack_size × packs_received "
                        f"({lot.pack_size} × {lot.packs_received} "
                        f"= {lot.pack_size * lot.packs_received}) "
                        f"must equal doses_received ({lot.doses_received})"))
        l = PelletLot(
            dose_type_id=dt.id,
            qualgen_lot_number=lot.qualgen_lot_number.strip(),
            expiration_date=_parse_date(lot.expiration_date, "expiration_date"),
            doses_originally_received=lot.doses_received,
            packs_received=lot.packs_received,
            pack_size=lot.pack_size,
            receipt_id=r.id,
            received_by=by,
            notes=lot.notes,
        )
        db.add(l); db.flush()
        _audit(db, actor=by, action="lot_received",
               receipt_id=r.id, lot_id=l.id, dose_type_id=dt.id,
               location=r.location,
               summary=(f"Received {l.doses_originally_received} doses {dt.label} "
                        f"lot {l.qualgen_lot_number} exp {l.expiration_date}"))
        created_lots.append(l)

    _audit(db, actor=by, action="receipt_created", receipt_id=r.id,
           summary=f"Created receipt {r.qualgen_order_number or '(no order #)'} "
                   f"with {len(created_lots)} lot(s)")

    # Unscheduled receipts that bring controlled (Schedule III) testosterone
    # into inventory without an order paper trail get an explicit audit row
    # so DEA reporting can surface them as "acquired without a 222-equivalent
    # record." (Fable audit #19.) The receipt already requires notes; this
    # makes the controlled flag visible without scraping notes text.
    if payload.is_unscheduled:
        controlled_lots = [l for l in created_lots
                           if l.dose_type and l.dose_type.is_controlled]
        if controlled_lots:
            _audit(db, actor=by, action="unscheduled_controlled_receipt",
                   receipt_id=r.id, location=r.location,
                   delta_doses=sum(l.doses_originally_received for l in controlled_lots),
                   detail={
                       "lot_ids": [str(l.id) for l in controlled_lots],
                       "lot_numbers": [l.qualgen_lot_number for l in controlled_lots],
                       "notes": payload.notes,
                   },
                   summary=(f"Unscheduled controlled (Sch III) receipt at "
                            f"{r.location}: {len(controlled_lots)} lot(s) entered "
                            f"without an order — flagged for DEA review"))

    db.commit(); db.refresh(r)
    return {"receipt_id": str(r.id), "lots": [str(l.id) for l in created_lots]}


class VerifyManifestIn(BaseModel):
    witness_user: Optional[str] = None    # required for any testosterone in the receipt


@router.post("/receipts/{receipt_id}/verify-manifest")
def verify_manifest(receipt_id: str, payload: VerifyManifestIn,
                     db: Session = Depends(get_db),
                     current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.WORK))):
    """Verify the manifest matches what physically arrived. THIS is the
    step that pushes doses into PelletStock at the receiving location."""
    r = (db.query(PelletReceipt)
           .filter(PelletReceipt.id == receipt_id).first())
    if not r:
        raise HTTPException(status_code=404, detail="receipt not found")
    if r.manifest_verified:
        raise HTTPException(status_code=409, detail="receipt already verified")

    # Re-pull lots
    lots = (db.query(PelletLot)
              .options(joinedload(PelletLot.dose_type))
              .filter(PelletLot.receipt_id == r.id).all())

    has_controlled = any(l.dose_type and l.dose_type.is_controlled for l in lots)
    by = current_user.get("email") or "system"

    witness = _validate_witness(db, payload.witness_user, by,
                                  controlled=has_controlled)

    # Atomic claim: only one thread flips the flag. A second concurrent
    # caller's UPDATE matches 0 rows because manifest_verified is now true,
    # so the second caller raises 409 instead of double-crediting stock.
    # (Fable audit #2.)
    now = now_utc_naive()
    claimed = db.execute(
        update(PelletReceipt)
          .where(PelletReceipt.id == r.id,
                 PelletReceipt.manifest_verified == False)  # noqa: E712
          .values(manifest_verified=True,
                  manifest_verified_by=by,
                  manifest_verified_at=now)
    ).rowcount
    if not claimed:
        raise HTTPException(status_code=409, detail="receipt already verified")
    db.refresh(r)

    # If the receipt is tied to an order, build a dose_type -> order_line
    # map so we can copy unit cost onto each lot and track doses-received
    # for status progression.
    order_lines_by_dose = {}
    order: Optional[PelletOrder] = None
    if r.order_id:
        order = (db.query(PelletOrder)
                    .options(joinedload(PelletOrder.lines))
                    .filter(PelletOrder.id == r.order_id).first())
        if order:
            for ol in (order.lines or []):
                order_lines_by_dose[str(ol.dose_type_id)] = ol

    for l in lots:
        s = _get_or_create_stock(db, l.id, r.location)
        _adjust_stock(db, s, l.doses_originally_received)

        # Copy acquisition cost from the matching order line (if any)
        ol = order_lines_by_dose.get(str(l.dose_type_id))
        if ol and ol.unit_cost is not None and l.pack_size:
            l.unit_cost = ol.unit_cost
            try:
                l.cost_per_dose = Decimal(ol.unit_cost) / Decimal(l.pack_size)
            except Exception:
                pass
            ol.doses_received = int((ol.doses_received or 0) + l.doses_originally_received)

        _audit(db, actor=by, action="stock_received",
               lot_id=l.id, receipt_id=r.id, location=r.location,
               delta_doses=l.doses_originally_received,
               summary=f"Stock +{l.doses_originally_received} {l.dose_type.label if l.dose_type else ''} "
                       f"lot {l.qualgen_lot_number} → {r.location}")

    # Advance order status if applicable
    if order:
        all_complete = True
        for ol in (order.lines or []):
            ordered_doses = (ol.pack_size or 0) * (ol.pack_count or 0)
            if (ol.doses_received or 0) < ordered_doses:
                all_complete = False
                break
        new_status = "received" if all_complete else "partially_received"
        if order.status != new_status:
            order.status = new_status
            _audit(db, actor=by, action="order_status_changed",
                   receipt_id=r.id,
                   summary=f"Order {order.qualgen_order_number or order.id} → {new_status}",
                   detail={"order_id": str(order.id), "new_status": new_status})

    _audit(db, actor=by, action="manifest_verified",
           receipt_id=r.id, location=r.location,
           detail={"witness": witness, "lots": len(lots)},
           summary=f"Manifest verified for receipt {r.qualgen_order_number or r.id} "
                   f"by {by}" + (f" witness {witness}" if witness else ""))
    db.commit()
    return {"ok": True, "verified_at": r.manifest_verified_at.isoformat()}


# ─── Receipt attachments (packing slip PDFs) ────────────────────────

@router.post("/receipts/{receipt_id}/attachments", status_code=201)
async def upload_receipt_attachment(receipt_id: str,
                                      file: UploadFile = File(...),
                                      db: Session = Depends(get_db),
                                      current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.WORK))):
    """Upload a PDF of the packing slip / shipping manifest for this receipt."""
    r = db.query(PelletReceipt).filter(PelletReceipt.id == receipt_id).first()
    if not r:
        raise HTTPException(status_code=404, detail="receipt not found")
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=422, detail="expected a PDF file")

    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=422, detail="empty upload")
    if len(contents) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="file too large (25MB max)")
    _assert_is_pdf(contents, file.filename)

    key = save_blob(prefix="pellet-attachments", body=contents,
                    filename=file.filename or "upload.pdf")

    by = current_user.get("email") or "system"
    att = PelletReceiptAttachment(
        receipt_id=r.id,
        filename=file.filename,
        content_type=file.content_type or "application/pdf",
        size_bytes=len(contents),
        storage_path=key,
        uploaded_by=by,
    )
    db.add(att); db.flush()
    _audit(db, actor=by, action="receipt_attachment_uploaded",
           receipt_id=r.id,
           summary=f"Uploaded packing slip {file.filename} to receipt {r.qualgen_order_number or r.id}",
           detail={"receipt_id": str(r.id), "filename": file.filename,
                    "size_bytes": len(contents)})
    db.commit(); db.refresh(att)
    return {
        "id":           str(att.id),
        "filename":     att.filename,
        "content_type": att.content_type,
        "size_bytes":   att.size_bytes,
        "uploaded_at":  att.uploaded_at.isoformat(),
        "uploaded_by":  att.uploaded_by,
    }


@router.get("/receipts/{receipt_id}/attachments/{att_id}")
def download_receipt_attachment(receipt_id: str, att_id: str,
                                  db: Session = Depends(get_db),
                                  current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.VIEW))):
    att = (db.query(PelletReceiptAttachment)
             .filter(PelletReceiptAttachment.id == att_id,
                     PelletReceiptAttachment.receipt_id == receipt_id).first())
    if not att:
        raise HTTPException(status_code=404, detail="attachment not found")
    if is_legacy_local_path(att.storage_path):
        raise HTTPException(status_code=410,
                              detail="This file is from before the cloud migration and is no longer available.")
    return serve_blob(
        local_path=None,
        gcs_object=att.storage_path,
        media_type=att.content_type or "application/pdf",
        filename=att.filename,
        disposition="attachment",
    )


@router.delete("/receipts/{receipt_id}/attachments/{att_id}")
def delete_receipt_attachment(receipt_id: str, att_id: str,
                                db: Session = Depends(get_db),
                                current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.MANAGE))):
    att = (db.query(PelletReceiptAttachment)
             .filter(PelletReceiptAttachment.id == att_id,
                     PelletReceiptAttachment.receipt_id == receipt_id).first())
    if not att:
        raise HTTPException(status_code=404, detail="attachment not found")
    # Block deletion of chain-of-custody documents after a receipt is
    # manifest-verified — once verification is on the books, the
    # manifest/invoice is the DEA paper trail. (Fable audit #9.)
    receipt = (db.query(PelletReceipt)
                  .filter(PelletReceipt.id == receipt_id).first())
    if receipt and receipt.manifest_verified:
        raise HTTPException(status_code=409,
                            detail="cannot delete attachment after the receipt is manifest-verified")
    # NB: we do not delete the underlying blob — orphans are cheap and
    # the audit trail is preserved.
    by = current_user.get("email") or "system"
    fname = att.filename
    db.delete(att)
    _audit(db, actor=by, action="receipt_attachment_deleted",
           receipt_id=receipt_id,
           summary=f"Deleted packing slip {fname} from receipt {receipt_id}",
           detail={"receipt_id": receipt_id, "filename": fname})
    db.commit()
    return {"ok": True}


@router.get("/receipts")
def list_receipts(
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.VIEW)),
    verified_only: bool = False,
    page: int = 1,
    per_page: int = 50,
):
    q = db.query(PelletReceipt)
    if verified_only:
        q = q.filter(PelletReceipt.manifest_verified.is_(True))
    total = q.count()
    rows = (q.order_by(desc(PelletReceipt.received_date),
                       desc(PelletReceipt.created_at))
              .offset((page - 1) * per_page).limit(per_page).all())
    return {"total": total, "page": page,
            "receipts": [
                {
                    "id": str(r.id),
                    "qualgen_order_number": r.qualgen_order_number,
                    "ordered_date": str(r.ordered_date) if r.ordered_date else None,
                    "received_date": str(r.received_date),
                    "received_by": r.received_by,
                    "location": r.location,
                    "manifest_verified": bool(r.manifest_verified),
                    "manifest_verified_by": r.manifest_verified_by,
                    "manifest_verified_at": r.manifest_verified_at.isoformat()
                                              if r.manifest_verified_at else None,
                    "notes": r.notes,
                    "is_replacement": bool(r.is_replacement),
                    "order_id":       str(r.order_id) if r.order_id else None,
                    "attachments": [
                        {
                            "id":           str(a.id),
                            "filename":     a.filename,
                            "content_type": a.content_type,
                            "size_bytes":   a.size_bytes,
                            "uploaded_at":  a.uploaded_at.isoformat() if a.uploaded_at else None,
                            "uploaded_by":  a.uploaded_by,
                        }
                        for a in (r.attachments or [])
                    ],
                }
                for r in rows
            ]}


# ─── Inter-location transfers ───────────────────────────────────────

class TransferIn(BaseModel):
    lot_id:        str
    from_location: str
    to_location:   str
    doses:         DoseQty
    witness_user:  Optional[str] = None
    notes:         Optional[str] = None
    # Optional: if the courier is taking custody at create time, fill these
    # and the transfer skips 'packed' and goes straight to 'in_transit'.
    courier_user:  Optional[str] = None
    courier_notes: Optional[str] = None


@router.post("/transfers", status_code=201)
def create_transfer(payload: TransferIn,
                     db: Session = Depends(get_db),
                     current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.WORK))):
    if payload.from_location not in PELLET_LOCATIONS \
       or payload.to_location not in PELLET_LOCATIONS:
        raise HTTPException(status_code=422, detail="invalid location")
    if payload.from_location == payload.to_location:
        raise HTTPException(status_code=422, detail="from/to must differ")
    if payload.doses <= 0:
        raise HTTPException(status_code=422, detail="doses must be positive")

    l = (db.query(PelletLot).options(joinedload(PelletLot.dose_type))
           .filter(PelletLot.id == payload.lot_id).first())
    if not l:
        raise HTTPException(status_code=404, detail="lot not found")

    by = current_user.get("email") or "system"
    is_controlled = bool(l.dose_type and l.dose_type.is_controlled)
    witness = _validate_witness(db, payload.witness_user, by,
                                  controlled=is_controlled)

    src = _get_or_create_stock(db, l.id, payload.from_location)
    if src.doses_on_hand < payload.doses:
        raise HTTPException(status_code=409,
                            detail=f"Insufficient stock at {payload.from_location}: "
                                   f"have {src.doses_on_hand}, need {payload.doses}")
    _adjust_stock(db, src, -(payload.doses))

    # If a courier was provided at pack time, validate them and skip
    # straight to in_transit. For Sch III the courier must differ from the
    # packer (separate signatures on the chain of custody).
    courier = (payload.courier_user or "").strip() or None
    if courier and is_controlled and courier.lower() == by.lower():
        raise HTTPException(status_code=422,
                            detail="courier must be a different user than the packer (Schedule III)")

    t = PelletTransfer(
        lot_id=l.id,
        from_location=payload.from_location,
        to_location=payload.to_location,
        doses=payload.doses,
        sent_by=by,
        notes=payload.notes,
        courier_user=courier,
        courier_picked_up_at=(now_utc_naive() if courier else None),
        courier_notes=(payload.courier_notes or None) if courier else None,
        status=("in_transit" if courier else "packed"),
    )
    db.add(t); db.flush()

    _audit(db, actor=by, action="transfer_sent",
           lot_id=l.id, transfer_id=t.id, location=payload.from_location,
           delta_doses=-payload.doses,
           detail={"witness": witness,
                   "to": payload.to_location, "doses": payload.doses,
                   "courier_at_pack": courier},
           summary=(f"Transfer packed: {payload.doses} {l.dose_type.label if l.dose_type else ''} "
                    f"{payload.from_location} → {payload.to_location}"
                    + (f" (courier {courier})" if courier else "")))
    if courier:
        _audit(db, actor=by, action="transfer_picked_up",
                lot_id=l.id, transfer_id=t.id, location=payload.from_location,
                detail={"courier": courier, "at_pack": True},
                summary=f"Courier {courier} took custody at pack time")
    db.commit(); db.refresh(t)
    return {"transfer_id": str(t.id), "status": t.status}


class TransferPickupIn(BaseModel):
    courier_user:  str            # required
    courier_notes: Optional[str] = None


@router.post("/transfers/{transfer_id}/take-custody")
def take_custody(transfer_id: str, payload: TransferPickupIn,
                   db: Session = Depends(get_db),
                   current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.WORK))):
    """Courier signs in. Flips packed → in_transit. For Sch III the courier
    must be a different user than the packer (chain-of-custody separation)."""
    t = (db.query(PelletTransfer).options(joinedload(PelletTransfer.lot)
                                              .joinedload(PelletLot.dose_type))
           .filter(PelletTransfer.id == transfer_id).first())
    if not t:
        raise HTTPException(status_code=404, detail="transfer not found")
    if t.status != "packed":
        raise HTTPException(status_code=409,
                            detail=f"transfer is {t.status}; can only take custody of a 'packed' transfer")

    courier = (payload.courier_user or "").strip()
    if not courier:
        raise HTTPException(status_code=422, detail="courier_user is required")

    is_controlled = bool(t.lot and t.lot.dose_type and t.lot.dose_type.is_controlled)
    if is_controlled and courier.lower() == (t.sent_by or "").lower():
        raise HTTPException(status_code=422,
                            detail="courier must be a different user than the packer (Schedule III)")

    by = current_user.get("email") or "system"
    t.courier_user = courier
    t.courier_picked_up_at = now_utc_naive()
    t.courier_notes = (payload.courier_notes or None)
    t.status = "in_transit"

    _audit(db, actor=by, action="transfer_picked_up",
            lot_id=t.lot_id, transfer_id=t.id, location=t.from_location,
            detail={"courier": courier, "notes": payload.courier_notes},
            summary=f"Courier {courier} took custody — transfer {t.id} now in transit")
    db.commit()
    return {"ok": True, "status": t.status,
            "courier_picked_up_at": t.courier_picked_up_at.isoformat()}


class TransferReceiveIn(BaseModel):
    witness_user: Optional[str] = None


@router.post("/transfers/{transfer_id}/receive")
def receive_transfer(transfer_id: str, payload: TransferReceiveIn,
                       db: Session = Depends(get_db),
                       current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.WORK))):
    t = (db.query(PelletTransfer).options(joinedload(PelletTransfer.lot)
                                              .joinedload(PelletLot.dose_type))
           .filter(PelletTransfer.id == transfer_id).first())
    if not t:
        raise HTTPException(status_code=404, detail="transfer not found")
    is_controlled = bool(t.lot and t.lot.dose_type and t.lot.dose_type.is_controlled)
    # Sch III chain of custody: courier must have signed in before the
    # destination can receive. Non-controlled may receive directly from
    # 'packed' (no separate courier handoff required).
    if t.status not in ("packed", "in_transit"):
        raise HTTPException(status_code=409, detail=f"transfer is {t.status}")
    if is_controlled and t.status != "in_transit":
        raise HTTPException(status_code=409,
                            detail="Schedule III transfer cannot be received until a courier "
                                   "has signed for custody (use 'Take custody' first)")

    by = current_user.get("email") or "system"
    witness = _validate_witness(db, payload.witness_user, by,
                                  controlled=is_controlled)

    # Atomic claim — same pattern as verify_manifest. Two concurrent
    # /transfers/{id}/receive calls (double-click on the receive button)
    # would otherwise both pass the status check above and each add
    # t.doses to stock. The UPDATE matches a single source-of-truth row
    # transition; the second caller's rowcount is 0 → 409.
    # (Fable audit #2.)
    allowed_from = ["in_transit"] if is_controlled else ["packed", "in_transit"]
    now = now_utc_naive()
    claimed = db.execute(
        update(PelletTransfer)
          .where(PelletTransfer.id == t.id,
                 PelletTransfer.status.in_(allowed_from))
          .values(status="received", received_at=now, received_by=by)
    ).rowcount
    if not claimed:
        db.refresh(t)
        raise HTTPException(status_code=409,
                            detail=f"transfer is {t.status}")
    db.refresh(t)

    dest = _get_or_create_stock(db, t.lot_id, t.to_location)
    _adjust_stock(db, dest, t.doses)

    _audit(db, actor=by, action="transfer_received",
           lot_id=t.lot_id, transfer_id=t.id, location=t.to_location,
           delta_doses=t.doses,
           detail={"witness": witness},
           summary=f"Transfer received {t.doses} → {t.to_location}")
    db.commit()
    return {"ok": True, "received_at": t.received_at.isoformat()}


class TransferCancelIn(BaseModel):
    reason:       str   # short reason: lost | wrong_send | not_needed | other
    witness_user: Optional[str] = None    # required when lot is controlled
    notes:        Optional[str] = None


@router.post("/transfers/{transfer_id}/cancel")
def cancel_transfer(transfer_id: str, payload: TransferCancelIn,
                      db: Session = Depends(get_db),
                      current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.WORK))):
    """Cancel a packed or in-transit transfer and refund the source stock.

    The model has had a 'cancelled' status forever but no endpoint to set
    it; a transfer that never shipped used to strand doses in limbo
    forever, surfacing only as a count variance at month-end.
    (Fable audit #12.)
    """
    if not (payload.reason or "").strip():
        raise HTTPException(status_code=422, detail="reason is required")

    t = (db.query(PelletTransfer)
           .options(joinedload(PelletTransfer.lot).joinedload(PelletLot.dose_type))
           .filter(PelletTransfer.id == transfer_id).first())
    if not t:
        raise HTTPException(status_code=404, detail="transfer not found")
    if t.status not in ("packed", "in_transit"):
        raise HTTPException(status_code=409,
                            detail=f"transfer is {t.status}; only packed or in_transit "
                                   "can be cancelled")

    by = current_user.get("email") or "system"
    is_controlled = bool(t.lot and t.lot.dose_type and t.lot.dose_type.is_controlled)
    witness = _validate_witness(db, payload.witness_user, by,
                                  controlled=is_controlled)

    # Atomic claim — same pattern as receive_transfer. Two concurrent
    # cancels would otherwise both pass the status check and both refund
    # source stock, double-crediting the lot at the from_location.
    now = now_utc_naive()
    claimed = db.execute(
        update(PelletTransfer)
          .where(PelletTransfer.id == t.id,
                 PelletTransfer.status.in_(["packed", "in_transit"]))
          .values(status="cancelled",
                  cancelled_at=now,
                  cancelled_by=by)
    ).rowcount
    if not claimed:
        db.refresh(t)
        raise HTTPException(status_code=409,
                            detail=f"transfer is {t.status}")
    db.refresh(t)

    # Refund the source stock — the pack step debited it.
    src = _get_or_create_stock(db, t.lot_id, t.from_location)
    _adjust_stock(db, src, t.doses)

    _audit(db, actor=by, action="transfer_cancelled",
           lot_id=t.lot_id, transfer_id=t.id, location=t.from_location,
           delta_doses=t.doses,
           detail={"reason": payload.reason, "witness": witness,
                   "from": t.from_location, "to": t.to_location,
                   "doses": t.doses, "notes": payload.notes,
                   "controlled": is_controlled},
           summary=(f"Transfer cancelled: {t.doses} refunded to "
                    f"{t.from_location} (reason: {payload.reason})"))
    db.commit()
    return {"ok": True, "cancelled_at": now.isoformat(), "refunded_doses": t.doses}


@router.get("/transfers")
def list_transfers(
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.VIEW)),
    status: Optional[str] = None,
    page: int = 1, per_page: int = 50,
):
    q = (db.query(PelletTransfer)
           .options(joinedload(PelletTransfer.lot).joinedload(PelletLot.dose_type)))
    if status:
        q = q.filter(PelletTransfer.status == status)
    total = q.count()
    rows = (q.order_by(desc(PelletTransfer.sent_at))
              .offset((page - 1) * per_page).limit(per_page).all())
    return {"total": total, "page": page,
            "transfers": [
                {
                    "id": str(t.id),
                    "lot_id":       str(t.lot_id),
                    "lot_label":    t.lot.dose_type.label if t.lot and t.lot.dose_type else None,
                    "qualgen_lot":  t.lot.qualgen_lot_number if t.lot else None,
                    "from_location": t.from_location,
                    "to_location":   t.to_location,
                    "doses":         t.doses,
                    "status":        t.status,
                    "sent_at":       t.sent_at.isoformat() if t.sent_at else None,
                    "sent_by":       t.sent_by,
                    "received_at":   t.received_at.isoformat() if t.received_at else None,
                    "received_by":   t.received_by,
                }
                for t in rows
            ]}


# ─── Disposal ───────────────────────────────────────────────────────

class DisposalIn(BaseModel):
    lot_id:       str
    location:     str
    doses:        DoseQty
    reason:       str
    witness_user: Optional[str] = None
    notes:        Optional[str] = None


@router.post("/disposals", status_code=201)
def create_disposal(payload: DisposalIn,
                      db: Session = Depends(get_db),
                      current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.WORK))):
    if payload.reason not in DISPOSAL_REASONS:
        raise HTTPException(status_code=422,
                            detail=f"reason must be one of {DISPOSAL_REASONS}")
    if payload.reason == "other" and not (payload.notes or "").strip():
        raise HTTPException(status_code=422,
                            detail="notes required for reason='other'")
    if payload.doses <= 0:
        raise HTTPException(status_code=422, detail="doses must be positive")
    if payload.location not in PELLET_LOCATIONS:
        raise HTTPException(status_code=422, detail="invalid location")

    l = (db.query(PelletLot).options(joinedload(PelletLot.dose_type))
           .filter(PelletLot.id == payload.lot_id).first())
    if not l:
        raise HTTPException(status_code=404, detail="lot not found")

    by = current_user.get("email") or "system"
    is_controlled = bool(l.dose_type and l.dose_type.is_controlled)
    witness = _validate_witness(db, payload.witness_user, by,
                                  controlled=is_controlled)

    s = _get_or_create_stock(db, l.id, payload.location)
    if s.doses_on_hand < payload.doses:
        raise HTTPException(status_code=409,
                            detail=f"Insufficient stock at {payload.location}: "
                                   f"have {s.doses_on_hand}, need {payload.doses}")
    _adjust_stock(db, s, -(payload.doses))

    d = PelletDisposal(
        lot_id=l.id, location=payload.location, doses=payload.doses,
        reason=payload.reason, performed_by=by,
        witness_user=witness or None, notes=payload.notes,
    )
    db.add(d); db.flush()

    _audit(db, actor=by, action="disposal",
           lot_id=l.id, disposal_id=d.id, location=payload.location,
           delta_doses=-payload.doses,
           detail={"reason": payload.reason, "witness": witness,
                   "controlled": is_controlled},
           summary=f"Disposal: {payload.doses} {l.dose_type.label if l.dose_type else ''} "
                   f"({payload.reason}) at {payload.location}")
    db.commit(); db.refresh(d)
    return {"disposal_id": str(d.id)}


@router.get("/disposals")
def list_disposals(
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.VIEW)),
    location: Optional[str] = None,
    reason: Optional[str] = None,
    page: int = 1, per_page: int = 100,
):
    q = (db.query(PelletDisposal)
           .options(joinedload(PelletDisposal.lot).joinedload(PelletLot.dose_type)))
    if location:
        q = q.filter(PelletDisposal.location == location)
    if reason:
        q = q.filter(PelletDisposal.reason == reason)
    total = q.count()
    rows = (q.order_by(desc(PelletDisposal.occurred_at))
              .offset((page - 1) * per_page).limit(per_page).all())
    return {"total": total, "page": page,
            "disposals": [
                {
                    "id": str(d.id),
                    "lot_id":      str(d.lot_id),
                    "lot_label":   d.lot.dose_type.label if d.lot and d.lot.dose_type else None,
                    "qualgen_lot": d.lot.qualgen_lot_number if d.lot else None,
                    "location":    d.location,
                    "doses":       d.doses,
                    "reason":      d.reason,
                    "occurred_at": d.occurred_at.isoformat(),
                    "performed_by": d.performed_by,
                    "witness_user": d.witness_user,
                    "notes":        d.notes,
                }
                for d in rows
            ]}


# ─── Daily counts ───────────────────────────────────────────────────

class CountStartIn(BaseModel):
    location: str       # 'all' for headquarters (counts every location)
    notes:    Optional[str] = None
    scope:    str = "all"       # 'all' | 'controlled_only'
    witness_user: Optional[str] = None    # captured at start (required when scope sees Sch III)


def _unconfirmed_visits_blocking_count(db: Session, location: str) -> list[dict]:
    """Visits scheduled today or earlier whose dose card still has Proposed
    (planned/pulled) lines. Counts can't reconcile inventory if these visits
    haven't been finalized — stock thinks the doses are reserved but the
    provider may have inserted them already, or may not have.

    `location` may be a specific location or "all".
    """
    today = _date.today()
    proposed_statuses = ["planned", "pulled"]
    excluded_visit_statuses = ["cancelled", "billed"]

    q = (db.query(PelletVisit)
           .options(joinedload(PelletVisit.patient))
           .join(PelletVisitDose, PelletVisitDose.visit_id == PelletVisit.id)
           .filter(PelletVisit.scheduled_date.isnot(None),
                   PelletVisit.scheduled_date <= today,
                   PelletVisit.status.notin_(excluded_visit_statuses),
                   PelletVisit.is_historical.is_(False),
                   PelletVisitDose.status.in_(proposed_statuses)))
    if location != "all":
        q = q.filter(PelletVisit.location == location)
    rows = q.distinct().order_by(PelletVisit.scheduled_date).all()
    return [
        {
            "visit_id":       str(v.id),
            "patient_id":     str(v.patient_id) if v.patient_id else None,
            "patient_name":   v.patient.patient_name if v.patient else None,
            "chart_number":   v.patient.chart_number if v.patient else None,
            "scheduled_date": str(v.scheduled_date) if v.scheduled_date else None,
            "location":       v.location,
            "status":         v.status,
        }
        for v in rows
    ]


@router.post("/visits/run-stale-sweep")
def run_stale_visit_sweep(
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_super_admin()),
):
    """Manual trigger for the nightly stale-visit auto-cancel sweep.
    Primary runner is the pellet_stale_sweep Cloud Run Job (registered
    in app/jobs/run.py). Super-admin only. (Fable note 6.)"""
    from app.services.pellet.stale_sweep import sweep_stale_visits
    by = current_user.get("email") or "system"
    return sweep_stale_visits(db, actor=by)


@router.get("/counts/pre-check")
def counts_pre_check(
    location: str = "white_plains",
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.VIEW)),
):
    """Returns the list of visits whose Proposed insertions must be
    confirmed before a daily count can be started at `location`."""
    if location != "all" and location not in PELLET_LOCATIONS:
        raise HTTPException(status_code=422, detail="invalid location")
    blocking = _unconfirmed_visits_blocking_count(db, location)
    return {"location": location, "blocking_visits": blocking,
            "can_start": len(blocking) == 0}


@router.post("/counts/start", status_code=201)
def start_count(payload: CountStartIn,
                  db: Session = Depends(get_db),
                  current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.WORK))):
    location = payload.location
    if location != "all" and location not in PELLET_LOCATIONS:
        raise HTTPException(status_code=422, detail="invalid location")

    # Uniqueness: one non-cancelled count per (location, date). 'all' overlaps
    # every per-location count for the same day, and vice versa.
    today = _date.today()
    start_of_day = datetime(today.year, today.month, today.day)
    end_of_day = start_of_day + timedelta(days=1)
    if location == "all":
        existing_q = (db.query(PelletCount)
                        .filter(PelletCount.started_at >= start_of_day,
                                PelletCount.started_at < end_of_day,
                                PelletCount.status != "cancelled"))
    else:
        existing_q = (db.query(PelletCount)
                        .filter(PelletCount.started_at >= start_of_day,
                                PelletCount.started_at < end_of_day,
                                PelletCount.status != "cancelled",
                                PelletCount.location.in_([location, "all"])))
    existing = existing_q.first()
    if existing:
        raise HTTPException(
            status_code=409,
            detail={
                "message": (f"A count for {existing.location} is already "
                            f"{existing.status} today. Open or cancel that count "
                            f"before starting another."),
                "existing_count_id": str(existing.id),
                "existing_location": existing.location,
                "existing_status":   existing.status,
            })

    blocking = _unconfirmed_visits_blocking_count(db, location)
    if blocking:
        raise HTTPException(
            status_code=409,
            detail={
                "message": (f"Cannot start count: {len(blocking)} visit(s) at or before "
                            f"today still have proposed (unconfirmed) insertions. Confirm "
                            f"the dose card on each visit before starting the count."),
                "blocking_visits": blocking,
            })
    if payload.scope not in ("all", "controlled_only"):
        raise HTTPException(status_code=422,
                            detail="scope must be 'all' or 'controlled_only'")

    by = current_user.get("email") or "system"

    # Snapshot every active (lot × location) balance into PelletCountLine.
    # If scope='controlled_only', only include lots whose dose_type is controlled.
    stock_q = (db.query(PelletStock)
                  .join(PelletLot, PelletLot.id == PelletStock.lot_id)
                  .join(PelletDoseType, PelletDoseType.id == PelletLot.dose_type_id)
                  .filter(PelletStock.doses_on_hand > 0))
    if location != "all":
        stock_q = stock_q.filter(PelletStock.location == location)
    if payload.scope == "controlled_only":
        stock_q = stock_q.filter(PelletDoseType.is_controlled.is_(True))
    snapshot = stock_q.all()

    # Witness required at start when the count will see any Sch III lot.
    will_see_controlled = any(
        s.lot.dose_type.is_controlled for s in snapshot if s.lot and s.lot.dose_type
    )
    witness_start = _validate_witness(db, payload.witness_user, by,
                                        controlled=will_see_controlled) or None

    c = PelletCount(location=location, started_by=by, notes=payload.notes,
                    scope=payload.scope, witness_user_start=witness_start)
    db.add(c)
    try:
        db.flush()
    except Exception as exc:
        # ix_pellet_counts_one_per_day fired: a concurrent start_count won
        # the race. Surface a clean 409 instead of a 500. (Fable audit #10.)
        from sqlalchemy.exc import IntegrityError
        db.rollback()
        if isinstance(exc, IntegrityError):
            raise HTTPException(status_code=409,
                                detail="another count for this location/day was started concurrently")
        raise

    for s in snapshot:
        db.add(PelletCountLine(
            count_id=c.id, lot_id=s.lot_id,
            location=s.location,
            expected_doses=s.doses_on_hand,
        ))

    _audit(db, actor=by, action="count_started",
           count_id=c.id, location=location,
           summary=(f"Daily count started at {location} "
                    f"({payload.scope}, {len(snapshot)} lots snapshot"
                    + (f", witness {witness_start}" if witness_start else "")
                    + ")"))
    db.commit(); db.refresh(c)
    return {"count_id": str(c.id), "lines_snapshot": len(snapshot),
            "scope": c.scope, "witness_user_start": c.witness_user_start}


class CountScanIn(BaseModel):
    lot_id:        str
    counted_doses: CountQty
    # Required when the count's location is "all" AND the scanned lot
    # has stock at more than one location. Ignored when c.location is
    # a specific site (we just use c.location).
    location:      Optional[str] = None
    notes:         Optional[str] = None


@router.post("/counts/{count_id}/scan")
def record_count_scan(count_id: str, payload: CountScanIn,
                        db: Session = Depends(get_db),
                        current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.WORK))):
    c = db.query(PelletCount).filter(PelletCount.id == count_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="count not found")
    if c.status != "in_progress":
        raise HTTPException(status_code=409, detail=f"count is {c.status}")

    # Resolve which location this scan reconciles. For a site-specific
    # count, the location is the count's location. For an "all" count,
    # the client must say which location they're scanning at — multiple
    # lines for the same lot can exist (one per location) and we have
    # to know which one to update or reject.
    if c.location != "all":
        target_location = c.location
        if payload.location and payload.location != c.location:
            raise HTTPException(status_code=422,
                detail=f"this count is scoped to {c.location}; "
                       "do not send a different location")
    else:
        if not (payload.location or "").strip():
            raise HTTPException(status_code=422,
                detail="location is required for an 'all'-scope count")
        if payload.location not in PELLET_LOCATIONS:
            raise HTTPException(status_code=422, detail="invalid location")
        target_location = payload.location

    line = (db.query(PelletCountLine)
              .filter(PelletCountLine.count_id == c.id,
                      PelletCountLine.lot_id == payload.lot_id,
                      PelletCountLine.location == target_location).first())
    if not line:
        # Backfill compatibility: legacy lines created before the
        # location column existed are nullable. Match by lot only if
        # no location-keyed line exists and the lot has at most one
        # legacy row in this count.
        legacy = (db.query(PelletCountLine)
                    .filter(PelletCountLine.count_id == c.id,
                            PelletCountLine.lot_id == payload.lot_id,
                            PelletCountLine.location.is_(None)).all())
        if len(legacy) == 1:
            line = legacy[0]
            line.location = target_location
        elif len(legacy) > 1:
            raise HTTPException(status_code=409,
                detail="ambiguous legacy count line — start a fresh count")

    if not line:
        # Unexpected scan — add it as a discrepancy line, with location.
        line = PelletCountLine(count_id=c.id, lot_id=payload.lot_id,
                                location=target_location,
                                expected_doses=0)
        db.add(line); db.flush()
    by = current_user.get("email") or "system"
    # Capture the previous values so a re-scan emits a before/after
    # audit row — DEA expects an immutable count record. (Fable audit #5.)
    prev_counted = line.counted_doses
    prev_by = line.counted_by
    prev_at = line.counted_at
    prev_notes = line.notes

    line.counted_doses = payload.counted_doses
    line.counted_at = now_utc_naive()
    line.counted_by = by
    line.notes = payload.notes

    if prev_counted is not None and (
            prev_counted != line.counted_doses
            or (prev_by or "") != (line.counted_by or "")
            or (prev_notes or "") != (line.notes or "")):
        _audit(db, actor=by, action="count_line_rescanned",
               lot_id=line.lot_id, count_id=c.id, location=target_location,
               detail={
                   "before": {
                       "counted_doses": prev_counted,
                       "counted_by": prev_by,
                       "counted_at": prev_at.isoformat() if prev_at else None,
                       "notes": prev_notes,
                   },
                   "after": {
                       "counted_doses": line.counted_doses,
                       "counted_by": line.counted_by,
                       "counted_at": line.counted_at.isoformat(),
                       "notes": line.notes,
                   },
                   "expected_doses": line.expected_doses,
               },
               summary=(f"Count line rescanned for lot {line.lot_id} at "
                        f"{target_location}: {prev_counted} → "
                        f"{line.counted_doses}"))
    db.commit(); db.refresh(line)
    variance = (line.counted_doses or 0) - (line.expected_doses or 0)
    return {"line_id": str(line.id), "variance": variance}


class CountFinishIn(BaseModel):
    witness_user: Optional[str] = None
    notes:        Optional[str] = None


@router.post("/counts/{count_id}/finish")
def finish_count(count_id: str, payload: CountFinishIn,
                   db: Session = Depends(get_db),
                   current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.WORK))):
    c = (db.query(PelletCount).options(joinedload(PelletCount.lines)
                                            .joinedload(PelletCountLine.lot)
                                            .joinedload(PelletLot.dose_type))
           .filter(PelletCount.id == count_id).first())
    if not c:
        raise HTTPException(status_code=404, detail="count not found")
    if c.status != "in_progress":
        raise HTTPException(status_code=409, detail=f"count is {c.status}")

    by = current_user.get("email") or "system"

    # Witness required if any controlled stock was in the count
    has_controlled = any(
        l.lot and l.lot.dose_type and l.lot.dose_type.is_controlled
        for l in (c.lines or [])
    )
    witness = _validate_witness(db, payload.witness_user, by,
                                  controlled=has_controlled) or None

    # Reconcile: any variance with notes is acceptable. If variance != 0
    # AND no notes, block finish so the user has to justify.
    open_variances = []
    for l in (c.lines or []):
        if l.counted_doses is None:
            open_variances.append((l, "not yet counted"))
            continue
        v = (l.counted_doses or 0) - (l.expected_doses or 0)
        if v != 0 and not (l.notes or "").strip():
            open_variances.append((l, f"variance={v} needs notes"))
    if open_variances:
        raise HTTPException(
            status_code=409,
            detail={"unresolved_lines": [
                {"lot_id": str(l.lot_id), "issue": issue}
                for (l, issue) in open_variances
            ]},
        )

    # Apply variances: adjust PelletStock to match counted_doses.
    # Each count line carries the explicit (lot_id, location) tuple it
    # was snapshot against (Fable audit #3). Legacy lines created
    # before the column existed have location=None — fall back to the
    # count's location when it's site-scoped, and skip them when the
    # count was "all" (we can't safely guess which site they meant).
    for l in (c.lines or []):
        line_location = l.location or (c.location if c.location != "all" else None)
        if not line_location:
            # Legacy "all"-count line with no location stored — skip
            # rather than risk overwriting the wrong stock row.
            _audit(db, actor=by, action="count_line_skipped_legacy",
                   lot_id=l.lot_id, count_id=c.id,
                   summary=(f"Skipped legacy count line for lot {l.lot_id} "
                            "— no location recorded; manual reconciliation needed"))
            continue

        s = (db.query(PelletStock)
               .filter(PelletStock.lot_id == l.lot_id,
                       PelletStock.location == line_location).first())
        if s is None:
            continue
        delta = (l.counted_doses or 0) - s.doses_on_hand
        if delta != 0:
            _adjust_stock(db, s, delta)
            _audit(db, actor=by, action="stock_adjusted",
                   lot_id=l.lot_id, count_id=c.id, location=line_location,
                   delta_doses=delta,
                   detail={"reason": l.notes or "count reconciliation",
                           "expected": l.expected_doses, "counted": l.counted_doses},
                   summary=f"Count reconciliation: {delta:+d} doses on lot {l.lot_id}")

    c.status = "finished"
    c.finished_at = now_utc_naive()
    c.finished_by = by
    c.witness_user = witness
    if payload.notes:
        c.notes = ((c.notes or "") + "\n" + payload.notes).strip()

    _audit(db, actor=by, action="count_finished",
           count_id=c.id, location=c.location,
           detail={"witness": witness,
                   "lines": len(c.lines or [])},
           summary=f"Count finished at {c.location}")
    db.commit()

    # Generate the count PDF (best-effort — failure here doesn't unfinish
    # the count). Stored as a PelletCountAttachment.
    pdf_id = None
    try:
        from app.services.pellet.count_pdf import generate_count_pdf
        body, fname = generate_count_pdf(db, c)
        key = save_blob(prefix="pellet-attachments", body=body, filename=fname)
        att = PelletCountAttachment(
            count_id=c.id, filename=fname, storage_path=key,
            size_bytes=len(body), generated_by=by,
        )
        db.add(att); db.commit(); db.refresh(att)
        pdf_id = str(att.id)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning(
            "pellet count PDF generation failed for count %s: %s", c.id, exc)

    return {"ok": True, "finished_at": c.finished_at.isoformat(),
            "pdf_attachment_id": pdf_id}


@router.post("/counts/{count_id}/cancel")
def cancel_count(count_id: str,
                   db: Session = Depends(get_db),
                   current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.WORK))):
    """Cancel an in-progress count. No stock changes happen until finish, so
    cancellation is safe. The count_started audit row is preserved; a
    count_cancelled row is appended."""
    c = db.query(PelletCount).filter(PelletCount.id == count_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="count not found")
    if c.status != "in_progress":
        raise HTTPException(status_code=409,
                            detail=f"only in-progress counts can be cancelled (this one is {c.status})")
    by = current_user.get("email") or "system"
    c.status = "cancelled"
    c.finished_at = now_utc_naive()
    c.finished_by = by
    _audit(db, actor=by, action="count_cancelled",
            count_id=c.id, location=c.location,
            summary=f"Cancelled in-progress count at {c.location}")
    db.commit()
    return {"ok": True}


@router.post("/counts/{count_id}/regenerate-pdf", status_code=201)
def regenerate_count_pdf(count_id: str,
                            db: Session = Depends(get_db),
                            current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.MANAGE))):
    """Re-render the count PDF (e.g. after a signature/notes correction).
    Appends a new attachment row — history of prior PDFs is preserved."""
    c = (db.query(PelletCount).options(joinedload(PelletCount.lines)
                                           .joinedload(PelletCountLine.lot)
                                           .joinedload(PelletLot.dose_type))
           .filter(PelletCount.id == count_id).first())
    if not c:
        raise HTTPException(status_code=404, detail="count not found")
    if c.status != "finished":
        raise HTTPException(status_code=409,
                            detail="PDF is only available for finished counts")
    from app.services.pellet.count_pdf import generate_count_pdf
    by = current_user.get("email") or "system"
    body, fname = generate_count_pdf(db, c)
    key = save_blob(prefix="pellet-attachments", body=body, filename=fname)
    att = PelletCountAttachment(
        count_id=c.id, filename=fname, storage_path=key,
        size_bytes=len(body), generated_by=by,
    )
    db.add(att); db.commit(); db.refresh(att)
    return {
        "id": str(att.id),
        "filename": att.filename,
        "size_bytes": att.size_bytes,
        "generated_at": att.generated_at.isoformat(),
    }


@router.get("/counts/{count_id}/pdf")
def download_count_pdf(count_id: str,
                          db: Session = Depends(get_db),
                          current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.VIEW))):
    """Download the most-recent PDF attachment for this count."""
    att = (db.query(PelletCountAttachment)
             .filter(PelletCountAttachment.count_id == count_id)
             .order_by(desc(PelletCountAttachment.generated_at)).first())
    if not att:
        raise HTTPException(status_code=404, detail="no PDF generated for this count")
    if is_legacy_local_path(att.storage_path):
        raise HTTPException(status_code=410,
                              detail="This file is from before the cloud migration and is no longer available.")
    by = current_user.get("email") or "system"
    _audit(db, actor=by, action="count_pdf_downloaded",
           count_id=count_id,
           detail={"attachment_id": str(att.id), "filename": att.filename},
           summary=f"Downloaded count PDF {att.filename}")
    db.commit()
    return serve_blob(
        local_path=None,
        gcs_object=att.storage_path,
        media_type="application/pdf",
        filename=att.filename,
        disposition="attachment",
    )


@router.get("/counts/{count_id}")
def get_count(count_id: str,
                db: Session = Depends(get_db),
                current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.VIEW))):
    c = (db.query(PelletCount).options(joinedload(PelletCount.lines)
                                            .joinedload(PelletCountLine.lot)
                                            .joinedload(PelletLot.dose_type),
                                       joinedload(PelletCount.attachments))
           .filter(PelletCount.id == count_id).first())
    if not c:
        raise HTTPException(status_code=404, detail="count not found")
    return {
        "id": str(c.id),
        "location":    c.location,
        "status":      c.status,
        "scope":       c.scope,
        "started_at":  c.started_at.isoformat(),
        "started_by":  c.started_by,
        "witness_user_start": c.witness_user_start,
        "finished_at": c.finished_at.isoformat() if c.finished_at else None,
        "finished_by": c.finished_by,
        "witness_user": c.witness_user,
        "notes":       c.notes,
        "attachments": [
            {
                "id":           str(a.id),
                "filename":     a.filename,
                "size_bytes":   a.size_bytes,
                "generated_at": a.generated_at.isoformat() if a.generated_at else None,
                "generated_by": a.generated_by,
            }
            for a in (c.attachments or [])
        ],
        "lines": [
            {
                "id":              str(l.id),
                "lot_id":          str(l.lot_id),
                "lot_label":       l.lot.dose_type.label if l.lot and l.lot.dose_type else None,
                "qualgen_lot":     l.lot.qualgen_lot_number if l.lot else None,
                "expiration_date": str(l.lot.expiration_date) if l.lot else None,
                "is_controlled":   bool(l.lot.dose_type.is_controlled)
                                    if l.lot and l.lot.dose_type else False,
                "expected_doses":  l.expected_doses,
                "counted_doses":   l.counted_doses,
                "variance":        ((l.counted_doses or 0) - (l.expected_doses or 0))
                                    if l.counted_doses is not None else None,
                "counted_at":      l.counted_at.isoformat() if l.counted_at else None,
                "counted_by":      l.counted_by,
                "notes":           l.notes,
            }
            for l in (c.lines or [])
        ],
    }


@router.get("/counts")
def list_counts(
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.VIEW)),
    status: Optional[str] = None,
    include_cancelled: bool = False,
    page: int = 1, per_page: int = 50,
):
    q = db.query(PelletCount).options(joinedload(PelletCount.attachments))
    if status:
        q = q.filter(PelletCount.status == status)
    elif not include_cancelled:
        q = q.filter(PelletCount.status != "cancelled")
    total = q.count()
    rows = (q.order_by(desc(PelletCount.started_at))
              .offset((page - 1) * per_page).limit(per_page).all())
    return {"total": total, "page": page,
            "counts": [
                {
                    "id":          str(c.id),
                    "location":    c.location,
                    "status":      c.status,
                    "scope":       c.scope,
                    "started_at":  c.started_at.isoformat(),
                    "started_by":  c.started_by,
                    "witness_user_start": c.witness_user_start,
                    "finished_at": c.finished_at.isoformat() if c.finished_at else None,
                    "finished_by": c.finished_by,
                    "witness_user": c.witness_user,
                    "has_pdf":     len(c.attachments or []) > 0,
                    "latest_pdf_at": (c.attachments[0].generated_at.isoformat()
                                        if c.attachments else None),
                }
                for c in rows
            ]}


# ─── Audit log ──────────────────────────────────────────────────────

@router.get("/audit")
def list_audit(
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.VIEW)),
    action: Optional[str] = None,
    lot_id: Optional[str] = None,
    actor: Optional[str] = None,
    location: Optional[str] = None,
    days: int = 30,
    page: int = 1, per_page: int = 200,
):
    cutoff = now_utc_naive() - timedelta(days=days)
    q = db.query(PelletAuditEvent).filter(PelletAuditEvent.at >= cutoff)
    if action:
        q = q.filter(PelletAuditEvent.action == action)
    if lot_id:
        q = q.filter(PelletAuditEvent.lot_id == lot_id)
    if actor:
        q = q.filter(PelletAuditEvent.actor.ilike(f"%{actor}%"))
    if location:
        q = q.filter(PelletAuditEvent.location == location)
    total = q.count()
    rows = (q.order_by(desc(PelletAuditEvent.at))
              .offset((page - 1) * per_page).limit(per_page).all())
    return {"total": total, "page": page,
            "events": [
                {
                    "id":        str(e.id),
                    "at":        e.at.isoformat(),
                    "actor":     e.actor,
                    "action":    e.action,
                    "lot_id":    str(e.lot_id) if e.lot_id else None,
                    "location":  e.location,
                    "delta_doses": e.delta_doses,
                    "summary":   e.summary,
                    "detail":    e.detail,
                }
                for e in rows
            ]}


# ─── Manual ─────────────────────────────────────────────────────────

@router.get("/manual")
def list_manual(db: Session = Depends(get_db),
                  current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.VIEW))):
    rows = (db.query(PelletManualSection)
              .order_by(PelletManualSection.sort_order,
                        PelletManualSection.title).all())
    return [
        {
            "id":         str(s.id),
            "slug":       s.slug,
            "title":      s.title,
            "sort_order": s.sort_order,
            "body_md":    s.body_md,
            "updated_by": s.updated_by,
            "updated_at": s.updated_at.isoformat() if s.updated_at else None,
        }
        for s in rows
    ]


class ManualPatch(BaseModel):
    title:      Optional[str] = None
    body_md:    Optional[str] = None
    sort_order: Optional[int] = None


@router.patch("/manual/{section_id}")
def patch_manual(section_id: str, payload: ManualPatch,
                   db: Session = Depends(get_db),
                   current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.MANAGE))):
    s = db.query(PelletManualSection).filter(PelletManualSection.id == section_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="section not found")
    # Capture before-state for audit (Fable audit #17). Manual edits
    # affect SOP documentation referenced by DEA-relevant procedures, so
    # mutate-without-audit was inconsistent with the module's posture.
    before = {"title": s.title, "sort_order": s.sort_order,
              "body_md_len": len(s.body_md or "")}
    data = payload.model_dump(exclude_unset=True)
    changed = []
    for k, v in data.items():
        if getattr(s, k, None) != v:
            changed.append(k)
            setattr(s, k, v)
    by = current_user.get("email") or "system"
    s.updated_by = by
    if changed:
        _audit(db, actor=by, action="manual_section_edited",
               detail={"section_id": str(s.id), "slug": s.slug,
                       "fields_changed": changed, "before": before,
                       "new_body_len": len(s.body_md or "")},
               summary=f"Edited manual section {s.slug!r}: {', '.join(changed)}")
    db.commit(); db.refresh(s)
    return {"id": str(s.id), "slug": s.slug, "title": s.title,
            "sort_order": s.sort_order, "body_md": s.body_md,
            "updated_by": s.updated_by, "updated_at": s.updated_at.isoformat()}


class ManualIn(BaseModel):
    slug:       str
    title:      str
    body_md:    str = ""
    sort_order: int = 1000


@router.post("/manual", status_code=201)
def create_manual_section(payload: ManualIn,
                            db: Session = Depends(get_db),
                            current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.MANAGE))):
    slug = payload.slug.strip().lower()
    title = payload.title.strip()
    if not slug or not title:
        raise HTTPException(status_code=422, detail="slug and title required")
    existing = (db.query(PelletManualSection)
                  .filter(PelletManualSection.slug == slug).first())
    if existing:
        raise HTTPException(status_code=409, detail=f"slug {slug!r} already exists")
    s = PelletManualSection(
        slug=slug, title=title, body_md=payload.body_md,
        sort_order=payload.sort_order,
        updated_by=current_user.get("email") or "system",
    )
    db.add(s); db.commit(); db.refresh(s)
    return {"id": str(s.id), "slug": s.slug, "title": s.title,
            "sort_order": s.sort_order, "body_md": s.body_md,
            "updated_by": s.updated_by, "updated_at": s.updated_at.isoformat()}


@router.delete("/manual/{section_id}", status_code=204)
def delete_manual_section(section_id: str,
                            db: Session = Depends(get_db),
                            current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.MANAGE))):
    s = db.query(PelletManualSection).filter(PelletManualSection.id == section_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="section not found")
    db.delete(s); db.commit()
    return


# ─── Filter presets (per-user saved searches) ───────────────────────

class FilterPresetIn(BaseModel):
    name:         str
    filters_json: dict
    is_default:   bool = False


def _preset_dict(p: PelletFilterPreset) -> dict:
    return {
        "id":           str(p.id),
        "name":         p.name,
        "filters_json": p.filters_json or {},
        "is_default":   bool(p.is_default),
        "created_at":   p.created_at.isoformat() if p.created_at else None,
        "updated_at":   p.updated_at.isoformat() if p.updated_at else None,
    }


def _clear_other_pellet_defaults(db: Session, owner: str, keep_id) -> None:
    (db.query(PelletFilterPreset)
       .filter(PelletFilterPreset.owner_email == owner,
               PelletFilterPreset.id != keep_id,
               PelletFilterPreset.is_default.is_(True))
       .update({"is_default": False}, synchronize_session=False))


@router.get("/filter-presets")
def list_presets(db: Session = Depends(get_db),
                   current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.VIEW))):
    email = (current_user.get("email") or "").lower()
    rows = (db.query(PelletFilterPreset)
              .filter(PelletFilterPreset.owner_email == email)
              .order_by(PelletFilterPreset.name).all())
    return [_preset_dict(p) for p in rows]


@router.post("/filter-presets")
def create_or_upsert_preset(payload: FilterPresetIn,
                              db: Session = Depends(get_db),
                              current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.VIEW))):
    email = (current_user.get("email") or "").lower()
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="name is required")
    existing = (db.query(PelletFilterPreset)
                  .filter(PelletFilterPreset.owner_email == email,
                          PelletFilterPreset.name == name).first())
    if existing:
        existing.filters_json = payload.filters_json or {}
        existing.is_default   = bool(payload.is_default)
        existing.updated_at   = now_utc_naive()
        if existing.is_default:
            _clear_other_pellet_defaults(db, email, existing.id)
        db.commit(); db.refresh(existing)
        return _preset_dict(existing)
    row = PelletFilterPreset(
        owner_email=email, name=name,
        filters_json=payload.filters_json or {},
        is_default=bool(payload.is_default),
    )
    db.add(row); db.flush()
    if row.is_default:
        _clear_other_pellet_defaults(db, email, row.id)
    db.commit(); db.refresh(row)
    return _preset_dict(row)


@router.put("/filter-presets/{preset_id}")
def update_preset(preset_id: str, payload: FilterPresetIn,
                    db: Session = Depends(get_db),
                    current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.VIEW))):
    email = (current_user.get("email") or "").lower()
    row = (db.query(PelletFilterPreset)
             .filter(PelletFilterPreset.id == preset_id,
                     PelletFilterPreset.owner_email == email).first())
    if not row:
        raise HTTPException(status_code=404, detail="preset not found")
    row.name         = payload.name.strip() or row.name
    row.filters_json = payload.filters_json or {}
    row.is_default   = bool(payload.is_default)
    row.updated_at   = now_utc_naive()
    if row.is_default:
        _clear_other_pellet_defaults(db, email, row.id)
    db.commit(); db.refresh(row)
    return _preset_dict(row)


@router.delete("/filter-presets/{preset_id}")
def delete_preset(preset_id: str,
                    db: Session = Depends(get_db),
                    current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.VIEW))):
    email = (current_user.get("email") or "").lower()
    row = (db.query(PelletFilterPreset)
             .filter(PelletFilterPreset.id == preset_id,
                     PelletFilterPreset.owner_email == email).first())
    if not row:
        raise HTTPException(status_code=404, detail="preset not found")
    db.delete(row); db.commit()
    return {"ok": True}


# ─── Mammogram facility catalog ─────────────────────────────────────

def _mammo_fac_dict(f: PelletMammoFacility) -> dict:
    return {
        "id":         str(f.id),
        "name":       f.name,
        "phone":      f.phone,
        "fax":        f.fax,
        "address":    f.address,
        "notes":      f.notes,
        "is_active":  bool(f.is_active),
        "sort_order": f.sort_order,
    }


@router.get("/mammo-facilities")
def list_mammo_facilities(
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.VIEW)),
    active_only: bool = True,
):
    q = db.query(PelletMammoFacility)
    if active_only:
        q = q.filter(PelletMammoFacility.is_active.is_(True))
    rows = q.order_by(PelletMammoFacility.sort_order,
                       PelletMammoFacility.name).all()
    return [_mammo_fac_dict(f) for f in rows]


class MammoFacilityIn(BaseModel):
    name:       str
    phone:      Optional[str] = None
    fax:        Optional[str] = None
    address:    Optional[str] = None
    notes:      Optional[str] = None
    is_active:  bool = True
    sort_order: int = 100


@router.post("/mammo-facilities", status_code=201)
def create_mammo_facility(payload: MammoFacilityIn,
                            db: Session = Depends(get_db),
                            current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.MANAGE))):
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="name required")
    if db.query(PelletMammoFacility).filter(PelletMammoFacility.name == name).first():
        raise HTTPException(status_code=409, detail=f"{name!r} already exists")
    f = PelletMammoFacility(
        name=name, phone=payload.phone, fax=payload.fax,
        address=payload.address, notes=payload.notes,
        is_active=payload.is_active, sort_order=payload.sort_order,
    )
    db.add(f); db.commit(); db.refresh(f)
    return _mammo_fac_dict(f)


class MammoFacilityPatch(BaseModel):
    name:       Optional[str] = None
    phone:      Optional[str] = None
    fax:        Optional[str] = None
    address:    Optional[str] = None
    notes:      Optional[str] = None
    is_active:  Optional[bool] = None
    sort_order: Optional[int] = None


@router.patch("/mammo-facilities/{facility_id}")
def patch_mammo_facility(facility_id: str, payload: MammoFacilityPatch,
                          db: Session = Depends(get_db),
                          current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.MANAGE))):
    f = db.query(PelletMammoFacility).filter(PelletMammoFacility.id == facility_id).first()
    if not f:
        raise HTTPException(status_code=404, detail="facility not found")
    data = payload.model_dump(exclude_unset=True)
    before = {k: getattr(f, k, None) for k in data.keys()}
    changed = []
    for k, v in data.items():
        if before.get(k) != v:
            changed.append(k)
            setattr(f, k, v)
    by = current_user.get("email") or "system"
    if changed:
        # Fable audit #17: mutate-without-audit was inconsistent with the
        # module's posture. Mammo facility records drive recall faxes.
        _audit(db, actor=by, action="mammo_facility_edited",
               detail={"facility_id": str(f.id), "fields_changed": changed,
                       "before": before},
               summary=f"Edited mammo facility {f.name!r}: {', '.join(changed)}")
    db.commit(); db.refresh(f)
    return _mammo_fac_dict(f)


@router.delete("/mammo-facilities/{facility_id}", status_code=204)
def delete_mammo_facility(facility_id: str,
                            db: Session = Depends(get_db),
                            current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.MANAGE))):
    f = db.query(PelletMammoFacility).filter(PelletMammoFacility.id == facility_id).first()
    if not f:
        raise HTTPException(status_code=404, detail="facility not found")
    by = current_user.get("email") or "system"
    _audit(db, actor=by, action="mammo_facility_deleted",
           detail={"facility_id": str(f.id), "name": f.name,
                   "address": getattr(f, "address", None)},
           summary=f"Deleted mammo facility {f.name!r}")
    db.delete(f); db.commit()
    return


# ─── Dose suggestion / prior dose ────────────────────────────────

@router.get("/patients/{patient_id}/prior-dose")
def get_prior_dose(patient_id: str,
                     db: Session = Depends(get_db),
                     current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.VIEW))):
    """Return totals per hormone from the patient's most-recent visit
    where doses were actually inserted (used by the Set Dose flow as the
    'carry forward' default for established patients)."""
    p = (db.query(PelletPatient)
           .options(joinedload(PelletPatient.visits)
                       .joinedload(PelletVisit.doses)
                       .joinedload(PelletVisitDose.dose_type))
           .filter(PelletPatient.id == patient_id).first())
    if not p:
        raise HTTPException(status_code=404, detail="patient not found")

    # Find the most recent visit with at least one inserted/added dose
    candidates = sorted(
        [v for v in (p.visits or [])
         if any(d.status in ("inserted", "added") for d in (v.doses or []))],
        key=lambda v: v.inserted_at or v.scheduled_date or v.created_at or '',
        reverse=True,
    )
    if not candidates:
        # Fall back to most recent visit with planned doses
        candidates = sorted(
            [v for v in (p.visits or []) if v.doses],
            key=lambda v: v.created_at or '',
            reverse=True,
        )
    if not candidates:
        return {"visit_id": None, "inserted_at": None,
                "estradiol_mg": 0, "testosterone_mg": 0,
                "components": []}

    v = candidates[0]
    used = [d for d in v.doses
            if d.status in ("inserted", "added", "planned", "pulled")]
    estradiol_mg = sum(
        float(d.dose_type.dose_mg) * d.quantity
        for d in used if d.dose_type and d.dose_type.hormone == "estradiol"
    )
    testosterone_mg = sum(
        float(d.dose_type.dose_mg) * d.quantity
        for d in used if d.dose_type and d.dose_type.hormone == "testosterone"
    )
    return {
        "visit_id":        str(v.id),
        "visit_kind":      v.visit_kind,
        "inserted_at":     v.inserted_at.isoformat() if v.inserted_at else None,
        "scheduled_date":  str(v.scheduled_date) if v.scheduled_date else None,
        "estradiol_mg":    estradiol_mg,
        "testosterone_mg": testosterone_mg,
        "components": [
            {
                "dose_type_id": str(d.dose_type_id),
                "label":        d.dose_type.label if d.dose_type else None,
                "hormone":      d.dose_type.hormone if d.dose_type else None,
                "dose_mg":      float(d.dose_type.dose_mg) if d.dose_type else None,
                "count":        d.quantity,
                "status":       d.status,
            }
            for d in used
        ],
    }


class DoseSuggestIn(BaseModel):
    estradiol_mg:    float = 0
    testosterone_mg: float = 0
    location:        str = "white_plains"


@router.post("/dosing/suggest")
def suggest_dose(payload: DoseSuggestIn,
                   db: Session = Depends(get_db),
                   current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.VIEW))):
    """Return ranked dose combinations for each hormone that sum to the
    requested total mg — prefers in-stock first, then fewest pellets."""
    if payload.location not in PELLET_LOCATIONS:
        raise HTTPException(status_code=422, detail="invalid location")
    return dose_suggest.suggest(
        db,
        estradiol_mg=payload.estradiol_mg,
        testosterone_mg=payload.testosterone_mg,
        location=payload.location,
    )


# ─── Pellet config (KV settings) ────────────────────────────────────

class PelletConfigPayload(BaseModel):
    stale_visit_days:         Optional[int] = Field(default=None, ge=1, le=365)
    dose_suggest_max_pellets: Optional[int] = Field(default=None, ge=1, le=50)
    dose_suggest_max_results: Optional[int] = Field(default=None, ge=1, le=50)
    labs_valid_days:          Optional[int] = Field(default=None, ge=1, le=3650)
    mammo_valid_days:         Optional[int] = Field(default=None, ge=1, le=3650)
    require_mammo:            Optional[bool] = None
    require_labs:             Optional[bool] = None
    require_consent:          Optional[bool] = None
    consent_template_id:      Optional[str] = None


@router.get("/config")
def get_pellet_config(db: Session = Depends(get_db),
                       current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.VIEW))):
    out = dict(PELLET_SETTINGS_DEFAULTS)
    for r in db.query(PelletConfig).all():
        out[r.key] = r.value
    return out


@router.put("/config")
def put_pellet_config(payload: PelletConfigPayload,
                       db: Session = Depends(get_db),
                       current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.MANAGE))):
    actor = current_user.get("email") or "system"
    data = payload.model_dump(exclude_unset=True, mode="json")
    for k, v in data.items():
        if k not in PELLET_SETTINGS_DEFAULTS:
            continue
        row = db.query(PelletConfig).filter(PelletConfig.key == k).first()
        if row is None:
            db.add(PelletConfig(key=k, value=v, updated_by=actor))
        else:
            row.value = v
            row.updated_by = actor
    db.commit()
    return get_pellet_config(db, current_user)   # echo merged config


# ─── ModMed appointment upload ──────────────────────────────────────

@router.post("/appointments/upload", status_code=201)
async def upload_appointments(
    file: UploadFile = File(...),
    cancel_missing: bool = Form(False),
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.WORK)),
):
    """Upload a ModMed "Pellet Insert" appointment list. Upserts each
    row keyed on (MRN, appt date). Optionally cancels any in-range
    in_progress visit not present in the upload."""
    # cancel_missing is a mass-mutation switch: it auto-cancels every
    # in-progress visit in the date range that's absent from the file.
    # A wrong or partial upload at WORK tier would bulk-cancel real
    # visits. Gate the destructive flag at MANAGE. (Fable audit #14.)
    if cancel_missing:
        from app.permissions.resolver import effective_tier
        actor_email = (current_user.get("email") or "").lower().strip()
        if effective_tier(db, actor_email, Module.PELLETS) < Tier.MANAGE:
            raise HTTPException(
                status_code=403,
                detail="cancel_missing requires Tier.MANAGE on the Pellets module")

    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=422, detail="empty file")
    if len(contents) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="file >25MB; split it")

    try:
        rows = appt_import.parse_excel(contents)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Could not parse Excel: {e}")

    actor = current_user.get("email") or "system"
    report = appt_import.import_appointments(
        db, rows, actor=actor, cancel_missing=bool(cancel_missing),
    )
    db.commit()
    return report


# ─── Patients ───────────────────────────────────────────────────────

PELLET_ACTIVE_MONTHS_KEY = "pellet_active_months_cutoff"
DEFAULT_PELLET_ACTIVE_MONTHS = 6


@router.get("/settings/active-months")
def get_active_months(db: Session = Depends(get_db),
                        current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.VIEW))):
    return {"months": _get_active_months_cutoff(db),
            "default": DEFAULT_PELLET_ACTIVE_MONTHS}


class ActiveMonthsIn(BaseModel):
    months: int


@router.patch("/settings/active-months")
def set_active_months(payload: ActiveMonthsIn,
                        db: Session = Depends(get_db),
                        current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.MANAGE))):
    if payload.months < 1 or payload.months > 120:
        raise HTTPException(status_code=422, detail="months must be between 1 and 120")
    from app.models.practice_config import PracticeConfig
    row = db.query(PracticeConfig).filter(PracticeConfig.key == PELLET_ACTIVE_MONTHS_KEY).first()
    if row is None:
        row = PracticeConfig(key=PELLET_ACTIVE_MONTHS_KEY, value=str(payload.months))
        db.add(row)
    else:
        row.value = str(payload.months)
    by = current_user.get("email") or "system"
    _audit(db, actor=by, action="pellet_settings_changed",
            summary=f"Active-patient cutoff set to {payload.months} months",
            detail={"key": PELLET_ACTIVE_MONTHS_KEY, "value": payload.months})
    db.commit()
    return {"months": payload.months}


# ─── Inventory lock (pre-production cutover safeguard) ─────────────

@router.get("/settings/inventory-lock")
def get_inventory_lock(db: Session = Depends(get_db),
                        current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.VIEW))):
    """Returns the current pellet-inventory lock state. Visible to anyone
    with pellet:read so the UI can show a banner when locked."""
    from app.services.pellet.lock import get_lock_state
    return get_lock_state(db)


class InventoryLockIn(BaseModel):
    locked: bool
    reason: Optional[str] = None


@router.post("/settings/inventory-lock")
def set_inventory_lock(payload: InventoryLockIn,
                        db: Session = Depends(get_db),
                        current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.MANAGE))):
    """Toggle the pellet inventory lock. When ON, admin-style inventory
    edits (lot metadata, dose-type catalog, historical visit fix-ups)
    return 423 unless the caller is a pellet:manage admin who passes an
    override_reason on the guarded call."""
    from app.services.pellet.lock import set_lock_state
    by = current_user.get("email") or "system"
    return set_lock_state(db, locked=payload.locked, by_email=by,
                            reason=payload.reason)


def _get_active_months_cutoff(db: Session) -> int:
    """Practice-wide setting: a patient is 'active' when their last visit is
    within this many months. Default 6, configurable via PracticeConfig."""
    from app.models.practice_config import get_setting
    raw = get_setting(db, PELLET_ACTIVE_MONTHS_KEY)
    try:
        n = int(raw) if raw else DEFAULT_PELLET_ACTIVE_MONTHS
    except (TypeError, ValueError):
        n = DEFAULT_PELLET_ACTIVE_MONTHS
    return max(1, min(120, n))   # clamp 1..120 months


def _bagged_milestone(active):
    """The active visit's completed 'bagged' milestone, or None."""
    if not active:
        return None
    return next((m for m in (active.milestones or [])
                  if m.kind == "bagged" and m.status == "done"), None)


def _active_visit_bagged(active) -> bool:
    return bool(active and (active.bagged_at or _bagged_milestone(active)))


def _active_visit_bagged_at(active):
    """ISO timestamp the bag was marked — prefers v.bagged_at (Fill Bag),
    falls back to the 'bagged' milestone's completed_at."""
    if not active:
        return None
    if active.bagged_at:
        return active.bagged_at.isoformat()
    ms = _bagged_milestone(active)
    return ms.completed_at.isoformat() if ms and ms.completed_at else None


# ── "Ready to insert" gate ──────────────────────────────────────────
# A visit is ready when, as of its scheduled insertion date:
#   1. mammo result is acceptable (BI-RADS 1/2 or testosterone-only)
#   2. mammo is within 1 year (waived for testosterone-only — no mammo)
#   3. labs are in: FSH+TSH+E2 all entered within 14 days, OR not required
#   4. payment collected
#   5. doses bagged
#   6. not already inserted
ACCEPTABLE_MAMMO_RESULTS = {
    "BI-RADS 1", "BI-RADS 2", "Not Required - Testosterone Only",
}
MAMMO_NOT_REQUIRED = "Not Required - Testosterone Only"


def _has_lab_value(v) -> bool:
    return bool(v) and str(v).strip().lower() != "pending"


def _labs_status(p, ref_date, labs_days: int = 14) -> str:
    """Reason code for labs readiness as of ref_date:
    "not_required" | "none" | "missing_values" | "no_date" | "stale" | "ok"."""
    if p.labs_not_required:
        return "not_required"
    has_values = (_has_lab_value(p.labs_fsh)
                  and _has_lab_value(p.labs_tsh)
                  and _has_lab_value(p.labs_estradiol))
    if not has_values:
        # No labs at all (no date AND no values) vs. partial/missing values.
        if not p.labs_date and not (p.labs_fsh or p.labs_tsh or p.labs_estradiol):
            return "none"
        return "missing_values"
    if not p.labs_date or not ref_date:
        return "no_date"
    if p.labs_date < ref_date - timedelta(days=labs_days):
        return "stale"
    return "ok"


def _mammo_status(p, ref_date, mammo_days: int = 365) -> str:
    """Reason code for mammo readiness as of ref_date:
    "not_required" | "none" | "unacceptable" | "no_date" | "stale" | "ok"."""
    if p.mammo_result == MAMMO_NOT_REQUIRED:
        return "not_required"
    if not p.mammo_result:
        return "none"
    if p.mammo_result not in ACCEPTABLE_MAMMO_RESULTS:
        return "unacceptable"
    if not p.mammo_date or not ref_date:
        return "no_date"
    if p.mammo_date < ref_date - timedelta(days=mammo_days):
        return "stale"
    return "ok"


def _mammo_ready(p, ref_date, mammo_days: int = 365) -> bool:
    return _mammo_status(p, ref_date, mammo_days) in {"ok", "not_required"}


def _labs_ready(p, ref_date, labs_days: int = 14) -> bool:
    return _labs_status(p, ref_date, labs_days) in {"ok", "not_required"}


def _visit_ready(p, active, labs_days: int = 14, mammo_days: int = 365) -> bool:
    if not active or not active.scheduled_date:
        return False
    if active.status == "inserted":
        return False
    if active.payment_status != "collected":
        return False
    if not _active_visit_bagged(active):
        return False
    ref = active.scheduled_date
    return (_mammo_ready(p, ref, mammo_days)
            and _labs_ready(p, ref, labs_days))


def _patient_view_extras(p: PelletPatient, today: _date,
                            active_months: int = DEFAULT_PELLET_ACTIVE_MONTHS,
                            labs_days: int = 14, mammo_days: int = 365) -> dict:
    """Compute view-related fields on a patient: last visit date, days-
    since, next scheduled, recall_due_date / recall_is_due, active visit
    state. Cheap — relies on p.visits being already loaded."""
    visits = sorted(p.visits or [], key=lambda v: v.created_at or datetime.min)
    # Most recent inserted-or-scheduled date
    last_visit_dt = None
    last_visit_id = None
    for v in visits:
        d = (v.inserted_at.date() if v.inserted_at
              else v.scheduled_date if v.scheduled_date else None)
        if d and (last_visit_dt is None or d > last_visit_dt):
            last_visit_dt = d
            last_visit_id = v.id

    # Active visit selection: among non-billed/non-cancelled visits, prefer
    # the soonest visit scheduled today-or-later. A past visit stuck in
    # status='inserted' (awaiting billing close-out) is NOT what staff
    # actively work — the next scheduled visit is.
    open_visits = [v for v in visits if v.status not in ("billed", "cancelled")]
    future = [v for v in open_visits if v.scheduled_date and v.scheduled_date >= today]
    if future:
        active = min(future, key=lambda v: v.scheduled_date)
    elif open_visits:
        active = max(open_visits,
                     key=lambda v: (v.scheduled_date or _date.min,
                                     v.created_at or datetime.min))
    else:
        active = None

    # Upcoming = next future scheduled visit
    next_dt = None
    for v in visits:
        if v.scheduled_date and v.scheduled_date >= today \
           and v.status not in ("billed", "cancelled"):
            if next_dt is None or v.scheduled_date < next_dt:
                next_dt = v.scheduled_date

    # Recall due — only meaningful when no active visit; uses configured
    # per-patient interval (default 4 months).
    interval = p.recall_interval_months or 4
    recall_due_dt = None
    recall_is_due = False
    if last_visit_dt:
        # Approximate "N months" as N*30 days for the comparator
        from datetime import timedelta as _td
        recall_due_dt = last_visit_dt + _td(days=interval * 30)
        if recall_due_dt < today and not active:
            recall_is_due = True

    days_since_last_visit = (today - last_visit_dt).days if last_visit_dt else None
    # Derived activity: a patient is "active" when their last visit was
    # within `active_months` months (approximated as N*30 days). Patients
    # who have never been seen are NOT active.
    cutoff_days = active_months * 30
    is_currently_active = (days_since_last_visit is not None
                              and days_since_last_visit <= cutoff_days)

    return {
        "last_visit_date":   str(last_visit_dt) if last_visit_dt else None,
        "last_visit_id":     str(last_visit_id) if last_visit_id else None,
        "days_since_last_visit": days_since_last_visit,
        "is_currently_active": is_currently_active,
        "active_months_cutoff": active_months,
        "next_scheduled_date": str(next_dt) if next_dt else None,
        "recall_due_date":   str(recall_due_dt) if recall_due_dt else None,
        "recall_is_due":     recall_is_due,
        "active_visit_id":   str(active.id) if active else None,
        "active_visit_status": active.status if active else None,
        "active_visit_payment_status": active.payment_status if active else None,
        "active_visit_scheduled_date": str(active.scheduled_date)
                                          if active and active.scheduled_date else None,
        "active_visit_location": active.location if active else None,
        "active_visit_has_doses": bool(active and (active.doses or [])),
        # "Bagged" = the visit's 'bagged' milestone is done. That milestone
        # is completed by Fill Bag, the dose-card set, OR a manual advance —
        # only Fill Bag also sets v.bagged_at, so the milestone is the
        # authoritative signal (column kept as a fallback).
        "active_visit_bagged": _active_visit_bagged(active),
        "active_visit_bagged_at": _active_visit_bagged_at(active),
        # "Ready to insert" — see _visit_ready. Sub-flags drive the tooltip.
        "active_visit_ready": _visit_ready(p, active, labs_days, mammo_days),
        "active_visit_mammo_ready": (
            _mammo_ready(p, active.scheduled_date, mammo_days)
            if active and active.scheduled_date else False),
        "active_visit_labs_ready": (
            _labs_ready(p, active.scheduled_date, labs_days)
            if active and active.scheduled_date else False),
        # Reason codes (why ✗) — None when there is no active visit.
        "active_visit_mammo_reason": (
            _mammo_status(p, active.scheduled_date, mammo_days)
            if active and active.scheduled_date else None),
        "active_visit_labs_reason": (
            _labs_status(p, active.scheduled_date, labs_days)
            if active and active.scheduled_date else None),
        "active_visit_doses_pulled": (
            sum(1 for d in (active.doses or [])
                  if d.status in ("pulled", "added"))
            if active else 0
        ),
        "active_visit_doses_planned": (
            sum(1 for d in (active.doses or []) if d.status == "planned")
            if active else 0
        ),
        "visits_total":      len(visits),
    }


def _patient_dict(p: PelletPatient, include_visits: bool = False,
                    view_extras: Optional[dict] = None,
                    labs_days: int = 14, mammo_days: int = 365) -> dict:
    out = {
        "labs_valid_days":  labs_days,
        "mammo_valid_days": mammo_days,
        "id":                str(p.id),
        "chart_number":      p.chart_number,
        "patient_name":      p.patient_name,
        "patient_dob":       str(p.patient_dob) if p.patient_dob else None,
        "patient_email":     p.patient_email,
        "patient_phone":     p.patient_phone,
        "primary_insurance": p.primary_insurance,
        "patient_type":      p.patient_type,
        "status":            p.status,
        "modmed_link":       p.modmed_link,
        "mammo_verified":    bool(p.mammo_verified),
        "mammo_date":        str(p.mammo_date) if p.mammo_date else None,
        "mammo_result":      p.mammo_result,
        "mammo_verified_by": p.mammo_verified_by,
        "mammo_verified_at": p.mammo_verified_at.isoformat() if p.mammo_verified_at else None,
        "labs_verified":     bool(p.labs_verified),
        "labs_not_required": bool(p.labs_not_required),
        "labs_date":         str(p.labs_date) if p.labs_date else None,
        "labs_fsh":          p.labs_fsh,
        "labs_tsh":          p.labs_tsh,
        "labs_estradiol":    p.labs_estradiol,
        "labs_verified_by":  p.labs_verified_by,
        "labs_verified_at":  p.labs_verified_at.isoformat() if p.labs_verified_at else None,
        "notes":             p.notes,
        "recall_interval_months": p.recall_interval_months,
        "preferred_lab_name":     p.preferred_lab_name,
        "preferred_lab_phone":    p.preferred_lab_phone,
        "preferred_lab_address":  p.preferred_lab_address,
        "preferred_mammo_facility_name":    p.preferred_mammo_facility_name,
        "preferred_mammo_facility_phone":   p.preferred_mammo_facility_phone,
        "preferred_mammo_facility_fax":     p.preferred_mammo_facility_fax,
        "preferred_mammo_facility_address": p.preferred_mammo_facility_address,
        "created_at":        p.created_at.isoformat() if p.created_at else None,
        "created_by":        p.created_by,
        "buckets":           sorted(patient_buckets(p)),
    }
    if view_extras:
        out.update(view_extras)
    if include_visits:
        out["visits"] = [_visit_dict(v, include_milestones=False, include_doses=False)
                          for v in (p.visits or [])]
    return out


def _visit_dict(v: PelletVisit, include_milestones: bool = True,
                  include_doses: bool = True) -> dict:
    out = {
        "id":                  str(v.id),
        "patient_id":          str(v.patient_id),
        "patient_name":        v.patient.patient_name if v.patient else None,
        "chart_number":        v.patient.chart_number if v.patient else None,
        "patient_type":        v.patient.patient_type if v.patient else None,
        "visit_kind":          v.visit_kind,
        "status":              v.status,
        "price_amount":        float(v.price_amount) if v.price_amount is not None else None,
        "payment_status":      v.payment_status,
        "klara_sent_at":       v.klara_sent_at.isoformat() if v.klara_sent_at else None,
        "klara_sent_by":       v.klara_sent_by,
        "payment_collected_at": v.payment_collected_at.isoformat() if v.payment_collected_at else None,
        "payment_collected_by": v.payment_collected_by,
        "scheduled_date":      str(v.scheduled_date) if v.scheduled_date else None,
        "location":            v.location,
        "modmed_link":         v.modmed_link,
        "provider":            v.provider,
        "bagged_at":           v.bagged_at.isoformat() if v.bagged_at else None,
        "bagged_by":           v.bagged_by,
        "inserted_at":         v.inserted_at.isoformat() if v.inserted_at else None,
        "inserted_by":         v.inserted_by,
        "outcome":             v.outcome,
        "outcome_notes":       v.outcome_notes,
        "claim_number":        v.claim_number,
        "billed_at":           v.billed_at.isoformat() if v.billed_at else None,
        "billed_by":           v.billed_by,
        "notes":               v.notes,
        "is_historical":       bool(v.is_historical),
        "created_at":          v.created_at.isoformat() if v.created_at else None,
        "created_by":          v.created_by,
    }
    if include_milestones:
        out["milestones"] = [
            {
                "id":           str(m.id), "kind": m.kind, "title": m.title,
                "position":     m.position, "status": m.status,
                "completed_at": m.completed_at.isoformat() if m.completed_at else None,
                "completed_by": m.completed_by, "notes": m.notes,
            }
            for m in (v.milestones or [])
        ]
    if include_doses:
        out["doses"] = [
            {
                "id":           str(d.id),
                "dose_type_id": str(d.dose_type_id),
                "dose_label":   d.dose_type.label if d.dose_type else None,
                "is_controlled": bool(d.dose_type.is_controlled) if d.dose_type else False,
                "lot_id":       str(d.lot_id) if d.lot_id else None,
                "qualgen_lot":  d.lot.qualgen_lot_number if d.lot else None,
                "lot_expiration_date": (d.lot.expiration_date.isoformat()
                                         if d.lot and d.lot.expiration_date else None),
                "quantity":     d.quantity,
                "position":     d.position,
                "status":       d.status,
                "pulled_at":    d.pulled_at.isoformat() if d.pulled_at else None,
                "pulled_by":    d.pulled_by,
                "notes":        d.notes,
            }
            for d in (v.doses or [])
        ]
    return out


class PatientIn(BaseModel):
    chart_number:      str
    patient_name:      str
    patient_dob:       Optional[str] = None
    patient_email:     Optional[str] = None
    patient_phone:     Optional[str] = None
    primary_insurance: Optional[str] = None
    patient_type:      str = "new"   # new | established
    notes:             Optional[str] = None


@router.post("/patients", status_code=201)
def create_patient(payload: PatientIn,
                     db: Session = Depends(get_db),
                     current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.WORK))):
    chart = payload.chart_number.strip()
    if not chart:
        raise HTTPException(status_code=422, detail="chart_number required")
    if payload.patient_type not in PATIENT_TYPES:
        raise HTTPException(status_code=422, detail="patient_type must be new or established")
    existing = db.query(PelletPatient).filter(PelletPatient.chart_number == chart).first()
    if existing:
        raise HTTPException(status_code=409,
                            detail=f"patient with chart # {chart} already enrolled")

    by = current_user.get("email") or "system"
    p = PelletPatient(
        chart_number=chart,
        patient_name=payload.patient_name.strip(),
        patient_dob=_parse_date(payload.patient_dob, "patient_dob"),
        patient_email=payload.patient_email,
        patient_phone=payload.patient_phone,
        primary_insurance=payload.primary_insurance,
        patient_type=payload.patient_type,
        notes=payload.notes,
        created_by=by,
    )
    db.add(p); db.flush()
    _audit(db, actor=by, action="patient_enrolled",
            summary=f"Enrolled {p.patient_name} (chart {p.chart_number}, {p.patient_type})",
            detail={"chart_number": p.chart_number, "patient_type": p.patient_type})
    db.commit(); db.refresh(p)
    return _patient_dict(p, include_visits=True,
                         labs_days=cfg(db, "labs_valid_days"),
                         mammo_days=cfg(db, "mammo_valid_days"))


PATIENT_VIEWS = ["roster", "last_visits", "upcoming", "recall_due",
                 "needs_mammo", "needs_dosing", "ready", "paid", "unpaid"]


@router.get("/patient-view-counts")
def patient_view_counts(
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.VIEW)),
):
    """Lean count-per-view endpoint for the tab strip. Loads patients
    once (still with visits joined) and counts each view in Python —
    much cheaper than 9 separate full-list calls."""
    rows = (db.query(PelletPatient)
              .options(joinedload(PelletPatient.visits)
                        .joinedload(PelletVisit.doses))
              .all())
    today = _date.today()
    active_months = _get_active_months_cutoff(db)
    out = {v: 0 for v in PATIENT_VIEWS}
    out["roster"] = len(rows)
    for p in rows:
        x = _patient_view_extras(p, today, active_months)
        if x["last_visit_date"]:
            out["last_visits"] += 1
        if x["next_scheduled_date"]:
            out["upcoming"] += 1
        if x["recall_is_due"]:
            out["recall_due"] += 1
        if not p.mammo_verified:
            out["needs_mammo"] += 1
        if x["active_visit_id"] and not x["active_visit_has_doses"]:
            out["needs_dosing"] += 1
        if (x["active_visit_id"]
            and x["active_visit_payment_status"] == "collected"
            and x["active_visit_doses_planned"] == 0
            and x["active_visit_doses_pulled"] > 0
            and x["active_visit_status"] != "inserted"):
            out["ready"] += 1
        if x["active_visit_payment_status"] == "collected":
            out["paid"] += 1
        if (x["active_visit_id"]
            and x["active_visit_payment_status"] in ("not_sent", "sent")):
            out["unpaid"] += 1
    return out


@router.get("/patients")
def list_patients(
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.VIEW)),
    status: Optional[str] = None,
    patient_type: Optional[str] = None,
    bucket: Optional[str] = None,
    view: str = "roster",
    search: Optional[str] = None,
    location: Optional[str] = None,
    # Date-range filter for the 'upcoming' view (next_scheduled_date)
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    page: int = 1,
    per_page: int = 200,
):
    if view not in PATIENT_VIEWS:
        raise HTTPException(status_code=422, detail=f"unknown view: {view}")

    # Use selectinload (separate batched IN queries) instead of joinedload
    # (LEFT JOIN cross-product). With ~1,200 patients × ~5,300 visits ×
    # thousands of milestones + ~7,500 doses, joinedload was producing
    # millions of rows that SQLAlchemy had to dedupe in Python — the
    # "Loading week…" sat for 10 s on /pellets/patients?view=upcoming.
    q = (db.query(PelletPatient)
           .options(selectinload(PelletPatient.visits)
                     .selectinload(PelletVisit.milestones),
                    selectinload(PelletPatient.visits)
                     .selectinload(PelletVisit.doses)))
    # 'active' / 'inactive' status are DERIVED from last_visit_date (handled
    # post-query). Any other status value (e.g. 'declined') still filters
    # the DB column directly.
    DERIVED_STATUSES = {"active", "inactive"}
    if status and status not in DERIVED_STATUSES:
        q = q.filter(PelletPatient.status == status)
    if patient_type:
        q = q.filter(PelletPatient.patient_type == patient_type)
    if search:
        like = f"%{search}%"
        q = q.filter(or_(
            PelletPatient.patient_name.ilike(like),
            PelletPatient.chart_number.ilike(like),
        ))
    rows = q.all()
    today = _date.today()
    active_months = _get_active_months_cutoff(db)
    labs_days = cfg(db, "labs_valid_days")
    mammo_days = cfg(db, "mammo_valid_days")

    # Compute view-extras for each patient once
    decorated = [(p, _patient_view_extras(p, today, active_months,
                                          labs_days, mammo_days)) for p in rows]

    # Apply derived status filter (active = seen in last N months)
    if status == "active":
        decorated = [(p, x) for (p, x) in decorated if x["is_currently_active"]]
    elif status == "inactive":
        decorated = [(p, x) for (p, x) in decorated if not x["is_currently_active"]]

    # Apply bucket filter (legacy — kept for the chip bar)
    if bucket:
        decorated = [(p, x) for (p, x) in decorated
                      if bucket in patient_buckets(p)]

    # Active-visit location filter (used by the calendar's location dropdown)
    if location:
        decorated = [(p, x) for (p, x) in decorated
                      if x["active_visit_location"] == location]

    # ── View-specific filter + sort ──
    if view == "last_visits":
        decorated = [(p, x) for (p, x) in decorated if x["last_visit_date"]]
        decorated.sort(key=lambda t: t[1]["last_visit_date"] or "", reverse=True)

    elif view == "upcoming":
        decorated = [(p, x) for (p, x) in decorated if x["next_scheduled_date"]]
        if from_date:
            decorated = [(p, x) for (p, x) in decorated
                          if x["next_scheduled_date"] >= from_date]
        if to_date:
            decorated = [(p, x) for (p, x) in decorated
                          if x["next_scheduled_date"] <= to_date]
        decorated.sort(key=lambda t: t[1]["next_scheduled_date"] or "")

    elif view == "recall_due":
        decorated = [(p, x) for (p, x) in decorated if x["recall_is_due"]]
        # Sort: longest overdue first
        decorated.sort(key=lambda t: t[1].get("days_since_last_visit") or 0, reverse=True)

    elif view == "needs_mammo":
        decorated = [(p, x) for (p, x) in decorated if not p.mammo_verified]
        decorated.sort(key=lambda t: t[1]["last_visit_date"] or "", reverse=True)

    elif view == "needs_dosing":
        # Active visit exists but no doses yet
        decorated = [(p, x) for (p, x) in decorated
                      if x["active_visit_id"] and not x["active_visit_has_doses"]]
        decorated.sort(key=lambda t: t[0].patient_name)

    elif view == "ready":
        # Mammo current + acceptable, labs in, paid, bagged — see _visit_ready.
        # Same flag the calendar's green "ready" badge uses, so they agree.
        decorated = [(p, x) for (p, x) in decorated if x["active_visit_ready"]]
        decorated.sort(key=lambda t: t[1]["active_visit_scheduled_date"] or "")

    elif view == "paid":
        decorated = [(p, x) for (p, x) in decorated
                      if x["active_visit_payment_status"] == "collected"]
        decorated.sort(key=lambda t: t[0].patient_name)

    elif view == "unpaid":
        decorated = [(p, x) for (p, x) in decorated
                      if x["active_visit_id"]
                         and x["active_visit_payment_status"] in ("not_sent", "sent")]
        decorated.sort(key=lambda t: t[0].patient_name)

    else:  # roster
        decorated.sort(key=lambda t: (t[0].patient_name or "").lower())

    total = len(decorated)
    paged = decorated[(page - 1) * per_page : page * per_page]
    return {
        "total": total, "page": page, "view": view,
        # include_visits=False — the table consumes view_extras instead,
        # which is precomputed on the server and ~10× smaller per row.
        "patients": [_patient_dict(p, include_visits=False, view_extras=x,
                                   labs_days=labs_days, mammo_days=mammo_days)
                      for (p, x) in paged],
    }


@router.get("/patients/{patient_id}")
def get_patient(patient_id: str,
                  db: Session = Depends(get_db),
                  current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.VIEW))):
    p = (db.query(PelletPatient)
           .options(joinedload(PelletPatient.visits)
                      .joinedload(PelletVisit.milestones),
                    joinedload(PelletPatient.visits)
                      .joinedload(PelletVisit.doses)
                      .joinedload(PelletVisitDose.dose_type),
                    joinedload(PelletPatient.visits)
                      .joinedload(PelletVisit.doses)
                      .joinedload(PelletVisitDose.lot),
                    joinedload(PelletPatient.mammos),
                    joinedload(PelletPatient.labs),
                    joinedload(PelletPatient.patient_notes))
           .filter(PelletPatient.id == patient_id).first())
    if not p:
        raise HTTPException(status_code=404, detail="patient not found")
    # HIPAA access logging: record who read this chart (Fable audit #13).
    # The audit log is the same write-only table the rest of the module
    # uses; phi_access_chart_view is its own action so reporting can
    # filter views vs. mutations.
    _audit(db, actor=current_user.get("email") or "system",
           action="phi_access_chart_view",
           detail={"patient_id": str(p.id), "chart_number": p.chart_number},
           summary=f"Viewed patient chart {p.chart_number or p.id}")
    db.commit()
    out = _patient_dict(p, include_visits=False,
                        labs_days=cfg(db, "labs_valid_days"),
                        mammo_days=cfg(db, "mammo_valid_days"))
    out["visits"] = [_visit_dict(v) for v in (p.visits or [])]
    out["mammos"] = [_mammo_dict(m) for m in (p.mammos or [])]
    out["labs"]   = [_lab_dict(l)   for l in (p.labs   or [])]
    out["notes"]  = [_note_dict(n)  for n in (p.patient_notes or [])]
    return out


class PatientPatch(BaseModel):
    patient_name:      Optional[str] = None
    patient_dob:       Optional[str] = None
    patient_email:     Optional[str] = None
    patient_phone:     Optional[str] = None
    primary_insurance: Optional[str] = None
    patient_type:      Optional[str] = None
    status:            Optional[str] = None
    modmed_link:       Optional[str] = None
    notes:             Optional[str] = None
    recall_interval_months: Optional[int] = None
    labs_not_required:      Optional[bool] = None
    preferred_lab_name:     Optional[str] = None
    preferred_lab_phone:    Optional[str] = None
    preferred_lab_address:  Optional[str] = None
    preferred_mammo_facility_name:    Optional[str] = None
    preferred_mammo_facility_phone:   Optional[str] = None
    preferred_mammo_facility_fax:     Optional[str] = None
    preferred_mammo_facility_address: Optional[str] = None


@router.patch("/patients/{patient_id}")
def patch_patient(patient_id: str, payload: PatientPatch,
                    db: Session = Depends(get_db),
                    current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.WORK))):
    p = db.query(PelletPatient).filter(PelletPatient.id == patient_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="patient not found")
    data = payload.model_dump(exclude_unset=True)
    if "patient_type" in data and data["patient_type"] not in PATIENT_TYPES:
        raise HTTPException(status_code=422, detail="patient_type must be new or established")
    if "patient_dob" in data:
        data["patient_dob"] = _parse_date(data["patient_dob"], "patient_dob")
    for k, v in data.items():
        setattr(p, k, v)
    db.commit(); db.refresh(p)
    return _patient_dict(p,
                         labs_days=cfg(db, "labs_valid_days"),
                         mammo_days=cfg(db, "mammo_valid_days"))


# Prerequisite verification — mammogram + labs

class MammoIn(BaseModel):
    mammo_date:       str
    mammo_result:     str   # "BI-RADS 1", "BI-RADS 2", or freeform
    facility_name:    Optional[str] = None
    facility_phone:   Optional[str] = None
    facility_fax:     Optional[str] = None
    facility_address: Optional[str] = None
    notes:            Optional[str] = None


@router.post("/patients/{patient_id}/verify-mammo")
def verify_mammo(patient_id: str, payload: MammoIn,
                   db: Session = Depends(get_db),
                   current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.WORK))):
    """Records a new mammo result. Appends to the patient's mammo history
    AND updates the cached scalar fields on PelletPatient so existing
    filter views ('Needs mammo' etc.) still work."""
    p = db.query(PelletPatient).filter(PelletPatient.id == patient_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="patient not found")
    by = current_user.get("email") or "system"
    dt = _parse_date(payload.mammo_date, "mammo_date")

    # 1. History row (append, never overwrite)
    row = PelletPatientMammo(
        patient_id=p.id,
        mammo_date=dt,
        result=payload.mammo_result.strip(),
        facility_name=payload.facility_name,
        facility_phone=payload.facility_phone,
        facility_fax=payload.facility_fax,
        facility_address=payload.facility_address,
        notes=payload.notes,
        verified_by=by,
    )
    db.add(row)

    # 2. Update cached scalars on PelletPatient — only if this is the
    # most recent mammo (i.e. no later one already on file)
    latest = (db.query(PelletPatientMammo)
                .filter(PelletPatientMammo.patient_id == p.id)
                .order_by(PelletPatientMammo.mammo_date.desc()).first())
    if not latest or (dt and (latest.mammo_date or _date.min) <= dt):
        p.mammo_verified = True
        p.mammo_date = dt
        p.mammo_result = payload.mammo_result.strip()
        p.mammo_verified_by = by
        p.mammo_verified_at = now_utc_naive()

    _complete_visit_milestone_for_patient(db, p, "mammo_verified", by)
    _audit(db, actor=by, action="mammo_verified",
            summary=f"Mammo verified for {p.patient_name}: {payload.mammo_result} on {dt}",
            detail={"chart": p.chart_number, "result": payload.mammo_result, "date": str(dt)})
    db.commit(); db.refresh(p)
    return _patient_dict_with_history(db, p)


@router.delete("/patients/{patient_id}/mammos/{mammo_id}", status_code=204)
def delete_mammo(patient_id: str, mammo_id: str,
                   db: Session = Depends(get_db),
                   current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.WORK))):
    row = (db.query(PelletPatientMammo)
             .filter(PelletPatientMammo.id == mammo_id,
                     PelletPatientMammo.patient_id == patient_id).first())
    if not row:
        raise HTTPException(status_code=404, detail="mammo entry not found")
    by = current_user.get("email") or "system"
    db.delete(row); db.flush()
    # Refresh patient cached scalars to the new latest
    _refresh_mammo_cache(db, patient_id)
    _audit(db, actor=by, action="mammo_deleted",
            summary=f"Deleted mammo entry {mammo_id}",
            detail={"patient_id": patient_id})
    db.commit()
    return


def _refresh_mammo_cache(db: Session, patient_id: str) -> None:
    p = db.query(PelletPatient).filter(PelletPatient.id == patient_id).first()
    if not p: return
    latest = (db.query(PelletPatientMammo)
                .filter(PelletPatientMammo.patient_id == patient_id)
                .order_by(PelletPatientMammo.mammo_date.desc()).first())
    if latest:
        p.mammo_verified = True
        p.mammo_date = latest.mammo_date
        p.mammo_result = latest.result
    else:
        p.mammo_verified = False
        p.mammo_date = None
        p.mammo_result = None


class LabsIn(BaseModel):
    labs_date:      str
    labs_fsh:       Optional[str] = None
    labs_tsh:       Optional[str] = None
    labs_estradiol: Optional[str] = None
    notes:          Optional[str] = None


@router.post("/patients/{patient_id}/verify-labs")
def verify_labs(patient_id: str, payload: LabsIn,
                  db: Session = Depends(get_db),
                  current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.WORK))):
    p = db.query(PelletPatient).filter(PelletPatient.id == patient_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="patient not found")
    by = current_user.get("email") or "system"
    dt = _parse_date(payload.labs_date, "labs_date")

    row = PelletPatientLab(
        patient_id=p.id,
        labs_date=dt,
        fsh=payload.labs_fsh,
        tsh=payload.labs_tsh,
        estradiol=payload.labs_estradiol,
        notes=payload.notes,
        verified_by=by,
    )
    db.add(row)

    latest = (db.query(PelletPatientLab)
                .filter(PelletPatientLab.patient_id == p.id)
                .order_by(PelletPatientLab.labs_date.desc()).first())
    if not latest or (dt and (latest.labs_date or _date.min) <= dt):
        p.labs_verified = True
        p.labs_date = dt
        p.labs_fsh = payload.labs_fsh
        p.labs_tsh = payload.labs_tsh
        p.labs_estradiol = payload.labs_estradiol
        p.labs_verified_by = by
        p.labs_verified_at = now_utc_naive()

    _complete_visit_milestone_for_patient(db, p, "labs_verified", by)
    _audit(db, actor=by, action="labs_verified",
            summary=f"Labs verified for {p.patient_name} on {dt}",
            detail={"chart": p.chart_number,
                    "fsh": payload.labs_fsh, "tsh": payload.labs_tsh,
                    "estradiol": payload.labs_estradiol})
    db.commit(); db.refresh(p)
    return _patient_dict_with_history(db, p)


@router.delete("/patients/{patient_id}/labs/{lab_id}", status_code=204)
def delete_lab(patient_id: str, lab_id: str,
                 db: Session = Depends(get_db),
                 current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.WORK))):
    row = (db.query(PelletPatientLab)
             .filter(PelletPatientLab.id == lab_id,
                     PelletPatientLab.patient_id == patient_id).first())
    if not row:
        raise HTTPException(status_code=404, detail="lab entry not found")
    by = current_user.get("email") or "system"
    db.delete(row); db.flush()
    _refresh_labs_cache(db, patient_id)
    _audit(db, actor=by, action="labs_deleted",
            summary=f"Deleted labs entry {lab_id}")
    db.commit()
    return


def _refresh_labs_cache(db: Session, patient_id: str) -> None:
    p = db.query(PelletPatient).filter(PelletPatient.id == patient_id).first()
    if not p: return
    latest = (db.query(PelletPatientLab)
                .filter(PelletPatientLab.patient_id == patient_id)
                .order_by(PelletPatientLab.labs_date.desc()).first())
    if latest:
        p.labs_verified = True
        p.labs_date = latest.labs_date
        p.labs_fsh = latest.fsh
        p.labs_tsh = latest.tsh
        p.labs_estradiol = latest.estradiol
    else:
        p.labs_verified = False
        p.labs_date = None
        p.labs_fsh = None; p.labs_tsh = None; p.labs_estradiol = None


# ── Patient notes ──

class PatientNoteIn(BaseModel):
    body: str


@router.post("/patients/{patient_id}/notes", status_code=201)
def add_patient_note(patient_id: str, payload: PatientNoteIn,
                       db: Session = Depends(get_db),
                       current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.WORK))):
    p = db.query(PelletPatient).filter(PelletPatient.id == patient_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="patient not found")
    if not payload.body.strip():
        raise HTTPException(status_code=422, detail="note body required")
    by = current_user.get("email") or "system"
    n = PelletPatientNote(
        patient_id=p.id, author=by, body=payload.body.strip(),
    )
    db.add(n); db.commit(); db.refresh(n)
    return {"id": str(n.id), "author": n.author,
            "body": n.body, "created_at": n.created_at.isoformat()}


@router.delete("/patients/{patient_id}/notes/{note_id}", status_code=204)
def delete_patient_note(patient_id: str, note_id: str,
                          db: Session = Depends(get_db),
                          current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.WORK))):
    n = (db.query(PelletPatientNote)
            .filter(PelletPatientNote.id == note_id,
                    PelletPatientNote.patient_id == patient_id).first())
    if not n:
        raise HTTPException(status_code=404, detail="note not found")
    db.delete(n); db.commit()
    return


def _patient_dict_with_history(db: Session, p: PelletPatient) -> dict:
    """Lightweight helper used by POST/PATCH endpoints that don't load
    relationships up-front."""
    out = _patient_dict(p,
                        labs_days=cfg(db, "labs_valid_days"),
                        mammo_days=cfg(db, "mammo_valid_days"))
    out["mammos"] = [_mammo_dict(m) for m in (p.mammos or [])]
    out["labs"]   = [_lab_dict(l)   for l in (p.labs   or [])]
    out["notes"]  = [_note_dict(n)  for n in (p.patient_notes or [])]
    return out


def _mammo_dict(m: PelletPatientMammo) -> dict:
    return {
        "id":               str(m.id),
        "mammo_date":       str(m.mammo_date),
        "result":           m.result,
        "facility_name":    m.facility_name,
        "facility_phone":   m.facility_phone,
        "facility_fax":     m.facility_fax,
        "facility_address": m.facility_address,
        "notes":            m.notes,
        "verified_by":      m.verified_by,
        "created_at":       m.created_at.isoformat() if m.created_at else None,
    }


def _lab_dict(l: PelletPatientLab) -> dict:
    return {
        "id":          str(l.id),
        "labs_date":   str(l.labs_date),
        "fsh":         l.fsh,
        "tsh":         l.tsh,
        "estradiol":   l.estradiol,
        "notes":       l.notes,
        "verified_by": l.verified_by,
        "created_at":  l.created_at.isoformat() if l.created_at else None,
    }


def _note_dict(n: PelletPatientNote) -> dict:
    return {
        "id":         str(n.id),
        "author":     n.author,
        "body":       n.body,
        "created_at": n.created_at.isoformat() if n.created_at else None,
    }


def _complete_visit_milestone_for_patient(db: Session, p: PelletPatient,
                                            kind: str, by: str) -> None:
    """Cascade prerequisite verification to the latest open visit."""
    v = (db.query(PelletVisit)
           .filter(PelletVisit.patient_id == p.id,
                   PelletVisit.status.notin_(["billed", "cancelled"]))
           .order_by(PelletVisit.created_at.desc()).first())
    if not v:
        return
    m = next((m for m in v.milestones if m.kind == kind), None)
    if m and m.status == "pending":
        m.status = "done"
        m.completed_at = now_utc_naive()
        m.completed_by = by


# ─── Visits ─────────────────────────────────────────────────────────

class DoseLineIn(BaseModel):
    dose_type_id: str
    quantity:     DoseQty = 1
    # Optional explicit lot override. When set, the named lot is used
    # instead of FIFO auto-pick. Lot must (a) belong to the dose_type and
    # (b) have enough stock at the visit's location.
    lot_id:       Optional[str] = None


class VisitIn(BaseModel):
    patient_id:   str
    visit_kind:   str = "initial"  # initial | booster | repeat
    doses:        list[DoseLineIn] = []   # dose card
    scheduled_date: Optional[str] = None
    location:     str                     # required — where the insertion will happen
    provider:     Optional[str] = None
    notes:        Optional[str] = None


@router.post("/visits", status_code=201)
def create_visit(payload: VisitIn,
                   db: Session = Depends(get_db),
                   current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.WORK))):
    if payload.visit_kind not in VISIT_KINDS:
        raise HTTPException(status_code=422, detail="invalid visit_kind")
    if payload.location not in PELLET_LOCATIONS:
        raise HTTPException(status_code=422,
                            detail=f"location must be one of {PELLET_LOCATIONS}")

    p = db.query(PelletPatient).filter(PelletPatient.id == payload.patient_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="patient not found")

    by = current_user.get("email") or "system"
    v = PelletVisit(
        patient_id=p.id,
        visit_kind=payload.visit_kind,
        status="in_progress",
        price_amount=default_price_for(p.patient_type)
                       if payload.visit_kind == "initial"
                       else default_price_for("established"),
        scheduled_date=_parse_date(payload.scheduled_date, "scheduled_date"),
        location=payload.location,
        provider=payload.provider,
        notes=payload.notes,
        created_by=by,
    )
    db.add(v); db.flush()
    spawn_milestones(db, v, p.patient_type)

    # Dose card — add planned doses
    for i, d in enumerate(payload.doses, start=1):
        dt = db.query(PelletDoseType).filter(PelletDoseType.id == d.dose_type_id).first()
        if not dt:
            raise HTTPException(status_code=422,
                                detail=f"unknown dose type {d.dose_type_id}")
        if d.quantity <= 0:
            raise HTTPException(status_code=422, detail="quantity must be positive")
        db.add(PelletVisitDose(
            visit_id=v.id, dose_type_id=dt.id, quantity=d.quantity,
            position=i, status="planned",
        ))

    # Flush so the newly-added milestones are visible via v.milestones
    db.flush()
    db.refresh(v)

    # If the patient already has verified mammo/labs, auto-complete those milestones
    if p.mammo_verified:
        _complete_milestone(v, "mammo_verified", by)
    if p.labs_verified:
        _complete_milestone(v, "labs_verified", by)

    _audit(db, actor=by, action="visit_created",
            summary=f"New {v.visit_kind} pellet visit for {p.patient_name}",
            detail={"patient_id": str(p.id), "visit_kind": v.visit_kind,
                    "doses_planned": len(payload.doses)})
    db.commit(); db.refresh(v)
    return _visit_dict(v)


# ─── Historical-import visits ───────────────────────────────────────
# These exist purely to record past pellet insertions from before the
# system existed (or from an old EHR). They never touch PelletStock,
# never spawn milestones, never appear in the daily-count blocker query.

class HistoricalVisitIn(BaseModel):
    scheduled_date: str                 # YYYY-MM-DD — the date of the past visit
    visit_kind:     str = "repeat"      # initial | booster | repeat
    location:       Optional[str] = None
    provider:       Optional[str] = None
    outcome_notes:  Optional[str] = None  # free-form dose summary ("E 25mg + T 100mg")
    notes:          Optional[str] = None


@router.post("/patients/{patient_id}/historical-visits", status_code=201)
def create_historical_visit(patient_id: str, payload: HistoricalVisitIn,
                              override_reason: Optional[str] = None,
                              db: Session = Depends(get_db),
                              current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.WORK))):
    """Record a past pellet insertion that happened before this system was
    used. Does NOT affect inventory in any way — no dose lines are created,
    no milestones spawned, no audit-stock rows written. Use this for
    historical chart data import or manual backfill."""
    from app.services.pellet.lock import ensure_unlocked_or_override
    ensure_unlocked_or_override(db, current_user=current_user,
                                  override_reason=override_reason,
                                  action_label="historical visit create")
    p = db.query(PelletPatient).filter(PelletPatient.id == patient_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="patient not found")
    dt = _parse_date(payload.scheduled_date, "scheduled_date")
    if not dt:
        raise HTTPException(status_code=422, detail="scheduled_date is required")
    if payload.visit_kind not in VISIT_KINDS:
        raise HTTPException(status_code=422,
                            detail=f"visit_kind must be one of {VISIT_KINDS}")
    if payload.location and payload.location not in PELLET_LOCATIONS:
        raise HTTPException(status_code=422, detail="invalid location")

    by = current_user.get("email") or "system"
    v = PelletVisit(
        patient_id=p.id,
        visit_kind=payload.visit_kind,
        status="inserted",           # treat as a completed past visit
        scheduled_date=dt,
        inserted_at=datetime(dt.year, dt.month, dt.day),
        inserted_by=by,
        outcome="perfect",
        outcome_notes=payload.outcome_notes,
        location=payload.location,
        provider=payload.provider,
        notes=payload.notes,
        is_historical=True,
        created_by=by,
    )
    db.add(v); db.flush()
    _audit(db, actor=by, action="visit_historical_added",
            summary=f"Historical visit recorded for {p.patient_name} on {dt}",
            detail={"patient_id": str(p.id), "visit_id": str(v.id),
                    "scheduled_date": str(dt)})
    db.commit(); db.refresh(v)
    return _visit_dict(v)


class HistoricalVisitPatch(BaseModel):
    scheduled_date: Optional[str] = None
    visit_kind:     Optional[str] = None
    location:       Optional[str] = None
    provider:       Optional[str] = None
    outcome_notes:  Optional[str] = None
    notes:          Optional[str] = None


@router.patch("/visits/{visit_id}/historical")
def patch_historical_visit(visit_id: str, payload: HistoricalVisitPatch,
                             override_reason: Optional[str] = None,
                             db: Session = Depends(get_db),
                             current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.WORK))):
    from app.services.pellet.lock import ensure_unlocked_or_override
    ensure_unlocked_or_override(db, current_user=current_user,
                                  override_reason=override_reason,
                                  action_label="historical visit edit")
    v = db.query(PelletVisit).filter(PelletVisit.id == visit_id).first()
    if not v:
        raise HTTPException(status_code=404, detail="visit not found")
    if not v.is_historical:
        raise HTTPException(status_code=409,
                            detail="this endpoint only edits historical (imported) visits")
    by = current_user.get("email") or "system"
    if payload.scheduled_date is not None:
        dt = _parse_date(payload.scheduled_date, "scheduled_date")
        if dt:
            v.scheduled_date = dt
            v.inserted_at = datetime(dt.year, dt.month, dt.day)
    if payload.visit_kind is not None:
        if payload.visit_kind not in VISIT_KINDS:
            raise HTTPException(status_code=422,
                                detail=f"visit_kind must be one of {VISIT_KINDS}")
        v.visit_kind = payload.visit_kind
    if payload.location is not None:
        if payload.location and payload.location not in PELLET_LOCATIONS:
            raise HTTPException(status_code=422, detail="invalid location")
        v.location = payload.location or None
    if payload.provider is not None:
        v.provider = payload.provider or None
    if payload.outcome_notes is not None:
        v.outcome_notes = payload.outcome_notes or None
    if payload.notes is not None:
        v.notes = payload.notes or None
    _audit(db, actor=by, action="visit_historical_edited",
            summary=f"Edited historical visit {v.id}",
            detail={"visit_id": str(v.id)})
    db.commit(); db.refresh(v)
    return _visit_dict(v)


@router.delete("/visits/{visit_id}/historical", status_code=204)
def delete_historical_visit(visit_id: str,
                              override_reason: Optional[str] = None,
                              db: Session = Depends(get_db),
                              current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.WORK))):
    from app.services.pellet.lock import ensure_unlocked_or_override
    ensure_unlocked_or_override(db, current_user=current_user,
                                  override_reason=override_reason,
                                  action_label="historical visit delete")
    v = db.query(PelletVisit).filter(PelletVisit.id == visit_id).first()
    if not v:
        raise HTTPException(status_code=404, detail="visit not found")
    if not v.is_historical:
        raise HTTPException(status_code=409,
                            detail="only historical visits can be deleted via this endpoint")
    by = current_user.get("email") or "system"
    patient_id = str(v.patient_id)
    _audit(db, actor=by, action="visit_historical_deleted",
            summary=f"Deleted historical visit {v.id}",
            detail={"visit_id": str(v.id), "patient_id": patient_id})
    db.delete(v)
    db.commit()
    return


def _complete_milestone(v: PelletVisit, kind: str, by: str,
                          notes: Optional[str] = None) -> bool:
    m = next((m for m in v.milestones if m.kind == kind), None)
    if not m or m.status not in ("pending",):
        return False
    m.status = "done"
    m.completed_at = now_utc_naive()
    m.completed_by = by
    if notes:
        m.notes = notes
    return True


@router.get("/visits/{visit_id}")
def get_visit(visit_id: str,
                db: Session = Depends(get_db),
                current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.VIEW))):
    v = (db.query(PelletVisit)
           .options(joinedload(PelletVisit.patient),
                    joinedload(PelletVisit.milestones),
                    joinedload(PelletVisit.doses).joinedload(PelletVisitDose.dose_type),
                    joinedload(PelletVisit.doses).joinedload(PelletVisitDose.lot))
           .filter(PelletVisit.id == visit_id).first())
    if not v:
        raise HTTPException(status_code=404, detail="visit not found")
    return _visit_dict(v)


class VisitPatch(BaseModel):
    scheduled_date: Optional[str] = None
    location:       Optional[str] = None
    provider:       Optional[str] = None
    price_amount:   Optional[float] = None
    notes:          Optional[str] = None


@router.patch("/visits/{visit_id}")
def patch_visit(visit_id: str, payload: VisitPatch,
                  db: Session = Depends(get_db),
                  current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.WORK))):
    v = (db.query(PelletVisit).options(joinedload(PelletVisit.milestones))
           .filter(PelletVisit.id == visit_id).first())
    if not v:
        raise HTTPException(status_code=404, detail="visit not found")
    if payload.location and payload.location not in PELLET_LOCATIONS:
        raise HTTPException(status_code=422, detail="invalid location")
    by = current_user.get("email") or "system"
    data = payload.model_dump(exclude_unset=True)
    if "scheduled_date" in data:
        v.scheduled_date = _parse_date(data["scheduled_date"], "scheduled_date")
        # Setting a date auto-completes the 'scheduled' milestone
        if v.scheduled_date and _complete_milestone(v, "scheduled", by):
            _audit(db, actor=by, action="visit_scheduled",
                    summary=f"Scheduled visit on {v.scheduled_date}",
                    detail={"visit_id": str(v.id), "date": str(v.scheduled_date)})
    # Capture before/after for audit on any non-schedule field change.
    # Location especially matters for the Sch III chain-of-custody trail.
    field_changes = {}
    for k in ("location", "provider", "price_amount", "notes"):
        if k in data:
            old = getattr(v, k)
            new = data[k]
            if old != new:
                field_changes[k] = {"from": (str(old) if old is not None else None),
                                     "to":   (str(new) if new is not None else None)}
            setattr(v, k, new)
    if field_changes:
        _audit(db, actor=by, action="visit_edited",
                summary=(f"Edited visit {v.id}: "
                        + ", ".join(f"{k} {c['from']!r}→{c['to']!r}"
                                      for k, c in field_changes.items())),
                detail={"visit_id": str(v.id), "changes": field_changes})
    db.commit(); db.refresh(v)
    return _visit_dict(v)


# Reschedule + cancel --------------------------------------------------

class VisitRescheduleIn(BaseModel):
    new_date: str           # YYYY-MM-DD
    reason:   Optional[str] = None


@router.post("/visits/{visit_id}/reschedule")
def reschedule_visit(visit_id: str, payload: VisitRescheduleIn,
                      db: Session = Depends(get_db),
                      current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.WORK))):
    """Move a visit to a new date without touching dose state. The visit
    stays open (status unchanged) — staff just need the new appointment
    on the calendar. Audited with old/new date + reason."""
    v = (db.query(PelletVisit).options(joinedload(PelletVisit.milestones))
           .filter(PelletVisit.id == visit_id).first())
    if not v:
        raise HTTPException(status_code=404, detail="visit not found")
    if v.status in ("billed", "cancelled"):
        raise HTTPException(status_code=409,
                            detail=f"visit is {v.status} — cannot reschedule")
    new_dt = _parse_date(payload.new_date, "new_date")
    if not new_dt:
        raise HTTPException(status_code=422, detail="new_date required")
    old_dt = v.scheduled_date
    v.scheduled_date = new_dt
    # Setting a date auto-completes the 'scheduled' milestone if it was pending
    by = current_user.get("email") or "system"
    _complete_milestone(v, "scheduled", by)
    _audit(db, actor=by, action="visit_rescheduled",
            summary=(f"Rescheduled visit from "
                     f"{old_dt or 'unscheduled'} → {new_dt}"
                     + (f": {payload.reason}" if payload.reason else "")),
            detail={"visit_id": str(v.id),
                     "from": str(old_dt) if old_dt else None,
                     "to":   str(new_dt),
                     "reason": payload.reason})
    db.commit(); db.refresh(v)
    return _visit_dict(v)


class VisitCancelIn(BaseModel):
    reason: str             # required — for the audit trail


@router.post("/visits/{visit_id}/cancel")
def cancel_visit(visit_id: str, payload: VisitCancelIn,
                  db: Session = Depends(get_db),
                  current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.WORK))):
    """Cancel a visit before insertion. If any doses have been pulled from
    stock, they're returned to the visit's location's stock (same logic
    as /insert with outcome=cancelled). Sets status=cancelled."""
    v = (db.query(PelletVisit).options(joinedload(PelletVisit.milestones),
                                         joinedload(PelletVisit.doses)
                                           .joinedload(PelletVisitDose.dose_type))
           .filter(PelletVisit.id == visit_id).first())
    if not v:
        raise HTTPException(status_code=404, detail="visit not found")
    if v.status in ("billed", "cancelled"):
        raise HTTPException(status_code=409,
                            detail=f"visit is already {v.status}")
    if not (payload.reason and payload.reason.strip()):
        raise HTTPException(status_code=422, detail="reason required")

    by = current_user.get("email") or "system"

    # Return any pulled/added doses to the visit's location's stock.
    pulled_doses = [d for d in (v.doses or []) if d.status in ("pulled", "added")]
    if pulled_doses:
        location = _require_visit_location(v)
        for d in pulled_doses:
            if d.lot_id:
                stock = _get_or_create_stock(db, d.lot_id, location)
                _adjust_stock(db, stock, d.quantity)
                _audit(db, actor=by, action="dose_returned",
                        lot_id=d.lot_id, location=location,
                        delta_doses=d.quantity,
                        summary=(f"Returned {d.quantity} {d.dose_type.label} to stock "
                                 f"(visit cancelled)"),
                        detail={"visit_id": str(v.id), "visit_dose_id": str(d.id)})
            d.status = "returned"
            d.resolved_at = now_utc_naive()
            d.resolved_by = by

    v.status  = "cancelled"
    v.outcome = v.outcome or "cancelled"
    v.outcome_notes = payload.reason.strip()
    _audit(db, actor=by, action="visit_cancelled",
            summary=f"Visit cancelled: {payload.reason.strip()}",
            detail={"visit_id": str(v.id),
                     "reason": payload.reason.strip(),
                     "doses_returned": len(pulled_doses)})
    db.commit(); db.refresh(v)
    return _visit_dict(v)


# Klara payment lifecycle ----------------------------------------------

@router.post("/visits/{visit_id}/klara-sent")
def klara_sent(visit_id: str,
                 db: Session = Depends(get_db),
                 current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.WORK))):
    v = (db.query(PelletVisit).options(joinedload(PelletVisit.milestones))
           .filter(PelletVisit.id == visit_id).first())
    if not v:
        raise HTTPException(status_code=404, detail="visit not found")
    by = current_user.get("email") or "system"
    v.payment_status = "sent"
    v.klara_sent_at = now_utc_naive()
    v.klara_sent_by = by
    _audit(db, actor=by, action="klara_sent",
            summary=f"Klara payment link sent (${v.price_amount}) for visit {v.id}",
            detail={"visit_id": str(v.id), "amount": float(v.price_amount or 0)})
    db.commit(); db.refresh(v)
    return _visit_dict(v)


@router.post("/visits/{visit_id}/payment-collected")
def payment_collected(visit_id: str,
                        db: Session = Depends(get_db),
                        current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.WORK))):
    v = (db.query(PelletVisit).options(joinedload(PelletVisit.milestones))
           .filter(PelletVisit.id == visit_id).first())
    if not v:
        raise HTTPException(status_code=404, detail="visit not found")
    by = current_user.get("email") or "system"
    v.payment_status = "collected"
    v.payment_collected_at = now_utc_naive()
    v.payment_collected_by = by
    _complete_milestone(v, "payment_collected", by)
    _audit(db, actor=by, action="payment_collected",
            summary=f"Payment collected (${v.price_amount}) for visit {v.id}",
            detail={"visit_id": str(v.id), "amount": float(v.price_amount or 0)})
    db.commit(); db.refresh(v)
    return _visit_dict(v)


# Dose card management --------------------------------------------------

class DoseCardIn(BaseModel):
    doses: list[DoseLineIn]


@router.put("/visits/{visit_id}/dose-card")
def set_dose_card(visit_id: str, payload: DoseCardIn,
                    db: Session = Depends(get_db),
                    current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.WORK))):
    """Replace the PROPOSED dose card. Stock is auto-adjusted:
      • Old proposed doses with assigned lots → returned to stock
      • New proposed doses → FIFO lot auto-assigned + stock decremented
    Confirmed doses (inserted/added/reduced/returned/disposed) are
    untouched — only `pellet:manage` can edit those, via separate
    endpoints."""
    v = (db.query(PelletVisit).options(joinedload(PelletVisit.milestones),
                                         joinedload(PelletVisit.doses)
                                           .joinedload(PelletVisitDose.dose_type))
           .filter(PelletVisit.id == visit_id).first())
    if not v:
        raise HTTPException(status_code=404, detail="visit not found")
    if v.status in ("billed", "cancelled"):
        raise HTTPException(status_code=409,
                            detail=f"can't edit dose card on a {v.status} visit")
    by = current_user.get("email") or "system"
    location = _require_visit_location(v)

    # 1. Return all PROPOSED ('planned' or 'pulled') doses to stock
    for d in list(v.doses):
        if d.status in ("planned", "pulled") and d.lot_id:
            stock = _get_or_create_stock(db, d.lot_id, location)
            _adjust_stock(db, stock, d.quantity)
            _audit(db, actor=by, action="dose_proposed_return",
                    lot_id=d.lot_id, location=location,
                    delta_doses=d.quantity,
                    summary=(f"Returned {d.quantity} {d.dose_type.label if d.dose_type else ''} "
                             f"to stock (dose card replaced)"),
                    detail={"visit_id": str(v.id), "visit_dose_id": str(d.id)})
        if d.status in ("planned", "pulled"):
            db.delete(d)
    db.flush()

    # 2. Add new proposed doses + assign lot (FIFO unless caller specified
    #    a lot_id) + decrement stock
    short_components = []   # collect shortages for one combined error
    pending = []
    for i, d in enumerate(payload.doses, start=1):
        dt = db.query(PelletDoseType).filter(PelletDoseType.id == d.dose_type_id).first()
        if not dt:
            raise HTTPException(status_code=422,
                                detail=f"unknown dose type {d.dose_type_id}")
        if d.quantity <= 0:
            raise HTTPException(status_code=422, detail="quantity must be positive")
        if d.lot_id:
            pair = _specific_lot_with_stock(db, d.lot_id, dt.id, d.quantity, location)
        else:
            pair = _earliest_lot_with_stock(db, dt.id, d.quantity, location)
        if not pair:
            short_components.append(f"{d.quantity}× {dt.label}")
            continue
        pending.append((i, dt, d.quantity, pair))

    if short_components:
        raise HTTPException(
            status_code=409,
            detail=(f"Insufficient stock at {location} for: "
                    f"{', '.join(short_components)}. "
                    f"Choose an alternative or wait for restock."))

    for i, dt, qty, (lot, stock) in pending:
        _adjust_stock(db, stock, -(qty))
        db.add(PelletVisitDose(
            visit_id=v.id, dose_type_id=dt.id, quantity=qty,
            lot_id=lot.id, position=i, status="planned",
            pulled_at=now_utc_naive(), pulled_by=by,
        ))
        _audit(db, actor=by, action="dose_proposed_pull",
                lot_id=lot.id, location=location, delta_doses=-qty,
                summary=(f"Proposed dose pulled: {qty}× {dt.label} "
                         f"lot {lot.qualgen_lot_number}"),
                detail={"visit_id": str(v.id)})

    # Mark dose-card milestone done
    _complete_milestone(v, "dosed_in_dosagio", by)
    _complete_milestone(v, "dose_set_from_prior", by)
    # Auto-complete the 'bagged' milestone too, since stock has been allocated
    _complete_milestone(v, "bagged", by)

    _audit(db, actor=by, action="dose_card_set",
            summary=f"Proposed dose card set for visit {v.id} ({len(payload.doses)} lines)",
            detail={"visit_id": str(v.id),
                    "doses": [{"dose_type_id": d.dose_type_id, "qty": d.quantity}
                              for d in payload.doses]})
    db.commit(); db.refresh(v)
    return _visit_dict(v)


# Append a dose to ANY visit (past, active, or future) ---------------

class DoseAppendIn(BaseModel):
    dose_type_id: str
    quantity:     DoseQty = 1
    notes:        Optional[str] = None
    # Optional explicit lot override. When set, the named lot is used
    # instead of FIFO auto-pick.
    lot_id:       Optional[str] = None


@router.post("/visits/{visit_id}/doses", status_code=201)
def append_visit_dose(visit_id: str, payload: DoseAppendIn,
                        db: Session = Depends(get_db),
                        current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.WORK))):
    """Append a dose to a visit.
      - Active visit  → 'planned' (Proposed) — FIFO lot auto-assigned + stock decremented
      - Confirmed/billed visit → historical 'inserted' record (no stock impact)

    Editing on confirmed/billed visits requires `pellet:manage`."""
    v = (db.query(PelletVisit).options(joinedload(PelletVisit.doses)
                                           .joinedload(PelletVisitDose.dose_type),
                                         joinedload(PelletVisit.milestones))
           .filter(PelletVisit.id == visit_id).first())
    if not v:
        raise HTTPException(status_code=404, detail="visit not found")
    if v.status == "cancelled":
        raise HTTPException(status_code=409, detail="visit is cancelled")
    dt = db.query(PelletDoseType).filter(PelletDoseType.id == payload.dose_type_id).first()
    if not dt:
        raise HTTPException(status_code=422, detail="unknown dose type")
    if payload.quantity <= 0:
        raise HTTPException(status_code=422, detail="quantity must be positive")

    by = current_user.get("email") or "system"
    is_confirmed_visit = v.status in ("inserted", "billed")
    if is_confirmed_visit and not _is_admin(db, current_user):
        raise HTTPException(
            status_code=403,
            detail="This visit is confirmed — only a manager can edit doses.",
        )

    pos = max([d.position for d in v.doses], default=0) + 1
    location = _require_visit_location(v)

    if is_confirmed_visit:
        # Historical/manager-edit record — no stock impact
        d = PelletVisitDose(
            visit_id=v.id, dose_type_id=dt.id, quantity=payload.quantity,
            position=pos, status="inserted",
            resolved_at=v.inserted_at or now_utc_naive(),
            resolved_by=by, notes=payload.notes,
        )
        db.add(d); db.flush()
        _audit(db, actor=by, action="dose_appended_historical",
                summary=f"Historical dose appended ({payload.quantity}× {dt.label})",
                detail={"visit_id": str(v.id), "manager_override": True})
    else:
        # Proposed dose — caller can pin a lot, else FIFO + decrement stock
        if payload.lot_id:
            pair = _specific_lot_with_stock(
                db, payload.lot_id, dt.id, payload.quantity, location)
        else:
            pair = _earliest_lot_with_stock(db, dt.id, payload.quantity, location)
        if not pair:
            raise HTTPException(
                status_code=409,
                detail=(f"Insufficient stock at {location} for "
                        f"{payload.quantity}× {dt.label}."))
        lot, stock = pair
        _adjust_stock(db, stock, -(payload.quantity))
        d = PelletVisitDose(
            visit_id=v.id, dose_type_id=dt.id, quantity=payload.quantity,
            lot_id=lot.id, position=pos, status="planned",
            pulled_at=now_utc_naive(), pulled_by=by,
            notes=payload.notes,
        )
        db.add(d); db.flush()
        _audit(db, actor=by, action="dose_proposed_pull",
                lot_id=lot.id, location=location,
                delta_doses=-payload.quantity,
                summary=(f"Proposed dose pulled: {payload.quantity}× {dt.label} "
                         f"lot {lot.qualgen_lot_number}"),
                detail={"visit_id": str(v.id), "visit_dose_id": str(d.id)})

    db.commit(); db.refresh(v)
    return _visit_dict(v)


class DoseLotChangeIn(BaseModel):
    """Body for PATCH /visits/{visit_id}/doses/{dose_id} — swap the lot
    on a still-proposed (planned/pulled) dose. Mutates stock: returns
    the old lot's reserve and pulls from the new lot."""
    lot_id: str


@router.patch("/visits/{visit_id}/doses/{dose_id}")
def change_dose_lot(visit_id: str, dose_id: str, payload: DoseLotChangeIn,
                      db: Session = Depends(get_db),
                      current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.WORK))):
    """Swap the lot assigned to a proposed (planned/pulled) dose.
    Returns the old lot's reserve and pulls from the new lot. Provider
    in-room reassignment — common when the provider wants a different
    expiration or label. Confirmed doses require `pellet:manage`."""
    d = (db.query(PelletVisitDose).options(joinedload(PelletVisitDose.dose_type))
           .filter(PelletVisitDose.id == dose_id,
                   PelletVisitDose.visit_id == visit_id).first())
    if not d:
        raise HTTPException(status_code=404, detail="dose entry not found")
    v = db.query(PelletVisit).filter(PelletVisit.id == visit_id).first()
    if not v:
        raise HTTPException(status_code=404, detail="visit not found")
    if d.status in CONFIRMED_DOSE_STATUSES and not _is_admin(db, current_user):
        raise HTTPException(
            status_code=403,
            detail="This dose is already confirmed — only a manager can edit it.")
    if d.status not in ("planned", "pulled"):
        raise HTTPException(
            status_code=409,
            detail=(f"Only proposed (planned/pulled) doses can have their lot "
                    f"swapped (this one is {d.status})."))

    by = current_user.get("email") or "system"
    location = _require_visit_location(v)

    new_lot, new_stock = _specific_lot_with_stock(
        db, payload.lot_id, d.dose_type_id, d.quantity, location)

    old_lot_id = d.lot_id
    if str(old_lot_id) == str(new_lot.id):
        raise HTTPException(
            status_code=409,
            detail="The dose is already pulled from that lot.")

    # Return the old lot's reserve
    if old_lot_id is not None:
        old_stock = _get_or_create_stock(db, old_lot_id, location)
        _adjust_stock(db, old_stock, d.quantity)
        _audit(db, actor=by, action="dose_proposed_return",
               lot_id=old_lot_id, location=location, delta_doses=d.quantity,
               summary=(f"Returned {d.quantity} {d.dose_type.label if d.dose_type else ''} "
                          f"to stock (lot swap)"),
               detail={"visit_id": str(v.id), "visit_dose_id": str(d.id)})

    # Pull from the new lot
    _adjust_stock(db, new_stock, -(d.quantity))
    d.lot_id = new_lot.id
    d.pulled_at = now_utc_naive()
    d.pulled_by = by

    _audit(db, actor=by, action="dose_proposed_pull",
           lot_id=new_lot.id, location=location, delta_doses=-d.quantity,
           summary=(f"Lot swap: pulled {d.quantity}× "
                      f"{d.dose_type.label if d.dose_type else ''} "
                      f"from lot {new_lot.qualgen_lot_number}"),
           detail={"visit_id": str(v.id), "visit_dose_id": str(d.id),
                    "old_lot_id": str(old_lot_id) if old_lot_id else None})

    db.commit(); db.refresh(d)
    return {
        "dose_id":   str(d.id),
        "lot_id":    str(d.lot_id),
        "qualgen_lot_number": new_lot.qualgen_lot_number,
        "expiration_date": (new_lot.expiration_date.isoformat()
                              if new_lot.expiration_date else None),
    }


class DoseLotIdentifyIn(BaseModel):
    """Body for POST /visits/{visit_id}/doses/{dose_id}/identify-lot —
    retroactively record which lot was actually used on a confirmed
    (inserted/added/etc.) dose. Stock is rebalanced: if a different
    lot was previously debited, +1 is returned to it and -1 is taken
    from the new lot."""
    lot_id: str
    reason: str   # e.g. "lot missed at pull-time, identified from paper log"


@router.post("/visits/{visit_id}/doses/{dose_id}/identify-lot")
def identify_dose_lot(visit_id: str, dose_id: str, payload: DoseLotIdentifyIn,
                       db: Session = Depends(get_db),
                       current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.MANAGE))):
    """Retroactively identify (or correct) the lot recorded on a
    confirmed dose. Used when the lot wasn't captured at pre-bag time
    and staff later identified it from the paper manifest or photo.

    Differs from PATCH /doses/{dose_id} (change_dose_lot) in that:
      - the dose may already be in a terminal status (inserted, added,
        reduced, returned, disposed)
      - the audit row records a retroactive identification, not a
        provider-room swap

    Stock rebalancing:
      - If the dose currently has a non-NULL lot_id, that lot was
        debited at pull-time and shouldn't have been; +d.quantity is
        added back to its stock at the visit's location.
      - The new lot is debited -d.quantity (since that's the lot the
        device actually came from). If the dose had no recorded lot
        before, only the debit happens — a future inventory count
        will catch any historical drift on an unidentified lot."""
    if not (payload.reason or "").strip():
        raise HTTPException(status_code=422, detail="reason is required")

    d = (db.query(PelletVisitDose)
           .options(joinedload(PelletVisitDose.dose_type))
           .filter(PelletVisitDose.id == dose_id,
                   PelletVisitDose.visit_id == visit_id).first())
    if not d:
        raise HTTPException(status_code=404, detail="dose entry not found")

    v = db.query(PelletVisit).filter(PelletVisit.id == visit_id).first()
    if not v:
        raise HTTPException(status_code=404, detail="visit not found")

    new_lot = (db.query(PelletLot)
                 .filter(PelletLot.id == payload.lot_id).first())
    if not new_lot:
        raise HTTPException(status_code=404, detail="lot not found")
    if new_lot.dose_type_id != d.dose_type_id:
        raise HTTPException(status_code=409,
            detail="lot dose type does not match the dose's dose type")

    old_lot_id = d.lot_id
    if str(old_lot_id) == str(new_lot.id):
        raise HTTPException(status_code=409,
            detail="the dose is already identified with that lot")

    location = _require_visit_location(v)
    by = current_user.get("email") or "system"

    # Stock rebalance — return to the previously-debited lot first, then
    # debit the newly-identified lot.
    if old_lot_id is not None:
        old_stock = _get_or_create_stock(db, old_lot_id, location)
        _adjust_stock(db, old_stock, d.quantity)
        _audit(db, actor=by, action="dose_lot_retro_return",
               lot_id=old_lot_id, location=location, delta_doses=d.quantity,
               summary=(f"Retro lot fix: returned {d.quantity}× "
                        f"{d.dose_type.label if d.dose_type else ''} "
                        f"to stock (was debited in error)"),
               detail={"visit_id": str(v.id), "visit_dose_id": str(d.id),
                       "new_lot_id": str(new_lot.id),
                       "reason": payload.reason.strip()})

    new_stock = _get_or_create_stock(db, new_lot.id, location)
    _adjust_stock(db, new_stock, -(d.quantity))
    _audit(db, actor=by, action="dose_lot_retro_debit",
           lot_id=new_lot.id, location=location, delta_doses=-d.quantity,
           summary=(f"Retro lot fix: debited {d.quantity}× "
                    f"{d.dose_type.label if d.dose_type else ''} "
                    f"from lot {new_lot.qualgen_lot_number}"),
           detail={"visit_id": str(v.id), "visit_dose_id": str(d.id),
                   "previous_lot_id": str(old_lot_id) if old_lot_id else None,
                   "reason": payload.reason.strip()})

    d.lot_id = new_lot.id
    _audit(db, actor=by, action="dose_lot_retroactive_identification",
           lot_id=new_lot.id,
           summary=(f"Retroactively identified lot for "
                    f"{d.dose_type.label if d.dose_type else 'dose'} "
                    f"on visit {visit_id}: {new_lot.qualgen_lot_number}"),
           detail={"visit_id": str(visit_id),
                   "visit_dose_id": str(d.id),
                   "previous_lot_id": str(old_lot_id) if old_lot_id else None,
                   "new_lot_id":      str(new_lot.id),
                   "new_lot_number":  new_lot.qualgen_lot_number,
                   "dose_status_at_change": d.status,
                   "reason": payload.reason.strip()})
    db.commit(); db.refresh(d)
    return {
        "dose_id": str(d.id),
        "lot_id":  str(d.lot_id),
        "qualgen_lot_number": new_lot.qualgen_lot_number,
        "previous_lot_id": str(old_lot_id) if old_lot_id else None,
        "new_lot_stock": new_stock.doses_on_hand,
    }


@router.delete("/visits/{visit_id}/doses/{dose_id}", status_code=204)
def delete_visit_dose(visit_id: str, dose_id: str,
                        db: Session = Depends(get_db),
                        current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.WORK))):
    """Delete a dose entry. Behavior depends on status:
      - planned/pulled (Proposed) → returns to stock automatically
      - inserted/added/returned/reduced/disposed (Confirmed)
        → requires `pellet:manage` (manager/super-admin only)"""
    d = (db.query(PelletVisitDose).options(joinedload(PelletVisitDose.dose_type))
           .filter(PelletVisitDose.id == dose_id,
                   PelletVisitDose.visit_id == visit_id).first())
    if not d:
        raise HTTPException(status_code=404, detail="dose entry not found")
    by = current_user.get("email") or "system"

    if d.status in CONFIRMED_DOSE_STATUSES and not _is_admin(db, current_user):
        raise HTTPException(
            status_code=403,
            detail=(f"This dose is confirmed ({d.status}) — only a manager "
                    f"or super-admin can delete it."),
        )

    # Return to stock if this dose was holding stock
    v = db.query(PelletVisit).filter(PelletVisit.id == visit_id).first()
    if not v:
        raise HTTPException(status_code=404, detail="visit not found")
    location = _require_visit_location(v)
    if d.status in ("planned", "pulled") and d.lot_id:
        stock = _get_or_create_stock(db, d.lot_id, location)
        _adjust_stock(db, stock, d.quantity)
        _audit(db, actor=by, action="dose_proposed_return",
                lot_id=d.lot_id, location=location,
                delta_doses=d.quantity,
                summary=(f"Returned {d.quantity}× "
                         f"{d.dose_type.label if d.dose_type else 'dose'} to stock "
                         f"(proposed dose deleted)"),
                detail={"visit_id": visit_id, "visit_dose_id": dose_id})

    db.delete(d)
    _audit(db, actor=by, action="dose_deleted",
            summary=f"Deleted dose {dose_id} (status={d.status})",
            detail={"manager_override": d.status in CONFIRMED_DOSE_STATUSES})
    db.commit()
    return


# Bag fill — pull pellets from a lot at a location -------------------

class BagFillLineIn(BaseModel):
    visit_dose_id: str
    lot_id:        str


class BagFillIn(BaseModel):
    lines:    list[BagFillLineIn]
    # location is sourced from the visit row (see _require_visit_location).
    # Field is kept as Optional only to absorb older clients that still send
    # it; the visit's stored location wins regardless. (Fable audit #8.)
    location: Optional[str] = None


@router.post("/visits/{visit_id}/fill-bag")
def fill_bag(visit_id: str, payload: BagFillIn,
               db: Session = Depends(get_db),
               current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.WORK))):
    """Tattiana pulls each planned dose from a specific lot and stages
    them in the patient's bag. Decrements PelletStock per line and writes
    audit rows."""
    v = (db.query(PelletVisit).options(joinedload(PelletVisit.milestones),
                                         joinedload(PelletVisit.doses)
                                           .joinedload(PelletVisitDose.dose_type))
           .filter(PelletVisit.id == visit_id).first())
    if not v:
        raise HTTPException(status_code=404, detail="visit not found")
    visit_location = _require_visit_location(v)

    by = current_user.get("email") or "system"
    by_dose = {str(d.id): d for d in v.doses}

    for line in payload.lines:
        d = by_dose.get(line.visit_dose_id)
        if not d:
            raise HTTPException(status_code=404,
                                detail=f"visit dose {line.visit_dose_id} not found")
        if d.status != "planned":
            raise HTTPException(status_code=409,
                                detail=f"dose {d.id} status is {d.status}, expected planned")
        lot = db.query(PelletLot).filter(PelletLot.id == line.lot_id).first()
        if not lot:
            raise HTTPException(status_code=404, detail=f"lot {line.lot_id} not found")
        if lot.dose_type_id != d.dose_type_id:
            raise HTTPException(status_code=422,
                                detail=f"lot {lot.qualgen_lot_number} doesn't match dose {d.dose_type.label}")

        stock = _get_or_create_stock(db, lot.id, visit_location)
        if stock.doses_on_hand < d.quantity:
            raise HTTPException(status_code=409,
                                detail=f"Insufficient stock for {d.dose_type.label} "
                                       f"lot {lot.qualgen_lot_number} at {visit_location}: "
                                       f"have {stock.doses_on_hand}, need {d.quantity}")
        _adjust_stock(db, stock, -(d.quantity))
        d.lot_id = lot.id
        d.status = "pulled"
        d.pulled_at = now_utc_naive()
        d.pulled_by = by

        _audit(db, actor=by, action="dose_pulled",
                lot_id=lot.id, location=visit_location,
                delta_doses=-d.quantity,
                summary=f"Pulled {d.quantity} {d.dose_type.label} lot {lot.qualgen_lot_number} for visit {v.id}",
                detail={"visit_id": str(v.id), "patient": v.patient.patient_name if v.patient else None,
                        "visit_dose_id": str(d.id)})

    # Mark bagged milestone
    all_pulled = all(d.status != "planned" for d in v.doses)
    if all_pulled:
        v.bagged_at = now_utc_naive()
        v.bagged_by = by
        _complete_milestone(v, "bagged", by)

    db.commit(); db.refresh(v)
    return _visit_dict(v)


# Insertion outcome ---------------------------------------------------

class InsertionOutcomeIn(BaseModel):
    outcome:           str  # perfect | rescheduled | cancelled
    notes:             Optional[str] = None


@router.post("/visits/{visit_id}/insert")
def record_insertion(visit_id: str, payload: InsertionOutcomeIn,
                       db: Session = Depends(get_db),
                       current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.WORK))):
    """Record the insertion outcome.
      perfect     — all pulled doses become inserted (terminal for those rows)
      rescheduled — all pulled doses become 'returned' (added back to stock)
      cancelled   — same as rescheduled but visit closes

    Mid-procedure add/reduce/disposal happen via separate endpoints.
    """
    v = (db.query(PelletVisit).options(joinedload(PelletVisit.milestones),
                                         joinedload(PelletVisit.doses)
                                           .joinedload(PelletVisitDose.dose_type))
           .filter(PelletVisit.id == visit_id).first())
    if not v:
        raise HTTPException(status_code=404, detail="visit not found")
    if payload.outcome not in ("perfect", "rescheduled", "cancelled"):
        raise HTTPException(status_code=422,
                            detail="outcome must be perfect, rescheduled, or cancelled")
    by = current_user.get("email") or "system"

    if payload.outcome == "perfect":
        for d in v.doses:
            if d.status in ("pulled", "added"):
                d.status = "inserted"
                d.resolved_at = now_utc_naive()
                d.resolved_by = by
        v.inserted_at = now_utc_naive()
        v.inserted_by = by
        v.outcome = "perfect"
        v.status = "inserted"
        _complete_milestone(v, "inserted", by)

    else:  # rescheduled / cancelled
        # Return all pulled doses to the visit's location's stock.
        location = _require_visit_location(v)
        for d in v.doses:
            if d.status in ("pulled", "added"):
                if d.lot_id:
                    stock = _get_or_create_stock(db, d.lot_id, location)
                    _adjust_stock(db, stock, d.quantity)
                    _audit(db, actor=by, action="dose_returned",
                            lot_id=d.lot_id, location=location,
                            delta_doses=d.quantity,
                            summary=f"Returned {d.quantity} {d.dose_type.label} to stock "
                                    f"({payload.outcome})",
                            detail={"visit_id": str(v.id), "visit_dose_id": str(d.id)})
                d.status = "returned"
                d.resolved_at = now_utc_naive()
                d.resolved_by = by
        v.outcome = payload.outcome
        v.status = "cancelled" if payload.outcome == "cancelled" else "rescheduled"

    v.outcome_notes = payload.notes
    _audit(db, actor=by, action="visit_outcome",
            summary=f"Visit outcome: {payload.outcome}",
            detail={"visit_id": str(v.id), "notes": payload.notes})
    db.commit(); db.refresh(v)
    return _visit_dict(v)


@router.post("/visits/{visit_id}/confirm-as-planned")
def confirm_doses_as_planned(visit_id: str,
                               db: Session = Depends(get_db),
                               current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.WORK))):
    """One-click confirmation: flip every Proposed (planned/pulled) dose on
    this visit to `inserted`. For doses that are still `planned` (never
    pulled from the safe), this also FIFO-assigns a lot and decrements stock
    in the same transaction — useful when a provider performed the
    insertion straight from the plan without going through Fill Bag.

    Fails (409) if no FIFO lot is available for any still-planned dose.
    """
    v = (db.query(PelletVisit)
           .options(joinedload(PelletVisit.doses).joinedload(PelletVisitDose.dose_type),
                    joinedload(PelletVisit.milestones))
           .filter(PelletVisit.id == visit_id).first())
    if not v:
        raise HTTPException(status_code=404, detail="visit not found")
    if v.status in ("billed", "cancelled"):
        raise HTTPException(status_code=409,
                            detail=f"visit is {v.status} — cannot confirm")

    proposed = [d for d in (v.doses or []) if d.status in ("planned", "pulled")]
    if not proposed:
        raise HTTPException(status_code=409,
                            detail="no proposed doses on this visit")

    by = current_user.get("email") or "system"
    location = _require_visit_location(v)
    now = now_utc_naive()

    # First pass: validate every planned dose has a FIFO lot available.
    for d in proposed:
        if d.status == "planned":
            pair = _earliest_lot_with_stock(db, d.dose_type_id, d.quantity, location)
            if not pair:
                raise HTTPException(
                    status_code=409,
                    detail=(f"Not enough on-hand {d.dose_type.label if d.dose_type else ''} "
                            f"at {location} to confirm — pull aborted. Adjust the dose card "
                            f"or transfer stock first."))

    # Second pass: assign + decrement + mark inserted.
    pulled_count = 0
    for d in proposed:
        if d.status == "planned":
            # Stock can be consumed between the validate pass and here by a
            # concurrent visit. Surface a clean 409 instead of a TypeError
            # 500 on the None return. (Fable audit #11.)
            pair = _earliest_lot_with_stock(db, d.dose_type_id, d.quantity, location)
            if not pair:
                raise HTTPException(
                    status_code=409,
                    detail=(f"Stock for {d.dose_type.label if d.dose_type else ''} at "
                            f"{location} drained before the pull could complete — try again."))
            lot, stock = pair
            d.lot_id = lot.id
            _adjust_stock(db, stock, -(d.quantity))
            d.pulled_at = now
            d.pulled_by = by
            _audit(db, actor=by, action="dose_proposed_pull",
                   lot_id=lot.id, location=location,
                   delta_doses=-d.quantity,
                   summary=(f"Pulled {d.quantity} {d.dose_type.label if d.dose_type else ''} "
                            f"lot {lot.qualgen_lot_number} → confirm-as-planned for visit"),
                   detail={"visit_id": str(v.id), "visit_dose_id": str(d.id)})
            pulled_count += 1
        d.status = "inserted"
        d.resolved_at = now
        d.resolved_by = by

    v.inserted_at = now
    v.inserted_by = by
    v.outcome = "perfect"
    v.status = "inserted"
    _complete_milestone(v, "inserted", by)

    _audit(db, actor=by, action="visit_confirmed_as_planned",
            summary=(f"Confirmed {len(proposed)} dose(s) as planned for visit "
                    + (v.patient.patient_name if v.patient else "")),
            detail={"visit_id": str(v.id), "doses_confirmed": len(proposed),
                    "doses_freshly_pulled": pulled_count})
    db.commit(); db.refresh(v)
    return _visit_dict(v)


# Per-line confirm-insertion --------------------------------------------
#
# This is the in-room "what was actually inserted" workflow: each bagged
# dose gets one of three actions, plus the provider can add brand-new
# doses (mid-procedure additions) — all in one transaction.

_CONFIRM_ACTIONS = {"insert", "return", "swap"}


class ConfirmInsertLine(BaseModel):
    """One decision per existing planned/pulled dose.

    action="insert" → flip to 'inserted'; stock unchanged
                       (reservation = real consumption)
    action="return" → flip to 'returned'; stock += quantity
    action="swap"   → mark original 'returned' (stock += original qty),
                      then create a fresh 'inserted' dose with new dose
                      type / lot / quantity. Lot is FIFO if new_lot_id
                      omitted; quantity defaults to original.
    """
    dose_id:           str
    action:            str
    new_dose_type_id:  Optional[str] = None
    new_lot_id:        Optional[str] = None
    new_quantity:      Optional[DoseQty] = None


class ConfirmInsertAddition(BaseModel):
    """A brand-new dose the provider added in-room."""
    dose_type_id: str
    quantity:     DoseQty = 1
    lot_id:       Optional[str] = None
    notes:        Optional[str] = None


class ConfirmInsertionIn(BaseModel):
    lines:        list[ConfirmInsertLine] = []
    additions:    list[ConfirmInsertAddition] = []
    notes:        Optional[str] = None


@router.post("/visits/{visit_id}/confirm-insertion")
def confirm_insertion(visit_id: str, payload: ConfirmInsertionIn,
                        db: Session = Depends(get_db),
                        current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.WORK))):
    """Per-line confirmation of what was actually inserted vs. bagged.

    Each line targets one existing planned/pulled dose with insert / return
    / swap. Additions create brand-new 'inserted' rows. All transitions
    are atomic and audited.

    After commit, the visit transitions to status='inserted' (or stays
    there if already inserted)."""
    v = (db.query(PelletVisit)
           .options(joinedload(PelletVisit.doses).joinedload(PelletVisitDose.dose_type),
                    joinedload(PelletVisit.milestones))
           .filter(PelletVisit.id == visit_id).first())
    if not v:
        raise HTTPException(status_code=404, detail="visit not found")
    if v.status in ("billed", "cancelled"):
        raise HTTPException(status_code=409,
                            detail=f"visit is {v.status} — cannot confirm")

    by = current_user.get("email") or "system"
    location = _require_visit_location(v)
    now = now_utc_naive()
    by_dose = {str(d.id): d for d in (v.doses or [])}

    # ── 1. Validate every line up front (so partial commits don't happen) ──
    for line in payload.lines:
        if line.action not in _CONFIRM_ACTIONS:
            raise HTTPException(status_code=422,
                                detail=f"action must be one of {sorted(_CONFIRM_ACTIONS)}")
        d = by_dose.get(line.dose_id)
        if not d:
            raise HTTPException(status_code=404,
                                detail=f"dose {line.dose_id} not on this visit")
        if d.status not in ("planned", "pulled"):
            raise HTTPException(
                status_code=409,
                detail=(f"dose {d.id} is {d.status}; only planned/pulled doses "
                        "can be confirmed via this endpoint."))
        if line.action == "swap":
            if not line.new_dose_type_id:
                raise HTTPException(status_code=422,
                                    detail="swap requires new_dose_type_id")
            new_dt = (db.query(PelletDoseType)
                        .filter(PelletDoseType.id == line.new_dose_type_id).first())
            if not new_dt:
                raise HTTPException(status_code=404,
                                    detail=f"dose type {line.new_dose_type_id} not found")
            new_qty = line.new_quantity if line.new_quantity is not None else d.quantity
            if new_qty <= 0:
                raise HTTPException(status_code=422, detail="new_quantity must be > 0")
            if line.new_lot_id:
                _specific_lot_with_stock(db, line.new_lot_id, new_dt.id, new_qty, location)
            else:
                if not _earliest_lot_with_stock(db, new_dt.id, new_qty, location):
                    raise HTTPException(
                        status_code=409,
                        detail=(f"Insufficient stock at {location} for "
                                f"{new_qty}× {new_dt.label}."))

    for add in payload.additions:
        new_dt = (db.query(PelletDoseType)
                    .filter(PelletDoseType.id == add.dose_type_id).first())
        if not new_dt:
            raise HTTPException(status_code=404,
                                detail=f"dose type {add.dose_type_id} not found")
        if add.quantity <= 0:
            raise HTTPException(status_code=422, detail="quantity must be > 0")
        if add.lot_id:
            _specific_lot_with_stock(db, add.lot_id, new_dt.id, add.quantity, location)
        else:
            if not _earliest_lot_with_stock(db, new_dt.id, add.quantity, location):
                raise HTTPException(
                    status_code=409,
                    detail=(f"Insufficient stock at {location} for "
                            f"{add.quantity}× {new_dt.label}."))

    # ── 2. Apply ──
    next_pos = max([d.position for d in (v.doses or [])], default=0)

    stats = {"inserted": 0, "returned": 0, "swapped": 0, "added": 0}

    for line in payload.lines:
        d = by_dose[line.dose_id]
        if line.action == "insert":
            d.status      = "inserted"
            d.resolved_at = now
            d.resolved_by = by
            _audit(db, actor=by, action="dose_inserted",
                   lot_id=d.lot_id, location=location, delta_doses=0,
                   summary=(f"Confirmed insertion: {d.quantity}× "
                              f"{d.dose_type.label if d.dose_type else ''}"),
                   detail={"visit_id": str(v.id), "visit_dose_id": str(d.id)})
            stats["inserted"] += 1

        elif line.action == "return":
            if d.lot_id:
                old_stock = _get_or_create_stock(db, d.lot_id, location)
                _adjust_stock(db, old_stock, d.quantity)
            d.status      = "returned"
            d.resolved_at = now
            d.resolved_by = by
            _audit(db, actor=by, action="dose_returned",
                   lot_id=d.lot_id, location=location,
                   delta_doses=d.quantity,
                   summary=(f"Bagged dose returned to stock: "
                              f"{d.quantity}× "
                              f"{d.dose_type.label if d.dose_type else ''}"),
                   detail={"visit_id": str(v.id), "visit_dose_id": str(d.id)})
            stats["returned"] += 1

        else:  # swap
            # Return the original
            if d.lot_id:
                old_stock = _get_or_create_stock(db, d.lot_id, location)
                _adjust_stock(db, old_stock, d.quantity)
            d.status      = "returned"
            d.resolved_at = now
            d.resolved_by = by
            _audit(db, actor=by, action="dose_returned",
                   lot_id=d.lot_id, location=location, delta_doses=d.quantity,
                   summary=(f"Swap — original returned: "
                              f"{d.quantity}× "
                              f"{d.dose_type.label if d.dose_type else ''}"),
                   detail={"visit_id": str(v.id), "visit_dose_id": str(d.id)})
            # Insert the replacement
            new_dt = (db.query(PelletDoseType)
                        .filter(PelletDoseType.id == line.new_dose_type_id).first())
            new_qty = line.new_quantity if line.new_quantity is not None else d.quantity
            if line.new_lot_id:
                lot, stock = _specific_lot_with_stock(
                    db, line.new_lot_id, new_dt.id, new_qty, location)
            else:
                pair = _earliest_lot_with_stock(db, new_dt.id, new_qty, location)
                if not pair:
                    raise HTTPException(
                        status_code=409,
                        detail=(f"Stock for {new_dt.label} at {location} drained "
                                f"before the swap could complete — try again."))
                lot, stock = pair
            _adjust_stock(db, stock, -(new_qty))
            next_pos += 1
            new_d = PelletVisitDose(
                visit_id=v.id, dose_type_id=new_dt.id, quantity=new_qty,
                lot_id=lot.id, position=next_pos, status="inserted",
                pulled_at=now, pulled_by=by,
                resolved_at=now, resolved_by=by,
                notes=f"Swap from {d.dose_type.label if d.dose_type else ''}",
            )
            db.add(new_d); db.flush()
            _audit(db, actor=by, action="dose_added",
                   lot_id=lot.id, location=location, delta_doses=-new_qty,
                   summary=(f"Swap — inserted {new_qty}× {new_dt.label} "
                              f"from lot {lot.qualgen_lot_number}"),
                   detail={"visit_id": str(v.id), "visit_dose_id": str(new_d.id),
                            "swapped_from_dose_id": str(d.id)})
            stats["swapped"] += 1

    for add in payload.additions:
        new_dt = (db.query(PelletDoseType)
                    .filter(PelletDoseType.id == add.dose_type_id).first())
        if add.lot_id:
            lot, stock = _specific_lot_with_stock(
                db, add.lot_id, new_dt.id, add.quantity, location)
        else:
            pair = _earliest_lot_with_stock(db, new_dt.id, add.quantity, location)
            if not pair:
                raise HTTPException(
                    status_code=409,
                    detail=(f"Stock for {new_dt.label} at {location} drained "
                            f"before the additional dose could be pulled — try again."))
            lot, stock = pair
        _adjust_stock(db, stock, -(add.quantity))
        next_pos += 1
        new_d = PelletVisitDose(
            visit_id=v.id, dose_type_id=new_dt.id, quantity=add.quantity,
            lot_id=lot.id, position=next_pos, status="inserted",
            pulled_at=now, pulled_by=by,
            resolved_at=now, resolved_by=by,
            notes=add.notes,
        )
        db.add(new_d); db.flush()
        _audit(db, actor=by, action="dose_added",
               lot_id=lot.id, location=location, delta_doses=-add.quantity,
               summary=(f"Provider added in-room: {add.quantity}× "
                          f"{new_dt.label} lot {lot.qualgen_lot_number}"),
               detail={"visit_id": str(v.id), "visit_dose_id": str(new_d.id)})
        stats["added"] += 1

    # ── 3. Visit-level transition ──
    if not v.inserted_at:
        v.inserted_at = now
        v.inserted_by = by
    v.outcome  = "perfect"
    v.status   = "inserted"
    _complete_milestone(v, "inserted", by)

    if payload.notes:
        v.outcome_notes = (
            (v.outcome_notes + " | " if v.outcome_notes else "")
            + payload.notes
        )

    _audit(db, actor=by, action="visit_confirm_insertion",
           summary=(f"Per-line insertion confirmed for "
                      f"{v.patient.patient_name if v.patient else ''}: "
                      f"{stats['inserted']} kept, {stats['returned']} returned, "
                      f"{stats['swapped']} swapped, {stats['added']} added"),
           detail={"visit_id": str(v.id), **stats})

    db.commit(); db.refresh(v)
    return _visit_dict(v)


# Mid-procedure add / reduce / dispose --------------------------------

class MidAddIn(BaseModel):
    dose_type_id: str
    lot_id:       str
    quantity:     DoseQty = 1
    # location is sourced from the visit row (Fable audit #8). Kept
    # Optional only to absorb older clients still sending it.
    location:     Optional[str] = None
    notes:        Optional[str] = None


@router.post("/visits/{visit_id}/add-dose")
def add_dose_mid_procedure(visit_id: str, payload: MidAddIn,
                              db: Session = Depends(get_db),
                              current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.WORK))):
    """Provider decides mid-insertion to add an additional pellet. Pulled
    from the safe, decremented from stock, marked 'added' on the visit."""
    v = (db.query(PelletVisit).options(joinedload(PelletVisit.doses))
           .filter(PelletVisit.id == visit_id).first())
    if not v:
        raise HTTPException(status_code=404, detail="visit not found")
    visit_location = _require_visit_location(v)
    dt = db.query(PelletDoseType).filter(PelletDoseType.id == payload.dose_type_id).first()
    if not dt:
        raise HTTPException(status_code=404, detail="dose type not found")
    lot = db.query(PelletLot).filter(PelletLot.id == payload.lot_id).first()
    if not lot or lot.dose_type_id != dt.id:
        raise HTTPException(status_code=422, detail="lot doesn't match dose type")

    by = current_user.get("email") or "system"
    stock = _get_or_create_stock(db, lot.id, visit_location)
    if stock.doses_on_hand < payload.quantity:
        raise HTTPException(status_code=409,
                            detail=f"Insufficient stock at {visit_location}")
    _adjust_stock(db, stock, -(payload.quantity))

    pos = max([d.position for d in v.doses], default=0) + 1
    d = PelletVisitDose(
        visit_id=v.id, dose_type_id=dt.id, lot_id=lot.id,
        quantity=payload.quantity, position=pos,
        status="added", pulled_at=now_utc_naive(), pulled_by=by,
        notes=payload.notes,
    )
    db.add(d); db.flush()
    _audit(db, actor=by, action="dose_added_mid",
            lot_id=lot.id, location=visit_location,
            delta_doses=-payload.quantity,
            summary=f"Mid-procedure add: {payload.quantity} {dt.label} for visit {v.id}",
            detail={"visit_id": str(v.id), "visit_dose_id": str(d.id),
                    "notes": payload.notes})
    db.commit(); db.refresh(v)
    return _visit_dict(v)


class VisitDoseDisposalIn(BaseModel):
    visit_dose_id: str
    reason:        str   # dropped | broken | other
    witness_user:  Optional[str] = None
    # location is sourced from the visit row (Fable audit #8). Kept
    # Optional only to absorb older clients still sending it.
    location:      Optional[str] = None
    notes:         Optional[str] = None


@router.post("/visits/{visit_id}/dispose-dose")
def dispose_visit_dose(visit_id: str, payload: VisitDoseDisposalIn,
                          db: Session = Depends(get_db),
                          current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.WORK))):
    """A pellet from this visit's bag is dropped/broken → biohazard.
    Already-pulled (stock already decremented), so we just write a
    PelletDisposal row + flip the dose status. For Schedule III, witness
    is required."""
    if payload.reason not in ("dropped", "broken", "other"):
        raise HTTPException(status_code=422, detail="invalid reason")
    v = (db.query(PelletVisit).options(joinedload(PelletVisit.doses)
                                         .joinedload(PelletVisitDose.dose_type),
                                         joinedload(PelletVisit.doses)
                                           .joinedload(PelletVisitDose.lot))
           .filter(PelletVisit.id == visit_id).first())
    if not v:
        raise HTTPException(status_code=404, detail="visit not found")
    visit_location = _require_visit_location(v)
    d = next((x for x in v.doses if str(x.id) == payload.visit_dose_id), None)
    if not d:
        raise HTTPException(status_code=404, detail="visit dose not found")
    if d.status not in ("pulled", "added"):
        raise HTTPException(status_code=409,
                            detail=f"dose status is {d.status}, can't dispose")
    by = current_user.get("email") or "system"
    is_controlled = bool(d.dose_type and d.dose_type.is_controlled)
    witness = _validate_witness(db, payload.witness_user, by,
                                  controlled=is_controlled)

    disposal = PelletDisposal(
        lot_id=d.lot_id, location=visit_location, doses=d.quantity,
        reason=payload.reason, performed_by=by,
        witness_user=witness or None, notes=payload.notes,
    )
    db.add(disposal); db.flush()
    d.status = "disposed"
    d.resolved_at = now_utc_naive()
    d.resolved_by = by

    _audit(db, actor=by, action="dose_disposed_mid",
            lot_id=d.lot_id, disposal_id=disposal.id,
            location=visit_location,
            summary=f"Mid-procedure disposal: {d.quantity} {d.dose_type.label} ({payload.reason})",
            detail={"visit_id": str(v.id), "visit_dose_id": str(d.id),
                    "reason": payload.reason, "witness": witness,
                    "controlled": is_controlled})
    db.commit(); db.refresh(v)
    return _visit_dict(v)


# Bill close-out ------------------------------------------------------

class BillIn(BaseModel):
    claim_number: str


@router.post("/visits/{visit_id}/bill")
def bill_visit(visit_id: str, payload: BillIn,
                 db: Session = Depends(get_db),
                 current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.WORK))):
    v = (db.query(PelletVisit).options(joinedload(PelletVisit.milestones))
           .filter(PelletVisit.id == visit_id).first())
    if not v:
        raise HTTPException(status_code=404, detail="visit not found")
    if not payload.claim_number.strip():
        raise HTTPException(status_code=422, detail="claim_number required")
    by = current_user.get("email") or "system"
    v.claim_number = payload.claim_number.strip()
    v.billed_at = now_utc_naive()
    v.billed_by = by
    v.status = "billed"
    _complete_milestone(v, "billed", by)
    _audit(db, actor=by, action="visit_billed",
            summary=f"Visit billed under claim #{v.claim_number}",
            detail={"visit_id": str(v.id), "claim": v.claim_number})
    db.commit(); db.refresh(v)
    return _visit_dict(v)


# Revert one workflow step — reversible status, audited ---------------

class RevertIn(BaseModel):
    reason: str


def _reopen_milestone(v: PelletVisit, kind: str) -> None:
    m = next((m for m in (v.milestones or []) if m.kind == kind), None)
    if m:
        m.status = "pending"
        m.completed_at = None
        m.completed_by = None


@router.post("/visits/{visit_id}/revert")
def revert_visit_status(visit_id: str, payload: RevertIn,
                          db: Session = Depends(get_db),
                          current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.WORK))):
    """Step a visit one stage backward: un-bill (billed→inserted),
    un-insert (inserted→in_progress), or un-bag (bagged→scheduled).
    A reason is required; every revert writes a StateTransitionAudit row
    (actor + before→after + reason). Un-insert and un-bill require
    pellet:manage; un-bag needs only pellet:work."""
    reason = (payload.reason or "").strip()
    if not reason:
        raise HTTPException(status_code=422, detail="reason is required")
    v = (db.query(PelletVisit).options(joinedload(PelletVisit.milestones),
                                         joinedload(PelletVisit.doses)
                                           .joinedload(PelletVisitDose.dose_type))
           .filter(PelletVisit.id == visit_id).first())
    if not v:
        raise HTTPException(status_code=404, detail="visit not found")
    by = current_user.get("email") or "system"
    bagged_done = any(m.kind == "bagged" and m.status == "done"
                      for m in (v.milestones or []))

    if v.status == "billed":
        if not _is_admin(db, current_user):
            raise HTTPException(status_code=403, detail="un-bill requires pellet:manage")
        before, after, action = "billed", "inserted", "unbill"
        v.status = "inserted"
        v.billed_at = None
        v.billed_by = None
        _reopen_milestone(v, "billed")

    elif v.status == "inserted":
        if not _is_admin(db, current_user):
            raise HTTPException(status_code=403, detail="un-insert requires pellet:manage")
        before, after, action = "inserted", "in_progress", "uninsert"
        for d in v.doses:
            if d.status == "inserted":
                d.status = "pulled"
                d.resolved_at = None
                d.resolved_by = None
        v.inserted_at = None
        v.inserted_by = None
        v.outcome = None
        v.status = "in_progress"
        _reopen_milestone(v, "inserted")

    elif v.status == "in_progress" and bagged_done:
        before, after, action = "bagged", "scheduled", "unbag"
        _reopen_milestone(v, "bagged")
        # Return pulled pellets to stock — but ONLY for real (non-historical)
        # bag-fills. Historical/imported visits never decremented live stock,
        # so restoring would create phantom inventory.
        location = v.location
        for d in v.doses:
            if d.status in ("planned", "pulled"):
                if (not v.is_historical) and d.lot_id and location:
                    stock = _get_or_create_stock(db, d.lot_id, location)
                    _adjust_stock(db, stock, d.quantity)
                    _audit(db, actor=by, action="dose_returned",
                            lot_id=d.lot_id, location=location, delta_doses=d.quantity,
                            summary=f"Un-bag returned {d.quantity}× {d.dose_type.label} to stock",
                            detail={"visit_id": str(v.id), "visit_dose_id": str(d.id),
                                    "reason": reason})
                d.status = "returned"
                d.resolved_at = now_utc_naive()
                d.resolved_by = by
        v.bagged_at = None
        v.bagged_by = None

    else:
        raise HTTPException(
            status_code=422,
            detail=f"nothing to revert from status '{v.status}'")

    from app.services.state_audit import log_state_transition
    log_state_transition(db, entity_type="pellet_visit", entity_id=v.id,
                          action=f"revert_{action}", actor=by,
                          before=before, after=after,
                          summary=f"Reverted {before} → {after}",
                          detail={"reason": reason, "visit_id": str(v.id)})
    _audit(db, actor=by, action=f"visit_revert_{action}",
            summary=f"Reverted {before} → {after}: {reason}",
            detail={"visit_id": str(v.id), "reason": reason})
    db.commit(); db.refresh(v)
    return _visit_dict(v)


@router.get("/visits/{visit_id}/transitions")
def visit_transitions(visit_id: str,
                        db: Session = Depends(get_db),
                        current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.VIEW))):
    """Status-change history for a visit (who flipped it A→B, when, why)."""
    from app.models.state_transition import StateTransitionAudit
    rows = (db.query(StateTransitionAudit)
              .filter(StateTransitionAudit.entity_type == "pellet_visit",
                      StateTransitionAudit.entity_id == str(visit_id))
              .order_by(StateTransitionAudit.at.desc()).all())
    return [{
        "id":     str(r.id),
        "action": r.action,
        "before": r.before_value,
        "after":  r.after_value,
        "actor":  r.actor,
        "at":     r.at.isoformat() if r.at else None,
        "reason": (r.detail or {}).get("reason") if isinstance(r.detail, dict) else None,
        "summary": r.summary,
    } for r in rows]


# Generic milestone toggle (for any pending milestone) ---------------

class MilestoneAdvanceIn(BaseModel):
    status: str = "done"   # done | skipped | not_applicable
    notes:  Optional[str] = None


@router.post("/visits/{visit_id}/milestones/{milestone_id}/advance")
def advance_milestone(visit_id: str, milestone_id: str,
                        payload: MilestoneAdvanceIn,
                        db: Session = Depends(get_db),
                        current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.WORK))):
    v = (db.query(PelletVisit).options(joinedload(PelletVisit.milestones))
           .filter(PelletVisit.id == visit_id).first())
    if not v:
        raise HTTPException(status_code=404, detail="visit not found")
    m = next((m for m in v.milestones if str(m.id) == milestone_id), None)
    if not m:
        raise HTTPException(status_code=404, detail="milestone not found")
    if payload.status not in ("done", "skipped", "not_applicable", "pending"):
        raise HTTPException(status_code=422, detail="invalid status")
    by = current_user.get("email") or "system"
    m.status = payload.status
    if payload.status == "pending":
        m.completed_at = None
        m.completed_by = None
    else:
        m.completed_at = now_utc_naive()
        m.completed_by = by
    if payload.notes is not None:
        m.notes = payload.notes

    # Keep the payment fields on the visit in sync with the corresponding
    # milestone — otherwise the calendar / list views (which read
    # v.payment_status) disagree with the detail page (which shows the
    # milestone checkmark). Mark-done sets the visit fields; reopen clears.
    if m.kind == "klara_sent":
        if payload.status == "done":
            if v.payment_status == "not_sent":
                v.payment_status = "sent"
            v.klara_sent_at = v.klara_sent_at or now_utc_naive()
            v.klara_sent_by = v.klara_sent_by or by
        elif payload.status == "pending":
            if v.payment_status == "sent":
                v.payment_status = "not_sent"
            v.klara_sent_at = None
            v.klara_sent_by = None
    elif m.kind == "payment_collected":
        if payload.status == "done":
            v.payment_status = "collected"
            v.payment_collected_at = v.payment_collected_at or now_utc_naive()
            v.payment_collected_by = v.payment_collected_by or by
        elif payload.status == "pending":
            # If klara was sent, fall back to "sent", otherwise "not_sent"
            v.payment_status = "sent" if v.klara_sent_at else "not_sent"
            v.payment_collected_at = None
            v.payment_collected_by = None

    _audit(db, actor=by, action="milestone_advanced",
            summary=f"Milestone '{m.title}' → {m.status}",
            detail={"visit_id": str(v.id), "milestone_kind": m.kind,
                    "status": m.status, "notes": payload.notes})
    db.commit(); db.refresh(v)
    return _visit_dict(v)


# ─── Patient-action feed (pellet portal) ───
@router.get("/activity")
def pellet_activity(unread_only: bool = False, limit: int = 100,
                    db: Session = Depends(get_db),
                    current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.VIEW))):
    q = (db.query(PelletActivity, PelletPatient)
           .join(PelletPatient, PelletPatient.id == PelletActivity.pellet_patient_id))
    if unread_only:
        q = q.filter(PelletActivity.read_at.is_(None))
    pairs = q.order_by(PelletActivity.created_at.desc()).limit(max(0, int(limit))).all()
    return {"items": [{
        "id": str(a.id), "pellet_patient_id": str(a.pellet_patient_id),
        "patient_name": p.patient_name, "chart_number": p.chart_number,
        "kind": a.kind, "summary": a.summary, "actor": a.actor,
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "handled_at": a.handled_at.isoformat() if a.handled_at else None,
        "read_at": a.read_at.isoformat() if a.read_at else None,
    } for a, p in pairs]}


@router.get("/activity/unread-count")
def pellet_activity_unread(db: Session = Depends(get_db),
                           current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.VIEW))):
    n = db.query(PelletActivity).filter(PelletActivity.read_at.is_(None)).count()
    return {"count": n}


@router.post("/activity/read-all")
def pellet_activity_read_all(db: Session = Depends(get_db),
                             current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.WORK))):
    by = (current_user.get("email") or "").lower() or None
    (db.query(PelletActivity).filter(PelletActivity.read_at.is_(None))
       .update({"read_at": now_utc_naive(), "read_by": by}))
    db.commit(); return {"ok": True}


@router.post("/activity/{activity_id}/verify")
def pellet_activity_verify(activity_id: str, db: Session = Depends(get_db),
                           current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.WORK))):
    a = db.query(PelletActivity).filter(PelletActivity.id == activity_id).first()
    if a is None:
        raise HTTPException(status_code=404, detail="activity not found")
    p = db.query(PelletPatient).filter(PelletPatient.id == a.pellet_patient_id).first()
    by = (current_user.get("email") or "").lower() or None
    now = now_utc_naive()
    if a.kind in ("mammo_uploaded", "labs_self_reported") and p is None:
        raise HTTPException(status_code=409, detail="patient record missing")
    if a.kind == "mammo_uploaded":
        p.mammo_verified = True; p.mammo_verified_by = by; p.mammo_verified_at = now
    elif a.kind == "labs_self_reported":
        p.labs_verified = True; p.labs_verified_by = by; p.labs_verified_at = now
    a.handled_at = now; a.handled_by = by
    if a.read_at is None:
        a.read_at = now; a.read_by = by
    db.commit()
    return {"ok": True, "kind": a.kind}
