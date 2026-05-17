"""Curated picklists for the surgery scheduling UI.

These drive the dropdown options on the SurgeryDetail page. Edit the
lists here to add / remove / rename options; the frontend fetches them
via GET /api/surgery/picklists.

Keep the lists short and practice-specific — these are working sets, not
exhaustive code references. A free-text fallback is always available on
the UI for unusual cases that aren't in the list.
"""

# Single operating surgeon at WWC. Assistant surgeons (e.g. Dr. Gillespie)
# come from outside practices and are tracked separately on the surgery.
SURGEONS = [
    "Aryian Cooke, MD",
]


# Top payers / plans we see at WWC. Includes Maryland Medicaid MCOs that
# also drive the Medicaid sterilization (HHS-687) supplemental consent.
INSURANCE_COMPANIES = [
    # Alphabetical. Maryland Medicaid MCOs are marked "(MCO)" inline because
    # those names trigger the HHS-687 sterilization consent in the matcher.
    "Aetna",
    "Aetna Better Health (MCO)",
    "Anthem",
    "Blue Cross & Blue Shield Federal",
    "Blue Cross & Blue Shield HMO",
    "Blue Cross & Blue Shield PPO",
    "BlueCross Family Plan (MCO)",
    "CareFirst BlueChoice",
    "Cigna",
    "Humana",
    "Johns Hopkins EHP",
    "Johns Hopkins USFHP",
    "Maryland Physicians Care (MCO)",
    "Medicare",
    "MedStar Family Choice (MCO)",
    "Priority Partners (MCO)",
    "Tricare Prime",
    "Tricare Select",
    "United Healthcare Community Plan (MCO)",
    "UnitedHealthcare",
    "UnitedHealthcare Choice Plus",
    "Wellpoint Maryland (MCO)",

    # Always last:
    "Self-Pay",
    "Other",
]


# Top 30 procedures Aryian performs (CPT + description). The frontend
# adds these to surgery.procedures as {"cpt": "...", "description": "..."}.
PROCEDURES = [
    # Hysteroscopy / D&C family
    {"cpt": "58558", "description": "Hysteroscopy with D&C +/- polypectomy"},
    {"cpt": "58563", "description": "Hysteroscopy with endometrial ablation"},
    {"cpt": "58561", "description": "Hysteroscopy with myomectomy"},
    {"cpt": "58555", "description": "Diagnostic hysteroscopy"},
    {"cpt": "58120", "description": "Dilation and curettage (non-OB)"},
    {"cpt": "58100", "description": "Endometrial biopsy"},

    # Robotic / laparoscopic hysterectomy
    {"cpt": "58571", "description": "Total laparoscopic hysterectomy (uterus ≤ 250 g)"},
    {"cpt": "58572", "description": "Total laparoscopic hysterectomy (uterus > 250 g)"},
    {"cpt": "58573", "description": "TLH with bilateral salpingo-oophorectomy (≤ 250 g)"},
    {"cpt": "58574", "description": "TLH with BSO (> 250 g)"},
    {"cpt": "58541", "description": "Laparoscopic supracervical hysterectomy (≤ 250 g)"},
    {"cpt": "58542", "description": "Laparoscopic supracervical hysterectomy with BSO (≤ 250 g)"},

    # Myomectomy
    {"cpt": "58545", "description": "Robotic-assisted laparoscopic myomectomy"},
    {"cpt": "58546", "description": "Laparoscopic myomectomy (5 or more myomas)"},

    # Other laparoscopic
    {"cpt": "49320", "description": "Diagnostic laparoscopy"},
    {"cpt": "58662", "description": "Laparoscopic ablation/excision of endometriosis"},
    {"cpt": "58661", "description": "Laparoscopic bilateral salpingectomy / oophorectomy"},
    {"cpt": "58670", "description": "Laparoscopic tubal ligation (with fulguration)"},
    {"cpt": "58671", "description": "Laparoscopic tubal occlusion (Falope rings/clips)"},

    # Cervical / LEEP / vulvar
    {"cpt": "57522", "description": "Cervical LEEP (loop excision)"},
    {"cpt": "57461", "description": "Cone biopsy with LEEP"},
    {"cpt": "57500", "description": "Cervical biopsy (single or multiple)"},
    {"cpt": "56605", "description": "Vulvar biopsy"},
    {"cpt": "56440", "description": "Bartholin cyst marsupialization"},

    # IUD / contraception
    {"cpt": "58300", "description": "IUD insertion"},
    {"cpt": "58301", "description": "IUD removal"},

    # Prolapse / pessary
    {"cpt": "57160", "description": "Pessary fitting / insertion"},

    # Tubal sterilization (Essure removal etc.)
    {"cpt": "58662", "description": "Essure removal (laparoscopic)"},

    # In-office
    {"cpt": "57452", "description": "Colposcopy (no biopsy)"},
    {"cpt": "57454", "description": "Colposcopy with biopsy"},
]


