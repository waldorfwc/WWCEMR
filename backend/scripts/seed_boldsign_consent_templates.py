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
# Side-effect imports so SQLAlchemy can resolve Surgery's cross-module
# relationships (payments, emails, sms, etc.) at mapper-config time.
from app.models import stripe_payment  # noqa: F401
from app.models import patient_email  # noqa: F401
from app.models import patient_sms  # noqa: F401


# (name, boldsign_id, procedure_match, facility_match, is_supplemental)
TEMPLATES = [
    # ── Hospital procedures (MedStar + CRMC) ──────────────────────
    ("Hospital — Robotic-Assisted TLH Consent",
     "1e12f2cc-73e3-418d-a8b5-88cc25aa7c07",
     ["robotic-assisted hysterectomy", "total laparoscopic hysterectomy", "tlh"],
     ["medstar", "crmc"], False),

    ("Hospital — Total Abdominal Hysterectomy Consent",
     "9733016a-f008-4d0f-aa14-a7f35d82e731",
     ["total abdominal hysterectomy", "abdominal hysterectomy"],
     ["medstar", "crmc"], False),

    ("Hospital — Robotic-Assisted Laparoscopic Myomectomy",
     "5df3519d-bf6f-4d5c-8976-836830a8eb29",
     ["robotic-assisted laparoscopic myomectomy", "robotic myomectomy"],
     ["medstar", "crmc"], False),

    ("Hospital — Abdominal Myomectomy Consent",
     "53dbd2d8-dfed-4d0f-b328-70087e2af564",
     ["abdominal myomectomy", "open myomectomy"],
     ["medstar", "crmc"], False),

    ("Hospital — Hysteroscopic Removal of Fibroids (MyoSure) Consent",
     "4dd415e1-8182-4a2b-9b74-baa859f18fa5",
     ["myosure", "hysteroscopic myomectomy", "hysteroscopy with myomectomy"],
     ["medstar", "crmc"], False),

    ("Hospital — Hysteroscopic Endometrial Ablation Consent",
     "deab1f55-1167-41a5-9f94-03b95b3bf613",
     ["hysteroscopy with endometrial ablation"],
     ["medstar", "crmc"], False),

    ("Hospital — Hysteroscopy D&C Consent",
     "bf5aa2a7-a466-49f4-b595-ad90e71caada",
     ["hysteroscopy with d&c", "d&c", "dilation and curettage"],
     ["medstar", "crmc"], False),

    ("Hospital — Diagnostic Laparoscopy Consent",
     "79b91d75-52cc-4c43-ad4d-dec0a586ea38",
     ["diagnostic laparoscopy"],
     ["medstar", "crmc"], False),

    ("Hospital — Laparoscopic Ovarian/Fallopian Tube/Pelvic Surgery, Cystectomy",
     "3f8f1dca-fd6e-4b39-977c-e7aa891d5eb1",
     ["ovarian cyst", "cystectomy", "endometriosis",
      "ablation/excision of endometriosis", "oophorectomy"],
     ["medstar", "crmc"], False),

    ("Hospital — Laparoscopic Removal of Bilateral Tubes for Sterilization",
     "3f76eedd-f5d5-4724-bc60-d2c8deac58cf",
     ["bilateral salpingectomy", "tubal sterilization",
      "tubal ligation", "tubal occlusion", "salpingectomy"],
     ["medstar", "crmc"], False),

    ("Hospital — Cold Knife Cone Consent",
     "678fef4e-7eaf-4b8d-afbe-326096869548",
     ["cold knife cone", "cone biopsy"],
     ["medstar", "crmc"], False),

    ("Hospital — LEEP Consent",
     "38156839-964a-4632-be23-d23d7a847ffe",
     ["cervical leep", "leep"],
     ["medstar", "crmc"], False),

    ("Hospital — IUD Removal and Insertion Consent",
     "9450cf2b-398d-45a2-97bd-84f169635ee2",
     ["iud insertion", "iud removal"],
     ["medstar", "crmc"], False),

    # ── Office procedures (White Plains) ──────────────────────────
    ("Office — Hysteroscopy D&C Consent",
     "3bcfa608-eb8c-4c38-8fc4-1dd1c0efb556",
     ["hysteroscopy with d&c", "d&c", "dilation and curettage"],
     ["office"], False),

    ("Office — LEEP Consent",
     "0648f5c9-f158-45b4-8553-59f6ee6b3181",
     ["cervical leep", "leep"],
     ["office"], False),

    ("Office — Endometrial Ablation (NovaSure) Consent",
     "0ae2f511-7d48-472c-9c64-3c8fe8640383",
     ["novasure"],
     ["office"], False),

    ("Office — Hysteroscopic IUD Removal Consent",
     "2f5105e4-2bd8-476b-bdb3-543940ff84b3",
     ["iud removal", "hysteroscopic iud removal"],
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
