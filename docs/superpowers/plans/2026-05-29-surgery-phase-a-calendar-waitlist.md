# Surgery Phase A — Calendar + Waitlist Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a monthly calendar view at `/surgery/calendar` (week stays embedded on `/surgery`) and surface the 5 columns the waitlist needs (patient, notice, procedure, facility, urgency).

**Architecture:** Pure UI for the calendar; small backend additions for the urgency enum + waitlist endpoint extension. No new tables.

**Tech Stack:** FastAPI + SQLAlchemy, Postgres in prod / SQLite in tests, React + TanStack Query, Tailwind, lucide-react icons, pytest, Playwright smoke.

---

## File Structure

- **Create:** `backend/tests/test_surgery_waitlist_columns.py` — backend coverage for the extended waitlist payload.
- **Modify:** `backend/app/models/surgery.py` — add `urgency` to `Surgery`.
- **Modify:** `backend/app/routers/surgery.py` — extend `/surgery/admin/waitlist` payload + accept `urgency` on the patch endpoint.
- **Modify:** `frontend/src/pages/SurgeryCalendar.jsx` — new `MonthlyCalendar` component, default view = month.
- **Modify:** `frontend/src/pages/SurgeryWaitlist.jsx` — 4 new columns + sort.
- **Modify:** `frontend/src/pages/SurgeryDetail.jsx` — urgency edit control.

---

## Section 1 — Backend: `Surgery.urgency` field

### Task A1: Add `urgency` column to Surgery model

**Files:**
- Modify: `backend/app/models/surgery.py` (insert near other top-level Surgery enums, ~line 88)

- [ ] **Step 1: Add the column on the `Surgery` model**

In `backend/app/models/surgery.py`, locate the Surgery class (line 35) and find the block around line 88–90 where `procedure_classification` is defined. Add right below it:

```python
    # Waitlist urgency. `routine` is the default; `expedited` and `urgent`
    # surface in the waitlist sort + UI accent. Used only as a sort key —
    # it does not gate scheduling.
    urgency = Column(String(20), default="routine", nullable=False)
    # values: routine | expedited | urgent
```

- [ ] **Step 2: Add a module-level constant for the allowed values**

Near the top of `backend/app/models/surgery.py` (just below the imports, before the first model), add:

```python
SURGERY_URGENCY_VALUES = ("routine", "expedited", "urgent")
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/models/surgery.py
git commit -m "feat(surgery): add urgency field (routine|expedited|urgent)"
```

---

### Task A2: Extend `/surgery/admin/waitlist` payload + accept urgency on patch

**Files:**
- Create: `backend/tests/test_surgery_waitlist_columns.py`
- Modify: `backend/app/routers/surgery.py` — `list_waitlist` handler + `patch_surgery` handler

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_surgery_waitlist_columns.py`:

```python
"""Coverage for the extended waitlist payload (Phase A)."""
from datetime import date

from app.models.surgery import Surgery, SurgeryWaitlist


def _make_surgery(db, **kw):
    s = Surgery(
        chart_number="1234",
        patient_name="Jane Doe",
        procedures=[{"name": "Hysterectomy", "cpt": "58150"}],
        eligible_facilities=["medstar", "office"],
        selected_facility="medstar",
        urgency=kw.pop("urgency", "routine"),
        status="in_progress",
    )
    for k, v in kw.items():
        setattr(s, k, v)
    db.add(s); db.flush()
    return s


def test_waitlist_returns_new_columns(client, db):
    s = _make_surgery(db, urgency="urgent")
    db.add(SurgeryWaitlist(surgery_id=s.id, advance_notice_days=10))
    db.commit()

    resp = client.get("/api/surgery/admin/waitlist")
    assert resp.status_code == 200, resp.text
    rows = resp.json()["waitlist"]
    assert len(rows) == 1
    row = rows[0]
    assert row["patient_name"] == "Jane Doe"
    assert row["advance_notice_days"] == 10
    assert row["procedure_name"] == "Hysterectomy"
    assert row["facility"] == "medstar"
    assert row["urgency"] == "urgent"


