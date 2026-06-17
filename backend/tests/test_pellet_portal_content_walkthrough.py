"""Authenticated walk-through of the enriched pellet portal content: a patient
sees their appointment history (with dosage), receipts, and the Rules & Info
block; a staff read-only preview token can view all of them too.
"""
from datetime import date
from decimal import Decimal
import pytest

from app.models.pellet import PelletPatient, PelletVisit, PelletVisitDose, PelletDoseType
from app.models.pellet_payment import PelletPayment
from app.services.pellet import portal_auth
from app.utils.dt import now_utc_naive


@pytest.fixture
def seeded(db):
    p = PelletPatient(patient_name="Doe, Jane", chart_number="MRN1",
                      patient_dob=date(1980, 5, 1), patient_phone="3015551234")
    db.add(p); db.flush()
    dt = PelletDoseType(hormone="estradiol", dose_mg=12.5, label="Estradiol 12.5mg")
    db.add(dt); db.flush()
    v = PelletVisit(patient_id=p.id, visit_kind="repeat", status="inserted",
                    scheduled_date=date(2026, 5, 1), location="white_plains",
                    provider="Cooke, Aryian, MD", inserted_at=now_utc_naive())
    db.add(v); db.flush()
    db.add(PelletVisitDose(visit_id=v.id, dose_type_id=dt.id, quantity=2))
    db.add(PelletPayment(pellet_patient_id=p.id, kind="single", amount=Decimal("400.00"),
                         status="paid", requested_by="patient",
                         stripe_payment_intent_id="pi_1", paid_at=now_utc_naive()))
    db.commit(); db.refresh(p)
    return p


def test_portal_content_walkthrough(client, db, seeded, capsys):
    log = []
    p = seeded
    h = {"Authorization": f"Bearer {portal_auth.issue_portal_token(p)}"}

    appts = client.get("/api/pellet-portal/appointments", headers=h).json()["items"]
    assert len(appts) == 1 and appts[0]["doses"] == [{"label": "Estradiol 12.5mg", "quantity": 2}]
    log.append(f"1. /appointments → 1 visit on {appts[0]['scheduled_date']} at "
               f"{appts[0]['location']} w/ {appts[0]['provider']}, dosage Estradiol 12.5mg ×2")

    recs = client.get("/api/pellet-portal/receipts", headers=h).json()["items"]
    assert len(recs) == 1 and recs[0]["has_receipt"] is True and recs[0]["amount"] == 400.0
    log.append(f"2. /receipts → 1 paid receipt (${recs[0]['amount']:.2f}, {recs[0]['kind_label']}), "
               "Stripe receipt link available")

    info = client.get("/api/pellet-portal/info", headers=h).json()
    assert "info_text" in info and len(info["info_text"]) > 0
    log.append("3. /info → Rules & Info block returned (staff-editable markdown)")

    # Staff read-only preview token sees the same content.
    tok = client.post(f"/api/pellets/patients/{p.id}/portal-preview-token").json()["token"]
    sh = {"Authorization": f"Bearer {tok}"}
    assert client.get("/api/pellet-portal/appointments", headers=sh).status_code == 200
    assert client.get("/api/pellet-portal/receipts", headers=sh).status_code == 200
    assert client.get("/api/pellet-portal/info", headers=sh).status_code == 200
    log.append("4. staff 'View as patient' preview token → all three pages viewable (read-only)")

    with capsys.disabled():
        print("\n  -- Pellet portal patient-content walk-through (authenticated) --")
        for line in log:
            print("   " + line)
