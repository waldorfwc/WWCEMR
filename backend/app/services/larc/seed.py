"""Idempotently seed the LARC device-type catalog on boot.

Run from database.init_db() — re-runs on every boot are safe; rows are
only inserted when missing. Edit this list to add a new device brand;
existing devices won't be touched.
"""
from __future__ import annotations

from app.database import SessionLocal
from app.models.larc import LarcDeviceType


SEED_DEVICE_TYPES = [
    # (name, manufacturer, category, default_flow, typical_cost,
    #  reorder_threshold, reorder_quantity, notes)
    ("Liletta",   "Medicines360",    "larc",             "in_stock",
     750.00, 3, 3,
     "Practice keeps in stock at all three locations."),
    ("Mirena",    "Bayer",           "larc",             "pharmacy_order",
     900.00, None, None,
     "Bayer device — shares enrollment form with Skyla/Kyleena (different option boxes)."),
    ("Skyla",     "Bayer",           "larc",             "pharmacy_order",
     850.00, None, None,
     "Bayer device — shares enrollment form with Mirena/Kyleena."),
    ("Kyleena",   "Bayer",           "larc",             "pharmacy_order",
     900.00, None, None,
     "Bayer device — shares enrollment form with Mirena/Skyla."),
    ("Paragard",  "CooperSurgical",  "larc",             "pharmacy_order",
     900.00, None, None,
     "Copper IUD — separate enrollment form from Bayer devices."),
    ("Nexplanon", "Organon (Merck)", "larc",             "pharmacy_order",
     1100.00, None, None,
     "Subdermal implant — separate enrollment form."),
    # Office-procedure (single-use, consumed during a surgery)
    ("NovaSure",  "Hologic",         "office_procedure", "in_stock",
     1300.00, 2, 3,
     "Endometrial ablation handpiece — single-use. Assigned at surgery scheduling."),
    ("Benesta",   "Caldera Medical", "office_procedure", "in_stock",
     800.00, 2, 3,
     "Tissue resector for polyp removal during D&C — single-use."),
]


# BoldSign template IDs for the three known pharmacy-enrollment forms.
# Mirena/Skyla/Kyleena share one (Bayer); Paragard and Nexplanon each
# have their own. Only Nexplanon is wired into the send flow today
# (Phase 2); the other two light up in Phase 5 once their fields are
# labeled and per-template prefill rules are added.
LARC_ENROLLMENT_TEMPLATES = {
    "Nexplanon": "9af154d6-0bc7-43f6-bf94-175b7daf27e6",
    "Paragard":  "9a8f78cc-5de0-4b61-a05b-fa2cadb98ae7",
    "Mirena":    "2918da35-1fed-4e9b-ad9c-4103c5db8e85",
    "Skyla":     "2918da35-1fed-4e9b-ad9c-4103c5db8e85",
    "Kyleena":   "2918da35-1fed-4e9b-ad9c-4103c5db8e85",
}


def seed_larc_device_types():
    db = SessionLocal()
    try:
        existing = {t.name: t for t in db.query(LarcDeviceType).all()}
        added = 0
        for name, mfr, category, flow, cost, threshold, qty, notes in SEED_DEVICE_TYPES:
            if name in existing:
                # Backfill category + reorder_quantity on existing rows that
                # were seeded before those columns existed.
                t = existing[name]
                dirty = False
                if not t.category or t.category != category:
                    t.category = category; dirty = True
                if qty is not None and not t.reorder_quantity:
                    t.reorder_quantity = qty; dirty = True
                if dirty:
                    added += 1   # count as touched
                continue
            db.add(LarcDeviceType(
                name=name, manufacturer=mfr, category=category, default_flow=flow,
                typical_cost=cost, reorder_threshold=threshold,
                reorder_quantity=qty, notes=notes,
            ))
            added += 1

        # Backfill BoldSign enrollment template IDs on the three pharmacy-
        # order families. Idempotent — only fills when blank or stale.
        for t in db.query(LarcDeviceType).filter(
            LarcDeviceType.name.in_(LARC_ENROLLMENT_TEMPLATES.keys())
        ).all():
            want = LARC_ENROLLMENT_TEMPLATES.get(t.name)
            if want and t.enrollment_form_template != want:
                t.enrollment_form_template = want
                added += 1

        if added:
            db.commit()
    finally:
        db.close()
