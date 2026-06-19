"""End-to-end surgery lifecycle regression suite.

Primary "did an update break the core flow" test. Walks a surgery through
its full lifecycle via the real API and asserts the status transitions +
key side effects at each step:

    create (manual) → classify/duration (PATCH) → provision capacity
    (seed BlockDay) → schedule (coordinator) → consent signed → boarding
    slip → complete

A second test exercises the CANCEL pipeline and the PATCH-status guard.

Uses the super-admin `client` fixture (bypasses all permission tiers).

Seams that touch external services are stubbed:
  - maybe_assign_surgery_number → Postgres nextval sequence (no SQLite seq)
  - boarding-slip save_blob → avoids writing a generated PDF to disk
Both are seams only; the assertions still exercise the real handler logic.
"""
from datetime import date, time, timedelta
from unittest.mock import patch

import pytest

from app.models.surgery import (
    Surgery, BlockDay, SurgerySlot, SurgeryFile,
    ConsentTemplate, SurgeryConsentEnvelope,
)


# next_surgery_number() relies on a Postgres sequence (nextval) the SQLite
# test DB doesn't have; stub it for every test in this module exactly as
# tests/test_surgery_manual_intake.py does.
@pytest.fixture(autouse=True)
def _no_pg_sequence():
    with patch(
        "app.services.surgery.local_helpers.maybe_assign_surgery_number",
        return_value="SUR00001",
    ):
        yield


def _manual_payload(**overrides):
    """Minimal valid body for POST /api/surgery/manual.

    eligible_facilities=["medstar"] → selected_facility auto-set to medstar
    (single eligible facility), which the boarding slip + scheduling rely on.
    """
    p = {
        "chart_number": "E2E100",
        "patient_name": "Doe, Jane",
        "first_name": "Jane",
        "last_name": "Doe",
        "dob": "1985-03-12",
        "phone": "240-555-0101",
        "email": "jane.e2e@example.com",
        "address_street": "1 Main St",
        "address_city": "Waldorf",
        "address_state": "MD",
        "address_zip": "20601",
        "primary_insurance": "Aetna",
        "primary_member_id": "A123",
        "surgeon_primary": "Aryian Cooke, MD",
        "surgery_name": "Robotic Hysterectomy",
        # A robotic CPT so the classifier lands on a robotic kind; we also
        # set procedure_classification explicitly below for determinism.
        "procedures": [{"cpt": "58573", "description": "Robotic hysterectomy"}],
        "diagnoses": [{"icd": "D25.1", "description": "Leiomyoma"}],
        "eligible_facilities": ["medstar"],
        "estimated_minutes": 180,
        "preop_date": "2026-07-01",
    }
    p.update(overrides)
    return p


def _seed_medstar_block_day(db, *, days_ahead=21):
    """Seed a future MedStar BlockDay with capacity for a robotic_180 case.

    can_fit() keys capacity on block_day.facility ('medstar' → robotic rule,
    options robotic_180×3 / robotic_240×2); block_kind only feeds
    _default_duration_for. 7:00–15:00 = 480 min window so a 180-min case fits.
    No blackout is seeded on this date.
    """
    bd = BlockDay(
        facility="medstar",
        block_date=date.today() + timedelta(days=days_ahead),
        block_kind="robotic_180",
        start_time=time(7, 0),
        end_time=time(15, 0),
    )
    db.add(bd)
    db.commit()
    db.refresh(bd)
    return bd


# ─── Test 1: full happy-path lifecycle ──────────────────────────────────

