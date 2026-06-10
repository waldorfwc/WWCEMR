"""Seed the Pellet dose-type catalog + initial manual sections.

Run from database.init_db() — re-runs on every boot are safe; rows are
only inserted when missing. Edit this list to add a new dose; existing
catalog rows aren't touched (admins edit thresholds in the UI).
"""
from __future__ import annotations

from app.database import SessionLocal
from app.models.pellet import PelletDoseType, PelletManualSection, PelletMammoFacility


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
    seed_pellet_manual()
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


# ─── Manual sections ────────────────────────────────────────────────

SEED_MANUAL_SECTIONS = [
    ("overview", "Overview", 10, """\
The **Pellet** module tracks Bio-Identical Hormone Pellets (Estradiol +
Testosterone) from order through insertion. Pellets are ordered from
**Qualgen**, ship in 4 business days (no backlog), and arrive at the
White Plains office.

**DEA Schedule III**: Testosterone is a controlled substance. Daily counts,
witnessed disposals, and a perpetual inventory audit are mandatory.

**Storage**: White Plains has a **double-locked box** that receives every
shipment. Pellets are then transferred to Brandywine and Arlington as
needed; each transfer is logged.

**FIFO rule**: New pellets go to the **back** of storage so older pellets
are used first. This minimizes expiration loss.
"""),

    ("ordering", "Ordering from Qualgen", 20, """\
Orders are placed manually on the Qualgen website (no API integration).

When the dashboard shows a **Reorder alert** for a dose, that means
on-hand has dropped to or below the configured threshold. Order the
configured quantity unless a backlog is expected.

Current reorder rules (editable on the **Dose type catalog** page):

| Hormone | Dose | Reorder ≤ packs | Order qty packs |
|---|---|---|---|
| Estradiol | 6mg | 5 | 6 |
| Estradiol | 10mg | 10 | 30 |
| Estradiol | 12.5mg | 20 | 30 |
| Estradiol | 15mg | 10 | 6 |
| Testosterone | 25mg | 20 | 30 |
| Testosterone | 37.5mg | 20 | 30 |
| Testosterone | 50mg | 0 (order when empty) | 6 |
| Testosterone | 87.5mg | 20 | 30 |
| Testosterone | 100mg | 20 | 30 |
| Testosterone | 200mg | 0 (order when empty) | 6 |
"""),

    ("receiving", "Receiving + manifest verification", 30, """\
Every Qualgen shipment lands at **White Plains**. Workflow:

1. Open the box. Pull out the shipping manifest.
2. Click **Receive shipment** on the dashboard. Enter the Qualgen
   order # + each lot you find in the box:
   - Lot # (Qualgen-printed)
   - Expiration date
   - Pack size (6/12/30) × pack count → total doses
3. Compare on-screen totals to the manifest line-by-line.
4. Click **Verify manifest** — this is the gatekeeper before inventory
   reflects the new doses.
5. Place new pellets at the **back of storage** (FIFO).

The system writes one audit event per lot received and one for the
manifest-verified action. The receiver and the verifier should be
**two different people** when handling Testosterone (Schedule III).
"""),

    ("transfers", "Transfers to Brandywine + Arlington", 40, """\
Brandywine and Arlington pull from the White Plains stock as needed.

1. Click **Transfer** on the dashboard.
2. Choose the lot + destination + dose count.
3. The sender's stock decrements immediately; the destination's stock
   increments when the receiver clicks **Mark received** at the other
   end.

The destination must mark received same-day. Anything left
`in_transit` for >24h surfaces on the dashboard for follow-up.

> For testosterone, both legs of the transfer (send + receive) should
> include a witness signature — entered in the form.
"""),

    ("daily-count", "Daily count (DEA-grade)", 50, """\
A daily count of every controlled (testosterone) lot is required. Counts
of estradiol are recommended but not legally required.

Workflow:

1. **Start count** at your location (or "All locations" for headquarters).
2. The system snapshots every expected (lot × location) balance into the
   count.
3. Walk the shelf — for each lot, enter the actual dose count you see.
4. Variance is calculated in real-time. Any non-zero variance must have
   a notes explanation before the count can finish.
5. **Finish count** — testosterone counts require a witness signature.

The audit log records the count, every line, and the variance reason.
"""),

    ("disposal", "Disposal — biohazard", 60, """\
Pellets that are dropped, broken, expired, or otherwise unusable get
disposed of in a biohazard sharps container. **We do not contact
Qualgen for returns — the practice absorbs the loss.**

Workflow:

1. From the lot's stock row, click **Dispose**.
2. Choose reason: dropped / broken / expired / other.
3. Enter the dose count.
4. For testosterone, a **witness signature** is required (Schedule III).
5. Drop the pellet into the biohazard container.

Disposal writes a row to the audit log and decrements stock immediately.
"""),

    ("audit", "Audit log", 70, """\
Every state change writes one row to the pellet audit log:

- Receipt (one row per lot, one for manifest verification)
- Transfer (sent + received)
- Disposal (with reason + witness if controlled)
- Stock adjustments
- Counts (start + finish + per-line variance)

The log is **write-only** — DEA requires a permanent perpetual inventory
record. Filter by user, lot, dose, action, or system-only.
"""),
]


def seed_pellet_manual() -> None:
    db = SessionLocal()
    try:
        existing = {s.slug for s in db.query(PelletManualSection).all()}
        added = 0
        for slug, title, order, body in SEED_MANUAL_SECTIONS:
            if slug in existing:
                continue
            db.add(PelletManualSection(
                slug=slug, title=title, sort_order=order,
                body_md=body, updated_by="system:seed",
            ))
            added += 1
        if added:
            db.commit()
    finally:
        db.close()
