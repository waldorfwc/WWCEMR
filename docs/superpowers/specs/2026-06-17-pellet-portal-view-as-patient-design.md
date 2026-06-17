# Pellet Portal — "View as Patient" (staff preview) Design

**Status:** Approved 2026-06-17. A faithful mirror of the existing surgery coordinator-preview,
adapted to the pellet patient portal. Staff open a patient's pellet portal in a new tab, **read-only**.

## Goal
Let staff preview exactly what a pellet patient sees in their portal (checklist, payments, schedule),
without being able to act as the patient — no booking, paying, subscribing, submitting labs/mammo, or
signing consent while impersonating.

## Precedent (mirror this)
Surgery: `issue_portal_token(s, viewer="staff:<email>", ttl_minutes=60)` adds a `viewer` claim;
`require_portal_token` rejects non-GET when `viewer` starts with `staff:`; `portal_preview.py` mints
the token + writes an `IMPERSONATE` audit row; `SurgeryDetail.jsx` "View as Patient" button opens
`/portal/s/{id}?staff_token=<token>`.

## Design

### 1. Token — `backend/app/services/pellet/portal_auth.py`
Extend `issue_portal_token(p, *, viewer: str | None = None, ttl_minutes: int | None = None)`:
- Embed `"viewer": viewer` in the JWT payload when provided.
- When `ttl_minutes` is given, set `exp = now + ttl_minutes` (else the existing 30-day default).
- The `scope` stays `pellet_portal` and `ppv` is still included (so revocation still applies).

### 2. Read-only gate — `require_pellet_token`
Add `request: Request` to the dependency signature. After validating the token, if
`(claims.get("viewer") or "").startswith("staff:")` and `request.method != "GET"`, raise
`HTTPException(403, "Preview mode is read-only.")`. GET requests (dashboard, payment/status,
schedule slots, etc.) work normally so the preview renders fully.

### 3. Mint endpoint (staff) — `backend/app/routers/pellet.py`
`POST /pellets/patients/{patient_id}/portal-preview-token`, gated
`requires_tier(Module.PELLETS, Tier.VIEW)`:
- 404 if the patient doesn't exist.
- Mint `issue_portal_token(p, viewer=f"staff:{email}", ttl_minutes=60)`.
- Write a HIPAA `IMPERSONATE` audit row (via `log_action`: action="IMPERSONATE",
  resource_type="pellet_patient", resource_id=patient id, patient_id=chart_number, user_id=email,
  description noting read-only preview).
- Return `{"token": ..., "pellet_patient_id": ...}`.

### 4. Frontend launch — `frontend/src/pages/PelletPatientDetail.jsx`
Add a "View as Patient" button (Eye icon, secondary style) in the patient header:
`api.post('/pellets/patients/{id}/portal-preview-token')` → open
`/pellet-portal/home?staff_token=<encodeURIComponent(token)>` in a new tab (`noopener,noreferrer`).
Tooltip: "Open this patient's portal in a new tab (read-only)."

### 5. Portal accepts the staff token — `frontend/src/lib/pellet-portal-api.js` + shell
On portal load, if a `staff_token` query param is present, store it via `setPelletSession({token})`
(localStorage `wwc.pellet-portal.token`) and strip it from the URL. Simplest: do this in
`PelletPortalShell.jsx` on mount (read `?staff_token`, persist, then proceed) so any
`/pellet-portal/home*` deep link with the param authenticates. The existing 401-redirect interceptor
still applies if the token is missing/expired.

## Testing
- Backend: token round-trip carries `viewer` + honors `ttl_minutes`; `require_pellet_token` 403s a
  non-GET with a staff-viewer token but allows GET; mint endpoint returns a token + writes the
  IMPERSONATE audit row; a staff-viewer token can GET `/dashboard` but is 403'd on
  `POST /schedule/slots/{id}/book` and `POST /payment/subscribe`.
- Frontend: build clean; staff button + shell `staff_token` capture (headless render optional).
- Authenticated walk-through: mint a preview token → GET dashboard works → POST book returns 403
  read-only.

## Out of scope
- No change to the patient experience or any patient-facing behavior.
- Preview is whole-portal read-only (block all non-GET); no partial-action allowance.
- No separate "staff is previewing" banner in the portal UI this round (could add later); the
  read-only 403s are the guardrail.

## Conventions
Mirror surgery exactly; MM/DD/YYYY, Title Case; no secrets in source; deploy `--project=wwc-solutions`.
