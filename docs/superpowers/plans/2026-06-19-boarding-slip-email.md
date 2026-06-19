# Boarding-Slip Email (manual + scheduled auto-send) — Implementation Plan

**Goal:** Email the boarding slip to per-facility recipient lists configured in Surgery Settings, both on-demand (manual, multi-recipient) and automatically X hours after a surgery date is selected.

**Approved design decisions:** per-facility recipient lists (MedStar vs CRMC separate); resend on reschedule (a new selected date re-arms the auto-send).

**Tech:** FastAPI, SQLAlchemy, in-process APScheduler (fax_poller.start_scheduler — the active cron), React.

## Verified anchors
- Settings registry: `backend/app/services/surgery/settings.py` `SETTINGS_DEFAULTS` dict; read via `cfg(db, key)`. Writes go through `PUT /surgery/config` (`backend/app/routers/surgery_config.py`), gated by `ConfigPayload` (only keys in `CONFIG_DEFAULTS` persist; the PUT loop is at ~285).
- `ConfigPayload` (surgery_config.py:112) — Pydantic model; add Optional fields + an email validator. `CONFIG_DEFAULTS` mirrors `SETTINGS_DEFAULTS`.
- Surgery model: `backend/app/models/surgery.py` has `boarding_slip_overrides` (JSON). Add `boarding_slip_auto_emailed_at` (DateTime, nullable) near it.
- Lightweight migration: `backend/app/database.py` `_apply_lightweight_migrations()` `needed` list — append `("surgeries", "boarding_slip_auto_emailed_at", "DATETIME")`.
- Existing email send: `send_boarding_slip` (surgery.py ~3398) email branch (~3540-3590) builds a `MIMEMultipart` with the PDF attachment via `smtplib` + `_smtp_settings()` (from `app.services.checklist_notifications`), single `payload.to`. `_record_send(entry)` appends to the file's send history; `log_action(..., "PHI_BOARDING_SLIP_SENT", ...)` audits. `BoardingSlipSendPayload`: `kind, to, subject, message, file_id`.
- Slip generation: `app/services/surgery/boarding_slip.py` `generate_for_surgery(db, s, *, by_email, overrides=None) -> SurgeryFile`. Latest slip = `SurgeryFile` where `kind="boarding_slip"` ordered by `uploaded_at` desc. Bytes via `app.services.storage.read_blob(f.path)`.
- "Date selected" timestamp = `SurgerySlot.created_at` (slot is created when the date is booked). On reschedule the old slot is deleted + a new one created (`pick_or_reschedule` in `date_picker.py`), so the new slot's `created_at` is the new selection time.
- Reschedule hook: `date_picker.py` `pick_or_reschedule`, the `if is_reschedule:` block already clears `s.calendar_invite_sent_at` — clear `s.boarding_slip_auto_emailed_at` there too.
- Cron: `app/services/fax_poller.py` `start_scheduler()` — surgery crons added via `sched.add_job(_wrapper, "cron", ..., id=..., max_instances=1, coalesce=True)`; wrappers open `SessionLocal()`, call the service, swallow/log errors; `claim_cron_run(db, name, run_key)` guards multi-instance dupes. Also `app/jobs/run.py` `@register("name")` for Cloud Run Job parity.
- Facilities: `medstar`, `crmc` (only these need a boarding slip).

## Tasks

