"""Authenticated live walk-through of the Start LARC Process flow:
intake -> advisory suggest-flow -> create assignment, across all three
fulfillment paths (in-stock, pharmacy, office-procedure). Drives the real
/api/larc endpoints through the authenticated test client.

Run: pytest tests/test_larc_start_process_walkthrough.py -s
"""
from app.models.larc import LarcDeviceType, LarcDevice
from app.models.user import User


def _dt(db, name, default_flow):
    dt = LarcDeviceType(
        name=name,
        category=("office_procedure" if default_flow == "office_procedure" else "larc"),
        default_flow=default_flow, is_active=True)
    db.add(dt); db.commit(); db.refresh(dt)
    return dt


def test_start_larc_process_walkthrough(client, db, capsys):
    log = []

    # Seed a provider (for the "Requested By" dropdown) + three device types:
    # one with stock, one pharmacy-only, one consumable office-procedure.
    db.add(User(email="acooke@waldorfwomenscare.com", display_name="Aryian Cooke",
                npi="1234567890", clinician_role="provider", credential="MD",
                is_active=True))
    mirena = _dt(db, "Mirena", "pharmacy_order")
    kyleena = _dt(db, "Kyleena", "pharmacy_order")
    novasure = _dt(db, "NovaSure", "office_procedure")
    db.add(LarcDevice(our_id="WWC-700", device_type_id=mirena.id, status="unassigned"))
    db.commit()
    log.append("seeded: provider Aryian Cooke MD + Mirena/Kyleena/NovaSure; 1 Mirena in stock")

    # 1. Reasons config — drives the Reason-for-Request dropdown.
    reasons = client.get("/api/larc/config").json()["reason_for_request_options"]
    log.append("1. GET /larc/config -> reasons: "
               + ", ".join(f"{r['reason']} ({r['icd10']})" for r in reasons))

    # 2. Providers — drives the Requested-By dropdown.
    provs = client.get("/api/admin/users/clinicians").json()
    log.append("2. GET /admin/users/clinicians -> "
               + ", ".join(f"{p['display_name']}, {p['credential']}" for p in provs))

    # 3-5. Advisory suggestion for each path.
    def suggest(dt, label):
        s = client.post("/api/larc/assignments/suggest-flow",
                        json={"device_type_id": str(dt.id)}).json()
        log.append(f"   suggest-flow {label} -> '{s['suggested_flow']}' "
                   f"(stock={s['in_stock_count']}); override options={s['allowed_flows']}")
        return s
    log.append("3-5. POST /larc/assignments/suggest-flow (advisory, per device type):")
    s_stock = suggest(mirena, "Mirena (1 in stock)")
    suggest(kyleena, "Kyleena (no stock)")
    suggest(novasure, "NovaSure (consumable)")

    # 6. Create, following the in-stock suggestion for Mirena.
    r = client.post("/api/larc/assignments", json={
        "chart_number": "MRN900", "patient_name": "Doe, Jane",
        "patient_first_name": "Jane", "patient_last_name": "Doe",
        "patient_dob": "1990-05-01", "patient_email": "jane@example.com",
        "patient_cell": "240-555-0100", "device_type_id": str(mirena.id),
        "source_flow": s_stock["suggested_flow"],
        "reason_for_request": reasons[0]["reason"],
        "reason_icd10": reasons[0]["icd10"],
        "requested_by_provider": "Aryian Cooke",
        "inserting_provider_email": "acooke@waldorfwomenscare.com",
        "inserting_provider_name": "Aryian Cooke",
        "inserting_provider_npi": "1234567890",
    })
    assert r.status_code == 201, r.text
    aid = r.json()["id"]
    got = client.get(f"/api/larc/assignments/{aid}").json()
    log.append(f"6. POST /larc/assignments (source_flow={s_stock['suggested_flow']}) -> 201")
    log.append(f"   created assignment {aid[:8]}…  reason='{got['reason_for_request']}' "
               f"({got['reason_icd10']})  requested_by='{got['requested_by_provider']}'  "
               f"inserting_provider='{got['inserting_provider_name']}'")

    assert got["reason_for_request"] == reasons[0]["reason"]
    assert got["requested_by_provider"] == "Aryian Cooke"

    with capsys.disabled():
        print("\n  ── Start LARC Process — authenticated end-to-end walk-through ──")
        for line in log:
            print("   " + line)
        print()
