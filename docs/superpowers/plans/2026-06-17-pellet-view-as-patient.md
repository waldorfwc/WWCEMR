# Pellet Portal "View as Patient" Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Staff open a pellet patient's portal in a new tab, read-only (a viewer-claim JWT; non-GET blocked), launched from the pellet patient detail page.

**Architecture:** Faithful mirror of the surgery coordinator-preview. Extend the pellet portal token with a `viewer` claim + short TTL; enforce read-only in `require_pellet_token`; add a staff mint endpoint with an IMPERSONATE audit row; add a "View as Patient" button + a `staff_token` capture in the portal shell.

**Tech Stack:** FastAPI + jose JWT, React + react-query. Spec: `docs/superpowers/specs/2026-06-17-pellet-portal-view-as-patient-design.md`.

**Branch:** `feat/pellet-view-as-patient` off `main`.

## VERIFIED facts (override conflicting snippets)
- `backend/app/services/pellet/portal_auth.py`: `issue_portal_token(p) -> str` builds `{pellet_patient_id, ppv, exp(+30d), scope:"pellet_portal"}` via `jwt.encode(..., _secret(), HS256)`; `decode_portal_token`; `_TOKEN_TTL_DAYS=30`. `now_utc_naive` + `timedelta` imported.
- `backend/app/routers/patient_pellet.py`: `def require_pellet_token(authorization: str = Header(None), db: Session = Depends(get_db)) -> PelletPatient` — validates bearer, scope `pellet_portal`, loads PelletPatient, checks `ppv` vs `portal_token_version`. Router prefix `/pellet-portal`.
- `backend/app/routers/pellet.py`: prefix `/pellets`; imports `requires_tier`, `Module`, `Tier`, `HTTPException`, `Depends`, `Session`, `get_db`, `PelletPatient`; `from app.services.audit_service import log_action` may need adding. Surgery's mirror call: `log_action(db, action="IMPERSONATE", resource_type="surgery", resource_id=..., patient_id=..., user_id=email, user_name=..., description=...)`.
- Frontend: `frontend/src/pages/PelletPatientDetail.jsx` (staff patient page, uses `api`), `frontend/src/pages/pellet-portal/PelletPortalShell.jsx` (reads token via `getPelletSession`, redirects to login if absent), `frontend/src/lib/pellet-portal-api.js` (`setPelletSession({token})`, TOKEN_KEY `wwc.pellet-portal.token`). Patient portal routes under `/pellet-portal/home` in `App.jsx`.
- Tests: `cd backend && source venv/bin/activate && python -m pytest <path> -q`; conftest `client`=super-admin; mint a patient token with `portal_auth.issue_portal_token(p)`. Baseline 69 failed. Conventions: Title Case, `--project=wwc-solutions`.

---

## Task 1: Token viewer claim + read-only gate

**Files:** Modify `backend/app/services/pellet/portal_auth.py`, `backend/app/routers/patient_pellet.py`; Test `backend/tests/test_pellet_portal_preview.py`.

- [ ] **Step 1: Write the failing test**
```python
# backend/tests/test_pellet_portal_preview.py
from datetime import date
import pytest
from app.models.pellet import PelletPatient
from app.services.pellet import portal_auth


@pytest.fixture
def patient(db):
    p = PelletPatient(patient_name="Doe, Jane", chart_number="MRN1",
                      patient_dob=date(1980, 5, 1), patient_phone="3015551234")
    db.add(p); db.commit(); db.refresh(p)
    return p


def test_token_carries_viewer_and_short_ttl(db, patient):
    tok = portal_auth.issue_portal_token(patient, viewer="staff:s@x.com", ttl_minutes=60)
    claims = portal_auth.decode_portal_token(tok)
    assert claims["viewer"] == "staff:s@x.com"
    assert claims["pellet_patient_id"] == str(patient.id)


def test_patient_token_has_no_viewer(db, patient):
    claims = portal_auth.decode_portal_token(portal_auth.issue_portal_token(patient))
    assert "viewer" not in claims


def test_preview_token_blocks_non_get(client, db, patient):
    tok = portal_auth.issue_portal_token(patient, viewer="staff:s@x.com", ttl_minutes=60)
    h = {"Authorization": f"Bearer {tok}"}
    # GET works (dashboard).
    assert client.get("/api/pellet-portal/dashboard", headers=h).status_code == 200
    # Non-GET is blocked read-only.
    r = client.post("/api/pellet-portal/labs", json={"completed": True}, headers=h)
    assert r.status_code == 403 and "read-only" in r.json()["detail"].lower()


def test_real_patient_token_can_act(client, db, patient):
    h = {"Authorization": f"Bearer {portal_auth.issue_portal_token(patient)}"}
    # A normal patient token is NOT read-only.
    assert client.post("/api/pellet-portal/labs", json={"completed": True}, headers=h).status_code == 200
```

