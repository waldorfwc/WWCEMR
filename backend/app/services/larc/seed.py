"""Idempotently seed the LARC device-type catalog on boot.

Run from database.init_db() — re-runs on every boot are safe; rows are
only inserted when missing. Edit this list to add a new device brand;
existing devices won't be touched.
"""
from __future__ import annotations

from app.database import SessionLocal
from app.models.larc import LarcDeviceType, LarcManualSection


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
    seed_larc_manual()


# ─── Initial LARC manual content ────────────────────────────────────

SEED_MANUAL_SECTIONS = [
    ("overview", "Overview", 10, """\
The LARC (Long-Acting Reversible Contraceptive) module tracks every device
from order request through billing. Devices range from $750–$1,100, so every
unit is accounted for to prevent costly loss.

**Two flows:**

- **In-stock** — practice keeps Liletta on hand. Patient gets one off the shelf.
- **Pharmacy-order** — Mirena, Skyla, Kyleena, Paragard, Nexplanon. Ordered through
  the patient's prescription benefit, ships to the practice.

Device request and benefits checks arrive as ModMed Tasks (no integration).
"""),

    ("in-stock-flow", "In-stock workflow (Liletta)", 20, """\
1. **Benefits check** — verify insurance, record copay/coinsurance/patient
   responsibility.
2. **Patient responsibility entered in ModMed** — checkbox once done.
3. **Patient notified to schedule** — Klara message (drafter pre-fills total cost).
4. **Insertion appointment scheduled** — record the date.
5. **Device checked out** — MA pulls from cabinet (see Check-out rules).
6. **Device inserted** — record outcome (inserted, failed, no-show, etc.).
7. **Billed** — record the ModMed claim # to close the assignment.
"""),

    ("office-procedure-overview", "Office Procedure Devices — overview", 35, """\
**Office Procedure Devices** are single-use surgical instruments consumed
during an in-office procedure. They share the same locked cabinet, audit
log, label printing, and physical inventory count as LARC devices — but
the workflow is shorter because there's no patient self-service step.

**Devices tracked:**

| Device | Manufacturer | Cost | Used for |
|---|---|---|---|
| **NovaSure** | Hologic | ~$1,300 | Endometrial ablation (handpiece) |
| **Bensta** | Caldera Medical | ~$800 | Tissue resector — polyp removal during D&C / hysteroscopy |

**How they differ from LARC:**

- **No DocuSign / enrollment** — patient signs surgery consents separately
  (handled by the Surgery module).
- **No pharmacy step** — devices are kept in stock, never ordered patient-specific.
- **No patient self-service** — the scheduler picks the device at surgery
  booking; the patient never interacts with the device workflow.
- **Single milestone catalog** — only 4 steps instead of 10.
- **Reorder when stock ≤ 2** — system auto-flags on the dashboard with
  the suggested order quantity (3 by default, configurable per device type).

**Where the device is picked:** on the Surgery detail page, when the
procedure list mentions ablation / polyp / D&C / hysteroscopy, an
**Office-procedure device** card appears with a picker. Once a device is
bound to the surgery, it's linked via `linked_surgery_id` and the LARC
audit trail follows it through consumption and billing.
"""),

    ("office-procedure-flow", "Office-procedure workflow", 36, """\
The flow has **4 milestones** (vs LARC's 10):

1. **Benefits verified** — record insurance, copay/coinsurance, and patient
   responsibility. Same as LARC.
2. **Device picked from inventory** — the scheduler opens the surgery, sees
   the **Office-procedure device** card, and picks an unallocated NovaSure
   (for ablations) or Bensta (for polypectomy / D&C). This:
   - Sets the device status to `assigned`.
   - Stamps `linked_surgery_id` on the LARC assignment.
   - Auto-completes the "Device picked from inventory" milestone.
3. **Device consumed during procedure** — after the surgery, the MA opens
   the LARC assignment and clicks **Mark device consumed**. This:
   - Sets the device status to `inserted` (reusing the LARC term so
     dashboards and audits stay consistent).
   - Records `inserted_at` + `inserted_by`.
4. **Billed** — once the claim is submitted in ModMed, enter the claim # to
   close the assignment. Same as LARC.

**Dashboard buckets** (chip filters on `/larc`):

- **OP — Pick Device** — surgery scheduled but no device chosen yet.
- **OP — Assigned** — device picked, awaiting procedure day.
- **OP — To Bill** — consumed during procedure, claim # not yet recorded.

**Reorder rule:** when on-hand count for a device type drops to **≤ 2**,
a yellow reorder alert appears on the dashboard with the suggested
quantity (3 by default). Thresholds + quantities are editable on the
**Device type catalog** page.

**Expiration tracking:** office-procedure devices follow the same
365-day expiry-hold rule as LARC. Devices within 365 days of expiry are
flagged on the dashboard so they can be used before they're scrapped.

> **Linking back to the surgery:** the assignment detail page shows a
> "View linked surgery →" link inside the *Device picked from inventory*
> milestone card. Clicking the device's `our_id` from the Surgery page
> deep-links into the LARC assignment.
"""),

    ("pharmacy-flow", "Pharmacy-order workflow", 30, """\
1. **Benefits check** — same as in-stock.
2. **Enrollment form sent** — DocuSign envelope to patient (per device type's
   template). Bayer devices (Mirena/Skyla/Kyleena) share a form with different
   options checked. Patients without DocuSign get the paper form.
3. **Enrollment form signed** — patient returns the signed form.
4. **Request faxed to pharmacy** — pick the right pharmacy from the directory
   (the patient's prescription plan determines which one). The fax # auto-fills.
   This starts the **14-day SLA clock**.
5. **Device received** — when the package arrives:
   - Mint a new our_id label (e.g. WWC0701).
   - Record manufacturer lot # from the box (matters for FDA recalls).
   - Record expiration date.
   - Print the QR-coded label and put it on the box.
6. **Patient notified** → schedule → check out → insert → bill (same as in-stock).

> If a pharmacy order is more than 14 days past faxed and not received, it
> shows up on the dashboard's **Overdue pharmacy orders** panel — call the
> pharmacy to follow up.
"""),

    ("checkout", "Device check-out rules", 40, """\
**Who can check out a device**: MAs and managers only.

**Identity check**: the MA enters the patient's DOB at request time. The system
verifies it matches the chart.

**Hybrid approval**:

- **Auto-approved** when ALL gates pass:
  - DOB matches the chart
  - Appointment date = today
  - Benefits-verified milestone is done
  - Device is currently `assigned` (not lost / defective / inserted / billed)
- **Flagged for manager approval** if any gate fails — manager reviews on
  the *Pending checkouts* page.

**Given-to**: storage is locked, so the MA records who they handed the device
to (often a provider). This is the chain of custody.

**After the visit**, the MA must record the outcome within 24 hours:

| Outcome | Effect |
|---|---|
| `inserted` | Device → status=inserted. Next: bill it. |
| `failed_unused` | Device returns to stock. |
| `failed_used` | Device → defective. Trigger manufacturer return + replacement. |
| `patient_no_show` / `patient_canceled` / `office_canceled` | Device → unassigned. |
| `lost` | Records the dollar-value loss. |
| `other` | Notes are required. |

> Checkouts not acknowledged within 24 hours surface on the dashboard's
> **Unacknowledged checkouts** panel.
"""),

    ("defective", "Defective device → manufacturer return", 50, """\
When insertion fails with the device used (`failed_used`), the device is
presumed defective. The assignment shows a red **Defective device — replacement
chain** banner with a 2-step flow:

1. **Return to manufacturer**: record RMA #, courier (FedEx/UPS/etc.),
   tracking #. Device → status=returned.
2. **Receive replacement**: when the manufacturer ships back, mint a new
   `our_id` for the replacement. The system:
   - Creates a new LarcDevice row with `replaces_device_id` pointing back.
   - Marks the original `replacement_device_id` to the new one.
   - Opens a new assignment for the patient on the replacement device.
   - Carries over completed milestones (benefits, enrollment).
   - Closes the original assignment.

This preserves the full audit trail: you can always see the chain
**defective → returned → replacement** by following the device links.
"""),

    ("reallocation", "6-month reallocation + Owed list", 60, """\
Assigned devices that haven't been inserted for **180 days** get reallocated:

- The device goes back to **unassigned**.
- The patient lands on the **Owed list**.
- The patient has **until the original device's expiration date** to claim a
  fresh one. If they come back, create a new LARC request for them and
  resolve their Owed-list entry as `reallocated`.
- If they don't come back, mark the entry `declined` or let it auto-expire.

Devices within **365 days of expiry** also get reallocated automatically
(so we don't risk inserting near-expired product). This is checked by a
daily sweep at 9:15 AM.
"""),

    ("dashboard", "Dashboard buckets", 70, """\
Each active assignment falls into one or more workload buckets:

- **Outstanding** — every active assignment
- **Incomplete** — missing intake info
- **Needs Benefits** — benefits not yet verified
- **Needs Enrollment** — pharmacy-order: enrollment form not signed
- **Needs Fax** — enrollment signed but request not yet faxed
- **Awaiting Receipt** — request faxed, device not yet here
- **Received — Notify** — device arrived, patient not notified yet
- **Appt Scheduled** — insertion booked
- **Checked Out** — device pulled, awaiting outcome
- **Inserted — To Bill** — successfully inserted, claim # not yet recorded
- **Failed — Need Replacement** — defective device, replacement not requested
- **Failed — Pending** — replacement device pending from manufacturer
- **Unack Checkout** — checkout sat >24h with no outcome
- **Owed List** — patient owed a reallocated device

Click any chip to filter the assignment list.
"""),

    ("audit", "Audit trail", 80, """\
Every state change writes one row to the audit log:

- Devices added, edited, lost, returned, replacement received
- Assignments created, milestones advanced
- Checkouts requested, auto-approved, manager-approved, denied, acknowledged
- Outcomes recorded, billed
- System sweeps (expiry hold, stale reallocation, pharmacy SLA breach)
- Inventory counts started / finished / marked lost

**Filter the audit log** by user, device, patient, action, or system-only.
Use it to investigate any discrepancy.
"""),

    ("storage", "Storage locations", 90, """\
Three WWC locations have locked LARC cabinets:

- **White Plains** (`white_plains`) — main office
- **Arlington** (`arlington`)
- **Brandywine** (`brandywine`)

Each device is tagged with its location at receipt. When running a Physical
Inventory Count, you can scope it to one location or count everything at once.
"""),

    ("inventory-count", "Physical inventory count", 100, """\
Open **Physical count** from the dashboard. Workflow:

1. **Start count** — pick a location (or all).
2. The system snapshots every expected on-hand device into the count.
3. **Scan each device** in the cabinet:
   - **USB scanner**: just plug in, scan QR or type our_id, press Enter.
   - **Phone camera**: tap 📷 Camera (requires HTTPS).
4. The page surfaces in real time:
   - **Unexpected scans** — device shows up here that wasn't expected at this location
   - **Not yet scanned** — devices the system expected but you haven't found
5. **Finish** — any unscanned devices are marked **lost** with their purchase
   price counted toward the loss tally.

Recommended: run a count every quarter, plus an annual full reconciliation.
"""),

    ("pricing", "Pricing context", 110, """\
Per-device costs (approximate, update via the Device type catalog):

| Device | Cost |
|---|---|
| Liletta | ~$750 |
| Skyla | ~$850 |
| Mirena | ~$900 |
| Kyleena | ~$900 |
| Paragard | ~$900 |
| Nexplanon | ~$1,100 |

These drive the loss-tracking dashboard. A single lost or expired device costs
hundreds of dollars — accounting and audit are why this module exists.
"""),
]


def seed_larc_manual():
    """One-time seed of the LARC manual sections. Idempotent — only adds
    sections whose slug doesn't already exist."""
    db = SessionLocal()
    try:
        existing = {s.slug for s in db.query(LarcManualSection).all()}
        added = 0
        for slug, title, sort_order, body in SEED_MANUAL_SECTIONS:
            if slug in existing:
                continue
            db.add(LarcManualSection(
                slug=slug, title=title, sort_order=sort_order,
                body_md=body, updated_by="system:seed",
            ))
            added += 1
        if added:
            db.commit()
    finally:
        db.close()
