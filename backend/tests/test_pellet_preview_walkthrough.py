"""Authenticated walk-through of the pellet "View as Patient" staff preview:
staff mints a read-only preview token, GETs the patient's portal (works),
and is blocked (403) from any mutating action while impersonating.
"""
from datetime import date
import pytest

from app.models.pellet import PelletPatient
from app.services.pellet import portal_auth


@pytest.fixture
def patient(db):
    p = PelletPatient(patient_name="Doe, Jane", chart_number="MRN1",
                      patient_dob=date(1980, 5, 1), patient_phone="3015551234")
    db.add(p); db.commit(); db.refresh(p)
    return p


def test_preview_walkthrough(client, db, patient, capsys):
    log = []

    # 1. Staff mints a read-only portal-preview token (also writes an IMPERSONATE audit row).
    r = client.post(f"/api/pellets/patients/{patient.id}/portal-preview-token")
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    claims = portal_auth.decode_portal_token(token)
    assert claims["viewer"].startswith("staff:")
    log.append(f"1. staff minted preview token (viewer={claims['viewer']}, read-only)")

    h = {"Authorization": f"Bearer {token}"}

    # 2. Staff can VIEW the patient's portal (GET works).
    d = client.get("/api/pellet-portal/dashboard", headers=h)
    assert d.status_code == 200
    log.append("2. GET /dashboard with preview token → 200 (staff sees the patient's portal)")

    # 3. Staff CANNOT act as the patient — mutating calls are blocked read-only.
    book = client.post("/api/pellet-portal/payment/subscribe", headers=h)
    assert book.status_code == 403 and "read-only" in book.json()["detail"].lower()
    labs = client.post("/api/pellet-portal/labs", json={"completed": True}, headers=h)
    assert labs.status_code == 403
    log.append("3. POST /payment/subscribe and /labs → 403 'Preview mode is read-only' (no acting as patient)")

    with capsys.disabled():
        print("\n  -- Pellet 'View as Patient' preview walk-through (authenticated) --")
        for line in log:
            print("   " + line)
