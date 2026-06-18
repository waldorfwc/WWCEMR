# Permanent-Outcome Confirm — Recall Modals Design

**Status:** Approved 2026-06-17. Close the permanent-outcome confirm gap in BOTH recall detail modals
(WWE `Recalls.jsx` and pellet `PelletRecallDetail.jsx`): when a caller logs a "permanent" outcome
(Declined / Do Not Call / Patient deceased / Left practice), show an inline styled confirmation and,
on confirm, resend with `confirm_permanent=true`.

## Problem
`log_outcome` (`app/routers/recalls.py`) returns **HTTP 409** with a detail message when a permanent
outcome is submitted without `confirm_permanent=true` (`OutcomePayload.confirm_permanent` exists; the
pellet `/pellets/recall/{id}/outcome` reuses that same payload, so it already accepts the flag). But
neither modal sends the flag or handles the 409:
- **WWE `Recalls.jsx`:** shows a ⚠ "permanent suppression" warning (it knows `isPermanent` from the
  catalog) but its `submit` mutation sends no `confirm_permanent` and has no `onError` — so clicking
  "Log outcome" on a permanent outcome **fails silently** (409 swallowed; nothing happens).
- **Pellet `PelletRecallDetail.jsx`:** `logOutcome` has `onError: alert(detail)` — so a permanent
  outcome shows the 409 message as a dead-end alert with no way to proceed.

## Decision (from brainstorming)
- **No backend change** — `log_outcome` + `confirm_permanent` already exist and the pellet endpoint
  already accepts the flag.
- **Reactive, uniform approach in both modals:** on the outcome submit, if the response is **HTTP
  409**, show an **inline styled confirm** in the Log Call Outcome card (amber block with the
  backend's detail message + "Confirm & Remove" / "Cancel"). "Confirm & Remove" resends the SAME
  `{outcome, notes}` with `confirm_permanent: true`. "Cancel" dismisses. Non-409 errors alert as
  before. Same pattern in each modal (no shared component — their mutations are wired differently).

## Architecture

### Backend
No code change. Add a verification test only (the behavior already exists).

### Frontend — both modals (same pattern)
In each modal, the outcome-submit mutation gains:
1. **A `confirmPermanent` argument** — `mutationFn: (vars) => api.post(<outcome-url>, { outcome,
   notes: notes || null, confirm_permanent: vars?.confirmPermanent || false }).then(r => r.data)`.
   The normal "Log Outcome" button calls `submit.mutate()` (flag false).
2. **An `onError`** — if `err?.response?.status === 409`, set `permanentConfirm` state to
   `err.response.data.detail` (the message) instead of failing silently / alerting. Otherwise
   `alert(detail)` as today.
3. **An inline confirm block** in the Log Call Outcome card, shown when `permanentConfirm` is set: an
   amber-bordered box with the detail message and two buttons —
   - **Confirm & Remove** → `submit.mutate({ confirmPermanent: true })` (resends with the flag); on
     success the existing `onSuccess` runs (invalidate/refresh/close) and `permanentConfirm` clears.
   - **Cancel** → clears `permanentConfirm` (leaves the form intact).
4. On a successful submit, clear `permanentConfirm`.

`Recalls.jsx` keeps its existing proactive ⚠ "permanent suppression" warning (informative); the new
confirm block is the actionable step. `PelletRecallDetail.jsx` gets the same inline confirm block.
Title Case button labels ("Confirm & Remove", "Cancel"). Amber styling matching the app's warning
cards.

## Testing
- **Backend** (`backend/tests/test_pellet_recall_router.py`, append): posting a permanent outcome
  (e.g. `"Declined recall"`) to `/pellets/recall/{id}/outcome` without the flag → **409**; with
  `{"outcome": "Declined recall", "confirm_permanent": true}` → **200** and it's logged. (Confirms
  the pellet endpoint relays `confirm_permanent` through the delegated `log_outcome`.)
- **Frontend:** `npm run build` clean (both modals). The inline confirm UI is build-verified.

## File structure
- Modify `frontend/src/pages/PelletRecallDetail.jsx` (outcome mutation + inline confirm).
- Modify `frontend/src/pages/Recalls.jsx` (outcome mutation + inline confirm).
- `backend/tests/test_pellet_recall_router.py` — one verification test. No backend code change.

## Out of scope (YAGNI)
No backend changes; no change to the outcome taxonomy or to non-permanent outcomes; no new shared
component; no change to cooldown/completed outcome handling.

## Conventions
Title Case button labels; reuse the app's amber warning-card styling; the backend's 409 `detail` is
the single source of the message shown to the caller.
