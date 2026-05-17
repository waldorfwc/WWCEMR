"""Full-history seed for the Pellet patient module.

Combines 4 Greenway / ModMed exports into our model:
  - BHRT-flagged patients (demographics)
  - Prescribed-pellets list
  - AR/charged history (= "established" patient bucket)
  - Appointment history (Insert Only + Booster + Active future appts)

For each unique patient, enrolls one PelletPatient row. For each Insert
Only / Booster appointment, creates one PelletVisit with:
  - inserted_at        = appointment date (Complete status)
  - status='billed'    if appt status='Complete'
  - status='in_progress' if appt status='Active'  (future-scheduled)
  - visit_kind:
      first Insert Only per patient → 'initial'
      subsequent Insert Only         → 'repeat'
      Booster                        → 'booster'
  - location + provider              joined from AR data on (chart, date)
  - milestones spawned + all auto-completed for Complete visits
  - no dose-card detail (we don't have it)

In Office Visit appts (37 rows) are skipped — they're consults, not
insertions, and would just be noise.

Run with no flag = dry-run. Pass --apply to commit.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from datetime import date as _date, datetime
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd

from app.database import SessionLocal, init_db
from app.models.pellet import (
    PelletAuditEvent, PelletPatient, PelletVisit, PelletVisitMilestone,
)
from app.services.pellet_workflow import (
    milestone_catalog, default_price_for, spawn_milestones,
)


BHRT_PATH = "/Users/wwcclaudecode/Downloads/SottoPelle Patients.xls"
RX_PATH   = "/Users/wwcclaudecode/Downloads/Pellet Patient Prescribe Pellets.xls"
AR_PATH   = "/Users/wwcclaudecode/Downloads/SottoPellet AR.xls"
APPT_PATH = "/Users/wwcclaudecode/Downloads/SottoPellet Appt Completed Active.xls"


def _to_str(v) -> Optional[str]:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip()
    return s or None


def _to_date(v):
    if v is None or pd.isna(v):
        return None
    try:
        return pd.to_datetime(v).date()
    except Exception:
        return None


def _name_from(first, last, fallback=None):
    f = _to_str(first); l = _to_str(last)
    if l and f:
        return f"{l.title()}, {f.title()}"
    if fallback:
        n = _to_str(fallback)
        if n:
            return n
    return l or f or "(no name)"


def _classify_appt_type(t: str) -> Optional[str]:
    """Map appointment type string to our visit_kind ('initial', 'booster',
    'repeat'), or None to skip."""
    if not t:
        return None
    s = t.lower()
    if "booster" in s:
        return "booster"
    if "in office visit" in s:
        return None     # consult, skip
    if "insert only" in s:
        return "insert_only"   # decide initial vs repeat after sorting
    return None


# Default recall cadence — patient detail page edits this per patient
DEFAULT_RECALL_MONTHS = 4


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    init_db()
    db = SessionLocal()
    try:
        print("Loading reports…")
        bhrt = pd.read_excel(BHRT_PATH)
        rx   = pd.read_excel(RX_PATH)
        ar   = pd.read_excel(AR_PATH)
        appt = pd.read_excel(APPT_PATH)
        print(f"  BHRT: {len(bhrt)}  Rx: {len(rx)}  AR: {len(ar)}  Appt: {len(appt)}")

        # ── Build per-patient demographic lookup ──
        demo = {}   # chart_number → {name, dob, email, phone}

        for _, r in bhrt.iterrows():
            chart = _to_str(r.get("Patient: Patient ID"))
            if not chart: continue
            demo.setdefault(chart, {})
            d = demo[chart]
            d["name"] = _to_str(r.get("Patient: Patient Name")) or d.get("name")
            d["dob"]  = _to_date(r.get("Patient: Date Of Birth")) or d.get("dob")
            d["email"]= _to_str(r.get("Patient: EMail"))  or d.get("email")
            d["phone"]= _to_str(r.get("Patient: Phone Primary")) or d.get("phone")

        for _, r in rx.iterrows():
            chart = _to_str(r.get("Patient: Patient ID"))
            if not chart: continue
            demo.setdefault(chart, {})
            d = demo[chart]
            if not d.get("name"):
                d["name"] = _name_from(r.get("Patient: First Name"), r.get("Patient: Last Name"))
            d["dob"]   = d.get("dob")   or _to_date(r.get("Patient: Date Of Birth"))
            d["email"] = d.get("email") or _to_str(r.get("Patient: EMail"))
            d["phone"] = d.get("phone") or _to_str(r.get("Patient: Phone Primary"))

        for _, r in ar.iterrows():
            chart = _to_str(r.get("Patient ID"))
            if not chart: continue
            demo.setdefault(chart, {})
            d = demo[chart]
            if not d.get("name"):
                d["name"] = _name_from(r.get("Patient First Name"), r.get("Patient Last Name"))
            d["dob"] = d.get("dob") or _to_date(r.get("Patient Date of Birth"))

        for _, r in appt.iterrows():
            chart = _to_str(r.get("Patient: Patient ID"))
            if not chart: continue
            demo.setdefault(chart, {})
            d = demo[chart]
            if not d.get("name"):
                d["name"] = _name_from(r.get("Patient: First Name"), r.get("Patient: Last Name"))
            d["dob"]   = d.get("dob")   or _to_date(r.get("Patient: Date Of Birth"))
            d["email"] = d.get("email") or _to_str(r.get("Patient: E-Mail"))
            d["phone"] = d.get("phone") or _to_str(r.get("Patient: Phone Primary"))

        # ── Build AR lookup: (chart, dos) → (provider, location) ──
        ar_lookup = {}
        for _, r in ar.iterrows():
            chart = _to_str(r.get("Patient ID"))
            dos = _to_date(r.get("Date: Service date of the charge"))
            if not chart or not dos: continue
            key = (chart, dos)
            prov = _to_str(r.get("Rendering Care Provider Name"))
            loc  = _to_str(r.get("Service Location Name"))
            ar_lookup.setdefault(key, {})
            if prov: ar_lookup[key]["provider"] = prov
            if loc:  ar_lookup[key]["location"] = loc

        # Normalize location to our enum
        def map_location(loc_str: Optional[str]) -> Optional[str]:
            if not loc_str: return None
            s = loc_str.lower()
            if "white plains" in s: return "white_plains"
            if "brandywine"   in s: return "brandywine"
            if "arlington"    in s: return "arlington"
            return None

        # Charged patients (= established cohort)
        ar_chart_set = {_to_str(c) for c in ar["Patient ID"].dropna()}
        ar_chart_set = {c for c in ar_chart_set if c}

        # ── Sort appointments per patient for kind assignment ──
        appt_rows = []
        skipped_consult = 0; skipped_type = 0
        for _, r in appt.iterrows():
            chart = _to_str(r.get("Patient: Patient ID"))
            dt = _to_date(r.get("Appointment: Date"))
            atype = _to_str(r.get("Appointment: Type"))
            astatus = _to_str(r.get("Appointment: Status"))
            if not chart or not dt or not atype:
                skipped_type += 1; continue
            kind_hint = _classify_appt_type(atype)
            if kind_hint is None:
                skipped_consult += 1; continue
            appt_rows.append({
                "chart": chart, "date": dt, "type": atype,
                "status": astatus, "kind_hint": kind_hint,
            })

        # Per-patient sort by date so we can mark first insertion = 'initial'
        appt_rows.sort(key=lambda x: (x["chart"], x["date"]))
        first_insert_seen = set()    # chart numbers that have had their first insert assigned
        visit_rows = []
        for a in appt_rows:
            chart = a["chart"]
            kind = a["kind_hint"]
            if kind == "insert_only":
                if chart not in first_insert_seen:
                    kind = "initial"
                    first_insert_seen.add(chart)
                else:
                    kind = "repeat"
            a["visit_kind"] = kind
            visit_rows.append(a)

        # ── Upsert patients ──
        n_patients_added = 0
        n_patients_existing = 0
        chart_to_patient = {}

        all_charts = set(demo.keys())
        # Also union with any chart that appears anywhere
        for r in visit_rows: all_charts.add(r["chart"])
        all_charts.update(ar_chart_set)

        for chart in sorted(all_charts):
            existing = (db.query(PelletPatient)
                          .filter(PelletPatient.chart_number == chart).first())
            if existing:
                chart_to_patient[chart] = existing
                n_patients_existing += 1
                continue

            d = demo.get(chart, {})
            # patient_type: established if charged at least once OR has insertion appts
            had_insertion = any(r["chart"] == chart for r in visit_rows)
            ptype = "established" if (chart in ar_chart_set or had_insertion) else "new"

            # Activity status: if last appointment is "Active" (future) → active;
            # if had insertions before but nothing recent → still active (just on recall);
            # appt-but-no-charge could be a declined consult → inactive
            had_charge = chart in ar_chart_set
            appt_charts = {r["chart"] for r in visit_rows}
            if not had_charge and chart in appt_charts:
                status = "inactive"   # consult-only or future appt
            else:
                status = "active"

            p = PelletPatient(
                chart_number=chart,
                patient_name=d.get("name") or "(no name)",
                patient_dob=d.get("dob"),
                patient_email=d.get("email"),
                patient_phone=d.get("phone"),
                patient_type=ptype,
                status=status,
                recall_interval_months=DEFAULT_RECALL_MONTHS,
                created_by="system:greenway-seed",
                notes=f"Seeded {datetime.utcnow().date().isoformat()} from "
                      f"Greenway/ModMed historical reports. "
                      f"BHRT: {chart in {_to_str(x) for x in bhrt['Patient: Patient ID']}}. "
                      f"Charged: {had_charge}.",
            )
            db.add(p); db.flush()
            chart_to_patient[chart] = p
            n_patients_added += 1

        # ── Create one visit per appointment row ──
        n_visits_added = 0
        n_visits_active = 0
        for a in visit_rows:
            chart = a["chart"]
            p = chart_to_patient.get(chart)
            if not p: continue

            # Look up provider/location from AR (joined by chart + dos)
            ar_meta = ar_lookup.get((chart, a["date"]), {})
            loc = map_location(ar_meta.get("location"))
            prov = ar_meta.get("provider")

            is_complete = (a["status"] or "").lower() == "complete"
            v = PelletVisit(
                patient_id=p.id,
                visit_kind=a["visit_kind"],
                status="billed" if is_complete else "in_progress",
                scheduled_date=a["date"],
                location=loc,
                provider=prov,
                price_amount=default_price_for(
                    "new" if a["visit_kind"] == "initial" else "established"
                ),
                payment_status="collected" if is_complete else "not_sent",
                payment_collected_at=(datetime.combine(a["date"], datetime.min.time())
                                       if is_complete else None),
                inserted_at=(datetime.combine(a["date"], datetime.min.time())
                              if is_complete else None),
                outcome="perfect" if is_complete else None,
                billed_at=(datetime.combine(a["date"], datetime.min.time())
                            if is_complete else None),
                claim_number=f"HIST-{a['date'].isoformat()}" if is_complete else None,
                created_by="system:greenway-seed",
                notes=f"Historical {a['type']} from ModMed appt history. "
                      f"No dose-card detail available.",
            )
            db.add(v); db.flush()
            spawn_milestones(db, v, p.patient_type)

            if is_complete:
                # Mark every milestone as done with completed_at = appt date
                stamp = datetime.combine(a["date"], datetime.min.time())
                for m in v.milestones:
                    m.status = "done"
                    m.completed_at = stamp
                    m.completed_by = "system:greenway-seed"
                n_visits_added += 1
            else:
                n_visits_active += 1

            db.add(PelletAuditEvent(
                actor="system:greenway-seed",
                action="visit_historical",
                location=loc,
                summary=f"Historical {a['visit_kind']} visit on {a['date']} for {p.patient_name}",
                detail={"appt_type": a["type"], "appt_status": a["status"],
                        "provider": prov},
            ))

        print()
        print("──── Seed summary ────")
        print(f"  Patients enrolled (new):     {n_patients_added}")
        print(f"  Patients already in DB:      {n_patients_existing}")
        print(f"  Historical visits (billed):  {n_visits_added}")
        print(f"  Active future visits:        {n_visits_active}")
        print(f"  Appt rows skipped (consult): {skipped_consult}")
        print(f"  Appt rows skipped (no date): {skipped_type}")

        if not args.apply:
            db.rollback()
            print("\nDRY RUN — rolled back. Re-run with --apply to commit.")
        else:
            db.commit()
            print("\n✓ Committed.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
