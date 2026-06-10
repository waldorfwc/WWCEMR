"""Behind-schedule sweep for surgeries.

Walks every active surgery (status: new, in_progress, confirmed) and
identifies its current milestone. If the milestone is overdue by >48h
relative to its expected_duration_days, sends one escalation message
to the surgery's escalate_to_email (defaulting to the office_manager
group when not set per-surgery).

Idempotent — uses Surgery.escalation_sent_at on the milestone row to
avoid spamming the same manager every hour.

Reuses the checklist notification helpers (send_email + send_slack_dm)
so messages land in the same channels.
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta
from app.utils.dt import now_utc_naive
from typing import Optional

from sqlalchemy.orm import Session, joinedload

from app.models.surgery import Surgery, SurgeryMilestone
from app.models.user import User
from app.services import checklist_notifications as notif

log = logging.getLogger(__name__)


# Behind-schedule threshold. Anything overdue by more than this gets
# the manager notified. <48h shows on the To-do panel only.
ESCALATION_HOURS = 48


def _current_milestone(s: Surgery) -> Optional[SurgeryMilestone]:
    pending = [m for m in (s.milestones or [])
               if m.status not in ("done", "skipped", "not_applicable")]
    pending.sort(key=lambda m: m.position)
    return pending[0] if pending else None


def _milestone_age_days(m: SurgeryMilestone) -> int:
    # Anchor to m.started_at, falling back to the surgery's creation
    # date when the milestone is still pending. Previously this fell
    # back to surgery.updated_at — which gets bumped by every unrelated
    # PATCH (SMS toggle, notes edit, surgeon swap), silently resetting
    # the overdue clock on every still-open milestone. The result was
    # that manager nudges never fired for genuinely-stale cases.
    base = m.started_at or m.surgery.created_at
    if not base:
        return 0
    base_date = base.date() if hasattr(base, 'date') else base
    return max(0, (date.today() - base_date).days)


def _resolve_recipient(db: Session, s: Surgery) -> Optional[User]:
    """If the surgery has escalate_to_email, send to that user. Otherwise
    fan out to every Office Manager group member — the first one with
    notify_email enabled gets the message (rest see it on the dashboard)."""
    if s.escalate_to_email:
        return (db.query(User)
                  .filter(User.email == s.escalate_to_email,
                          User.is_active.is_(True))
                  .first())
    # Default: pick anyone in Office Manager group
    from app.models.groups import Group
    grp = db.query(Group).filter(Group.name == "Office Manager").first()
    if grp:
        for u in grp.members:
            if u.is_active:
                return u
    return None


def run_escalation_sweep(db: Session) -> dict:
    """Hourly cron entry point. Returns counts."""
    surgeries = (db.query(Surgery)
                   .options(joinedload(Surgery.milestones))
                   .filter(Surgery.status.in_(["new", "in_progress", "confirmed"]))
                   .all())

    grouped: dict[str, list[dict]] = {}    # manager_email → [items]
    instances_to_mark: list[SurgeryMilestone] = []

    for s in surgeries:
        m = _current_milestone(s)
        if not m or not m.expected_duration_days:
            continue
        age = _milestone_age_days(m)
        overdue_days = age - m.expected_duration_days
        overdue_hours = overdue_days * 24
        if overdue_hours <= ESCALATION_HOURS:
            continue

        # Already escalated? skip (we only nag once per overdue cycle)
        if (m.data_json or {}).get("escalation_sent_at"):
            continue

        manager = _resolve_recipient(db, s)
        if not manager:
            log.info("Surgery %s: no escalation recipient available", s.id)
            continue

        item = {
            "surgery_id": str(s.id),
            "patient_name": s.patient_name,
            "chart_number": s.chart_number,
            "milestone": m.title,
            "hours_overdue": overdue_hours,
            "days_overdue": overdue_days,
        }
        grouped.setdefault(manager.email, []).append(item)
        instances_to_mark.append(m)

    sent = 0
    for email, items in grouped.items():
        manager = (db.query(User)
                     .filter(User.email == email, User.is_active.is_(True))
                     .first())
        if not manager:
            continue
        result = _send_surgery_escalation(manager, items, db=db)
        if result.get("sent"):
            sent += 1

    now = now_utc_naive().isoformat()
    for m in instances_to_mark:
        m.data_json = {**(m.data_json or {}), "escalation_sent_at": now}
    if instances_to_mark:
        db.commit()

    return {
        "managers_notified": sent,
        "milestones_escalated": len(instances_to_mark),
    }


def _send_surgery_escalation(manager: User, items: list[dict],
                              db: Optional[Session] = None) -> dict:
    """Email + Slack DM to one manager, listing all their behind-schedule
    surgeries in a single digest."""
    name = manager.display_name or manager.email.split("@")[0]
    subject = f"WWC · {len(items)} surgery milestone(s) >48h behind schedule"

    rows_html = "".join(
        f'<li><strong>{it["patient_name"]}</strong> — {it["milestone"]} '
        f'<span style="color:#999">({it["days_overdue"]}d behind, chart {it["chart_number"]})</span></li>'
        for it in items
    )
    rows_text = "\n".join(
        f"  • {it['patient_name']} — {it['milestone']} ({it['days_overdue']}d behind, chart {it['chart_number']})"
        for it in items
    )

    html = f"""
    <p>Hi {name},</p>
    <p>The following surgery milestones are more than 48h past their expected duration:</p>
    <ul>{rows_html}</ul>
    <p><a href="https://gw.waldorfwomenscare.com/surgery" style="color:#7B2D5E">Open surgery dashboard →</a></p>
    """
    text = (f"Hi {name}, {len(items)} surgery milestone(s) need attention:\n"
            f"{rows_text}\n\nDashboard: https://gw.waldorfwomenscare.com/surgery")

    email_ok = notif.send_email(manager.email, subject, html, text) if manager.notify_email else False
    slack_ok = False
    if manager.notify_slack:
        slack_text = (
            f"🚩 *{len(items)}* surgery milestone(s) >48h behind:\n"
            + "\n".join(f"• {it['patient_name']} — {it['milestone']} _({it['days_overdue']}d)_"
                        for it in items[:8])
            + (f"\n_…and {len(items) - 8} more_" if len(items) > 8 else "")
            + "\n<https://gw.waldorfwomenscare.com/surgery|Open dashboard →>"
        )
        slack_ok = notif.send_slack_dm(manager, slack_text, db=db)

    return {"sent": email_ok or slack_ok, "email": email_ok, "slack": slack_ok}
