# Missing Charges Triage Reminder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A weekly (Thu 8am ET) reminder to configured biller recipient(s) — email + Slack DM — whenever untriaged `new` missing-charge rows exist, plus an always-on in-app banner on the Missing Charges page.

**Architecture:** A new `missing_charges_triage` service (recipients setting in `PracticeConfig` + a `send_triage_reminders` sweep using the existing `send_email`/`send_slack_dm` helpers). A weekly cron mirrors the existing `missing_charges_weekly` wiring (APScheduler + `jobs/run.py` + Cloud Run Job + `claim_cron_run` idempotency). Frontend adds a banner + a recipients settings input, both on the Missing Charges page.

**Tech Stack:** FastAPI, SQLAlchemy, pytest; React + Vite, @tanstack/react-query. Datetimes via `app.utils.dt.now_utc_naive`.

**Spec:** `docs/superpowers/specs/2026-06-22-missing-charges-triage-reminder-design.md`

---

## Background for the implementer

- **Untriaged = `MissingCharge.status == "new"`.** `MissingCharge.created_at` exists (`backend/app/models/missing_charge.py:90`) → use it for "oldest X days". Nothing auto-promotes `new`; a biller does it manually.
- **Notification helpers** (`backend/app/services/checklist_notifications.py`, all verified live 2026-06-22):
  - `send_email(to: str, subject: str, html_body: str, text_body: str = "") -> bool` (SMTP)
  - `send_slack_dm(user: User, text: str, blocks: list = None) -> bool` (no-ops gracefully if the user has no Slack)
- **Settings store:** `PracticeConfig` (`backend/app/models/practice_config.py`) — KV: `key` PK, `value` VARCHAR(500).
- **Base URL:** `app.services.missing_charges_email._app_base_url()` reads `APP_BASE_URL`.
- **Idempotency:** `claim_cron_run(db, job_name, run_key) -> bool` (`backend/app/services/cron_lock.py`) — returns True iff this caller won the claim.
- **Cron pattern to mirror** (`backend/app/services/fax_poller.py:359-361`):
  ```python
  sched.add_job(_missing_charges_weekly_emails, "cron",
                day_of_week="mon", hour=8, minute=0,
                id="missing_charges_weekly", max_instances=1, coalesce=True)
  ```
  Use `logging.getLogger(__name__)` — a bare `log` caused a NameError in the sibling job (fixed 2026-06-22).
- **Job registry:** `backend/app/jobs/run.py` (`@register("name")`). **Provisioner:** `scripts/migrate/create_cloud_run_jobs.sh` (`JOBS` array; Thursday = cron dow `4`).
- **Frontend page:** `frontend/src/pages/MissingCharges.jsx` already fetches the dashboard summary as `dash` (`useQuery` ~line 99) and renders status chips using `dash.by_status[s.v]`; clicking a chip does `setFilters({ ...filters, status: s.v, open_only: false })`. The status summary comes from `GET /missing-charges/dashboard` → `by_status` (`backend/app/routers/missing_charges.py:245-262`), MISSING_CHARGES VIEW-gated.
- **User model:** `from app.models.user import User`. Tier-grant test pattern: see `backend/tests/test_manual_api.py` (`client_factory`, `_grant` with `UserModuleOverride(..., added_by="test")`, LARC="device_larc"; MISSING_CHARGES = `"billing_missing_charges"`; VIEW=10/WORK=20/MANAGE=30).
- Run one test: `cd backend && ./venv/bin/python -m pytest tests/<file>::<name> -v`. Frontend: `cd frontend && npm run build`.

## File Structure

- **Create** `backend/app/services/missing_charges_triage.py` — recipients setting accessor + `send_triage_reminders` sweep.
- **Modify** `backend/app/routers/missing_charges.py` — `GET`/`PUT /triage-recipients`.
- **Modify** `backend/app/services/fax_poller.py` — `_missing_charges_triage_reminder()` + scheduler entry.
- **Modify** `backend/app/jobs/run.py` — register the job.
- **Modify** `scripts/migrate/create_cloud_run_jobs.sh` — JOBS entry.
- **Modify** `frontend/src/pages/MissingCharges.jsx` — banner + recipients input.
- **Tests:** `backend/tests/test_missing_charges_triage.py`.

