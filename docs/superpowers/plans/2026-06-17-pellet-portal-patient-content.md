# Pellet Portal Patient Content + Left Nav Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add patient identity (Name + MRN), a left-side nav, an Appointments history page (with dosage), a Receipts page (Stripe-hosted receipt links), and a staff-editable Rules & Info page to the pellet patient portal.

**Architecture:** New patient GET endpoints on `patient_pellet.py` (`/appointments`, `/receipts`, `/receipts/{id}/receipt-url`, `/info`) + a `portal_info_text` config key; restructure `PelletPortalShell` to a left sidebar + main content; new portal pages + a staff "Portal Info" settings tab. All read-only (staff "View as patient" can see them).

**Tech Stack:** FastAPI + Stripe (one-time + invoice), React + react-query + `marked`. Spec: `docs/superpowers/specs/2026-06-17-pellet-portal-patient-content-design.md`.

**Branch:** `feat/pellet-portal-content` off `main`.

## VERIFIED facts (override conflicting snippets)
- `PelletVisit` (`backend/app/models/pellet.py`): `patient_id, visit_kind, status, scheduled_date, location, provider, inserted_at, outcome`; relationship `doses = relationship("PelletVisitDose", ...)`.
- `PelletVisitDose`: `visit_id, dose_type_id, quantity, status`. `PelletDoseType` has `label`.
- `PelletPayment` (`backend/app/models/pellet_payment.py`): `pellet_patient_id, kind` (single|package|subscription_invoice|manual), `amount`, `status` (requested|paid|failed|expired|refunded), `paid_at`, `stripe_payment_intent_id`, `stripe_invoice_id`.
- `patient_pellet.py`: prefix `/pellet-portal`; `require_pellet_token(request, authorization, db) -> PelletPatient`; imports `cfg`, `Session`, `get_db`, `HTTPException`, `PelletPatient`, `PelletPayment` (T-payments), `pelletpay`. Stripe client: `app.services.pellet.payments._client()` (lazy `import stripe; stripe.api_key=...`), `is_configured()`.
- Config: `PELLET_SETTINGS_DEFAULTS` + `cfg(db,key)` in `app/services/pellet/settings.py`; `PelletConfigPayload` in `pellet.py` (~3266) — PUT persists any key in defaults.
- Frontend: portal pages in `frontend/src/pages/pellet-portal/`; `pelletPortalApi` in `lib/pellet-portal-api.js`; portal routes under `/pellet-portal/home` in `App.jsx`; `PelletPortalShell.jsx` (header + staff_token capture + token guard + `<Outlet/>`). Markdown: `marked` (^18) — mirror `frontend/src/pages/PelletManual.jsx`'s render. `fmt` from `utils/api`.
- Tests: `cd backend && source venv/bin/activate && python -m pytest <path> -q`; conftest `client`=super-admin; patient token via `portal_auth.issue_portal_token(p)`. Baseline 69 failed. Conventions: MM/DD/YYYY, Title Case, `--project=wwc-solutions`.

---

## Task 1: config `portal_info_text` + `/info` + `/appointments`

**Files:** Modify `backend/app/services/pellet/settings.py`, `backend/app/routers/pellet.py` (ConfigPayload), `backend/app/routers/patient_pellet.py`; Test `backend/tests/test_pellet_portal_content.py`.

