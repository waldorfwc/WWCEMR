# UI Redesign Foundation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebrand `wwc-era-project` to the WWC Gynecology & Aesthetics plum palette, replace the sidebar with a top-nav shell, and redesign the Dashboard around operational metrics. One additive backend endpoint; no schema changes.

**Architecture:** Tailwind-based design tokens drive the whole system — change `tailwind.config.js` + `src/index.css` and every page inherits the new look. A new `TopNav.jsx` replaces `Sidebar.jsx` at the shell level in `App.jsx`. Dashboard is rewritten against a new aggregate endpoint `GET /api/dashboard/summary` that queries existing tables (`claims`, `payments`, `denials`) — no migrations.

**Tech Stack:** React 18, Vite 5, Tailwind 3, React Router 6, React Query 5, recharts, lucide-react (frontend). FastAPI, SQLAlchemy, pytest + FastAPI TestClient (backend).

**Reference spec:** `docs/superpowers/specs/2026-04-19-ui-redesign-foundation-design.md`

---

## Pre-flight notes

- The project is **not yet a git repo** — Task 1 initializes it before any other work.
- Logo assets are already in place at `frontend/src/assets/wwc-logo.png` and `frontend/src/assets/wwc-logo-full.png`.
- The dev server starts via `./start.sh` at repo root (backend on :8000, frontend on :3000).
- No frontend test framework exists and adding one is out of scope — frontend verification is manual via `npm run dev`. Backend gets a minimal pytest setup (one file, one test) because the dashboard endpoint is worth locking in.
- **SQLite date arithmetic:** use `func.date('now', '-30 days')` with `Payment.payment_date >=`, not Python `datetime` — the DB is SQLite and this keeps it fast and timezone-sane.

---

## Task 1: Initialize git repo

**Files:**
- Create: `/Users/wwcclaudecode/Documents/wwc-era-project/.gitignore`

- [ ] **Step 1: Create `.gitignore`**

Write this exact content to `/Users/wwcclaudecode/Documents/wwc-era-project/.gitignore`:

```
# Python
__pycache__/
*.py[cod]
*$py.class
venv/
.venv/
*.egg-info/

# Node
node_modules/
dist/
.vite/

# Data & secrets
*.db
*.db.bak.*
*.log
uploads/
exports/
backend/venv/
rc-credentials.json
.env
.env.local

# OS
.DS_Store

# Superpowers workspace
.superpowers/
```

- [ ] **Step 2: Initialize repo and make the baseline commit**

Run:
```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git init
git add -A
git commit -m "chore: initial commit — pre-redesign baseline"
```

Expected: "Initialized empty Git repository..." then one commit created with hundreds of files.

- [ ] **Step 3: Verify**

Run: `git log --oneline`
Expected: one line showing the baseline commit.

---

## Task 2: Update Tailwind config with plum palette and fonts

**Files:**
- Modify: `frontend/tailwind.config.js` (full rewrite)

- [ ] **Step 1: Rewrite `frontend/tailwind.config.js`**

Replace the entire file with:

```js
/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx,ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // Plum scale — sampled from the WWC Gynecology & Aesthetics logo.
        // `primary` kept as an alias so existing `text-primary-500` etc. still work.
        plum: {
          50:  '#FBF6FC',
          100: '#F3E4F6',
          300: '#D4AED9',
          400: '#A876AB',
          600: '#7B4A8A',
          700: '#6A3876',
          900: '#4A2552',
          ink: '#3D1F45',
        },
        primary: {
          50:  '#FBF6FC',
          100: '#F3E4F6',
          300: '#D4AED9',
          400: '#A876AB',
          500: '#6A3876',  // historical `primary-500` now maps to plum.700
          600: '#7B4A8A',
          700: '#6A3876',
          900: '#4A2552',
        },
        border: {
          subtle: '#E6D3EA',
        },
        ink: '#3D1F45',
        muted: '#6B5A70',
        success: '#2E7D32',
        danger: '#C62828',
        warning: '#F57C00',
        info: '#1976D2',
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'Segoe UI', 'sans-serif'],
        serif: ['Fraunces', 'Georgia', '"Times New Roman"', 'serif'],
      },
      letterSpacing: {
        wordmark: '0.12em',
      },
    },
  },
  plugins: [],
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/tailwind.config.js
git commit -m "style(frontend): swap primary palette to WWC plum scale, add Fraunces serif"
```

---

## Task 3: Add Google Fonts and update title

**Files:**
- Modify: `frontend/index.html`

- [ ] **Step 1: Rewrite `frontend/index.html`**

