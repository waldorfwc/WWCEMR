# LARC Overview, Inventory Export & Provider Management — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Five Device-Tracking changes: on-hand ownership counts on Overview, CSV/PDF inventory export, Start-LARC button next to Add Device, a regression test for unacknowledged checkouts, and an Add-Provider form in Practice Profile.

**Architecture:** Each change reuses an existing pattern — dashboard tally loop, CSV (`rows_to_csv`) + reportlab PDF (mirroring pellet inventory export), the `LarcNav` button area, and `POST /admin/users` (extended for clinician fields). No schema changes.

**Tech Stack:** FastAPI + SQLAlchemy + pytest (`client`/`db` fixtures, super-admin client, `/api/...`); React + Vite + react-query (no JS test runner — verify via `npm run build` + manual).

**Spec:** `docs/superpowers/specs/2026-06-21-larc-overview-export-providers-design.md`

**Conventions:** `now_utc_naive()`; on-hand status set = `["unassigned","assigned","received"]`; tests seed via models on `db`; run from `backend/` with `source venv/bin/activate`; scoped `git add`; Title Case UI; MM/DD/YYYY via `fmt.date`.

---

## File Structure
- **Backend:** `app/routers/larc.py` (dashboard ownership tally; export endpoints + rows helper), `app/services/larc/inventory_export.py` (new — PDF), `app/routers/admin_users.py` (`CreateUserPayload` + create logic). Tests: `test_larc_dashboard_ownership.py`, `test_larc_unack_checkouts.py`, `test_larc_inventory_export.py`, `test_admin_add_clinician.py` (new).
- **Frontend:** `components/larc/StartLarcProcessDrawer.jsx` (extracted), `components/larc/LarcNav.jsx`, `pages/Larc.jsx`, `pages/LarcDevices.jsx`, `pages/admin/PracticeSettings.jsx`.

---

# GROUP A — Overview ownership counts

## Task 1: Backend `on_hand_by_ownership`

**Files:** Modify `backend/app/routers/larc.py` (`dashboard()`, the on-hand loop ~257-272 + return ~343); Test `backend/tests/test_larc_dashboard_ownership.py`

- [ ] **Step 1: Failing test** — `backend/tests/test_larc_dashboard_ownership.py`:

```python
from app.models.larc import LarcDevice, LarcDeviceType


def _dt(db):
    dt = LarcDeviceType(name="Mirena", category="larc", default_flow="pharmacy_order", is_active=True)
    db.add(dt); db.commit(); db.refresh(dt)
    return dt


def test_dashboard_on_hand_by_ownership(db, client):
    dt = _dt(db)
    # on-hand (counted)
    db.add(LarcDevice(our_id="W1", device_type_id=dt.id, status="unassigned", ownership="wwc_owned"))
    db.add(LarcDevice(our_id="W2", device_type_id=dt.id, status="assigned",   ownership="wwc_owned"))
    db.add(LarcDevice(our_id="P1", device_type_id=dt.id, status="received",   ownership="patient_owned"))
    db.add(LarcDevice(our_id="C1", device_type_id=dt.id, status="unassigned", ownership="wwc_claimed"))
    # NOT on-hand (excluded — terminal/checked_out)
    db.add(LarcDevice(our_id="B1", device_type_id=dt.id, status="billed",      ownership="wwc_owned"))
    db.add(LarcDevice(our_id="X1", device_type_id=dt.id, status="checked_out", ownership="wwc_owned"))
    db.commit()

    body = client.get("/api/larc/dashboard").json()
    own = body["on_hand_by_ownership"]
    assert own["wwc_owned"] == 2
    assert own["patient_owned"] == 1
    assert own["wwc_claimed"] == 1
```

- [ ] **Step 2: Run, expect FAIL** — `cd backend && source venv/bin/activate && pytest tests/test_larc_dashboard_ownership.py -q` (KeyError: on_hand_by_ownership).

- [ ] **Step 3: Implement** — in `dashboard()`, where `on_hand_by_type`/`_location`/`_category` are initialized (~257), add:
```python
    on_hand_by_ownership: dict = {"wwc_owned": 0, "patient_owned": 0, "wwc_claimed": 0}
```
Inside the existing `for d in devices:` loop (alongside the type/location/category tallies), add:
```python
        on_hand_by_ownership[d.ownership or "wwc_owned"] = \
            on_hand_by_ownership.get(d.ownership or "wwc_owned", 0) + 1
```
In the return dict (~343, next to `"on_hand_by_type": on_hand_by_type,`), add:
```python
        "on_hand_by_ownership": on_hand_by_ownership,
```
(The `devices` query already filters status in `["unassigned","assigned","received"]`, so terminal/checked_out are excluded automatically.)

