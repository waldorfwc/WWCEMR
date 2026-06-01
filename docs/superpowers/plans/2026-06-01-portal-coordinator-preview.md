# Coordinator Portal Preview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Add a "View as patient" button on the staff surgery admin page so coordinators can see exactly what each patient sees in the portal — without going through SMS 2FA, and without being able to act on the patient's behalf.

**Architecture:** Staff-issued impersonation JWT with a `viewer: "staff:<email>"` claim and 1-hour TTL. The existing `require_portal_token` middleware accepts the JWT but enforces a method-level read-only gate: when `viewer` starts with `"staff:"`, all non-GET requests return 403. Frontend reads the `viewer` claim and hides every write button + shows a persistent "Preview mode" banner.

**Spec:** Memory file `project_portal_coordinator_preview.md`. Approved 2026-06-01.

**Tech stack:** Same as portal. New code in `patient_portal_auth.py`, `patient_portal.py` (middleware), new admin endpoint in a separate file. Frontend changes in `portal-api.js` + portal entry + each portal page.

**Key facts (don't relitigate):**
- `issue_portal_token(surgery)` at `backend/app/services/patient_portal_auth.py:119` issues a JWT with claims `{sub: surgery_id, aud: "wwc:patient-portal", exp}`. TTL is `surgery.scheduled_date + 30 days`.
- `verify_portal_token(token)` returns just `sub` (a string surgery_id). Need to either extend this or add a sibling that returns the full payload so middleware can read `viewer`.
- `require_portal_token` at `backend/app/routers/patient_portal.py:167` validates the token + surgery_id match. Doesn't see the HTTP method today.
- `get_current_user` at `backend/app/routers/auth.py:44` returns a **dict** with the staff user's email + permissions (per observation #945, "Enriched User Dict").
- Patient portal frontend stores the JWT in `localStorage` (`portal-api.js`).

---

## Task 1: Backend — viewer claim + readonly middleware gate

**Files:**
- Modify: `backend/app/services/patient_portal_auth.py` — `issue_portal_token` accepts optional `viewer`; add `decode_portal_token` that returns the full payload dict
- Modify: `backend/app/routers/patient_portal.py` — `require_portal_token` becomes method-aware
- Test: `backend/tests/test_patient_portal_preview_token.py` (new)

- [ ] **Step 1: Failing tests** at `backend/tests/test_patient_portal_preview_token.py`:

```python
"""Coordinator portal preview — viewer claim + read-only enforcement."""
from app.models.surgery import Surgery
from app.services.patient_portal_auth import (
    issue_portal_token, verify_portal_token, decode_portal_token,
)


def test_issue_portal_token_default_has_no_viewer(db):
    s = Surgery(chart_number="1", patient_name="Pat", status="new")
    db.add(s); db.commit(); db.refresh(s)
    token = issue_portal_token(s)
    payload = decode_portal_token(token)
    assert payload["sub"] == str(s.id)
    assert payload.get("viewer") is None


def test_issue_portal_token_with_viewer(db):
    s = Surgery(chart_number="2", patient_name="Pat", status="new")
    db.add(s); db.commit(); db.refresh(s)
    token = issue_portal_token(s, viewer="staff:ocooke@example.com",
                                  ttl_minutes=60)
    payload = decode_portal_token(token)
    assert payload["sub"] == str(s.id)
    assert payload["viewer"] == "staff:ocooke@example.com"
    # verify_portal_token still returns just the sub for backward compat
    assert verify_portal_token(token) == str(s.id)


def test_require_portal_token_blocks_writes_when_viewer_is_staff(client, db):
    """A token with viewer='staff:*' may GET but not POST/PUT/PATCH/DELETE."""
    from app.services.patient_portal_auth import issue_portal_token
    s = Surgery(chart_number="3", patient_name="Pat", status="new",
                  cell_phone="+12405551234", email="p@example.com")
    db.add(s); db.commit(); db.refresh(s)
    staff_tok = issue_portal_token(s, viewer="staff:ocooke@example.com",
                                       ttl_minutes=60)
    # GET works
    r_get = client.get(f"/api/patient/portal/{s.id}/dashboard",
                          headers={"Authorization": f"Bearer {staff_tok}"})
    assert r_get.status_code == 200, r_get.text
    # POST is blocked at the middleware
    r_post = client.post(f"/api/patient/portal/{s.id}/self-report/labs",
                            headers={"Authorization": f"Bearer {staff_tok}"})
    assert r_post.status_code == 403
    assert "read" in r_post.json()["detail"].lower()


def test_require_portal_token_allows_writes_for_patient_token(client, db):
    """A normal patient token (no viewer claim) can still POST."""
    from app.services.patient_portal_auth import issue_portal_token
    s = Surgery(chart_number="4", patient_name="Pat", status="new",
                  cell_phone="+12405551234", email="p@example.com")
    db.add(s); db.commit(); db.refresh(s)
    patient_tok = issue_portal_token(s)
    r_post = client.post(f"/api/patient/portal/{s.id}/self-report/labs",
                            headers={"Authorization": f"Bearer {patient_tok}"})
    assert r_post.status_code == 200, r_post.text
```

- [ ] **Step 2: Run, confirm fail.**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && \
  ./venv/bin/pytest tests/test_patient_portal_preview_token.py -v
```

- [ ] **Step 3: Extend `issue_portal_token`** at `patient_portal_auth.py:119`:

```python
def issue_portal_token(surgery: Surgery, *,
                          viewer: Optional[str] = None,
                          ttl_minutes: Optional[int] = None) -> str:
    """Sign a portal JWT. Default TTL is scheduled_date + 30 days. Pass
    `ttl_minutes` for short-lived tokens (e.g. coordinator preview = 60).
    Pass `viewer="staff:<email>"` so the read-only gate kicks in for
    non-GET requests."""
    if ttl_minutes is not None:
        exp = datetime.utcnow() + timedelta(minutes=ttl_minutes)
    else:
        exp = compute_token_exp(surgery)
    payload = {
        "sub": str(surgery.id),
        "aud": PORTAL_TOKEN_AUDIENCE,
        "exp": exp,
    }
    if viewer:
        payload["viewer"] = viewer
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")
```

- [ ] **Step 4: Add `decode_portal_token`** right after `verify_portal_token`:

```python
def decode_portal_token(token: str) -> Optional[dict]:
    """Return the full JWT payload dict (or None if invalid). Use this
    when you need the viewer claim; otherwise prefer verify_portal_token
    which just returns the sub."""
    try:
        return jwt.decode(token, settings.secret_key, algorithms=["HS256"],
                            audience=PORTAL_TOKEN_AUDIENCE)
    except JWTError:
        return None
```

- [ ] **Step 5: Update `require_portal_token`** at `patient_portal.py:167` to be method-aware:

```python
from fastapi import Request   # add to existing imports at top of file

def require_portal_token(
    request: Request,
    surgery_id: str,
    authorization: str = Header(default=""),
) -> str:
    """Validate Bearer token; ensure it's for THIS surgery_id. When the
    token's `viewer` claim is a staff impersonation (starts with 'staff:'),
    reject non-GET requests — coordinators preview, they don't act."""
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing token")
    token = authorization.split(" ", 1)[1].strip()
    payload = auth.decode_portal_token(token)
    if payload is None:
        raise HTTPException(status_code=401, detail="Invalid token")
    sub = payload.get("sub")
    if sub != surgery_id:
        raise HTTPException(status_code=403, detail="Wrong surgery")
    viewer = payload.get("viewer") or ""
    if viewer.startswith("staff:") and request.method != "GET":
        raise HTTPException(status_code=403,
                              detail="Preview mode is read-only.")
    return sub
```

- [ ] **Step 6: Run, confirm pass + full regression.**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && \
  ./venv/bin/pytest tests/test_patient_portal_preview_token.py tests/test_patient_portal_endpoints.py -v 2>&1 | tail -20
```

- [ ] **Step 7: Commit.**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/services/patient_portal_auth.py \
        backend/app/routers/patient_portal.py \
        backend/tests/test_patient_portal_preview_token.py
git commit -m "feat(portal-preview): viewer claim + read-only middleware gate"
```

---

## Task 2: Backend — staff endpoint that issues the preview token

**Files:**
- Create: `backend/app/routers/portal_preview.py`
- Modify: `backend/app/main.py` (register the new router)
- Modify: `backend/tests/test_patient_portal_preview_token.py` (append)

- [ ] **Step 1: Failing tests** — append to the test file:

```python
def test_portal_preview_token_requires_staff_auth(client, db):
    """Without a staff session cookie, the preview endpoint 401s."""
    from app.models.surgery import Surgery
    s = Surgery(chart_number="P", patient_name="Pat", status="new",
                  cell_phone="+12405551234")
    db.add(s); db.commit(); db.refresh(s)
    r = client.post(f"/api/admin/surgeries/{s.id}/portal-preview-token")
    assert r.status_code == 401


def test_portal_preview_token_returns_short_lived_staff_jwt(client, db,
                                                                authed_staff):
    """authed_staff is a fixture that returns a (client, user_dict, headers)
    triple with a logged-in staff session. Bakes viewer='staff:<email>'
    into the JWT."""
    from app.models.surgery import Surgery
    from app.services.patient_portal_auth import decode_portal_token
    s = Surgery(chart_number="P", patient_name="Pat", status="new",
                  cell_phone="+12405551234")
    db.add(s); db.commit(); db.refresh(s)
    r = client.post(f"/api/admin/surgeries/{s.id}/portal-preview-token",
                      headers=authed_staff["headers"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["surgery_id"] == str(s.id)
    payload = decode_portal_token(body["token"])
    assert payload["sub"] == str(s.id)
    assert payload["viewer"] == f"staff:{authed_staff['user']['email']}"
    # TTL ≤ 65 minutes from now (1-hour window with small margin)
    from datetime import datetime, timedelta
    exp = datetime.utcfromtimestamp(payload["exp"])
    assert exp <= datetime.utcnow() + timedelta(minutes=65)
```

If `authed_staff` isn't an existing fixture, scan `tests/conftest.py` for an equivalent (e.g., `staff_client` or `admin_user`) and adapt. If nothing exists, build the fixture inline in this test file using the existing auth helpers.

- [ ] **Step 2: Run, confirm fail.**

- [ ] **Step 3: Create the router** at `backend/app/routers/portal_preview.py`:

```python
"""Staff endpoint: issue a short-lived patient-portal JWT for previewing.

Coordinators click "View as patient" on the surgery admin page; this
returns an impersonation JWT bearing a viewer="staff:<email>" claim so
the patient_portal middleware can enforce read-only access.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.surgery import Surgery
from app.routers.auth import get_current_user
from app.services.patient_portal_auth import issue_portal_token

router = APIRouter(prefix="/api/admin/surgeries", tags=["admin"])


@router.post("/{surgery_id}/portal-preview-token")
def portal_preview_token(
    surgery_id: str,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="surgery not found")
    token = issue_portal_token(
        s,
        viewer=f"staff:{user['email']}",
        ttl_minutes=60,
    )
    return {"token": token, "surgery_id": str(s.id)}
```

- [ ] **Step 4: Register the router** in `backend/app/main.py`. Find the existing `app.include_router(...)` block. Add:

```python
from app.routers import portal_preview
app.include_router(portal_preview.router)
```

(Match the existing import style — there may be a grouped import line for routers.)

- [ ] **Step 5: Run, confirm pass.**

- [ ] **Step 6: Commit.**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/routers/portal_preview.py backend/app/main.py \
        backend/tests/test_patient_portal_preview_token.py
git commit -m "feat(portal-preview): POST /api/admin/surgeries/{sid}/portal-preview-token"
```

---

## Task 3: Frontend — auth helper exposes `viewer` + reads `?staff_token=`

**Files:**
- Modify: `frontend/src/lib/portal-api.js`
- Modify: the patient portal entry point — likely `frontend/src/pages/portal/PortalApp.jsx` or whatever component owns `/patient-portal/*` routes

- [ ] **Step 1: Read the existing entry point** to confirm where the login redirect logic lives:

```bash
/usr/bin/grep -rn "patient-portal\|portalApi\|PortalLogin\|Routes" \
  /Users/wwcclaudecode/Documents/wwc-era-project/frontend/src/pages/portal/ \
  /Users/wwcclaudecode/Documents/wwc-era-project/frontend/src/App.jsx \
  2>&1 | /usr/bin/head -25
```

The "is the user logged in?" gate likely lives in a `PortalRoute` or `useEffect` that checks `localStorage.getItem(TOKEN_KEY)`. The preview flow needs to intercept BEFORE that check fires.

- [ ] **Step 2: Decode helper in `portal-api.js`**. Add:

```javascript
export function decodePortalToken(token) {
  if (!token) return null
  try {
    const [, b64] = token.split('.')
    const json = atob(b64.replace(/-/g, '+').replace(/_/g, '/'))
    return JSON.parse(json)
  } catch {
    return null
  }
}

export function getPortalViewer() {
  const payload = decodePortalToken(localStorage.getItem(TOKEN_KEY))
  return payload?.viewer || null
}

export function isStaffPreview() {
  return (getPortalViewer() || '').startsWith('staff:')
}
```

The `TOKEN_KEY` const should already exist in this file; reuse it.

- [ ] **Step 3: Intercept `?staff_token=` on portal mount.** In the portal entry component (the one that owns `/patient-portal/:sid/*` routes), add a `useEffect` that:

```jsx
useEffect(() => {
  const url = new URL(window.location.href)
  const tok = url.searchParams.get('staff_token')
  if (tok) {
    // Bake into localStorage just like /verify would, then strip the
    // query param so the URL doesn't leak the token on share.
    setPortalAuth(tok, sid)
    url.searchParams.delete('staff_token')
    window.history.replaceState({}, '', url.toString())
  }
}, [sid])
```

`setPortalAuth(token, sid)` should be the existing helper that `/verify` uses — find it in `portal-api.js` and reuse.

- [ ] **Step 4: Build check.**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/frontend && npm run build 2>&1 | tail -6
```

- [ ] **Step 5: Commit.**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add frontend/src/lib/portal-api.js frontend/src/pages/portal/<entry>.jsx
git commit -m "feat(portal-preview): decode JWT viewer + accept ?staff_token= on mount"
```

---

## Task 4: Frontend — preview banner + hide write buttons

**Files:**
- Create: `frontend/src/components/portal/PreviewBanner.jsx`
- Modify: every portal page with write actions:
  - `frontend/src/pages/portal/Dashboard.jsx` (self-report buttons)
  - `frontend/src/pages/portal/Documents.jsx` (Clearance upload, FMLA upload + pay)
  - `frontend/src/pages/portal/Payments.jsx` (Pay button)
  - `frontend/src/pages/portal/Schedule.jsx` (slot claim)
  - `frontend/src/pages/portal/Consent.jsx` (sign button)

- [ ] **Step 1: PreviewBanner** at `frontend/src/components/portal/PreviewBanner.jsx`:

```jsx
import { getPortalViewer } from '../../lib/portal-api'

export default function PreviewBanner() {
  const viewer = getPortalViewer()
  if (!viewer?.startsWith('staff:')) return null
  const email = viewer.slice('staff:'.length)
  return (
    <div className="bg-amber-100 border-b border-amber-300 px-4 py-2
                       text-center text-sm text-amber-900">
      <strong>Preview mode</strong> — viewing as patient (read-only).
      Signed in as <strong>{email}</strong>.
    </div>
  )
}
```

Render at the top of the portal layout (find the outer `<div>` in the portal entry component and put `<PreviewBanner />` first).

- [ ] **Step 2: Hide writes** — in each of the 5 pages above, import:

```jsx
import { isStaffPreview } from '../../lib/portal-api'
```

Then wrap or gate every write button:

```jsx
{!isStaffPreview() && (
  <button onClick={...}>Upload</button>
)}
```

A pure render-time check is fine; no need for state. For buttons that have a `disabled` state already, prefer `disabled={busy || isStaffPreview()}` so the layout stays the same.

For FmlaCard (Documents.jsx) and ClearanceCard, also hide the "Pay $25" button and the StepUpPayFlow trigger. For Schedule.jsx, hide the slot claim CTA. For Consent.jsx, hide the sign action.

- [ ] **Step 3: Build check.**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/frontend && npm run build 2>&1 | tail -6
```

- [ ] **Step 4: Commit.**

```bash
git add frontend/src/components/portal/PreviewBanner.jsx \
        frontend/src/pages/portal/Dashboard.jsx \
        frontend/src/pages/portal/Documents.jsx \
        frontend/src/pages/portal/Payments.jsx \
        frontend/src/pages/portal/Schedule.jsx \
        frontend/src/pages/portal/Consent.jsx
git commit -m "feat(portal-preview): PreviewBanner + hide write buttons when staff viewer"
```

---

## Task 5: Frontend — "View as patient" button on SurgeryDetail

**Files:**
- Modify: `frontend/src/pages/SurgeryDetail.jsx`

- [ ] **Step 1: Inspect** the existing SurgeryDetail layout to find a good place for the button (likely near the patient name header or in an action toolbar):

```bash
/usr/bin/grep -n "patient_name\|chart_number\|<button\|toolbar\|Header" \
  /Users/wwcclaudecode/Documents/wwc-era-project/frontend/src/pages/SurgeryDetail.jsx \
  2>&1 | /usr/bin/head -20
```

- [ ] **Step 2: Add the button + handler**. Replace the section identified in Step 1:

```jsx
async function viewAsPatient() {
  try {
    const { data } = await api.post(
      `/api/admin/surgeries/${sid}/portal-preview-token`
    )
    const url = `/patient-portal/${data.surgery_id}?staff_token=${
      encodeURIComponent(data.token)
    }`
    window.open(url, '_blank', 'noopener,noreferrer')
  } catch (e) {
    alert(e?.response?.data?.detail || 'Could not start preview.')
  }
}
```

```jsx
<button onClick={viewAsPatient} className="btn-secondary text-sm">
  View as patient
</button>
```

`api` is the existing axios instance for admin calls — find the import at the top of the file. If it's named differently (e.g., `axios` or `client`), match it.

- [ ] **Step 3: Build check + commit.**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/frontend && npm run build 2>&1 | tail -6
git add frontend/src/pages/SurgeryDetail.jsx
git commit -m "feat(portal-preview): View as patient button on SurgeryDetail"
```

---

## Task 6: Smoke test in prod (manual)

I drive this after Tasks 1–5 are merged + deployed.

- [ ] **Step 1: Deploy backend v47 + frontend v_portal_preview**

```bash
cd backend && gcloud builds submit --project=wwc-solutions --region=us-east4 \
  --tag=us-east4-docker.pkg.dev/wwc-solutions/app/backend:v47 .
gcloud run deploy backend --project=wwc-solutions --region=us-east4 \
  --image=us-east4-docker.pkg.dev/wwc-solutions/app/backend:v47 --quiet
# Then frontend same pattern with tag v_portal_preview
```

- [ ] **Step 2: Pick a real surgery** that has interesting state (clearance required, FMLA in progress, balance due, slots available). Get its sid from the admin UI.

- [ ] **Step 3: Click "View as patient"** on the SurgeryDetail page. Verify:
  - A new tab opens at `/patient-portal/{sid}` with no query param (token stripped)
  - The "Preview mode" amber banner shows with my email
  - The Dashboard renders fully populated (milestones, next-action, etc.)

- [ ] **Step 4: Try to write** (each should fail or be hidden):
  - Click around the FmlaCard, ClearanceCard, Payments page, Schedule, Consent — all write buttons should be HIDDEN (not just disabled)
  - From DevTools, try a direct `fetch('/api/patient/portal/{sid}/self-report/labs', { method: 'POST', headers: ... })` — verify 403 "Preview mode is read-only"

- [ ] **Step 5: Verify reads work** for each page (Dashboard, Documents, Payments, Schedule, Consent, Messages if exists).

- [ ] **Step 6: Verify the token TTL** by leaving the tab open ~65 minutes and confirming reads start failing with 401.

- [ ] **Step 7: No cleanup needed** — we used a real surgery, made no writes, and the preview JWT can't do anything after it expires.

- [ ] **Step 8: Mark Task #154 complete.**