Replace the entire file with:

```html
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>WWC Gynecology &amp; Aesthetics</title>
    <link rel="preconnect" href="https://fonts.googleapis.com" />
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
    <link
      href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,600;9..144,700&family=Inter:wght@400;500;600;700&display=swap"
      rel="stylesheet"
    />
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.jsx"></script>
  </body>
</html>
```

- [ ] **Step 2: Commit**

```bash
git add frontend/index.html
git commit -m "chore(frontend): load Fraunces + Inter from Google Fonts, update page title"
```

---

## Task 4: Rewrite component layer in index.css

**Files:**
- Modify: `frontend/src/index.css` (full rewrite)

- [ ] **Step 1: Rewrite `frontend/src/index.css`**

Replace the entire file with:

```css
@tailwind base;
@tailwind components;
@tailwind utilities;

@layer base {
  html { -webkit-font-smoothing: antialiased; }
  body {
    @apply bg-plum-50 text-ink font-sans;
  }
  h1, h2, h3, h4 {
    @apply font-serif tracking-tight text-ink;
  }
}

@layer components {
  /* Structural */
  .card {
    @apply bg-white border border-border-subtle rounded-lg p-3.5;
  }
  .stat {
    @apply card flex flex-col gap-1;
  }
  .display-number {
    @apply font-serif font-semibold tracking-tight text-ink;
  }

  /* Buttons */
  .btn-primary {
    @apply bg-plum-700 text-white px-3 py-1.5 rounded-md hover:bg-plum-600 transition-colors font-medium text-sm;
  }
  .btn-secondary {
    @apply bg-white text-plum-700 border border-border-subtle px-3 py-1.5 rounded-md hover:bg-plum-100 transition-colors font-medium text-sm;
  }
  .btn-danger {
    @apply bg-danger text-white px-3 py-1.5 rounded-md hover:opacity-90 transition-colors font-medium text-sm;
  }

  /* Inputs */
  .input {
    @apply w-full border border-border-subtle rounded-md px-3 py-2 text-sm bg-white
           focus:outline-none focus:ring-2 focus:ring-plum-700 focus:border-transparent
           hover:border-plum-300;
  }

  /* Badges — colors unchanged, radius matches chips */
  .badge {
    @apply inline-flex items-center px-2 py-0.5 rounded text-xs font-medium;
  }
  .badge-paid       { @apply badge bg-green-100 text-green-800; }
  .badge-denied     { @apply badge bg-red-100 text-red-800; }
  .badge-partial    { @apply badge bg-yellow-100 text-yellow-800; }
  .badge-pending    { @apply badge bg-gray-100 text-gray-800; }
  .badge-appealing  { @apply badge bg-blue-100 text-blue-800; }
  .badge-written_off{ @apply badge bg-plum-100 text-plum-900; }

  /* Tables */
  .table-th {
    @apply px-4 py-3 text-left text-xs font-semibold text-muted uppercase tracking-wider;
  }
  .table-td {
    @apply px-4 py-3 text-sm text-ink;
  }
  .table-row {
    @apply hover:bg-plum-50 border-b border-border-subtle;
  }

  /* Label helper */
  .eyebrow {
    @apply text-[11px] uppercase tracking-wider font-semibold text-muted;
  }
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/index.css
git commit -m "style(frontend): rewrite component layer around plum tokens"
```

---

## Task 5: Build the TopNav component

**Files:**
- Create: `frontend/src/components/layout/TopNav.jsx`

- [ ] **Step 1: Create `frontend/src/components/layout/TopNav.jsx`**

Write this exact content:

