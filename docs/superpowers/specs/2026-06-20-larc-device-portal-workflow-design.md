# LARC Device Tracking — Workflow Changes + Patient Portal — Design

**Date:** 2026-06-20
**Status:** Approved (design); ready for implementation plan
**Area:** Device Tracking (LARC) — staff workflow + new patient-facing portal
**Build mode:** One combined project (per decision).

## Goal

Tighten the LARC device-tracking workflow around device ownership and a simplified two-track status model, automate device allocation and per-step patient notifications, and add a patient-facing portal where patients pay their responsibility (Stripe), view/sign enrollment forms (BoldSign), and track their request.

## Decisions (approved)

1. **Single combined project** (not phased).
2. **Insurance-card upload** is shown only for the **pharmacy** fulfillment path; hidden for in-stock / office-procedure (practice-owned).
3. **Patient-owned devices:** keep the internal "Device inserted" record but **drop the "Insertion billed (claim #)" step** entirely (no claim/billing for patient-owned).
4. **Notifications:** email always; **SMS only after the patient opts in at portal first login**.
5. **[confirmed default]** Auto-allocation with no matching stock → raises a staff dashboard alert ("Ready to allocate — no stock"), never silently fails.
6. **[confirmed default]** MA checkout lives on the **LARC dashboard** main page **and** My Checklist; removed from the assignment detail page.
7. **[confirmed default]** Portal login resolves to the patient's **most-recent active request**; if a patient has more than one active request, the dashboard lists them and lets them switch.

## Background (existing code this builds on)

- **Milestones:** `backend/app/services/larc/workflow.py` — `IN_STOCK_MILESTONES`, `PHARMACY_ORDER_MILESTONES`, `OFFICE_PROCEDURE_MILESTONES`, `spawn_milestones()`, `assignment_buckets()`.
- **Ownership:** `LarcDevice.ownership` ∈ `patient_owned | wwc_owned | wwc_claimed` (`backend/app/models/larc.py`). Billing gated on ownership in `mark_billed()` / `close_out()` (`backend/app/routers/larc.py` ~2709-2776).
- **Allocation:** `allocate_device()` (~2033-2117), gates `benefits_verified_at` + `patient_paid_at`; `record_payment()` (~2005-2026); `verify_benefits()` (~1506-1571); shared `pick_source_flow()` (`backend/app/services/larc/source_flow.py`).
- **Checkout:** detail `CheckoutPlaceholderBody` (`LarcAssignment.jsx` ~1045-1112) → `/checkout-request`; My Checklist `LarcCheckoutCard` (`MyChecklist.jsx` ~642-689) → `/checkout-direct`; manager queue `LarcCheckouts.jsx` → `/checkouts/pending|decide`.
- **Insurance card:** `InsuranceCardCard` (`LarcAssignment.jsx` ~1455-1561) → `/assignments/{id}/insurance-card`; fields `insurance_card_key/filename/content_type`.
- **Notifications:** `send_patient_email(db, kind=…)` / `send_patient_sms(db, kind=…)` with `EmailTemplate`/`SmsTemplate` (by `kind`) + `PatientEmail`/`PatientSms` audit tables + `{{var}}` rendering (`backend/app/services/patient_email.py`, `patient_sms.py`). SMS consent currently only on `Surgery` (`sms_consent`). LARC `/notify` today is a milestone marker only — **no real send**.
- **Portals:** surgery (`patient_portal.py`, `patient_portal_auth.py`, `frontend/src/pages/portal/*`) and pellet (`patient_pellet.py`, `pellet/portal_auth.py`, `frontend/src/pages/pellet-portal/*`). Both: 2-step SMS-OTP, JWT with `*_token_version` revocation, `PortalShell` layout, `JourneyTimeline` tracker.
- **Stripe:** `create_checkout_session(kind=…)` (`stripe_payments.py`) + webhook `POST /api/stripe/webhook` (handles surgery + pellet). `SurgeryPayment`/`PelletPayment` models. Live webhook subscribes `checkout.session.completed`, `charge.refunded`, `payment_intent.payment_failed`, `checkout.session.expired`, `invoice.paid`, `customer.subscription.*`.
- **BoldSign:** LARC 3-signer enrollment (Receptionist → Patient → Provider) `enrollment_sender.py`; embedded sign link `get_embedded_sign_link(envelope_id, email)` (`boldsign_envelopes.py`); webhook `POST /api/boldsign/webhook`.

