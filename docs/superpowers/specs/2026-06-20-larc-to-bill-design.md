# LARC "To Bill" Worklist — Design

**Date:** 2026-06-20
**Status:** Approved (design); ready for implementation plan
**Area:** Device Tracking (LARC)

## Goal

Add a "To Bill" navigation tab to Device Tracking: a worklist of practice-owned devices that have been checked out but not yet billed, with an inline claim-number field to record the ModMed claim and complete billing.

## Decisions (approved)

1. **Scope:** all **checked-out, not-yet-billed** assignments whose device is **practice-owned** (`wwc_owned` or `wwc_claimed`). Patient-owned devices are excluded (they have no billing step).
2. **Two row states:** an **inserted** device shows an editable claim-# field + Save (ready to bill now); a device that's **checked out but not yet inserted** shows an "Awaiting insertion" badge with the claim field disabled.
3. **Tab placement:** after "Checkouts" in the LARC nav. **Tier:** VIEW to see the list; saving a claim uses the existing WORK-gated `/bill` endpoint.

## Background (existing pieces reused)

- **Billing endpoint (unchanged):** `POST /api/larc/assignments/{id}/bill {claim_number}` (`backend/app/routers/larc.py`, `mark_billed`) — WORK-gated; requires `status == "inserted"`; rejects patient-owned devices; sets `claim_number`/`billed_at`/`billed_by`, marks the `billed` milestone done, flips assignment + device status to `billed`.
- **Milestones:** `device_checked_out`, `device_inserted`, `billed` (`backend/app/services/larc/workflow.py`). The `inserted_not_billed` bucket = `done(device_inserted) and not done(billed)`.
- **Ownership:** `LarcDevice.ownership ∈ {patient_owned, wwc_owned, wwc_claimed}`.
- **Assignment serializer** (`_assignment_dict`) already exposes `device_ownership`, `device_our_id`, `device_type_name`, `patient_name`, `chart_number`, `milestones`.
- **Nav:** `frontend/src/components/larc/LarcNav.jsx` (tab list); routes in `frontend/src/routes.jsx` under `/larc`.

## Architecture

A single read endpoint feeds a single new page; billing reuses the existing endpoint.

```
LarcNav "To Bill" tab → /larc/to-bill (LarcToBill.jsx)
        │  GET /api/larc/to-bill
        ▼
  [practice-owned, device_checked_out done, billed not done]  → rows {patient, device, checked_out_at, inserted, claim_number}
        │  per inserted row: claim # field + Save
        ▼
  POST /api/larc/assignments/{id}/bill {claim_number}   (existing)
        │ success → row drops off the list
```

## Components

### Backend — `GET /api/larc/to-bill`
- New route in `backend/app/routers/larc.py`, gated `requires_tier(Module.LARC, Tier.VIEW)`.
- Query active, not-deleted assignments; include one where:
  - device ownership ∈ {`wwc_owned`, `wwc_claimed`} (a device is bound by checkout time, so `a.device` exists),
  - `device_checked_out` milestone is done,
  - `billed` milestone is NOT done.
- Use the existing milestone `done()`-style check (reuse the helper pattern in `assignment_buckets`, or load `a.milestones`).
- Return `{ "total": N, "items": [ {assignment_id, patient_name, chart_number, device_our_id, device_type_name, device_ownership, checked_out_at, inserted (bool), claim_number} ] }`, ordered by `checked_out_at` ascending (oldest first — bill the oldest first).
- `checked_out_at`: the `device_checked_out` milestone's completion timestamp (or the assignment's relevant checkout timestamp if that's where it's recorded — use whatever field records checkout time). `inserted` = `device_inserted` milestone done.

### Frontend — `LarcToBill.jsx` (route `/larc/to-bill`)
- `useQuery(['larc-to-bill'])` → `GET /larc/to-bill`.
- Table columns: **Patient** (name + chart #), **Device** (our_id + type), **Checked Out** (`fmt.date`), **Status**, **Claim #**.
  - **inserted** row: a claim-# text input + "Save" button → `useMutation` POST `/larc/assignments/{id}/bill {claim_number}`; on success invalidate `['larc-to-bill']` + `['larc-dashboard']` (row drops off). Disable Save until the field is non-empty.
  - **not-inserted** row: an amber "Awaiting insertion" badge; claim field + Save disabled.
- Empty state: "No devices waiting to be billed."
- Title Case headers/labels.

### Nav + route
- `LarcNav.jsx`: add `{ to: '/larc/to-bill', label: 'To Bill', tier: TIER.VIEW }` after the Checkouts entry.
- `routes.jsx`: add `{ path: 'to-bill', element: <LarcToBill />, module: M.LARC, tier: TIER.VIEW }` under the `/larc` parent.

## Data flow
1. Staff open **To Bill** → see oldest-first practice-owned checked-out-unbilled devices.
2. For an inserted device, staff type the ModMed claim # and Save → `/bill` records it → assignment/device → `billed`, `billed` milestone done → row disappears.
3. A not-yet-inserted device shows "Awaiting insertion" until the insertion outcome is recorded elsewhere; then it becomes billable on this list.

## Error handling & edge cases
- **Patient-owned excluded** — never appear (no claim/billing for them).
- **Save on a not-inserted row** is impossible (UI-disabled); the backend `/bill` also 409s if `status != inserted`, as a backstop.
- **Empty claim #** — Save disabled client-side; backend 422s on blank.
- **Concurrent bill** — `/bill` is idempotent enough (second attempt 409s "can only bill an inserted assignment" once status flips to billed); the row also disappears after the first success.
- **Permissions** — VIEW can see; a non-WORK user's Save 403s server-side (acceptable; could hide Save for non-WORK later).

## Testing
- Backend (`client`/`db`): `GET /larc/to-bill` includes a practice-owned inserted-not-billed assignment (with `inserted: true`) AND a practice-owned checked-out-not-inserted one (`inserted: false`); excludes a patient-owned checked-out one; excludes a not-yet-checked-out one; excludes an already-billed one; ordering is oldest-first. (The `/bill` submission path is already covered by existing tests.)
- Frontend: `npm run build`; manual — inserted rows accept a claim # and drop off after Save; not-inserted rows show "Awaiting insertion" with Save disabled; the tab appears after Checkouts.

## Out of scope
- Changing the billing endpoint or the close-out (patient-owned) path.
- Bulk-billing multiple rows at once (single-row Save is enough for v1).

## Affected/new files
- Backend: `app/routers/larc.py` (new `GET /to-bill`); test `tests/test_larc_to_bill.py` (new).
- Frontend: `src/pages/LarcToBill.jsx` (new), `src/components/larc/LarcNav.jsx`, `src/routes.jsx`.