```jsx
import { NavLink } from 'react-router-dom'
import { LogOut } from 'lucide-react'
import logoMark from '../../assets/wwc-logo.png'

const nav = [
  { to: '/',          label: 'Dashboard' },
  { to: '/ar',        label: 'A/R' },
  { to: '/documents', label: 'Charts' },
  { to: '/claims',    label: 'Claims' },
  { to: '/denials',   label: 'Denials' },
  { to: '/appeals',   label: 'Appeals' },
  { to: '/import',    label: 'Import' },
  { to: '/audit',     label: 'Audit' },
]

export default function TopNav({ user, onLogout }) {
  return (
    <header className="bg-white border-b border-border-subtle h-[60px] px-6 flex items-center gap-6 sticky top-0 z-10">
      <div className="flex items-center gap-2.5 shrink-0">
        <img src={logoMark} alt="WWC" className="w-8 h-8 object-contain" />
        <div className="leading-tight">
          <div className="font-serif font-semibold text-plum-700 text-[12px] tracking-wordmark">
            WWC GYNECOLOGY
          </div>
          <div className="font-serif italic text-plum-600 text-[11px] -mt-0.5">
            &amp; Aesthetics
          </div>
        </div>
      </div>

      <nav className="flex gap-0.5 text-sm">
        {nav.map(({ to, label }) => (
          <NavLink
            key={to}
            to={to}
            end={to === '/'}
            className={({ isActive }) =>
              `px-3 py-2 -mb-px border-b-2 transition-colors ${
                isActive
                  ? 'text-plum-700 border-plum-700 font-medium'
                  : 'text-muted border-transparent hover:text-plum-700'
              }`
            }
          >
            {label}
          </NavLink>
        ))}
      </nav>

      <div className="ml-auto flex items-center gap-3">
        <span className="bg-plum-100 text-plum-700 px-2.5 py-1 rounded text-[11px] font-medium">
          Maryland · Internal
        </span>
        {user && (
          <div className="flex items-center gap-2">
            {user.picture ? (
              <img src={user.picture} alt="" className="w-8 h-8 rounded-full" />
            ) : (
              <div className="w-8 h-8 rounded-full bg-plum-300 text-plum-ink flex items-center justify-center text-xs font-semibold">
                {(user.name || user.email || '?')[0].toUpperCase()}
              </div>
            )}
            <div className="text-[12px] leading-tight">
              <div className="font-medium text-ink truncate max-w-[160px]">
                {user.name || user.email}
              </div>
              <div className="text-muted truncate max-w-[160px]">{user.email}</div>
            </div>
            <button
              onClick={onLogout}
              className="p-1.5 rounded hover:bg-plum-100 text-muted hover:text-plum-700"
              title="Sign out"
            >
              <LogOut size={16} />
            </button>
          </div>
        )}
      </div>
    </header>
  )
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/layout/TopNav.jsx
git commit -m "feat(frontend): add TopNav shell with WWC wordmark and plum active-state"
```

---

## Task 6: Wire TopNav into App.jsx and retire the Sidebar

**Files:**
- Modify: `frontend/src/App.jsx`
- Delete: `frontend/src/components/layout/Sidebar.jsx`

- [ ] **Step 1: Rewrite `frontend/src/App.jsx`**

Replace the entire file with:

```jsx
import { useState } from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import TopNav from './components/layout/TopNav'
import Dashboard from './pages/Dashboard'
import Claims from './pages/Claims'
import ClaimDetail from './pages/ClaimDetail'
import Patients from './pages/Patients'
import PatientDetail from './pages/PatientDetail'
import Denials from './pages/Denials'
import Appeals from './pages/Appeals'
import ImportFiles from './pages/ImportFiles'
import AuditLog from './pages/AuditLog'
import ARDashboard from './pages/ARDashboard'
import Documents from './pages/Documents'
import PatientChart from './pages/PatientChart'
import { LoginPage, AuthCallback } from './pages/Login'

function ProtectedApp({ user, onLogout }) {
  return (
    <div className="min-h-screen flex flex-col bg-plum-50">
      <TopNav user={user} onLogout={onLogout} />
      <main className="flex-1 overflow-auto">
        <div className="max-w-[1440px] mx-auto p-6">
          <Routes>
            <Route path="/"                    element={<Dashboard />} />
            <Route path="/ar"                  element={<ARDashboard />} />
            <Route path="/documents"           element={<Documents />} />
            <Route path="/chart/:chartNumber"  element={<PatientChart />} />
            <Route path="/claims"              element={<Claims />} />
            <Route path="/claims/:id"          element={<ClaimDetail />} />
            <Route path="/patients"            element={<Patients />} />
            <Route path="/patients/:id"        element={<PatientDetail />} />
            <Route path="/denials"             element={<Denials />} />
            <Route path="/appeals"             element={<Appeals />} />
            <Route path="/import"              element={<ImportFiles />} />
            <Route path="/audit"               element={<AuditLog />} />
            <Route path="*"                    element={<Navigate to="/" />} />
          </Routes>
        </div>
      </main>
    </div>
  )
}

export default function App() {
  const [user, setUser] = useState(() => {
    const saved = localStorage.getItem('user')
    const token = localStorage.getItem('session_token')
    if (saved && token) return JSON.parse(saved)
    return null
  })

  function handleLogin(data) {
    setUser({ email: data.email, name: data.name, picture: data.picture })
  }

  function handleLogout() {
    localStorage.removeItem('session_token')
    localStorage.removeItem('user')
    setUser(null)
  }

  return (
    <Routes>
      <Route path="/login" element={
        user ? <Navigate to="/" /> : <LoginPage onLogin={handleLogin} />
      } />
      <Route path="/auth/callback" element={<AuthCallback onLogin={handleLogin} />} />
      <Route path="/*" element={
        user ? <ProtectedApp user={user} onLogout={handleLogout} /> : <Navigate to="/login" />
      } />
    </Routes>
  )
}
```