- [ ] **Step 4: Run, expect PASS;** then `pytest tests/ -q -k larc`.
- [ ] **Step 5: Commit** — `git add backend/app/routers/larc.py backend/tests/test_larc_dashboard_ownership.py && git commit -m "feat(larc): dashboard on_hand_by_ownership counts"`

## Task 2: Overview ownership cards (frontend)

**Files:** Modify `frontend/src/pages/Larc.jsx`. Verify `npm run build`.

- [ ] **Step 1:** Read `Larc.jsx` where the dashboard `dash` data is consumed and the on-hand-by-type cards render (~136-168). Add a small row of three cards reading `dash?.on_hand_by_ownership` — **Practice Owned** (`wwc_owned`), **Patient Owned** (`patient_owned`), **Practice Claimed** (`wwc_claimed`) — each showing the count (default 0). Use the same card styling as the existing on-hand cards; place the row near the top of the Overview (e.g., just above or below the on-hand-by-type section). Title Case labels.
- [ ] **Step 2:** `cd frontend && npm run build` → `✓ built`.
- [ ] **Step 3: Commit** — `git add frontend/src/pages/Larc.jsx && git commit -m "feat(larc): Overview ownership count cards"`
- [ ] **Step 4: Manual:** the three cards render with correct counts from the dashboard.

---

# GROUP B — Start-LARC button next to Add Device

## Task 3: Extract drawer + move button to LarcNav (frontend)

**Files:** Create `frontend/src/components/larc/StartLarcProcessDrawer.jsx`; Modify `frontend/src/pages/Larc.jsx`, `frontend/src/components/larc/LarcNav.jsx`. Verify `npm run build`.

