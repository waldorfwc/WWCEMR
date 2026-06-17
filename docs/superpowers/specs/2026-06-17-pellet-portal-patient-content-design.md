# Pellet Portal — Patient Content + Left Nav Design

**Status:** Approved 2026-06-17. Enrich the pellet patient portal: show the patient's identity
(Name + MRN), add a left-side navigation, and add Appointments (history), Receipts, and a
staff-editable Rules & Info page.

## Goal
The portal currently shows only a requirement checklist. Add patient-facing content so a pellet
patient can see who they are, their appointment/insertion history (with dosage), their payment
receipts, and the practice's rules/refund policy — navigated via a left sidebar.

## Decisions (from brainstorming)
- **Receipts:** list paid payments + a per-payment link to the **Stripe-hosted receipt** (fetched
  on demand). No PDF generation.
- **History:** ONE "Appointments" page listing all visits (upcoming + past) with date, location,
  provider, dosage, status. Past insertions are the completed rows.
- **Rules & Info:** the whole page is ONE staff-editable markdown block (`portal_info_text`); staff
  maintain all wording including the day numbers. The enforced gating still uses live config values.

## Architecture

### Backend — patient endpoints (`backend/app/routers/patient_pellet.py`, prefix `/pellet-portal`, behind `require_pellet_token`; all GET so staff preview can view)
1. `GET /appointments` → the patient's `PelletVisit`s newest-first:
   `[{id, scheduled_date, location, provider, status, visit_kind, inserted_at,
      doses: [{label, quantity}]}]`. Dosage from `PelletVisitDose` joined to `PelletDoseType`
   (label) for that visit. Includes both upcoming (scheduled, not yet inserted) and past.
2. `GET /receipts` → paid `PelletPayment`s newest-first:
   `[{id, kind, kind_label, amount, paid_at, status, has_receipt}]`. `has_receipt` true when the
   payment has a Stripe id we can resolve a receipt from.
3. `GET /receipts/{payment_id}/receipt-url` → resolves the Stripe-hosted receipt URL on demand and
   returns `{url}` (302 or JSON). For single/package: retrieve the PaymentIntent
   (`expand=["latest_charge"]`) → `latest_charge.receipt_url`. For `subscription_invoice`: retrieve
   the Invoice → `hosted_invoice_url`. Best-effort: 404 `{detail:"receipt unavailable"}` if Stripe
   isn't configured or no URL exists. Only the patient's own payment (scope check).
4. `GET /info` → `{info_text: cfg(db, "portal_info_text")}` (the Rules & Info markdown block).
5. Extend `GET /dashboard` patient block already returns `{patient_name, chart_number}` — reuse it
   for the sidebar identity (no new endpoint).

### Backend — config
- `PELLET_SETTINGS_DEFAULTS`: add `"portal_info_text"` (string) with starter copy:
  mammogram within 1 year; labs within 14 days; pellets must be prepaid before scheduling;
  a refund-policy paragraph (placeholder language staff replace). Add to `PelletConfigPayload`
  (`Optional[str]`).

### Frontend — portal (`frontend/src/pages/pellet-portal/`)
- `PelletPortalShell.jsx`: restructure to a left sidebar (patient Name + MRN at top, nav links:
  Checklist, Appointments, Payments, Schedule, Receipts, Rules & Info) + main `<Outlet/>`. Keep the
  existing header (logo, Sign Out) and the `staff_token` capture. Responsive: sidebar collapses /
  stacks on narrow screens (match existing portal styling). Pull identity from a `useQuery` on
  `/pellet-portal/dashboard` (or a light shared hook).
- New pages:
  - `PelletAppointments.jsx` — `GET /appointments`; table/cards: date (MM/DD/YYYY), location,
    provider, dosage (e.g. "Estradiol 12.5mg ×2"), status chip. Empty state.
  - `PelletReceipts.jsx` — `GET /receipts`; rows: date, amount ($), type, status; "View Receipt"
    button → `GET /receipts/{id}/receipt-url` → `window.open(url)` (or show "unavailable").
  - `PelletInfo.jsx` — `GET /info`; render `info_text` as markdown (use an existing markdown
    renderer if the repo has one; else a minimal safe renderer). Read-only.
- `App.jsx`: add child routes under `/pellet-portal/home`: `appointments`, `receipts`, `info`.

### Frontend — staff config
- `PelletSettings.jsx`: a "Portal Info" tab with a `<textarea>` bound to `portal_info_text`, saved
  via `PUT /pellets/config` (existing pattern). Hint: "Shown to patients on the portal's Rules &
  Info page (markdown)."

## Testing
- Backend: `/appointments` returns the patient's visits with dosage; `/receipts` lists paid
  payments with `has_receipt`; `/receipts/{id}/receipt-url` returns a Stripe URL (Stripe mocked) and
  404s when unresolvable / not the patient's payment; `/info` returns the configured text; config
  round-trips `portal_info_text`. A staff preview (viewer) token can GET all of these (read-only).
- Frontend: build clean; the shell renders the sidebar + identity; headless render optional.
- Authenticated walk-through: seed a patient with a completed visit + dose + a paid payment →
  `/appointments` shows it with dosage → `/receipts` lists it → `/info` returns the block.

## Out of scope
- No PDF receipts (link Stripe's). No new patient demographics beyond name/MRN. No editing of
  appointments from the portal (read-only history). The Rules & Info numbers are hand-maintained
  copy; enforced gating is unchanged.

## Conventions
MM/DD/YYYY, Title Case, money `$X.XX`; `pelletPortalApi` for patient calls; no secrets in source;
deploy `--project=wwc-solutions`.
