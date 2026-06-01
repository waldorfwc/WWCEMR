# Patient Portal P2 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire real Payments and Schedule pages into the portal shell, replacing the P1 "Coming soon" stubs. Step-up SMS at payment-submit time. Schedule gated on payment-or-no-balance.

**Architecture:** New endpoints under the existing `/api/patient/portal/{sid}/...` prefix, all gated by `require_portal_token` from P1. Stripe Checkout reuses the existing `svc.create_checkout_session` from Phase H. Slot-claim logic extracted from `patient_surgery.py:505` into a shared service so the magic-link and portal flows don't drift.

**Spec:** `docs/superpowers/specs/2026-05-31-patient-portal-p2-design.md`

**Key facts about the existing code (don't relitigate):**
- `Surgery.amount_paid` is the cumulative paid amount, maintained by the Stripe webhook (`stripe_payments.py:_handle_checkout_completed`).
- `_outstanding_balance(s)` at `stripe_payments.py:357` returns `Decimal | None` = `pt_resp - amount_paid`.
- The magic-link `POST /select-slot` does: blackout check → start-time parse → overlap check → `SurgerySlot.add` → stamp surgery scheduled_date/selected_facility → SurgeryNote → calendar sync → confirmation email/SMS.

---

## Task 1: Schema — `schedule_gate_override` flag

**Files:**
- Modify: `backend/app/models/surgery.py` — add 3 columns
- Modify: `backend/app/database.py` — nothing (the columns are on an existing model)
- Create: `backend/scripts/migrate_patient_portal_p2.py`
- Test: `backend/tests/test_patient_portal_p2_schema.py`

- [ ] **Step 1: Failing test**

```python
"""Patient portal P2 schema."""
from app.models.surgery import Surgery


def test_surgery_has_schedule_gate_override(db):
    s = Surgery(chart_number="1", patient_name="Pat", status="new")
    db.add(s); db.commit(); db.refresh(s)
    assert s.schedule_gate_override is False
    assert s.schedule_gate_override_at is None
    assert s.schedule_gate_override_by is None
```

- [ ] **Step 2: Run, confirm fail (AttributeError).**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && ./venv/bin/pytest tests/test_patient_portal_p2_schema.py -v
```

- [ ] **Step 3: Add columns to Surgery.** Find the block where the P1 self-report flags live (`labs_self_reported`, `hospital_preop_self_reported`) and add immediately after:

```python
    # Patient portal — coordinator can let patient self-schedule even when
    # balance is unpaid (e.g. payment plan in flight, insurance under appeal).
    schedule_gate_override    = Column(Boolean, default=False, nullable=False)
    schedule_gate_override_at = Column(DateTime, nullable=True)
    schedule_gate_override_by = Column(String(120), nullable=True)
```

- [ ] **Step 4: Run, confirm pass.**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && ./venv/bin/pytest tests/test_patient_portal_p2_schema.py -v
```

- [ ] **Step 5: Migration script** at `backend/scripts/migrate_patient_portal_p2.py`:

```python
"""Idempotent migration for Patient Portal P2.

Adds: schedule_gate_override(+_at, +_by) columns on surgeries.

Run on prod:
    DATABASE_URL='postgresql+psycopg2://...' \
        ./venv/bin/python scripts/migrate_patient_portal_p2.py
"""
import os
import sys
from sqlalchemy import create_engine, text

DDL = [
    """ALTER TABLE surgeries
       ADD COLUMN IF NOT EXISTS schedule_gate_override BOOLEAN NOT NULL DEFAULT FALSE""",
    """ALTER TABLE surgeries
       ADD COLUMN IF NOT EXISTS schedule_gate_override_at TIMESTAMP NULL""",
    """ALTER TABLE surgeries
       ADD COLUMN IF NOT EXISTS schedule_gate_override_by VARCHAR(120) NULL""",
]


def main():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL not set", file=sys.stderr); sys.exit(2)
    eng = create_engine(db_url)
    with eng.begin() as conn:
        for ddl in DDL:
            conn.execute(text(ddl))
            print(f"  ✓ {ddl.split(chr(10))[0][:80]}")
    print("\nDone.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Commit.**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/models/surgery.py backend/scripts/migrate_patient_portal_p2.py \
        backend/tests/test_patient_portal_p2_schema.py
git commit -m "feat(portal-p2): schedule_gate_override columns + migration"
```

---

## Task 2: `issue_challenge` gets a `purpose` parameter

**Files:**
- Modify: `backend/app/services/patient_portal_auth.py`
- Modify: `backend/tests/test_patient_portal_auth.py`

The current `issue_challenge` hardcodes the "portal sign-in" SMS copy. For P2's step-up flow we need a different message ("Code to authorize your payment"). One function, one new parameter.

- [ ] **Step 1: Failing test** — append to `backend/tests/test_patient_portal_auth.py`:

```python
def test_issue_challenge_payment_purpose_uses_payment_copy(db):
    from unittest.mock import patch
    s = _make_surgery(db)
    with patch("app.services.patient_portal_auth.send_sms",
                return_value=True) as mock_sms:
        challenge_token, code = issue_challenge(db, s, purpose="payment")
    args, _ = mock_sms.call_args
    body = args[1]
    assert "payment" in body.lower() or "charge" in body.lower()
    assert code in body
    # Sign-in copy should NOT appear in payment SMS
    assert "sign-in" not in body.lower()
```

- [ ] **Step 2: Run, confirm fail (`unexpected keyword argument 'purpose'`).**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && ./venv/bin/pytest tests/test_patient_portal_auth.py -v -k purpose
```

- [ ] **Step 3: Update `issue_challenge`** in `backend/app/services/patient_portal_auth.py`. Change the signature to accept `purpose`, default `"login"`, and pick the SMS body accordingly:

```python
PURPOSE_COPY = {
    "login":   ("WWC: Your portal sign-in code is {code}. "
                  "Expires in {ttl} minutes."),
    "payment": ("WWC: Code to authorize your payment: {code}. "
                  "Expires in {ttl} minutes. If you didn't request this, ignore."),
}


def issue_challenge(db: Session, surgery: Surgery,
                      purpose: str = "login") -> tuple[str, str]:
    """Generate a code, persist its hash, SMS the plaintext to the surgery's
    cell_phone. Returns (challenge_token, plaintext_code).

    `purpose` picks the SMS copy — "login" (default, sign-in) or "payment"
    (step-up before charge). The lifecycle is identical; only the body
    text changes. The PatientPortalAuthCode row does NOT store purpose —
    the caller is responsible for invoking verify_code from the matching
    endpoint context (and route-level checks prevent cross-purpose abuse).

    Precondition: surgery.cell_phone or surgery.phone must be non-empty.
    If both are blank, the SMS silently no-ops and the patient cannot
    complete the action. Endpoints must validate before calling.
    """
    code = _generate_code()
    challenge_token = secrets.token_urlsafe(32)
    row = PatientPortalAuthCode(
        surgery_id=surgery.id,
        challenge_token=challenge_token,
        code_hash=_bcrypt.hashpw(code.encode(), _bcrypt.gensalt()).decode(),
        expires_at=_now() + timedelta(minutes=CODE_TTL_MINUTES),
        sent_to_phone=surgery.cell_phone or surgery.phone or "",
    )
    db.add(row); db.commit()
    phone = row.sent_to_phone
    template = PURPOSE_COPY.get(purpose, PURPOSE_COPY["login"])
    body = template.format(code=code, ttl=CODE_TTL_MINUTES)
    send_sms(phone, body)
    return challenge_token, code
```

- [ ] **Step 4: Run, confirm 9 tests pass** (the 8 existing + 1 new):

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && ./venv/bin/pytest tests/test_patient_portal_auth.py -v
```

- [ ] **Step 5: Commit.**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/services/patient_portal_auth.py backend/tests/test_patient_portal_auth.py
git commit -m "feat(portal-p2): issue_challenge purpose param (login | payment)"
```

---

## Task 3: Extract slot-claim into a shared service

**Files:**
- Create: `backend/app/services/surgery_self_schedule.py`
- Modify: `backend/app/routers/patient_surgery.py:505` — `patient_select_slot` → call the new service
- Test: `backend/tests/test_surgery_self_schedule.py`

The existing magic-link `POST /select-slot` body does seven things (blackout, parse, overlap, slot insert, surgery stamp, note, calendar+confirmation). We're going to call all of this from BOTH the magic-link router (existing) AND the new portal router (T7). Extract once; reuse twice.

- [ ] **Step 1: Failing test** for the shared service:

```python
"""Shared slot-claim service — used by magic-link + portal flows."""
from datetime import date, time, timedelta
from unittest.mock import patch
from app.models.surgery import Surgery
from app.models.block_day import BlockDay
from app.services.surgery_self_schedule import claim_slot_for_patient, SelfScheduleError


def _seed_bd(db, *, facility="office", days_out=14):
    bd = BlockDay(
        block_date=date.today() + timedelta(days=days_out),
        facility=facility,
        start_time=time(8, 0), end_time=time(15, 0),
        block_kind="office_d_and_c",
    )
    db.add(bd); db.commit(); db.refresh(bd)
    return bd


def _seed_s(db):
    s = Surgery(
        chart_number="1", patient_name="Pat",
        eligible_facilities=["office"], status="new",
        procedure_classification="office_d_and_c",
        estimated_minutes=60,
    )
    db.add(s); db.commit(); db.refresh(s)
    return s


def test_claim_books_the_slot_and_stamps_surgery(db):
    s = _seed_s(db); bd = _seed_bd(db)
    with patch("app.services.surgery_self_schedule.upsert_event_for_surgery"):
        with patch("app.services.surgery_self_schedule._send_surgery_confirmation_email"):
            result = claim_slot_for_patient(
                db, s, block_day_id=str(bd.id),
                start_time_str="08:00",
                sent_by="portal:e2e-test",
            )
    db.refresh(s)
    assert s.scheduled_date == bd.block_date
    assert s.selected_facility == "office"
    assert result["start_time"] == "08:00"
    assert result["block_day_id"] == str(bd.id)


def test_claim_raises_on_blackout(db, monkeypatch):
    s = _seed_s(db); bd = _seed_bd(db)
    # Force is_date_blacked_out to return a truthy "blackout" object
    from collections import namedtuple
    BO = namedtuple("BO", "label reason scope")
    monkeypatch.setattr(
        "app.services.surgery_self_schedule.is_date_blacked_out",
        lambda db, d, fac, surg_email: BO("Doctor away", None, "surgeon"),
    )
    try:
        claim_slot_for_patient(db, s, block_day_id=str(bd.id),
                                  start_time_str="08:00", sent_by="x")
    except SelfScheduleError as e:
        assert "Doctor away" in str(e) or "blocked" in str(e).lower()
        return
    raise AssertionError("expected SelfScheduleError")
```

- [ ] **Step 2: Run, confirm fail (ImportError).**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && ./venv/bin/pytest tests/test_surgery_self_schedule.py -v
```

- [ ] **Step 3: Create the service** at `backend/app/services/surgery_self_schedule.py`. Lift the logic from `patient_surgery.py:505`. Imports + helpers it needs are already in patient_surgery (look for `_parse_hhmm`, `_default_duration_for`, `overlapping_slot`, `is_date_blacked_out`, `upsert_event_for_surgery`, `_send_surgery_confirmation_email`). Some of those live in `patient_surgery.py` itself — for those, copy the functions into the new service (not re-import, since circular import risk).

```python
"""Shared slot-claim logic for patient self-scheduling.

Used by:
  - patient_surgery.py POST /{surgery_id}/select-slot   (magic-link flow)
  - patient_portal.py POST /{sid}/slots/{block_day_id}/claim (portal flow)

The two callers differ only in auth — the booking semantics are identical
and live here so they can't drift.
"""
from __future__ import annotations

import logging
from datetime import time as dtime
from typing import Optional

from sqlalchemy.orm import Session

from app.models.surgery import Surgery, SurgeryNote, SurgerySlot
from app.models.block_day import BlockDay
from app.services.surgery_blackout import is_date_blacked_out
from app.services.surgery_slot_overlap import overlapping_slot
from app.services.surgery_date_picker import _default_duration_for
from app.services.google_calendar_sync import upsert_event_for_surgery
# Import the existing confirmation helper from patient_surgery — we don't
# move it because it's still used by other endpoints there.
from app.routers.patient_surgery import _send_surgery_confirmation_email

log = logging.getLogger(__name__)


class SelfScheduleError(Exception):
    """Raised when a slot claim can't proceed. Carries a patient-facing
    message via str()."""
    def __init__(self, message: str, *, status_code: int = 409):
        super().__init__(message)
        self.status_code = status_code


def _parse_hhmm(s: str) -> dtime:
    h, m = s.split(":")
    return dtime(int(h), int(m))


def claim_slot_for_patient(
    db: Session,
    surgery: Surgery,
    *,
    block_day_id: str,
    start_time_str: str,
    sent_by: str,
) -> dict:
    """Book the slot. Raises SelfScheduleError if blocked.

    Returns: {slot_id, block_day_id, start_time, duration_minutes}
    """
    bd = db.query(BlockDay).filter(BlockDay.id == block_day_id).first()
    if not bd:
        raise SelfScheduleError("Block day not found", status_code=404)

    blackout = is_date_blacked_out(db, bd.block_date, bd.facility,
                                      surgery.surgeon_email)
    if blackout:
        raise SelfScheduleError(
            f"That date is blocked: {blackout.label or blackout.reason} "
            f"({blackout.scope})",
            status_code=409,
        )

    start = _parse_hhmm(start_time_str)
    duration = _default_duration_for(db, surgery, bd)

    conflict = overlapping_slot(db, bd.id, start, duration)
    if conflict:
        raise SelfScheduleError(
            f"That time overlaps an existing slot at "
            f"{conflict.start_time.strftime('%H:%M')} "
            f"({conflict.duration_minutes} min)",
            status_code=409,
        )

    slot = SurgerySlot(
        block_day_id=bd.id, surgery_id=surgery.id,
        start_time=start, duration_minutes=duration,
        procedure_kind=bd.block_kind,
    )
    db.add(slot)
    surgery.scheduled_date = bd.block_date
    surgery.scheduled_start_time = start
    surgery.selected_facility = bd.facility
    db.add(SurgeryNote(
        surgery_id=surgery.id,
        created_by=sent_by,
        content=(f"Patient self-scheduled {bd.block_date} "
                 f"{start.strftime('%H:%M')} ({duration} min) at "
                 f"{bd.facility}."),
    ))
    db.commit()
    db.refresh(slot)

    try:
        upsert_event_for_surgery(db, surgery)
    except Exception as e:
        log.warning("calendar sync failed: %s", e)
    try:
        _send_surgery_confirmation_email(db, surgery, slot, sent_by=sent_by)
    except Exception as e:
        log.warning("confirmation email failed: %s", e)

    return {
        "slot_id": str(slot.id),
        "block_day_id": str(bd.id),
        "start_time": start.strftime("%H:%M"),
        "duration_minutes": duration,
    }
```

- [ ] **Step 4: Update `patient_select_slot`** in `backend/app/routers/patient_surgery.py:505` to call the new service. Keep the existing endpoint signature; replace the body:

```python
@router.post("/{surgery_id}/select-slot")
def patient_select_slot(
    surgery_id: str,
    payload: SelectSlotIn,
    db: Session = Depends(get_db),
    _token: str = Depends(require_patient_token),
):
    """Patient self-schedules into a specific block-day slot by start time.
    Magic-link flow. Portal flow uses /api/patient/portal/{sid}/slots/.../claim."""
    from app.services.surgery_self_schedule import (
        claim_slot_for_patient, SelfScheduleError,
    )
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="surgery not found")
    try:
        result = claim_slot_for_patient(
            db, s,
            block_day_id=payload.block_day_id,
            start_time_str=payload.start_time,
            sent_by="patient:self-service",
        )
    except SelfScheduleError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    return {"ok": True, **result}
```

- [ ] **Step 5: Run the existing magic-link test suite to confirm no regression.** Look for files that exercise `/select-slot`:

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && ./venv/bin/pytest tests/test_patient_select_slot.py tests/test_surgery_self_schedule.py -v
```

Both must pass.

- [ ] **Step 6: Commit.**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/services/surgery_self_schedule.py backend/app/routers/patient_surgery.py \
        backend/tests/test_surgery_self_schedule.py
git commit -m "refactor(surgery): extract patient slot-claim into shared service"
```

---

## Task 4: Schedule gate helper

**Files:**
- Modify: `backend/app/services/surgery_self_schedule.py` — append helper
- Modify: `backend/tests/test_surgery_self_schedule.py` — append tests

- [ ] **Step 1: Failing tests** — append:

```python
def test_gate_passes_when_pt_resp_is_zero(db):
    from app.services.surgery_self_schedule import schedule_gate_for_surgery
    s = _seed_s(db); s.patient_responsibility = 0; db.commit()
    allowed, reason = schedule_gate_for_surgery(s)
    assert allowed is True and reason is None


def test_gate_blocks_when_unpaid(db):
    from app.services.surgery_self_schedule import schedule_gate_for_surgery
    s = _seed_s(db); s.patient_responsibility = 250; s.amount_paid = 0
    db.commit()
    allowed, reason = schedule_gate_for_surgery(s)
    assert allowed is False
    assert "$250.00" in reason


def test_gate_passes_when_fully_paid(db):
    from app.services.surgery_self_schedule import schedule_gate_for_surgery
    s = _seed_s(db); s.patient_responsibility = 250; s.amount_paid = 250
    db.commit()
    allowed, reason = schedule_gate_for_surgery(s)
    assert allowed is True and reason is None


def test_gate_passes_when_coordinator_overrides(db):
    from app.services.surgery_self_schedule import schedule_gate_for_surgery
    s = _seed_s(db); s.patient_responsibility = 250; s.amount_paid = 0
    s.schedule_gate_override = True; db.commit()
    allowed, reason = schedule_gate_for_surgery(s)
    assert allowed is True
```

- [ ] **Step 2: Run, confirm fail (ImportError).**

- [ ] **Step 3: Append to `backend/app/services/surgery_self_schedule.py`:**

```python
def schedule_gate_for_surgery(surgery: Surgery) -> tuple[bool, Optional[str]]:
    """Decide whether a patient may self-schedule.

    Returns (allowed, reason). 'reason' is a patient-facing string when
    not allowed; None when allowed.

    Rules:
      pt_resp <= 0                        → allowed (no balance to pay)
      Surgery.amount_paid >= pt_resp      → allowed (paid in full)
      surgery.schedule_gate_override      → allowed (coordinator override)
      otherwise                           → not allowed, show outstanding amount
    """
    pt_resp = float(surgery.patient_responsibility or 0)
    if pt_resp <= 0:
        return True, None
    paid = float(surgery.amount_paid or 0)
    if paid >= pt_resp:
        return True, None
    if surgery.schedule_gate_override:
        return True, None
    outstanding = pt_resp - paid
    return False, (f"Please make your payment before booking a surgery date. "
                    f"Outstanding balance: ${outstanding:.2f}")
```

- [ ] **Step 4: Run, confirm pass.**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && ./venv/bin/pytest tests/test_surgery_self_schedule.py -v
```

- [ ] **Step 5: Commit.**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/services/surgery_self_schedule.py backend/tests/test_surgery_self_schedule.py
git commit -m "feat(portal-p2): schedule_gate_for_surgery helper"
```

---

## Task 5: GET /payments endpoint

**Files:**
- Modify: `backend/app/routers/patient_portal.py` — append payment-history handler
- Modify: `backend/tests/test_patient_portal_endpoints.py` — append payment tests

- [ ] **Step 1: Failing test** — append:

```python
def test_payments_returns_balance_and_history(client, db):
    from app.services.patient_portal_auth import issue_portal_token
    from app.models.stripe_payment import SurgeryPayment
    s = _seed_surgery(db)
    s.patient_responsibility = 500
    s.amount_paid = 100
    db.add(SurgeryPayment(
        surgery_id=s.id, status="paid",
        amount_requested=100, amount_paid=100,
        requested_by="staff",
    ))
    db.commit()
    token = issue_portal_token(s)
    r = client.get(f"/api/patient/portal/{s.id}/payments",
                     headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert float(body["due"])     == 500
    assert float(body["paid"])    == 100
    assert float(body["balance"]) == 400
    assert len(body["history"]) == 1
    assert body["history"][0]["status"] == "paid"
```

- [ ] **Step 2: Run, confirm fail (404 on endpoint).**

- [ ] **Step 3: Append to `backend/app/routers/patient_portal.py`:**

```python
# ─── /{surgery_id}/payments ─────────────────────────────────────

from app.models.stripe_payment import SurgeryPayment


@router.get("/{surgery_id}/payments")
def portal_payments(surgery_id: str, db: Session = Depends(get_db),
                      _: str = Depends(require_portal_token)):
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="surgery not found")
    due  = float(s.patient_responsibility or 0)
    paid = float(s.amount_paid or 0)
    balance = max(0.0, due - paid)
    history = []
    for p in (s.payments or []):
        history.append({
            "id":        str(p.id),
            "status":    p.status,
            "amount_requested": str(p.amount_requested or 0),
            "amount_paid":      str(p.amount_paid or 0),
            "requested_at":     p.requested_at.isoformat() if p.requested_at else None,
            "paid_at":          p.paid_at.isoformat() if p.paid_at else None,
            "checkout_url":     p.checkout_url,
        })
    return {
        "due":     due,
        "paid":    paid,
        "balance": balance,
        "history": history,
    }
```

- [ ] **Step 4: Run, confirm pass.**

- [ ] **Step 5: Commit.**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/routers/patient_portal.py backend/tests/test_patient_portal_endpoints.py
git commit -m "feat(portal-p2): GET /payments — balance + history"
```

---

## Task 6: Payment step-up + checkout endpoints

**Files:**
- Modify: `backend/app/routers/patient_portal.py` — append step-up + checkout handlers
- Modify: `backend/tests/test_patient_portal_endpoints.py` — append step-up + checkout tests

- [ ] **Step 1: Failing tests** — append:

```python
def test_step_up_sends_payment_purpose_sms(client, db):
    from app.services.patient_portal_auth import issue_portal_token
    s = _seed_surgery(db); s.patient_responsibility = 250; db.commit()
    token = issue_portal_token(s)
    with patch("app.services.patient_portal_auth.send_sms",
                return_value=True) as mock_sms:
        r = client.post(f"/api/patient/portal/{s.id}/payments/step-up",
                          headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    assert "step_up_token" in r.json()
    body = mock_sms.call_args[0][1]
    assert "payment" in body.lower() or "charge" in body.lower()


def test_step_up_blocks_when_no_balance(client, db):
    from app.services.patient_portal_auth import issue_portal_token
    s = _seed_surgery(db); s.patient_responsibility = 0; db.commit()
    token = issue_portal_token(s)
    r = client.post(f"/api/patient/portal/{s.id}/payments/step-up",
                     headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 422  # no outstanding balance


def test_checkout_rejects_invalid_code(client, db):
    from app.services.patient_portal_auth import issue_portal_token
    s = _seed_surgery(db); s.patient_responsibility = 250; db.commit()
    token = issue_portal_token(s)
    with patch("app.services.patient_portal_auth._generate_code",
                return_value="111111"):
        with patch("app.services.patient_portal_auth.send_sms",
                    return_value=True):
            step = client.post(
                f"/api/patient/portal/{s.id}/payments/step-up",
                headers={"Authorization": f"Bearer {token}"}
            ).json()
    r = client.post(
        f"/api/patient/portal/{s.id}/payments/checkout",
        json={"step_up_token": step["step_up_token"], "code": "000000"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 401


def test_checkout_creates_session_with_correct_code(client, db):
    from app.services.patient_portal_auth import issue_portal_token
    from app.models.stripe_payment import SurgeryPayment
    s = _seed_surgery(db); s.patient_responsibility = 250; db.commit()
    token = issue_portal_token(s)

    class FakePay:
        id = "pay_test_id"
        checkout_url = "https://stripe.test/cs_123"

    with patch("app.services.patient_portal_auth._generate_code",
                return_value="111111"), \
         patch("app.services.patient_portal_auth.send_sms",
                return_value=True), \
         patch("app.services.stripe_payments.is_configured",
                return_value=True), \
         patch("app.services.stripe_payments.create_checkout_session",
                return_value=FakePay()):
        step = client.post(
            f"/api/patient/portal/{s.id}/payments/step-up",
            headers={"Authorization": f"Bearer {token}"}
        ).json()
        r = client.post(
            f"/api/patient/portal/{s.id}/payments/checkout",
            json={"step_up_token": step["step_up_token"], "code": "111111"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert r.status_code == 200, r.text
    assert r.json()["checkout_url"].startswith("https://stripe.test/")
```

- [ ] **Step 2: Run, confirm fails.**

- [ ] **Step 3: Add handlers** to `backend/app/routers/patient_portal.py`:

```python
from app.services import stripe_payments as stripe_svc


class StepUpResponse(BaseModel):
    step_up_token: str


@router.post("/{surgery_id}/payments/step-up")
def portal_payments_step_up(
    surgery_id: str,
    db: Session = Depends(get_db),
    _: str = Depends(require_portal_token),
):
    """Send a fresh SMS code; returns challenge_token for the next step.
    Caller must POST /payments/checkout within 5 minutes with the code."""
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="surgery not found")
    due  = float(s.patient_responsibility or 0)
    paid = float(s.amount_paid or 0)
    if due <= 0 or paid >= due:
        raise HTTPException(status_code=422,
                              detail="No outstanding balance to pay.")
    if not (s.cell_phone or s.phone or "").strip():
        raise HTTPException(status_code=409,
                              detail="No phone on file — call our office at "
                                     "240-252-2140.")
    challenge_token, _code = auth.issue_challenge(db, s, purpose="payment")
    return {"step_up_token": challenge_token}


class CheckoutPayload(BaseModel):
    step_up_token: str
    code: str


@router.post("/{surgery_id}/payments/checkout")
def portal_payments_checkout(
    surgery_id: str,
    payload: CheckoutPayload,
    db: Session = Depends(get_db),
    _: str = Depends(require_portal_token),
):
    """Verify the step-up code; create a Stripe Checkout session for the
    outstanding balance. Returns the URL the browser should visit."""
    code = "".join(c for c in (payload.code or "") if c.isdigit())
    if len(code) != 6:
        raise HTTPException(status_code=401, detail="Invalid code")
    matched_sid = auth.verify_code(db, payload.step_up_token, code)
    if matched_sid is None or matched_sid != surgery_id:
        raise HTTPException(status_code=401, detail="Invalid code")
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="surgery not found")
    due  = float(s.patient_responsibility or 0)
    paid = float(s.amount_paid or 0)
    amount = max(0.0, due - paid)
    if amount <= 0:
        raise HTTPException(status_code=422,
                              detail="No outstanding balance to pay.")
    if not stripe_svc.is_configured():
        raise HTTPException(status_code=503,
                              detail="Payments aren't available right now.")
    try:
        pay = stripe_svc.create_checkout_session(
            db, s, amount=amount,
            description="Surgery balance (patient self-service)",
            actor="patient:portal",
        )
    except Exception as e:
        log = logging.getLogger(__name__)
        log.exception("portal checkout create failed")
        raise HTTPException(status_code=502, detail=f"Stripe error: {e}")
    return {"checkout_url": pay.checkout_url, "payment_id": str(pay.id)}
```

Add `import logging` to the imports at the top of the file if it's not already present (T4's polish removed it).

- [ ] **Step 4: Run, confirm pass.**

- [ ] **Step 5: Commit.**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/routers/patient_portal.py backend/tests/test_patient_portal_endpoints.py
git commit -m "feat(portal-p2): POST /payments/step-up + /payments/checkout (SMS step-up gate)"
```

---

## Task 7: GET /slots + POST /slots/{block_day_id}/claim

**Files:**
- Modify: `backend/app/routers/patient_portal.py` — append slots + claim handlers
- Modify: `backend/tests/test_patient_portal_endpoints.py` — append slot tests

- [ ] **Step 1: Failing tests** — append:

```python
def test_slots_returns_gate_state_when_unpaid(client, db):
    from app.services.patient_portal_auth import issue_portal_token
    s = _seed_surgery(db)
    s.patient_responsibility = 250
    s.amount_paid = 0
    s.procedure_classification = "office_d_and_c"
    db.commit()
    token = issue_portal_token(s)
    r = client.get(f"/api/patient/portal/{s.id}/slots",
                     headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["gate"]["allowed"] is False
    assert "$250" in body["gate"]["reason"]
    assert body["block_days"] == []  # hidden when gate blocks


def test_slots_returns_block_days_when_gate_passes(client, db):
    from app.services.patient_portal_auth import issue_portal_token
    from app.models.block_day import BlockDay
    from datetime import date as _d, time as _t, timedelta as _td
    s = _seed_surgery(db)
    s.patient_responsibility = 0
    s.eligible_facilities = ["office"]
    s.procedure_classification = "office_d_and_c"
    s.estimated_minutes = 60
    db.add(BlockDay(
        block_date=_d.today() + _td(days=14),
        facility="office",
        start_time=_t(8, 0), end_time=_t(15, 0),
        block_kind="office_d_and_c",
    ))
    db.commit()
    token = issue_portal_token(s)
    r = client.get(f"/api/patient/portal/{s.id}/slots",
                     headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["gate"]["allowed"] is True
    assert len(body["block_days"]) >= 1


def test_claim_blocks_when_gate_fails(client, db):
    from app.services.patient_portal_auth import issue_portal_token
    from app.models.block_day import BlockDay
    from datetime import date as _d, time as _t, timedelta as _td
    s = _seed_surgery(db)
    s.patient_responsibility = 250  # gate blocks
    s.eligible_facilities = ["office"]
    s.procedure_classification = "office_d_and_c"
    bd = BlockDay(
        block_date=_d.today() + _td(days=14),
        facility="office",
        start_time=_t(8, 0), end_time=_t(15, 0),
        block_kind="office_d_and_c",
    )
    db.add(bd); db.commit(); db.refresh(bd)
    token = issue_portal_token(s)
    r = client.post(
        f"/api/patient/portal/{s.id}/slots/{bd.id}/claim",
        json={"start_time": "08:00"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 409
```

- [ ] **Step 2: Run, confirm fails.**

- [ ] **Step 3: Add handlers** to `backend/app/routers/patient_portal.py`:

```python
# ─── /{surgery_id}/slots ─────────────────────────────────────────

from app.services.surgery_self_schedule import (
    claim_slot_for_patient, SelfScheduleError, schedule_gate_for_surgery,
)


@router.get("/{surgery_id}/slots")
def portal_slots(surgery_id: str, days_ahead: int = 180,
                   db: Session = Depends(get_db),
                   _: str = Depends(require_portal_token)):
    """Available block days for this surgery. When the schedule gate is
    blocked, returns an empty days list along with the reason; the
    frontend renders a payment-prompt banner instead of the picker."""
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="surgery not found")
    allowed, reason = schedule_gate_for_surgery(s)
    if not allowed:
        return {
            "gate": {"allowed": False, "reason": reason},
            "block_days": [],
        }
    # Delegate to the existing patient_surgery /slots logic via a small
    # wrapper. The magic-link router's `patient_slots` is the canonical
    # implementation; we re-export the same payload shape (`days` list)
    # but under a key called `block_days` for portal clarity.
    from app.routers.patient_surgery import patient_slots as _ms_slots
    raw = _ms_slots(surgery_id, days_ahead=days_ahead, db=db, _token="")
    # `_ms_slots` returns dict with `days`. Translate keys.
    return {
        "gate": {"allowed": True, "reason": None},
        "block_days": raw.get("days", []),
        "procedure_kind": raw.get("procedure_kind"),
        "duration_minutes": raw.get("duration_minutes"),
    }


class PortalClaimPayload(BaseModel):
    start_time: str  # "HH:MM"


@router.post("/{surgery_id}/slots/{block_day_id}/claim")
def portal_claim_slot(
    surgery_id: str,
    block_day_id: str,
    payload: PortalClaimPayload,
    db: Session = Depends(get_db),
    _: str = Depends(require_portal_token),
):
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="surgery not found")
    allowed, reason = schedule_gate_for_surgery(s)
    if not allowed:
        raise HTTPException(status_code=409, detail=reason)
    try:
        result = claim_slot_for_patient(
            db, s,
            block_day_id=block_day_id,
            start_time_str=payload.start_time,
            sent_by="patient:portal",
        )
    except SelfScheduleError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    return {"ok": True, **result}
```

The clever bit: `portal_slots` calls `patient_slots` directly (passing an empty `_token=""` since FastAPI dependency injection isn't invoked when you call the function directly — the `_token` parameter just takes its default). This works because `patient_slots` has no other token-dependent logic; the auth is purely the Depends wrapper.

If that gets ugly to maintain, extract the slot-listing into the same `surgery_self_schedule.py` service as a follow-up.

- [ ] **Step 4: Run, confirm pass.**

- [ ] **Step 5: Commit.**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/routers/patient_portal.py backend/tests/test_patient_portal_endpoints.py
git commit -m "feat(portal-p2): GET /slots (gated) + POST /slots/{id}/claim"
```

---

## Task 8: Frontend — Payments page

**Files:**
- Modify (overwrite stub): `frontend/src/pages/portal/stubs/PaymentsStub.jsx` — rename concept inside, replace with real component
- Or move to a new file `frontend/src/pages/portal/Payments.jsx` and update the App.jsx route. Pick whichever feels cleaner; the test is just that `/portal/s/:sid/payments` renders the real page.

- [ ] **Step 1: Rename and write the page.** Rename the file:

```bash
git mv /Users/wwcclaudecode/Documents/wwc-era-project/frontend/src/pages/portal/stubs/PaymentsStub.jsx \
       /Users/wwcclaudecode/Documents/wwc-era-project/frontend/src/pages/portal/Payments.jsx
```

Update the import in `frontend/src/App.jsx` accordingly: `import PaymentsStub from './pages/portal/stubs/PaymentsStub'` → `import Payments from './pages/portal/Payments'`, and the route element.

Overwrite `frontend/src/pages/portal/Payments.jsx` with:

```jsx
import { useState, useEffect, useRef } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useParams, useSearchParams } from 'react-router-dom'
import { portalApi } from '../../lib/portal-api'

function fmtMoney(n) {
  return `$${Number(n).toFixed(2)}`
}

function BalanceCard({ data, onPayClick }) {
  const balance = Number(data.balance)
  if (balance <= 0 && Number(data.due) > 0) {
    return (
      <div className="bg-green-50 border border-green-200 rounded-lg p-4">
        <div className="text-sm text-green-700">Paid in full ✓</div>
        <div className="text-2xl font-semibold text-gray-900 mt-1">
          {fmtMoney(data.paid)}
        </div>
      </div>
    )
  }
  if (Number(data.due) === 0) {
    return (
      <div className="bg-gray-50 border border-gray-200 rounded-lg p-4">
        <div className="text-sm text-gray-600">Nothing to pay</div>
        <p className="text-xs text-gray-500 mt-1">
          Your insurance covers the full cost of this procedure.
        </p>
      </div>
    )
  }
  return (
    <div className="bg-plum-50 border border-plum-200 rounded-lg p-4">
      <div className="text-sm text-plum-700">You owe</div>
      <div className="text-3xl font-semibold text-gray-900 mt-1">
        {fmtMoney(balance)}
      </div>
      <button onClick={onPayClick} className="btn-primary mt-3">
        Pay now
      </button>
    </div>
  )
}

function PayFlow({ sid, onDone, onCancel }) {
  const [stage, setStage] = useState('sending')   // 'sending' | 'code' | 'redirecting' | 'error'
  const [token, setToken] = useState(null)
  const [digits, setDigits] = useState(['','','','','',''])
  const [err, setErr] = useState('')
  const refs = useRef([])

  // Send code on mount
  useEffect(() => {
    let cancelled = false
    portalApi.post(`/${sid}/payments/step-up`).then(r => {
      if (cancelled) return
      setToken(r.data.step_up_token)
      setStage('code')
    }).catch(e => {
      if (cancelled) return
      setErr(e?.response?.data?.detail || 'Could not start payment.')
      setStage('error')
    })
    return () => { cancelled = true }
  }, [sid])

  function setDigit(i, v) {
    const c = v.replace(/\D/g, '').slice(-1)
    const next = [...digits]; next[i] = c; setDigits(next)
    if (c && i < 5) refs.current[i+1]?.focus()
  }

  async function submit(e) {
    e?.preventDefault?.()
    const code = digits.join('')
    if (code.length !== 6) return
    setErr(''); setStage('redirecting')
    try {
      const { data } = await portalApi.post(`/${sid}/payments/checkout`, {
        step_up_token: token, code,
      })
      window.location.assign(data.checkout_url)
    } catch (e) {
      setErr(e?.response?.data?.detail || 'Invalid code.')
      setStage('code')
    }
  }

  useEffect(() => {
    if (stage === 'code' && digits.every(d => d !== '')) submit()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [digits, stage])

  if (stage === 'sending') {
    return <div className="text-sm text-gray-500 mt-4">Sending you a code…</div>
  }
  if (stage === 'redirecting') {
    return <div className="text-sm text-gray-500 mt-4">Redirecting to Stripe…</div>
  }
  if (stage === 'error') {
    return (
      <div className="mt-4">
        <div className="text-sm text-red-600">{err}</div>
        <button onClick={onCancel} className="btn-secondary mt-2">Back</button>
      </div>
    )
  }
  return (
    <form onSubmit={submit} className="mt-4 space-y-3">
      <div className="text-sm text-gray-600">
        Enter the 6-digit code we just texted you. (5 min expiry.)
      </div>
      <div className="flex gap-2">
        {digits.map((d, i) => (
          <input key={i}
                  ref={el => refs.current[i] = el}
                  type="text" inputMode="numeric"
                  maxLength={1} value={d}
                  onChange={e => setDigit(i, e.target.value)}
                  className="w-10 h-12 text-center text-lg rounded border-gray-300" />
        ))}
      </div>
      {err && <div className="text-sm text-red-600">{err}</div>}
      <div className="flex gap-2">
        <button type="submit" disabled={digits.join('').length !== 6}
                 className="btn-primary">Continue</button>
        <button type="button" onClick={onCancel}
                 className="btn-secondary">Cancel</button>
      </div>
    </form>
  )
}

function History({ rows }) {
  if (!rows?.length) return null
  return (
    <section className="bg-white rounded-lg shadow p-4">
      <h2 className="text-sm font-semibold text-gray-700 mb-3">History</h2>
      <ul className="divide-y divide-gray-100">
        {rows.map(r => (
          <li key={r.id} className="py-2 flex items-center justify-between text-sm">
            <span>{(r.paid_at || r.requested_at || '').slice(0, 10)}</span>
            <span className="text-gray-900">{fmtMoney(r.amount_paid)}</span>
            <span className={`text-xs px-2 py-1 rounded ${
              r.status === 'paid' ? 'bg-green-100 text-green-700' :
              r.status === 'failed' ? 'bg-red-100 text-red-700' :
              'bg-gray-200 text-gray-700'
            }`}>{r.status}</span>
          </li>
        ))}
      </ul>
    </section>
  )
}

export default function Payments() {
  const { sid } = useParams()
  const [sp] = useSearchParams()
  const qc = useQueryClient()
  const [showFlow, setShowFlow] = useState(false)
  const { data, isLoading } = useQuery({
    queryKey: ['portal-payments', sid],
    queryFn: () => portalApi.get(`/${sid}/payments`).then(r => r.data),
    refetchInterval: sp.get('session_id') ? 2000 : false,
    staleTime: 10_000,
  })

  // Stop polling when balance drops to 0 (webhook caught up)
  useEffect(() => {
    if (data && Number(data.balance) === 0 && sp.get('session_id')) {
      qc.invalidateQueries({ queryKey: ['portal-dashboard', sid] })
    }
  }, [data, sid, sp, qc])

  if (isLoading) return <div className="text-sm text-gray-500">Loading…</div>
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold text-gray-900">Payments</h1>
      <BalanceCard data={data} onPayClick={() => setShowFlow(true)} />
      {showFlow && (
        <PayFlow sid={sid} onCancel={() => setShowFlow(false)} onDone={() => setShowFlow(false)} />
      )}
      <History rows={data.history} />
    </div>
  )
}
```

- [ ] **Step 2: Build check.**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/frontend && npm run build 2>&1 | tail -8
```

- [ ] **Step 3: Commit.**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add frontend/src/pages/portal/ frontend/src/App.jsx
git commit -m "feat(portal-p2): Payments page — balance, step-up SMS, history"
```

---

## Task 9: Frontend — Schedule page

**Files:**
- Modify: `frontend/src/pages/portal/stubs/ScheduleStub.jsx` → rename to `frontend/src/pages/portal/Schedule.jsx`
- Modify: `frontend/src/App.jsx` import + route element

- [ ] **Step 1: Rename + write the page.**

```bash
git mv /Users/wwcclaudecode/Documents/wwc-era-project/frontend/src/pages/portal/stubs/ScheduleStub.jsx \
       /Users/wwcclaudecode/Documents/wwc-era-project/frontend/src/pages/portal/Schedule.jsx
```

Update `App.jsx` to import + route the new path.

Overwrite `frontend/src/pages/portal/Schedule.jsx` with:

```jsx
import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useParams, Link } from 'react-router-dom'
import { portalApi } from '../../lib/portal-api'

function GateBanner({ gate, sid }) {
  return (
    <div className="bg-amber-50 border border-amber-200 rounded-lg p-4">
      <div className="text-sm text-amber-700 font-medium">
        Payment required before scheduling
      </div>
      <p className="text-sm text-gray-700 mt-1">{gate.reason}</p>
      <Link to={`/portal/s/${sid}/payments`}
            className="btn-primary mt-3 inline-block">
        Go to Payments
      </Link>
    </div>
  )
}

function BlockDayList({ days, onPick }) {
  if (!days?.length) {
    return (
      <div className="bg-white rounded-lg shadow p-4 text-sm text-gray-600">
        No open dates within the next 6 months. Please call our office at
        <a className="text-plum-700 underline ml-1" href="tel:2402522140">240-252-2140</a>.
      </div>
    )
  }
  return (
    <section className="bg-white rounded-lg shadow p-4">
      <h2 className="text-sm font-semibold text-gray-700 mb-3">
        Open dates
      </h2>
      <ul className="divide-y divide-gray-100">
        {days.map(d => (
          <li key={`${d.block_day_id}-${d.proposed_start_time}`}
              className="py-3 flex items-center justify-between">
            <div>
              <div className="text-sm font-medium text-gray-900">
                {d.weekday}, {d.block_date}
              </div>
              <div className="text-xs text-gray-500 mt-0.5">
                Arrive at {d.proposed_start_time} · {d.facility}
                {d.cases_already_booked > 0 ? ` · ${d.cases_already_booked} other case(s) that day` : ''}
              </div>
            </div>
            <button onClick={() => onPick(d)} className="btn-primary text-sm">
              Pick this date
            </button>
          </li>
        ))}
      </ul>
    </section>
  )
}

function ConfirmModal({ day, onConfirm, onCancel, busy }) {
  if (!day) return null
  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center p-4 z-50">
      <div className="bg-white rounded-lg shadow-lg p-5 max-w-sm w-full space-y-3">
        <h3 className="font-semibold text-gray-900">Confirm your surgery date</h3>
        <p className="text-sm text-gray-600">
          {day.weekday}, {day.block_date} at {day.proposed_start_time}<br />
          {day.facility}
        </p>
        <div className="flex justify-end gap-2 pt-2">
          <button onClick={onCancel} className="btn-secondary">Cancel</button>
          <button onClick={onConfirm} disabled={busy} className="btn-primary">
            {busy ? 'Booking…' : 'Confirm'}
          </button>
        </div>
      </div>
    </div>
  )
}

export default function Schedule() {
  const { sid } = useParams()
  const qc = useQueryClient()
  const [picked, setPicked] = useState(null)
  const [err, setErr] = useState('')

  const { data, isLoading } = useQuery({
    queryKey: ['portal-slots', sid],
    queryFn: () => portalApi.get(`/${sid}/slots`).then(r => r.data),
    staleTime: 30_000,
  })

  const claim = useMutation({
    mutationFn: () => portalApi.post(
      `/${sid}/slots/${picked.block_day_id}/claim`,
      { start_time: picked.proposed_start_time },
    ).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['portal-dashboard', sid] })
      qc.invalidateQueries({ queryKey: ['portal-slots', sid] })
      setPicked(null)
    },
    onError: (e) => setErr(e?.response?.data?.detail || 'Could not book.'),
  })

  if (isLoading) return <div className="text-sm text-gray-500">Loading…</div>
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold text-gray-900">Schedule</h1>
      {!data.gate.allowed ? (
        <GateBanner gate={data.gate} sid={sid} />
      ) : (
        <>
          <BlockDayList days={data.block_days} onPick={setPicked} />
          {err && <div className="text-sm text-red-600">{err}</div>}
        </>
      )}
      <ConfirmModal day={picked}
                       onCancel={() => setPicked(null)}
                       onConfirm={() => claim.mutate()}
                       busy={claim.isPending} />
    </div>
  )
}
```

- [ ] **Step 2: Build check.**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/frontend && npm run build 2>&1 | tail -8
```