---

## Architecture

```
                         ┌─────────────────────────────────────┐
   Staff (existing UI)   │  LarcAssignment + LarcMilestone      │   Patient portal (NEW)
   create / benefits /   │  + sms_consent, portal_token_version │   /larc-portal/*
   record-payment /      │                                     │   status · pay · sign
   checkout (dashboard)  └───────────────┬─────────────────────┘
                                         │
        ┌────────────────────────────────┼─────────────────────────────┐
        │                                │                              │
  patient_track(a)                try_auto_allocate(a)          notify_larc_step(a, step)
  (projector: internal      (benefits+paid → claim device,      (email always; SMS if
   milestones → 5 patient    else 'no stock' alert)              a.sms_consent; per-step
   steps, per track)                                             templates; idempotent)
        │                                │                              │
   portal status            Stripe webhook (larc branch) ──────────────┘
   tracker                  marks LarcPayment paid → sets paid_at → try_auto_allocate + notify
```

New modules keep responsibilities isolated: a **track projector**, an **auto-allocation** service, a **notification** helper, a **portal auth** module, and a **LarcPayment** model + Stripe branch — each small and independently testable.

---

## Components

### A. Milestone tracks & projector
- **Edit catalogs** (`workflow.py`): remove `appt_scheduled` from `IN_STOCK_MILESTONES` and `PHARMACY_ORDER_MILESTONES`. The `billed` milestone stays in the catalog but is treated as **not-applicable for patient-owned** devices (see Billing). Update `assignment_buckets()` to drop the `appt_scheduled` bucket and bridge `device_received`/allocation → checkout directly.
- **New projector** `patient_track(assignment) -> {track, steps:[{key,label,status}]}` in a new `backend/app/services/larc/patient_track.py`:
  - **pharmacy:** `request_received` (created) → `enrollment_completed` (milestone `enrollment_signed`) → `enrollment_faxed` (`request_faxed`) → `device_received` (`device_received`) → `patient_notified` (`patient_notified`).
  - **practice-owned (in_stock):** `request_received` → `responsibility_determined` (`benefits_verified`) → `responsibility_satisfied` (`patient_paid_at`) → `device_allocated` (device bound / allocate step) → `patient_notified`.
  - Each step status ∈ `done | current | upcoming`. This single projection feeds both the portal tracker and which step a notification announces.

