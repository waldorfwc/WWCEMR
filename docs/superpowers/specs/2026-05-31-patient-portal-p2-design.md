# Patient Portal — P2: Payments + Scheduling

**Status:** Draft for review
**Author:** Claude Code, 2026-05-31
**Builds on:** P1 (auth + shell + dashboard) shipped earlier today

## Goal

Replace two of the dashboard "Coming soon" stubs with real screens that the patient can self-serve:

1. **Payments** — view balance, view history, pay the patient-responsibility balance via Stripe Checkout. Step-up SMS confirmation before charge.
2. **Schedule** — view available slots, pick a date/time. Gated on payment-or-no-balance.

Once P2 ships, the patient's blocking interactions with the coordinator drop dramatically: pay → pick a date → done. No phone calls, no email tag, no "can I take Wednesday" back-and-forth.

## Architecture

Both screens are FastAPI router additions mounted under the existing `/api/patient/portal/{sid}/...` prefix, gated by `require_portal_token`. Frontend is two more pages under `/portal/s/:sid/...`. No new tables.

```
┌─────────────────────────────────────────────────────────────────────┐
│  Patient Portal P2                                                  │
│                                                                     │
│  GET  /api/patient/portal/{sid}/payments                            │
│         returns: balance, history (existing SurgeryPayment rows)    │
│                                                                     │
│  POST /api/patient/portal/{sid}/payments/step-up                    │
│         sends fresh SMS code (5-min TTL), returns step_up_token     │
│                                                                     │
│  POST /api/patient/portal/{sid}/payments/checkout                   │
│         requires {step_up_token, code}, creates Stripe Checkout     │
│         session, returns checkout URL                               │
│                                                                     │
│  GET  /api/patient/portal/{sid}/slots                               │
│         wraps existing patient_surgery /slots logic                 │
│         (eligible block days + open slots), checks schedule gate    │
│                                                                     │
│  POST /api/patient/portal/{sid}/slots/{slot_id}/claim               │
│         books the slot, enforces schedule gate again, updates       │
│         scheduled_date + scheduled_start_time + fires existing      │
│         confirmation email + SMS hooks                              │
│                                                                     │
│  Frontend                                                           │
│    /portal/s/:sid/payments  — <PortalPayments /> replaces stub      │
│    /portal/s/:sid/schedule  — <PortalSchedule /> replaces stub      │
└─────────────────────────────────────────────────────────────────────┘
```

## Step-up SMS auth for payments

Long-TTL JWT (30 days post-op) + localStorage means the auth token is reusable for ~3 months. That's fine for read-only screens but unacceptable for "click button → my card gets charged." We re-prove ownership of the phone before charging.

```
[1] Patient on /portal/s/{sid}/payments clicks "Pay now"
[2] Frontend: POST /payments/step-up
      → backend: generate 6-digit code, hash to a new
                  PatientPortalAuthCode row (5-min TTL), SMS it,
                  return {step_up_token}
[3] Frontend shows a 6-digit input (same UX as /portal/verify)
[4] On submit: POST /payments/checkout {step_up_token, code}
      → backend: verify_code() — same lifecycle as portal sign-in
                  (3 wrong codes kills the challenge)
      → on success: create Stripe Checkout session, return URL
      → on failure: 401, patient can retry from step 2
[5] Frontend: window.location = checkout_url
[6] Stripe handles card entry; on success redirects to
    /portal/s/{sid}/payments?session_id=cs_...
[7] Frontend reads session_id, fires GET /payments to refresh
    history (the webhook from Phase H already updates the DB).
```

### Reuse vs. fork

We reuse the **same `PatientPortalAuthCode` table** and the **same `auth.issue_challenge()` / `auth.verify_code()` helpers** from P1. No new schema. The challenge_token's lifecycle is identical; what differs is the caller — it's just a different endpoint that issues and verifies it.

The implementation note: `issue_challenge` currently sends `"WWC: Your portal sign-in code is {code}."` — wrong copy for a payment confirmation. Two options:

- **A.** Add a `purpose` parameter to `issue_challenge(db, surgery, purpose="login")` that picks the SMS body. Quick, no schema change.
- **B.** Keep `issue_challenge` for sign-in, write `issue_payment_challenge` next to it with the same persistence + different copy.

