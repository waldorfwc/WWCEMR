# Pellet Lot Dedup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop duplicate pellet lots — merge a freshly-verified lot into its per-office canonical at receive-verify time, and provide a one-time audited migration to merge the existing 32 duplicates — preserving every stock total and chain-of-custody link.

**Architecture:** A `PelletLot.location` column makes a lot's office explicit. One shared `merge_lot(db, src, dst)` helper re-points all 6 FK references, sums stock per location, carries forward fields, deletes `src`, and audits — used by both `verify_manifest` (live prevention) and a one-time `--dry-run`-capable migration (cleanup).

**Tech Stack:** FastAPI + SQLAlchemy, pytest, Cloud Run job for the migration.

**Spec:** `docs/superpowers/specs/2026-06-23-pellet-lot-dedup-design.md`

---

## Verified facts

- `PelletLot` (`backend/app/models/pellet.py:71-101`): columns incl. `dose_type_id`, `qualgen_lot_number`, `expiration_date`, `doses_originally_received`, `receipt_id`, `received_at`, `unit_cost`, `cost_per_dose`, `notes`. `stock_rows = relationship("PelletStock", cascade="all, delete-orphan", back_populates="lot")`. No uniqueness on `(qualgen_lot_number, dose_type_id)`.
- **6 FK columns reference `pellet_lots.id`** (all must be re-pointed before deleting a lot, since the FK default is RESTRICT): `PelletStock.lot_id`, `PelletVisitDose.lot_id`, `PelletAuditEvent.lot_id` (nullable), `PelletTransfer.lot_id`, `PelletDisposal.lot_id`, `PelletCountLine.lot_id`.
- `PelletStock` has `UniqueConstraint(lot_id, location, name="uq_pellet_stock_lot_loc")` and `doses_on_hand`.
- Receive flow: `create_receipt` (`pellet.py:1459`) creates a `PelletLot` per receipt line (lines 1557-1568, linked by `receipt_id`); `verify_manifest` (`pellet.py:1610`) credits stock in the loop at **1663-1681**: `s = _get_or_create_stock(db, l.id, r.location)` then `_adjust_stock(db, s, l.doses_originally_received)`; `r.location` is the office.
- `_audit(db, *, actor, action, lot_id=, dose_type_id=, location=, delta_doses=, summary=, detail=)` (`pellet.py:80`). `now_utc_naive` from `app.utils.dt`.
- Smartsheet import placeholder expiration: `UNKNOWN_EXP = date(2099, 12, 31)` (`pellet_smartsheet_history_import.py`).
- Lightweight migration: `backend/app/database.py` has a `needed` list of `(table, column, coltype)` and `_adapt_coltype_for_dialect`. One-off scripts follow `backend/scripts/backfill_surgery_slots.py` (`init_db()` → `SessionLocal()` → idempotent loop → `db.commit()` → summary print).
- Pellet locations constant: `PELLET_LOCATIONS` in `pellet.py` (values incl. `white_plains`, `brandywine`).
- Test conventions: `backend/tests/test_pellet_*.py` seed `PelletPatient/PelletVisit/PelletVisitDose/PelletDoseType/PelletLot/PelletStock` directly; `client_factory(user=u)` with a super-admin `User`; `PelletDoseType` requires `hormone`, `dose_mg`, `is_controlled`; `PelletLot` requires `doses_originally_received`; endpoints under `/api`.

---

## Task 1: Add `PelletLot.location` column

**Files:**
- Modify: `backend/app/models/pellet.py`
- Modify: `backend/app/database.py`
- Test: `backend/tests/test_pellet_lot_dedup.py` (create)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_pellet_lot_dedup.py`:

```python
from datetime import date
from app.models.pellet import PelletLot, PelletDoseType


def _dt(db, label="Testosterone 100mg"):
    dt = PelletDoseType(label=label, hormone="testosterone", dose_mg=100, is_controlled=True)
    db.add(dt); db.commit(); db.refresh(dt)
    return dt