- [ ] **Step 3: Commit.**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add frontend/src/pages/portal/ frontend/src/App.jsx
git commit -m "feat(portal-p2): Schedule page — gate banner, day picker, confirm modal"
```

---

## Task 10: Drop "soon" suffix from Payments + Schedule nav

**Files:**
- Modify: `frontend/src/pages/portal/PortalShell.jsx`

- [ ] **Step 1: Update the NAV array.** Remove `comingSoon: true` from `payments` and `schedule`:

```jsx
const NAV = [
  { to: '',          label: 'Dashboard' },
  { to: 'payments',  label: 'Payments' },
  { to: 'schedule',  label: 'Schedule' },
  { to: 'consent',   label: 'Consent',   comingSoon: true },
  { to: 'documents', label: 'Documents', comingSoon: true },
  { to: 'messages',  label: 'Messages',  comingSoon: true },
]
```

- [ ] **Step 2: Build check.**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/frontend && npm run build 2>&1 | tail -8
```

- [ ] **Step 3: Commit.**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add frontend/src/pages/portal/PortalShell.jsx
git commit -m "feat(portal-p2): drop 'soon' from Payments + Schedule nav items"
```

---

## Task 11: Staff UI — schedule gate override toggle

**Files:**
- Modify: `backend/app/routers/surgery.py` — add a `PATCH /surgery/{id}/schedule-gate-override` endpoint (staff-only, audited)
- Modify: `frontend/src/pages/SurgeryDetail.jsx` — small checkbox + label in the existing admin section
- Test: `backend/tests/test_surgery_schedule_gate_override.py`

The coordinator needs a way to flip `Surgery.schedule_gate_override` from True/False. P2 backend respects the flag; staff UI controls it.

- [ ] **Step 1: Failing tests** at `backend/tests/test_surgery_schedule_gate_override.py`:

```python
"""Staff endpoint for flipping schedule_gate_override."""
from app.models.surgery import Surgery


