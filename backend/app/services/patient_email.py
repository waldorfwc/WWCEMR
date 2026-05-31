"""Patient transactional email — render template + send + audit.

Soft-fail by default: if SMTP or template lookup fails, we write a
PatientEmail row with status='failed' or 'skipped' and return. We never
raise into the caller — patient communication is a side effect of the
primary action (booking, payment, etc.), not the action itself.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.patient_email import (
    EmailTemplate, PatientEmail,
)
from app.services.checklist_notifications import send_email

log = logging.getLogger(__name__)


_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


def render(template_text: str, context: dict) -> str:
    """Substitute {{var}} placeholders. Missing vars render as ''."""
    def _repl(m):
        return str(context.get(m.group(1), ""))
    return _VAR_RE.sub(_repl, template_text)


def _strip_html(html: str) -> str:
    """Cheap fallback when a template has no text_body."""
    no_tags = re.sub(r"<[^>]+>", "", html)
    return re.sub(r"\s+", " ", no_tags).strip()


def send_patient_email(
    db: Session,
    *,
    kind: Optional[str],
    to_email: Optional[str],
    context: dict,
    sent_by: str,
    surgery_id: Optional[Any] = None,
    chart_number: Optional[str] = None,
    ad_hoc_subject: Optional[str] = None,
    ad_hoc_html: Optional[str] = None,
) -> PatientEmail:
    """Send a patient email and write the audit row.

    Two modes:
      Template send  — pass `kind`. Subject + body come from EmailTemplate
                       and get rendered with `context`.
      Ad-hoc send    — pass `kind=None` plus `ad_hoc_subject` + `ad_hoc_html`.
                       The composer endpoint uses this.

    Returns the PatientEmail row regardless of success/failure so the
    caller can inspect `.status`.
    """
    # Resolve subject + body
    subject = None
    html = None
    if kind:
        template = (db.query(EmailTemplate)
                      .filter(EmailTemplate.kind == kind,
                              EmailTemplate.is_active.is_(True))
                      .first())
        if template is None:
            return _record(db, status="skipped",
                           failure_reason=f"no active template for kind={kind}",
                           kind=kind, to_email=to_email or "(missing)",
                           subject="(unrendered)", html="(unrendered)",
                           context=context, sent_by=sent_by,
                           surgery_id=surgery_id, chart_number=chart_number)
        subject = render(template.subject, context)
        html    = render(template.html_body, context)
        text    = render(template.text_body, context) if template.text_body else _strip_html(html)
    else:
        if not ad_hoc_subject or not ad_hoc_html:
            return _record(db, status="skipped",
                           failure_reason="ad-hoc send missing subject or html",
                           kind=None, to_email=to_email or "(missing)",
                           subject=ad_hoc_subject or "(missing)",
                           html=ad_hoc_html or "(missing)",
                           context=context, sent_by=sent_by,
                           surgery_id=surgery_id, chart_number=chart_number)
        subject = render(ad_hoc_subject, context)
        html    = render(ad_hoc_html, context)
        text    = _strip_html(html)

    if not to_email:
        return _record(db, status="skipped",
                       failure_reason="recipient email address is blank",
                       kind=kind, to_email="(missing)",
                       subject=subject, html=html,
                       context=context, sent_by=sent_by,
                       surgery_id=surgery_id, chart_number=chart_number)

    try:
        ok = send_email(to_email, subject, html, text)
    except Exception as e:
        log.warning("patient email SMTP raised: %s", e)
        ok = False
    status = "sent" if ok else "failed"
    failure_reason = None if ok else "SMTP send returned False (check SMTP_* config)"

    return _record(db, status=status, failure_reason=failure_reason,
                   kind=kind, to_email=to_email,
                   subject=subject, html=html,
                   context=context, sent_by=sent_by,
                   surgery_id=surgery_id, chart_number=chart_number)


def _record(db, *, status, failure_reason, kind, to_email, subject, html,
            context, sent_by, surgery_id, chart_number) -> PatientEmail:
    row = PatientEmail(
        surgery_id=surgery_id, chart_number=chart_number,
        to_email=to_email, template_kind=kind,
        rendered_subject=subject, rendered_html=html,
        status=status, failure_reason=failure_reason,
        sent_by=sent_by, context=context or {},
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row
