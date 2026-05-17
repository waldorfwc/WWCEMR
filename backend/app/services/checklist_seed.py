"""Seed daily-task templates for Phase A — 4 core roles, ~20 templates total.

Run via: python scripts/seed_checklist_templates.py
"""
from __future__ import annotations

from datetime import time
from typing import List, Dict, Any

from sqlalchemy.orm import Session

from app.models.checklist import TaskTemplate


# (role, category, title, description, due_time, priority)
SEED_TEMPLATES: List[Dict[str, Any]] = [

    # ─── MA (Medical Assistant) ─────────────────────────────────────
    dict(role="ma", category="clinical", title="Stock exam rooms",
         description="Restock all exam rooms before first patient: gowns, drapes, speculums, gel, gloves, table paper, alcohol prep.",
         due_time=time(8, 0), priority="high"),

    dict(role="ma", category="compliance", title="Vaccine fridge / freezer temperature log",
         description="Record morning + afternoon temperatures for vaccine refrigerator and freezer. Out-of-range readings (fridge: <36°F or >46°F; freezer: <-58°F or >5°F) require immediate action.",
         due_time=time(9, 0), priority="critical"),

    dict(role="ma", category="clinical", title="Specimens picked up by courier",
         description="Confirm all morning specimens (Pap smears, biopsies, cultures, urine) handed off to courier (LabCorp / Quest). Update specimen log.",
         due_time=time(15, 30), priority="high"),

    dict(role="ma", category="communication", title="Klara clinical messages cleared",
         description="All clinical Klara messages assigned to MA queue resolved or escalated to provider before EOD.",
         due_time=time(17, 30), priority="high"),

    dict(role="ma", category="compliance", title="Autoclave end-of-day cycle log",
         description="Log today's autoclave cycle(s): date, load contents, operator initials, cycle parameters, indicator results.",
         due_time=time(17, 45), priority="critical"),

    dict(role="ma", category="clinical", title="Sharps container check",
         description="Replace any sharps container at 75% fill or higher. Log replacement.",
         due_time=time(17, 45), priority="medium"),

    # ─── Pellet daily count (DEA Schedule III — per location) ───────
    dict(role="ma", category="compliance", title="Pellet daily count — White Plains",
         description=("Walk the White Plains double-locked box. Count every testosterone "
                      "(Sch III) and estradiol lot. Open Pellet inventory → Daily count → "
                      "Start count at White Plains. Confirm any pellet insertions for "
                      "appointments today or earlier are confirmed first — the count "
                      "cannot start otherwise."),
         due_time=time(9, 0), priority="critical"),

    dict(role="ma", category="compliance", title="Pellet daily count — Brandywine",
         description=("Walk the Brandywine pellet storage. Count every lot on hand at "
                      "Brandywine. Open Pellet inventory → Daily count → Start count at "
                      "Brandywine. Confirm any pellet insertions at Brandywine for "
                      "appointments today or earlier are confirmed first."),
         due_time=time(9, 0), priority="critical"),

    dict(role="ma", category="compliance", title="Pellet daily count — Arlington",
         description=("Walk the Arlington pellet storage. Count every lot on hand at "
                      "Arlington. Open Pellet inventory → Daily count → Start count at "
                      "Arlington. Confirm any pellet insertions at Arlington for "
                      "appointments today or earlier are confirmed first."),
         due_time=time(9, 0), priority="critical"),

    # ─── FRONT DESK ─────────────────────────────────────────────────
    dict(role="front_desk", category="admin", title="Print today's schedule + flag insurance issues",
         description="Print the day's appointments. Cross-check against yesterday's eligibility verifications — flag any patients whose coverage was unconfirmed.",
         due_time=time(7, 45), priority="high"),

    dict(role="front_desk", category="billing", title="Verify insurance for today's appointments",
         description="Run real-time eligibility check for every patient on today's schedule. Note copay amount + deductible status on each chart.",
         due_time=time(8, 30), priority="critical"),

    dict(role="front_desk", category="communication", title="Confirm tomorrow's appointments",
         description="Send appointment confirmation calls / texts for tomorrow's patients. Rebook any cancellations and call the waitlist.",
         due_time=time(15, 0), priority="high"),

    dict(role="front_desk", category="admin", title="Mark all absent patients (No-Show / Cancelled)",
         description="Every patient on today's schedule must be marked: arrived, no-show, or cancelled. Zero unmarked at EOD. No-shows get logged for billing follow-up.",
         due_time=time(17, 30), priority="critical"),

    dict(role="front_desk", category="billing", title="Close daily transactions",
         description="Run end-of-day batch close in EHR. Reconcile cash drawer + credit card receipts against batch total. Generate deposit slip.",
         due_time=time(17, 45), priority="critical"),

    dict(role="front_desk", category="communication", title="All Klara messages resolved",
         description="All non-clinical Klara messages (scheduling, billing, demographics) handled or assigned to next-day owner.",
         due_time=time(17, 45), priority="high"),

    # ─── BILLING — PAYMENTS ─────────────────────────────────────────
    dict(role="billing_payments", category="billing", title="Post insurance ERAs",
         description="Auto-post available ERAs; investigate any held/error items in the unposted ERA queue.",
         due_time=time(14, 0), priority="high"),

    dict(role="billing_payments", category="billing", title="Post patient payments",
         description="Apply payments handed off from front desk to claims/balances.",
         due_time=time(14, 30), priority="high"),

    dict(role="billing_payments", category="billing", title="Reconcile bank deposit",
         description="Compare yesterday's posted payments against bank deposit (BAI2 file). Flag discrepancies for review.",
         due_time=time(15, 0), priority="critical"),

    # ─── BILLING — DENIALS ──────────────────────────────────────────
    dict(role="billing_denials", category="billing", title="Work the Denials queue",
         description="Open Active AR → Denials tab. Triage any new appealable denials. Draft appeals for any nearing TF deadline.",
         due_time=time(11, 0), priority="critical"),

    dict(role="billing_denials", category="billing", title="File Level-1 appeals on TF-urgent denials",
         description="Any denied claim with TF status of 'urgent' (≤14 days remaining) — generate and send the appeal letter today.",
         due_time=time(15, 0), priority="critical"),

    dict(role="billing_denials", category="billing", title="Follow up on appeals submitted >30 days ago",
         description="Run report of appeals sent >30 days ago without response. Phone payer for status update on each.",
         due_time=time(16, 0), priority="medium"),

    # ─── BILLING — CODING ───────────────────────────────────────────
    dict(role="billing_coding", category="billing", title="Code yesterday's encounters",
         description="Review every encounter from yesterday: codes captured, modifiers applied, dx-procedure linkage correct.",
         due_time=time(11, 0), priority="high"),

    dict(role="billing_coding", category="billing", title="Query providers on incomplete documentation",
         description="Send CDQ (clinical documentation query) for any chart missing critical info (severity, laterality, complications, time-based components).",
         due_time=time(15, 30), priority="medium"),

    # ─── CARIBCALL / VIRTUAL RECEPTIONIST ───────────────────────────
    dict(role="caribcall", category="communication", title="Return overnight voicemails",
         description="Return all voicemails left after-hours. Log each with action taken (booked, rescheduled, info given, etc.).",
         due_time=time(9, 30), priority="high"),

    dict(role="caribcall", category="communication", title="Handle Klara scheduling messages",
         description="All scheduling/rebooking Klara messages handled by EOD.",
         due_time=time(17, 30), priority="high"),

    dict(role="caribcall", category="communication", title="All missed calls returned",
         description="Every missed call from today's call log returned or callback scheduled. None outstanding at EOD.",
         due_time=time(17, 30), priority="high"),
]


def seed(db: Session) -> dict:
    """Idempotent — only adds templates that don't already exist (matched by
    title + role)."""
    inserted = 0
    skipped = 0
    for spec in SEED_TEMPLATES:
        existing = db.query(TaskTemplate).filter(
            TaskTemplate.title == spec["title"],
            TaskTemplate.role == spec["role"],
        ).first()
        if existing:
            skipped += 1
            continue
        db.add(TaskTemplate(frequency="daily", active=True, **spec))
        inserted += 1
    db.commit()
    return {"inserted": inserted, "skipped": skipped, "total_seed": len(SEED_TEMPLATES)}


if __name__ == "__main__":
    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from app.database import SessionLocal, init_db
    from app.models import (patient, claim, payment, denial, appeal, audit, clinical,
                            document, fax_log, guid, import_audit, patient_directory,
                            adjustment_code_reference, payment_analysis, practice_config,
                            user, active_ar, appeal_letters, bai2, checklist)
    init_db()
    db = SessionLocal()
    res = seed(db)
    print(f"Seeded checklist templates: {res['inserted']} new, {res['skipped']} already-present, {res['total_seed']} total")
    db.close()
