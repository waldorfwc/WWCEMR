# LARC Sweep Auto-Claim — Design

**Date:** 2026-06-21
**Status:** Approved

## Problem

When an automatic LARC sweep reallocates a device — frees it from an
assignment, marks it `unassigned`, and adds the patient to the Owed list —
it does **not** change the device's `ownership`. A patient-owned (pharmacy)
device that a patient never used stays classified `patient_owned`, so WWC
cannot bill insurance for it even though WWC has effectively claimed it.

Managers can already flip ownership manually (the device-detail
"change" link → `ChangeOwnershipModal` → `POST /larc/devices/{id}/change-ownership`),
which is the correct path when a patient explicitly declines. But the
*automatic* year-of-receipt / unused-device path is unhandled.

## Goal

Make the reallocation sweeps claim ownership themselves: when a sweep pulls
a **patient-owned** device back to the Owed list, also flip its ownership to
`wwc_claimed` and record it in the audit trail. Managers retain the manual
path for explicit-decline cases (already shipped, unchanged).

## Scope

In scope (backend only):
1. Auto-flip `patient_owned` → `wwc_claimed` inside the shared
   `_push_to_owed()` helper, so both sweeps inherit the behavior.
2. Re-base the stale-assignment sweep's 180-day clock on device receipt.

Out of scope:
- The manual Change Ownership UI + endpoint — **already shipped** and
  unchanged. The device-detail page renders a MANAGE-gated "change" link →
  `ChangeOwnershipModal` (required reason, three ownership options) wired to
  `POST /larc/devices/{device_id}/change-ownership`.
- The `wwc_claimed` badge label — already rendered as "WWC Claimed".

## Design

### 1. Centralized auto-claim in `_push_to_owed()`

`backend/app/services/larc/sweeps.py` — `_push_to_owed(db, a, expires_at, actor, summary)`
is called by **both** `sweep_expiry_hold` and `sweep_stale_assignments`. It
already deactivates the assignment, sets `a.device.status = "unassigned"`,
creates the `LarcOwedPatient` row, and logs a `device_reallocated` audit
event.

Add, after the existing reallocation logic and **only when**
`a.device.ownership == "patient_owned"`:

- Set `a.device.ownership = "wwc_claimed"`.
- Log a second audit event via `log_audit`, `action="ownership_changed"`,
  matching the manual endpoint's format:
  - `device=a.device`
  - `summary`: `"Ownership changed: patient owned → wwc claimed. Reason: auto-claimed on reallocation (<actor>)."`
  - `detail={"from": "patient_owned", "to": "wwc_claimed", "reason": "<auto reason>"}`

Devices that are `wwc_owned` or already `wwc_claimed` are left untouched —
"claimed" only makes sense for a formerly patient-owned device.

`purchasing_patient_chart` / `purchasing_patient_name` are **not** modified —
they were recorded at device creation and already capture who originally
paid; that's what distinguishes a `wwc_claimed` device from a `wwc_owned`
one.

The flip happens inside the same transaction as the reallocation (the
sweeps `db.commit()` once at the end), so reallocation + claim are atomic.

### 2. Re-base the stale-sweep clock on receipt

`backend/app/services/larc/sweeps.py` — `sweep_stale_assignments`.

Current candidate filter:

```python
.filter(LarcAssignment.is_active.is_(True),
        LarcAssignment.created_at <= cutoff,
        LarcAssignment.inserted_at.is_(None),
        LarcAssignment.status.notin_(["billed", "cancelled"]))
```

Change the date comparison to measure from device receipt when available,
falling back to assignment creation:

```python
from sqlalchemy import func
...
.filter(LarcAssignment.is_active.is_(True),
        func.coalesce(LarcAssignment.device_received_at,
                      LarcAssignment.created_at) <= cutoff,
        LarcAssignment.inserted_at.is_(None),
        LarcAssignment.status.notin_(["billed", "cancelled"]))
```

Rationale: "not used after receipt of 180 days." Pharmacy devices arrive
after enrollment, so `created_at` starts the clock too early. In-stock
assignments have no `device_received_at`, so `COALESCE` keeps their current
behavior exactly.

`device_received_at` already exists on `LarcAssignment` (used by
`sweep_pharmacy_sla`). The expiry sweep is unchanged.

## Behavior matrix

| Sweep | Device ownership before | Owed list | Ownership after | `ownership_changed` audit |
|-------|-------------------------|-----------|-----------------|---------------------------|
| stale (180d) | patient_owned | patient added | **wwc_claimed** | yes |
| stale (180d) | wwc_owned | patient added | wwc_owned | no |
| expiry (365d) | patient_owned | patient added | **wwc_claimed** | yes |
| expiry (365d) | wwc_owned | patient added | wwc_owned | no |

## Testing

Backend pytest (`backend/tests/`):

1. **Stale sweep claims a patient-owned device** — assignment with a
   patient-owned device, `device_received_at` 200 days ago, not inserted →
   after `sweep_stale_assignments`: device `ownership == "wwc_claimed"`,
   status `unassigned`, patient on Owed list, an `ownership_changed` audit
   event exists for the device.
2. **Stale sweep leaves a wwc-owned device's ownership alone** — same setup
   but `ownership="wwc_owned"` → reallocated to Owed, but
   `ownership == "wwc_owned"` and **no** `ownership_changed` event.
3. **Expiry sweep claims a patient-owned device** — patient-owned device
   expiring within the hold window, active assignment → after
   `sweep_expiry_hold`: `ownership == "wwc_claimed"` + audit event.
4. **Receipt-basis cutoff** — patient-owned assignment created 400 days ago
   but `device_received_at` 30 days ago (< 180) → **not** swept (clock runs
   from receipt, not creation). Counterpart: received 200 days ago → swept.

## Risks

- Re-basing the stale clock changes which assignments the sweep catches.
  Net effect: pharmacy devices that were being swept slightly early (clock
  from enrollment) now wait until 180 days after actual receipt. In-stock
  assignments are unaffected (`COALESCE` fallback). This is the intended
  correction, confirmed with the user.
- Auto-claim is idempotent: `_push_to_owed` already dedupes the Owed row,
  and once flipped to `wwc_claimed` the device no longer matches the
  `patient_owned` guard, so re-running a sweep won't re-log.