---

### Task 1: Recipients setting accessor + endpoint

**Files:**
- Create: `backend/app/services/missing_charges_triage.py`
- Modify: `backend/app/routers/missing_charges.py`
- Test: `backend/tests/test_missing_charges_triage.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_missing_charges_triage.py
from app.services.missing_charges_triage import (
    get_triage_recipients, set_triage_recipients, TRIAGE_RECIPIENTS_KEY,
)


def test_recipients_roundtrip(db):
    assert get_triage_recipients(db) == []
    set_triage_recipients(db, "a@wwc.com, b@wwc.com ,")
    assert get_triage_recipients(db) == ["a@wwc.com", "b@wwc.com"]


def test_recipients_endpoint(client, db):
    # super-admin `client` passes the MANAGE gate
    r = client.put("/api/missing-charges/triage-recipients",
                   json={"recipients": ["x@wwc.com"]})
    assert r.status_code == 200
    g = client.get("/api/missing-charges/triage-recipients")
    assert g.status_code == 200
    assert g.json()["recipients"] == ["x@wwc.com"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_missing_charges_triage.py -v`
Expected: FAIL — `ModuleNotFoundError: app.services.missing_charges_triage`.

- [ ] **Step 3: Create the service accessor**

```python
# backend/app/services/missing_charges_triage.py
"""Triage reminder for untriaged (status='new') missing-charge rows.

Weekly, if any `new` rows exist, email + Slack-DM the configured biller
recipient(s). Recipients live in PracticeConfig (a single CSV value).
"""
import logging
from sqlalchemy.orm import Session

from app.models.practice_config import PracticeConfig

TRIAGE_RECIPIENTS_KEY = "missing_charges_triage_recipients"
log = logging.getLogger(__name__)


def get_triage_recipients(db: Session) -> list[str]:
    row = (db.query(PracticeConfig)
             .filter(PracticeConfig.key == TRIAGE_RECIPIENTS_KEY).first())
    if not row or not row.value:
        return []
    return [e.strip() for e in row.value.split(",") if e.strip()]


def set_triage_recipients(db: Session, value: str) -> None:
    csv = ",".join(e.strip() for e in (value or "").split(",") if e.strip())
    row = (db.query(PracticeConfig)
             .filter(PracticeConfig.key == TRIAGE_RECIPIENTS_KEY).first())
    if row:
        row.value = csv
    else:
        db.add(PracticeConfig(key=TRIAGE_RECIPIENTS_KEY, value=csv))
    db.commit()
```

- [ ] **Step 4: Add the endpoint**