def test_waitlist_facility_falls_back_to_first_eligible(client, db):
    s = _make_surgery(db, selected_facility=None,
                       eligible_facilities=["office", "crmc"])
    db.add(SurgeryWaitlist(surgery_id=s.id, advance_notice_days=5))
    db.commit()

    rows = client.get("/api/surgery/admin/waitlist").json()["waitlist"]
    assert rows[0]["facility"] == "office"


def test_patch_surgery_accepts_urgency(client, db):
    s = _make_surgery(db, urgency="routine")
    db.commit()

    resp = client.patch(f"/api/surgery/{s.id}", json={"urgency": "expedited"})
    assert resp.status_code == 200, resp.text
    db.refresh(s)
    assert s.urgency == "expedited"


def test_patch_surgery_rejects_bogus_urgency(client, db):
    s = _make_surgery(db)
    db.commit()

    resp = client.patch(f"/api/surgery/{s.id}", json={"urgency": "panic"})
    assert resp.status_code == 422
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd backend && ./venv/bin/pytest tests/test_surgery_waitlist_columns.py -v
```

Expected: 4 failures (waitlist payload missing fields, patch endpoint not accepting urgency).

- [ ] **Step 3: Extend the waitlist endpoint payload**

In `backend/app/routers/surgery.py`, find the waitlist endpoint (search for `@router.get("/admin/waitlist")` — the handler is `list_waitlist`). Locate the per-row dict construction and add three keys:

```python
        # ... inside the per-row dict ...
        "patient_name":       s.patient_name,
        "procedure_name":     (s.procedures[0].get("name") if s.procedures else None),
        "facility":           (s.selected_facility
                                 or (s.eligible_facilities[0]
                                      if s.eligible_facilities else None)),
        "urgency":            s.urgency,
        # ... existing keys ...
```

(Reuse the existing column-list pattern in the handler — don't restructure it.)

- [ ] **Step 4: Accept urgency on the patch endpoint**

Find the `SurgeryPatch` Pydantic model in `backend/app/routers/surgery.py` (or wherever the patch payload lives — likely in the same router). Add:

```python
class SurgeryPatch(BaseModel):
    # ... existing fields ...
    urgency: Optional[str] = None
```

In the `patch_surgery` handler, after `data = payload.model_dump(exclude_unset=True)`, add:

```python
    if "urgency" in data:
        from app.models.surgery import SURGERY_URGENCY_VALUES
        if data["urgency"] not in SURGERY_URGENCY_VALUES:
            raise HTTPException(status_code=422,
                                detail=f"unknown urgency: {data['urgency']}")
        s.urgency = data["urgency"]
```

- [ ] **Step 5: Re-run tests**

```bash
cd backend && ./venv/bin/pytest tests/test_surgery_waitlist_columns.py -v
```

Expected: 4 passes.

- [ ] **Step 6: Commit**

```bash
git add backend/app/routers/surgery.py backend/tests/test_surgery_waitlist_columns.py
git commit -m "feat(surgery): waitlist payload exposes procedure/facility/urgency; patch accepts urgency"
```

---

## Section 2 — Frontend: Waitlist columns

### Task A3: Add Notice, Type, Location, Urgency columns to `/surgery/waitlist`

**Files:**
- Modify: `frontend/src/pages/SurgeryWaitlist.jsx`

- [ ] **Step 1: Add urgency styling constants near the top**

In `frontend/src/pages/SurgeryWaitlist.jsx`, near the existing `FACILITY_LABEL` constant, add:

```jsx
const URGENCY_TONE = {
  routine:   'bg-gray-100 text-gray-700',
  expedited: 'bg-amber-100 text-amber-800',
  urgent:    'bg-red-100 text-red-700',
}
const URGENCY_LABEL = {
  routine: 'Routine', expedited: 'Expedited', urgent: 'Urgent',
}
const URGENCY_RANK = { urgent: 0, expedited: 1, routine: 2 }
```

- [ ] **Step 2: Add sort state**

Inside the `SurgeryWaitlist` component (after the existing `useState` calls), add:

```jsx
  const [sortKey, setSortKey] = useState('urgency')   // 'urgency' | 'notice' | 'facility'
  const [sortDir, setSortDir] = useState('asc')