def test_override_flag_starts_false(client, db, staff_token):
    s = Surgery(chart_number="1", patient_name="Pat", status="new")
    db.add(s); db.commit(); db.refresh(s)
    assert s.schedule_gate_override is False


def test_staff_can_enable_override(client, db, staff_token):
    s = Surgery(chart_number="1", patient_name="Pat", status="new")
    db.add(s); db.commit(); db.refresh(s)
    r = client.patch(f"/api/surgery/{s.id}/schedule-gate-override",
                       headers={"Authorization": f"Bearer {staff_token}"},
                       json={"enabled": True})
    assert r.status_code == 200, r.text
    db.refresh(s)
    assert s.schedule_gate_override is True
    assert s.schedule_gate_override_at is not None
    assert s.schedule_gate_override_by is not None


def test_staff_can_disable_override(client, db, staff_token):
    s = Surgery(chart_number="1", patient_name="Pat",
                  status="new", schedule_gate_override=True)
    db.add(s); db.commit(); db.refresh(s)
    r = client.patch(f"/api/surgery/{s.id}/schedule-gate-override",
                       headers={"Authorization": f"Bearer {staff_token}"},
                       json={"enabled": False})
    assert r.status_code == 200
    db.refresh(s)
    assert s.schedule_gate_override is False
