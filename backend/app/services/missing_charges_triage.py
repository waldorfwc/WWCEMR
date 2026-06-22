"""Triage reminder for untriaged (status='new') missing-charge rows.

Weekly, if any `new` rows exist, email + Slack-DM the configured biller
recipient(s). Recipients live in PracticeConfig (a single CSV value).
"""
import logging
from sqlalchemy.orm import Session

from app.models.practice_config import PracticeConfig
from app.models.missing_charge import MissingCharge
from app.models.user import User
from app.services.checklist_notifications import send_email, send_slack_dm
from app.services.missing_charges_email import _app_base_url
from app.utils.dt import now_utc_naive

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
