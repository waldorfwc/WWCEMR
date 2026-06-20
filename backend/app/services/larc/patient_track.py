"""Project a LarcAssignment's internal milestones onto the 5 patient-visible
status steps. Used by the patient portal tracker and per-step notifications."""
from __future__ import annotations
from app.models.larc import LarcAssignment

_PHARMACY = [
    ("request_received",     "Provider Request Received", None),
    ("enrollment_completed", "Enrollment Form Completed", "enrollment_signed"),
    ("enrollment_faxed",     "Enrollment Form Faxed",     "request_faxed"),
    ("device_received",      "Device Received",           "device_received"),
    ("patient_notified",     "Patient Notified",          "patient_notified"),
]
_PRACTICE = [
    ("request_received",         "Provider Request Received",        None),
    ("responsibility_determined","Patient Responsibility Determined","benefits_verified"),
    ("responsibility_satisfied", "Patient Responsibility Satisfied", "__paid__"),
    ("device_allocated",         "Device Allocated",                 "__allocated__"),
    ("patient_notified",         "Patient Notified",                 "patient_notified"),
]


def _done(a: LarcAssignment, kind) -> bool:
    if kind is None:
        return True                       # request_received: true once the row exists
    if kind == "__paid__":
        return a.patient_paid_at is not None
    if kind == "__allocated__":
        return a.device_id is not None
    for m in (a.milestones or []):
        if m.kind == kind:
            return m.status in ("done", "skipped", "not_applicable")
    return False


def patient_track(a: LarcAssignment) -> dict:
    track = "pharmacy" if a.source_flow == "pharmacy_order" else "practice_owned"
    spec = _PHARMACY if track == "pharmacy" else _PRACTICE
    steps, marked_current = [], False
    for key, label, kind in spec:
        if _done(a, kind):
            status = "done"
        elif not marked_current:
            status = "current"; marked_current = True
        else:
            status = "upcoming"
        steps.append({"key": key, "label": label, "status": status})
    return {"track": track, "steps": steps}