In `backend/app/routers/missing_charges.py`, add (mirror the existing handlers' `requires_tier(Module.MISSING_CHARGES, ...)` style; `BaseModel`/`List` are already imported there — confirm and add if missing):

```python
class TriageRecipientsIn(BaseModel):
    recipients: list[str] = []


@router.get("/triage-recipients")
def get_triage_recipients_endpoint(
        db: Session = Depends(get_db),
        current_user: dict = Depends(requires_tier(Module.MISSING_CHARGES, Tier.MANAGE))):
    from app.services.missing_charges_triage import get_triage_recipients
    return {"recipients": get_triage_recipients(db)}


@router.put("/triage-recipients")
def put_triage_recipients_endpoint(
        payload: TriageRecipientsIn,
        db: Session = Depends(get_db),
        current_user: dict = Depends(requires_tier(Module.MISSING_CHARGES, Tier.MANAGE))):
    from app.services.missing_charges_triage import set_triage_recipients, get_triage_recipients
    set_triage_recipients(db, ",".join(payload.recipients))
    return {"recipients": get_triage_recipients(db)}
```

- [ ] **Step 5: Run to verify it passes**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_missing_charges_triage.py -v` (2 pass)
Also: `./venv/bin/python -c "import app.main"`.

- [ ] **Step 6: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/services/missing_charges_triage.py backend/app/routers/missing_charges.py backend/tests/test_missing_charges_triage.py
git commit -m "feat(missing-charges): triage-recipients setting + endpoint

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Reminder sweep (`send_triage_reminders`)

**Files:**
- Modify: `backend/app/services/missing_charges_triage.py`
- Test: `backend/tests/test_missing_charges_triage.py`

- [ ] **Step 1: Write the failing tests**

Append:

```python
from datetime import timedelta
from app.utils.dt import now_utc_naive
from app.models.missing_charge import MissingCharge
import app.services.missing_charges_triage as mct


def _new_row(db, mrn, days_ago=0):
    from datetime import date
    c = MissingCharge(patient_mrn=mrn, patient_name="Doe", appointment_date=date(2026, 1, 1),
                      primary_provider="Dr A", status="new")
    db.add(c); db.commit(); db.refresh(c)
    if days_ago:
        c.created_at = now_utc_naive() - timedelta(days=days_ago); db.commit()
    return c


def test_reminder_skips_when_no_untriaged(db):
    set_triage_recipients(db, "a@wwc.com")
    rep = mct.send_triage_reminders(db)
    assert rep["skipped"] == "no_untriaged"


def test_reminder_skips_when_no_recipients(db):
    _new_row(db, "M1")
    rep = mct.send_triage_reminders(db)
    assert rep["skipped"] == "no_recipients" and rep["count"] == 1


def test_reminder_sends_email_to_recipients(db, monkeypatch):
    _new_row(db, "M1", days_ago=4)
    _new_row(db, "M2")
    set_triage_recipients(db, "a@wwc.com")
    calls = []
    monkeypatch.setattr(mct, "send_email", lambda to, subj, html, text_body="": calls.append((to, subj)) or True)
    monkeypatch.setattr(mct, "send_slack_dm", lambda user, text: False)
    rep = mct.send_triage_reminders(db)
    assert rep["count"] == 2 and rep["oldest_days"] >= 4
    assert calls and calls[0][0] == "a@wwc.com"
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_missing_charges_triage.py -k reminder -v`
Expected: FAIL — `send_triage_reminders` not defined.

- [ ] **Step 3: Implement the sweep**

Add to `backend/app/services/missing_charges_triage.py` (top-level imports so tests can monkeypatch the names on this module):

```python
from app.models.missing_charge import MissingCharge
from app.models.user import User
from app.services.checklist_notifications import send_email, send_slack_dm
from app.services.missing_charges_email import _app_base_url
from app.utils.dt import now_utc_naive


def _triage_url() -> str:
    return f"{_app_base_url().rstrip('/')}/billing/missing-charges?status=new"


def _digest_text(count: int, oldest_days: int) -> str:
    return (f"{count} missing charge(s) are still 'new' and need triage "
            f"(oldest {oldest_days} day(s) old). Triage them so the responsible "
            f"providers get billed: {_triage_url()}")


def _digest_html(count: int, oldest_days: int) -> str:
    url = _triage_url()
    return (f"<p><strong>{count}</strong> missing charge(s) are still "
            f"<strong>new</strong> and need triage (oldest <strong>{oldest_days}</strong> "
            f"day(s) old).</p><p>Triage them so the responsible providers get billed:</p>"
            f'<p><a href="{url}">Open Missing Charges (untriaged)</a></p>')


def send_triage_reminders(db: Session, *, triggered_by: str = "system") -> dict:
    new_rows = db.query(MissingCharge).filter(MissingCharge.status == "new").all()
    count = len(new_rows)
    if count == 0:
        return {"skipped": "no_untriaged", "count": 0}
    oldest = min(r.created_at for r in new_rows)
    oldest_days = (now_utc_naive() - oldest).days
    recipients = get_triage_recipients(db)
    if not recipients:
        log.info("triage reminder: %d untriaged but no recipients configured", count)
        return {"skipped": "no_recipients", "count": count}
    subject = f"{count} missing charge(s) need triage"
    html = _digest_html(count, oldest_days)
    text = _digest_text(count, oldest_days)
    sent = []
    for email in recipients:
        user = (db.query(User)
                  .filter(User.email == email, User.is_active.is_(True)).first())
        email_ok = bool(send_email(email, subject, html, text_body=text))
        slack_ok = bool(user) and bool(send_slack_dm(user, text))
        sent.append({"email": email, "email_ok": email_ok, "slack_ok": slack_ok})
    return {"triggered_by": triggered_by, "count": count,
            "oldest_days": oldest_days, "recipients": sent}
```

(Confirm `MissingCharge` has `patient_mrn`, `patient_name`, `appointment_date`, `primary_provider`, `status`, `created_at` as used by the test's `_new_row` — they exist in `app/models/missing_charge.py`.)

- [ ] **Step 4: Run to verify they pass**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_missing_charges_triage.py -v` (all pass)

- [ ] **Step 5: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/services/missing_charges_triage.py backend/tests/test_missing_charges_triage.py
git commit -m "feat(missing-charges): triage reminder sweep (email + Slack DM)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Weekly cron wiring

**Files:**
- Modify: `backend/app/services/fax_poller.py`
- Modify: `backend/app/jobs/run.py`
- Test: `backend/tests/test_missing_charges_triage.py`

- [ ] **Step 1: Write the failing test**

Append (idempotency + that the cron entrypoint calls the sweep once):

```python
def test_cron_entrypoint_is_idempotent_per_day(db, monkeypatch):
    _new_row(db, "M1")
    set_triage_recipients(db, "a@wwc.com")
    monkeypatch.setattr(mct, "send_email", lambda *a, **k: True)
    monkeypatch.setattr(mct, "send_slack_dm", lambda *a, **k: False)
    import app.services.fax_poller as fp
    # SessionLocal inside the entrypoint must use the test engine — patch it.
    from app.database import SessionLocal
    monkeypatch.setattr(fp, "SessionLocal", lambda: db)
    calls = {"n": 0}
    real = mct.send_triage_reminders
    monkeypatch.setattr(mct, "send_triage_reminders",
                        lambda *a, **k: (calls.__setitem__("n", calls["n"] + 1), real(*a, **k))[1])
    fp._missing_charges_triage_reminder()
    fp._missing_charges_triage_reminder()   # same day → claim_cron_run blocks the 2nd
    assert calls["n"] == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_missing_charges_triage.py -k cron -v`
Expected: FAIL — `_missing_charges_triage_reminder` not defined.

- [ ] **Step 3: Add the cron entrypoint + scheduler entry**

In `backend/app/services/fax_poller.py`, add next to `_missing_charges_weekly_emails`:

```python
def _missing_charges_triage_reminder():
    from datetime import date
    db = SessionLocal()
    try:
        from app.services.cron_lock import claim_cron_run
        if not claim_cron_run(db, "missing_charges_triage_reminder", date.today().isoformat()):
            return
        from app.services.missing_charges_triage import send_triage_reminders
        report = send_triage_reminders(db, triggered_by="system:weekly-cron")
        logging.getLogger(__name__).info("Missing-charges triage reminder: %s", report)
    finally:
        db.close()
```

And in `start_scheduler()`, right after the `missing_charges_weekly` `add_job(...)`:

```python
    # Missing-charges triage reminder — Thursday 8 AM weekly (ahead of the
    # Monday provider email) so billers triage new rows in time.
    sched.add_job(_missing_charges_triage_reminder, "cron",
                  day_of_week="thu", hour=8, minute=0,
                  id="missing_charges_triage_reminder", max_instances=1, coalesce=True)
```

In `backend/app/jobs/run.py`, add (mirror the `missing_charges_weekly` registration):

```python
@register("missing_charges_triage_reminder")
def missing_charges_triage_reminder():
    from app.services.fax_poller import _missing_charges_triage_reminder
    _missing_charges_triage_reminder()
```

- [ ] **Step 4: Run to verify it passes**

Run:
```bash
cd backend && ./venv/bin/python -m pytest tests/test_missing_charges_triage.py -v
./venv/bin/python -c "import app.main"
```
Expected: all pass; import clean.

- [ ] **Step 5: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/services/fax_poller.py backend/app/jobs/run.py backend/tests/test_missing_charges_triage.py
git commit -m "feat(missing-charges): weekly Thu triage-reminder cron (idempotent)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Provision the Cloud Run Job

**Files:**
- Modify: `scripts/migrate/create_cloud_run_jobs.sh`

- [ ] **Step 1: Add the JOBS entry**

In `scripts/migrate/create_cloud_run_jobs.sh`, add to the `JOBS=( ... )` array (Thursday = cron dow `4`):

```bash
  "missing-charges-triage   0 8 * * 4       missing_charges_triage_reminder"
```

- [ ] **Step 2: Validate the script**

Run: `bash -n scripts/migrate/create_cloud_run_jobs.sh`
Expected: no output (valid).

- [ ] **Step 3: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add scripts/migrate/create_cloud_run_jobs.sh
git commit -m "chore(jobs): provision missing-charges-triage weekly job entry

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 4: At DEPLOY time only** (not part of TDD; the controller runs this after merge + backend image build, using the current backend image `$TAG`):

```bash
# create the job from the current backend image
gcloud run jobs create missing-charges-triage \
  --image=us-east4-docker.pkg.dev/wwc-solutions/app/backend:$TAG \
  --region=us-east4 --project=wwc-solutions \
  --service-account=worker@wwc-solutions.iam.gserviceaccount.com \
  --args=missing_charges_triage_reminder \
  --set-secrets="$(bash -c 'source <(grep -A12 "SECRETS_FLAG=" scripts/migrate/create_cloud_run_jobs.sh); echo "$ALL_SECRETS"')" 2>/dev/null || \
gcloud run jobs update missing-charges-triage \
  --image=us-east4-docker.pkg.dev/wwc-solutions/app/backend:$TAG \
  --region=us-east4 --project=wwc-solutions
# Scheduler trigger: Thursday 08:00 America/New_York
gcloud scheduler jobs create http missing-charges-triage-trigger \
  --location=us-east4 --project=wwc-solutions \
  --schedule="0 8 * * 4" --time-zone="America/New_York" \
  --uri="https://us-east4-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/wwc-solutions/jobs/missing-charges-triage:run" \
  --http-method=POST \
  --oauth-service-account-email=worker@wwc-solutions.iam.gserviceaccount.com 2>/dev/null || true
```
(If the inline `--set-secrets` derivation is awkward, the simplest reliable path is to run the whole idempotent `bash scripts/migrate/create_cloud_run_jobs.sh` — it now reads the live backend image and creates job + trigger for every JOBS row, including this one. The in-process APScheduler entry from Task 3 also runs it, so the Cloud Run Job is belt-and-suspenders; `claim_cron_run` dedupes.)

---

### Task 5: In-app banner

**Files:**
- Modify: `frontend/src/pages/MissingCharges.jsx`

- [ ] **Step 1: Add the banner**

The page already has `const { data: dash } = useQuery(...)` (the `/missing-charges/dashboard` summary) and a `filters` state with `setFilters`. Add, just above the status-chip row (find where the chips render `dash?.by_status?.[s.v]`), a banner shown only when there are untriaged rows:

```jsx
{(dash?.by_status?.new ?? 0) > 0 && (
  <div className="mb-3 flex items-center justify-between gap-3 rounded border border-amber-300 bg-amber-50 px-3 py-2">
    <div className="text-[13px] text-amber-900">
      <strong>{dash.by_status.new}</strong> untriaged charge{dash.by_status.new === 1 ? '' : 's'} —
      triage them so the responsible providers get billed.
    </div>
    <button
      className="btn-primary text-xs whitespace-nowrap"
      onClick={() => setFilters({ ...filters, status: 'new', open_only: false })}>
      Triage now
    </button>
  </div>
)}
```

(Match `filters`/`setFilters` exactly as the existing chip onClick uses them — `status: 'new', open_only: false`.)

- [ ] **Step 2: Build**

Run: `cd frontend && npm run build`
Expected: `✓ built`, no errors.

- [ ] **Step 3: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add frontend/src/pages/MissingCharges.jsx
git commit -m "feat(missing-charges): untriaged-new banner on the Missing Charges page

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Recipients settings UI

**Files:**
- Modify: `frontend/src/pages/MissingCharges.jsx`

- [ ] **Step 1: Add the recipients input**

Add a small "Triage reminder recipients" editor (visible to MANAGE users — reuse the page's existing tier check if present; otherwise render it and let the MANAGE-gated PUT enforce). Place it near the page's existing settings/mappings area. Wire it to the Task 1 endpoints:

```jsx
// near other useQuery hooks:
const { data: triageCfg } = useQuery({
  queryKey: ['mc-triage-recipients'],
  queryFn: () => api.get('/missing-charges/triage-recipients').then(r => r.data),
})
const [recipientsInput, setRecipientsInput] = useState('')
useEffect(() => {
  if (triageCfg?.recipients) setRecipientsInput(triageCfg.recipients.join(', '))
}, [triageCfg])
const saveRecipients = useMutation({
  mutationFn: () => api.put('/missing-charges/triage-recipients',
    { recipients: recipientsInput.split(',').map(s => s.trim()).filter(Boolean) }).then(r => r.data),
  onSuccess: () => qc.invalidateQueries({ queryKey: ['mc-triage-recipients'] }),
  onError: (e) => alert(e?.response?.data?.detail || 'Save failed'),
})
```

```jsx
// render block:
<div className="card !p-3 mb-3">
  <div className="text-[11px] uppercase tracking-wide text-gray-500 mb-1">Triage reminder recipients</div>
  <div className="flex items-center gap-2">
    <input className="input text-xs flex-1" placeholder="biller@wwc.com, biller2@wwc.com"
           value={recipientsInput} onChange={e => setRecipientsInput(e.target.value)} />
    <button className="btn-secondary text-xs" disabled={saveRecipients.isPending}
            onClick={() => saveRecipients.mutate()}>
      {saveRecipients.isPending ? 'Saving…' : 'Save'}
    </button>
  </div>
  <div className="text-[10px] text-gray-500 mt-1">Weekly (Thu 8am) email + Slack DM when untriaged charges exist.</div>
</div>
```

Ensure `useState`, `useEffect`, `useMutation`, `useQueryClient` (`qc`) and `api` are imported (the page already imports react-query + `api`; add `useEffect` to the React import if missing).

- [ ] **Step 2: Build**

Run: `cd frontend && npm run build`
Expected: `✓ built`, no errors.

- [ ] **Step 3: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add frontend/src/pages/MissingCharges.jsx
git commit -m "feat(missing-charges): triage-recipients settings input

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- Configured recipients in PracticeConfig + endpoint → Task 1. ✓
- Sweep `send_triage_reminders` (count `new`, oldest age, skip when 0 / no recipients, email + Slack per recipient) → Task 2. ✓
- Weekly Thu-8am cron mirroring `missing_charges_weekly` + idempotency + real logger → Task 3. ✓
- Cloud Run Job provisioning → Task 4. ✓
- In-app banner (untriaged `new` > 0 → banner + filter-to-new) → Task 5. ✓
- Recipients settings UI → Task 6. ✓
- Channels = email + Slack DM + banner (checklist dropped) → Tasks 2 + 5. ✓

**Placeholder scan:** none — every code step has full code; Task 4 Step 4 is an explicit deploy command (not TDD), clearly labeled.

**Type/name consistency:** `TRIAGE_RECIPIENTS_KEY`, `get_triage_recipients`, `set_triage_recipients`, `send_triage_reminders`, `_missing_charges_triage_reminder`, the `{"skipped": ..., "count": ...}` / `{"count", "oldest_days", "recipients"}` report shapes, `send_email`/`send_slack_dm` signatures, and the `filters.status='new'` frontend contract are consistent across tasks. `MissingCharge.status == "new"` and `created_at` verified against the model. Cron `id`/job name `missing_charges_triage_reminder` consistent in scheduler, `jobs/run.py`, and `claim_cron_run` key.