- [ ] **Step 2: Delete the old Sidebar**

Run:
```bash
rm frontend/src/components/layout/Sidebar.jsx
```

- [ ] **Step 3: Commit**

```bash
git add -A frontend/src/App.jsx frontend/src/components/layout/
git commit -m "feat(frontend): swap Sidebar for TopNav shell, vertical layout with 1440px workspace"
```

---

## Task 7: Rebrand the Login page

**Files:**
- Modify: `frontend/src/pages/Login.jsx` (only the `LoginPage` export)

- [ ] **Step 1: Replace the `LoginPage` export in `frontend/src/pages/Login.jsx`**

Find the existing `export function LoginPage({ onLogin })` block (roughly lines 23–52) and replace it with:

```jsx
import logoFull from '../assets/wwc-logo-full.png'

export function LoginPage({ onLogin }) {
  return (
    <div className="min-h-screen bg-plum-50 flex items-center justify-center p-6">
      <div className="bg-white rounded-xl border border-border-subtle p-8 w-[420px] text-center">
        <img src={logoFull} alt="WWC Gynecology & Aesthetics" className="w-40 mx-auto mb-6" />
        <div className="mb-6">
          <div className="font-serif text-xl text-ink">Revenue &amp; Records Workspace</div>
          <div className="text-xs text-muted mt-1">Maryland · Internal Use Only</div>
        </div>

        <a
          href={getGoogleAuthUrl()}
          className="inline-flex items-center gap-3 px-6 py-3 bg-white border-2 border-border-subtle rounded-lg hover:border-plum-400 hover:shadow-md transition-all text-sm font-medium text-ink"
        >
          <svg width="20" height="20" viewBox="0 0 48 48">
            <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/>
            <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/>
            <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"/>
            <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/>
          </svg>
          Sign in with Google
        </a>

        <div className="mt-6 text-xs text-muted">
          Access restricted to @waldorfwomenscare.com and @caribcall.com
        </div>
      </div>
    </div>
  )
}
```

The `AuthCallback` export and the helper functions at the top of the file stay unchanged.

- [ ] **Step 2: Also update the `AuthCallback` loading screen background**

Find the `AuthCallback` function and change `className="min-h-screen bg-gray-50 flex items-center justify-center"` to `className="min-h-screen bg-plum-50 flex items-center justify-center"` and `className="text-gray-500 text-sm"` to `className="text-muted text-sm"`.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/Login.jsx
git commit -m "style(frontend): rebrand login page with WWC logo and plum surfaces"
```

---

## Task 8: Add pytest infrastructure and write failing dashboard test

**Files:**
- Create: `backend/tests/__init__.py`
- Create: `backend/tests/conftest.py`
- Create: `backend/tests/test_dashboard.py`
- Modify: `backend/requirements.txt`

- [ ] **Step 1: Add test deps**

Append these lines to `backend/requirements.txt`:
```
pytest
pytest-asyncio
```

- [ ] **Step 2: Install**

Run (from repo root):
```bash
cd backend && source venv/bin/activate && pip install pytest pytest-asyncio
```

Expected: successful install.

- [ ] **Step 3: Create test package init**

Create empty `backend/tests/__init__.py` (zero bytes).

- [ ] **Step 4: Create `backend/tests/conftest.py`**

Write:

```python
"""Shared pytest fixtures: in-memory SQLite + FastAPI TestClient."""
import os
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from app.database import Base, get_db
from app.main import app


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSession()
    yield session
    session.close()
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client(db):
    def override_get_db():
        try:
            yield db
        finally:
            pass
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
```

- [ ] **Step 5: Create `backend/tests/test_dashboard.py`**

Write:

```python
"""Tests for GET /api/dashboard/summary."""