```

- [ ] **Step 3: Apply sort to the list**

Find the `const list = data?.waitlist || []` line and replace it with:

```jsx
  const list = useMemo(() => {
    const rows = [...(data?.waitlist || [])]
    rows.sort((a, b) => {
      let av, bv
      if (sortKey === 'urgency')  { av = URGENCY_RANK[a.urgency] ?? 99; bv = URGENCY_RANK[b.urgency] ?? 99 }
      else if (sortKey === 'notice') { av = a.advance_notice_days ?? 0; bv = b.advance_notice_days ?? 0 }
      else                        { av = (a.facility || ''); bv = (b.facility || '') }
      if (av < bv) return sortDir === 'asc' ? -1 : 1
      if (av > bv) return sortDir === 'asc' ?  1 : -1
      return 0
    })
    return rows
  }, [data, sortKey, sortDir])
```

- [ ] **Step 4: Add column headers + cells in the table render**

Find the existing table render (search for the `<table>` element or `<tr>` rendering in the file). Replace the row rendering with this 5-column layout (keep the existing facility-grouping logic if it's there, or simplify to a flat table per the spec):

```jsx
  <table className="w-full text-sm">
    <thead className="bg-gray-50 text-[11px] uppercase text-gray-500">
      <tr>
        <th className="text-left px-4 py-2">Patient</th>
        <th className="text-left px-3 py-2">
          <button onClick={() => { setSortKey('notice'); setSortDir(d => d === 'asc' ? 'desc' : 'asc') }}>
            Notice {sortKey === 'notice' && (sortDir === 'asc' ? '↑' : '↓')}
          </button>
        </th>
        <th className="text-left px-3 py-2">Type</th>
        <th className="text-left px-3 py-2">
          <button onClick={() => { setSortKey('facility'); setSortDir(d => d === 'asc' ? 'desc' : 'asc') }}>
            Location {sortKey === 'facility' && (sortDir === 'asc' ? '↑' : '↓')}
          </button>
        </th>
        <th className="text-left px-3 py-2">
          <button onClick={() => { setSortKey('urgency'); setSortDir(d => d === 'asc' ? 'desc' : 'asc') }}>
            Urgency {sortKey === 'urgency' && (sortDir === 'asc' ? '↑' : '↓')}
          </button>
        </th>
        <th className="px-4 py-2 w-[120px] text-right">Actions</th>
      </tr>
    </thead>
    <tbody>
      {list.map(w => (
        <tr key={w.id} className="border-t border-border-subtle hover:bg-gray-50">
          <td className="px-4 py-3">
            <Link to={`/surgery/${w.surgery_id}`} className="text-plum-700 hover:underline">
              {w.patient_name}
            </Link>
          </td>
          <td className="px-3 py-3 text-[12px]">{w.advance_notice_days}d</td>
          <td className="px-3 py-3 text-[12px]">{w.procedure_name || '—'}</td>
          <td className="px-3 py-3 text-[12px]">{FACILITY_LABEL[w.facility] || w.facility || '—'}</td>
          <td className="px-3 py-3">
            <span className={`text-[11px] px-2 py-0.5 rounded ${URGENCY_TONE[w.urgency] || URGENCY_TONE.routine}`}>
              {URGENCY_LABEL[w.urgency] || 'Routine'}
            </span>
          </td>
          <td className="px-4 py-3 text-right">
            <button className="text-[11px] px-2 py-1 rounded border border-gray-200 hover:bg-plum-50"
                    onClick={() => removeFromWaitlist.mutate(w.surgery_id)}
                    title="Remove from waitlist">
              <Trash2 size={11} />
            </button>
          </td>
        </tr>
      ))}
    </tbody>
  </table>
