"""Release-the-day alerts for surgery block schedule.

Two rules, both run daily:

  Hospital release  — Any MedStar/CRMC block day in the next 14 days
                      with 0 booked surgeries → email + Slack the
                      scheduler so they can call the hospital and
                      release the slot back.

  Office release    — When an office procedure day is exactly 6 days
                      out and has fewer than 6 procedures booked →
                      notify scheduler + office manager to open the
                      remaining time for clinic patients.

Idempotent on BlockDay.release_alert_sent_at so we don't re-send the
same alert daily.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session, joinedload

from app.models.groups import Group
from app.models.surgery import BlockDay, SurgerySlot
from app.models.user import User
from app.services import checklist_notifications as notif

log = logging.getLogger(__name__)


from app.models.surgery_config import (
    SurgeryConfig, SurgeryAlertRecipient,
)


_CONFIG_DEFAULTS = {
    "office_full_threshold":   6,
    "office_lookahead_days":   6,
    "hospital_lookahead_days": 14,
}


def _cfg(db, key: str):
    row = db.query(SurgeryConfig).filter(SurgeryConfig.key == key).first()
    return row.value if row else _CONFIG_DEFAULTS[key]


def _configured_recipients(db, alert_kind: str) -> list[str]:
    rows = (db.query(SurgeryAlertRecipient)
              .filter(SurgeryAlertRecipient.alert_kind == alert_kind).all())
    return [r.email for r in rows]


FACILITY_LABEL = {
    "medstar": "MedStar SMHC",
    "crmc":    "UM Charles Regional",
    "office":  "White Plains Office",
}


# ─── Recipient resolution ────────────────────────────────────────

def _scheduler_recipients(db: Session) -> list[User]:
    """Anyone in the Front Desk OR Office Manager group who's active."""
    out: dict[str, User] = {}
    for gname in ("Front Desk", "Office Manager"):
        grp = db.query(Group).filter(Group.name == gname).first()
        if not grp:
            continue
        for u in grp.members:
            if u.is_active and u.email not in out:
                out[u.email] = u
    return list(out.values())


def _office_manager_recipients(db: Session) -> list[User]:
    grp = db.query(Group).filter(Group.name == "Office Manager").first()
    if not grp:
        return []
    return [u for u in grp.members if u.is_active]


def _office_release_recipients(db) -> list:
    """Return the configured list if non-empty; otherwise fall back to the
    role-based query (schedulers + office managers). Falling back means we
    never silently lose alerts during rollout."""
    configured = _configured_recipients(db, "office_release")
    if configured:
        # Build lightweight User-like dicts so downstream notif code that
        # iterates email/notify_email/display_name still works without
        # touching the User model.
        from types import SimpleNamespace
        # slack_user_id=None so _resolve_slack_user_id can read it without
        # AttributeError if a future change enables notify_slack on configured
        # recipients. Slack is intentionally disabled because we don't map
        # arbitrary configured emails to Slack user IDs.
        return [SimpleNamespace(email=e, notify_email=True,
                                  notify_slack=False, slack_user_id=None,
                                  display_name=e)
                for e in configured]
    schedulers = _scheduler_recipients(db)
    managers   = _office_manager_recipients(db)
    seen = {}
    for u in schedulers + managers:
        if u.email not in seen:
            seen[u.email] = u
    return list(seen.values())


def _hospital_release_recipients(db) -> list:
    configured = _configured_recipients(db, "hospital_release")
    if configured:
        from types import SimpleNamespace
        # slack_user_id=None so _resolve_slack_user_id can read it without
        # AttributeError if a future change enables notify_slack on configured
        # recipients. Slack is intentionally disabled because we don't map
        # arbitrary configured emails to Slack user IDs.
        return [SimpleNamespace(email=e, notify_email=True,
                                  notify_slack=False, slack_user_id=None,
                                  display_name=e)
                for e in configured]
    return _scheduler_recipients(db)


# ─── Hospital release ────────────────────────────────────────────

def find_hospital_release_candidates(db: Session) -> list[BlockDay]:
    """Hospital block days in the next 14 days with 0 booked slots."""
    today = date.today()
    end = today + timedelta(days=_cfg(db, "hospital_lookahead_days"))
    candidates = (db.query(BlockDay)
                    .options(joinedload(BlockDay.slots))
                    .filter(BlockDay.facility.in_(["medstar", "crmc"]),
                            BlockDay.block_date >= today,
                            BlockDay.block_date <= end,
                            BlockDay.release_alert_sent_at.is_(None))
                    .order_by(BlockDay.block_date)
                    .all())
    return [bd for bd in candidates if not (bd.slots or [])]


