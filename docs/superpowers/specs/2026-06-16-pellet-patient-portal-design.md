# Pellet Patient Portal — Design

**Status:** Approved design (2026-06-16). Decoupled patient-facing portal for the pellet
program, mirroring the existing surgery patient portal. Built in three independently-usable
phases.

**Goal:** Let pellet patients self-serve online — complete their pre-insertion requirements
(mammogram, labs, consent), pay (single / package / subscription), and then schedule an
insertion appointment — but only once every requirement and payment gate is satisfied.

---

## 1. Approach

A **decoupled pellet portal** that mirrors the surgery portal's proven patterns rather than
sharing a portal core (which would destabilize the working surgery portal) or bolting patient
features onto staff pages (which gives no real patient experience).

Reused building blocks (same patterns, pellet-specific instances):
- Patient auth: DOB + last-4 → SMS code → JWT portal token (surgery `patient_portal.py` model).
- Consent: BoldSign envelopes (see [[project_signatures_boldsign]]).
- Payments: Stripe (one-time today; recurring added for subscriptions).
- Uploads: blob storage adapter (`save_blob`/`serve_blob`, `STORAGE_BACKEND=gcs`).
- Staff feed: surgery To-Do / Activity feed pattern (`{items:[...]}` response shape).

## 2. Architecture

- **Backend**
  - `app/routers/patient_pellet.py` — patient-facing endpoints, gated by a pellet-portal JWT.
  - `app/routers/pellet.py` (extend) — staff config, verification check-offs, notification feed,
    scheduling-availability admin.
  - `app/services/pellet/` — `settings.py` (extend config registry), new `scheduling.py`
    (availability → slot materialization), `payments.py` (price/discount/credit math),
    `gating.py` (the "ready to schedule" decision).
- **Frontend**
  - `src/pages/PatientPellet.jsx` — patient portal shell + checklist dashboard (mirrors
    `PatientSurgery.jsx`), with sub-views for mammo upload, labs self-report, consent, buy/subscribe,
    and slot booking.
  - Staff side: extend `Pellets.jsx` / `PelletSettings.jsx`; add a pellet To-Do/Activity panel and
    an availability editor.
- **Auth:** pellet-portal token carries `pellet_patient_id` + a `ptv` (portal-token-version) claim;
  a `portal_token_version` column on `PelletPatient` allows revoking tokens after sensitive changes
  (consent reset, etc.), exactly like surgery.

## 3. Data Model Additions

All new tables/columns created via the lightweight-migration `needed` list / Base metadata.

- `PelletPatient` (extend): `portal_token_version int default 0`.
- `PelletConsent`: `id, pellet_patient_id, boldsign_envelope_id, status, signed_at,
  expires_at (= signed_at + 365d), template_id, created_at`. A patient is "consented" iff a row
  exists with `status=signed` and `expires_at > now`.
- `PelletPortalAuthAttempt` (or reuse the surgery auth-attempt pattern): challenge tokens / SMS
  codes for login throttling.
- `PelletPayment`: `id, pellet_patient_id, kind (single|package|subscription_invoice),
  stripe_payment_intent_id / stripe_invoice_id, amount, insertions_purchased, status, paid_at,
  created_at`. (Distinct from surgery `SurgeryPayment`.)
- `PelletInsertionCredit`: running ledger — `id, pellet_patient_id, delta (+purchased / -consumed),
  source (package|subscription|single|adjustment), reason, balance_after, created_at, created_by`.
  Current balance = sum(delta). "Insertion balance" the patient sees = credits from package/single;
  subscription accrues a separate **money** credit (below).
- `PelletSubscription`: `id, pellet_patient_id, stripe_subscription_id, monthly_amount,
  accrued_credit (money), status (active|canceled|past_due), started_at, canceled_at`.
  Monthly `invoice.paid` → `accrued_credit += monthly_amount`. Scheduling deducts the insertion
  price from `accrued_credit` on completion.
- `PelletAvailabilityTemplate`: `id, location_id, provider_name, kind (adhoc|daily|weekday|weekly|
  monthly), rule (JSON: weekday list / day-of-month / date), start_time, slot_minutes,
  active_from, active_to, created_by`. Materialized forward into slots (like surgery block days).
- `PelletSlot`: `id, template_id (nullable for adhoc), location_id, provider_name, start_at,
  end_at, status (open|booked|blocked|canceled), pellet_visit_id (nullable), capacity=1`.
- `PelletPatientAction` (notification feed): `id, pellet_patient_id, kind (mammo_uploaded|
  labs_self_reported|payment_made|consent_signed|booked), summary, detail (JSON), created_at,
  handled_at, handled_by`. Drives the staff feed + check-off.
- `PelletVisit` (existing): link `slot_id`; ensure it carries scheduled date/time + completion.

## 4. Phase 1 — Requirements + Portal (ships first)

### Patient
- **Login:** DOB + last-4 → SMS code → JWT (mirror surgery). Portal home = a **requirement
  checklist** card: Mammogram · Labs · Consent done/pending; Payment + Scheduling shown as
  locked/"coming up" until later phases.
- **Mammogram:** upload a file → `PelletPatientMammo` (exists) with status *pending verification*;
  shows "submitted, awaiting staff review."
- **Labs:** self-report completion — attestation checkbox + optional draw date/values — sets a
  pending labs record; status *pending verification*.
- **Consent:** sign a BoldSign insertion-consent envelope in-portal; on signed webhook a
  `PelletConsent` row is written with `expires_at = signed_at + 365d`. If a still-valid consent
  exists, the step shows done and is not re-requested.

