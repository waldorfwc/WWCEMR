"""Notification dispatch for checklist tasks — Slack + Email + SMS.

Channels:
  - Email via SMTP (Gmail / Workspace; per-user)
  - Slack DMs via per-workspace bot tokens (per-user, routed by email domain)
      SLACK_BOT_TOKEN_WWC        → waldorfwomenscare.com users
      SLACK_BOT_TOKEN_CARIBCALL  → caribcall.com users
  - Slack channel webhook for team-level summaries (optional, fallback)
      SLACK_CHECKLIST_WEBHOOK_URL
  - SMS via Twilio (per-user, only if notify_sms=True and phone is on file)
      TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER

Missing credentials never raise — the dispatch logs and returns False so the
rest of the loop continues for other users / channels.

HIPAA note for SMS: SMS is NOT HIPAA-compliant for PHI. The morning/EOD
templates here send only generic counts and task titles — never patient
names, chart numbers, or clinical details. Templates wired to user-specific
patient data must NOT be sent via SMS.
"""
from __future__ import annotations

import logging
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import List, Optional

import httpx
from sqlalchemy.orm import Session

from app.models.user import User
from app.utils.dt import now_utc_naive

log = logging.getLogger(__name__)


SLACK_API = "https://slack.com/api"
TWILIO_API_BASE = "https://api.twilio.com/2010-04-01"


def _slack_webhook_url() -> str:
    return os.environ.get("SLACK_CHECKLIST_WEBHOOK_URL", "").strip()


def _slack_bot_token_for(email: str) -> str:
    """Return the bot token for the workspace this user lives in.

    Routing by email domain — caribcall.com → CaribCall workspace,
    everything else → WWC workspace.
    """
    domain = (email or "").split("@")[-1].lower().strip()
    if domain == "caribcall.com":
        return os.environ.get("SLACK_BOT_TOKEN_CARIBCALL", "").strip()
    return os.environ.get("SLACK_BOT_TOKEN_WWC", "").strip()


