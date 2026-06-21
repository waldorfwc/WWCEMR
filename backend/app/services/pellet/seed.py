"""Seed the Pellet dose-type catalog + initial manual sections.

Run from database.init_db() — re-runs on every boot are safe; rows are
only inserted when missing. Edit this list to add a new dose; existing
catalog rows aren't touched (admins edit thresholds in the UI).
"""
from __future__ import annotations

from app.database import SessionLocal
from app.models.pellet import PelletDoseType, PelletMammoFacility


# (hormone, dose_mg, reorder_threshold_packs, reorder_qty_packs,
#  pack_sizes_list, is_controlled, notes)
SEED_DOSE_TYPES = [
    # Estradiol — not a controlled substance
    ("estradiol",    6.0,    5,   6,  [6, 12, 30], False, ""),
    ("estradiol",   10.0,   10,  30,  [6, 12, 30], False, ""),
    ("estradiol",   12.5,   20,  30,  [6, 12, 30], False, ""),
    ("estradiol",   15.0,   10,   6,  [6, 12, 30], False, ""),
    # Testosterone — DEA Schedule III
    ("testosterone", 25.0,   20,  30,  [6, 12, 30], True,  "DEA Schedule III"),
    ("testosterone", 37.5,   20,  30,  [6, 12, 30], True,  "DEA Schedule III"),
    ("testosterone", 50.0,    0,   6,  [6, 12, 30], True,  "DEA Schedule III"),
    ("testosterone", 87.5,   20,  30,  [6, 12, 30], True,  "DEA Schedule III"),
    ("testosterone", 100.0,  20,  30,  [6, 12, 30], True,  "DEA Schedule III"),
    ("testosterone", 200.0,   0,   6,  [6, 12, 30], True,  "DEA Schedule III"),
]


def _label(hormone: str, dose_mg: float) -> str:
    h = "Estradiol" if hormone == "estradiol" else "Testosterone"
    # Drop trailing .0 on whole numbers (e.g., 100.0 → "100")
    dose_str = f"{dose_mg:g}"
    return f"{h} {dose_str}mg"


def seed_pellet_dose_types() -> None:
    db = SessionLocal()
    try:
        existing = {(t.hormone, float(t.dose_mg)): t
                      for t in db.query(PelletDoseType).all()}
        added = 0
        for hormone, dose, thresh, qty, packs, controlled, notes in SEED_DOSE_TYPES:
            if (hormone, dose) in existing:
                continue
            db.add(PelletDoseType(
                hormone=hormone,
                dose_mg=dose,
                label=_label(hormone, dose),
                is_controlled=controlled,
                reorder_threshold_packs=thresh,
                reorder_qty_packs=qty,
                pack_sizes=packs,
                notes=notes or None,
            ))
            added += 1
        if added:
            db.commit()
    finally:
        db.close()
    seed_mammo_facilities()


# ─── Mammogram facility catalog (within ~15 miles of Waldorf MD) ──

SEED_MAMMO_FACILITIES = [
    # (name, phone, fax, address, sort_order)
    ("American Radiology Services — Waldorf",
     "301-638-2457", "301-638-9542",
     "3510 Old Washington Road, Suite 101\nWaldorf, MD 20602",
     10),
    ("Radiology Imaging Associates — O'Donnell Place (Waldorf)",
     "410-298-0454", "301-694-2606",
     "10400 O'Donnell Place, Suite 100\nWaldorf, MD 20603",
     20),
    ("Radiology Imaging Associates — Pembrooke Square (Waldorf)",
     "301-870-8434", None,
     "11335 Pembrooke Square Medical Center, Suites 101 / 104 / 114 / 116\nWaldorf, MD 20603",
     30),
    ("MedStar Radiology Network — Brandywine",
     "240-546-3010", "240-681-2446",
     "13950 Brandywine Road, Suite G25\nBrandywine, MD 20613",
     40),
    ("MedStar Radiology Network — Southern Maryland (Clinton)",
     "301-877-5588", "301-868-2298",
     "7501 Surratts Road, Suite 105\nClinton, MD 20735",
     50),
    ("UM Charles Regional Imaging — Dotson Imaging Center (La Plata)",
     "301-539-0345", None,
     "5 N. La Plata Court, Suite 104\nLa Plata, MD 20646",
     60),
]


def seed_mammo_facilities() -> None:
    db = SessionLocal()
    try:
        existing = {f.name for f in db.query(PelletMammoFacility).all()}
        added = 0
        for name, phone, fax, address, sort_order in SEED_MAMMO_FACILITIES:
            if name in existing:
                continue
            db.add(PelletMammoFacility(
                name=name, phone=phone, fax=fax, address=address,
                sort_order=sort_order, is_active=True,
            ))
            added += 1
        if added:
            db.commit()
    finally:
        db.close()


