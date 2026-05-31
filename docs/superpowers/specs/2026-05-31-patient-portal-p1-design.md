# Patient Portal — P1: Auth + Dashboard + Shell

**Status:** Draft for review
**Author:** Claude Code, 2026-05-31
**Builds on:** Existing patient_surgery router (slot picker auth + status)

## Goal

Ship the foundation of a patient self-service portal. P1 covers three things:

1. **Sign-in flow** — DOB + last-4-of-phone, then an SMS code (true 2FA).
2. **Portal shell** — branded layout, navigation, session handling.
3. **Milestone dashboard** — read-only view of "what's done, what's left" for this surgery.

P1 is intentionally read-mostly. Payment, scheduling, consent, FMLA, clearance, chat, etc. land in P2–P6. Once a patient can sign in and see their progress, every subsequent phase is a screen we wire into the same shell.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  /portal                            (new frontend route prefix) │
│   ├── /portal/login                 [DOB + last-4]              │
│   ├── /portal/verify                [SMS code entry]            │
│   └── /portal/s/{surgery_id}/...    [authenticated routes]      │
│         └── /                       [dashboard — P1 ends here]  │
│                                                                 │
│  Backend                                                        │
│   POST /api/patient/portal/login    (DOB + last-4 → SMS code)   │
│   POST /api/patient/portal/verify   (code → JWT)                │
│   GET  /api/patient/portal/{sid}/dashboard  (auth required)     │
│         returns: surgery summary + milestone list + next steps  │
└─────────────────────────────────────────────────────────────────┘
```

The existing `/p/surgery/{id}/*` URLs (slot picker links delivered via email) keep working — they're for one-shot patient actions before a portal account makes sense. The new `/portal/*` URLs are the durable, session-based portal.

## Auth flow

The existing patient_surgery auth gives a JWT immediately after DOB + last-4 match. P1 inserts an SMS step between match and JWT.

```
[1] Patient navigates to /portal/login
[2] Enters DOB + last-4 of phone
[3] POST /api/patient/portal/login
      → backend finds matching Surgery(s) by (dob, last4)
      → if 0 matches:      generic "not found"   (don't leak existence)
      → if locked out:     429 with "wait N min" message
      → otherwise:         generates 6-digit code, stores hash in
                           PatientPortalAuthCode (5-min TTL),
                           sends via Twilio SMS, returns
                           { challenge_token, surgery_id_hint }
[4] /portal/verify shows code-entry form, posts:
      POST /api/patient/portal/verify
        { challenge_token, code }
      → checks hash + expiry
      → on success: issues JWT (PATIENT_PORTAL_AUDIENCE), TTL 1 hr,
                    redirects to /portal/s/{surgery_id}/
      → on failure: 401 + increments PatientAuthAttempt counter
[5] All /portal/* routes require the JWT in Authorization header
```

### Auth design decisions

- **Surgery-scoped sessions.** JWT's `sub` is the `surgery_id`, not a patient ID. WWC tracks Surgery rows, not Patient rows, so a person with two upcoming surgeries gets two portal sessions. Future Patient-table consolidation can change this without breaking the URL structure.
- **Code storage.** `PatientPortalAuthCode(surgery_id, code_hash, expires_at, used_at)`. Hash with bcrypt so a DB dump doesn't leak active codes. Mark `used_at` on success so a code can't be replayed.
- **Code transport.** Twilio SMS via the existing `send_sms` infrastructure. New SMS template `sms_portal_login_code` (5th kind — extends `SMS_TEMPLATE_KINDS`).
- **Rate limits.**
  - Login (DOB+last4): existing pattern — 3 fails in 15 min = lockout.
  - Code verify: 3 wrong codes for the same challenge_token = challenge invalidated (must restart).
  - Code request: 1 code per 60 seconds per surgery_id (prevents SMS bombing).
- **Recovery path.** If patient's phone changed: portal shows a "lost access? call the office at 240-252-2140" link below the form. No self-serve recovery — too easy to abuse for a clinical system.

### What we change vs. keep

| Code | Decision |
|---|---|
| Existing `_issue_patient_token` / `_verify_patient_token` | **Keep + rename audience.** The existing magic-link auth keeps using `wwc:patient-surgery`. The new portal auth uses `wwc:patient-portal` so a magic-link JWT can't be used as a portal session and vice-versa. |
| Existing `PatientAuthAttempt` lockout table | **Reuse.** Same window/threshold rules apply. |
| Existing `/api/patient/surgery/{id}/auth` endpoint | **Keep** for the magic-link flow (slot picker email). Mark as legacy in comments. |

## Portal shell

### Layout

A persistent shell with three regions:
- **Header** — WWC logo, patient first name, "Sign out".
- **Side nav** (collapsible on mobile) — Dashboard / Payments / Schedule / Consent / Documents / Messages. P1 only lights up Dashboard; the rest render a `"Coming soon"` stub but keep the nav stable so nothing moves later.
- **Main** — page content.

Mobile-first; everything stacks on small screens. Tailwind, matching existing app styling. The header strip is the lighter "WWC Apps" branding from the login screen change earlier today.

### Routing

React Router under `/portal`:

```
/portal/login              <PortalLogin />
/portal/verify             <PortalVerify />     (carries challenge_token in URL state)
/portal/s/:sid             <PortalShell>        (route guard checks JWT)
   /                       <Dashboard />
   /payments               <PaymentsStub />     ("Coming soon" — wired in P2)
   /schedule               <ScheduleStub />     ("Coming soon" — wired in P2)
   /consent                <ConsentStub />      ("Coming soon" — wired in P3)
   /documents              <DocumentsStub />    ("Coming soon" — wired in P4–P5)
   /messages               <MessagesStub />     ("Coming soon" — wired in P6)
```

Route guard: any `/portal/s/*` route checks for a valid JWT in localStorage. If missing or expired, redirect to `/portal/login` and preserve the original target so the patient lands where they were trying to go.

### Session handling

- JWT stored in localStorage under `wwc.portal.token`.
- TanStack Query handles refetch/cache. A 401 from any API call → clear token + redirect to login.
- Sign out = clear localStorage + redirect to `/portal/login`. No server-side logout call needed (JWT is stateless; TTL is short enough).

## Milestone dashboard

The dashboard is the page after sign-in. It shows the patient's surgery and a list of milestones with status badges.

### Surgery summary card

| Field | Source |
|---|---|
| Procedure | `surgery.procedures[0].description` (first procedure for now) |
| Surgeon | `surgery.surgeon_primary` |
| Date | `surgery.scheduled_date` — or "not scheduled yet" |
| Time | `surgery.scheduled_start_time` — or "TBD" |
| Facility | Friendly label from `FACILITY_SHORT[selected_facility]` |
| Patient responsibility | `surgery.patient_responsibility` — or "calculating" |

### Milestone list

Each row: icon + label + status badge + optional CTA-stub.

| Milestone | Source | Status logic | CTA (lights up in phase) |
|---|---|---|---|
| Patient responsibility paid | `SurgeryPayment` rows where `status=succeeded` | sum ≥ patient_responsibility → ✓ done, else amount paid / amount due | P2: "Pay now" |
| Surgery date set | `surgery.scheduled_date IS NOT NULL` | ✓ done if set, else "Schedule" | P2: opens slot picker |
| Consent forms signed | `SurgeryConsentEnvelope` rows | all signed → ✓, any sent → in progress, none → not started | P3: "Sign now" |
| Hospital pre-op call | `surgery.hospital_preop_self_reported` *(new col)* | self-reported ✓ | P5: "I completed my call" |
| Labs completed | `surgery.labs_self_reported` *(new col)* | self-reported ✓ | P5: "I had my labs done" |
| FMLA submitted | `surgery.fmla_status` *(added in P5)* | **Row hidden in P1** — surfaces in P5 when the column exists | P5: "Submit FMLA" |

The two new columns (`hospital_preop_self_reported`, `labs_self_reported`) are 1-line schema additions in P1 even though their CTAs ship in P5 — we want the dashboard logic to be stable from day one. `fmla_status` lands in P5 with the rest of FMLA, and the dashboard hides the row until that column exists. The dashboard endpoint inspects `hasattr(surgery, "fmla_status")` to decide whether to include the row — no `if phase ≥ 5` flags in code.

### "Next thing to do" banner

Top of dashboard, a single highlighted action card surfacing the highest-priority incomplete milestone. The patient knows what to do next without having to read all six rows.

Priority order:
1. Patient responsibility unpaid → "Make a payment to lock in your date"
2. Surgery date not picked → "Choose your surgery date"
3. Consent not signed → "Sign your consent forms"
4. FMLA needed → "Submit your FMLA request"
5. Labs/pre-op pending → "Mark your labs and pre-op as complete"

All milestones done → "You're all set — see you on {{surgery_date}}!"

## Data needs

### New tables

```python
class PatientPortalAuthCode(Base):
    __tablename__ = "patient_portal_auth_codes"
    id              = Column(GUID, primary_key=True, default=new_uuid)
    surgery_id      = Column(GUID, ForeignKey("surgeries.id"), nullable=False, index=True)
    challenge_token = Column(String(64), nullable=False, unique=True, index=True)
    code_hash       = Column(String(60), nullable=False)         # bcrypt
    fail_count      = Column(Integer, default=0, nullable=False)
    expires_at      = Column(DateTime, nullable=False)
    used_at         = Column(DateTime, nullable=True)
    created_at      = Column(DateTime, default=datetime.utcnow, nullable=False)
    sent_to_phone   = Column(String(40), nullable=True)          # for audit
```

### New surgery columns

```python
# Self-report flags for P5 milestones — added in P1 so dashboard logic is stable
labs_self_reported              = Column(Boolean, default=False, nullable=False)
labs_self_reported_at           = Column(DateTime, nullable=True)
hospital_preop_self_reported    = Column(Boolean, default=False, nullable=False)
hospital_preop_self_reported_at = Column(DateTime, nullable=True)
```

### New SMS template kind

Extend `SMS_TEMPLATE_KINDS` and add to the seed script:

```
sms_portal_login_code:
  body: "WWC: Your portal sign-in code is {{code}}. Expires in 5 minutes."
  (≤95 chars — well under 1 segment)
```

## New backend endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/patient/portal/login` | (dob, phone_last4) → sends SMS, returns challenge_token |
| POST | `/api/patient/portal/verify` | (challenge_token, code) → JWT |
| GET  | `/api/patient/portal/{sid}/dashboard` | Surgery summary + milestones + next-thing banner |

The `dashboard` endpoint is the only one the React shell calls during P1; it returns everything the dashboard needs in one round-trip.

## New frontend pages

```
frontend/src/pages/portal/
  PortalLogin.jsx           Two-field form (DOB picker + 4-digit input)
  PortalVerify.jsx          Six-digit code input (auto-advance per digit)
  PortalShell.jsx           Header + sidebar + Outlet
  Dashboard.jsx             Summary card + milestone list + banner
  stubs/PaymentsStub.jsx    "Coming soon" with phase-2 note
  stubs/ScheduleStub.jsx    (same pattern)
  stubs/ConsentStub.jsx
  stubs/DocumentsStub.jsx
  stubs/MessagesStub.jsx

frontend/src/lib/portal-api.js   axios client w/ JWT injection + 401 handler
frontend/src/hooks/usePortalAuth.js   Token state + sign-in/sign-out actions
```

Routing wired in `App.jsx` (or wherever the top-level router lives) under the `/portal` prefix.

## Out of scope for P1 (defer to later phases)

- Payments UI, Stripe Checkout from portal (P2)
- Slot picker / scheduling from portal (P2)
- Consent-from-portal trigger (P3)
- Document vault, FMLA upload, clearance upload, instructions PDFs (P4–P5)
- Coordinator chat thread (P6)
- Waitlist preferences editor (P6)
- Multi-surgery picker (if a patient has 2 active surgeries, P1 just lets them sign into one at a time)
- Spanish translation
- Account-recovery self-serve (call office instead)

## Open questions

These don't block the spec — they affect P2+ details. Flagging now so we don't relitigate later.

1. **Portal URL — subdomain or path?** Currently writing this as `gw.waldorfwomenscare.com/portal/*`. We could split it onto `portal.waldorfwomenscare.com` later if branding warrants. P1 lives at `/portal` on the existing domain to avoid the DNS+TLS setup.
2. **First-touch onboarding.** How does a patient discover the portal URL? Likely an email signature link + a line in confirmation/reminder emails. Not a code change for P1, but worth a coordinator briefing.
3. **Token TTL.** 1 hr matches the existing magic-link TTL. Patient portal use may benefit from longer (e.g. 4 hr) so it doesn't expire mid-task. Worth revisiting after P2 once we see usage patterns.

## Tech stack

- Backend: FastAPI + SQLAlchemy (existing). JWT via `python-jose` (existing). SMS via `send_sms` → Twilio (existing). Bcrypt for code hashing via `passlib[bcrypt]` (already a dep).
- Frontend: React + Vite + TanStack Query + Tailwind (existing). React Router v6 routes added under `/portal`.
- DB: Postgres (Cloud SQL). One new table + 4 new columns; reversible alembic-style migration script.

## Risks / things to watch

- **SMS deliverability.** Twilio is reliable but carrier filtering can occasionally drop short-code-style messages. We're sending from a long-code (+12402522415) so risk is low, but the verify page should offer "didn't receive a code? resend after 60s" affordance.
- **Phone-number drift.** Patients change phones. The "call the office" recovery path is a manual interrupt for the coordinator — should be rare but plan on it.
- **Token leakage.** localStorage is XSS-readable. P1 portal has no high-risk write actions (just reading dashboard) so blast radius is low. P2 adds payments, so before P2 we should harden against XSS (audit dependencies, set strict CSP). Note for P2 plan.
