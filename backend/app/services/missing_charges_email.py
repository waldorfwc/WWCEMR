"""Weekly provider emails for the Missing Charges workflow.

For each provider with open `needs_to_be_billed` rows:
  1. Look up the provider's user account by matching their display name
     (the Excel uses "Last, First" and so do our users' full_name).
  2. Mint a 60-day signed-token portal URL.
  3. Send an HTML+text email listing each row with one-click 'Billed' / 'Error'
     links into the portal.
  4. Stamp `last_emailed_at` on every row included.

If SMTP isn't configured, `send_email` returns False and we log instead —
still useful for previewing the email content.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Optional
from urllib.parse import quote

from sqlalchemy.orm import Session

from app.models.missing_charge import MissingCharge, ProviderUserMapping
from app.models.user import User
from app.services import missing_charges_token as token_svc
from app.services.checklist_notifications import send_email

log = logging.getLogger(__name__)


def _app_base_url() -> str:
    """Public base URL for portal links — used in the email body."""
    return (os.environ.get("APP_BASE_URL")
            or "https://gw.waldorfwomenscare.com").rstrip("/")


def _provider_user(db: Session, provider_name: str) -> Optional[User]:
    """Match the Excel provider string ('Last, First') to a User row.

    Order of attempts:
      1. Explicit ProviderUserMapping row (active)
      2. Exact User.display_name match
      3. Reversed 'First Last' match if Excel uses 'Last, First'
    """
    if not provider_name:
        return None

    # 1. Explicit mapping — wins over any fuzzy match. Ignored mappings
    #    return None (cron skips with status='ignored' downstream).
    mapping = (db.query(ProviderUserMapping)
                 .filter(ProviderUserMapping.provider_name == provider_name,
                         ProviderUserMapping.is_active == "Y")
                 .first())
    if mapping:
        if mapping.is_ignored == "Y":
            return None
        if mapping.user_email:
            u = db.query(User).filter(User.email == mapping.user_email,
                                        User.is_active.is_(True)).first()
            if u:
                return u
        # Active mapping with no email AND not ignored — treat as no match
        return None

    # 2. Exact display_name match
    u = db.query(User).filter(User.display_name == provider_name,
                               User.is_active.is_(True)).first()
    if u:
        return u

    # 3. 'Last, First' → 'First Last' fallback
    if "," in provider_name:
        last, first = [s.strip() for s in provider_name.split(",", 1)]
        reversed_name = f"{first} {last}".strip()
        u = db.query(User).filter(User.display_name == reversed_name,
                                   User.is_active.is_(True)).first()
        if u:
            return u
    return None


def _build_email(provider: str, portal_url: str, rows: list[MissingCharge]) -> tuple[str, str, str]:
    """Return (subject, html_body, text_body)."""
    count = len(rows)
    subject = f"WWC · {count} appointment{'s' if count != 1 else ''} needs charging"

    # HTML rows
    rows_html = ""
    rows_text = ""
    for c in rows:
        rows_html += (
            f'<tr>'
            f'<td style="padding:6px 8px;border-top:1px solid #eee">{c.appointment_date}</td>'
            f'<td style="padding:6px 8px;border-top:1px solid #eee">{(c.patient_name or "—")}<br>'
            f'<span style="font-size:11px;color:#888">MRN {c.patient_mrn}</span></td>'
            f'<td style="padding:6px 8px;border-top:1px solid #eee">{c.appointment_type or "—"}</td>'
            f'<td style="padding:6px 8px;border-top:1px solid #eee">{c.payer or "—"}</td>'
            f'</tr>'
        )
        link_note = (
            f" ({c.patient_link})" if c.patient_link else ""
        )
        rows_text += (
            f"  • {c.appointment_date} — {c.patient_name} "
            f"(MRN {c.patient_mrn}) — {c.appointment_type or '—'}{link_note}\n"
        )

    html = f"""\
