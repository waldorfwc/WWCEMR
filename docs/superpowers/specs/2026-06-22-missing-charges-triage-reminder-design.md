# Missing Charges — Untriaged Triage Reminder — Design

**Date:** 2026-06-22
**Status:** Approved (design); spec under review

## Problem

Imported missing-charge rows land as `status="new"` and must be **manually
triaged** by a biller (marked "Needs to be billed") before the weekly cron
emails the responsible provider. There is no nudge when `new` rows pile up
untriaged, so charges can sit and never reach a provider. We want a weekly
reminder to the biller(s) when untriaged `new` rows exist.

(Verified: nothing auto-promotes `new` → `needs_to_be_billed`; it's a manual
biller action via `routers/missing_charges.py:303`. The provider email
(`missing_charges_email.send_provider_emails`) only sends `needs_to_be_billed`
rows, Monday 8am ET.)

## Goal

A weekly reminder to configured biller recipient(s) whenever any `new`
(untriaged) missing-charge rows exist, plus an always-on in-app banner.

## Decisions (settled)

- **Channels:** Email digest + Slack DM + in-app banner. (Checklist task channel
  dropped.)
- **Recipients:** A configured list (setting), not auto-derived from tiers.
- **Cadence:** Weekly, **Thursday 8am ET** — a few days ahead of the Monday 8am
  provider email so billers can triage `new` → `needs_to_be_billed` in time.
- **Trigger condition:** any `new` row exists (count > 0).

## Architecture

All four building blocks already exist and were verified working in the
2026-06-22 integration system test (SMTP email, Slack DM, the weekly-cron
pattern, the missing-charges summary).

### 1. Configured recipients (setting)

- Store in the existing `PracticeConfig` KV table (`models/practice_config.py`:
  `key` PK, `value` VARCHAR(500)) under key **`missing_charges_triage_recipients`**
  — a comma-separated list of emails. Empty/absent → the reminder no-ops.
- Read/write helper in `app/services/missing_charges_triage.py`:
  `get_triage_recipients(db) -> list[str]` (parse CSV, strip, drop blanks).
- Editable in the Missing Charges settings UI (a text input). A small
  `GET`/`PUT /api/missing-charges/triage-recipients` pair gated by
  `requires_tier(Module.MISSING_CHARGES, Tier.MANAGE)`.

### 2. Reminder sweep — `send_triage_reminders(db, *, triggered_by="system")`

New module `app/services/missing_charges_triage.py`. Logic:

```
new_rows = db.query(MissingCharge).filter(MissingCharge.status == "new").all()
count = len(new_rows)
if count == 0:
    return {"skipped": "no_untriaged", "count": 0}
oldest = min(r.created_at for r in new_rows)            # MissingCharge.created_at exists
oldest_days = (now_utc_naive() - oldest).days
recipients = get_triage_recipients(db)
if not recipients:
    logging.getLogger(__name__).info("triage reminder: %d untriaged but no recipients configured", count)
    return {"skipped": "no_recipients", "count": count}
sent = []
for email in recipients:
    user = db.query(User).filter(User.email == email, User.is_active.is_(True)).first()
    subject = f"{count} missing charge(s) need triage"
    html = _digest_html(count, oldest_days, _triage_url())   # link to /billing/missing-charges?status=new
    email_ok = send_email(email, subject, html, text_body=_digest_text(count, oldest_days))
    slack_ok = bool(user) and send_slack_dm(user, _digest_text(count, oldest_days))
    sent.append({"email": email, "email_ok": email_ok, "slack_ok": slack_ok})
return {"count": count, "oldest_days": oldest_days, "recipients": sent}
```

- `send_email` and `send_slack_dm(user, text)` are the existing helpers in
  `app/services/checklist_notifications.py` (verified live). Slack is skipped
  when the email doesn't resolve to an active `User` or the user has no Slack —
  `send_slack_dm` already no-ops gracefully.
- `_triage_url()` builds `<app_base>/billing/missing-charges?status=new` (reuse
  the base-url helper `missing_charges_email._app_base_url`).
- Never raises on a single recipient's failure — collect per-recipient ok flags.

### 3. Weekly cron — `missing_charges_triage_reminder`

Mirror the existing `missing_charges_weekly` wiring exactly (so it inherits the
same idempotency + Cloud-Run-Job setup):

- **`app/services/fax_poller.py`** — add `_missing_charges_triage_reminder()`:
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
  and register in `start_scheduler()`:
  ```python
  sched.add_job(_missing_charges_triage_reminder, "cron",
                day_of_week="thu", hour=8, minute=0,
                id="missing_charges_triage_reminder", max_instances=1, coalesce=True)
  ```
  (Use `logging.getLogger(__name__)`, NOT a bare `log` — the sibling weekly job
  had exactly that NameError bug, fixed 2026-06-22.)
- **`app/jobs/run.py`** — `@register("missing_charges_triage_reminder")` calling
  `_missing_charges_triage_reminder()`.
- **`scripts/migrate/create_cloud_run_jobs.sh`** — add a `JOBS` row:
  `"missing-charges-triage   0 8 * * 4   missing_charges_triage_reminder"`
  (Thursday = cron dow 4). Provision the new job + Scheduler trigger via gcloud
  at deploy (the provisioner now reads the live backend image — fixed 2026-06-22).

`claim_cron_run(db, job_name, run_key=date.today())` dedupes so the in-process
APScheduler tick and the Cloud Run Job don't double-send on the same day.

### 4. In-app banner (frontend, always-on)

On the Missing Charges page (`frontend/src/pages/MissingCharges.jsx`), when the
existing summary's `by_status.new > 0`, render an amber banner: "N untriaged
charges — triage so providers get billed", with a **Triage now** button that
sets the page's status filter to `new`. Reads the existing summary endpoint
(`by_status`, `routers/missing_charges.py:249-260`); no cron involved. Shows for
anyone who can view the page.

## Testing

Backend (pytest, `backend/tests/test_missing_charges_triage.py`):
- **Sends to configured recipients** — seed `new` rows + set the
  `missing_charges_triage_recipients` config; assert `send_email` (and
  `send_slack_dm` for a recipient that resolves to a Slack-enabled user) are
  called with the count; report `count` correct.
- **No untriaged → skip** — no `new` rows → returns `{"skipped":"no_untriaged"}`,
  no email sent.
- **No recipients → skip** — `new` rows but empty config → returns
  `{"skipped":"no_recipients"}`, no email sent, logged.
- **oldest_days** — oldest `new` row's `created_at` drives the age.
- **Recipients endpoint** — `GET`/`PUT /triage-recipients` round-trips the CSV
  and is MANAGE-gated.
- Mock `send_email`/`send_slack_dm` (don't send real messages in tests).

Frontend: `npm run build`; manual check the banner appears when `new > 0` and
the button filters to `new`.

## Risks

- **Double-send:** `claim_cron_run` (run_key = date) guarantees one send/day
  across the in-process scheduler + Cloud Run Job.
- **Bare-logger rot:** explicitly use `logging.getLogger(__name__)` (the weekly
  sibling's NameError bug).
- **Recipient not a User:** email still sends to the address; Slack DM is
  skipped gracefully.
- **New Cloud Run Job + trigger** is net-new infra (one job + one Scheduler
  trigger) — provisioned at deploy; low risk, mirrors 13 existing jobs.
