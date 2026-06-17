"""Surgery Type catalog endpoints: picklists exposure + MANAGE-gated CRUD.

`client` is the super-admin fixture (passes all tier checks)."""
from app.models.surgery import ConsentTemplate


def _consent(db):
    t = ConsentTemplate(name="Hyst Consent", cpt_codes=["58558"], procedure_match=[],
                        facility_match=[], insurance_match=[])
    db.add(t); db.commit(); db.refresh(t)
    return str(t.id)


def test_crud_and_picklists(client, db):
    cid = _consent(db)

    # Create
    r = client.post("/api/surgery/admin/surgery-types", json={
        "name": "Hysteroscopy with D&C",
        "cpts": [{"cpt": "58558", "description": "Hysteroscopy with D&C"},
                 {"cpt": "58120", "description": "D&C"}],
        "classification": "minor",
        "eligible_facilities": ["medstar"],
        "consent_template_ids": [cid],
    })
    assert r.status_code in (200, 201), r.text
    tid = r.json()["id"]

    # Picklists now carries surgery_types AND a flattened procedures list.
    pk = client.get("/api/surgery/picklists").json()
    names = [t["name"] for t in pk["surgery_types"]]
    assert "Hysteroscopy with D&C" in names
    mine = next(t for t in pk["surgery_types"] if t["id"] == tid)
    assert mine["consent_template_ids"] == [cid]
    assert {"cpt": "58120", "description": "D&C"} in pk["procedures"]

    # Update
    r = client.put(f"/api/surgery/admin/surgery-types/{tid}", json={
        "name": "Hysteroscopy with D&C (updated)",
        "cpts": [{"cpt": "58558", "description": "Hysteroscopy with D&C"}],
        "classification": "major",
    })
    assert r.status_code == 200, r.text
    assert r.json()["classification"] == "major"

    # Soft-delete -> drops out of picklists, listed with include_inactive
    assert client.delete(f"/api/surgery/admin/surgery-types/{tid}").status_code == 200
    pk2 = client.get("/api/surgery/picklists").json()
    assert tid not in [t["id"] for t in pk2["surgery_types"]]
    allr = client.get("/api/surgery/admin/surgery-types?include_inactive=true").json()
    assert tid in [t["id"] for t in allr]


def test_reorder(client, db):
    a = client.post("/api/surgery/admin/surgery-types",
                    json={"name": "A", "cpts": [{"cpt": "1", "description": "a"}]}).json()["id"]
    b = client.post("/api/surgery/admin/surgery-types",
                    json={"name": "B", "cpts": [{"cpt": "2", "description": "b"}]}).json()["id"]
    assert client.post("/api/surgery/admin/surgery-types/reorder",
                       json={"ordered_ids": [b, a]}).status_code == 200
    listed = client.get("/api/surgery/admin/surgery-types").json()
    order = [t["id"] for t in listed if t["id"] in (a, b)]
    assert order == [b, a]
