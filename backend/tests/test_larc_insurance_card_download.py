"""Regression: GET /larc/assignments/{id}/insurance-card used os.path.* but
larc.py had no module-level `import os` -> NameError -> 500 ("Couldn't load
preview"). This test downloads a stored card and asserts it serves (200).
"""
import app.config as appcfg
from app.models.larc import LarcDeviceType, LarcAssignment
from app.services.storage import save_blob

# Minimal valid 1x1 PNG.
_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000154a24f5f0000000049454e44ae426082"
)


def _assignment_with_card(db, tmp_root):
    dt = LarcDeviceType(name="Mirena", category="larc",
                        default_flow="pharmacy_order", is_active=True)
    db.add(dt); db.commit(); db.refresh(dt)
    a = LarcAssignment(chart_number="T1", patient_name="Doe, J",
                       device_type_id=dt.id, source_flow="pharmacy_order",
                       status="new")
    db.add(a); db.commit(); db.refresh(a)
    key = save_blob(prefix="larc/insurance-cards", body=_PNG, filename="card.png")
    a.insurance_card_key = key
    a.insurance_card_content_type = "image/png"
    a.insurance_card_filename = "card.png"
    db.commit()
    return a


def test_download_insurance_card_serves_200(client, db, monkeypatch, tmp_path):
    monkeypatch.setattr(appcfg.settings, "documents_local_root", str(tmp_path))
    a = _assignment_with_card(db, tmp_path)
    r = client.get(f"/api/larc/assignments/{a.id}/insurance-card")
    assert r.status_code == 200, r.text
    assert r.content == _PNG
