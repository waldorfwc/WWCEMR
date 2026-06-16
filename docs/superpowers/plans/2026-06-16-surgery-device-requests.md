# Surgery → Device Tracking device requests

> subagent-driven-development. Backend TDD; suite baseline 69 failed / 0 errors. Frontend build + headless load before deploy.

**Branch:** `feat/surgery-device-requests` off `main`.

**Goal:** When a surgery that requires a LARC/office-procedure device is **scheduled**, auto-create a request in Device Tracking — captured as a `LarcAssignment` linked back to the surgery — recording **who** requested (the provider), **what** device, and **when**. The system **auto-picks the path** from inventory: a matching device in stock → in-stock (allocate-existing) flow; otherwise → the device-type's order flow (pharmacy_order / office_procedure). The coordinator still confirms the final allocate/send in Device Tracking.

## Decisions (from user)
- Trigger: **on scheduling** (scheduled_date set).
- Decision: **auto-pick** source_flow from inventory; coordinator confirms.
- Requester = the **provider** (`surgery.surgeon_primary`); `created_by` = the staff/system that scheduled.
- The `LarcAssignment` IS the request (surfaces in existing LARC buckets: new / needs_benefits / op_needs_device).

## Current state (verified)
- Surgery devices: `Surgery.device_types` (JSON list of names, non-"None"), `device_required`, `surgeon_primary`. Names map 1:1 to `LarcDeviceType.name` (Liletta in_stock; Mirena/Skyla/Kyleena/Paragard/Nexplanon pharmacy_order; NovaSure/Benesta office_procedure).
- `LarcAssignment` (backend/app/models/larc.py): has `source_flow` (in_stock|pharmacy_order|office_procedure), `device_type_id`, `linked_surgery_id` (exists, unused), `created_by`, `created_at`, patient identity fields, `status`. Created via `POST /larc/assignments` (`create_assignment`, routers/larc.py ~1216) with `AssignmentIn`.
- Allocate flow exists (`/larc/assignments/{id}/allocate-device`); inventory = `LarcDevice` rows with `status` (unassigned…), `device_type_id`, `location`. `LarcDeviceType.default_flow` is the per-type strategy.
- scheduled_date is set in: `block_schedule.book_slot` (~460, the shared booking helper), `date_picker` (~272), `self_schedule` (~231), candidate_import/smartsheet (import paths — do NOT hook these). User-facing schedule endpoints: coordinator schedule (surgery.py), patient_pick (patient_surgery.py), self_schedule.
- LARC dashboard buckets in Larc.jsx incl `op_needs_device`. Assignment dict built in larc.py.

---

## B1 — `requested_by_provider` on LarcAssignment
**Files:** `backend/app/models/larc.py`, `backend/app/database.py`.
Add `requested_by_provider = Column(String(200), nullable=True)` (the surgeon/provider who needs the device). Migration entry `("larc_assignments","requested_by_provider","VARCHAR(200)")`. Verify import. Commit `feat(larc): requested_by_provider on assignment (B1)`.

---