def test_pellet_lot_has_location_column(db):
    dt = _dt(db)
    lot = PelletLot(dose_type_id=dt.id, qualgen_lot_number="L1",
                    expiration_date=date(2027, 1, 1), doses_originally_received=10,
                    location="white_plains")
    db.add(lot); db.commit(); db.refresh(lot)
    assert lot.location == "white_plains"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && pytest tests/test_pellet_lot_dedup.py -q`
Expected: FAIL — `TypeError: 'location' is an invalid keyword argument for PelletLot`.

- [ ] **Step 3: Add the column to the model**

In `backend/app/models/pellet.py`, in `PelletLot` (after `received_by`):

```python
    # Which office this lot belongs to (model B: one lot record per office).
    # Stamped at receipt time; backfilled for legacy rows by the dedup migration.
    location                    = Column(String(40), nullable=True)
```

- [ ] **Step 4: Add the lightweight-migration entry**

In `backend/app/database.py`, add to the `needed` list:

```python
    ("pellet_lots", "location", "VARCHAR(40)"),
```

- [ ] **Step 5: Run to verify it passes**

Run: `cd backend && pytest tests/test_pellet_lot_dedup.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/models/pellet.py backend/app/database.py backend/tests/test_pellet_lot_dedup.py
git commit -m "feat(pellet): add PelletLot.location column"
```

---

## Task 2: Shared `merge_lot` helper

**Files:**
- Create: `backend/app/services/pellet/lot_merge.py`
- Test: `backend/tests/test_pellet_lot_dedup.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_pellet_lot_dedup.py`:

```python
from app.models.pellet import (
    PelletStock, PelletVisitDose, PelletAuditEvent, PelletPatient, PelletVisit,
)
from app.services.pellet.lot_merge import merge_lot, UNKNOWN_EXP


def _lot(db, dt, *, number, loc, exp=date(2027, 1, 1), orig=10, on_hand=None,
         receipt_id=None):
    lot = PelletLot(dose_type_id=dt.id, qualgen_lot_number=number,
                    expiration_date=exp, doses_originally_received=orig,
                    location=loc, receipt_id=receipt_id)
    db.add(lot); db.flush()
    if on_hand is not None:
        db.add(PelletStock(lot_id=lot.id, location=loc, doses_on_hand=on_hand, status="active"))
    db.commit(); db.refresh(lot)
    return lot


def _oh(db, lot, loc):
    s = (db.query(PelletStock)
           .filter(PelletStock.lot_id == lot.id, PelletStock.location == loc).first())
    return s.doses_on_hand if s else 0


def test_merge_lot_repoints_stock_doses_audit_and_deletes_src(db):
    dt = _dt(db)
    dst = _lot(db, dt, number="L9", loc="white_plains", exp=date(2027, 5, 1), orig=20, on_hand=5)
    src = _lot(db, dt, number="L9", loc="white_plains", exp=UNKNOWN_EXP, orig=8, on_hand=3)
    # a dose + an audit row pointing at src
    p = PelletPatient(patient_name="A", chart_number="C1", patient_dob=date(1980, 1, 1))
    db.add(p); db.flush()
    v = PelletVisit(patient_id=p.id, visit_kind="initial", status="inserted",
                    location="white_plains", scheduled_date=date(2026, 6, 1))
    db.add(v); db.flush()
    d = PelletVisitDose(visit_id=v.id, dose_type_id=dt.id, quantity=2, position=1,
                        status="inserted", lot_id=src.id)
    db.add(d)
    db.add(PelletAuditEvent(actor="x", action="dose_pulled", lot_id=src.id, delta_doses=-2))
    db.commit()

    res = merge_lot(db, src=src, dst=dst, actor="system:test")
    db.commit()

    assert res["merged"] is True
    # src is gone
    assert db.query(PelletLot).filter(PelletLot.id == src.id).first() is None
    # stock summed onto dst (5 + 3)
    assert _oh(db, dst, "white_plains") == 8
    assert db.query(PelletStock).filter(PelletStock.lot_id == src.id).count() == 0
    # dose + audit re-pointed
    db.refresh(d); assert str(d.lot_id) == str(dst.id)
    assert db.query(PelletAuditEvent).filter(
        PelletAuditEvent.action == "dose_pulled",
        PelletAuditEvent.lot_id == dst.id).count() == 1
    # fields carried forward
    db.refresh(dst)
    assert dst.doses_originally_received == 28          # 20 + 8
    assert dst.expiration_date == date(2027, 5, 1)      # dst kept its real exp
    # a lot_merged audit was written
    assert db.query(PelletAuditEvent).filter(
        PelletAuditEvent.action == "lot_merged", PelletAuditEvent.lot_id == dst.id).count() == 1