- [ ] **Step 1: Write the failing test**
```python
# backend/tests/test_pellet_portal_content.py
from datetime import date
import pytest
from app.models.pellet import PelletPatient, PelletVisit, PelletVisitDose, PelletDoseType
from app.services.pellet import portal_auth


@pytest.fixture
def auth(db):
    p = PelletPatient(patient_name="Doe, Jane", chart_number="MRN1",
                      patient_dob=date(1980, 5, 1), patient_phone="3015551234")
    db.add(p); db.commit(); db.refresh(p)
    return p, {"Authorization": f"Bearer {portal_auth.issue_portal_token(p)}"}


def test_info_returns_config_text(client, db, auth):
    _p, h = auth
    body = client.get("/api/pellet-portal/info", headers=h).json()
    assert "info_text" in body and isinstance(body["info_text"], str)
    assert len(body["info_text"]) > 0   # ships with starter copy


def test_config_roundtrips_portal_info_text(client, db):
    r = client.put("/api/pellets/config", json={"portal_info_text": "## Rules\nBe within 1 year."})
    assert r.status_code == 200, r.text
    assert client.get("/api/pellets/config").json()["portal_info_text"] == "## Rules\nBe within 1 year."


def test_appointments_lists_visits_with_dosage(client, db, auth):
    p, h = auth
    dt = PelletDoseType(hormone="estradiol", dose_mg=12.5, label="Estradiol 12.5mg")
    db.add(dt); db.flush()
    v = PelletVisit(patient_id=p.id, visit_kind="repeat", status="inserted",
                    scheduled_date=date(2026, 5, 1), location="white_plains",
                    provider="Cooke, Aryian, MD")
    db.add(v); db.flush()
    db.add(PelletVisitDose(visit_id=v.id, dose_type_id=dt.id, quantity=2))
    db.commit()
    items = client.get("/api/pellet-portal/appointments", headers=h).json()["items"]
    assert len(items) == 1
    a = items[0]
    assert a["location"] == "white_plains" and a["provider"] == "Cooke, Aryian, MD"
    assert a["status"] == "inserted" and a["scheduled_date"] == "2026-05-01"
    assert a["doses"] == [{"label": "Estradiol 12.5mg", "quantity": 2}]


def test_appointments_empty_for_new_patient(client, db, auth):
    _p, h = auth
    assert client.get("/api/pellet-portal/appointments", headers=h).json()["items"] == []
```

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Config** — `settings.py` `PELLET_SETTINGS_DEFAULTS` add:
```python
    "portal_info_text": (
        "## Pellet Therapy — What to Know\n\n"
        "- **Mammogram:** must be within the last 1 year.\n"
        "- **Labs:** must be drawn within the last 14 days.\n"
        "- **Payment:** pellets must be paid for before you can schedule an insertion.\n\n"
        "### Refund Policy\n\n"
        "Replace this with the practice's refund policy."
    ),
```
`pellet.py` `PelletConfigPayload` add: `portal_info_text: Optional[str] = None`.

- [ ] **Step 4: Endpoints** in `patient_pellet.py`:
```python
from app.models.pellet import PelletVisit, PelletVisitDose, PelletDoseType


@router.get("/info")
def portal_info(p: PelletPatient = Depends(require_pellet_token),
                db: Session = Depends(get_db)):
    return {"info_text": cfg(db, "portal_info_text")}


@router.get("/appointments")
def appointments(p: PelletPatient = Depends(require_pellet_token),
                 db: Session = Depends(get_db)):
    visits = (db.query(PelletVisit)
                .filter(PelletVisit.patient_id == p.id)
                .order_by(PelletVisit.scheduled_date.desc().nullslast(),
                          PelletVisit.created_at.desc())
                .all())
    # Dose-type labels in one lookup.
    labels = {str(d.id): d.label for d in db.query(PelletDoseType).all()}
    out = []
    for v in visits:
        doses = (db.query(PelletVisitDose)
                   .filter(PelletVisitDose.visit_id == v.id).all())
        out.append({
            "id": str(v.id), "visit_kind": v.visit_kind, "status": v.status,
            "scheduled_date": v.scheduled_date.isoformat() if v.scheduled_date else None,
            "location": v.location, "provider": v.provider,
            "inserted_at": v.inserted_at.isoformat() if v.inserted_at else None,
            "doses": [{"label": labels.get(str(d.dose_type_id), "—"),
                       "quantity": d.quantity} for d in doses],
        })
    return {"items": out}
```
(`PelletVisit.scheduled_date.desc().nullslast()` — if `nullslast()` errors on SQLite, fall back to `order_by(PelletVisit.created_at.desc())`. Confirm at run time.)

- [ ] **Step 5: Run — expect 4 PASS.** Regression `-k pellet` ≤ baseline; `python -c "import app.main"`.