### Staff
- **Patient-action feed** (new pellet To-Do/Activity, `{items:[...]}` shape): one row per patient
  action — *mammo uploaded*, *labs self-reported*, *payment made*, *consent signed*, *booked* —
  each with one-click **verify/check-off**. Verifying a mammo/labs action sets the existing
  `mammo_verified` / `labs_verified` flags (+ `_verified_by/_at`). Unread badge like surgery.
- **Config (`PelletSettings`):** per-requirement *required?* toggles; reuse `labs_valid_days` (14)
  and `mammo_valid_days` (365); consent template selection + 1-yr validity window.

### Gating (this phase)
`requirements_met = mammo_verified (within mammo_valid_days) AND labs_verified (within
labs_valid_days) AND valid consent`. Surfaced read-only now; enforced for scheduling in Phase 3.

## 5. Phase 2 — Payments

### Config (`PelletSettings`)
- `insertion_price` (default 400.00).
- `package_discount_tiers`: list of `{count, percent_off}` (default `[{2,5},{3,10},{4,15}]`),
  fully editable.
- `subscription_monthly_amount` (configurable; nullable disables subscriptions).
- Per-option *enabled?* toggles (single / package / subscription).

### Patient flows (Stripe)
- **Single:** one-time Stripe Checkout for `insertion_price`. On success → `PelletInsertionCredit
  +1 (source=single)`.
- **Package:** patient picks a count; price = `count × insertion_price × (1 − tier%)`; one-time
  Checkout. On success → `PelletInsertionCredit +count (source=package)`.
- **Subscription:** Stripe recurring subscription at `subscription_monthly_amount`. Each
  `invoice.paid` → `PelletSubscription.accrued_credit += monthly_amount` + a `PelletPayment`
  (kind=subscription_invoice) + a feed action. Patient may **bank multiple** insertions of credit.

### Draw-down (consistent rule)
Define **available insertions** = `insertion_credit_balance` (package/single credits) `+
floor(subscription.accrued_credit / insertion_price)`, and **open bookings** = count of this
patient's booked-but-not-yet-completed insertions. **Payment standing** for a *new* booking =
`available_insertions > open_bookings` — i.e. a patient may only hold as many open appointments as
their credit covers (prevents booking several insertions on one credit). The actual **draw-down**
happens **when staff mark the insertion completed**: package/single → credit −1; subscription →
`accrued_credit − insertion_price`. So booking checks coverage but does not deduct; completion
deducts. Canceling an open booking simply frees the coverage.

### Webhook
Extend the existing Stripe webhook (see [[project_stripe_webhook_broken]]) to also handle
`invoice.paid`, `customer.subscription.created/updated/deleted`. Idempotent via
`ProcessedStripeEvent` (exists).

## 6. Phase 3 — Availability + Scheduling

### Availability (staff)
- Editor to define availability per **location**: ad-hoc specific dates, or recurring
  daily / specific weekday / weekly / monthly, each with a start time + `slot_minutes`.
- A forward-materialization job turns templates into `PelletSlot` rows (modeled on the surgery
  block-day materialization), horizon-bounded by a config `schedule_horizon_days`.
- Each slot = location + provider + time, **capacity 1**.

### Booking (patient)
- Flow: **pick a location → see open slots (each labeled with its provider) → book**.
- The booking button is enabled only when **all gates pass**:
  `requirements_met AND payment_standing_ok AND valid_consent`. Otherwise the portal shows exactly
  which gate is outstanding with a link to resolve it.
- Booking sets `PelletSlot.status=booked`, links a `PelletVisit` (scheduled date/time), emits a
  `booked` feed action.
- **Reschedule / cancel:** releases the old slot; cancel within a configurable window may be
  restricted (reuse the surgery cancellation-config pattern if desired — out of scope unless asked).
- **Completion:** staff mark the visit completed → triggers the Phase-2 credit draw-down and sets
  `mammo`/`labs` recency expectations for the next cycle.

## 7. Configuration Summary (all configurable per request)

`insertion_price`, `package_discount_tiers`, `subscription_monthly_amount`, option enable toggles,
per-requirement required toggles, `labs_valid_days`, `mammo_valid_days`, consent template + validity,
`slot_minutes`, `schedule_horizon_days`. All live in the pellet config registry
(`PELLET_SETTINGS_DEFAULTS` + `cfg()`), edited under Pellet Settings.

## 8. Testing

- Backend TDD per phase: auth/token-version, requirement submission + staff verify, consent
  validity window, payment math (package discount tiers, credit ledger), subscription credit accrual
  + draw-down, gating decision truth table, availability materialization, slot booking + the
  all-gates enforcement (booking blocked when any gate fails), reschedule/cancel.
- Authenticated walk-throughs (test client + headless portal render) per phase, following the
  session's verification pattern (see [[feedback_headless_smoke_auth_gap]]).
- Suite kept at baseline.

## 9. Out of Scope / Notes

- No sharing/refactor of the surgery portal; pellets are isolated.
- Klara is not integrated; patient SMS uses the real Twilio path only (see
  [[project_klara_drafts_not_sends]]).
- Money sanity ceiling applies to all money inputs (see [[feedback_money_sanity_ceiling]]).
- Dates render MM/DD/YYYY; titles Title Case.
- Subscription cancellation/refund policy beyond "stop charges, keep accrued credit" is out of scope
  for v1.

## 10. Phasing

1. **Phase 1 — Requirements + Portal:** auth, checklist dashboard, mammo upload, labs self-report,
   consent (1-yr), staff patient-action feed + check-off, config toggles. *Usable standalone.*
2. **Phase 2 — Payments:** config (price/tiers/monthly), single/package/subscription via Stripe,
   credit ledger + draw-down rule, webhook events.
3. **Phase 3 — Availability + Scheduling:** availability templates + slot materialization, location→slot
   booking gated on all requirements + payment + consent, reschedule/cancel, completion → draw-down.