- [ ] **Step 1:** In `Larc.jsx`, cut the entire `function StartLarcProcessDrawer(...) { ... }` component and move it into a new file `frontend/src/components/larc/StartLarcProcessDrawer.jsx` as `export default function StartLarcProcessDrawer(...)`. Carry over all imports it needs (useState, useQuery, useMutation, useQueryClient, api, the lucide icons it uses, etc.). It already takes `{ onClose, onCreated }`.
- [ ] **Step 2:** In `Larc.jsx`: remove the header "Start LARC Process" button + its `startOpen` state + the drawer mount; import is no longer needed. Leave the rest of the page intact (the header may now have no action button — that's fine).
- [ ] **Step 3:** In `LarcNav.jsx`: import `useState`, `useNavigate`, and `StartLarcProcessDrawer`. Add a WORK-gated "Start LARC Process" button immediately before the existing "+ Add Device" block (both inside the nav's right-side actions). Wire local state:
```jsx
  const [startOpen, setStartOpen] = useState(false)
  const navigate = useNavigate()
  ...
  {tier(MODULE.LARC, TIER.WORK) && (
    <button className="btn-primary text-sm flex items-center gap-1" onClick={() => setStartOpen(true)}>
      <Plus size={13} /> Start LARC Process
    </button>
  )}
  {/* existing + Add Device NavLink */}
  ...
  {startOpen && <StartLarcProcessDrawer
    onClose={() => setStartOpen(false)}
    onCreated={(id) => { setStartOpen(false); navigate('/larc/assignments/' + id) }} />}
```
Use the real `tier`/`MODULE`/`TIER` tokens already imported in LarcNav (the Add Device block uses `tier(MODULE.LARC, TIER.WORK)`), and import `Plus` from lucide-react. Place the button + Add Device together in the same flex container so they sit side by side.
- [ ] **Step 4:** `cd frontend && npm run build` → `✓ built` (no leftover `StartLarcProcessDrawer` reference in Larc.jsx, no missing imports).
- [ ] **Step 5: Commit** — `git add frontend/src/components/larc/StartLarcProcessDrawer.jsx frontend/src/pages/Larc.jsx frontend/src/components/larc/LarcNav.jsx && git commit -m "feat(larc): Start LARC Process button beside Add Device in nav"`
- [ ] **Step 6: Manual:** "Start LARC Process" appears next to "+ Add Device" on every LARC page; opens the drawer; creating navigates to the assignment; Overview no longer has its own button.

---

# GROUP C — Unacknowledged-checkouts regression test

## Task 4: Regression test (backend test only)

**Files:** Test `backend/tests/test_larc_unack_checkouts.py`. No production change.

- [ ] **Step 1: Write the test** — `backend/tests/test_larc_unack_checkouts.py`:

```python
"""Regression: unacknowledged-checkouts dashboard alert + acknowledge flow."""
from datetime import timedelta
from app.utils.dt import now_utc_naive
from app.models.larc import LarcAssignment, LarcDevice, LarcDeviceType, LarcCheckout


def _setup(db, *, requested_hours_ago):
    dt = LarcDeviceType(name="Mirena", category="larc", default_flow="pharmacy_order", is_active=True)
    db.add(dt); db.commit(); db.refresh(dt)
    dev = LarcDevice(our_id="W1", device_type_id=dt.id, status="checked_out", ownership="wwc_owned")
    db.add(dev); db.commit(); db.refresh(dev)
    a = LarcAssignment(chart_number="MRN1", patient_name="Doe, J", device_type_id=dt.id,
                       device_id=dev.id, source_flow="in_stock", status="checked_out")
    db.add(a); db.commit(); db.refresh(a)
    c = LarcCheckout(assignment_id=a.id, device_id=dev.id, requested_by="ma@wwc.com",
                     approval_status="approved",
                     requested_at=now_utc_naive() - timedelta(hours=requested_hours_ago))
    db.add(c); db.commit(); db.refresh(c)
    return c


def test_old_unacked_checkout_listed_then_cleared_by_ack(client, db):
    c = _setup(db, requested_hours_ago=48)        # older than the 24h ack window
    body = client.get("/api/larc/dashboard").json()
    ids = [u["checkout_id"] for u in body["unacknowledged_checkouts"]]
    assert str(c.id) in ids

    r = client.post(f"/api/larc/checkouts/{c.id}/acknowledge")
    assert r.status_code == 200, r.text
    assert r.json()["acknowledged_at"]

    body2 = client.get("/api/larc/dashboard").json()
    ids2 = [u["checkout_id"] for u in body2["unacknowledged_checkouts"]]
    assert str(c.id) not in ids2


def test_recent_checkout_not_listed(client, db):
    c = _setup(db, requested_hours_ago=1)         # within the ack window
    body = client.get("/api/larc/dashboard").json()
    ids = [u["checkout_id"] for u in body["unacknowledged_checkouts"]]
    assert str(c.id) not in ids
```

- [ ] **Step 2: Run, expect PASS** — `cd backend && source venv/bin/activate && pytest tests/test_larc_unack_checkouts.py -q`. (The feature already works, so this should pass immediately. If it FAILS, the feature has a real bug — STOP and report the failure with output rather than altering the test to pass.)
  - Note: verify `LarcCheckout` field names against `backend/app/models/larc.py` (`assignment_id`, `device_id`, `requested_by`, `approval_status`, `requested_at`, `acknowledged_at`). Adjust seed kwargs to the real columns if any differ.
- [ ] **Step 3: Commit** — `git add backend/tests/test_larc_unack_checkouts.py && git commit -m "test(larc): unacknowledged-checkouts dashboard + acknowledge regression"`

---

# GROUP D — Inventory CSV/PDF export

## Task 5: Export rows helper + CSV endpoint

**Files:** Modify `backend/app/routers/larc.py` (rows helper + `GET /devices/export.csv`); Test `backend/tests/test_larc_inventory_export.py`

- [ ] **Step 1: Failing test** — `backend/tests/test_larc_inventory_export.py`:

```python
from app.models.larc import LarcAssignment, LarcDevice, LarcDeviceType


def _dt(db):
    dt = LarcDeviceType(name="Mirena", category="larc", default_flow="pharmacy_order", is_active=True)
    db.add(dt); db.commit(); db.refresh(dt)
    return dt


def test_export_csv_on_hand_with_assignee(client, db):
    dt = _dt(db)
    assigned = LarcDevice(our_id="W-AS", device_type_id=dt.id, status="assigned",
                          ownership="wwc_owned", manufacturer_lot="LOT9",
                          location="white_plains")
    db.add(assigned); db.commit(); db.refresh(assigned)
    db.add(LarcAssignment(chart_number="MRN5", patient_name="Doe, Jane", device_type_id=dt.id,
                          device_id=assigned.id, source_flow="in_stock", status="in_progress",
                          is_active=True))
    db.add(LarcDevice(our_id="W-UN", device_type_id=dt.id, status="unassigned",
                      ownership="wwc_owned", manufacturer_lot="LOT1", location="white_plains"))
    db.add(LarcDevice(our_id="W-BILL", device_type_id=dt.id, status="billed", ownership="wwc_owned"))
    db.commit()

    r = client.get("/api/larc/devices/export.csv")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/csv")
    text = r.text
    assert "W-AS" in text and "W-UN" in text
    assert "W-BILL" not in text          # terminal status excluded
    assert "Doe, Jane" in text           # assignee for the assigned device
    assert "LOT9" in text
```

- [ ] **Step 2: Run, expect FAIL.**

- [ ] **Step 3: Implement** — in `backend/app/routers/larc.py`. First a rows helper (module-level), then the CSV route. Confirm `rows_to_csv` import (`from app.services.larc.reports import rows_to_csv`), `StreamingResponse` (from fastapi.responses), `joinedload`, `LOCATION_LABELS`/`LOCATIONS`, and `LARC_OWNERSHIP` labels exist (use the `_device_dict` label maps for ownership/location):

```python
ON_HAND_STATUSES = ["unassigned", "assigned", "received"]

def _inventory_export_rows(db) -> list[dict]:
    devs = (db.query(LarcDevice)
              .options(joinedload(LarcDevice.device_type))
              .filter(LarcDevice.status.in_(ON_HAND_STATUSES))
              .order_by(LarcDevice.expiration_date.asc().nullslast())
              .all())
    # Resolve assignees in one query: device_id -> active assignment patient.
    dev_ids = [d.id for d in devs]
    assignee = {}
    if dev_ids:
        for a in (db.query(LarcAssignment)
                    .filter(LarcAssignment.device_id.in_(dev_ids),
                            LarcAssignment.is_active.is_(True)).all()):
            assignee[a.device_id] = f"{a.patient_name or ''} ({a.chart_number or ''})".strip()
    rows = []
    for d in devs:
        rows.append({
            "Our ID": d.our_id,
            "Device Type": d.device_type.name if d.device_type else "",
            "Lot": d.manufacturer_lot or "",
            "Expiration": d.expiration_date.strftime("%m/%d/%Y") if d.expiration_date else "",
            "Location": LOCATION_LABELS.get(d.location, d.location or ""),
            "Ownership": {"patient_owned": "Patient", "wwc_owned": "WWC",
                          "wwc_claimed": "WWC Claimed"}.get(d.ownership or "wwc_owned", d.ownership),
            "Status": d.status,
            "Assignee": assignee.get(d.id, ""),
        })
    return rows


@router.get("/devices/export.csv")
def export_devices_csv(db: Session = Depends(get_db),
                       current_user: dict = Depends(requires_tier(Module.LARC, Tier.VIEW))):
    from app.services.larc.reports import rows_to_csv
    rows = _inventory_export_rows(db)
    csv_text = rows_to_csv(rows)
    fname = f"larc-inventory-{_date.today().isoformat()}.csv"
    return StreamingResponse(iter([csv_text]), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{fname}"'})
```
Place `/devices/export.csv` BEFORE the `/devices/{device_id}` route so "export.csv" isn't parsed as a device id. Verify `_date`, `StreamingResponse`, `LOCATION_LABELS` are imported (LOCATION_LABELS is used by `_device_dict`/unallocated). If the ownership label map already exists as a helper/const, reuse it instead of the inline dict.

- [ ] **Step 4: Run, expect PASS;** `pytest tests/ -q -k larc`.
- [ ] **Step 5: Commit** — `git add backend/app/routers/larc.py backend/tests/test_larc_inventory_export.py && git commit -m "feat(larc): inventory CSV export (on-hand, with assignee)"`

## Task 6: PDF export

**Files:** Create `backend/app/services/larc/inventory_export.py`; Modify `backend/app/routers/larc.py` (`GET /devices/export.pdf`); Test append to `test_larc_inventory_export.py`

- [ ] **Step 1: Failing test (append):**

```python
def test_export_pdf_returns_pdf(client, db):
    dt = _dt(db)
    db.add(LarcDevice(our_id="W-UN", device_type_id=dt.id, status="unassigned",
                      ownership="wwc_owned", manufacturer_lot="LOT1", location="white_plains"))
    db.commit()
    r = client.get("/api/larc/devices/export.pdf")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf"
    assert r.content[:4] == b"%PDF"
```

- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3a: Create `backend/app/services/larc/inventory_export.py`** — mirror `backend/app/services/pellet/inventory_export.py::build_pdf` (read it). Signature `build_pdf(rows: list[dict], *, generated_by: str = "system") -> bytes`. Use reportlab `SimpleDocTemplate` (landscape letter), a title "LARC Inventory — On Hand", a generated-by/date meta line, and one table whose header is the row keys (Our ID, Device Type, Lot, Expiration, Location, Ownership, Status, Assignee) and body is the row values. Return `buf.getvalue()`. Keep it small; copy the pellet styling approach (title style, table style with header fill + grid).
- [ ] **Step 3b: Add the route** in `larc.py` (next to export.csv, also before `/devices/{device_id}`):
```python
@router.get("/devices/export.pdf")
def export_devices_pdf(db: Session = Depends(get_db),
                       current_user: dict = Depends(requires_tier(Module.LARC, Tier.VIEW))):
    from app.services.larc.inventory_export import build_pdf
    rows = _inventory_export_rows(db)
    by = current_user.get("email") or "system"
    pdf = build_pdf(rows, generated_by=by)
    log_audit(db, actor=by, action="inventory_export", summary=f"Exported {len(rows)} on-hand devices (PDF)")
    db.commit()
    fname = f"larc-inventory-{_date.today().isoformat()}.pdf"
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{fname}"'})
```
Confirm `Response` (fastapi.responses) is imported; adjust `log_audit(...)` to its real signature (it takes `actor`, `action`, optional `device`/`assignment`, `summary`, `detail` — pass only what fits; for an inventory-wide export there's no single device/assignment, so omit those).

- [ ] **Step 4: Run, expect PASS;** `pytest tests/ -q -k larc`.
- [ ] **Step 5: Commit** — `git add backend/app/services/larc/inventory_export.py backend/app/routers/larc.py backend/tests/test_larc_inventory_export.py && git commit -m "feat(larc): inventory PDF export"`

## Task 7: Export buttons (frontend)

**Files:** Modify `frontend/src/pages/LarcDevices.jsx`. Verify `npm run build`.

- [ ] **Step 1:** Add "Export CSV" and "Export PDF" buttons near the top of the devices page. Because the endpoints are auth-gated, download via an authed blob fetch (not a bare link). Add a helper:
```jsx
async function downloadExport(path, filename) {
  const res = await api.get(path, { responseType: 'blob' })
  const url = URL.createObjectURL(res.data)
  const a = document.createElement('a')
  a.href = url; a.download = filename; a.click()
  URL.revokeObjectURL(url)
}
```
and buttons:
```jsx
<button className="btn-secondary text-sm" onClick={() => downloadExport('/larc/devices/export.csv', 'larc-inventory.csv')}>Export CSV</button>
<button className="btn-secondary text-sm" onClick={() => downloadExport('/larc/devices/export.pdf', 'larc-inventory.pdf')}>Export PDF</button>
```
Use the page's existing `api` import. Match button styling/placement to the page (e.g., in the filter/header row).
- [ ] **Step 2:** `cd frontend && npm run build` → `✓ built`.
- [ ] **Step 3: Commit** — `git add frontend/src/pages/LarcDevices.jsx && git commit -m "feat(larc): inventory export buttons (CSV/PDF)"`
- [ ] **Step 4: Manual:** both buttons download a file listing on-hand devices with assignee/lot/expiration/location.

---

# GROUP E — Add provider in Practice Profile

## Task 8: Extend `POST /admin/users` for clinician fields (backend)

**Files:** Modify `backend/app/routers/admin_users.py` (`CreateUserPayload` + create handler); Test `backend/tests/test_admin_add_clinician.py`

- [ ] **Step 1: Failing test** — `backend/tests/test_admin_add_clinician.py`:

```python
def test_create_user_with_clinician_fields_shows_in_clinicians(client, db):
    r = client.post("/api/admin/users", json={
        "email": "acooke@waldorfwomenscare.com", "group": "clinical",
        "display_name": "Aryian Cooke", "npi": "1234567890",
        "clinician_role": "provider", "credential": "MD"})
    assert r.status_code in (200, 201), r.text
    clinicians = client.get("/api/admin/users/clinicians").json()
    match = [c for c in clinicians if c["email"] == "acooke@waldorfwomenscare.com"]
    assert match and match[0]["npi"] == "1234567890"
    assert match[0]["clinician_role"] == "provider" and match[0]["credential"] == "MD"
```

- [ ] **Step 2: Run, expect FAIL** (npi/role/credential not set on create → not in clinicians list).

- [ ] **Step 3: Implement** — in `admin_users.py`, extend `CreateUserPayload`:
```python
class CreateUserPayload(BaseModel):
    email: EmailStr
    group: UserGroup
    display_name: Optional[str] = None
    npi: Optional[str] = None
    clinician_role: Optional[str] = None
    credential: Optional[str] = None
```
In the create handler (`POST /users`), after building the new `User`, set the optional clinician fields when provided (strip → None):
```python
    if payload.npi is not None:
        user.npi = payload.npi.strip() or None
    if payload.clinician_role is not None:
        user.clinician_role = payload.clinician_role.strip() or None
    if payload.credential is not None:
        user.credential = payload.credential.strip() or None
```
Read the real create handler to set these on the correct `User` instance before commit. Keep `requires_super_admin()` gating. Existing callers (no new fields) are unaffected.

- [ ] **Step 4: Run, expect PASS;** `pytest tests/ -q -k "admin_user or clinician"`.
- [ ] **Step 5: Commit** — `git add backend/app/routers/admin_users.py backend/tests/test_admin_add_clinician.py && git commit -m "feat(admin): create user with clinician fields in one call"`

## Task 9: Providers section in Practice Profile (frontend)

**Files:** Modify `frontend/src/pages/admin/PracticeSettings.jsx`. Verify `npm run build`.

- [ ] **Step 1:** Read `PracticeSettings.jsx`. Add a **Providers** section (below the existing practice-settings groups): 
  - `useQuery(['clinicians'])` → `GET /admin/users/clinicians`; render the list (display_name, credential, role, NPI).
  - An **Add Provider** form: Display name, Email, NPI, Role (Provider/APP select), Credential (MD/DO/NP/PA select). On submit → `useMutation` `POST /admin/users` with `{email, display_name, group: 'clinical', npi, clinician_role, credential}`; on success invalidate `['clinicians']` and clear the form. Require name + email + NPI (NPI is what makes them appear in the dropdown). Surface the API error detail (e.g., duplicate email 409) via alert.
  - Use the page's existing `api` import + styling (inputs/buttons/cards).
- [ ] **Step 2:** `cd frontend && npm run build` → `✓ built`.
- [ ] **Step 3: Commit** — `git add frontend/src/pages/admin/PracticeSettings.jsx && git commit -m "feat(larc): Add Provider form in Practice Profile"`
- [ ] **Step 4: Manual:** in LARC Settings → Practice Profile (super-admin), the Providers list shows current clinicians; adding "Aryian Cooke / acooke@… / NPI / Provider / MD" succeeds and the provider then appears in the Start LARC Process "Requested By" dropdown.

---

# GROUP F — Verify + deploy

## Task 10: Full verification + deploy

- [ ] **Step 1:** `cd backend && source venv/bin/activate && python -m pytest -q -p no:cacheprovider` → all pass.
- [ ] **Step 2:** `cd frontend && npm run build` → `✓ built`.
- [ ] **Step 3 (deploy, only when the user asks):** backend first (dashboard/export/admin endpoints), then frontend:
```bash
SHA=$(git rev-parse --short HEAD)
gcloud builds submit backend/  --tag=us-east4-docker.pkg.dev/wwc-solutions/app/backend:$SHA  --project=wwc-solutions --region=us-east4
gcloud builds submit frontend/ --tag=us-east4-docker.pkg.dev/wwc-solutions/app/frontend:$SHA --project=wwc-solutions --region=us-east4
gcloud run services update backend  --region=us-east4 --project=wwc-solutions --image=...backend:$SHA
gcloud run services update frontend --region=us-east4 --project=wwc-solutions --image=...frontend:$SHA
```
No migrations (no schema changes).

## Notes / risks
- `/devices/export.csv` + `/devices/export.pdf` must be declared before the `/devices/{device_id}` route to avoid being parsed as a device id — verify route order after adding.
- reportlab is already a dependency (pellet inventory PDF uses it) — no new package.
- The Add-Provider form creates a login-capable User (email required); that's intended (the Requested-By dropdown needs a User row). Provider must have an NPI to appear in the dropdown.
- `log_audit` for the PDF export has no single device/assignment — pass only actor/action/summary.
