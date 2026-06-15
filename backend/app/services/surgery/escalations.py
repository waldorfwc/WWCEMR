"""Behind-schedule sweep for surgeries.

Walks every active surgery (status: new, in_progress, confirmed) and asks
the steps engine whether its CURRENT step is behind schedule -- the same
signal the dashboard's Critical Alerts use (step_engine.is_behind with the
config-driven expected-days map and the `critical_overdue_hours` grace
window). Every behind surgery is grouped by its escalation recipient and
each manager gets a single digest email/Slack DM.

Milestones were retired in the 2026-06 steps cutover, so the sweep no
longer reads SurgeryMilestone rows (which were always empty post-cutover,
silently killing the manager push -- audit #9).

Idempotent -- uses Surgery.escalation_state (a {step_key: sent_iso} map)
to nag a manager only once per overdue current step. When a surgery
advances to a new current step the key changes, so a genuinely-new stall
re-escalates.

Reuses the checklist notification helpers (send_email + send_slack_dm)
so messages land in the same channels.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.models.surgery import Surgery
from app.models.user import User
from app.services import checklist_notifications as notif
from app.services.surgery import step_engine
from app.services.surgery.settings import cfg
from app.utils.dt import now_utc_naive

log = logging.getLogger(__name__)


# Manager escalation uses the SAME behind-schedule threshold as the
# dashboard's Critical Alerts: `critical_overdue_hours` (default 48h).
# The old milestone code hardcoded ESCALATION_HOURS = 48, identical to the
# dashboard default -- there was never a separate, longer "manager" window,
# so we read the one shared setting rather than reintroduce a constant.


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


def find_behind_surgeries(db: Session) -> list[tuple[Surgery, dict, int]]:
    """Active surgeries whose current step is behind schedule per the
    steps engine. Returns (surgery, current_step_dict, hours_overdue)
    tuples. Pure candidate selection — no sending, no DB writes — so it
    can be tested in isolation.
    """
    surgeries = (db.query(Surgery)
                   .filter(Surgery.status.in_(["new", "in_progress", "confirmed"]))
                   .all())

    grace = cfg(db, "critical_overdue_hours")
    out: list[tuple[Surgery, dict, int]] = []
    for s in surgeries:
        cur = step_engine.current_step(s)
        if cur is None:
            continue
        behind, hours_overdue = step_engine.is_behind(
            s,
            expected_days=step_engine.expected_days_map(db, s),
            grace_hours=grace,
        )
        if not behind:
            continue
        out.append((s, cur, hours_overdue))
    return out


def run_escalation_sweep(db: Session) -> dict:
    """Hourly cron entry point. Returns counts."""
    grouped: dict[str, list[dict]] = {}    # manager_email → [items]
    surgeries_to_mark: list[tuple[Surgery, str, str]] = []  # (surgery, step_key, now_iso)

    now_iso = now_utc_naive().isoformat()

    for s, cur, hours_overdue in find_behind_surgeries(db):
        step_key = cur["key"]

        # Already escalated for this current step? skip (nag once per cycle).
        if (s.escalation_state or {}).get(step_key):
            continue

        days_overdue = hours_overdue // 24

        # Log the overdue step to the in-app activity feed once per overdue
        # step. Deduped by escalation_state: we mark the step below (via
        # surgeries_to_mark) on every sweep where we take action, so the
        # gate above stops a re-log on the next sweep — even when no manager
        # recipient is available for the email digest.
        from app.services.surgery.activity import record_activity
        record_activity(
            db, s, "step_overdue",
            f"Overdue: {cur['title']} ({days_overdue}d behind)",
            actor="system")
        surgeries_to_mark.append((s, step_key, now_iso))

        manager = _resolve_recipient(db, s)
        if not manager:
            log.info("Surgery %s: no escalation recipient available", s.id)
            continue

        item = {
            "surgery_id": str(s.id),
            "patient_name": s.patient_name,
            "chart_number": s.chart_number,
            "milestone": cur["title"],
            "hours_overdue": hours_overdue,
            "days_overdue": days_overdue,
        }
        grouped.setdefault(manager.email, []).append(item)

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

    for s, step_key, ts in surgeries_to_mark:
        s.escalation_state = {**(s.escalation_state or {}), step_key: ts}
    if surgeries_to_mark:
        db.commit()

    return {
        "managers_notified": sent,
        "surgeries_escalated": len(surgeries_to_mark),
    }


def _send_surgery_escalation(manager: User, items: list[dict],
                              db: Optional[Session] = None) -> dict:
    """Email + Slack DM to one manager, listing all their behind-schedule
    surgeries in a single digest."""
    name = manager.display_name or manager.email.split("@")[0]
    subject = f"WWC · {len(items)} surgery step(s) behind schedule"

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
    <p>The following surgery steps are behind their expected schedule:</p>
    <ul>{rows_html}</ul>
    <p><a href="https://gw.waldorfwomenscare.com/surgery" style="color:#7B2D5E">Open surgery dashboard →</a></p>
    """
    text = (f"Hi {name}, {len(items)} surgery step(s) need attention:\n"
            f"{rows_text}\n\nDashboard: https://gw.waldorfwomenscare.com/surgery")

    email_ok = notif.send_email(manager.email, subject, html, text) if manager.notify_email else False
    slack_ok = False
    if manager.notify_slack:
        slack_text = (
            f"🚩 *{len(items)}* surgery step(s) behind schedule:\n"
            + "\n".join(f"• {it['patient_name']} — {it['milestone']} _({it['days_overdue']}d)_"
                        for it in items[:8])
            + (f"\n_…and {len(items) - 8} more_" if len(items) > 8 else "")
            + "\n<https://gw.waldorfwomenscare.com/surgery|Open dashboard →>"
        )
        slack_ok = notif.send_slack_dm(manager, slack_text, db=db)

    return {"sent": email_ok or slack_ok, "email": email_ok, "slack": slack_ok}