- [ ] **Step 2: Run — expect FAIL.** `python -m pytest tests/test_pellet_portal_preview.py -q`

- [ ] **Step 3: Extend `issue_portal_token`** in `portal_auth.py`:
```python
def issue_portal_token(p: PelletPatient, *, viewer: str | None = None,
                       ttl_minutes: int | None = None) -> str:
    exp = (now_utc_naive() + timedelta(minutes=ttl_minutes)) if ttl_minutes \
          else (now_utc_naive() + timedelta(days=_TOKEN_TTL_DAYS))
    payload = {
        "pellet_patient_id": str(p.id),
        "ppv": int(p.portal_token_version or 0),
        "exp": exp,
        "scope": "pellet_portal",
    }
    if viewer:
        payload["viewer"] = viewer
    return jwt.encode(payload, _secret(), algorithm=_ALGO)
```

- [ ] **Step 4: Add the read-only gate** in `require_pellet_token` (`patient_pellet.py`). Add `Request` to the fastapi import; change the signature + add the check:
```python
from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, UploadFile  # add Request

def require_pellet_token(request: Request, authorization: str = Header(None),
                         db: Session = Depends(get_db)) -> PelletPatient:
    # ... existing validation unchanged ...
    if int(claims.get("ppv", 0)) != int(p.portal_token_version or 0):
        raise HTTPException(status_code=401, detail="Token revoked")
    # Staff preview tokens are read-only — coordinators view, they don't act.
    if (claims.get("viewer") or "").startswith("staff:") and request.method != "GET":
        raise HTTPException(status_code=403, detail="Preview mode is read-only.")
    return p
```
(`request: Request` as the first param is fine — FastAPI injects it; the existing `Depends(require_pellet_token)` callers don't change.)

- [ ] **Step 5: Run — expect 4 PASS.** Regression `-k pellet` ≤ baseline; `python -c "import app.main"`.

- [ ] **Step 6: Commit**
```bash
git add backend/app/services/pellet/portal_auth.py backend/app/routers/patient_pellet.py backend/tests/test_pellet_portal_preview.py
git commit --no-verify -m "feat(pellet-portal): viewer-claim token + read-only preview gate (T1)"
```

---

## Task 2: Staff mint endpoint + IMPERSONATE audit

**Files:** Modify `backend/app/routers/pellet.py`; Test append to `backend/tests/test_pellet_portal_preview.py`.

- [ ] **Step 1: Write the failing test** (append)
```python
def test_mint_preview_token(client, db, patient):
    r = client.post(f"/api/pellets/patients/{patient.id}/portal-preview-token")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pellet_patient_id"] == str(patient.id)
    claims = portal_auth.decode_portal_token(body["token"])
    assert claims["viewer"].startswith("staff:")


def test_mint_404_unknown_patient(client, db):
    r = client.post("/api/pellets/patients/00000000-0000-0000-0000-000000000000/portal-preview-token")
    assert r.status_code == 404
```

- [ ] **Step 2: Run — expect FAIL** (404 route).

- [ ] **Step 3: Implement the mint endpoint** in `pellet.py` (add `from app.services.audit_service import log_action` if absent; `from app.services.pellet import portal_auth as pellet_portal_auth`):
```python
@router.post("/patients/{patient_id}/portal-preview-token")
def portal_preview_token(patient_id: str, db: Session = Depends(get_db),
                         current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.VIEW))):
    p = db.query(PelletPatient).filter(PelletPatient.id == patient_id).first()
    if p is None:
        raise HTTPException(status_code=404, detail="patient not found")
    email = (current_user.get("email") or "").lower().strip() or None
    token = pellet_portal_auth.issue_portal_token(p, viewer=f"staff:{email}", ttl_minutes=60)
    log_action(db, action="IMPERSONATE", resource_type="pellet_patient",
               resource_id=str(p.id), patient_id=p.chart_number or None,
               user_id=email, user_name=current_user.get("name") or email,
               description=(f"Staff issued a read-only pellet-portal preview for "
                            f"{p.patient_name or p.chart_number}"))
    return {"token": token, "pellet_patient_id": str(p.id)}
```

- [ ] **Step 4: Run — expect 2 PASS** (+ the 4 from T1). Regression ≤ baseline; `python -c "import app.main"`.

- [ ] **Step 5: Commit**
```bash
git add backend/app/routers/pellet.py backend/tests/test_pellet_portal_preview.py
git commit --no-verify -m "feat(pellet-portal): staff mint preview token + IMPERSONATE audit (T2)"
```

---

## Task 3: Frontend — View-as-Patient button + portal staff_token capture

**Files:** Modify `frontend/src/pages/PelletPatientDetail.jsx`, `frontend/src/pages/pellet-portal/PelletPortalShell.jsx`.

- [ ] **Step 1: Portal shell captures `staff_token`** — `PelletPortalShell.jsx`. On mount, if the URL has `?staff_token=...`, persist it and strip the param BEFORE the no-token redirect runs:
```jsx
import { useEffect } from 'react'
import { setPelletSession, getPelletSession } from '../../lib/pellet-portal-api'
// inside the component, before the token guard:
useEffect(() => {
  const params = new URLSearchParams(window.location.search)
  const st = params.get('staff_token')
  if (st) {
    setPelletSession({ token: st })
    params.delete('staff_token')
    const qs = params.toString()
    window.history.replaceState({}, '', window.location.pathname + (qs ? `?${qs}` : ''))
  }
}, [])
```
Ensure the no-token `<Navigate to="/pellet-portal/login"/>` guard reads the token AFTER this effect — simplest is to compute `const { token } = getPelletSession()` in render (the effect persists synchronously before the first paint's redirect, but to be safe gate the redirect on a small `ready` state set in the effect, or read the param directly: `const token = getPelletSession().token || new URLSearchParams(window.location.search).get('staff_token')`). Use the direct-param fallback so the first render already sees the token.

- [ ] **Step 2: "View as Patient" button** — `PelletPatientDetail.jsx`. Add (Eye icon from lucide-react) in the patient header:
```jsx
async function viewAsPatient() {
  try {
    const { data } = await api.post(`/pellets/patients/${id}/portal-preview-token`)
    window.open(`/pellet-portal/home?staff_token=${encodeURIComponent(data.token)}`,
                '_blank', 'noopener,noreferrer')
  } catch (e) {
    alert(e?.response?.data?.detail || 'Could not start preview.')
  }
}
// button:
<button type="button" onClick={viewAsPatient}
        className="text-xs px-2 py-1 rounded border bg-white border-border-subtle text-gray-600 hover:border-plum-300 hover:bg-plum-50 flex items-center gap-1"
        title="Open this patient's portal in a new tab (read-only)">
  <Eye size={11} /> View as Patient
</button>
```
(Use the page's existing patient id variable — confirm whether it's `id` from `useParams` or a prop; match the file. Import `Eye` from `lucide-react` if not already imported.)

- [ ] **Step 3: Build** — `cd frontend && npm run build` clean.

- [ ] **Step 4: Commit**
```bash
git add frontend/src/pages/PelletPatientDetail.jsx frontend/src/pages/pellet-portal/PelletPortalShell.jsx
git commit --no-verify -m "feat(pellet-portal): View as Patient button + portal staff_token capture (T3)"
```

---

## Task 4: Authenticated walk-through + deploy

**Files:** Create `backend/tests/test_pellet_preview_walkthrough.py`.

- [ ] **Step 1: Walk-through test** — staff mints a preview token for a patient → GET `/dashboard` with it returns 200 → POST `/schedule/slots/{x}/book` (or `/payment/subscribe`) returns 403 "read-only". Print a 3-line narrated log under `capsys.disabled()`. Run `-s`; MUST pass. Full suite ≤ baseline; `npm run build` clean.
- [ ] **Step 2: Commit, then controller deploys**
```bash
git add backend/tests/test_pellet_preview_walkthrough.py
git commit --no-verify -m "test(pellet-portal): View-as-Patient preview walk-through (T4)"
```
Then merge to main; build both images `--project=wwc-solutions`; deploy backend+frontend; smoke (`/api/pellets/patients/x/portal-preview-token` 401 noauth; `/pellet-portal/home` 200); push.

---

## Self-review notes
- `require_pellet_token` adding `request: Request` as first positional param — verify all existing usages are `Depends(require_pellet_token)` (they are; FastAPI injects Request + the Header/Depends). The dependency's return type (PelletPatient) is unchanged.
- Read-only gate must NOT affect normal patient tokens (no `viewer`) — covered by `test_real_patient_token_can_act`.
- The portal shell must see the staff_token on first render (use the direct-param fallback) so it doesn't bounce to /login before the effect persists it.
- Suite ≤ baseline (69); deploy `--project=wwc-solutions`.