def test_merge_lot_carries_real_exp_onto_placeholder_canonical(db):
    dt = _dt(db)
    dst = _lot(db, dt, number="L8", loc="brandywine", exp=UNKNOWN_EXP, orig=5, on_hand=0)
    src = _lot(db, dt, number="L8", loc="brandywine", exp=date(2027, 9, 1), orig=4, on_hand=4)
    merge_lot(db, src=src, dst=dst, actor="system:test"); db.commit()
    db.refresh(dst)
    assert dst.expiration_date == date(2027, 9, 1)      # placeholder replaced
    assert _oh(db, dst, "brandywine") == 4
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && pytest tests/test_pellet_lot_dedup.py -k merge_lot -q`
Expected: FAIL — `ModuleNotFoundError: app.services.pellet.lot_merge`.

- [ ] **Step 3: Implement the helper**

Create `backend/app/services/pellet/lot_merge.py`:

```python
"""Merge a duplicate pellet lot into a canonical one.

Used by verify_manifest (live: a freshly-verified lot merges into the
pre-existing canonical for its number+strength+office) and by the one-time
dedup migration. Re-points all 6 FK references, sums stock per location,
carries forward fields, deletes src, and writes a `lot_merged` audit.

Stock increments use an atomic SQL UPDATE (not Python +=) so two concurrent
verifies of the same lot can't lose an update. The caller commits.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.models.pellet import (
    PelletLot, PelletStock, PelletVisitDose, PelletAuditEvent,
    PelletTransfer, PelletDisposal, PelletCountLine,
)

# Placeholder expiration the Smartsheet import uses for unknown-exp lots.
UNKNOWN_EXP = date(2099, 12, 31)

# Tables (besides PelletStock, handled specially) whose lot_id must re-point.
_FK_MODELS = (PelletVisitDose, PelletAuditEvent, PelletTransfer,
              PelletDisposal, PelletCountLine)


def merge_lot(db: Session, *, src: PelletLot, dst: PelletLot,
              actor: str = "system:lot-dedup") -> dict:
    if str(src.id) == str(dst.id):
        return {"merged": False, "reason": "same lot"}

    moved = 0
    # 1. Stock: fold each src stock row into dst's row at the same location
    #    (creating it if needed), atomically, then delete the src row.
    for s in db.query(PelletStock).filter(PelletStock.lot_id == src.id).all():
        dst_row = (db.query(PelletStock)
                     .filter(PelletStock.lot_id == dst.id,
                             PelletStock.location == s.location).first())
        if dst_row is None:
            dst_row = PelletStock(lot_id=dst.id, location=s.location, doses_on_hand=0)
            db.add(dst_row); db.flush()
        db.query(PelletStock).filter(PelletStock.id == dst_row.id).update(
            {"doses_on_hand": PelletStock.doses_on_hand + s.doses_on_hand},
            synchronize_session=False)
        moved += s.doses_on_hand
        db.delete(s)
    db.flush()

    # 2. Re-point the other 5 FK tables.
    for model in _FK_MODELS:
        db.query(model).filter(model.lot_id == src.id).update(
            {"lot_id": dst.id}, synchronize_session=False)
    db.flush()

    # 3. Carry forward onto the canonical.
    if dst.expiration_date == UNKNOWN_EXP and src.expiration_date != UNKNOWN_EXP:
        dst.expiration_date = src.expiration_date
    dst.doses_originally_received = ((dst.doses_originally_received or 0)
                                     + (src.doses_originally_received or 0))
    if dst.receipt_id is None and src.receipt_id is not None:
        dst.receipt_id = src.receipt_id
    if dst.unit_cost is None and src.unit_cost is not None:
        dst.unit_cost = src.unit_cost
    if dst.cost_per_dose is None and src.cost_per_dose is not None:
        dst.cost_per_dose = src.cost_per_dose
    if dst.location is None and src.location is not None:
        dst.location = src.location

    # 4. Audit, then delete src.
    src_id, src_num, src_rcpt = str(src.id), src.qualgen_lot_number, src.receipt_id
    db.add(PelletAuditEvent(
        actor=actor, action="lot_merged",
        lot_id=dst.id, dose_type_id=dst.dose_type_id, location=dst.location,
        summary=(f"Merged duplicate lot {src_num} ({src_id[:8]}) into "
                 f"canonical {dst.qualgen_lot_number} ({str(dst.id)[:8]})"),
        detail={"canonical_lot_id": str(dst.id), "merged_lot_id": src_id,
                "merged_stock_doses": moved,
                "merged_receipt_id": str(src_rcpt) if src_rcpt else None}))
    db.flush()
    # Expire src so its (now-empty) stock_rows collection doesn't cascade-delete
    # rows we already moved.
    db.expire(src)
    db.delete(src)
    db.flush()
    return {"merged": True, "moved_doses": moved, "src": src_id, "dst": str(dst.id)}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && pytest tests/test_pellet_lot_dedup.py -k merge_lot -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/pellet/lot_merge.py backend/tests/test_pellet_lot_dedup.py
git commit -m "feat(pellet): shared merge_lot helper (re-point FKs, sum stock, audit)"
```

---

## Task 3: Prevent — verify-time merge + receipt location stamping

**Files:**
- Modify: `backend/app/routers/pellet.py`
- Test: `backend/tests/test_pellet_lot_dedup.py`

- [ ] **Step 1: Write the failing test (full receive→verify flow)**

Append:

```python
from app.models.user import User


def _mgr(db):
    u = User(email="dedup@waldorfwomenscare.com", display_name="D", is_super_admin=True)
    db.add(u); db.commit()
    return u


def _receive_and_verify(client, dt, *, number, loc, doses, exp="2027-03-01", order_id=None):
    # Unscheduled receipt path (no order needed); notes required.
    r = client.post("/api/pellets/receipts", json={
        "location": loc, "is_unscheduled": True, "notes": "test receive",
        "lots": [{"dose_type_id": str(dt.id), "qualgen_lot_number": number,
                  "expiration_date": exp, "doses_received": doses}],
    })
    assert r.status_code == 201, r.text
    rid = r.json()["receipt_id"]
    v = client.post(f"/api/pellets/receipts/{rid}/verify-manifest", json={})
    assert v.status_code == 200, v.text
    return rid


def test_verify_merges_second_receipt_of_same_lot_same_office(client_factory, db):
    dt = _dt(db); u = _mgr(db); client = client_factory(user=u)
    _receive_and_verify(client, dt, number="LZ", loc="white_plains", doses=10)
    _receive_and_verify(client, dt, number="LZ", loc="white_plains", doses=6)
    lots = db.query(PelletLot).filter(PelletLot.qualgen_lot_number == "LZ").all()
    assert len(lots) == 1                       # merged into one canonical
    assert _oh(db, lots[0], "white_plains") == 16   # 10 + 6
    assert lots[0].doses_originally_received == 16


def test_verify_keeps_same_lot_at_two_offices_separate(client_factory, db):
    dt = _dt(db); u = _mgr(db); client = client_factory(user=u)
    _receive_and_verify(client, dt, number="LX", loc="white_plains", doses=10)
    _receive_and_verify(client, dt, number="LX", loc="brandywine", doses=7)
    lots = db.query(PelletLot).filter(PelletLot.qualgen_lot_number == "LX").all()
    assert len(lots) == 2                       # model B: per-office
    by_loc = {l.location: l for l in lots}
    assert _oh(db, by_loc["white_plains"], "white_plains") == 10
    assert _oh(db, by_loc["brandywine"], "brandywine") == 7
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && pytest tests/test_pellet_lot_dedup.py -k verify_ -q`
Expected: FAIL — `test_verify_merges_...` finds **2** lots (no merge yet).

- [ ] **Step 3: Stamp `location` in `create_receipt`**

In `backend/app/routers/pellet.py`, in the `PelletLot(...)` constructor inside `create_receipt` (around line 1557), add `location=r.location`:

```python
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
            location=r.location,
        )
```

- [ ] **Step 4: Merge freshly-verified lots in `verify_manifest`**

In `verify_manifest`, AFTER the existing per-lot stock-credit loop (the `for l in lots:` block ending around line 1681) and BEFORE the order-status block, insert:

```python
    # Dedup: fold each freshly-verified lot into the pre-existing canonical for
    # its (number, dose_type, office). Keeps one lot record per office (model B)
    # so receiving the same lot twice can't create duplicates. The merge also
    # moved its just-credited stock onto the canonical.
    from app.services.pellet.lot_merge import merge_lot
    for l in list(lots):
        canonical = (db.query(PelletLot)
                       .filter(PelletLot.qualgen_lot_number == l.qualgen_lot_number,
                               PelletLot.dose_type_id == l.dose_type_id,
                               PelletLot.location == r.location,
                               PelletLot.id != l.id)
                       .order_by(PelletLot.received_at.asc())
                       .first())
        if canonical is not None:
            merge_lot(db, src=l, dst=canonical, actor=by)
```

(Implementer: `lots` was loaded before stock credit; after `merge_lot` deletes `l`, do not reference `l` again — the loop is the last use. Confirm nothing below the loop iterates `lots`.)

- [ ] **Step 5: Run to verify it passes**

Run: `cd backend && pytest tests/test_pellet_lot_dedup.py -k verify_ -q` → PASS (2).
Then regression: `cd backend && pytest tests/ -k pellet -q` → green (receiving/verify tests unaffected).

- [ ] **Step 6: Commit**

```bash
git add backend/app/routers/pellet.py backend/tests/test_pellet_lot_dedup.py
git commit -m "feat(pellet): merge duplicate lots at manifest-verify time"
```

---

## Task 4: One-time dedup migration (`--dry-run`)

**Files:**
- Create: `backend/scripts/pellet_lot_dedup.py`
- Test: `backend/tests/test_pellet_lot_dedup.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
from app.scripts_pellet_lot_dedup_helpers import backfill_lot_locations, dedup_lots
# NOTE: implemented in backend/scripts/pellet_lot_dedup.py; import path below.


def test_dedup_migration_merges_existing_duplicates(db):
    dt = _dt(db)
    # 3 dups at white_plains: a placeholder-exp Smartsheet-style (no receipt) + two real
    a = _lot(db, dt, number="DUP", loc="white_plains", exp=UNKNOWN_EXP, orig=40, on_hand=0)
    b = _lot(db, dt, number="DUP", loc="white_plains", exp=date(2027, 6, 1), orig=10, on_hand=4)
    c = _lot(db, dt, number="DUP", loc="white_plains", exp=date(2027, 6, 1), orig=5, on_hand=2)
    # and one at brandywine (must stay separate)
    e = _lot(db, dt, number="DUP", loc="brandywine", exp=date(2027, 6, 1), orig=3, on_hand=1)
    total_before = (db.query(PelletStock).count(), )

    stats = dedup_lots(db, actor="system:test", dry_run=False)
    db.commit()

    wp = (db.query(PelletLot)
            .filter(PelletLot.qualgen_lot_number == "DUP", PelletLot.location == "white_plains").all())
    assert len(wp) == 1                         # 3 -> 1 at white_plains
    assert _oh(db, wp[0], "white_plains") == 6  # 0 + 4 + 2
    assert wp[0].expiration_date == date(2027, 6, 1)   # real exp, not placeholder
    bw = (db.query(PelletLot)
            .filter(PelletLot.qualgen_lot_number == "DUP", PelletLot.location == "brandywine").all())
    assert len(bw) == 1                         # brandywine untouched
    assert stats["groups_merged"] == 1 and stats["lots_deleted"] == 2


def test_dedup_migration_is_idempotent(db):
    dt = _dt(db)
    _lot(db, dt, number="X", loc="white_plains", exp=date(2027, 1, 1), orig=5, on_hand=5)
    _lot(db, dt, number="X", loc="white_plains", exp=date(2027, 1, 1), orig=5, on_hand=5)
    dedup_lots(db, actor="t", dry_run=False); db.commit()
    stats2 = dedup_lots(db, actor="t", dry_run=False); db.commit()
    assert stats2["groups_merged"] == 0         # nothing left to merge
    assert db.query(PelletLot).filter(PelletLot.qualgen_lot_number == "X").count() == 1
```

(Implementer: expose `backfill_lot_locations(db)` and `dedup_lots(db, *, actor, dry_run)` as importable functions in `backend/scripts/pellet_lot_dedup.py`; adjust the test import to `from scripts.pellet_lot_dedup import dedup_lots, backfill_lot_locations` if the repo's test config puts `backend/` on `sys.path` — check how other `backend/scripts` are imported in tests, else add `sys.path` insertion in the test. Use whichever import the repo already supports for `backend/scripts/*`.)

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && pytest tests/test_pellet_lot_dedup.py -k dedup_migration -q`
Expected: FAIL — import error / function missing.

- [ ] **Step 3: Implement the migration**

Create `backend/scripts/pellet_lot_dedup.py`:

```python
"""One-time: merge duplicate pellet lots (same qualgen_lot_number + dose_type +
office) into a single canonical record. Backfills PelletLot.location first.