def test_dashboard_summary_empty_db_returns_zeros(client):
    r = client.get("/api/dashboard/summary")
    assert r.status_code == 200
    data = r.json()

    assert data["collected_30d"] == 0
    assert data["collected_prior_30d"] == 0
    assert data["outstanding_total"] == 0
    assert data["outstanding_count"] == 0
    assert data["open_claims"] == 0
    assert data["claims_submitted_7d"] == 0
    assert data["timely_filing_at_risk_7d"] == 0
    assert data["denied_open"] == 0
    assert data["denied_delta_7d"] == 0

    assert data["resolved"] == {
        "30d": {"count": 0, "collected": 0},
        "60d": {"count": 0, "collected": 0},
        "90d": {"count": 0, "collected": 0},
    }
    assert data["attention"] == {
        "timely_filing": 0,
        "eras_unposted": 0,
        "fax_failures": 0,
    }


def test_dashboard_summary_shape_is_complete(client):
    """Contract test: every documented top-level key is present."""
    r = client.get("/api/dashboard/summary")
    assert r.status_code == 200
    expected_keys = {
        "collected_30d", "collected_prior_30d",
        "outstanding_total", "outstanding_count",
        "open_claims", "claims_submitted_7d",
        "timely_filing_at_risk_7d",
        "resolved", "denied_open", "denied_delta_7d",
        "attention",
    }
    assert expected_keys.issubset(r.json().keys())
```

- [ ] **Step 6: Run tests and confirm they fail**

Run (from `backend/`):
```bash
pytest tests/test_dashboard.py -v
```

Expected: both tests FAIL with 404 (endpoint not registered yet). This proves the test is exercising the real app and the endpoint is genuinely missing.

- [ ] **Step 7: Commit (red stage of TDD — failing tests are on purpose)**

```bash
git add backend/requirements.txt backend/tests/
git commit -m "test(backend): add pytest infra + failing contract tests for /dashboard/summary"
```

---

## Task 9: Implement /api/dashboard/summary

**Files:**
- Create: `backend/app/routers/dashboard.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: Create `backend/app/routers/dashboard.py`**

Write:

```python
"""Dashboard aggregate metrics.

All figures are derived from existing tables — no schema changes. Date
windows use SQLite's `date('now', '-N days')` because the production DB
is SQLite and we want the filter pushed to the engine.
"""
from datetime import date
from decimal import Decimal
from fastapi import APIRouter, Depends
from sqlalchemy import func, case
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.claim import Claim, ClaimStatus
from app.models.payment import Payment, PaymentType
from app.models.denial import Denial

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

TERMINAL_STATUSES = (
    ClaimStatus.PAID, ClaimStatus.DENIED, ClaimStatus.WRITTEN_OFF,
    ClaimStatus.REVERSED,
)
RESOLVED_STATUSES = (
    ClaimStatus.PAID, ClaimStatus.WRITTEN_OFF, ClaimStatus.REVERSED,
)
INSURANCE_PAYMENT_TYPES = (
    PaymentType.INSURANCE_PAYMENT, PaymentType.PATIENT_PAYMENT,
)
# Timely filing horizon: Medicare is 365 days, many commercial payers are 90.
# "At risk" = within 7 days of a 90-day horizon from date of service.
TIMELY_FILING_HORIZON_DAYS = 90
TIMELY_FILING_ALERT_DAYS = 7


def _zero_if_none(v):
    return float(v) if v is not None else 0


def _collected_in_window(db: Session, start_offset: int, end_offset: int = 0) -> float:
    """Sum payments with payment_date in [today - start_offset, today - end_offset]."""
    start_expr = func.date('now', f'-{start_offset} days')
    end_expr = func.date('now', f'-{end_offset} days') if end_offset else func.date('now')
    q = db.query(func.coalesce(func.sum(Payment.amount), 0)).filter(
        Payment.payment_type.in_([t.value for t in INSURANCE_PAYMENT_TYPES]),
        Payment.payment_date >= start_expr,
        Payment.payment_date <= end_expr,
    )
    return float(q.scalar() or 0)


def _resolved_window(db: Session, days: int) -> dict:
    """Claims moved to a resolved status within the last `days` days."""
    since = func.date('now', f'-{days} days')
    count = db.query(func.count(Claim.id)).filter(
        Claim.status.in_([s.value for s in RESOLVED_STATUSES]),
        Claim.statement_date >= since,
    ).scalar() or 0
    collected = db.query(func.coalesce(func.sum(Claim.paid_amount), 0)).filter(
        Claim.status.in_([s.value for s in RESOLVED_STATUSES]),
        Claim.statement_date >= since,
    ).scalar() or 0
    return {"count": int(count), "collected": float(collected)}


@router.get("/summary")
def dashboard_summary(db: Session = Depends(get_db)):
    # Collected
    collected_30d = _collected_in_window(db, 30)
    collected_prior_30d = _collected_in_window(db, 60, 30)

    # Outstanding: sum of positive balance on non-terminal claims
    outstanding_q = db.query(
        func.coalesce(func.sum(Claim.balance), 0),
        func.count(Claim.id),
    ).filter(
        Claim.status.notin_([s.value for s in TERMINAL_STATUSES]),
        Claim.balance > 0,
    ).one()
    outstanding_total = float(outstanding_q[0] or 0)
    outstanding_count = int(outstanding_q[1] or 0)

    # Open claims (not terminal)
    open_claims = db.query(func.count(Claim.id)).filter(
        Claim.status.notin_([s.value for s in TERMINAL_STATUSES]),
    ).scalar() or 0

    # Submitted last 7d (using statement_date as submission proxy)
    submitted_7d_since = func.date('now', '-7 days')
    claims_submitted_7d = db.query(func.count(Claim.id)).filter(
        Claim.statement_date >= submitted_7d_since,
    ).scalar() or 0

    # Timely filing: un-submitted open claims whose DOS is within
    # TIMELY_FILING_ALERT_DAYS of the horizon.
    horizon_warn = func.date(
        'now',
        f'-{TIMELY_FILING_HORIZON_DAYS - TIMELY_FILING_ALERT_DAYS} days',
    )
    timely_filing_at_risk = db.query(func.count(Claim.id)).filter(
        Claim.status.notin_([s.value for s in TERMINAL_STATUSES]),
        Claim.date_of_service_from.isnot(None),
        Claim.date_of_service_from <= horizon_warn,
    ).scalar() or 0

    # Denials
    denied_open = db.query(func.count(Denial.id)).filter(
        Denial.resolution_status == 'open',
    ).scalar() or 0
    denied_last_week = db.query(func.count(Denial.id)).filter(
        Denial.resolution_status == 'open',
        Denial.created_at >= func.date('now', '-7 days'),
    ).scalar() or 0

    return {
        "collected_30d": collected_30d,
        "collected_prior_30d": collected_prior_30d,
        "outstanding_total": outstanding_total,
        "outstanding_count": outstanding_count,
        "open_claims": int(open_claims),
        "claims_submitted_7d": int(claims_submitted_7d),
        "timely_filing_at_risk_7d": int(timely_filing_at_risk),
        "resolved": {
            "30d": _resolved_window(db, 30),
            "60d": _resolved_window(db, 60),
            "90d": _resolved_window(db, 90),
        },
        "denied_open": int(denied_open),
        "denied_delta_7d": int(denied_last_week),
        "attention": {
            "timely_filing": int(timely_filing_at_risk),
            "eras_unposted": 0,
            "fax_failures": 0,
        },
    }
```