```

Look at existing tests in `tests/` for how `staff_token` is fixtured — it's likely in `conftest.py`. If not, model it after another staff-only endpoint test.

- [ ] **Step 2: Run, confirm fail (404 on endpoint).**

- [ ] **Step 3: Add the endpoint** to `backend/app/routers/surgery.py`. Find an existing PATCH endpoint for a Surgery field as a template (e.g. one that sets `urgency` or `complexity`). Append a new handler following that pattern:

```python
class ScheduleGateOverridePayload(BaseModel):
    enabled: bool


@router.patch("/{surgery_id}/schedule-gate-override")
def patch_schedule_gate_override(
    surgery_id: str,
    payload: ScheduleGateOverridePayload,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_permission("surgery:work")),
):
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="surgery not found")
    s.schedule_gate_override = payload.enabled
    s.schedule_gate_override_at = datetime.utcnow()
    s.schedule_gate_override_by = current_user.get("email") or "system"
    db.commit()
    return {
        "ok": True,
        "schedule_gate_override": s.schedule_gate_override,
        "schedule_gate_override_at": s.schedule_gate_override_at.isoformat(),
        "schedule_gate_override_by": s.schedule_gate_override_by,
    }
```

- [ ] **Step 4: Run, confirm pass.**

- [ ] **Step 5: Frontend toggle.** In `frontend/src/pages/SurgeryDetail.jsx`, find a logical home for a small admin checkbox (likely near the urgency / complexity selectors). Add:

```jsx
// useMutation hook somewhere alongside other admin mutations:
const gateOverride = useMutation({
  mutationFn: (enabled) =>
    api.patch(`/surgery/${surgery.id}/schedule-gate-override`,
              { enabled }).then(r => r.data),
  onSuccess: () => qc.invalidateQueries({ queryKey: ['surgery', surgery.id] }),
})