def send_hospital_release_alert(recipients: list[User],
                                  days: list[BlockDay], db: Session) -> dict:
    """One digest per recipient covering all empty hospital block days."""
    if not days or not recipients:
        return {"sent": 0}

    rows_html = "".join(
        f'<li><strong>{bd.block_date}</strong> ({bd.block_date.strftime("%a")}) '
        f'— {FACILITY_LABEL.get(bd.facility, bd.facility)} '
        f'<span style="color:#999">{bd.start_time.strftime("%H:%M")}–{bd.end_time.strftime("%H:%M")}</span></li>'
        for bd in days
    )
    rows_text = "\n".join(
        f"  • {bd.block_date} ({bd.block_date.strftime('%a')}) — "
        f"{FACILITY_LABEL.get(bd.facility, bd.facility)} "
        f"{bd.start_time.strftime('%H:%M')}–{bd.end_time.strftime('%H:%M')}"
        for bd in days
    )

    lookahead = _cfg(db, "hospital_lookahead_days")
    subject = f"WWC · {len(days)} hospital block day(s) unbooked — release recommended"
    sent_count = 0
    for user in recipients:
        name = user.display_name or user.email.split("@")[0]
        html = f"""
        <p>Hi {name},</p>
        <p>The following hospital block days are within {lookahead} days and currently have
           no surgeries scheduled. Please contact the hospital to release these
           blocks (or book any pending cases that should fit):</p>
        <ul>{rows_html}</ul>
        <p><a href="https://gw.waldorfwomenscare.com/surgery/block-schedule" style="color:#7B2D5E">Open block schedule →</a></p>
        """
        text = (f"Hi {name}, {len(days)} hospital block day(s) unbooked within {lookahead} days:\n"
                f"{rows_text}\n\nBlock schedule: https://gw.waldorfwomenscare.com/surgery/block-schedule")

        if user.notify_email:
            if notif.send_email(user.email, subject, html, text):
                sent_count += 1
        if user.notify_slack:
            slack_text = (
                f"📅 *{len(days)}* hospital block day(s) unbooked within {lookahead} days:\n"
                + "\n".join(f"• {bd.block_date} ({bd.block_date.strftime('%a')}) — "
                            f"{FACILITY_LABEL.get(bd.facility, bd.facility)}"
                            for bd in days[:8])
                + (f"\n_…and {len(days) - 8} more_" if len(days) > 8 else "")
                + "\n<https://gw.waldorfwomenscare.com/surgery/block-schedule|Open →>"
            )
            notif.send_slack_dm(user, slack_text, db=db)
    return {"sent": sent_count}


# ─── Office under-booked release ─────────────────────────────────

def find_office_release_candidates(db: Session) -> list[BlockDay]:
    """Office procedure days that are exactly 6 days out with <6 booked."""
    target = date.today() + timedelta(days=_cfg(db, "office_lookahead_days"))
    candidates = (db.query(BlockDay)
                    .options(joinedload(BlockDay.slots))
                    .filter(BlockDay.facility == "office",
                            BlockDay.block_date == target,
                            BlockDay.release_alert_sent_at.is_(None))
                    .all())
    return [bd for bd in candidates if len(bd.slots or []) < _cfg(db, "office_full_threshold")]


def send_office_release_alert(scheduler_users: list[User],
                                manager_users: list[User],
                                days: list[BlockDay], db: Session) -> dict:
    """Combined recipient list. One per email."""
    if not days:
        return {"sent": 0}

    seen: dict[str, User] = {}
    for u in (scheduler_users + manager_users):
        seen[u.email] = u
    recipients = list(seen.values())
    if not recipients:
        return {"sent": 0}

    threshold = _cfg(db, "office_full_threshold")
    sent_count = 0
    for bd in days:
        booked = len(bd.slots or [])
        open_slots = threshold - booked
        subject = (f"WWC · Office procedure day {bd.block_date} only has {booked} of "
                   f"{threshold} booked — open the rest for clinic")
        for user in recipients:
            name = user.display_name or user.email.split("@")[0]
            html = f"""
            <p>Hi {name},</p>
            <p>Office procedure day on <strong>{bd.block_date}</strong>
              ({bd.block_date.strftime('%A')}) currently has only
              <strong>{booked}</strong> procedure(s) booked — fewer than the {threshold}
              we need for a full day.</p>
            <p>Please open the remaining ~{open_slots} slots for office patients
              (clinic visits) so the day stays productive.</p>
            <p><a href="https://gw.waldorfwomenscare.com/surgery/block-schedule" style="color:#7B2D5E">Open block schedule →</a></p>
            """
            text = (f"Hi {name}, office procedure day {bd.block_date} has only "
                    f"{booked}/{threshold} procedures booked. "
                    f"Open the rest of the day for clinic patients.\n\n"
                    f"https://gw.waldorfwomenscare.com/surgery/block-schedule")

            if user.notify_email:
                if notif.send_email(user.email, subject, html, text):
                    sent_count += 1
            if user.notify_slack:
                slack_text = (
                    f"🩺 Office procedure day *{bd.block_date}* "
                    f"({bd.block_date.strftime('%a')}) has only "
                    f"*{booked}/{threshold}* booked — open the rest for clinic.\n"
                    f"<https://gw.waldorfwomenscare.com/surgery/block-schedule|Open →>"
                )
                notif.send_slack_dm(user, slack_text, db=db)
    return {"sent": sent_count}


# ─── Orchestrator ───────────────────────────────────────────────

def run_release_sweep(db: Session) -> dict:
    now = datetime.utcnow()

    hospital_days = find_hospital_release_candidates(db)
    h_recipients = _hospital_release_recipients(db)
    h_result = send_hospital_release_alert(h_recipients, hospital_days, db)

    office_days = find_office_release_candidates(db)
    o_recipients = _office_release_recipients(db)
    o_result = send_office_release_alert(o_recipients, [], office_days, db)

    # Stamp release_alert_sent_at ONLY when the corresponding bucket
    # actually delivered to at least one recipient. Previously every
    # candidate was stamped regardless of SMTP outcome, so a single
    # full SMTP outage at sweep time permanently silenced the day.
    h_delivered = h_result.get("sent", 0) > 0
    o_delivered = o_result.get("sent", 0) > 0
    if h_delivered:
        for bd in hospital_days:
            bd.release_alert_sent_at = now
    if o_delivered:
        for bd in office_days:
            bd.release_alert_sent_at = now
    if h_delivered or o_delivered:
        db.commit()

    return {
        "hospital_unbooked": len(hospital_days),
        "hospital_emails_sent": h_result.get("sent", 0),
        "office_underbooked": len(office_days),
        "office_emails_sent": o_result.get("sent", 0),
    }