> **Note on `eras_unposted` and `fax_failures`:** These are wired to `0` in Phase 0. Phase 1 (fax) and Phase 3 (ERA) will compute real values — when they do, only this file changes. The dashboard card reacts to whatever value comes back.

- [ ] **Step 2: Register the router and drop the old stub**

Modify `backend/app/main.py`:

1. At the top with the other imports, change:
   ```python
   from app.routers import waystar, ar, documents, intake, chart, fax, auth
   ```
   to:
   ```python
   from app.routers import waystar, ar, documents, intake, chart, fax, auth, dashboard
   ```

2. Add this line with the other `include_router` calls:
   ```python
   app.include_router(dashboard.router, prefix="/api")
   ```

3. Delete the old stub — remove these lines entirely:
   ```python
   @app.get("/api/dashboard")
   def dashboard(db=None):
       from fastapi import Depends
       return {"message": "Use /api/claims/summary and /api/denials/summary for dashboard data"}
   ```

- [ ] **Step 3: Run tests and confirm they pass**

From `backend/`:
```bash
pytest tests/test_dashboard.py -v
```
Expected: both tests PASS.

- [ ] **Step 4: Smoke-test the running server**

From `backend/`:
```bash
source venv/bin/activate && uvicorn app.main:app --reload --port 8000 &
sleep 2
curl -s http://localhost:8000/api/dashboard/summary | python -m json.tool
kill %1
```
Expected: JSON with every key from the spec. Every numeric value will be `0` if the DB is empty, or real data if not.

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/dashboard.py backend/app/main.py
git commit -m "feat(backend): add /api/dashboard/summary aggregate endpoint"
```

---

## Task 10: Rewrite the Dashboard page

**Files:**
- Modify: `frontend/src/pages/Dashboard.jsx` (full rewrite)

- [ ] **Step 1: Replace `frontend/src/pages/Dashboard.jsx`**

Write:

```jsx
import { useQuery } from '@tanstack/react-query'
import { format } from 'date-fns'
import api, { fmt } from '../utils/api'