### T1 — Data layer + email service
Files: settings.py, surgery_config.py, models/surgery.py, database.py, NEW `app/services/surgery/boarding_slip_email.py`.
- settings.py: add keys `boarding_slip_recipients_medstar: []`, `boarding_slip_recipients_crmc: []`, `boarding_slip_auto_email_enabled: False`, `boarding_slip_auto_email_hours: 24`.
- surgery_config.py ConfigPayload: add `boarding_slip_recipients_medstar/crmc: Optional[list[str]]`, `boarding_slip_auto_email_enabled: Optional[bool]`, `boarding_slip_auto_email_hours: Optional[int] = Field(default=None, ge=0, le=336)`. Add a `@field_validator` for the two recipient lists that strips blanks, lowercases, and rejects entries without `@`.
- models/surgery.py: `boarding_slip_auto_emailed_at = Column(DateTime, nullable=True)`.
- database.py: migration entry.
- NEW service `boarding_slip_email.py`:
  - `recipients_for(db, facility) -> list[str]` — reads the per-facility cfg key (`boarding_slip_recipients_medstar`/`_crmc`), returns cleaned list ([] for other facilities).
  - `send_boarding_slip_email(db, s, file, recipients: list[str], *, sent_by, subject=None, message=None) -> dict` — build one MIME with the PDF attachment, `To` = all recipients, send via smtplib (reuse the existing email block's logic + `_smtp_settings()`), record send history on the file + `log_action` audit, return `{"ok": True, "to": recipients}`. Raise/return cleanly on SMTP-not-configured / send failure.
  - `auto_email_sweep(db, *, now=None) -> dict` — if `boarding_slip_auto_email_enabled` is False return `{"skipped": "disabled"}`. `cutoff = now - hours`. Query active (status not in cancelled/completed) surgeries at medstar/crmc with `boarding_slip_auto_emailed_at IS NULL` that have a `SurgerySlot` with `created_at <= cutoff`. For each: `recips = recipients_for(facility)`; skip (count) if empty; ensure a slip exists else `generate_for_surgery`; `send_boarding_slip_email`; on success stamp `s.boarding_slip_auto_emailed_at = now`. Return counts `{sent, skipped_no_recipients, errors}`.

### T2 — Endpoint + reschedule + cron wiring
Files: surgery.py, date_picker.py, fax_poller.py, app/jobs/run.py.
- `BoardingSlipSendPayload`: add `recipients: Optional[list[str]] = None` (keep `to` for back-compat). In the email branch, resolve `recipients = payload.recipients or [payload.to]` (split `to` on comma/semicolon), validate each, and call `boarding_slip_email.send_boarding_slip_email(...)` instead of the inline smtplib block. Keep the fax branch unchanged.
- date_picker.py `pick_or_reschedule`: in the `if is_reschedule:` block add `s.boarding_slip_auto_emailed_at = None`.
- fax_poller.py: add `_boarding_slip_autosend()` wrapper (SessionLocal → `auto_email_sweep`, guarded by `claim_cron_run(db, "surgery_boarding_slip_autosend", <hour key>)`); register hourly in `start_scheduler` (`"cron", minute=15, id="surgery_boarding_slip_autosend", max_instances=1, coalesce=True`).
- app/jobs/run.py: `@register("surgery_boarding_slip_autosend")` wrapper for Cloud Run Job parity.

### T3 — Frontend
Files: SurgerySettings.jsx, SurgeryDetail.jsx.
- SurgerySettings: a "Boarding-Slip Email" section — MedStar recipients + CRMC recipients (multi-email inputs, comma-separated or chip list), auto-send toggle, hours input. Persist via `PUT /surgery/config`.
- SurgeryDetail `SendBoardingSlipPanel`: pre-fill the email recipients from the surgery's facility config (read `/surgery/config`), allow editing/multiple, send all via the recipients field.

### T4 — Tests
- `recipients_for` reads the right per-facility key; blank/invalid filtered.
- `send_boarding_slip_email` builds a multi-recipient message (monkeypatch smtplib) and records history.
- `auto_email_sweep`: disabled → skip; a surgery with a slot older than X hours + recipients → sent + stamped; no recipients → skipped; already-stamped → not re-sent.
- reschedule clears `boarding_slip_auto_emailed_at`.
- endpoint: email to multiple recipients → 200.

## Conventions
MM/DD/YYYY, Title Case; `now_utc_naive()`; tests via `./venv/bin/python -m pytest` from backend/. No secrets in code.