Idempotent — re-running after a clean merge is a no-op. Run with --dry-run to
print the plan without writing.

Canonical per group: prefer a receipt-backed lot with a real (non-placeholder)
expiration; tie-break by earliest received_at.

Safety: asserts total stock doses and total doses_originally_received are
unchanged across the run; skips (and reports) any lot whose stock/doses span
more than one office.
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy.orm import Session

from app.database import SessionLocal, init_db
from app.models.pellet import (
    PelletLot, PelletStock, PelletVisitDose,
)
from app.services.pellet.lot_merge import merge_lot, UNKNOWN_EXP


def _lot_offices(db: Session, lot: PelletLot) -> set:
    """Distinct offices a lot touches: its stock rows + its doses' visit
    locations. Used to detect a lot spanning >1 office (unsafe to auto-merge)."""
    offices = set(
        loc for (loc,) in db.query(PelletStock.location)
                            .filter(PelletStock.lot_id == lot.id).all())
    rows = (db.query(PelletVisitDose)
              .filter(PelletVisitDose.lot_id == lot.id).all())
    for d in rows:
        v = d.visit
        if v is not None and v.location:
            offices.add(v.location)
    return offices


def backfill_lot_locations(db: Session) -> int:
    """Set location on every lot that has none: its stock row's location, else
    its receipt's location, else the modal location of its doses' visits."""
    n = 0
    for lot in db.query(PelletLot).filter(PelletLot.location.is_(None)).all():
        loc = None
        srows = db.query(PelletStock).filter(PelletStock.lot_id == lot.id).all()
        locs = {s.location for s in srows}
        if len(locs) == 1:
            loc = next(iter(locs))
        elif lot.receipt_id is not None and lot.receipt is not None:
            loc = lot.receipt.location
        else:
            doses = db.query(PelletVisitDose).filter(PelletVisitDose.lot_id == lot.id).all()
            counts = defaultdict(int)
            for d in doses:
                if d.visit is not None and d.visit.location:
                    counts[d.visit.location] += 1
            if counts:
                loc = max(counts, key=counts.get)
        if loc:
            lot.location = loc
            n += 1
    db.flush()
    return n