function Stat({ label, value, sub, subColor = 'text-muted', accent }) {
  return (
    <div
      className="stat"
      style={accent ? { borderLeft: `3px solid ${accent}` } : undefined}
    >
      <div className="eyebrow">{label}</div>
      <div className="display-number text-[26px] leading-none mt-1">{value}</div>
      {sub && <div className={`text-[11px] mt-1 ${subColor}`}>{sub}</div>}
    </div>
  )
}

function greeting(hour) {
  if (hour < 12) return 'Good morning'
  if (hour < 18) return 'Good afternoon'
  return 'Good evening'
}

export default function Dashboard() {
  const { data, isLoading } = useQuery({
    queryKey: ['dashboard-summary'],
    queryFn: () => api.get('/dashboard/summary').then(r => r.data),
  })

  const { data: faxes } = useQuery({
    queryKey: ['fax-recent'],
    queryFn: () => api.get('/fax/recent?limit=5')
      .then(r => Array.isArray(r.data) ? r.data : [])
      .catch(() => []),  // 404 / error → empty list, card renders empty state
  })

  const now = new Date()
  const delta = data && data.collected_prior_30d > 0
    ? Math.round(((data.collected_30d - data.collected_prior_30d) / data.collected_prior_30d) * 100)
    : null

  return (
    <div>
      {/* Header row */}
      <div className="flex items-baseline justify-between mb-5">
        <div>
          <h1 className="font-serif font-semibold text-ink text-[26px] tracking-tight m-0">
            {greeting(now.getHours())}
          </h1>
          <div className="text-muted text-[13px] mt-0.5">
            {format(now, 'EEEE, MMMM d')} · snapshot as of {format(now, 'h:mm a')}
          </div>
        </div>
        <div className="flex gap-2">
          <button className="btn-secondary" disabled title="Window selector — Phase 2">
            Last 30 days ▾
          </button>
          <a href="/claims" className="btn-primary">+ New claim</a>
        </div>
      </div>

      {/* Hero KPIs */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-3">
        <Stat
          label="Collected · 30d"
          value={data ? fmt.currency(data.collected_30d) : '—'}
          sub={delta !== null ? `${delta >= 0 ? '▲' : '▼'} ${Math.abs(delta)}% vs prior 30` : 'no prior data'}
          subColor={delta !== null && delta >= 0 ? 'text-success' : 'text-muted'}
        />
        <Stat
          label="Outstanding"
          value={data ? fmt.currency(data.outstanding_total) : '—'}
          sub={data ? `across ${data.outstanding_count.toLocaleString()} charges` : ''}
        />
        <Stat
          label="Open claims"
          value={data ? data.open_claims.toLocaleString() : '—'}
          sub={data ? `${data.claims_submitted_7d} submitted this week` : ''}
        />
        <Stat
          label="Timely filing · ≤7d"
          value={data ? data.timely_filing_at_risk_7d.toLocaleString() : '—'}
          sub={data && data.timely_filing_at_risk_7d > 0 ? 'needs submission' : 'clear'}
          subColor={data && data.timely_filing_at_risk_7d > 0 ? 'text-danger' : 'text-success'}
          accent={data && data.timely_filing_at_risk_7d > 0 ? '#C62828' : undefined}
        />
      </div>

      {/* Resolved by window + denials */}
      <div className="grid grid-cols-3 gap-3 mb-3">
        <div className="card col-span-2">
          <div className="flex items-center justify-between mb-3">
            <h2 className="font-serif font-semibold text-ink text-[15px] m-0">Claims resolved</h2>
            <div className="text-[11px] text-muted">by window</div>
          </div>
          <div className="grid grid-cols-3 gap-4">
            {(['30d', '60d', '90d']).map(k => (
              <div key={k}>
                <div className="eyebrow">Last {k}</div>
                <div className="display-number text-[20px] mt-1">
                  {data ? data.resolved[k].count.toLocaleString() : '—'}
                </div>
                <div className="text-[11px] text-muted">
                  {data ? `${fmt.currency(data.resolved[k].collected)} collected` : ''}
                </div>
              </div>
            ))}
          </div>
        </div>
        <Stat
          label="Denied claims"
          value={data ? data.denied_open.toLocaleString() : '—'}
          sub={data && data.denied_delta_7d > 0 ? `▲ ${data.denied_delta_7d} since last week` : 'no new denials'}
          subColor={data && data.denied_delta_7d > 0 ? 'text-danger' : 'text-muted'}
        />
      </div>

      {/* Recent faxes + attention */}
      <div className="grid grid-cols-2 gap-3">
        <div className="card">
          <h2 className="font-serif font-semibold text-ink text-[15px] m-0 mb-2">
            Recent faxes to EMA
          </h2>
          {faxes && faxes.length > 0 ? (
            <div>
              {faxes.map(f => (
                <div
                  key={f.id}
                  className="text-[12px] text-ink flex justify-between py-1.5 border-b border-plum-100 last:border-b-0"
                >
                  <span>{f.patient_name || f.chart_number}</span>
                  <span className={f.status === 'failed' ? 'text-warning' : 'text-success'}>
                    {f.status === 'sent' ? `✓ ${f.sent_at ? format(new Date(f.sent_at), 'h:mm a') : ''}` : f.status}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-[12px] text-muted py-6 text-center">No recent faxes yet.</div>
          )}
        </div>

        <div className="card">
          <h2 className="font-serif font-semibold text-ink text-[15px] m-0 mb-2">
            Needs your attention
          </h2>
          {data ? (
            <div className="text-[13px] text-ink">
              <div className="py-1.5 border-b border-plum-100 flex justify-between">
                <span>Claims approaching timely filing</span>
                <span className="font-medium">{data.attention.timely_filing}</span>
              </div>
              <div className="py-1.5 border-b border-plum-100 flex justify-between">
                <span>ERAs waiting to be posted</span>
                <span className="font-medium">{data.attention.eras_unposted}</span>
              </div>
              <div className="py-1.5 flex justify-between">
                <span>Fax failures to retry</span>
                <span className="font-medium">{data.attention.fax_failures}</span>
              </div>
            </div>
          ) : (
            <div className="text-[12px] text-muted py-6 text-center">
              {isLoading ? 'Loading...' : 'Dashboard unavailable.'}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Verify the page renders (dev smoke)**

From repo root:
```bash
./start.sh
```
Then open `http://localhost:3000`. Log in. Expected: Dashboard shows the new layout with your real data. Empty DB renders zeros (no crashes).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/Dashboard.jsx
git commit -m "feat(frontend): redesign Dashboard around /dashboard/summary with plum palette"
```

---

## Task 11: Cross-page regression smoke test

**Files:** none — verification only.

- [ ] **Step 1: Start the full stack**

From repo root:
```bash
./start.sh
```

- [ ] **Step 2: Walk every route and confirm no crashes**

Visit each of these URLs in the browser and confirm the page loads, the top nav highlights the correct item, buttons are plum (not navy), tables have plum hover rows, and no console errors appear:

- `http://localhost:3000/` (Dashboard)
- `http://localhost:3000/ar`
- `http://localhost:3000/documents`
- `http://localhost:3000/claims`
- `http://localhost:3000/denials`
- `http://localhost:3000/appeals`
- `http://localhost:3000/import`
- `http://localhost:3000/audit`
- `http://localhost:3000/patients`
- `http://localhost:3000/login` (sign out first) — confirm full-logo login page

- [ ] **Step 3: Fix any regressions inline**

If a page is broken by the palette rename (for example, a page uses a hardcoded `bg-blue-*` that no longer matches), patch it in the same task — scope is narrow: only fix breakage, do not redesign the page. Record each fix as a separate commit:

```bash
git add <files>
git commit -m "fix(frontend): <page> — <what broke>"
```

- [ ] **Step 4: Final commit**

If no fixes were needed:
```bash
git commit --allow-empty -m "test: Phase 0 UI redesign verified across all routes"
```

---

## Self-review results

- **Spec coverage:** ✓ Every spec section has at least one task. Palette (T2), typography (T3), logo (pre-copied + T5/T7), component library (T4), top-nav shell (T5/T6), dashboard redesign (T10), backend endpoint (T8/T9), login rebrand (T7), cross-page inheritance check (T11).
- **Placeholder scan:** ✓ No "TBD", no "add appropriate error handling", no "implement later." `eras_unposted = 0` / `fax_failures = 0` is an explicit, documented Phase-0 behavior, not a placeholder.
- **Type consistency:** ✓ Endpoint shape in T8 contract test matches the JSON returned in T9 and the fields consumed in T10. `TopNav` signature `{ user, onLogout }` matches what `App.jsx` passes in T6.
- **Git prereq:** ✓ T1 initializes the repo before any commit step runs.