def test_surgery_full_lifecycle_happy_path(client, db):
    # 1. CREATE ----------------------------------------------------------
    resp = client.post("/api/surgery/manual", json=_manual_payload())
    assert resp.status_code == 201, resp.text
    sid = resp.json()["id"]
    # Manual intake always starts at 'incomplete'.
    assert resp.json()["status"] == "incomplete"

    # 2. CLASSIFY + DURATION (PATCH) -------------------------------------
    # robotic_180 is directly bookable at MedStar per can_fit's robotic rule.
    # Setting duration_minutes=180 makes the coordinator-schedule default
    # equal the supplied duration, so no override_reason is required.
    # Also advance status to 'new' so book_slot flips it to 'confirmed'
    # (book_slot only confirms surgeries in {new, in_progress}).
    r = client.patch(f"/api/surgery/{sid}", json={
        "procedure_classification": "robotic_180",
        "duration_minutes": 180,
        "status": "new",
    })
    assert r.status_code == 200, r.text
    db.expire_all()
    s = db.query(Surgery).filter(Surgery.id == sid).first()
    assert s.procedure_classification == "robotic_180"
    assert s.duration_minutes == 180
    assert s.status == "new"

    # 3. PROVISION CAPACITY (seed BlockDay, no blackout) -----------------
    bd = _seed_medstar_block_day(db)

    # 4. SCHEDULE (coordinator) ------------------------------------------
    # 07:00 start, 180 min duration == template default → no override_reason.
    r = client.post(f"/api/surgery/{sid}/schedule", json={
        "block_day_id": str(bd.id),
        "start_time": "07:00",
        "duration_minutes": 180,
    })
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True

    db.expire_all()
    s = db.query(Surgery).filter(Surgery.id == sid).first()
    assert s.status == "confirmed"
    assert s.scheduled_date == bd.block_date
    assert s.scheduled_start_time == time(7, 0)
    slot = db.query(SurgerySlot).filter(SurgerySlot.surgery_id == sid).first()
    assert slot is not None
    assert slot.procedure_kind == "robotic_180"
    assert slot.duration_minutes == 180

    # 5. CONSENT → signed ------------------------------------------------
    # consent/sent flips consent_status='sent'. consent/signed requires an
    # uploaded SurgeryFile(kind='consent') as evidence (Fable audit M3), so
    # seed that file directly rather than calling the BoldSign send path.
    r = client.post(f"/api/surgery/{sid}/consent/sent", json={})
    assert r.status_code == 200, r.text
    db.expire_all()
    s = db.query(Surgery).filter(Surgery.id == sid).first()
    assert s.consent_status == "sent"

    db.add(SurgeryFile(
        surgery_id=sid, kind="consent", filename="signed_consent.pdf",
        path="surgery-files/signed_consent.pdf", mime_type="application/pdf",
    ))
    db.commit()

    r = client.post(f"/api/surgery/{sid}/consent/signed", json={})
    assert r.status_code == 200, r.text
    db.expire_all()
    s = db.query(Surgery).filter(Surgery.id == sid).first()
    assert s.consent_status == "signed"
    assert s.consent_signed_at is not None

    # 6. BOARDING SLIP ---------------------------------------------------
    # selected_facility=='medstar' satisfies the boarding-slip facility gate.
    # The MedStar template ships in the image; patch save_blob so the
    # generated PDF isn't written to disk under test (seam only — the real
    # PDF-fill logic still runs and a SurgeryFile row is created).
    with patch("app.services.surgery.boarding_slip.save_blob",
               return_value="surgery-files/boarding_slip.pdf"):
        r = client.post(f"/api/surgery/{sid}/boarding-slip", json={})
    assert r.status_code == 200, r.text
    file_id = r.json()["id"]
    assert file_id
    bs = (db.query(SurgeryFile)
            .filter(SurgeryFile.surgery_id == sid,
                    SurgeryFile.kind == "boarding_slip")
            .first())
    assert bs is not None

    # 7. COMPLETE --------------------------------------------------------
    r = client.patch(f"/api/surgery/{sid}", json={"status": "completed"})
    assert r.status_code == 200, r.text
    db.expire_all()
    s = db.query(Surgery).filter(Surgery.id == sid).first()
    assert s.status == "completed"
    assert s.completed_at is not None


# ─── Test 2: cancel pipeline + PATCH-status guard ───────────────────────

def test_surgery_cancel_pipeline_releases_slot_and_blocks_patch(client, db):
    # Create + advance + schedule (same path as the happy test).
    resp = client.post("/api/surgery/manual",
                       json=_manual_payload(chart_number="E2E200",
                                            email="cancel.e2e@example.com"))
    assert resp.status_code == 201, resp.text
    sid = resp.json()["id"]

    r = client.patch(f"/api/surgery/{sid}", json={
        "procedure_classification": "robotic_180",
        "duration_minutes": 180,
        "status": "new",
    })
    assert r.status_code == 200, r.text

    bd = _seed_medstar_block_day(db, days_ahead=28)
    r = client.post(f"/api/surgery/{sid}/schedule", json={
        "block_day_id": str(bd.id),
        "start_time": "07:00",
        "duration_minutes": 180,
    })
    assert r.status_code == 200, r.text
    assert db.query(SurgerySlot).filter(SurgerySlot.surgery_id == sid).count() == 1

    # GUARD: PATCH status='cancelled' must be rejected (must go through the
    # cancel endpoint so the slot is released + audit row written).
    r = client.patch(f"/api/surgery/{sid}", json={"status": "cancelled"})
    assert r.status_code == 409, r.text

    # CANCEL via the dedicated endpoint.
    r = client.post(f"/api/surgery/{sid}/cancel", json={"reason": "patient"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "cancelled"

    db.expire_all()
    s = db.query(Surgery).filter(Surgery.id == sid).first()
    assert s.status == "cancelled"
    # Slot released, scheduled_date cleared.
    assert db.query(SurgerySlot).filter(SurgerySlot.surgery_id == sid).count() == 0
    assert s.scheduled_date is None
    assert s.scheduled_start_time is None
