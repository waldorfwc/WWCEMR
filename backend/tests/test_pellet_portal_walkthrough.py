"""Authenticated end-to-end Phase-1 walk-through: patient logs in (token),
uploads a mammogram + self-reports labs + (consent signed), staff verify via
the feed; the requirement checklist flips todo → done."""
from datetime import date, timedelta
import io
import pytest
from app.models.pellet import PelletPatient
from app.models.pellet_portal import PelletConsent
from app.services.pellet import portal_auth
from app.utils.dt import now_utc_naive


@pytest.fixture(autouse=True)
def _local_storage_root(tmp_path, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "documents_local_root", str(tmp_path))


def test_phase1_walkthrough(client, db, capsys):
    log = []
    p = PelletPatient(patient_name="Doe, Jane", chart_number="MRN1",
                      patient_dob=date(1980, 5, 1), patient_phone="3015551234",
                      patient_email="j@x.com")
    db.add(p); db.commit(); db.refresh(p)
    h = {"Authorization": f"Bearer {portal_auth.issue_portal_token(p)}"}

    reqs = {r["key"]: r["status"] for r in
            client.get("/api/pellet-portal/dashboard", headers=h).json()["requirements"]}
    assert reqs == {"mammo": "todo", "labs": "todo", "consent": "todo"}
    log.append("1. dashboard: mammo/labs/consent all 'todo'")

    client.post("/api/pellet-portal/mammo",
                files={"file": ("m.pdf", io.BytesIO(b"%PDF x"), "application/pdf")}, headers=h)
    client.post("/api/pellet-portal/labs", json={"completed": True}, headers=h)
    log.append("2. patient uploaded mammo + self-reported labs (both pending)")

    feed = client.get("/api/pellets/activity").json()["items"]
    for a in feed:
        if a["kind"] in ("mammo_uploaded", "labs_self_reported"):
            r = client.post(f"/api/pellets/activity/{a['id']}/verify")
            assert r.status_code == 200, r.text
    log.append("3. staff verified mammo + labs via the feed")

    db.add(PelletConsent(pellet_patient_id=p.id, boldsign_envelope_id="e1",
                         status="signed", signed_at=now_utc_naive(),
                         expires_at=now_utc_naive() + timedelta(days=365)))
    db.commit()

    reqs2 = {r["key"]: r["status"] for r in
             client.get("/api/pellet-portal/dashboard", headers=h).json()["requirements"]}
    assert reqs2 == {"mammo": "done", "labs": "done", "consent": "done"}
    log.append("4. dashboard now: mammo/labs/consent all 'done' — ready for payment phase")

    with capsys.disabled():
        print("\n  -- Pellet portal Phase-1 walk-through (authenticated) --")
        for line in log:
            print("   " + line)
