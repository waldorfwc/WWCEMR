# UI Redesign — Phase 0: Foundation

**Date:** 2026-04-19
**Project:** wwc-era-project (Waldorf Women's Care / WWC Gynecology & Aesthetics)
**Author brainstorm session:** palette B (warm mauve → recalibrated to logo), shell C (top nav), logo-driven wordmark

## Goal

Rebrand the wwc-era-project frontend to match the WWC Gynecology & Aesthetics visual identity (plum / mauve), replace the current sidebar shell with a top navigation shell, and redesign the Dashboard around operational metrics the team actually uses day-to-day. This is the visual foundation that every later phase (document retrieval, charges, claims, ERA, etc.) will build on.

## Non-goals (deliberately out of Phase 0)

- Backend changes beyond one additive `/dashboard/summary` endpoint. Existing endpoints stay as-is.
- Page-level redesigns for Claims, Patients, Appeals, Denials, Documents, PatientChart, etc. — these get the new palette/typography/components for free, but their layout and IA are Phase 1+ work.
- New features. No fax, no Waystar integration changes, no charge creation in this phase.
- Dark mode.
- Mobile/responsive polish below `md` breakpoint (desktop-first; internal use).

## Scope

### 1. Visual system

**Palette** — sampled from the WWC logo:

| Token | Hex | Usage |
|---|---|---|
| `plum.ink` | `#3D1F45` | Primary text on light surfaces |
| `plum.900` | `#4A2552` | Pressed / strongest headings |
| `plum.700` | `#6A3876` | **Primary** — buttons, active nav, links, brand accents |
| `plum.600` | `#7B4A8A` | Primary hover, secondary brand |
| `plum.400` | `#A876AB` | Muted primary, chart fills |
| `plum.300` | `#D4AED9` | Lilac — avatars, subtle fills, logo accent |
| `plum.100` | `#F3E4F6` | Surface tint — active nav pill, badges |
| `plum.50`  | `#FBF6FC` | App background |
| `border.subtle` | `#E6D3EA` | Card + divider borders |
| `text.muted` | `#6B5A70` | Secondary text |
| `success` | `#2E7D32` | Paid, success |
| `danger` | `#C62828` | Denied, overdue, timely-filing alert |
| `warning` | `#F57C00` | Partial, retry |
| `info` | `#1976D2` | Adjusted |

**Typography:**

- Headings / numeric displays: **Fraunces** (600 weight), `letter-spacing: -0.02em`, variable serif. Loaded via Google Fonts.
- Body / UI: **Inter** (400/500/600), already the de-facto `font-sans`. Loaded via Google Fonts.
- Wordmark in header: Fraunces-caps for "WWC GYNECOLOGY" (letter-spacing `0.12em`), Fraunces-italic for "& Aesthetics".
- Font-family fallback chain: `Fraunces, Georgia, 'Times New Roman', serif` and `Inter, system-ui, -apple-system, sans-serif`.

**Logo assets:**

- `frontend/src/assets/wwc-logo-full.png` — full mark with wordmark (copied from `~/Downloads/WWC Gyn Logo.png`).
- `frontend/src/assets/wwc-logo.png` — monogram-only variant (copied from `~/Downloads/WWC.png`).
- Header uses the monogram (32×32 rendered) + HTML wordmark alongside; full mark reserved for the Login page.

### 2. App shell

Replace the 240px left sidebar (`frontend/src/components/layout/Sidebar.jsx`) with a horizontal top nav.

Structure:

- 60px fixed top bar, white background, `border-bottom: 1px solid border.subtle`.
- Left cluster: logo monogram (32×32) + two-line wordmark.
- Middle cluster: horizontal nav links — Dashboard, A/R, Charts, Claims, Denials, Appeals, Import, Audit. Active link has `border-bottom: 2px solid plum.700` and `color: plum.700`; inactive is `text.muted`.
- Right cluster: environment badge ("Maryland · Internal") + user avatar with dropdown (sign out lives here).
- Body area: full-width workspace, `background: plum.50`, content max-width `1440px` centered, `padding: 24px`.

Navigation array moves from the old `Sidebar.jsx` into a new `frontend/src/components/layout/TopNav.jsx`. `App.jsx` swaps `<Sidebar />` for `<TopNav />` and the body wrapper loses its `flex` row in favor of vertical stacking.

### 3. Component library (`src/index.css` + `tailwind.config.js`)

**`tailwind.config.js`** — replace the existing `primary` blue scale with the plum scale above; add `plum` as a semantic alias for `primary`. Add `fontFamily.serif = ['Fraunces', ...]` and ensure `fontFamily.sans = ['Inter', ...]`.

**`src/index.css`** — rewrite the `@layer components` block:

- `.card` — white, `border: 1px solid border.subtle`, `border-radius: 8px`, `padding: 14px`, no shadow (borders carry the structure, not shadows — more editorial).
- `.btn-primary` — `background: plum.700`, `color: white`, hover `plum.600`, `border-radius: 6px`, `padding: 6px 12px`, `font-weight: 500`, `font-size: 13px`.
- `.btn-secondary` — white, `border: 1px solid border.subtle`, `color: plum.700`, hover `background: plum.100`.
- `.btn-danger` — unchanged color semantics, same radius/padding update.
- `.badge-*` — keep existing semantic colors (paid/denied/partial/pending/appealing/written_off). Only radius changes from pill to `4px` for consistency with other chips.
- `.input` — focus ring `plum.700`, hover border `plum.300`.
- `.table-th` — `color: text.muted`, `letter-spacing: 0.04em`, `text-transform: uppercase`.
- `.table-row` — hover `plum.50`.
- New `.stat` helper class for dashboard metric cards: label uppercase + letter-spacing, value in Fraunces 26px, sub-text 11px.
- New `.display-number` class: `font-family: serif, font-weight: 600, letter-spacing: -0.02em` — for all large numeric displays.

**`index.html`** — add Google Fonts `<link>` for Fraunces (weights 500, 600) + Inter (400, 500, 600). `<title>` updates to "WWC Gynecology & Aesthetics".

### 4. Dashboard redesign

Replace the current `frontend/src/pages/Dashboard.jsx` layout. Keep the two existing data sources (`/claims/summary`, `/denials/summary`) and add one consolidated endpoint (see §5).

Layout:

- **Header row** — "Good morning" greeting (Fraunces), date + snapshot timestamp, 30-day window selector (static dropdown in Phase 0, wired in Phase 2+), `+ New claim` primary button (links to `/claims`; create-flow wiring is Phase 2).
- **Hero KPI row (4 cards):**
  1. **Collected · last 30d** — total payments posted in last 30d.
  2. **Outstanding** — sum of open balances across all patient ledgers + count of open charges.
  3. **Open claims** — count of claims not in `paid | denied | written_off` terminal states, with "submitted this week" sub.
  4. **Timely filing alert** — count of claims within 7 days of filing deadline, red left border when `> 0`. Card is clickable → Claims filtered to that cohort.
- **Secondary row (2 cards, 2:1 grid):**
  - **Claims resolved — by window** — 30 / 60 / 90 day resolved counts each with their collected amount.
  - **Denied claims** — total open denial count + trend vs last week.
- **Bottom row (2 cards, 1:1 grid):**
  - **Recent faxes to EMA** — 3–5 most recent outbound faxes. Phase 0 implementation: fetch from `/api/fax/recent?limit=5`; if the endpoint returns 404 or an empty list, render "No recent faxes yet" empty state. Card layout is final; data wiring completes in Phase 1.
  - **Needs your attention** — textual list: timely-filing count, ERAs waiting to be posted, fax failures.

Each metric that has no backing endpoint today renders an empty state rather than throwing. Dashboard must not crash when an endpoint returns 404.

### 5. Backend: one additive endpoint

Add `GET /api/dashboard/summary` returning:

```json
{
  "collected_30d": 48320.12,
  "collected_prior_30d": 43105.00,
  "outstanding_total": 212480.00,
  "outstanding_count": 1204,
  "open_claims": 387,
  "claims_submitted_7d": 51,
  "timely_filing_at_risk_7d": 14,
  "resolved": {
    "30d": {"count": 82, "collected": 42100.00},
    "60d": {"count": 164, "collected": 81300.00},
    "90d": {"count": 241, "collected": 118700.00}
  },
  "denied_open": 43,
  "denied_delta_7d": 3,
  "attention": {
    "timely_filing": 14,
    "eras_unposted": 8,
    "fax_failures": 3
  }
}
```

Implementation: new `backend/app/routers/dashboard.py`, registered in `main.py`. Queries the existing tables (`claims`, `payments`, `denials`, `eras`) — no schema changes. Uses SQLite date arithmetic (`date('now', '-30 days')`). "Recent faxes" stays on the existing `/fax/recent` endpoint if present, otherwise the card shows an empty state in Phase 0.

### 6. Login page

Swap the current login panel branding for the full WWC logo (`wwc-logo-full.png`) centered above the sign-in card. Card gets the new `.card` treatment and plum-accented primary button. No layout change beyond that.

### 7. All other pages

Inherit the new palette and component styles automatically through Tailwind + `index.css`. No layout changes to Claims, Patients, PatientChart, Appeals, Denials, Documents, ImportFiles, AuditLog, ARDashboard, ClaimDetail, PatientDetail in Phase 0. They will look rebranded (plum primary, new typography, new buttons) but keep their current structure. Individual page redesigns are Phase 1+.

## Files touched

Created:
- `frontend/src/components/layout/TopNav.jsx`
- `frontend/src/assets/wwc-logo.png` (already copied)
- `frontend/src/assets/wwc-logo-full.png` (already copied)
- `backend/app/routers/dashboard.py`

Modified:
- `frontend/tailwind.config.js` — palette + fontFamily
- `frontend/src/index.css` — component classes
- `frontend/index.html` — Google Fonts link, title
- `frontend/src/App.jsx` — swap Sidebar for TopNav
- `frontend/src/pages/Dashboard.jsx` — full rewrite
- `frontend/src/pages/Login.jsx` — logo swap
- `backend/app/main.py` — register dashboard router

Deleted:
- `frontend/src/components/layout/Sidebar.jsx` (after TopNav is wired)

## Verification

- `npm run dev` from `frontend/` — verify header, nav, dashboard render. Click each nav item; confirm active-state styling works.
- Dashboard with empty database — every card shows zero or empty state, no crashes.
- Dashboard with real data — KPI values reconcile with `/claims/summary` where they overlap.
- `uvicorn app.main:app` from `backend/` — hit `/api/dashboard/summary`, verify JSON shape.
- Manual check on each other page (Claims, Patients, etc.) — confirm nothing broke from the color-token change; buttons are plum, not navy.
- Login page renders the WWC logo and sign-in works end-to-end.

## Open questions — none blocking

- Window selector on dashboard (30d ▾) is static in Phase 0; becomes interactive when we wire collect/resolved endpoints to accept a window parameter (Phase 2).
- "Recent faxes" card is on a best-effort endpoint probe — if `/fax/recent` doesn't return a list shape, we render the empty state and pick this up in Phase 1 when we design the fax workflow proper.