## B2 — Bridge service: create linked requests
**File:** create `backend/app/services/surgery/device_requests.py`, test.
`sync_surgery_device_requests(db, surgery, actor_email=None) -> dict` (soft-fail; never raise into the scheduling flow):
- For each name in `surgery.device_types or []` where `name.strip().lower() != "none"`:
  - Resolve `LarcDeviceType` by case-insensitive name, `is_active`. If no match → add to `unmatched` list, skip (log; don't fabricate).
  - **Idempotency:** skip if an `is_active` `LarcAssignment` already exists with `linked_surgery_id == surgery.id` AND `device_type_id == dt.id`.
  - **Auto-pick source_flow:** `in_stock = db.query(LarcDevice).filter(device_type_id==dt.id, status=="unassigned").count()`. If `in_stock > 0` → `"in_stock"`; elif `dt.default_flow == "office_procedure"` → `"office_procedure"`; else → `"pharmacy_order"`.
  - Create `LarcAssignment(chart_number=surgery.chart_number, patient_name=surgery.patient_name, patient_first_name=surgery.first_name, patient_last_name=surgery.last_name, patient_dob=surgery.dob, primary_insurance=surgery.primary_insurance, device_type_id=dt.id, source_flow=<picked>, linked_surgery_id=surgery.id, requested_by_provider=surgery.surgeon_primary, created_by=(actor_email or "system:surgery-schedule"), status="new", notes=f"Auto-created from scheduled surgery {surgery.surgery_number or surgery.id}.")`. Write a LarcAuditEvent if that's the pattern (grep how create_assignment audits) with actor + "created_from_surgery".
  - Collect created ids.
- Return `{"created": [...ids], "skipped_existing": n, "unmatched": [names]}`.
Test `backend/tests/test_surgery_device_requests.py` (db fixture): seed LarcDeviceTypes (one in_stock w/ an unassigned LarcDevice, one pharmacy_order w/ none in stock, one office_procedure); a Surgery with `device_types=["Liletta","Mirena","Benesta","None"]`; call sync → creates 3 assignments with source_flow in_stock/pharmacy_order/office_procedure respectively, linked_surgery_id set, requested_by_provider = surgeon; calling again creates 0 (idempotent); an unknown device name → unmatched. Commit `feat(surgery): bridge service creates linked LARC device requests, auto-picking flow from inventory (B2)`.

---

## B3 — Fire on scheduling
**Files:** `backend/app/routers/surgery.py` (coordinator schedule endpoint), `backend/app/routers/patient_surgery.py` (`patient_pick`), `backend/app/routers/.../self_schedule` endpoint.
After each user-facing scheduling action successfully sets `scheduled_date` and commits, call `sync_surgery_device_requests(db, surgery, actor_email)` (actor = current_user email, or "system:patient-portal" for patient self-schedule). Wrap in try/except (soft-fail). Do NOT hook the import/seed paths. Because the bridge is idempotent, reschedules won't duplicate. (If a shared helper like `book_slot` is the single choke point for ALL three, prefer hooking the endpoints rather than book_slot to avoid import/seed contexts.)
Test: hit the coordinator schedule path for a surgery with a device → a linked assignment now exists (extend the B2 test or a router test using the client fixture; mock/stub as needed for the schedule endpoint's prerequisites). Suite ≤ baseline. Commit `feat(surgery): create device requests when a surgery is scheduled (B3)`.

---

## B4 — Expose the linkage both ways
**File:** `backend/app/routers/surgery.py` (`_surgery_dict`), `backend/app/routers/larc.py` (assignment dict).
- `_surgery_dict`: add `device_requests`: list of `{id, device_type, source_flow, status, requested_by_provider}` for active LarcAssignments with `linked_surgery_id == s.id` (query LarcAssignment+LarcDeviceType). So the surgery detail can show them.
- larc assignment dict (list + detail): add `linked_surgery_id`, `requested_by_provider`, and `from_surgery` = `linked_surgery_id is not None`.
Test: GET surgery → `device_requests` reflects the created assignment; LARC list row for it has `from_surgery True` + provider. Commit `feat(surgery/larc): surface the surgery↔device-request link both ways (B4)`.

---

## F1 — Surgery detail: Device Requests section
**File:** `frontend/src/pages/SurgeryDetail.jsx`.
Add a "Device Requests" card (only when `s.device_requests?.length`) listing each: device type, path badge (in-stock → "Allocate from stock"; pharmacy_order → "Order / enrollment"; office_procedure → "Office procedure"), status, and a link to the LARC assignment (`/larc/assignments/{id}`). If `s.device_required` but no requests yet (e.g. not scheduled), show a muted "Requests are created when the surgery is scheduled." Build clean. Commit `feat(surgery-detail): Device Requests section linking to Device Tracking (F1)`.

---

## F2 — LARC: surgery-origin badge
**File:** `frontend/src/pages/Larc.jsx` (assignment list rows) and/or `LarcAssignment.jsx` (detail header).
For assignments with `from_surgery`, show a small "From surgery" badge + `requested_by_provider` + requested date (created_at). On the assignment detail header, show "Requested by {provider} · {date} · from surgery" with a link back to `/surgery/{linked_surgery_id}`. Build clean. Commit `feat(larc): surgery-origin badge + requester/date on assignments (F2)`.

---

## F3 — Headless smoke + deploy
1. build + vite preview + Playwright load `/surgery` + `/larc` → /login, 0 console errors.
2. Merge to main; deploy backend then frontend; smoke (health 200, `/api/surgery/config` 401, `/larc` 200, `/surgery` 200); push origin.
3. Authed check: schedule a surgery with a Mirena (none in stock) → a pharmacy_order request appears in Device Tracking tagged from-surgery with the provider; one with Liletta in stock → an in-stock (allocate) request; surgery detail shows both linked.

## Out of scope
- No auto-allocation/auto-send — the coordinator still confirms allocate/enrollment in Device Tracking (the request just lands in the right flow).
- No change to LARC's own create flows; this adds a surgery-originated creation path + linkage.
- Unmatched device names are logged/surfaced, not auto-created (no fabricated device types).
