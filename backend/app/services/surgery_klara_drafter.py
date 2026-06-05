"""Draft Klara messages for surgery patients.

Since Klara has no API, the system generates text and the staff member
copies it into the Klara web app. The drafter knows about:

  - the patient's first name, surgery, facility(ies), pt responsibility
  - whether clearance is required (and asks for cardiologist info if so)
  - the patient's eligible facility options when more than one is open
  - cancellation fee policy + 14-day window
  - waitlist messaging

A library of templates lives here; admins will eventually be able to edit
them in /admin (Phase 3). For now the templates are inline and well-
named so the user can come tell us how they want the wording tuned.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from app.models.surgery import Surgery


FACILITY_LABEL = {
    "medstar": "MedStar Southern Maryland Hospital Center",
    "crmc":    "University of Maryland Charles Regional Medical Center",
    "office":  "our White Plains office",
}

FACILITY_SHORT = {
    "medstar": "MedStar SMHC",
    "crmc":    "UM Charles Regional",
    "office":  "the office",
}

# Minutes before the scheduled surgery/procedure time that the patient
# needs to physically arrive. Hospitals need 2 hours of pre-op prep;
# the in-office procedure rooms only need a 15-minute check-in.
ARRIVAL_OFFSET_MIN = {
    "medstar": 120,
    "crmc":    120,
    "office":  15,
}


def arrival_time_str(start_hhmm, facility_code) -> str:
    """Return the patient's arrival time as 'HH:MM' (24h), or '' if
    either the time or the facility is missing/unknown. Accepts a
    datetime.time or a 'HH:MM' string."""
    if not start_hhmm or not facility_code:
        return ""
    offset = ARRIVAL_OFFSET_MIN.get(facility_code)
    if offset is None:
        return ""
    from datetime import datetime, date, timedelta, time as _time
    if hasattr(start_hhmm, "hour"):
        st = start_hhmm
    else:
        try:
            hh, mm = (int(p) for p in str(start_hhmm).split(":")[:2])
            st = _time(hh, mm)
        except Exception:
            return ""
    dt = datetime.combine(date.today(), st) - timedelta(minutes=offset)
    return dt.strftime("%H:%M")


def _first_name(s: Surgery) -> str:
    return s.first_name or (s.patient_name or "").split(",")[-1].strip().split(" ")[0] or "there"


def _proc_phrase(s: Surgery) -> str:
    procs = s.procedures or []
    if not procs:
        return "your procedure"
    parts = []
    for p in procs:
        d = (p.get("description") or "").strip()
        if d:
            parts.append(d)
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return f"{parts[0]} and {parts[1]}"
    return ", ".join(parts[:-1]) + ", and " + parts[-1]


def _facility_phrase(s: Surgery) -> str:
    if s.selected_facility:
        return FACILITY_LABEL.get(s.selected_facility, s.selected_facility)
    if s.eligible_facilities and len(s.eligible_facilities) > 1:
        labels = [FACILITY_SHORT.get(f, f) for f in s.eligible_facilities]
        return " or ".join(labels)
    if s.eligible_facilities:
        return FACILITY_LABEL.get(s.eligible_facilities[0], s.eligible_facilities[0])
    return "the facility"


def _money(v) -> str:
    if v is None:
        return "TBD"
    try:
        return f"${Decimal(str(v)):.2f}"
    except Exception:
        return f"${v}"


def _patient_link(s: Surgery) -> str:
    """Patient portal entry point. They sign in with DOB + last 4 of phone."""
    return "https://gw.waldorfwomenscare.com/portal/login"


# ─── Templates ───────────────────────────────────────────────────

def initial_scheduling(s: Surgery) -> dict:
    """First Klara message — scheduling outreach. Includes balance,
    next steps, and (when clearance_required) the cardiology-info ask."""
    name = _first_name(s)
    proc = _proc_phrase(s)
    facility = _facility_phrase(s)
    pt_resp = _money(s.patient_responsibility)
    link = _patient_link(s)

    msg = f"""Hi {name},

This is Waldorf Women's Care — Dr. {(s.surgeon_primary or 'Cooke').split(',')[0].split()[-1]}'s office.

We're getting your {proc} scheduled at {facility}. Here's what's coming next:

