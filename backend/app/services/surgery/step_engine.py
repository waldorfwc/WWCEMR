"""Server-side Steps engine — single source of truth for surgery workflow
progress.

Port of the frontend's STEP_CFG_HOSPITAL / STEP_CFG_OFFICE +
stepCompletion() (SurgeryDetail.jsx). Replaces the retired milestone
system for behind-schedule / Critical Alerts. Pure functions over the
Surgery row — no writes.

State values: done | in_progress | todo | n/a
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Optional


@dataclass(frozen=True)
class StepDef:
    n: int
    key: str
    title: str
    optional: bool = False


HOSPITAL_STEPS: list[StepDef] = [
    StepDef(1,  "surgery_info",     "Surgery Info"),
    StepDef(2,  "benefits",         "Surgery Benefits"),
    StepDef(3,  "payment",          "Payment"),
    StepDef(4,  "consents",         "Consents"),
    StepDef(5,  "select_dates",     "Select Surgery Date & Post-Op Dates"),
    StepDef(6,  "device",           "Allocate Device", optional=True),
    StepDef(7,  "prior_auth",       "Prior Auth", optional=True),
    StepDef(8,  "clearance",        "Clearance / EKG", optional=True),
    StepDef(9,  "asst_surgeon",     "Asst Surgeon / Device Rep", optional=True),
    StepDef(10, "post_to_hospital", "Post Surgery to Hospital"),
    StepDef(11, "modmed_appt",      "Add Surgery Appointment to ModMed"),
    StepDef(12, "labs",             "Labs"),
    StepDef(13, "welfare_fu",       "Post Surgery Welfare F/U"),
    StepDef(14, "notes_reports",    "Surgery Notes & Reports"),
    StepDef(15, "bill",             "Bill Surgery"),
]

OFFICE_STEPS: list[StepDef] = [
    StepDef(1,  "surgery_info", "Add Surgery"),
    StepDef(2,  "benefits",     "Procedure Benefits"),
    StepDef(3,  "payment",      "Payment"),
    StepDef(4,  "consents",     "Consents"),
    StepDef(5,  "select_dates", "Select Procedure Date & Post-Op Dates"),
    StepDef(6,  "device",       "Allocate Device", optional=True),
    StepDef(7,  "prior_auth",   "Prior Auth", optional=True),
    StepDef(8,  "device_rep",   "Device Rep", optional=True),
    StepDef(9,  "modmed_appt",  "Add Procedure Appointment to ModMed"),
    StepDef(10, "welfare_fu",   "Post Surgery Welfare F/U"),
    StepDef(11, "path_report",  "Procedure Pathology Report", optional=True),
    StepDef(12, "bill",         "Bill Surgery"),
]

DEFAULT_EXPECTED_DAYS_HOSPITAL: dict[str, int] = {
    "surgery_info": 3, "benefits": 3, "payment": 5, "consents": 3,
    "select_dates": 14, "device": 3, "prior_auth": 5, "clearance": 5,
    "asst_surgeon": 5, "post_to_hospital": 2, "modmed_appt": 2,
    "labs": 3, "welfare_fu": 3, "notes_reports": 14, "bill": 7,
}
DEFAULT_EXPECTED_DAYS_OFFICE: dict[str, int] = {
    "surgery_info": 3, "benefits": 3, "payment": 5, "consents": 3,
    "select_dates": 14, "device": 3, "prior_auth": 5, "device_rep": 5,
    "modmed_appt": 2, "welfare_fu": 3, "path_report": 14, "bill": 7,
}

PRE_OP_STEP_KEYS_HOSPITAL = {st.key for st in HOSPITAL_STEPS[:12]}
PRE_OP_STEP_KEYS_OFFICE = {st.key for st in OFFICE_STEPS[:9]}

_STEP_DONE_TIMESTAMPS = {
    "benefits": "benefits_verified_at",
    "asst_surgeon": "assistant_surgeon_appt_confirmed_at",
    "device_rep": "assistant_surgeon_appt_confirmed_at",
    "post_to_hospital": "calendar_invite_sent_at",
    "modmed_appt": "scheduled_in_modmed_at",
    "bill": "billed_at",
}


def _is_office(s: Any) -> bool:
    return s.selected_facility == "office"


def steps_for(s: Any) -> list[StepDef]:
    return OFFICE_STEPS if _is_office(s) else HOSPITAL_STEPS


def _as_list(v: Any) -> list:
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except Exception:
            v = [v]
    return v or []


def surgery_info_missing(s: Any) -> list[str]:
    """Port of frontend checkSurgeryInfoMissing()."""
    missing: list[str] = []

    def need(cond, label):
        if not cond:
            missing.append(label)

    need(s.chart_number, "Chart number")
    need(s.patient_name, "Patient name")
    need(s.dob, "Date of birth")
    need(getattr(s, "cell_phone", None) or s.phone, "Phone")
    need(s.email, "Email")
    need(s.address_street, "Street address")
    need(s.address_city, "City")
    need(s.address_state, "State")
    need(s.address_zip, "ZIP code")
    need(s.primary_insurance, "Primary insurance")
    need(s.primary_member_id, "Primary member ID")
    need(s.surgeon_primary, "Surgeon")
    procs = _as_list(s.procedures)
    need(any((isinstance(p, dict) and (p.get("cpt") or p.get("description")))
             for p in procs), "At least one procedure (CPT)")
    dxs = _as_list(s.diagnoses)
    need(any((isinstance(d, dict) and (d.get("icd") or d.get("description")))
             for d in dxs), "At least one diagnosis (ICD-10)")
    need(s.estimated_minutes and float(s.estimated_minutes) > 0,
         "Estimated minutes")
    need(_as_list(s.eligible_facilities), "Eligible facility")
    need(s.preop_date, "Pre-op date")
    need(s.auth_status, "Prior-auth status decided")
    if getattr(s, "clearance_required", False):
        need(s.clearance_status, "Clearance status decided")
    if getattr(s, "assistant_surgeon_required", False):
        need(s.assistant_surgeon_name, "Assistant surgeon name")
    return missing


def _state(s: Any, key: str) -> str:
    """Completion state for one step key. Port of stepCompletion() /
    stepCompletionOffice() with one backend improvement: the device step
    reads the real device_required/device_assigned columns instead of
    being permanently 'todo'."""
    if key == "surgery_info":
        return "done" if not surgery_info_missing(s) else "todo"
    if key == "benefits":
        return "done" if s.benefits_verified_at else "todo"
    if key == "payment":
        resp = float(s.patient_responsibility or 0)
        paid = float(s.amount_paid or 0)
        if resp <= 0:
            return "done"
        return "done" if paid >= resp else "todo"
    if key == "consents":
        cs = (s.consent_status or "").lower()
        return "done" if cs in ("signed", "not_required") else "todo"
    if key == "select_dates":
        picked, post = bool(s.scheduled_date), bool(s.post_op_appt_date)
        if picked and post:
            return "done"
        if picked or post:
            return "in_progress"
        return "todo"
    if key == "device":
        if not getattr(s, "device_required", False):
            return "n/a"
        return "done" if getattr(s, "device_assigned", False) else "todo"
    if key == "prior_auth":
        status = (s.auth_status or "").lower()
        if status == "not_required":
            return "n/a"
        return "done" if status in ("approved", "completed") else "todo"
    if key == "clearance":
        cs = (s.clearance_status or "").lower()
        if cs == "not_required" or not getattr(s, "clearance_required", False):
            return "n/a"
        return ("done" if cs in ("received", "sent_to_hospital", "completed")
                else "todo")
    if key in ("asst_surgeon", "device_rep"):
        if not getattr(s, "assistant_surgeon_required", False):
            return "n/a"
        if (s.assistant_surgeon_office_notified_at
                and s.assistant_surgeon_appt_confirmed_at):
            return "done"
        return "todo"
    if key == "post_to_hospital":
        return "done" if s.calendar_invite_sent_at else "todo"
    if key == "modmed_appt":
        return "done" if s.scheduled_in_modmed_at else "todo"
    if key == "labs":
        return "done" if s.labs_sent_to_hospital else "todo"
    if key == "welfare_fu":
        pocs = (s.post_op_call_status or "").lower()
        return "done" if pocs == "spoke to pt." else "todo"
    if key == "notes_reports":
        ors = (s.operative_report_status or "").lower()
        return "done" if ors in ("completed", "received") else "todo"
    if key == "path_report":
        ors = (s.operative_report_status or "").lower()
        if ors == "not_required":
            return "n/a"
        return "done" if ors in ("completed", "received") else "todo"
    if key == "bill":
        return "done" if s.payment_posted_to_billing else "todo"
    return "todo"


def compute_steps(s: Any, titles: Optional[dict] = None) -> list:
    """Full step list with state — what the serializer emits."""
    titles = titles or {}
    out = []
    for st in steps_for(s):
        out.append({
            "n": st.n,
            "key": st.key,
            "title": titles.get(st.key, st.title),
            "optional": st.optional,
            "state": _state(s, st.key),
        })
    return out


def current_step(s: Any) -> Optional[dict]:
    """First step that is neither done nor n/a."""
    for step in compute_steps(s):
        if step["state"] in ("todo", "in_progress"):
            return step
    return None


def _entered_at(s: Any) -> Optional[datetime]:
    """Approximate when the current step was entered: the latest known
    completion timestamp among done steps, else updated_at/created_at."""
    stamps = []
    states = {st["key"]: st["state"] for st in compute_steps(s)}
    for key, field in _STEP_DONE_TIMESTAMPS.items():
        if states.get(key) == "done":
            v = getattr(s, field, None)
            if v:
                stamps.append(v)
    if stamps:
        return max(stamps)
    return s.updated_at or s.created_at


def is_behind(s: Any, *, expected_days: Optional[dict] = None,
              grace_hours: int = 48) -> tuple:
    """(is_behind, hours_overdue) for the surgery's current step."""
    cur = current_step(s)
    if cur is None:
        return False, 0
    defaults = (DEFAULT_EXPECTED_DAYS_OFFICE if _is_office(s)
                else DEFAULT_EXPECTED_DAYS_HOSPITAL)
    exp = (expected_days or {}).get(cur["key"], defaults.get(cur["key"], 7))
    base = _entered_at(s)
    if not base:
        return False, 0
    base_date = base.date() if hasattr(base, "date") else base
    age_days = max(0, (date.today() - base_date).days)
    overdue_days = age_days - int(exp)
    if overdue_days <= 0:
        return False, 0
    overdue_hours = overdue_days * 24
    return overdue_hours > grace_hours, overdue_hours


def expected_days_map(db, s: Any) -> dict:
    """Config-driven expected-days map for this surgery's pathway."""
    from app.services.surgery.settings import cfg
    key = ("step_expected_days_office" if _is_office(s)
           else "step_expected_days_hospital")
    defaults = (DEFAULT_EXPECTED_DAYS_OFFICE if _is_office(s)
                else DEFAULT_EXPECTED_DAYS_HOSPITAL)
    override = cfg(db, key) or {}
    return {**defaults, **{k: int(v) for k, v in override.items()}}


def titles_map(db, s: Any) -> dict:
    from app.services.surgery.settings import cfg
    key = "step_titles_office" if _is_office(s) else "step_titles_hospital"
    return cfg(db, key) or {}