# Top 30 diagnoses. ICD-10 codes + descriptions. Added to surgery.diagnoses
# as {"icd": "...", "description": "..."}.
DIAGNOSES = [
    # Leiomyoma / fibroids
    {"icd": "D25.9", "description": "Leiomyoma of uterus, unspecified"},
    {"icd": "D25.0", "description": "Submucous leiomyoma of uterus"},
    {"icd": "D25.1", "description": "Intramural leiomyoma of uterus"},
    {"icd": "D25.2", "description": "Subserosal leiomyoma of uterus"},

    # AUB / heavy menses
    {"icd": "N92.0", "description": "Heavy and frequent menstruation with regular cycle"},
    {"icd": "N92.1", "description": "Heavy and frequent menstruation with irregular cycle"},
    {"icd": "N93.9", "description": "Abnormal uterine and vaginal bleeding, unspecified"},
    {"icd": "N95.0", "description": "Postmenopausal bleeding"},

    # Endometriosis / adenomyosis
    {"icd": "N80.9", "description": "Endometriosis, unspecified"},
    {"icd": "N80.0", "description": "Adenomyosis"},
    {"icd": "N80.1", "description": "Endometriosis of ovary"},
    {"icd": "N80.3", "description": "Endometriosis of pelvic peritoneum"},

    # Endometrial / cervical pathology
    {"icd": "N85.00", "description": "Endometrial hyperplasia, unspecified"},
    {"icd": "N84.0",  "description": "Polyp of corpus uteri"},
    {"icd": "N84.1",  "description": "Polyp of cervix uteri"},
    {"icd": "N87.1",  "description": "Moderate cervical dysplasia (CIN II)"},
    {"icd": "N87.2",  "description": "Severe cervical dysplasia (CIN III)"},
    {"icd": "R87.620","description": "Low-grade squamous intraepithelial lesion (LGSIL)"},
    {"icd": "R87.621","description": "High-grade squamous intraepithelial lesion (HGSIL)"},

    # Ovarian
    {"icd": "N83.20", "description": "Ovarian cyst, unspecified"},
    {"icd": "N83.0",  "description": "Follicular cyst of ovary"},
    {"icd": "E28.2",  "description": "Polycystic ovarian syndrome"},

    # Pelvic pain / dyspareunia
    {"icd": "R10.2",  "description": "Pelvic and perineal pain"},
    {"icd": "N94.10", "description": "Dyspareunia, unspecified"},
    {"icd": "N94.6",  "description": "Dysmenorrhea, unspecified"},

    # Prolapse / vulvar
    {"icd": "N81.4",  "description": "Uterovaginal prolapse"},
    {"icd": "N81.1",  "description": "Cystocele"},
    {"icd": "N75.0",  "description": "Cyst of Bartholin's gland"},

    # Sterilization / contraception
    {"icd": "Z30.2",  "description": "Encounter for sterilization"},
    {"icd": "Z30.430","description": "Encounter for insertion of IUD"},
]


def all_picklists() -> dict:
    return {
        "surgeons": SURGEONS,
        "insurance_companies": INSURANCE_COMPANIES,
        "procedures": PROCEDURES,
        "diagnoses": DIAGNOSES,
    }