1. Your patient responsibility for this procedure is {pt_resp}.
   Please pay this through ModMed Pay before we can finalize a date.

2. Once your balance is clear, you'll be able to pick a surgery date here:
   {link}
   You'll need your DOB and the last 4 digits of your phone to verify.

3. After you pick a date, we'll send the consent forms via DocuSign.
"""

    if s.clearance_required:
        msg += """
4. **Important — medical clearance is required for this surgery.**
   Please schedule a clearance appointment with your primary care doctor
   AS SOON AS POSSIBLE — ideally 2-4 weeks before your surgery date. If
   their schedule is full or your PCP can't clear you, you may need a
   cardiologist to clear you instead.

   To save time: do you currently see a cardiologist?
     • If yes — please reply with their NAME, PHONE, and FAX so we can
       send them the clearance request directly.
     • If no — let us know; your PCP is the right starting point.

"""

    msg += """Reply here with any questions. Thanks!
— WWC Surgery Scheduling
"""
    return {
        "kind": "klara_initial",
        "subject": f"Surgery scheduling — {proc}",
        "body": msg.strip(),
    }


def date_reminder(s: Surgery) -> dict:
    """Reminder when patient hasn't picked a date yet."""
    name = _first_name(s)
    facility = _facility_phrase(s)
    pt_resp = _money(s.patient_responsibility)
    paid = (s.amount_paid or 0)
    balance = (s.patient_responsibility or 0) - paid
    link = _patient_link(s)

    body = f"""Hi {name},

Just a friendly check-in on scheduling your surgery at {facility}.
"""
    if balance > 0:
        body += f"""
Your balance is {_money(balance)} (of {pt_resp} total). Once paid through
ModMed Pay, you'll be able to pick a date at the link below.

"""
    body += f"""You can pick a date here when ready: {link}

Reply if you have any questions or need help with payment.
— WWC Surgery Scheduling
"""
    return {
        "kind": "klara_reminder",
        "subject": "Reminder: pick your surgery date",
        "body": body.strip(),
    }


def post_op_check_in(s: Surgery) -> dict:
    name = _first_name(s)
    body = f"""Hi {name},

Hope you're recovering well! How are you feeling today? Any of the following
post-op symptoms?

  • Heavy bleeding (more than a pad an hour for 2+ hours)
  • Fever over 100.4°F
  • Severe pain not controlled by your medication
  • Redness, swelling, or drainage from incision sites
  • Difficulty urinating or breathing
  • Anything else that feels off

Please reply yes/no — and if yes, please call us at 240-252-2140 right
away. Otherwise, see you at your post-op visit!

— WWC Surgery Scheduling
"""
    return {
        "kind": "klara_post_op",
        "subject": "Post-op check-in",
        "body": body.strip(),
    }


def waitlist_blast_for_open_slot(open_date: str, facility: str,
                                   procedure_kind: str) -> dict:
    """Generate the blast text for waitlist patients when an earlier slot
    opens. Staff sends to qualifying waitlist members who meet the
    advance-notice threshold."""
    facility_full = FACILITY_LABEL.get(facility, facility)
    body = f"""Hi — this is WWC Surgery Scheduling.

We have an open {procedure_kind.replace('_', ' ')} slot at {facility_full} on
**{open_date}**. We're reaching out to everyone on the waitlist who indicated
they could be ready in time.

If you'd like this date, reply **YES — {open_date}** as soon as you can.
The first patient to confirm gets the slot — others will go back on the list.

Reply **NO** if this date doesn't work; you'll stay on the waitlist for
future openings.

— WWC Surgery Scheduling
"""
    return {
        "kind": "waitlist_blast",
        "subject": f"Earlier surgery slot available — {open_date}",
        "body": body.strip(),
    }


# ─── Public API ──────────────────────────────────────────────────

DRAFTERS = {
    "initial_scheduling": initial_scheduling,
    "date_reminder":      date_reminder,
    "post_op_check_in":   post_op_check_in,
}


def draft(kind: str, s: Surgery) -> dict:
    if kind not in DRAFTERS:
        raise ValueError(f"unknown kind: {kind}")
    return DRAFTERS[kind](s)