### B. Ownership-conditional rules
- **Insurance upload:** gate on `source_flow` — render `InsuranceCardCard` only when `source_flow == 'pharmacy_order'`. Hide for `in_stock`/`office_procedure`. (Frontend conditional in `LarcAssignment.jsx`; backend upload endpoint left intact but the UI no longer offers it for practice-owned.)
- **Billing:** for a patient-owned bound device, the `billed` milestone is marked `not_applicable` by `spawn_milestones`/projector and the detail UI shows **no** claim entry and **no** "billed" step — `device_inserted` is the last tracked clinical step; an optional no-claim **close-out** remains to mark the record complete (status → a terminal state) but is not labeled "billed." Practice-owned billing (claim #) is unchanged.

### C. Auto-allocation
- New `backend/app/services/larc/allocation.py::try_auto_allocate(db, assignment) -> dict` extracting the device-claim logic from `allocate_device()`:
  - Pre: `source_flow == 'in_stock'`, no device yet, `benefits_verified_at` set, `patient_paid_at` set.
  - Atomically claim an `unassigned` device of the matching type (same conditional UPDATE as today). On success: bind device, mark the allocation step, audit.
  - **No matching stock:** set a flag/bucket `needs_allocation_no_stock` surfaced as a dashboard alert; return `{allocated: False, reason: 'no_stock'}`. Does not raise to the caller's request.
- Call sites: `record_payment()` (staff) and the Stripe webhook LARC branch — both invoke `try_auto_allocate` after `patient_paid_at` is set (and benefits verified). The manual `allocate_device` endpoint stays for staff override / the no-stock case.

### D. Notifications
- New `backend/app/services/larc/notifications.py::notify_larc_step(db, assignment, step, *, sent_by='system')`:
  - Always `send_patient_email(kind=<step kind>, to_email=a.patient_email, context=…)`.
  - If `a.sms_consent` and `a.patient_cell`: also `send_patient_sms(kind=<step kind>, to_phone=a.patient_cell, context=…, consent_override=True)` (consent already checked here; LARC has no Surgery row).
  - Idempotent per `(assignment_id, step)` via the audit tables' context (mirrors reminders).
- **Steps that notify the patient** (fired when the milestone completes):
  - pharmacy: `larc_enrollment_ready` (enrollment sent → "sign your form", links to portal), `larc_enrollment_faxed`, `larc_device_received`, `larc_ready` (= "Patient Notified": device ready / come in).
  - practice-owned: `larc_responsibility_due` (responsibility determined → portal pay link), `larc_payment_receipt` (satisfied), `larc_device_allocated`, `larc_ready`.
- New `EmailTemplate`/`SmsTemplate` rows (seed script) for each kind; `{{patient_name}}`, `{{practice_phone}}`, `{{portal_url}}`, `{{amount}}` context.
- New `LarcAssignment.sms_consent` (Boolean default False) + `sms_consented_at`/`sms_consented_by`, set at portal first login.

### E. MA checkout relocation
- **Remove** `CheckoutPlaceholderBody` from `LarcAssignment.jsx` (and its milestone-case wiring).
- **Keep** the My Checklist `LarcCheckoutCard` (`/checkout-direct`).
- **Add** a "Devices ready to check out" card on the **LARC dashboard** (`Larc.jsx`) reading `GET /larc/checkouts/ready` and posting `/checkout-direct` — same component pattern as My Checklist.

### F. Patient portal (new)
- **Routes (public):** `/larc-portal/login`, `/larc-portal/verify`, `/larc-portal/home/*` (status, payments, enrollment, documents) — registered in `App.jsx` public block; `frontend/src/pages/larc-portal/*` + `frontend/src/lib/larc-portal-api.js`.
- **Auth:** new `backend/app/services/larc/portal_auth.py` cloning `pellet/portal_auth.py`: DOB + last-4 phone → 6-digit SMS code → JWT (`scope:"larc_portal"`, `sub`=assignment_id, `lpv`=portal_token_version). `require_larc_portal_token` dependency. Login matches active `LarcAssignment` by DOB + last-4; resolves to most-recent active; dashboard can list/switch if >1.
- **Backend router:** `backend/app/routers/patient_larc.py` mounted `/api/larc-portal`:
  - `POST /login`, `POST /verify` (sets `sms_consent` on the matched assignment at first successful verify).
  - `GET /dashboard` → `patient_track(a)` + payment summary + enrollment status + documents.
  - `GET /payments`, `POST /payments/checkout` (Stripe).
  - `GET /enrollment`, `GET /enrollment/sign-link/{envelope_id}` (embedded BoldSign), `GET /enrollment/signed-pdf/{envelope_id}`.
  - `GET /documents` (enrollment PDF, receipts).
- **Frontend:** `LarcPortalShell` (mirror `PelletPortalShell`); **Status** page = two-track `JourneyTimeline`; **Payments** = balance + Stripe redirect; **Enrollment** = sign-now (pharmacy only); **Documents** = receipts + signed forms.

### G. Stripe integration
- New `LarcPayment` model (mirror `PelletPayment`): `assignment_id`, `kind="larc_patient_responsibility"`, `status` (requested/paid/refunded/failed/expired), `amount_requested/amount_paid`, `stripe_checkout_session_id`, `stripe_payment_intent_id`, `checkout_url`, timestamps.
- `create_checkout_session` extended with a LARC kind (or a thin `create_larc_checkout(db, assignment, amount)` wrapper) persisting a `LarcPayment`.
- **Webhook:** add a LARC branch to `_handle_session_completed()` (match `LarcPayment` by `stripe_checkout_session_id`) → set paid, set `assignment.patient_paid_at/by/amount`, fire `notify_larc_step(..., 'responsibility_satisfied')`, then `try_auto_allocate`. Reuses the existing `checkout.session.completed` subscription — no new Stripe events.

---

## Data model changes (`backend/app/models/larc.py` + migrations)

- `LarcAssignment`: `sms_consent` (Boolean, default False), `sms_consented_at` (DateTime), `sms_consented_by` (String 200), `portal_token_version` (Integer, default 0), `needs_allocation_no_stock` (Boolean default False — or derive as a bucket; prefer a flag for a cheap dashboard query).
- New table `larc_payments` (LarcPayment, per above).
- Lightweight migrations in `database.py` `needed` list for the new `larc_assignments` columns; `larc_payments` created via `Base.metadata`.

---

## Data flow (practice-owned, end to end)

1. Staff Start LARC Process (in-stock) → assignment `new`; track step 1 done.
2. Staff verify benefits → `responsibility_determined`; `notify_larc_step('responsibility_due')` emails the patient a portal pay link.
3. Patient logs into portal (opts into SMS), pays via Stripe.
4. Stripe webhook → `LarcPayment` paid → `patient_paid_at` set → `notify_larc_step('responsibility_satisfied')` (receipt) → `try_auto_allocate`.
5. Auto-allocate binds an in-stock device → `device_allocated` → `notify_larc_step('device_allocated')`. (No stock → staff alert.)
6. Staff mark patient notified → `larc_ready` → patient sees "ready"; MA checks the device out from the dashboard / My Checklist; insertion + (practice-owned) billing proceed as today.

Pharmacy flow mirrors this with enrollment-sign + fax + receive steps and no billing.

## Error handling & edge cases
- **Zero patient responsibility (practice-owned):** if `patient_responsibility` is 0/None once benefits are verified, mark `responsibility_satisfied` automatically (set `patient_paid_at`, no pay link, no Stripe), then `try_auto_allocate`. The portal Payments page shows a balance only when responsibility > 0.
- **No stock at auto-allocate:** staff alert bucket; manual `allocate_device` still available.
- **Multiple active requests per patient:** dashboard lists them; token scoped per assignment.
- **SMS not opted in / no cell:** email-only; never block.
- **Stripe webhook idempotency:** existing `ProcessedStripeEvent` dedup + row-locked paid-set.
- **Patient-owned billing:** `billed` milestone not-applicable; no claim UI.
- **Notification idempotency:** per (assignment, step) via audit-table context.
- **Portal token revocation:** bump `portal_token_version` (e.g., on request close/cancel).

## Testing
- Backend (pytest, `client`/`db`): track projector for both tracks & all step statuses; catalog edits (no `appt_scheduled`; patient-owned billed not-applicable); `try_auto_allocate` (success + no-stock alert); ownership rules (insurance gating logic, patient-owned billing); `notify_larc_step` (email always, SMS gated by consent, idempotent); portal auth (login/verify/JWT/revocation, multi-request resolution); Stripe webhook LARC branch (paid → paid_at → allocate + notify); checkout-direct from dashboard.
- Frontend: builds; portal pages render via the headless-preview pattern; status tracker shows both tracks; detail page no longer shows checkout/insurance-for-practice-owned.

## Out of scope
- Recurring/subscription payments (LARC is one-time responsibility).
- Refund UI (webhook refund handling can reuse the existing pattern later).
- Changing the BoldSign enrollment template content.

## Affected/!new files (reference)
- Backend: `models/larc.py`, `models/larc_payment.py` (new), `database.py`, `services/larc/workflow.py`, `services/larc/patient_track.py` (new), `services/larc/allocation.py` (new), `services/larc/notifications.py` (new), `services/larc/portal_auth.py` (new), `routers/larc.py`, `routers/patient_larc.py` (new), `routers/stripe_payments.py`, `services/stripe_payments.py`, seed script for LARC email/SMS templates.
- Frontend: `pages/Larc.jsx` (checkout card), `pages/LarcAssignment.jsx` (remove checkout, gate insurance, billing rules), `pages/larc-portal/*` (new), `lib/larc-portal-api.js` (new), `App.jsx` (routes).