- [ ] **Step 6: Commit**
```bash
git add backend/app/services/pellet/settings.py backend/app/routers/pellet.py backend/app/routers/patient_pellet.py backend/tests/test_pellet_portal_content.py
git commit --no-verify -m "feat(pellet-portal): /info + /appointments + portal_info_text config (T1)"
```

---

## Task 2: `/receipts` + on-demand Stripe receipt URL

**Files:** Modify `backend/app/routers/patient_pellet.py`; Test append to `backend/tests/test_pellet_portal_content.py`.

- [ ] **Step 1: Write the failing test** (append; Stripe mocked)
```python
from decimal import Decimal
from app.models.pellet_payment import PelletPayment
from app.services.pellet import payments as pay


def _paid(db, p, **kw):
    row = PelletPayment(pellet_patient_id=p.id, amount=Decimal("400.00"),
                        status="paid", requested_by="patient", **kw)
    db.add(row); db.commit(); db.refresh(row)
    return row


def test_receipts_lists_paid(client, db, auth):
    p, h = auth
    _paid(db, p, kind="single", stripe_payment_intent_id="pi_1")
    _paid(db, p, kind="subscription_invoice", stripe_invoice_id="in_1", amount=Decimal("100"))
    # An unpaid one is excluded.
    db.add(PelletPayment(pellet_patient_id=p.id, kind="single", amount=Decimal("400"),
                         status="requested", requested_by="patient")); db.commit()
    items = client.get("/api/pellet-portal/receipts", headers=h).json()["items"]
    assert len(items) == 2
    assert all(it["status"] == "paid" for it in items)
    assert {it["kind"] for it in items} == {"single", "subscription_invoice"}
    assert all(it["has_receipt"] for it in items)


def test_receipt_url_resolves_stripe(client, db, auth, monkeypatch):
    p, h = auth
    row = _paid(db, p, kind="single", stripe_payment_intent_id="pi_1")

    class _Charge: receipt_url = "https://stripe.test/receipt/abc"
    class _PI:
        latest_charge = _Charge()
    class _FakeStripe:
        class PaymentIntent:
            @staticmethod
            def retrieve(pid, **kw): return _PI()
    monkeypatch.setattr(pay, "is_configured", lambda: True)
    monkeypatch.setattr(pay, "_client", lambda: _FakeStripe)
    r = client.get(f"/api/pellet-portal/receipts/{row.id}/receipt-url", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["url"] == "https://stripe.test/receipt/abc"


def test_receipt_url_404_when_unresolvable(client, db, auth, monkeypatch):
    p, h = auth
    row = _paid(db, p, kind="single", stripe_payment_intent_id="pi_x")
    monkeypatch.setattr(pay, "is_configured", lambda: False)
    assert client.get(f"/api/pellet-portal/receipts/{row.id}/receipt-url", headers=h).status_code == 404


def test_receipt_url_rejects_other_patients_payment(client, db, auth):
    _p, h = auth
    other = PelletPatient(patient_name="Other, Pat", chart_number="MRN9",
                          patient_dob=date(1980, 1, 1), patient_phone="3015550000")
    db.add(other); db.flush()
    row = PelletPayment(pellet_patient_id=other.id, kind="single", amount=Decimal("400"),
                        status="paid", requested_by="patient", stripe_payment_intent_id="pi_o")
    db.add(row); db.commit()
    assert client.get(f"/api/pellet-portal/receipts/{row.id}/receipt-url", headers=h).status_code in (403, 404)
```

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement** in `patient_pellet.py`:
```python
_RECEIPT_KIND_LABELS = {"single": "Single Insertion", "package": "Package",
                        "subscription_invoice": "Subscription", "manual": "Manual"}


@router.get("/receipts")
def receipts(p: PelletPatient = Depends(require_pellet_token),
             db: Session = Depends(get_db)):
    rows = (db.query(PelletPayment)
              .filter(PelletPayment.pellet_patient_id == p.id,
                      PelletPayment.status == "paid")
              .order_by(PelletPayment.paid_at.desc().nullslast()).all())
    return {"items": [{
        "id": str(r.id), "kind": r.kind,
        "kind_label": _RECEIPT_KIND_LABELS.get(r.kind, r.kind),
        "amount": float(r.amount or 0),
        "paid_at": r.paid_at.isoformat() if r.paid_at else None,
        "status": r.status,
        "has_receipt": bool(r.stripe_payment_intent_id or r.stripe_invoice_id),
    } for r in rows]}


@router.get("/receipts/{payment_id}/receipt-url")
def receipt_url(payment_id: str, p: PelletPatient = Depends(require_pellet_token),
                db: Session = Depends(get_db)):
    row = db.query(PelletPayment).filter(PelletPayment.id == payment_id).first()
    if row is None or str(row.pellet_patient_id) != str(p.id):
        raise HTTPException(status_code=404, detail="receipt not found")
    if not pelletpay.is_configured():
        raise HTTPException(status_code=404, detail="receipt unavailable")
    url = None
    try:
        s = pelletpay._client()
        if row.stripe_invoice_id:
            inv = s.Invoice.retrieve(row.stripe_invoice_id)
            url = getattr(inv, "hosted_invoice_url", None) or (inv.get("hosted_invoice_url") if isinstance(inv, dict) else None)
        elif row.stripe_payment_intent_id:
            pi = s.PaymentIntent.retrieve(row.stripe_payment_intent_id, expand=["latest_charge"])
            ch = getattr(pi, "latest_charge", None) or (pi.get("latest_charge") if isinstance(pi, dict) else None)
            url = getattr(ch, "receipt_url", None) or (ch.get("receipt_url") if isinstance(ch, dict) else None)
    except Exception:
        url = None
    if not url:
        raise HTTPException(status_code=404, detail="receipt unavailable")
    return {"url": url}
```
(`pelletpay` is the `from app.services.pellet import payments as pelletpay` import already in the file. The test monkeypatches `pay.is_configured`/`pay._client` where `pay` is the same module — confirm the file uses `pelletpay` and the test patches the same module object.)