def dedup_lots(db: Session, *, actor: str = "system:lot-dedup", dry_run: bool = True) -> dict:
    backfill_lot_locations(db)

    stock_before = db.query(PelletStock).with_entities(
        PelletStock.doses_on_hand).all()
    total_stock_before = sum(s[0] for s in stock_before)
    total_orig_before = sum(
        (l.doses_originally_received or 0) for l in db.query(PelletLot).all())

    groups = defaultdict(list)
    for lot in db.query(PelletLot).all():
        if lot.location is None:
            continue  # un-backfillable; left for manual review
        groups[(lot.qualgen_lot_number, str(lot.dose_type_id), lot.location)].append(lot)

    stats = {"groups_seen": 0, "groups_merged": 0, "lots_deleted": 0,
             "skipped_multi_office": [], "plan": []}

    for key, lots in groups.items():
        if len(lots) < 2:
            continue
        stats["groups_seen"] += 1
        # Single-office guard: every lot in the group must touch only this office.
        office = key[2]
        bad = [str(l.id) for l in lots if (_lot_offices(db, l) - {office})]
        if bad:
            stats["skipped_multi_office"].append({"key": key, "lots": bad})
            continue
        # Canonical: receipt-backed + real exp first; tie-break earliest received_at.
        def rank(l):
            return (0 if (l.receipt_id is not None and l.expiration_date != UNKNOWN_EXP) else 1,
                    l.received_at or now_min())
        canonical = sorted(lots, key=rank)[0]
        dups = [l for l in lots if l.id != canonical.id]
        stats["plan"].append({"key": key, "canonical": str(canonical.id),
                              "merge": [str(d.id) for d in dups]})
        if not dry_run:
            for d in dups:
                merge_lot(db, src=d, dst=canonical, actor=actor)
                stats["lots_deleted"] += 1
            stats["groups_merged"] += 1

    if not dry_run:
        db.flush()
        total_stock_after = sum(
            s[0] for s in db.query(PelletStock).with_entities(PelletStock.doses_on_hand).all())
        total_orig_after = sum(
            (l.doses_originally_received or 0) for l in db.query(PelletLot).all())
        assert total_stock_after == total_stock_before, (
            f"stock total changed {total_stock_before} -> {total_stock_after}")
        assert total_orig_after == total_orig_before, (
            f"orig-received total changed {total_orig_before} -> {total_orig_after}")
    return stats