Picking **A** — one function, one parameter, simpler.

## Payments screen

### Data shown

- **Balance card** — `patient_responsibility - sum(payments where status=paid).amount_paid`. Three states:
  - `balance > 0` → "You owe $X" + [Pay now] button
  - `balance == 0 AND patient_responsibility > 0` → "Paid in full ✓"
  - `patient_responsibility == 0` → "Nothing to pay" (insurance covers full amount)
- **History** — list of SurgeryPayment rows: timestamp, amount, status badge, optional Stripe receipt link.

### Endpoints

```python
GET /api/patient/portal/{sid}/payments
    → { balance, due, paid, history: [{id, amount, status, paid_at, receipt_url}, ...] }

POST /api/patient/portal/{sid}/payments/step-up
    → { step_up_token }
    side-effect: SMS sent

POST /api/patient/portal/{sid}/payments/checkout
    body: { step_up_token, code }
    → 200 { checkout_url } | 401 invalid code
    side-effect: Stripe Checkout session created, SurgeryPayment row added
```

### Components

```
frontend/src/pages/portal/Payments.jsx
  ├── <BalanceCard /> — primary card with state + CTA
  ├── <PayFlow />     — handles step-up SMS → code entry → redirect
  └── <PaymentHistory />
```

`PayFlow` is its own self-contained mini-state-machine: idle → sending code → entering code → redirecting. Reuses the 6-digit input pattern from `PortalVerify`.

## Scheduling screen

### Data shown

- **Schedule gate banner** — if gate is unmet, show "Pay your balance before booking a date" with a link back to Payments. Else hide.
- **Block day picker** — month view, eligible facility days highlighted. Re-uses the existing `SurgerySlot` / `BlockDay` data model already feeding the magic-link slot picker.
- **Slot list for the picked day** — open slots for that day, ordered by `start_time`.
- **Confirm** — patient clicks a slot → confirmation modal → POST claim → success card with the booked time + "Add to calendar (.ics)" button.

### Schedule gate logic

```python
def schedule_gate_passes(surgery: Surgery) -> tuple[bool, str | None]:
    """Return (allowed, reason_if_blocked). 'reason' is patient-facing."""
    pt_resp = float(surgery.patient_responsibility or 0)
    if pt_resp <= 0:
        return True, None
    paid = sum(float(p.amount_paid or 0)
                for p in (surgery.payments or [])
                if p.status == "paid")
    if paid >= pt_resp:
        return True, None
    # Configurable override — coordinator can let patient schedule even
    # when unpaid (e.g. payment plan in flight, or insurance under
    # appeal). New column added in P2.
    if surgery.schedule_gate_override:
        return True, None
    return False, ("Please make your payment before booking a surgery date. "
                   f"Outstanding balance: ${pt_resp - paid:.2f}")
```

