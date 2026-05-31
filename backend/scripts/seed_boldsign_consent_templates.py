"""Seed ConsentTemplate rows from the BoldSign template inventory.

Fill in `BOLDSIGN_ID` on each row below with the actual template ID
copied from the BoldSign dashboard, then run once:

  DATABASE_URL='postgresql+psycopg2://...' \
      ./venv/bin/python scripts/seed_boldsign_consent_templates.py

Idempotent: matches on `name`. If a row with the same name exists, the
script updates its `boldsign_template_id`, `procedure_match`,
`facility_match`, and `is_supplemental` to whatever's in TEMPLATES below
(so re-running after editing this file is the right way to update).

Existing DocuSign template IDs on those rows are NOT touched — both
providers coexist.

Facility codes:
  "office"  — White Plains office
  "medstar" — MedStar Southern Maryland Hospital
  "crmc"    — Charles Regional Medical Center
  Hospital templates use ["medstar", "crmc"].

Fill in the BoldSign template IDs marked TODO before running.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.surgery import ConsentTemplate


# (name, boldsign_id, procedure_match, facility_match, is_supplemental)
TEMPLATES = [
    # ── Hospital procedures (MedStar + CRMC) ──────────────────────
    ("Hospital — Robotic-Assisted TLH Consent",
     "TODO",
     ["robotic-assisted hysterectomy", "total laparoscopic hysterectomy", "tlh"],
     ["medstar", "crmc"], False),

    ("Hospital — Total Abdominal Hysterectomy Consent",
     "TODO",
     ["total abdominal hysterectomy", "abdominal hysterectomy"],
     ["medstar", "crmc"], False),

    ("Hospital — Robotic-Assisted Laparoscopic Myomectomy",
     "TODO",
     ["robotic-assisted laparoscopic myomectomy", "robotic myomectomy"],
     ["medstar", "crmc"], False),

    ("Hospital — Abdominal Myomectomy Consent",
     "TODO",
     ["abdominal myomectomy", "open myomectomy"],
     ["medstar", "crmc"], False),

    ("Hospital — Hysteroscopic Removal of Fibroids (MyoSure) Consent",
     "TODO",
     ["myosure", "hysteroscopic myomectomy", "hysteroscopy with myomectomy"],
     ["medstar", "crmc"], False),

    ("Hospital — Hysteroscopic Endometrial Ablation Consent",
     "TODO",
     ["hysteroscopy with endometrial ablation"],
     ["medstar", "crmc"], False),

    ("Hospital — Hysteroscopy D&C Consent",
     "TODO",
     ["hysteroscopy with d&c", "d&c", "dilation and curettage"],
     ["medstar", "crmc"], False),

    ("Hospital — Diagnostic Laparoscopy Consent",
     "TODO",
     ["diagnostic laparoscopy"],
     ["medstar", "crmc"], False),

    ("Hospital — Laparoscopic Ovarian/Fallopian Tube/Pelvic Surgery, Cystectomy",
     "TODO",
     ["ovarian cyst", "cystectomy", "endometriosis",
      "ablation/excision of endometriosis", "oophorectomy"],
     ["medstar", "crmc"], False),

    ("Hospital — Laparoscopic Removal of Bilateral Tubes for Sterilization",
     "TODO",
     ["bilateral salpingectomy", "tubal sterilization",
      "tubal ligation", "tubal occlusion", "salpingectomy"],
     ["medstar", "crmc"], False),

    ("Hospital — Cold Knife Cone Consent",
     "TODO",
     ["cold knife cone", "cone biopsy"],
     ["medstar", "crmc"], False),

    ("Hospital — LEEP Consent",
     "TODO",
     ["cervical leep", "leep"],
     ["medstar", "crmc"], False),

    ("Hospital — IUD Removal and Insertion Consent",
     "TODO",
     ["iud insertion", "iud removal"],
     ["medstar", "crmc"], False),

    # ── Office procedures (White Plains) ──────────────────────────
    ("Office — Hysteroscopy D&C Consent",
     "TODO",
     ["hysteroscopy with d&c", "d&c", "dilation and curettage"],
     ["office"], False),

    ("Office — LEEP Consent",
     "TODO",
     ["cervical leep", "leep"],
     ["office"], False),

    ("Office — Endometrial Ablation (NovaSure) Consent",
     "TODO",
     ["novasure"],
     ["office"], False),

    # NOTE: LARC enrollment forms (Mirena/Skyla/Kyleena, Nexplanon, Paragard)
    # and BHRT — Letter of Medical Necessity are NOT in this seed. They belong
    # to the LARC and Pellet modules, which have their own envelope-send
    # entry points (or will).
]


def main():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL not set", file=sys.stderr)
        sys.exit(2)

    engine = create_engine(db_url)
    Session = sessionmaker(bind=engine)
    sess = Session()

    skipped_todo = []
    created = 0
    updated = 0

    for name, bs_id, proc_match, fac_match, is_supp in TEMPLATES:
        if bs_id == "TODO":
            skipped_todo.append(name)
            continue

        row = (sess.query(ConsentTemplate)
                  .filter(ConsentTemplate.name == name).first())
        if row is None:
            sess.add(ConsentTemplate(
                name=name,
                boldsign_template_id=bs_id,
                procedure_match=proc_match,
                facility_match=fac_match,
                is_supplemental=is_supp,
                is_active=True,
            ))
            created += 1
        else:
            row.boldsign_template_id = bs_id
            row.procedure_match = proc_match
            row.facility_match = fac_match
            row.is_supplemental = is_supp
            row.is_active = True
            updated += 1

    sess.commit()

    print(f"Created: {created}")
    print(f"Updated: {updated}")
    print(f"Skipped (TODO id):   {len(skipped_todo)}")
    if skipped_todo:
        print()
        print("Templates still missing a BoldSign id:")
        for n in skipped_todo:
            print(f"  - {n}")


if __name__ == "__main__":
    main()