def now_min():
    from datetime import datetime
    return datetime.min


def main():
    dry = "--apply" not in sys.argv
    init_db()
    db = SessionLocal()
    try:
        stats = dedup_lots(db, dry_run=dry)
        if dry:
            print("DRY RUN — no changes written")
        else:
            db.commit()
            print("APPLIED")
        print(f"  duplicate groups: {stats['groups_seen']}")
        print(f"  groups merged:    {stats['groups_merged']}")
        print(f"  lots deleted:     {stats['lots_deleted']}")
        if stats["skipped_multi_office"]:
            print(f"  SKIPPED (multi-office, manual review): {stats['skipped_multi_office']}")
        for p in stats["plan"]:
            print(f"   {p['key']}: keep {p['canonical'][:8]}, merge {[x[:8] for x in p['merge']]}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
```

(Implementer: if `PelletVisitDose.visit` / `PelletLot.receipt` relationships aren't defined, replace `.visit` / `.receipt` with explicit queries by `visit_id` / `receipt_id`. Verify the relationship names against `models/pellet.py` first.)

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && pytest tests/test_pellet_lot_dedup.py -k dedup_migration -q` → PASS (2).
Then the whole file: `pytest tests/test_pellet_lot_dedup.py -q` → all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/pellet_lot_dedup.py backend/tests/test_pellet_lot_dedup.py
git commit -m "feat(pellet): one-time lot-dedup migration (dry-run, idempotent, audited)"
```

---

## Task 5: Re-key the Smartsheet import's lot cache (lower priority)

**Files:**
- Modify: `backend/scripts/pellet_smartsheet_history_import.py`

- [ ] **Step 1: Re-key `lots_by_number`**

In `pellet_smartsheet_history_import.py`, the `lots_by_number` dict (around line 223) keys by `qualgen_lot_number` only, which silently collapses duplicates. Change the cache + lookups to key by `(qualgen_lot_number, dose_type_id, location)` so a re-run reuses the right per-office lot instead of auto-creating a new one. Concretely: build the dict as `{(l.qualgen_lot_number.strip(), str(l.dose_type_id), l.location): l for l in db.query(PelletLot).all() if l.qualgen_lot_number and l.location}`, and update the two `.get(rd["lot_raw"])` lookups (lines ~352, ~491) to `.get((rd["lot_raw"], str(rd["dose_type"].id), <sheet location>))`. Stamp `location=<sheet location>` on any lot it auto-creates.

(Implementer: the script processes one Smartsheet per location — use that sheet's location for the key + the created lot's `location`. This script is a manual one-shot; no automated test required, but `python -c "import ast; ast.parse(open('backend/scripts/pellet_smartsheet_history_import.py').read())"` must succeed.)

- [ ] **Step 2: Validate it parses + commit**

```bash
cd backend && python -c "import ast; ast.parse(open('scripts/pellet_smartsheet_history_import.py').read()); print('ok')"
git add backend/scripts/pellet_smartsheet_history_import.py
git commit -m "fix(pellet): smartsheet import keys lot cache by number+dose+office"
```

---

## Final verification + rollout (after all tasks)

- [ ] `cd backend && pytest tests/test_pellet_lot_dedup.py -q` → all pass.
- [ ] `cd backend && pytest tests/ -k pellet -q` → green (no regression).
- [ ] Full suite `cd backend && pytest -q` → green.
- [ ] Dispatch a final reviewer over the whole diff (focus: merge_lot conservation + FK re-point completeness; verify-time merge doesn't reference a deleted lot; migration invariants + single-office guard).
- [ ] Use `superpowers:finishing-a-development-branch`.
- [ ] **Rollout (operational, after merge+deploy):** run the migration as a Cloud Run job (backend image, DATABASE_URL, VPC) FIRST with `--dry-run` (default) → review the per-group plan in logs → then re-run with `--apply`. Then the physical count.

## Self-review notes

- **Spec coverage:** location column (T1); shared `merge_lot` re-pointing all 6 FKs + stock sum + carry-forward + audit + delete (T2); verify-time prevent + receipt stamping (T3); one-time migration with backfill, canonical rule, single-office guard, invariants, idempotency, dry-run (T4); smartsheet re-key (T5).
- **DRY:** one `merge_lot`, two callers (verify + migration). One `UNKNOWN_EXP`.
- **No hard unique index** (per the revised spec — two-step receive/verify makes transient dups legitimate).
- **Naming consistency:** `merge_lot(db, *, src, dst, actor)`, `dedup_lots(db, *, actor, dry_run)`, `backfill_lot_locations(db)`, `PelletLot.location`, audit action `lot_merged` — used identically across tasks.
- **Safety:** merge re-points before delete (FK RESTRICT); stock increment is atomic SQL; migration asserts totals unchanged + skips multi-office lots + dry-run default.