- [ ] **Step 4: Run — expect 4 new PASS.** Regression ≤ baseline; `python -c "import app.main"`.

- [ ] **Step 5: Commit**
```bash
git add backend/app/routers/patient_pellet.py backend/tests/test_pellet_portal_content.py
git commit --no-verify -m "feat(pellet-portal): receipts list + on-demand Stripe receipt URL (T2)"
```

---

## Task 3: Frontend — left-nav shell + Appointments/Receipts/Info pages + staff Portal Info tab

**Files:** Modify `frontend/src/pages/pellet-portal/PelletPortalShell.jsx`, `frontend/src/App.jsx`, `frontend/src/pages/PelletSettings.jsx`; Create `frontend/src/pages/pellet-portal/PelletAppointments.jsx`, `PelletReceipts.jsx`, `PelletInfo.jsx`.

- [ ] **Step 1: Shell left nav + identity** — restructure `PelletPortalShell.jsx`: keep the existing header (logo, Sign Out) and `staff_token` capture + token guard. Below the header, render a 2-column layout: a left sidebar + `<main><Outlet/></main>`. Sidebar top shows the patient identity from `useQuery(['pellet-portal-dash'], () => pelletPortalApi.get('/dashboard').then(r=>r.data))` → `data.patient.patient_name` + `MRN {data.patient.chart_number}`. Nav links (react-router `NavLink`, active style) to: `/pellet-portal/home` (Checklist, end), `home/appointments` (Appointments), `home/payments` (Payments), `home/schedule` (Schedule), `home/receipts` (Receipts), `home/info` (Rules & Info). Collapse to a stacked/topbar layout on small screens (mirror existing portal Tailwind). Keep "Back to Checklist" out now that nav exists (or leave header minimal).

- [ ] **Step 2: `PelletAppointments.jsx`** — `useQuery(['pellet-appts'], () => pelletPortalApi.get('/appointments').then(r=>r.data))`. Render cards/rows: `fmt.date(scheduled_date)`, location (title-cased: white_plains→White Plains), provider, dosage (`doses.map(d => `${d.label} ×${d.quantity}`).join(', ')`), status chip. Empty state "No appointments yet." Mirror PelletSchedule styling.

