# Patient Portal — P3: Consent

**Status:** Draft for review
**Author:** Claude Code, 2026-05-31
**Builds on:** P1 (auth + shell + dashboard), P2 (payments + scheduling), BoldSign integration (shipped earlier today)

## Goal

Replace the Consent stub with a real page that lets the patient:

1. See whether consent forms are needed for their surgery.
2. See the current status of each envelope (not sent / sent / signed).
3. Trigger the send themselves if the coordinator hasn't already.
4. Sign each form **in the portal** via an embedded BoldSign URL — no email round-trip required.
5. View the signed PDF after completion.

P3 is intentionally small. Most of the heavy machinery is already there:
- BoldSign envelopes (`app/services/boldsign_envelopes.py`)
- 17 ConsentTemplate rows seeded in prod
- Template matching by procedure + facility (`consent_template_matcher.py`)
- Webhook that updates `SurgeryConsentEnvelope.status` and `Surgery.consent_status` (`routers/boldsign.py`)
- Coordinator-side "Send via BoldSign" button on `SurgeryDetail.jsx`

P3 is essentially: expose the same machinery on the patient side, gated by `require_portal_token`.

## Architecture

```
┌────────────────────────────────────────────────────────────────────┐
│  Portal P3 — Consent                                                │
│                                                                     │
│  GET  /api/patient/portal/{sid}/consent                             │
│         returns:                                                    │
│           { templates_matched, envelopes, all_complete, can_send }  │
│         "envelopes" = list of SurgeryConsentEnvelope rows w/ status │
│         "templates_matched" = list of consent templates the matcher │
│                                picked for this surgery (procedure   │
│                                + facility), even if not sent yet    │
│                                                                     │
│  POST /api/patient/portal/{sid}/consent/send                        │
│         body: {} (no params)                                        │
│         delegates to boldsign_envelopes.send_consent_envelopes      │
│         (already used by coordinator endpoint)                      │
│         returns the same envelope list                              │
│                                                                     │
│  GET  /api/patient/portal/{sid}/consent/sign-link/{envelope_id}     │
│         returns: { sign_url }                                       │
│         calls BoldSign's getEmbeddedSignLink API for the PATIENT    │
│         signer email only — never returns the surgeon or witness    │
│         link from the portal (those go via email to those people)   │
│                                                                     │
│  GET  /api/patient/portal/{sid}/consent/signed-pdf/{envelope_id}    │
│         streams the signed PDF from BoldSign once status=signed.    │
│                                                                     │
│  Frontend                                                           │
│    /portal/s/:sid/consent  — <PortalConsent /> replaces stub        │
│      - List of envelopes (or templates if not sent yet)             │
│      - "Send for signing" button when no envelopes exist            │
│      - "Sign now" button per envelope → BoldSign embedded sign URL  │
│      - "Download signed PDF" link when status = signed              │
│      - Polling: re-fetch every 5 seconds while any envelope is in   │
│        sent/in_progress state (catches the webhook update without   │
│        requiring patient to refresh)                                │
└────────────────────────────────────────────────────────────────────┘
```

## Trigger model — patient-initiated or coordinator-initiated?

Both are supported with no special-casing. The coordinator's "Send via BoldSign" button on `SurgeryDetail.jsx` (already shipped) and the new patient-facing "Send for signing" button BOTH call the same `boldsign_envelopes.send_consent_envelopes()`. The portal endpoint just wraps that with patient auth.

The portal UI logic:
- **No matching templates** → "We don't have consent forms for this surgery on file. Call us." Hide the send button.
- **Templates match, no envelopes sent yet** → Show templates list with a single "Send for signing" CTA.
- **Envelopes exist** → Show per-envelope status with "Sign now" / "Already signed ✓" / "Download" actions.

If the coordinator sends first, the patient sees status as "sent" and the "Sign now" link on each row. If the patient sends first, the coordinator's UI updates immediately (since the data is the same `SurgeryConsentEnvelope` rows).

## Sign-link endpoint security

BoldSign's `getEmbeddedSignLink` API takes a `documentId` and a `signerEmail`. The portal must:

1. Look up the envelope row by `boldsign_envelope_id`.
2. Confirm the envelope belongs to `surgery_id` (path param).
3. Resolve the patient signer email from the surgery: `surgery.email`. Reject if the email doesn't match what BoldSign has for the patient role on that document.
4. Call `getEmbeddedSignLink` with the patient's email — never the surgeon's, never the witness's.
5. Return the URL.

This means: even if someone forges a request to sign as the surgeon, they only ever get the patient's signing link. The surgeon and witness flows happen via their own BoldSign emails (or eventually their own portal flows).

## Polling strategy

The frontend polls `GET /consent` every 5 seconds while any envelope is in `sent` or `in_progress` state. Stops polling once all envelopes are `signed`, `voided`, `declined`, or `failed`. TanStack Query's `refetchInterval` handles this with a conditional function returning the interval or `false`.

5 seconds because:
- BoldSign's webhook typically arrives within 2–4 seconds of signing.
- Patient probably won't switch tabs back to the portal for a few seconds anyway.
- 30 seconds would feel laggy; 1 second would burn requests.

## New backend endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET  | `/api/patient/portal/{sid}/consent` | portal | List templates + envelopes + status |
| POST | `/api/patient/portal/{sid}/consent/send` | portal | Send envelopes via existing service |
| GET  | `/api/patient/portal/{sid}/consent/sign-link/{envelope_id}` | portal | BoldSign embedded sign URL for patient role |
| GET  | `/api/patient/portal/{sid}/consent/signed-pdf/{envelope_id}` | portal | Streamed PDF download |

## New frontend page

```
frontend/src/pages/portal/Consent.jsx
  ├── <NoTemplatesMatch />   when matcher returns 0 templates
  ├── <UnsentTemplates />    when templates match but no envelopes
  ├── <EnvelopeList />       per-envelope rows with status + actions
  └── <SignedSummary />      green check + download links when all complete
```

## What's NOT in P3 (defer)

- **Surgeon / witness portal flow** — Aryian + surgery@ still sign via BoldSign emails. Building a separate portal for clinicians is overkill at this practice's size.
- **Consent re-send when patient closes a BoldSign tab** — BoldSign emails work fine for that path; the embedded URL is for when the patient already has the portal open.
- **Consent template editing from the portal** — admin-only via the existing `/admin/consent-templates` (or scheduled work).
- **Mid-document save state** — BoldSign handles partial completion; patient can resume from where they left off via the same embedded URL.
- **Step-up SMS at sign time** — Consent is not a financial action and can be voided. Plain portal JWT is the auth level.
- **PDF caching** — we re-fetch from BoldSign each time. If bandwidth becomes a concern, swap for GCS storage.

## Open questions

1. **Should we hide the "Send for signing" CTA in the portal if `surgery.scheduled_date` isn't set yet?** Argument for: don't generate paperwork before the date is confirmed. Argument against: patient might want to pre-sign so they don't have to scramble. Default: **show always**. Coordinator can still void if needed.

2. **What if BoldSign's `getEmbeddedSignLink` rate-limits?** We hit it on every "Sign now" click. Should be fine for current volume; if it becomes an issue, cache the URL for 5 minutes per envelope.

3. **Where do we host the signed PDF?** P3 streams it from BoldSign on every download. If BoldSign archives stop being available (free tier expiry etc.), the signed PDFs become unavailable. Worth fetching + storing to GCS at signing-completion time — but that's a P3b improvement.

## Risks

- **Embedded sign URL TTL.** BoldSign's embedded sign URLs are short-lived (5 minutes per their docs). If the patient gets the URL and then walks away for 10 minutes, the link is dead. The frontend handles this by lazily fetching the URL only when they click "Sign now" — not at page load.
- **Webhook race.** Patient signs, immediately clicks back to portal, polls before webhook arrives. They see status `sent` for a brief window. The 5-second polling catches this within one tick.
- **Multi-envelope completion order.** All 17 templates require Patient + Surgeon + Witness signing. The portal's "all_complete" boolean reflects `all(e.status == "signed" for e in envelopes)` — which means a patient who has signed their part but the surgeon hasn't will see status "in_progress." That's correct.

## Tech stack

Identical to P1/P2. New endpoints all live in `app/routers/patient_portal.py`. New frontend page replaces the existing `frontend/src/pages/portal/stubs/ConsentStub.jsx` placeholder.