```

- [ ] **Step 5: Verify the build passes**

```bash
cd frontend && npm run build 2>&1 | tail -5
```

Expected: build succeeds (warnings about chunk size are OK; no errors).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/pages/SurgeryWaitlist.jsx
git commit -m "feat(surgery): waitlist table — Patient/Notice/Type/Location/Urgency columns"
```

---

### Task A4: Add urgency edit to `SurgeryDetail`

**Files:**
- Modify: `frontend/src/pages/SurgeryDetail.jsx`

- [ ] **Step 1: Find the appropriate section**

In `frontend/src/pages/SurgeryDetail.jsx`, locate the existing block of editable surgery fields (search for a pattern like `procedure_classification` or `eligible_facilities` editor — the urgency control belongs in the same group).

- [ ] **Step 2: Add the urgency dropdown**

Add this block alongside the other field editors (use existing `patchMut` pattern — don't introduce a new mutation):

```jsx
<div className="flex items-center gap-2">
  <label className="text-[11px] uppercase text-gray-500 w-24">Urgency</label>
  <select className="input text-sm"
          value={s.urgency || 'routine'}
          onChange={e => patchMut.mutate({ urgency: e.target.value })}>
    <option value="routine">Routine</option>
    <option value="expedited">Expedited</option>
    <option value="urgent">Urgent</option>
  </select>
</div>
```

- [ ] **Step 3: Verify the build passes**

```bash
cd frontend && npm run build 2>&1 | tail -5
```

Expected: success.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/SurgeryDetail.jsx
git commit -m "feat(surgery): urgency dropdown on SurgeryDetail"
```

---

## Section 3 — Frontend: Monthly calendar

### Task A5: Add `MonthlyCalendar` component + Week/Month toggle

**Files:**
- Modify: `frontend/src/pages/SurgeryCalendar.jsx`

- [ ] **Step 1: Add date helpers if they don't already exist**

At the top of `frontend/src/pages/SurgeryCalendar.jsx`, ensure these helpers are present (most are already there; add what's missing):

```jsx
function startOfMonthGrid(iso) {
  // Returns the Monday of the week that contains the first of `iso`'s month.
  const [y, m] = iso.split('-').map(n => parseInt(n, 10))
  const first = new Date(y, m - 1, 1)
  const wd = (first.getDay() + 6) % 7  // 0=Mon, 6=Sun
  first.setDate(first.getDate() - wd)
  return isoDate(first)
}
function monthLabel(iso) {
  const [y, m] = iso.split('-').map(n => parseInt(n, 10))
  return new Date(y, m - 1, 1).toLocaleString('en-US', { month: 'long', year: 'numeric' })
}
function addMonths(iso, n) {
  const [y, m] = iso.split('-').map(x => parseInt(x, 10))
  const dt = new Date(y, m - 1 + n, 1)
  return isoDate(dt)
}
function inSameMonth(iso, anchorIso) {
  return iso.slice(0, 7) === anchorIso.slice(0, 7)
}
```

- [ ] **Step 2: Define the MonthlyCalendar component**

Add this component below the existing `WeeklyCalendar`:

```jsx
export function MonthlyCalendar() {
  const navigate = useNavigate()
  const [anchor, setAnchor] = useState(() => isoDate(new Date()))   // first of current month-ish
  const gridStart = useMemo(() => startOfMonthGrid(anchor), [anchor])
  const gridEnd = useMemo(() => addDays(gridStart, 41), [gridStart])  // 6 rows × 7 cols - 1

  const { data, isLoading } = useQuery({
    queryKey: ['surgery-calendar', gridStart, gridEnd],
    queryFn: () => api.get('/surgery/calendar', {
      params: { start: gridStart, end: gridEnd },
    }).then(r => r.data),
  })

  // Build day → surgeries map.
  const byDay = useMemo(() => {
    const m = {}
    for (const s of (data?.surgeries || [])) {
      const k = s.scheduled_date
      if (!k) continue
      if (!m[k]) m[k] = []
      m[k].push(s)
    }
    return m
  }, [data])

  const days = Array.from({ length: 42 }, (_, i) => addDays(gridStart, i))

  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <button className="btn-secondary text-sm flex items-center gap-1"
                  onClick={() => setAnchor(a => addMonths(a, -1))}>
            <ChevronLeft size={14} /> Prev
          </button>
          <button className="btn-secondary text-sm"
                  onClick={() => setAnchor(isoDate(new Date()))}>Today</button>
          <button className="btn-secondary text-sm flex items-center gap-1"
                  onClick={() => setAnchor(a => addMonths(a, 1))}>
            Next <ChevronRight size={14} />
          </button>
        </div>
        <h2 className="text-lg font-semibold text-gray-900">{monthLabel(anchor)}</h2>
        <div></div>
      </div>

      <div className="grid grid-cols-7 text-[11px] uppercase text-gray-500 mb-1">
        {WEEKDAY_LABELS.map(d => (
          <div key={d} className="text-center py-1">{d}</div>
        ))}
      </div>
      <div className="grid grid-cols-7 border-t border-l border-border-subtle">
        {days.map(iso => {
          const surgs = byDay[iso] || []
          const isToday = iso === isoDate(new Date())
          const dim = !inSameMonth(iso, anchor)
          return (
            <div key={iso}
                 className={`min-h-[110px] border-r border-b border-border-subtle p-1 ${
                   dim ? 'bg-gray-50 text-gray-400' : 'bg-white'
                 } ${isToday ? 'ring-2 ring-plum-400 ring-inset' : ''}`}>
              <div className="text-[11px] font-semibold mb-1">{iso.slice(-2)}</div>
              {surgs.slice(0, 6).map(s => {
                const fac = FACILITY_BADGE[s.facility] || { label: s.facility, tone: 'bg-gray-100 text-gray-700 border-gray-200' }
                return (
                  <button key={s.id} onClick={() => navigate(`/surgery/${s.id}`)}
                          className={`block w-full text-left text-[10px] truncate border rounded mb-0.5 px-1 py-0.5 ${fac.tone} hover:opacity-80`}>
                    <span className={`inline-block w-1.5 h-1.5 rounded-full mr-1 ${INDICATOR_TONE[s.indicator] || 'bg-gray-400'}`} />
                    {s.patient_name}
                  </button>
                )
              })}
              {surgs.length > 6 && (
                <div className="text-[10px] text-plum-700">+{surgs.length - 6} more</div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
```

- [ ] **Step 3: Wire the route's default to month + add a toggle**

Find the default export of `SurgeryCalendar.jsx` (a component currently rendering `<WeeklyCalendar />`). Replace it with a view-toggle wrapper:

```jsx
import { useSearchParams } from 'react-router-dom'

export default function SurgeryCalendarPage() {
  const [params, setParams] = useSearchParams()
  const view = params.get('view') === 'week' ? 'week' : 'month'

  function setView(v) {
    const next = new URLSearchParams(params)
    next.set('view', v)
    setParams(next, { replace: true })
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
          <CalIcon size={20} /> Surgery Calendar
        </h1>
        <div className="inline-flex rounded border border-border-subtle overflow-hidden text-sm">
          <button className={`px-3 py-1 ${view === 'month' ? 'bg-plum-600 text-white' : 'bg-white text-gray-700 hover:bg-plum-50'}`}
                  onClick={() => setView('month')}>Month</button>
          <button className={`px-3 py-1 ${view === 'week' ? 'bg-plum-600 text-white' : 'bg-white text-gray-700 hover:bg-plum-50'}`}
                  onClick={() => setView('week')}>Week</button>
        </div>
      </div>
      {view === 'month' ? <MonthlyCalendar /> : <WeeklyCalendar />}
    </div>
  )
}
```

(Remove the old default export above this new one.)

- [ ] **Step 4: Verify the build**

```bash
cd frontend && npm run build 2>&1 | tail -8
```

Expected: success.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/SurgeryCalendar.jsx
git commit -m "feat(surgery): monthly calendar view at /surgery/calendar with Week/Month toggle"
```

---

## Section 4 — Verification

### Task A6: Manual + Playwright verification

- [ ] **Step 1: Build + deploy backend if not already containing the urgency field**

```bash
cd backend && gcloud builds submit . --project=wwc-solutions --region=us-east4 \
  --tag=us-east4-docker.pkg.dev/wwc-solutions/app/backend:v23
gcloud run deploy backend --image=us-east4-docker.pkg.dev/wwc-solutions/app/backend:v23 \
  --region=us-east4 --project=wwc-solutions
```

(The `urgency` column is added to the table by `init_db()` on cold start — `create_all` is additive for new columns? **NO**, SQLAlchemy's `create_all` only creates *tables*, not new columns. You need a one-shot `ALTER TABLE` migration before the deploy. See Step 2.)

- [ ] **Step 2: Apply the column to production**

Use the Cloud SQL temporary-public-IP path (the same one used for pellet backfills):

```bash
# (one-shot)
export PATH="/opt/homebrew/share/google-cloud-sdk/bin:$PATH"
CURRENT_IP=$(curl -s https://api.ipify.org)
gcloud sql instances patch app-db --project=wwc-solutions --assign-ip \
  --authorized-networks=$CURRENT_IP/32 --quiet
PUBIP=$(gcloud sql instances describe app-db --project=wwc-solutions --format=json | \
        python3 -c "import sys,json; d=json.load(sys.stdin); print(next(ip['ipAddress'] for ip in d['ipAddresses'] if ip['type']=='PRIMARY'))")
export PGPASSWORD=$(gcloud secrets versions access latest --secret=cloudsql-postgres-root-password --project=wwc-solutions)
psql "host=$PUBIP user=postgres dbname=wwc_app sslmode=require" -c \
  "ALTER TABLE surgeries ADD COLUMN IF NOT EXISTS urgency VARCHAR(20) NOT NULL DEFAULT 'routine';"
unset PGPASSWORD
# Lock the IP back down.
gcloud sql instances patch app-db --project=wwc-solutions --no-assign-ip \
  --clear-authorized-networks --quiet
```

- [ ] **Step 3: Build + deploy frontend**

```bash
cd frontend && gcloud builds submit . --project=wwc-solutions --region=us-east4 \
  --tag=us-east4-docker.pkg.dev/wwc-solutions/app/frontend:v21
gcloud run deploy frontend --image=us-east4-docker.pkg.dev/wwc-solutions/app/frontend:v21 \
  --region=us-east4 --project=wwc-solutions
```

- [ ] **Step 4: Smoke-test in the browser**

Hit `https://gw.waldorfwomenscare.com/surgery/calendar` — confirm the month grid renders with 6 rows × 7 cols, today is ring-highlighted, dim cells outside the month are visible, and clicking a surgery navigates to `/surgery/:id`.

Hit `https://gw.waldorfwomenscare.com/surgery/waitlist` — confirm the 5 new columns render, sort buttons work (urgency, notice, location), and the urgency pill colors match (gray/amber/red).

Hit `https://gw.waldorfwomenscare.com/surgery/:id` for any in-progress surgery — confirm the **Urgency** dropdown is present and changes persist.

Hit `https://gw.waldorfwomenscare.com/surgery` — confirm the **embedded** weekly calendar is unchanged.

- [ ] **Step 5: Push**

```bash
git push origin main
```