The gate is enforced on **both** GET `/slots` (so the picker is hidden when blocked) and POST `/slots/{id}/claim` (so a stale tab can't book by replaying an old request).

### New schema

One bool on Surgery:
```python
schedule_gate_override = Column(Boolean, default=False, nullable=False)
```

The coordinator toggles this from the staff UI (already exists — `SurgeryDetail.jsx` admin section). A simple checkbox: "Allow patient to schedule without payment." Stamps `_at` + `_by` for audit.

### Endpoints

```python
GET /api/patient/portal/{sid}/slots
    → { gate: {allowed, reason}, block_days: [...], open_slots: [...] }

POST /api/patient/portal/{sid}/slots/{slot_id}/claim
    → 200 { surgery: <updated surgery summary> }
       side-effect: scheduled_date set, scheduled_start_time set,
                    google_calendar_sync, surgery_confirmation email + SMS fired,
                    Surgery.last_rescheduled_at/_by stamped (since
                    patient self-service counts as a reschedule path)
    → 409 if gate not passing (was unpaid; tab was stale)
    → 409 if slot already booked
    → 422 if slot belongs to a block day the surgery isn't eligible for
```

Most of this logic already exists in `app/routers/patient_surgery.py`'s `/select-slot` endpoint (magic-link flow). The portal endpoints are thin wrappers that swap the auth dependency from `require_patient_token` (magic-link audience) to `require_portal_token` (portal audience), and add the gate check.

**Implementation note:** rather than duplicate the slot-claim logic, extract it into a shared helper `app/services/surgery_self_schedule.py` with `claim_slot_for_patient(db, surgery, slot_id, sent_by) -> Surgery`. Both routers call it. Saves drift.

### Components

```
frontend/src/pages/portal/Schedule.jsx
  ├── <GateBanner /> — shown when gate is unmet
  ├── <BlockDayPicker /> — month grid (reused or shared with magic-link slot UI)
  ├── <SlotList />
  └── <ConfirmModal />
```

Reuse the existing magic-link slot picker components if they exist. If not, build new ones; later phases can consolidate.

## Out of scope for P2 (defer)

- **Payment plans (installments).** Promised in P2b — needs a model for `PaymentPlan(surgery_id, installments[], schedule)`, recurring Stripe invoice setup, late-payment recovery, plan modification, and SMS dunning. Not a P2 task.
- **Refund initiation by patient.** Coordinator-only for now.
- **Insurance re-verification** if benefits change mid-flow.
- **Schedule "I need to change this" / reschedule.** Patients can pick a slot once; coordinator reschedules. P2b adds "Request reschedule" affordance.
- **Pre-op questionnaire** and the rest of the doc-vault features. Those are P3-P5.
- **"Sign out everywhere"** server-side JTI denylist. Token revocation matters when accounts are compromised. For P2 we mitigate via step-up at every payment; revocation can wait until we have a real abuse signal.
- **CSP / Trusted Types audit.** Important pre-launch hardening, but the portal currently has no user-generated HTML rendering and no third-party scripts loaded — XSS surface is minimal. Add to a "before wide patient rollout" punch list.

## Open questions

1. **Block day picker UI** — does the existing magic-link slot picker have reusable components we can lift, or do we build fresh? **I'll check the codebase before writing the implementation plan.**
2. **Stripe Checkout success redirect URL** — should it be `/portal/s/{sid}/payments?session_id=...` (so the patient lands back in the portal) or the existing public path? Going with portal so the patient sees an updated history immediately, **resolved.**
3. **Schedule confirmation when patient picks a hospital slot** — current magic-link flow may have different "hospital vs. office" rules. Need to read `select-slot` first to understand. **Will be answered in the plan after I read the existing code.**

### Resolved

- **Payment plans** → deferred to P2b.
- **Step-up auth** → reuse `PatientPortalAuthCode` with a `purpose` parameter on `issue_challenge`.
- **Schedule gate override** → new `Surgery.schedule_gate_override` boolean, coordinator-controlled.
- **Shared slot-claim logic** → extracted to `app/services/surgery_self_schedule.py`, called by both magic-link and portal routers.

## Risks

- **Stripe webhook timing.** When the patient finishes Checkout, Stripe redirects them back to `/portal/s/{sid}/payments?session_id=...` *before* the webhook updates the database. The history list may show the just-completed payment as "pending" or be missing it entirely for a few seconds. Mitigation: client polls the history endpoint every 2 seconds for ~10 seconds after redirect when `session_id` is in the URL.
- **Step-up race.** Patient triggers step-up, gets the code, but takes >5 minutes to enter it. Code expires, patient retries. Acceptable UX; just need the error message to be clear ("Code expired, send a new one"). Reuses the existing `verify_code` "fail returns None" path.
- **Schedule gate stale tab.** Patient pays, has the schedule tab open from earlier, picks a slot — should work (gate now passes). Patient hasn't paid, opens schedule tab, tries to claim — backend re-checks gate, returns 409. Both paths tested.
- **Last_rescheduled counter inflation.** Currently `last_rescheduled_at` is bumped every time a slot is claimed via patient self-service. If we bump it on the *first* slot pick too, the counter is misleading ("rescheduled 1 time" when patient just booked once). Plan must distinguish "first claim" vs. "reschedule" — bump only when `scheduled_date is not None` before the new claim.

## Tech stack

Identical to P1: FastAPI + SQLAlchemy + Stripe (existing). React + Vite + Tailwind. Reusing the seeded `surgery_confirmation` email + `sms_surgery_confirmation` SMS templates for the confirmation hooks (already wired via the existing `_send_surgery_confirmation_email` helper in patient_surgery.py).
