"""Per-module manual seed content for the unified manual system."""
from app.database import SessionLocal
from app.models.manual import ManualSection

LARC_MANUAL_SECTIONS = [
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

    ("checkout-quick-action", "Check Out a Device (quick action)", 45, """\
The **Check Out a Device** button sits in the top-right of every LARC page,
next to *Start LARC Process* and *Add Device* (LARC Work access). It opens a
drawer listing every device that's **ready to check out** — an active
assignment with an on-hand device and no pending checkout.

For each patient:

1. Read the device's **Our ID off the physical label** and type it in.
2. Optionally record who you're handing it to (**Given to**).
3. Click **Check out**.

Typing the label ID is the safeguard — it must match the device bound to that
patient, so the wrong unit can't be checked out. This is a **direct checkout**:
it bypasses the standard request gates (DOB / same-day appt / benefits) and
records the checkout immediately, which is the fast path for the common
"device is here, patient is in the room" case. The "given to" chain-of-custody
still applies.

The same ready-to-check-out list also appears on the **Overview** tab and on
**My Checklist**, so you can work from wherever you are.

> For the gated request flow (DOB + same-day-appt + benefits checks, with
> manager approval when a gate fails), see **Device check-out rules** above.
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

> The **180-day** clock now runs from when the device was **received**, not
> from when the request was created — see *Device ownership & WWC Claimed*.
"""),

    ("device-ownership", "Device ownership & WWC Claimed", 65, """\
Every device carries an **ownership** classification that decides whether WWC
bills insurance for it:

| Ownership | Meaning | WWC bills insurance? |
|---|---|---|
| **WWC Owned** | WWC purchased the device outright. | Yes |
| **Patient Owned** | The patient or their pharmacy benefit paid (pharmacy-order devices). | **No** |
| **WWC Claimed** | Originally patient-owned, but WWC has claimed it (patient didn't use it in time, or declined). | Yes |

The badge shows on the device page; the original payer is kept in the
**Purchased by patient** field for patient-owned and WWC-claimed devices.

**Automatic claiming (sweeps).** When a reallocation sweep pulls a device back
to the Owed list, a **patient-owned** device is **automatically reclassified as
WWC Claimed** (WWC-owned devices are left as-is — "claimed" only applies to a
device the patient originally paid for). This happens on both sweeps:

- **Unused after receipt** — a pharmacy device not used **180 days after it was
  received** is reallocated and claimed. (The clock runs from device receipt,
  not from when the request was created.)
- **Near expiry** — a device within 365 days of expiry is reallocated and, if
  patient-owned, claimed.

Each auto-claim writes an `ownership_changed` row to the audit log (actor
`system:stale-sweep` / `system:expiry-sweep`), and the patient lands on the
**Owed list**.

**Manual claiming.** A manager can claim a device by hand from the device page:
click **change** next to the ownership badge, choose **WWC Claimed**, and record
a reason (e.g. "patient confirmed she no longer wants it inserted"). The reason
is required and is written to the audit trail. Use this the moment a patient
explicitly declines — you don't have to wait for the sweep.
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

PELLET_MANUAL_SECTIONS = [
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

MANUAL_SEEDS = {
    "device_larc": LARC_MANUAL_SECTIONS,
    "pellets":     PELLET_MANUAL_SECTIONS,
}


def seed_manuals(db=None):
    """Idempotent: insert only (module, slug) rows that don't already exist."""
    own = db is None
    if own:
        db = SessionLocal()
    try:
        for module, sections in MANUAL_SEEDS.items():
            have = {s.slug for s in db.query(ManualSection).filter_by(module=module).all()}
            added = 0
            for slug, title, sort_order, body in sections:
                if slug in have:
                    continue
                db.add(ManualSection(module=module, slug=slug, title=title,
                                     sort_order=sort_order, body_md=body,
                                     updated_by="system:seed"))
                added += 1
            if added:
                db.commit()
    finally:
        if own:
            db.close()