<html><body style="font-family:Helvetica,Arial,sans-serif;font-size:14px;color:#333">
<p>Hi Dr. {provider.split(',')[0]},</p>

<p>You have <strong>{count} appointment{'s' if count != 1 else ''}</strong> that need
charges entered. Open each one in ModMed, finish the note so it bills, then click
<strong>Mark as billed</strong> in the portal.</p>

<p style="margin:14px 0">
  <a href="{portal_url}"
     style="background:#7c2d92;color:#fff;padding:10px 14px;
            text-decoration:none;border-radius:4px;font-weight:600">
     Open the missing-charges portal
  </a>
</p>

<table cellspacing="0" cellpadding="0"
       style="border-collapse:collapse;font-size:13px;width:100%;border:1px solid #ddd">
  <thead style="background:#f5f0f8">
    <tr>
      <th style="padding:6px 8px;text-align:left">Date</th>
      <th style="padding:6px 8px;text-align:left">Patient</th>
      <th style="padding:6px 8px;text-align:left">Appointment type</th>
      <th style="padding:6px 8px;text-align:left">Payer</th>
    </tr>
  </thead>
  <tbody>
    {rows_html}
  </tbody>
</table>

<p style="font-size:12px;color:#888;margin-top:18px">
  The portal link is good for 60 days. If you can't bill a row (visit was canceled,
  no-show, etc.) click <em>Error</em> and explain — the biller will follow up.
</p>
</body></html>"""

    text = f"""\
Hi Dr. {provider.split(',')[0]},

You have {count} appointment(s) that need charges entered. Open each in ModMed,
finish the note, then click "Mark as billed" in the portal.

Portal: {portal_url}

Pending appointments:
{rows_text}

(The portal link is good for 60 days. If you can't bill a row, click Error
and explain — the biller will follow up.)
"""
    return subject, html, text


def send_provider_emails(db: Session, *, triggered_by: str = "system") -> dict:
    """Send one email per provider. Returns a report dict."""
    open_rows = (db.query(MissingCharge)
                   .filter(MissingCharge.status == "needs_to_be_billed",
                           MissingCharge.primary_provider.isnot(None))
                   .all())

    # Group by provider name
    by_provider: dict[str, list[MissingCharge]] = {}
    for c in open_rows:
        by_provider.setdefault(c.primary_provider, []).append(c)

    base = _app_base_url()
    report = {
        "triggered_by": triggered_by,
        "providers": [],
        "total_rows": len(open_rows),
        "sent_count": 0,
        "skipped_count": 0,
    }

    for provider, rows in by_provider.items():
        # Check if this provider is explicitly ignored — skip with a clear status
        ignored = (db.query(ProviderUserMapping)
                     .filter(ProviderUserMapping.provider_name == provider,
                             ProviderUserMapping.is_active == "Y",
                             ProviderUserMapping.is_ignored == "Y")
                     .first())
        if ignored:
            report["providers"].append({
                "provider": provider,
                "row_count": len(rows),
                "status": "ignored",
                "portal_url": None,
            })
            report["skipped_count"] += 1
            continue

        token = token_svc.mint_token(provider)
        portal_url = f"{base}/p/missing-charges/{quote(token)}"

        user = _provider_user(db, provider)
        if not user or not user.email:
            report["providers"].append({
                "provider": provider,
                "row_count": len(rows),
                "status": "skipped_no_email",
                "portal_url": portal_url,
            })
            report["skipped_count"] += 1
            continue

        subject, html, text = _build_email(provider, portal_url, rows)
        ok = send_email(user.email, subject, html, text)

        # Stamp last_emailed_at on every row included
        for c in rows:
            c.last_emailed_at = datetime.utcnow()
        db.commit()

        report["providers"].append({
            "provider": provider,
            "email": user.email,
            "row_count": len(rows),
            "status": "sent" if ok else "logged_only",
            "portal_url": portal_url,
        })
        if ok:
            report["sent_count"] += 1

    return report
