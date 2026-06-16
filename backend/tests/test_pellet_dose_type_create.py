"""POST /pellets/dose-types — add a new dose type (Settings → Dosage)."""
from app.models.pellet import PelletDoseType


def test_create_dose_type_minimal(client, db):
    r = client.post("/api/pellets/dose-types",
                    json={"hormone": "estradiol", "dose_mg": 8})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["hormone"] == "estradiol"
    assert body["dose_mg"] == 8.0
    assert body["label"] == "Estradiol 8mg"        # auto-generated
    assert body["is_controlled"] is False
    assert db.query(PelletDoseType).filter(
        PelletDoseType.hormone == "estradiol",
        PelletDoseType.dose_mg == 8).count() == 1


def test_testosterone_is_controlled(client, db):
    r = client.post("/api/pellets/dose-types",
                    json={"hormone": "testosterone", "dose_mg": 200,
                          "reorder_threshold_packs": 5, "pack_sizes": [10, 0, 5]})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["is_controlled"] is True           # DEA Schedule III
    assert body["reorder_threshold_packs"] == 5
    assert body["pack_sizes"] == [10, 5]           # non-positive dropped


def test_custom_label_kept(client, db):
    r = client.post("/api/pellets/dose-types",
                    json={"hormone": "estradiol", "dose_mg": 12.5,
                          "label": "E2 12.5 (compounded)"})
    assert r.status_code == 201
    assert r.json()["label"] == "E2 12.5 (compounded)"


def test_duplicate_rejected(client, db):
    client.post("/api/pellets/dose-types",
                json={"hormone": "estradiol", "dose_mg": 25})
    r = client.post("/api/pellets/dose-types",
                    json={"hormone": "estradiol", "dose_mg": 25})
    assert r.status_code == 409


def test_bad_dose_rejected(client, db):
    assert client.post("/api/pellets/dose-types",
                       json={"hormone": "estradiol", "dose_mg": 0}).status_code == 422
    assert client.post("/api/pellets/dose-types",
                       json={"hormone": "banana", "dose_mg": 10}).status_code == 422