// In the JSX (find the admin block):
<label className="flex items-center gap-2 text-xs text-gray-700">
  <input type="checkbox"
          checked={!!surgery.schedule_gate_override}
          onChange={e => gateOverride.mutate(e.target.checked)} />
  Allow patient to self-schedule without payment
</label>
```

Adjust the surrounding container if needed.

- [ ] **Step 6: Build check.**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/frontend && npm run build 2>&1 | tail -8
```

- [ ] **Step 7: Commit.**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/routers/surgery.py frontend/src/pages/SurgeryDetail.jsx \
        backend/tests/test_surgery_schedule_gate_override.py
git commit -m "feat(portal-p2): coordinator can override schedule gate per surgery"
```

---

## Task 12: Smoke test in prod (manual)

Done after Tasks 1–11 are merged and deployed. I drive this.

- [ ] **Step 1: Push + build + deploy.**
  - Push all P2 commits
  - Build backend `v42`, frontend `v_portal_p2`
  - Run `scripts/migrate_patient_portal_p2.py` against Cloud SQL
  - Deploy both

- [ ] **Step 2: Insert a test surgery** with my cell, `patient_responsibility = 250`, `amount_paid = 0`, eligible_facility office, procedure_classification office_d_and_c. (Same pattern as P1 smoke.)

- [ ] **Step 3: Portal sign-in** (`/portal/login` → /verify → dashboard). Confirm Payments + Schedule nav items no longer show "· soon".

- [ ] **Step 4: Hit Payments.**
  - "You owe $250" card
  - Click "Pay now"
  - Receive SMS with "payment" copy (NOT sign-in copy)
  - Enter code
  - Land on Stripe Checkout for $250

- [ ] **Step 5: Run a $0.50 test card** (use Stripe's `4242 4242 4242 4242` in TEST mode, or a real card with a small `patient_responsibility` if live).

- [ ] **Step 6: After redirect back to `/portal/s/{sid}/payments?session_id=...`:** confirm history list shows the just-paid row within 10 seconds (polling kicks in).

- [ ] **Step 7: Hit Schedule.** Should now show the open dates (gate passes since paid in full). Pick one. Confirm modal. Confirm. Should see:
  - Dashboard updates to show scheduled date
  - SMS + email confirmation arrive
  - Google Calendar event created on the surgeon's calendar (verify in surgeon's calendar)

- [ ] **Step 8: Cleanup** the test surgery + payment row + auth-code rows. Refund the test charge in Stripe dashboard if it was real. Close Cloud SQL public IP.

- [ ] **Step 9: Report results** with revision SHAs and what failed (if anything).