- [ ] **Step 3: `PelletReceipts.jsx`** — `useQuery(['pellet-receipts'], GET /receipts)`. Table: date (`fmt.date(paid_at)`), `kind_label`, `$amount.toFixed(2)`, status. A "View Receipt" button when `has_receipt` → `pelletPortalApi.get('/receipts/${id}/receipt-url')` → `window.open(res.url, '_blank')`; on 404 show inline "Receipt unavailable". Empty state "No receipts yet."

- [ ] **Step 4: `PelletInfo.jsx`** — `useQuery(['pellet-info'], GET /info)`. Render `info_text` as markdown using `marked` exactly like `frontend/src/pages/PelletManual.jsx` does (read PelletManual for the `marked.parse(...)` + `dangerouslySetInnerHTML` + `prose` class pattern; reuse it). Read-only.

- [ ] **Step 5: Routes** — `App.jsx`: under `/pellet-portal/home` add children `appointments`→`<PelletAppointments/>`, `receipts`→`<PelletReceipts/>`, `info`→`<PelletInfo/>` (+ imports).

- [ ] **Step 6: Staff Portal Info tab** — `PelletSettings.jsx`: add a "Portal Info" tab with a `<textarea>` (rows ~16, monospace) bound to `portal_info_text`, saved via the existing `PUT /pellets/config` pattern + invalidate `['pellet-config']`. Hint: "Shown to patients on the portal's Rules & Info page (markdown)."

- [ ] **Step 7: Build** — `cd frontend && npm run build` clean.

- [ ] **Step 8: Commit**
```bash
git add frontend/src/pages/pellet-portal/PelletPortalShell.jsx frontend/src/pages/pellet-portal/PelletAppointments.jsx frontend/src/pages/pellet-portal/PelletReceipts.jsx frontend/src/pages/pellet-portal/PelletInfo.jsx frontend/src/App.jsx frontend/src/pages/PelletSettings.jsx
git commit --no-verify -m "feat(pellet-portal): left-nav + Appointments/Receipts/Rules&Info pages + staff Portal Info tab (T3)"
```

---

## Task 4: Authenticated walk-through + deploy

**Files:** Create `backend/tests/test_pellet_portal_content_walkthrough.py`.

- [ ] **Step 1: Walk-through** — seed a patient with a completed `PelletVisit` + a `PelletVisitDose` and a paid `PelletPayment`; with a portal token: `GET /appointments` shows the visit with dosage; `GET /receipts` lists the payment (`has_receipt` true); `GET /info` returns the config text; and a staff-viewer token (mint via `/pellets/patients/{id}/portal-preview-token`) can GET all three (read-only preview). Print a 4-line narrated log under `capsys.disabled()`. Run `-s`; MUST pass. Full suite ≤ baseline; `npm run build` clean.
- [ ] **Step 2: Commit, then controller deploys**
```bash
git add backend/tests/test_pellet_portal_content_walkthrough.py
git commit --no-verify -m "test(pellet-portal): patient-content walk-through (T4)"
```
Then merge to main; build both images `--project=wwc-solutions`; deploy backend+frontend; smoke (`/api/pellet-portal/info` 401 noauth; `/pellet-portal/home/appointments` 200; `/pellet-portal/home/info` 200); push.

---

## Self-review notes
- Spec coverage: identity (shell, T3) ✓; left nav (T3) ✓; Appointments+dosage (T1) ✓; Receipts+Stripe link (T2) ✓; Rules&Info editable block (T1 config + /info, T3 page + T3 staff tab) ✓; walk-through (T4) ✓.
- `nullslast()` may not work on SQLite — fall back to ordering by `created_at.desc()` if it errors (note in T1/T2).
- Receipt-url must verify the payment belongs to the requesting patient (403/404) — covered by `test_receipt_url_rejects_other_patients_payment`.
- `PelletInfo` markdown: mirror PelletManual.jsx's `marked` usage exactly (don't introduce a new renderer).
- Suite ≤ baseline (69); deploy `--project=wwc-solutions`.
