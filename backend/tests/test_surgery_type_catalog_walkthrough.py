# backend/tests/test_surgery_type_catalog_walkthrough.py
"""Authenticated walk-through of the Surgery Type catalog: staff create a new
type (name + 2 CPTs + major + MedStar + a consent template), see it in the
intake picklist, edit a seeded built-in type, and soft-delete a type — the
picklist reflects each change. `client` is the super-admin fixture."""
from app.models.surgery import ConsentTemplate
from app.services.surgery.surgery_type_seed import seed_surgery_types


def test_catalog_walkthrough(client, db, capsys):
    log = []

    # 0. Seed the catalog from the legacy PROCEDURES list (as startup would).
    seeded = seed_surgery_types(db)
    assert seeded > 0
    log.append(f"0. catalog seeded from legacy list -> {seeded} surgery types")

    tmpl = ConsentTemplate(name="Hysteroscopy Consent", cpt_codes=["58558"],
                           procedure_match=[], facility_match=[], insurance_match=[])
    db.add(tmpl); db.commit(); db.refresh(tmpl)

    # 1. Staff add a new multi-CPT type with classification, location, consent.
    r = client.post("/api/surgery/admin/surgery-types", json={
        "name": "Hysteroscopy with D&C + Polypectomy",
        "cpts": [{"cpt": "58558", "description": "Hysteroscopy with D&C"},
                 {"cpt": "58120", "description": "D&C"}],
        "classification": "major",
        "eligible_facilities": ["medstar"],
        "consent_template_ids": [str(tmpl.id)],
    })
    assert r.status_code == 201, r.text
    new_id = r.json()["id"]
    log.append("1. created 'Hysteroscopy with D&C + Polypectomy' (2 CPTs, major, MedStar, 1 consent)")

    # 2. It appears in the intake picklist with its full mapping.
    pk = client.get("/api/surgery/picklists").json()
    mine = next(t for t in pk["surgery_types"] if t["id"] == new_id)
    assert mine["classification"] == "major"
    assert mine["eligible_facilities"] == ["medstar"]
    assert mine["consent_template_ids"] == [str(tmpl.id)]
    assert len(mine["cpts"]) == 2
    log.append("2. /surgery/picklists -> new type present with CPTs, classification, location, consent")

    # 3. Edit a seeded built-in type (rename + reclassify).
    built_in = next(t for t in pk["surgery_types"]
                    if t["cpts"] and t["cpts"][0]["cpt"] == "58555")  # Diagnostic hysteroscopy
    r = client.put(f"/api/surgery/admin/surgery-types/{built_in['id']}", json={
        "name": "Diagnostic Hysteroscopy (Office)",
        "cpts": built_in["cpts"],
        "classification": "office",
    })
    assert r.status_code == 200 and r.json()["classification"] == "office"
    log.append("3. edited a seeded built-in -> renamed + reclassified to office")

    # 4. Soft-delete the new type -> drops from the picklist, stays in admin list.
    assert client.delete(f"/api/surgery/admin/surgery-types/{new_id}").status_code == 200
    pk2 = client.get("/api/surgery/picklists").json()
    assert new_id not in [t["id"] for t in pk2["surgery_types"]]
    admin = client.get("/api/surgery/admin/surgery-types?include_inactive=true").json()
    assert new_id in [t["id"] for t in admin]
    log.append("4. soft-deleted the new type -> gone from picklist, retained (inactive) in admin list")

    with capsys.disabled():
        print("\n  -- Surgery Type catalog walk-through (authenticated) --")
        for line in log:
            print("   " + line)
