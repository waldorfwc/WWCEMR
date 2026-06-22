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
