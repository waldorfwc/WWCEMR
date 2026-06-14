"""Surgery soft-delete (B1–B3).

Covers:
  - DB-level: the global do_orm_execute filter hides soft-deleted surgeries
    from the legacy db.query(Surgery) API, and include_deleted=True surfaces
    them again.
  - API: POST /surgery/{id}/delete removes a surgery from list / detail /
    dashboard; POST /surgery/{id}/restore brings it back. A separate
    non-deleted surgery is unaffected (guards against over-hiding).
"""
import pytest
from unittest.mock import patch

from app.models.surgery import Surgery
from app.utils.dt import now_utc_naive


@pytest.fixture(autouse=True)
def _no_pg_sequence():
    with patch(
        "app.services.surgery.local_helpers.maybe_assign_surgery_number",
        return_value="SUR00001",
    ):
        yield


def _base_payload(**overrides):
    p = {
        "chart_number": "C200",
        "patient_name": "",
        "first_name": "Soft",
        "last_name": "Delete",
        "dob": "1990-04-15",
        "phone": "240-555-0100",
        "email": "soft@example.com",
        "address_street": "1 Main St",
        "address_city": "Waldorf",
        "address_state": "MD",
        "address_zip": "20601",
        "primary_insurance": "Aetna",
        "primary_member_id": "A123",
        "surgeon_primary": "",
        "surgery_name": "Hysteroscopy",
        "procedures": [{"cpt": "58558", "description": "Hysteroscopy"}],
        "diagnoses": [{"icd": "N84.0", "description": "Polyp"}],
        "eligible_facilities": ["office"],
        "estimated_minutes": 60,
        "preop_date": "2026-07-01",
    }
    p.update(overrides)
    return p


# ─── DB-level: global filter ────────────────────────────────────────────

def test_db_query_excludes_soft_deleted(db):
    live = Surgery(chart_number="C1", patient_name="Live, One", status="new")
    gone = Surgery(chart_number="C2", patient_name="Gone, Two", status="new",
                   deleted_at=now_utc_naive(), deleted_by="admin@x.com")
    db.add_all([live, gone])
    db.commit()

    ids = {s.chart_number for s in db.query(Surgery).all()}
    assert "C1" in ids
    assert "C2" not in ids, "soft-deleted surgery must be excluded from db.query"

    # filter() over the legacy Query API is also filtered
    by_chart = db.query(Surgery).filter(Surgery.chart_number == "C2").first()
    assert by_chart is None


def test_db_query_include_deleted_surfaces_soft_deleted(db):
    gone = Surgery(chart_number="C3", patient_name="Gone, Three", status="new",
                   deleted_at=now_utc_naive(), deleted_by="admin@x.com")
    db.add(gone)
    db.commit()

    with_opt = (db.query(Surgery)
                  .execution_options(include_deleted=True)
                  .filter(Surgery.chart_number == "C3")
                  .first())
    assert with_opt is not None
    assert with_opt.chart_number == "C3"


# ─── API: delete / restore ──────────────────────────────────────────────

def _create(client, **overrides):
    resp = client.post("/api/surgery/manual", json=_base_payload(**overrides))
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_delete_hides_from_list_detail_dashboard_then_restore(client):
    target = _create(client, chart_number="DEL1", first_name="Target",
                     last_name="Patient")
    keeper = _create(client, chart_number="KEEP1", first_name="Keep",
                     last_name="Patient")

    # both present initially
    listing = client.get("/api/surgery").json()
    charts = {row["chart_number"] for row in listing["surgeries"]}
    assert "DEL1" in charts and "KEEP1" in charts

    # delete the target
    r = client.post(f"/api/surgery/{target}/delete")
    assert r.status_code == 200, r.text
    assert r.json().get("ok") is True

    # gone from list
    charts = {row["chart_number"]
              for row in client.get("/api/surgery").json()["surgeries"]}
    assert "DEL1" not in charts
    assert "KEEP1" in charts, "non-deleted surgery must still appear"

    # detail 404
    assert client.get(f"/api/surgery/{target}").status_code == 404
    # keeper detail still 200
    assert client.get(f"/api/surgery/{keeper}").status_code == 200

    # restore
    rr = client.post(f"/api/surgery/{target}/restore")
    assert rr.status_code == 200, rr.text
    assert rr.json().get("ok") is True

    # reappears
    charts = {row["chart_number"]
              for row in client.get("/api/surgery").json()["surgeries"]}
    assert "DEL1" in charts
    assert client.get(f"/api/surgery/{target}").status_code == 200


def test_dashboard_counts_exclude_deleted(client):
    a = _create(client, chart_number="DSH1", first_name="A", last_name="A")
    _create(client, chart_number="DSH2", first_name="B", last_name="B")

    before = client.get("/api/surgery/dashboard").json()
    before_total = sum(before["buckets"].values())

    client.post(f"/api/surgery/{a}/delete")

    after = client.get("/api/surgery/dashboard").json()
    after_total = sum(after["buckets"].values())
    # The deleted active surgery must no longer be counted in any bucket.
    assert after_total < before_total


def test_delete_missing_returns_404(client):
    import uuid
    r = client.post(f"/api/surgery/{uuid.uuid4()}/delete")
    assert r.status_code == 404
