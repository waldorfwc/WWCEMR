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

SURGERY_MANUAL_SECTIONS = [
    ("overview", "Overview", 10, """\
The Surgery module tracks cases from initial intake through billing close-out.
Two primary settings drive the workflow:

**Facilities:**
- **MedStar SMHC** — robotic and major minimally-invasive cases.
- **UM Charles Regional (CRMC)** — minor outpatient or major open cases.
- **WWC Office Procedure Suite** — in-office procedures, Thursdays only.

Each case moves through numbered milestone cards on the Surgery Detail page.
The dashboard groups cases into workload buckets by which steps are still open.
"""),

    ("intake", "Starting a Surgery", 20, """\
New surgeries are created by bulk import or from the patient chart — not from
the dashboard. Cases arrive in **Incomplete** status and need to be triaged.

**Required intake fields** (from the Info milestone card):

- Patient name, chart #, DOB, contact info
- Procedure(s) and ICD-10 diagnosis codes
- Surgeon and facility
- Insurance / payer on file
- Surgery duration (set by the coordinator before the patient picks a date)

**Bulk import:** Upload a ModMed-style patient roster (.xlsx) via Surgery →
Bulk Import. Always run the Preview (dry-run) first — it shows how many rows
will create, skip (duplicate active chart #) or error without saving anything.
Imported cases land in **Incomplete** status.

> Duration defaults from the surgery type or the time extracted from the
> surgery order. The patient never sees a duration picker — coordinators
> set it before offering dates.
"""),

    ("benefits", "Benefits Verification", 30, """\
The **Benefits & Payment** milestone card contains two sub-sections:

**Benefits calculator:**
1. Pull the allowed amount from the Fee Schedule (Insurance + CPT).
2. Enter the patient's deductible, out-of-pocket max, coinsurance and copay.
3. The system estimates what the patient owes.
4. "Save + generate PDF" produces the benefits estimate PDF.

**Payment:**
- Request a Stripe payment link from this card and track the balance.

> If a secondary insurance is on file but its terms are blank, the
> calculator assumes secondary covers everything and shows $0 owed —
> an amber warning banner flags this case.
"""),

    ("consent", "Consents & E-Signatures", 40, """\
The **Consents** milestone card manages consent collection.

**Sending via BoldSign:**
1. Click "Send via BoldSign" — the system matches the case's CPT codes
   (primary match) or procedure keywords (fallback) to templates registered
   in Surgery Settings → Consent Templates.
2. One primary consent + any applicable supplemental templates (e.g. Medicaid
   sterilization) are sent together.
3. The patient signs electronically; click "View" to see the signed PDF.

**Manual options:**
- **Mark sent (paper)** — for in-person cases where paper forms are used.
- **Mark signed (manual)** — to record a consent signed outside the system.
- **Reset consent** — clears the send record to start over.

> Medicaid sterilization consent must be signed at least 30 and no more
> than 180 days before the procedure date.

> Consents only send if a template matching the case's procedures is
> registered in Surgery Settings → Consent Templates. If nothing sends,
> check the CPT/keyword match there first.
"""),

    ("scheduling", "Scheduling & Block Calendar", 50, """\
**Patient self-scheduling (default path):**
Patients self-schedule via a soft-auth portal (date of birth + last 4 digits
of phone). They see only days where their procedure fits the facility's
capacity rules.

**Coordinator-booked:**
"Schedule for patient" on the Surgery Detail page lets a coordinator pick a
block day and time slot directly. The Calendar page also offers an
"Open a day" drawer with a per-facility time grid and an "available" slot picker.

**Block calendar rules:**
- Recurring schedules generate block days on a 5-week pattern; "Re-materialize"
  rebuilds them after schedule or blackout changes.
- Blackouts block specific dates (holidays auto-seed through 2031; add PTO or
  facility-closed dates manually). "Add Surgery Day" on the Blackouts tab marks
  a one-off date as bookable.

**Capacity limits (from Settings → Facilities & Capacity):**
| Facility | Limit |
|---|---|
| MedStar | 3 × 180-min OR 2 × 240-min robotic (can't mix) |
| CRMC | 6 minor OR 2 major per day (can't mix) |
| Office | Fixed Thursday slot start times |

**Post-op dates:** after a date is booked, set the post-op visit dates
(Office and/or Telehealth) on the same card.

> The waitlist (Surgery → Waitlist) shows patients hoping for an earlier slot.
> Click an open date chip to see matches ranked by wait time, then copy the
> Klara blast and click "Patient claimed" to book.
"""),

    ("preop-postop", "Pre-Op & Post-Op Steps", 60, """\
The Surgery Detail page works **top to bottom** through numbered milestone cards.
The step engine tracks each card's completion and flags cases that fall behind
their expected window (configurable in Settings → Workflow Steps).

**Hospital pathway milestones (typical order):**
1. Info — intake complete
2. Benefits & Payment
3. Consents
4. Date selected + post-op scheduled
5. Hospital Posting / Boarding Slip — generate the facility form, edit fields,
   fax or email to the hospital; send history records every attempt.
6. ModMed appointment confirmed
7. Pre-op labs reported
8. Welfare call recorded
9. Bill surgery — record the ModMed claim #

**Optional cards** (appear only when the case needs them):
- Device (office-procedure device linked from LARC module)
- Prior Auth
- Clearance / EKG
- Assistant Surgeon

**Dashboard:** the Scheduler To-Do page (Surgery → To-Do) shows the single
next open step for every active surgery with a red "Xd behind" border when
past its expected window.

> A case is automatically moved to **Unresponsive** status when no date is
> picked within the configured window after the pre-op (Alerts & Windows tab).
"""),

    ("statuses", "Status Taxonomy", 70, """\
| DB value | Display label | Meaning |
|---|---|---|
| `incomplete` | Incomplete | Intake not finished — needs triage |
| `new` | New | Intake done, benefits not started |
| `in_progress` | Benefits Check | Actively working benefits / payment |
| `confirmed` | Pre-Surgery | Date booked, working toward procedure day |
| `completed` | Post-Surgery | Procedure done, billing close-out in progress |
| `hold` | Hold | Deliberately paused (coordinator hold) |
| `cancelled` | Canceled | Canceled; releases the block slot |
| `unresponsive` | Unresponsive | Auto-set when no date picked past the window |

**Auto-transitions:**
- A case moves to **Unresponsive** automatically when no date is picked within
  `unresponsive_after_days` of the pre-op (set in Alerts & Windows).
- Canceling a case releases its block slot.

**Cancel / Hold drawer:** choosing Cancel or Hold prompts for a reason
(patient, anesthesia, hospital, medical, hold or unresponsive). A
cancellation fee warning appears if the cancel is within the configured
window (default: 2 weeks before surgery).
"""),

    ("billing", "Billing Close-Out", 80, """\
**On the Surgery Detail page:**
1. The **ModMed** milestone card records that the appointment exists in ModMed.
2. The **Labs** card records that pre-op lab results are on file.
3. The **Bill Surgery** card records the ModMed claim # — entering it
   moves the case toward Post-Surgery status.

**Payment Posting (Surgery → Payment Posting):**
Lists Stripe patient payments (balance, FMLA, cancellation or no-show fees)
that still need posting to ModMed.

Workflow:
1. Click "How To Post In ModMed" for the step-by-step guide and field
   cheat-sheet (Amount, Confirmation # to copy from the row).
2. Post the payment in ModMed.
3. Type your initials and click "Mark Posted" — stamps initials and time.
4. Managers can "Un-mark" a row if posted by mistake.

**Fee Schedule (Surgery → Fee Schedule):**
Holds the contracted allowed dollar amount per Insurance + CPT that feeds the
benefits calculator. Also contains CCI/MPR edit overrides for bundled CPT pairs.

**Notes and files:** Post timestamped notes and upload files (order, op note,
path report) from the Surgery Detail page.
"""),

    ("settings", "Surgery Settings", 90, """\
Surgery Settings (Surgery → Settings) is practice-wide configuration.
Changes affect every surgery.

**Tabs:**

| Tab | What it controls |
|---|---|
| Alerts & Windows | Overdue threshold, labs/pre-op validity, schedule horizon, cancellation fee amount and window, office capacity, boarding-slip auto-email timing and recipients |
| Workflow Steps | Named steps and expected days for Hospital and Office pathways — "expected days" is what flags a case as behind |
| Post-Op Schedules | Visit rules (days after surgery, Office vs Telehealth) matched to a procedure by keyword |
| Facilities & Capacity | Facility list and daily case limits / office slot times |
| Clearances & Devices | Clearance types, device types, assistant surgeons, Payer ID → Insurance map |
| Surgery Types | Each procedure: CPTs, classification, eligible facilities, attached consents |
| Templates | Procedure, email and SMS templates with editable subject/body and preview |
| Consent Templates | BoldSign template IDs — matched by CPT codes (primary) or keywords (fallback), with optional facility/insurance conditions; mark "supplemental" to add on top of the primary |
| Message Templates | Staff-facing message snippets |
| Google Sync | Connects the surgery calendar to Google |

> If a consent won't send on a case, check the CPT/keyword match in
> Consent Templates first.
> Changing expected days in Workflow Steps re-scores which cases show
> as behind on the dashboard.
"""),
]

MANUAL_SEEDS = {
    "device_larc": LARC_MANUAL_SECTIONS,
    "pellets":     PELLET_MANUAL_SECTIONS,
    "surgery":     SURGERY_MANUAL_SECTIONS,
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
