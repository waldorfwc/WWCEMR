"""End-to-end exercise of the delete → restore lifecycle through the real
HTTP endpoints (authenticated test client = super-admin), mirroring exactly
what the Delete Surgery / Restore Deleted UI does. Each step asserts the
state the coordinator would see in the app."""
import pytest
from unittest.mock import patch


@pytest.fixture(autouse=True)
def _no_pg_sequence():
    # create_manual -> maybe_assign_surgery_number uses a Postgres-only
    # nextval() sequence the SQLite test DB lacks; stub it.
    with patch(
        "app.services.surgery.local_helpers.maybe_assign_surgery_number",
        return_value="SUR00001",
    ):
        yield


def _payload(**ov):
    p = {
        "chart_number": "E2E1",
        "patient_name": "",
        "first_name": "Erin",
        "last_name": "Example",
        "dob": "1988-02-02",
        "phone": "240-555-0199",
        "email": "erin@example.com",
        "address_street": "9 Test Ln",
        "address_city": "Waldorf",
        "address_state": "MD",
        "address_zip": "20601",
        "primary_insurance": "Aetna",
        "primary_member_id": "Z9",
        "surgeon_primary": "",
        "surgery_name": "Hysteroscopy",
        "procedures": [{"cpt": "58558", "description": "Hysteroscopy"}],
        "diagnoses": [{"icd": "N84.0", "description": "Polyp"}],
        "eligible_facilities": ["office"],
        "estimated_minutes": 60,
        "preop_date": "2026-07-01",
    }
    p.update(ov)
    return p


def _ids_in_list(client, search="E2E1"):
    r = client.get("/api/surgery", params={"search": search, "per_page": 50})
    assert r.status_code == 200, r.text
    return {s["id"] for s in r.json()["surgeries"]}


def _ids_in_deleted(client, search="E2E1"):
    r = client.get("/api/surgery/deleted", params={"search": search})
    assert r.status_code == 200, r.text
    return {s["id"] for s in r.json()["surgeries"]}


def test_delete_then_restore_full_lifecycle(client, capsys):
    log = []

    # 1. Create (what "Add New Surgery" does)
    r = client.post("/api/surgery/manual", json=_payload())
    assert r.status_code in (200, 201), r.text
    sid = r.json()["id"]
    log.append(f"1. created surgery {sid}")

    # 2. It shows in the active list + detail (what the coordinator sees)
    assert sid in _ids_in_list(client)
    assert client.get(f"/api/surgery/{sid}").status_code == 200
    assert sid not in _ids_in_deleted(client)
    log.append("2. appears in active list + GET 200; not in deleted view")

    # 3. Delete (what "Delete Surgery" / the drawer Delete button does)
    r = client.post(f"/api/surgery/{sid}/delete")
    assert r.status_code == 200, r.text
    assert r.json().get("ok") is True
    log.append("3. POST /delete -> ok")

    # 4. Gone everywhere: list excludes it, detail 404s, AND it's in the
    #    Restore Deleted view with who/when stamped.
    assert sid not in _ids_in_list(client)
    assert client.get(f"/api/surgery/{sid}").status_code == 404
    deleted = client.get("/api/surgery/deleted", params={"search": "E2E1"}).json()["surgeries"]
    row = next((d for d in deleted if d["id"] == sid), None)
    assert row is not None, "deleted surgery should appear in /surgery/deleted"
    assert row["deleted_at"] and row["deleted_by"], row
    log.append(f"4. removed from list + GET 404; in deleted view (by {row['deleted_by']})")

    # 5. Restore (what "Restore Deleted -> Restore" does)
    r = client.post(f"/api/surgery/{sid}/restore")
    assert r.status_code == 200, r.text
    assert r.json().get("ok") is True
    log.append("5. POST /restore -> ok")

    # 6. Back in the active system, out of the deleted view
    assert sid in _ids_in_list(client)
    assert client.get(f"/api/surgery/{sid}").status_code == 200
    assert sid not in _ids_in_deleted(client)
    log.append("6. back in active list + GET 200; gone from deleted view")

    with capsys.disabled():
        print("\n  ── delete/restore click-through (API) ──")
        for line in log:
            print("   ✓", line)