def _resolve_slack_user_id(user: User, db: Optional[Session] = None) -> Optional[str]:
    """Find this user's Slack user ID via users.lookupByEmail.

    Uses the cached User.slack_user_id when present. On first lookup, calls
    Slack and caches the result if a `db` session is provided.
    Returns None if the bot isn't installed in that workspace, the user
    isn't in the workspace, or the lookup fails.
    """
    if user.slack_user_id:
        return user.slack_user_id

    token = _slack_bot_token_for(user.email)
    if not token:
        return None

    try:
        r = httpx.get(
            f"{SLACK_API}/users.lookupByEmail",
            params={"email": user.email},
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        data = r.json()
        if data.get("ok"):
            slack_id = data["user"]["id"]
            if db is not None:
                user.slack_user_id = slack_id
                db.commit()
            return slack_id
        log.info("Slack lookup miss for %s: %s", user.email, data.get("error"))
    except Exception as exc:
        log.warning("Slack lookup error for %s: %s", user.email, exc)
    return None


def send_slack_dm(user: User, text: str, blocks: list = None,
                   db: Optional[Session] = None) -> bool:
    """Post a direct message to a single user via the appropriate workspace bot."""
    token = _slack_bot_token_for(user.email)
    if not token:
        log.info("SLACK DM (no bot token for %s domain): %s",
                 user.email, text[:160])
        return False

    slack_id = _resolve_slack_user_id(user, db=db)
    if not slack_id:
        log.info("SLACK DM (no slack_user_id for %s): %s",
                 user.email, text[:160])
        return False

    payload = {"channel": slack_id, "text": text}
    if blocks:
        payload["blocks"] = blocks
    try:
        r = httpx.post(
            f"{SLACK_API}/chat.postMessage",
            json=payload,
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json; charset=utf-8"},
            timeout=10,
        )
        data = r.json()
        if data.get("ok"):
            return True
        log.warning("Slack DM failed for %s: %s", user.email, data.get("error"))
        return False
    except Exception as exc:
        log.warning("Slack DM error for %s: %s", user.email, exc)
        return False


def _smtp_settings() -> dict:
    return {
        "host":     os.environ.get("SMTP_HOST", "").strip(),
        "port":     int(os.environ.get("SMTP_PORT", "587") or 587),
        "user":     os.environ.get("SMTP_USER", "").strip(),
        "password": os.environ.get("SMTP_PASSWORD", "").strip(),
        "from":     os.environ.get("SMTP_FROM", "").strip() or os.environ.get("SMTP_USER", "").strip(),
    }


# ─────────────────────────────────────────────────────────────────────
# SMS (Twilio)

def _twilio_settings() -> dict:
    return {
        "sid":   os.environ.get("TWILIO_ACCOUNT_SID", "").strip(),
        "token": os.environ.get("TWILIO_AUTH_TOKEN", "").strip(),
        "from":  os.environ.get("TWILIO_FROM_NUMBER", "").strip(),
    }


def _normalize_phone(raw: str) -> str:
    """Normalize a phone number to E.164. Accepts:
      - already-formatted '+12402522415'
      - 10-digit '2402522415' (assumed US)
      - 11-digit '12402522415' (assumed US)
      - dashes / spaces / parens — stripped
    Returns '' if it can't be normalized.
    """
    if not raw:
        return ""
    digits = "".join(c for c in raw if c.isdigit() or c == "+")
    if digits.startswith("+"):
        return digits
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    return ""


def send_sms(to_phone: str, body: str) -> Optional[str]:
    """Send an SMS via Twilio. Returns the Twilio Message SID on success
    (a truthy string), or None on any failure. Callers that just want a
    bool can `if send_sms(...)`. Body is clipped to 320 chars (~2 SMS
    segments) to keep costs sane."""
    cfg = _twilio_settings()
    if not (cfg["sid"] and cfg["token"] and cfg["from"]):
        log.info("SMS (no Twilio configured) to=%s: %s", to_phone, body[:120])
        return None
    to_e164 = _normalize_phone(to_phone)
    if not to_e164:
        log.info("SMS skipped — invalid phone %r", to_phone)
        return None

    # Cap body — receivers see the leading text either way; helps cost
    body = body if len(body) <= 320 else body[:317] + "…"
    try:
        r = httpx.post(
            f"{TWILIO_API_BASE}/Accounts/{cfg['sid']}/Messages.json",
            data={"From": cfg["from"], "To": to_e164, "Body": body},
            auth=(cfg["sid"], cfg["token"]),
            timeout=15,
        )
        if 200 <= r.status_code < 300:
            try:
                return r.json().get("sid") or ""
            except Exception:
                return ""
        log.warning("Twilio SMS failed [%s] to=%s: %s",
                    r.status_code, to_e164, r.text[:200])
        return None
    except Exception as exc:
        log.warning("Twilio SMS error to=%s: %s", to_e164, exc)
        return None


# ─────────────────────────────────────────────────────────────────────
# Slack

def send_slack(text: str, blocks: list = None) -> bool:
    """Post to the configured #checklist channel. Returns True on success."""
    url = _slack_webhook_url()
    if not url:
        log.info("SLACK (no webhook configured): %s", text[:200])
        return False
    payload = {"text": text}
    if blocks:
        payload["blocks"] = blocks
    try:
        r = httpx.post(url, json=payload, timeout=10)
        return 200 <= r.status_code < 300
    except Exception as exc:
        log.warning("Slack post failed: %s", exc)
        return False


# ─────────────────────────────────────────────────────────────────────
# Email (SMTP)

def send_email(to: str, subject: str, html_body: str, text_body: str = "") -> bool:
    cfg = _smtp_settings()
    if not (cfg["host"] and cfg["from"]):
        log.info("EMAIL (no SMTP configured) to=%s subject=%s", to, subject)
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = cfg["from"]
        msg["To"] = to
        if text_body:
            msg.attach(MIMEText(text_body, "plain"))
        msg.attach(MIMEText(html_body, "html"))
        with smtplib.SMTP(cfg["host"], cfg["port"]) as s:
            s.starttls()
            if cfg["user"] and cfg["password"]:
                s.login(cfg["user"], cfg["password"])
            s.sendmail(cfg["from"], [to], msg.as_string())
        return True
    except Exception as exc:
        log.warning("Email send failed: %s", exc)
        return False


# ─────────────────────────────────────────────────────────────────────
# Daily digests + nudges

def send_morning_digest(user: User, instances: list,
                         db: Optional[Session] = None) -> dict:
    """Email + per-user Slack DM at start of day with today's task list."""
    if not instances:
        return {"sent": False, "reason": "no tasks today"}

    name = user.display_name or user.email.split("@")[0]
    subject = f"WWC · Your checklist for today ({len(instances)} tasks)"
    rows_html = ""
    rows_text = ""
    for it in instances:
        priority = it.get("priority", "medium")
        title = it.get("title", "")
        due = it.get("due_at", "") or ""
        rows_html += (
            f'<tr><td style="padding:6px 8px"><span style="font-size:11px;color:#999">[{priority.upper()}]</span></td>'
            f'<td style="padding:6px 8px"><strong>{title}</strong></td>'
            f'<td style="padding:6px 8px;color:#999">{due[:16] if due else ""}</td></tr>'
        )
        rows_text += f"  • [{priority.upper()}] {title}{f' (due {due[:16]})' if due else ''}\n"

    html = f"""
    <p>Good morning, {name}.</p>
    <p>You have <strong>{len(instances)}</strong> tasks today:</p>
    <table style="border-collapse:collapse;font-family:system-ui,sans-serif;font-size:13px">
      {rows_html}
    </table>
    <p style="margin-top:14px">
      <a href="https://gw.waldorfwomenscare.com/checklist" style="color:#7B2D5E">Open Checklist →</a>
    </p>
    """
    text = f"Good morning, {name}.\n\nYou have {len(instances)} tasks today:\n\n{rows_text}\n\nOpen: https://gw.waldorfwomenscare.com/checklist\n"

    email_ok = send_email(user.email, subject, html, text) if user.notify_email else False

    slack_ok = False
    if user.notify_slack:
        slack_text = (
            f"📋 Good morning — you have *{len(instances)}* tasks today\n"
            + "\n".join(f"• [{it.get('priority','med').upper()}] {it.get('title','')}"
                        for it in instances[:8])
            + (f"\n_…and {len(instances) - 8} more on your checklist_"
               if len(instances) > 8 else "")
            + "\n<https://gw.waldorfwomenscare.com/checklist|Open Checklist →>"
        )
        slack_ok = send_slack_dm(user, slack_text, db=db)

    sms_ok = False
    if user.notify_sms and user.phone_number:
        # SMS = short summary only — no per-task PHI risk
        sms_body = (
            f"WWC Checklist · {len(instances)} task"
            f"{'s' if len(instances) != 1 else ''} for today. "
            f"Open: https://gw.waldorfwomenscare.com/checklist"
        )
        sms_ok = bool(send_sms(user.phone_number, sms_body))

    return {"sent": email_ok or slack_ok or sms_ok,
            "email": email_ok, "slack": slack_ok, "sms": sms_ok}


def send_eod_overdue_nudge(user: User, overdue: list,
                            db: Optional[Session] = None) -> dict:
    """5 PM personal DM + email nudge for any task still pending."""
    if not overdue:
        return {"sent": False, "reason": "all clear"}
    name = user.display_name or user.email.split("@")[0]
    subject = f"WWC · {len(overdue)} task(s) still open — please complete before EOD"
    rows_html = "".join(
        f'<li><strong>{it.get("title","")}</strong> '
        f'<span style="color:#999">({it.get("priority","med")})</span></li>'
        for it in overdue
    )
    rows_text = "\n".join(f"  • {it.get('title','')} ({it.get('priority','med')})"
                          for it in overdue)

    html = f"""
    <p>Hi {name},</p>
    <p>The following tasks are still open with EOD approaching:</p>
    <ul>{rows_html}</ul>
    <p><a href="https://gw.waldorfwomenscare.com/checklist" style="color:#7B2D5E">Open Checklist →</a></p>
    """
    text = f"Hi {name}, EOD reminder — {len(overdue)} tasks still open:\n{rows_text}\n\nOpen: https://gw.waldorfwomenscare.com/checklist"

    email_ok = send_email(user.email, subject, html, text) if user.notify_email else False
    slack_ok = False
    if user.notify_slack:
        slack_text = (
            f"⚠️ EOD reminder — you have *{len(overdue)}* task(s) still open:\n"
            + "\n".join(f"• {it.get('title','')}" for it in overdue[:5])
            + (f"\n_…and {len(overdue) - 5} more_" if len(overdue) > 5 else "")
            + "\n<https://gw.waldorfwomenscare.com/checklist|Open Checklist →>"
        )
        slack_ok = send_slack_dm(user, slack_text, db=db)
    sms_ok = False
    if user.notify_sms and user.phone_number:
        sms_body = (
            f"WWC Checklist · {len(overdue)} task"
            f"{'s' if len(overdue) != 1 else ''} still open at EOD. "
            f"https://gw.waldorfwomenscare.com/checklist"
        )
        sms_ok = bool(send_sms(user.phone_number, sms_body))
    return {"sent": email_ok or slack_ok or sms_ok,
            "email": email_ok, "slack": slack_ok, "sms": sms_ok}


def send_manager_escalation(manager: User, overdue_instances: list,
                             db: Optional[Session] = None) -> dict:
    """Notify a manager that one or more of their direct reports' tasks
    are past `escalate_after_hours` without an answer. One DM/email per
    manager per run, regardless of how many tasks are listed.

    `overdue_instances` is a list of dicts shaped like:
        {"task": "...", "owner": "...@...", "due_at": "...",
         "answer": None | "no", "followup": "..."}
    """
    if not overdue_instances:
        return {"sent": False, "reason": "nothing to escalate"}
    name = manager.display_name or manager.email.split("@")[0]
    subject = f"WWC · {len(overdue_instances)} overdue checklist task(s) need attention"
    rows_html = "".join(
        f'<li><strong>{it.get("task","")}</strong> '
        f'— {it.get("owner","")} '
        f'<span style="color:#999">(due {it.get("due_at","")})</span></li>'
        for it in overdue_instances
    )
    rows_text = "\n".join(
        f"  • {it.get('task','')} — {it.get('owner','')} (due {it.get('due_at','')})"
        for it in overdue_instances
    )
    html = f"""
    <p>Hi {name},</p>
    <p>The following tasks were not completed in time and you are listed
       as the escalation owner:</p>
    <ul>{rows_html}</ul>
    <p><a href="https://gw.waldorfwomenscare.com/manager-dashboard" style="color:#7B2D5E">Manager Dashboard →</a></p>
    """
    text = (f"Hi {name}, {len(overdue_instances)} task(s) need follow-up:\n"
            f"{rows_text}\n\nManager Dashboard: https://gw.waldorfwomenscare.com/manager-dashboard")

    email_ok = send_email(manager.email, subject, html, text) if manager.notify_email else False
    slack_ok = False
    if manager.notify_slack:
        slack_text = (
            f"🚩 *{len(overdue_instances)}* checklist task(s) need your follow-up:\n"
            + "\n".join(f"• {it.get('task','')} — _{it.get('owner','')}_"
                        for it in overdue_instances[:8])
            + (f"\n_…and {len(overdue_instances) - 8} more_"
               if len(overdue_instances) > 8 else "")
            + "\n<https://gw.waldorfwomenscare.com/manager-dashboard|Open Dashboard →>"
        )
        slack_ok = send_slack_dm(manager, slack_text, db=db)
    return {"sent": email_ok or slack_ok, "email": email_ok, "slack": slack_ok}


def run_escalation_sweep(db: Session) -> dict:
    """Find every task instance that is past escalate_after_hours, not yet
    answered, and has a manager set on its template. Group by manager,
    send one digest per manager, mark instances as escalated.

    Idempotent — only processes instances where escalation_sent_at is NULL.
    """
    from datetime import datetime, timedelta
    from sqlalchemy import and_
    from app.models.checklist import TaskInstance, TaskTemplate

    now = now_utc_naive()

    rows = (db.query(TaskInstance, TaskTemplate)
              .join(TaskTemplate, TaskInstance.template_id == TaskTemplate.id)
              .filter(
                  TaskInstance.escalation_sent_at.is_(None),
                  TaskInstance.status != "done",
                  TaskTemplate.escalate_to_email.isnot(None),
              ).all())

    by_manager: dict[str, list[dict]] = {}
    instances_to_mark: list[TaskInstance] = []

    for inst, tmpl in rows:
        # When did escalation become eligible?
        # If the template has a due_time, escalation is N hours past due_at.
        # Otherwise, N hours past midnight of due_date (so an undated daily
        # task at 9 AM with 24h escalation triggers the next day).
        from datetime import datetime as _dt
        base = inst.due_at or _dt.combine(inst.due_date, _dt.min.time())
        eligible_at = base + timedelta(hours=tmpl.escalate_after_hours or 24)
        if now < eligible_at:
            continue
        by_manager.setdefault(tmpl.escalate_to_email, []).append({
            "task": tmpl.question_text or tmpl.title,
            "owner": inst.assigned_to_email,
            "due_at": str(inst.due_at or inst.due_date),
            "answer": inst.answer,
            "followup": inst.followup_text or (
                str(inst.followup_count) if inst.followup_count is not None else None
            ),
        })
        instances_to_mark.append(inst)

    sent = 0
    for manager_email, items in by_manager.items():
        manager = db.query(User).filter(User.email == manager_email).first()
        if not manager:
            log.info("Escalation skipped — manager %s not in users table", manager_email)
            continue
        result = send_manager_escalation(manager, items, db=db)
        if result.get("sent"):
            sent += 1

    for inst in instances_to_mark:
        inst.escalation_sent_at = now
    if instances_to_mark:
        db.commit()

    return {"managers_notified": sent,
            "instances_escalated": len(instances_to_mark)}


def send_team_summary(user_count: int, task_count: int) -> bool:
    """One channel-wide post per day for visibility — not per user.

    Posts to the existing #checklist channel via the legacy webhook so
    everyone sees a heartbeat that the system ran today, without exposing
    individual task lists in the shared channel.
    """
    if user_count == 0:
        return False
    text = (f"📋 Morning checklist sent — *{user_count}* user{'s' if user_count != 1 else ''} "
            f"received personalized DMs for *{task_count}* task{'s' if task_count != 1 else ''} today.")
    return send_slack(text)
