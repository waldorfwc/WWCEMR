# Reputation Management Module — Design

**Status:** Draft for review
**Author:** Claude Code, 2026-06-01
**Builds on:** Existing backend (auth, audit, Twilio SMS), portal SMS-verification pattern from P1, existing admin UI shell.

## Goal

Give WWC a closed-loop review pipeline driven by per-employee QR codes:

1. Each patient-facing employee gets a printable QR code linked to their profile.
2. A patient scans the QR → mobile-friendly review page.
3. Patient picks 1–5 stars + optional comment; can optionally identify themselves via SMS verification.
4. If they pick 5 stars, after submit they're offered a one-tap handoff to WWC's Google Business review page.
5. All reviews land in our DB. Employees earn points for scans + completed reviews + 5-star ratings + Google share-clicks (no points for the Google review actually being posted — Google doesn't expose that).
6. A public embed (Webflow-friendly iframe) shows opt-in reviews to website visitors.
7. Admin UI lets staff create profiles, print QRs, see a leaderboard, and audit every review.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  PATIENT FLOW (public — no auth)                                │
│                                                                 │
│  reviews.waldorfwomenscare.com/r/{qr_token}                     │
│        ↓ JS POST /api/r/{token}/scan          (+1 scan point)   │
│  Mobile review form (★★★★★ + optional comment)                  │
│        ↓                                                        │
│  Optional: "I'm a WWC patient" toggle                           │
│        → POST /api/r/{token}/verify-patient (SMS challenge)     │
│        → enter code → links review to chart number              │
│        ↓                                                        │
│  POST /api/r/{token}/submit                  (+2 review points) │
│                                                  (+5 if 5-star) │
│        ↓                                                        │
│  If 5 stars → "Share on Google?" handoff                        │
│        → Google Business review URL (one-time configured)       │
│        → POST /api/r/{token}/google-clicked  (+3 share points)  │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  ADMIN FLOW (staff session via existing get_current_user)       │
│                                                                 │
│  /admin/reputation/profiles                                     │
│        — Create / edit / deactivate employee profiles           │
│        — Each profile gets a unique qr_token                    │
│        — Download printable QR PNG with name + role             │
│  /admin/reputation/leaderboard                                  │
│        — Total points + scan + review + 5-star + share counts   │
│  /admin/reputation/reviews                                      │
│        — All reviews, ordered by date                           │
│        — Chart-number linkage visible (PHI gate)                │
│        — Per-review "Show on public embed" approval toggle      │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  PUBLIC EMBED (for Webflow)                                     │
│                                                                 │
│  reviews.waldorfwomenscare.com/embed                            │
│        — Static-styled HTML; iframe-friendly                    │
│        — Shows only reviews with consent_to_display=true AND    │
│          approved_for_embed=true                                │
│        — Displays "Jane D." (first name + last initial)         │
│  GET /api/reviews/public                                        │
│        — JSON variant for custom JS embed                       │
└─────────────────────────────────────────────────────────────────┘
```

## New schema — 3 tables

### `reputation_profiles` — one per reviewable employee

```python
class ReputationProfile(Base):
    __tablename__ = "reputation_profiles"

    id            = Column(GUID(), primary_key=True, default=new_uuid)
    user_email    = Column(String(200), nullable=True)
    # nullable because non-system employees (cleaners, occasional staff)
    # might want a QR without a full user account
    display_name  = Column(String(120), nullable=False)
    # what the patient sees ("Sarah Smith, RN")
    role_label    = Column(String(80), nullable=True)
    # e.g. "Surgical Coordinator", "MA", "Front Desk"
    qr_token      = Column(String(40), nullable=False, unique=True, index=True)
    # short urlsafe token, embedded in the QR URL
    active        = Column(Boolean, default=True, nullable=False)
    created_at    = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at    = Column(DateTime, default=datetime.utcnow,
                              onupdate=datetime.utcnow, nullable=False)
```

### `reputation_scans` — every scan, even if no review followed

```python
class ReputationScan(Base):
    __tablename__ = "reputation_scans"
    __table_args__ = (
        Index("ix_reputation_scans_profile", "profile_id", "scanned_at"),
    )

    id              = Column(GUID(), primary_key=True, default=new_uuid)
    profile_id      = Column(GUID(),
                                ForeignKey("reputation_profiles.id",
                                            ondelete="CASCADE"),
                                nullable=False)
    scanned_at      = Column(DateTime, default=datetime.utcnow,
                                nullable=False)
    ip_address      = Column(String(45), nullable=True)  # for dedup
    user_agent      = Column(String(300), nullable=True)
    points_credited = Column(Integer, default=0, nullable=False)
    # 0 if deduped (same IP within 24h), 1 otherwise
```

### `reputation_reviews` — the actual submissions

```python
class ReputationReview(Base):
    __tablename__ = "reputation_reviews"
    __table_args__ = (
        Index("ix_reputation_reviews_profile", "profile_id", "submitted_at"),
    )

    id                   = Column(GUID(), primary_key=True, default=new_uuid)
    profile_id           = Column(GUID(),
                                      ForeignKey("reputation_profiles.id",
                                                  ondelete="CASCADE"),
                                      nullable=False)
    scan_id              = Column(GUID(),
                                      ForeignKey("reputation_scans.id",
                                                  ondelete="SET NULL"),
                                      nullable=True)
    stars                = Column(Integer, nullable=False)   # 1..5
    body                 = Column(Text, nullable=True)
    patient_first_name   = Column(String(80), nullable=True)
    # collected for the embed display; required if consent_to_display=true
    patient_last_initial = Column(String(2), nullable=True)
    patient_chart_number = Column(String(20), nullable=True)
    # populated only when the patient SMS-verifies; PHI — internal only
    patient_phone        = Column(String(20), nullable=True)
    # the verified phone, for audit
    consent_to_display   = Column(Boolean, default=False, nullable=False)
    approved_for_embed   = Column(Boolean, default=False, nullable=False)
    # staff toggles this — reviews don't appear on Webflow until approved
    google_clicked_at    = Column(DateTime, nullable=True)
    submitted_at         = Column(DateTime, default=datetime.utcnow,
                                      nullable=False)
```

## Points policy (configurable in code, hardcoded for v1)

| Action | Points |
|---|---|
| Scan (deduped: 1 per IP per 24h) | +1 |
| Review submitted (any star count) | +2 |
| 5-star review submitted | +5 (in addition to the +2) |
| "Share on Google" click after 5-star | +3 |

A patient who scans, submits a 5-star review, and shares on Google → employee gets **+11 points**.
A patient who scans and bails → **+1 point**.
The same patient re-scanning the same QR 10 times within 24h → still **+1 point**.

**Scan dedup**: server-side check `(profile_id, ip_address, last 24h)`. Best-effort — clinic WiFi means multiple patients share an IP. We accept the noise; it under-counts rather than over-counts. The dedup decision lives in `ReputationScan.points_credited` so it's auditable.

## Patient-side endpoints (no auth)

| Method | Path | Purpose |
|---|---|---|
| `GET`  | `/r/{qr_token}` | Mobile review-form HTML (SPA route) |
| `POST` | `/api/r/{qr_token}/scan` | Log scan, return profile display info |
| `POST` | `/api/r/{qr_token}/verify-patient/start` | Send SMS code to patient phone |
| `POST` | `/api/r/{qr_token}/verify-patient/check` | Verify code → return chart match |
| `POST` | `/api/r/{qr_token}/submit` | Submit review |
| `POST` | `/api/r/{qr_token}/google-clicked` | Track Google share click |
| `GET`  | `/api/reviews/public?limit=20` | Approved + opt-in reviews for embed |
| `GET`  | `/embed` | Server-rendered iframe page |

The verify-patient flow reuses `patient_portal_auth.issue_challenge` with `purpose="review"` (new value). Same Twilio infra; new SMS copy.

## Staff endpoints (`get_current_user` + `reputation:manage` permission for writes)

| Method | Path | Purpose |
|---|---|---|
| `GET`  | `/api/admin/reputation/profiles` | List profiles |
| `POST` | `/api/admin/reputation/profiles` | Create profile (mints qr_token) |
| `PATCH`| `/api/admin/reputation/profiles/{id}` | Update name/role/active |
| `POST` | `/api/admin/reputation/profiles/{id}/rotate-token` | New qr_token (invalidates old QR) |
| `GET`  | `/api/admin/reputation/profiles/{id}/qr.png` | Printable QR code PNG |
| `GET`  | `/api/admin/reputation/leaderboard` | Sorted points + stats |
| `GET`  | `/api/admin/reputation/reviews` | All reviews + chart linkage |
| `PATCH`| `/api/admin/reputation/reviews/{id}` | Approve/unapprove for embed |

A new permission `reputation:manage` belongs to the existing admin group.

## QR code generation

Generate at request time (cached) via the `qrcode` Python library, returned as a PNG. The QR encodes:

```
https://reviews.waldorfwomenscare.com/r/{qr_token}
```

Optional: embed the WWC logo in the center using `qrcode.image.styledpil`. Printable PNG is ~600×600 at 300dpi with the employee's `display_name` + `role_label` text below.

## Subdomain wiring

- `reviews.waldorfwomenscare.com` → Cloudflare CNAME → existing frontend Cloud Run service
- Frontend router serves `/r/:token` and `/embed` routes (plus the existing app at other paths)
- No new Cloud Run service — single deploy

## Webflow embed

Two options exposed:

1. **Iframe** (simplest): `<iframe src="https://reviews.waldorfwomenscare.com/embed" style="border:0; width:100%; height:600px"></iframe>`
2. **JSON** (custom JS): `fetch("https://reviews.waldorfwomenscare.com/api/reviews/public")` and render with site styles

For v1, ship the iframe. Add JSON later if Webflow folks want bespoke styling.

## HIPAA notes

- `patient_chart_number` + `patient_phone` are PHI. Admin endpoints that return these gated by `reputation:manage`.
- Public embed strictly returns: `stars`, `body`, `patient_first_name + last_initial` (if consent), `submitted_at`. Never the chart or phone.
- The SMS-verification flow does NOT grant any portal/data access — it only labels the review with a matched chart.
- Reviews are append-only. Editing is allowed only for the `approved_for_embed` toggle (audit-logged). Deletion requires `user:manage` permission and is full row removal — not a soft-delete — for right-to-be-forgotten compliance.

## What's NOT in v1 (defer)

- **API integration with Google** (we don't auto-post reviews; just hand off the URL)
- **Per-employee point weight configuration UI** (weights are hardcoded for v1; revisit if WWC wants experiments)
- **Manager dashboards / period rollups** (just a leaderboard for v1)
- **Patient deduplication across employees** (a patient scanning 5 different employees in one visit gives 5 employees points; that's fine — they each interacted with that patient)
- **Negative-review escalation** (e.g., auto-email manager on 1-star). Adds workflow noise; revisit
- **Patient consent UI for chart linkage** as a separate step — for v1 the SMS verify implicitly = consent

## Open questions

1. **Google Business review URL** — need from Oliver. Format is `https://search.google.com/local/writereview?placeid=PLACE_ID`. Find place_id via Google Business Profile.
2. **Display the employee's photo in the review form?** — Nice-to-have for v2; not required for v1.
3. **Multi-language?** — Defer; WWC patient base is mostly English.

## Risks

- **QR sticker swap**: someone replaces a real QR sticker with a fake one pointing elsewhere. Mitigation: print QRs with the WWC logo and `gw.waldorfwomenscare.com` watermark; train staff to flag tampered stickers. Not a code fix.
- **Review-bombing / fake reviews**: someone (competitor, disgruntled ex-employee) scans the QR and posts negative reviews. Mitigation: staff approval gate before reviews go on the public embed. They land in DB and inflate scan counts but don't reach the website until approved.
- **Patient confusion about "I'm a patient" toggle**: copy must make clear that identifying themselves links the review to their chart and is optional. The SMS verify before chart link is the trust boundary.

## Tech stack

Same as everywhere else. 3 new tables. New router `app/routers/reputation.py`. New `app/services/qr_generator.py` (depends on `qrcode` Python package, currently not in requirements — add it). Frontend: new patient-facing pages under a separate route tree + new admin pages.
