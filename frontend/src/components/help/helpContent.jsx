/**
 * Per-page Help content registry.
 *
 * Each entry is HAND-AUTHORED from the page's REAL controls + workflow — not
 * generated. Keep `body` to 1–2 plain-language sentences for non-technical
 * staff. `tone` colors the section icon / callout; see TONES below.
 *
 * Add a page by:
 *   1. authoring an entry keyed by a stable slug, then
 *   2. mapping its route(s) in helpKeyForPath().
 *
 * helpKeyForPath() returns null when no help is authored for the current
 * route, so <HelpButton/> renders nothing there.
 */
import {
  Calendar, DollarSign, AlertTriangle, FileSignature, ClipboardList,
  Users, Upload, Mail, Link2, Filter, RefreshCw, ListChecks,
  UserPlus, StickyNote, FileText, Pencil,
  LayoutGrid, BarChart3, Download, Phone, Boxes,
  Settings, SlidersHorizontal, Package, ScanLine, History,
  Inbox, ShieldCheck, KeyRound, ClipboardCheck,
} from 'lucide-react'

// tone → Tailwind classes for the section icon chip + callout.
export const TONES = {
  plum:  { icon: 'text-plum-700', chip: 'bg-plum-100 text-plum-700',   callout: 'bg-plum-50 border-plum-200 text-plum-900' },
  blue:  { icon: 'text-info',     chip: 'bg-blue-100 text-info',       callout: 'bg-blue-50 border-blue-200 text-blue-900' },
  amber: { icon: 'text-warning',  chip: 'bg-amber-100 text-warning',   callout: 'bg-amber-50 border-amber-200 text-amber-900' },
  green: { icon: 'text-success',  chip: 'bg-green-100 text-success',   callout: 'bg-green-50 border-green-200 text-green-900' },
  red:   { icon: 'text-danger',   chip: 'bg-red-100 text-danger',      callout: 'bg-red-50 border-red-200 text-red-900' },
  gray:  { icon: 'text-muted',    chip: 'bg-gray-100 text-muted',      callout: 'bg-gray-50 border-gray-200 text-gray-700' },
}

export const HELP_CONTENT = {
  // ── Surgery Detail · /surgery/:id ──────────────────────────────
  'surgery-detail': {
    title: 'Surgery Detail',
    steps: ['Info', 'Benefits & Payment', 'Consents', 'Schedule', 'Post & Bill'],
    sections: [
      { icon: ClipboardList, tone: 'plum', title: 'Milestone Cards',
        body: 'This case is worked top to bottom through numbered milestone cards — Info, Benefits, Payment, Consents, Date, Hospital Posting, ModMed, Labs and Bill. Optional cards (Device, Prior Auth, Clearance, Assistant Surgeon) appear only when the case needs them.' },
      { icon: Pencil, tone: 'gray', title: 'Patient Header',
        body: 'The header shows the patient, status and key fields, most of which edit inline. Click the patient name to fix it; use View as Patient, Send Portal Access, Klara message and Messages to communicate.' },
      { icon: DollarSign, tone: 'green', title: 'Benefits & Payment',
        body: 'The Benefits calculator estimates what the patient owes from their insurance terms (pull the allowed amount from the fee schedule), then "Save + generate PDF" makes the estimate. Payment lets you request a Stripe payment and see the balance.' },
      { icon: FileSignature, tone: 'blue', title: 'Consents',
        body: 'Send consent forms with "Send via BoldSign" for e-signature, then View the signed PDFs. You can also Mark sent (paper) or Mark signed (manual) for in-person cases, or Reset consent to start over.' },
      { icon: Calendar, tone: 'plum', title: 'Select Date & Post-Op',
        body: 'Patients usually self-schedule on the portal; "Schedule for patient" lets a coordinator book a block day and time slot. Then set the post-op visit dates (Office / Telehealth).' },
      { icon: FileText, tone: 'amber', title: 'Hospital Posting / Boarding Slip',
        body: 'Generate the facility posting form prefilled with the case details, "Edit Fields" to adjust, then Fax or Email it to the hospital. The send history records every attempt.' },
      { icon: StickyNote, tone: 'gray', title: 'Notes, Files & Bill',
        body: 'Post timestamped notes and upload files (order, op note, path report). Later cards mark the ModMed appointment, pre-op labs, welfare call, and record the ModMed claim # to bill the surgery.' },
      { icon: AlertTriangle, tone: 'red', title: 'Cancel / Hold',
        body: 'Cancel / hold opens a drawer to choose a reason (patient, anesthesia, hospital, medical, hold or unresponsive). A canceled case releases its block slot, and a cancellation fee may apply if within 2 weeks.' },
    ],
    tips: [
      'Blacked-out / non-block days are hidden when picking a date — you can only land on real surgery block days.',
      'If a secondary insurance is on file but its terms are left blank, the calculator assumes it covers everything and shows $0 owed — an amber banner warns you.',
      'Consents only send if templates matching the case’s procedures are registered in Surgery Settings.',
    ],
  },

  // ── Pellets · /pellets (Patients list) ─────────────────────────
  'pellets': {
    title: 'Pellets',
    steps: ['Enroll', 'Verify Mammo / Labs', 'Dose & Bag', 'Collect Payment', 'Insert'],
    sections: [
      { icon: Users, tone: 'plum', title: 'Patient Views',
        body: 'Switch between views like Upcoming, All patients, Recall due, Needs mammo, Needs dosing, Ready to insert, Paid and Unpaid to work the roster the way you need.' },
      { icon: Calendar, tone: 'blue', title: 'Upcoming Calendar',
        body: 'The Upcoming view shows a 7-day schedule with location chips and badges for mammo, labs, payment and bagging readiness.' },
      { icon: Filter, tone: 'gray', title: 'Search & Filters',
        body: 'Filter by name or chart number, patient type (New $500 / Established $400), status, and location. Save a filter combination as a preset chip for quick reuse.' },
      { icon: UserPlus, tone: 'green', title: 'Enroll Patient',
        body: 'Add a new pellet patient to the roster. Use Upload ModMed appts to pull in upcoming appointments from the schedule.' },
      { icon: ListChecks, tone: 'plum', title: 'Open a Patient',
        body: 'Click any row to open that patient’s detail page, where you verify mammo/labs, create the dose card, bag, take payment and mark the insertion.' },
      { icon: RefreshCw, tone: 'amber', title: 'Other Tabs',
        body: 'The top tabs cover Patient Activity, Inventory, Counts, Recall, Reports and Audit for the controlled-substance pellet supply.' },
    ],
    tips: [
      'Pellets are a DEA Schedule III controlled substance — Counts and Audit exist to keep the inventory reconciled.',
      'A green "ready" summary means mammo, labs and payment are all cleared for that visit.',
    ],
  },

  // ── Missing Charges · /billing/missing-charges ─────────────────
  'missing-charges': {
    title: 'Missing Charges',
    steps: ['Upload Report', 'Triage', 'Needs Billed', 'Provider Bills', 'Claim # → Billed'],
    sections: [
      { icon: Upload, tone: 'plum', title: 'Upload Report',
        body: 'Upload the ModMed "Appointment Missing Charges" Excel to load encounters with no charge on file. Rows already on file (same patient + date) are skipped automatically.' },
      { icon: ListChecks, tone: 'blue', title: 'Status Workflow',
        body: 'A row moves New → Needs to be billed → Provider says billed / Provider can’t bill → Billed. Use "Seen — Needs Billing", "No Show" or "Canceled" to triage New rows.' },
      { icon: Filter, tone: 'gray', title: 'Status Cards & Filters',
        body: 'Click a status counter card to filter to that status. Also search by patient, MRN or claim #, and filter by provider, payer or date of service. "Open only" hides billed / no-show / canceled rows.' },
      { icon: Mail, tone: 'green', title: 'Email Providers',
        body: 'Sends each provider one email listing their open "Needs to be billed" rows, each with a self-service link to mark rows Billed or Error. Use "Send Weekly Emails Now" for an ad-hoc run.' },
      { icon: Users, tone: 'amber', title: 'Provider Mappings',
        body: 'Inside Email Providers, set which user email each provider’s list goes to. "Auto-match from Google directory" matches unmapped providers; unmapped providers get no automated email until set.' },
      { icon: Link2, tone: 'red', title: 'Revoke Links',
        body: 'In the mappings table, "Revoke Links" invalidates a provider’s outstanding portal links — they receive a fresh link on the next email.' },
      { icon: FileText, tone: 'plum', title: 'Row Detail & Claim #',
        body: 'Click a row to open its detail drawer: triage it, read the provider’s error reason, add notes, and enter the ModMed claim # to mark it Billed. Billed rows can be Reopened.' },
    ],
    tips: [
      'Provider emails go out automatically every Monday at 8 AM; "Send Weekly Emails Now" is only for an ad-hoc run.',
      'The signed provider portal links expire after 14 days. Use "Revoke Links" on a provider mapping to invalidate their outstanding links immediately (e.g. when they leave).',
    ],
  },

  // ── Surgery Dashboard · /surgery ───────────────────────────────
  'surgery-dashboard': {
    title: 'Surgery Dashboard',
    steps: ['Watch Alerts', 'Filter the List', 'Open a Case', 'Work It'],
    sections: [
      { icon: ListChecks, tone: 'plum', title: 'Status & Step Counters',
        body: 'The counter chips tally cases that need attention — Unresponsive, Needs Repeat Pre-op, and each numbered step (Benefits, Consents, Post-Op Dates, Prior Auth, Clearance / EKG, Labs, Bill Surgery and more). Click a chip to filter the list to those cases.' },
      { icon: Calendar, tone: 'blue', title: 'Next Available Dates',
        body: 'Three cards show the next open surgery date and how many days out for MedStar (robotic), CRMC (minor/major) and Office (Thursdays), so you can quote scheduling at a glance.' },
      { icon: AlertTriangle, tone: 'red', title: 'Critical Alerts & To-Do',
        body: 'Critical Alerts lists milestones more than 48 hours overdue. The To-do panel flags cases on a now-blocked day ("Mark hospital notified"), hospital slots to release, and underbooked office days.' },
      { icon: Calendar, tone: 'plum', title: 'List & Calendar Views',
        body: 'Toggle between the worked list (grouped by action bucket) and a weekly calendar. The calendar has Prev / Next, This week, a "Jump to" date picker, and a legend for ready / open-tasks / behind cases.' },
      { icon: Filter, tone: 'gray', title: 'Search, Filters & Presets',
        body: 'Search by patient, chart # or surgery #, then narrow by Status and Facility, or the Urgent / Behind checkboxes. "More filters" adds procedure type, surgeon, insurance, auth status, dates and age. Save a combination as a preset chip and star one as your default.' },
    ],
    tips: [
      'Each list row opens that case’s Surgery Detail page — new cases are created from the patient chart / import, not from a button here.',
      'A scheduler alert banner warns when a booked case lands on a day that has since been blocked or is over capacity.',
    ],
  },

  // ── LARC / Device Tracking · /larc ─────────────────────────────
  'larc': {
    title: 'Device Tracking',
    steps: ['Work a Bucket', 'Benefits & Enroll', 'Fax Pharmacy', 'Receive', 'Insert & Bill'],
    sections: [
      { icon: ListChecks, tone: 'plum', title: 'Workflow Buckets',
        body: 'The bucket cards group every assignment by what it needs next — Needs Benefits, Needs Enrollment, Needs Fax, Awaiting Receipt, Received — Notify, Appt Scheduled, Inserted — To Bill, plus the Office-Procedure (OP) lanes. Click a bucket to load just those patients.' },
      { icon: UserPlus, tone: 'green', title: 'Enrollment & Benefits',
        body: 'Use "LARC Enrollment Form" to start a new pharmacy enrollment, or "Benefits for In-Stock Device" when the device is already on the shelf.' },
      { icon: Boxes, tone: 'blue', title: 'Device Inventory',
        body: 'The on-hand grid shows counts by device type, split into LARC and Office Procedure Devices, so you can see stock at a glance.' },
      { icon: AlertTriangle, tone: 'amber', title: 'Reorder & Expiry Alerts',
        body: 'Cards surface Reorder Alerts (below threshold), devices Expiring Within 365 Days, Overdue Pharmacy Orders, and Unacknowledged Checkouts — use the "Ack" button to clear a checkout.' },
      { icon: Filter, tone: 'gray', title: 'Assignment List',
        body: 'The list is sortable by Patient, Device, Flow, Status and Created with a search box for name or chart #. Click any row to open that assignment and advance it.' },
      { icon: LayoutGrid, tone: 'plum', title: 'Nav Tabs',
        body: 'The top tabs cover Overview, Devices, Checkouts, Owed, Reports, Inventory Count, EOD Report, Audit, Manual and Settings.' },
    ],
    tips: [
      'The "Owed List" / Owed tab tracks patients who still owe for a device after insertion.',
      'Use "+ Add Device" in the nav to log a new physical device into inventory.',
    ],
  },

  // ── LARC Reports · /larc/reports ───────────────────────────────
  'larc-reports': {
    title: 'Device Tracking Reports',
    steps: ['Set Filters', 'Read a Tile', 'Drill In', 'Download CSV'],
    sections: [
      { icon: Filter, tone: 'gray', title: 'Filter Bar',
        body: 'Set the date range (This Month, Last Month, Last 30 / 90 Days, or Custom), plus Location and Device Type. Every tile recalculates to match the filters.' },
      { icon: BarChart3, tone: 'plum', title: 'Report Tiles',
        body: 'Seven tiles summarize the program: Workflow Funnel, Outstanding Enrollment, Insertions, Billing Backlog, Owed Patients, Inventory Health and Insertion Outcomes.' },
      { icon: ListChecks, tone: 'blue', title: 'Click to Drill Down',
        body: 'Each tile headline — and the rows inside it (stages, categories, outcomes) — is clickable and opens a drawer listing the exact patients or devices behind that number.' },
      { icon: Download, tone: 'green', title: 'Download CSV',
        body: 'The drill-down drawer has a "Download CSV" button to export that list for follow-up or sharing.' },
    ],
    tips: [
      'Numbers are read-only summaries — to actually work a case, drill in and open the patient from the list.',
    ],
  },

  // ── Surgery Reports · /surgery/reports ─────────────────────────
  'surgery-reports': {
    title: 'Surgery Reports',
    steps: ['Set Filters', 'Read a Tile', 'Drill In', 'Download CSV'],
    sections: [
      { icon: Filter, tone: 'gray', title: 'Filter Bar',
        body: 'Choose the date range (This Month, Last Month, Last 30 / 90 Days, This Year or Custom) and filter by Facility and Surgeon. A toggle counts by Surgery Date or Created Date.' },
      { icon: BarChart3, tone: 'plum', title: 'Report Tiles',
        body: 'Tiles cover the surgery pipeline: Status Funnel, Not Ready (≤14 Days), Completed, Cycle Time, Payment Posting Backlog and Utilization.' },
      { icon: ListChecks, tone: 'blue', title: 'Click to Drill Down',
        body: 'Each tile headline and its sub-rows (status stages, blockers, facilities) is clickable and opens a drawer of the matching cases; each case links to its Surgery Detail page.' },
      { icon: Download, tone: 'green', title: 'Download CSV',
        body: 'The drill-down drawer has a "Download CSV" button to export the list.' },
    ],
    tips: [
      '"Not Ready (≤14 Days)" is the most actionable tile — it shows cases close to surgery that still have open blockers.',
    ],
  },

  // ── Pellet Reports · /pellets/reports ──────────────────────────
  'pellet-reports': {
    title: 'Pellet Reports',
    steps: ['Set Filters', 'Read a Tile', 'Drill In', 'Download CSV'],
    sections: [
      { icon: Filter, tone: 'gray', title: 'Filter Bar',
        body: 'Set the date range (This Month, Last Month, Last 30 / 90 Days, This Year or Custom), plus Location and Provider. Every tile recalculates to match.' },
      { icon: BarChart3, tone: 'plum', title: 'Report Tiles',
        body: 'Tiles cover the pellet program: Visit Status Funnel, Insertions, Recall Due, Prerequisites Not Ready, Billing Backlog and Inventory Health.' },
      { icon: RefreshCw, tone: 'amber', title: 'Recall Due & Sync',
        body: 'The Recall Due tile breaks patients into Overdue / Due Soon and contact status (Not Yet Contacted / Contacted), shows when recall data was last synced, and offers a "Sync Now" button to refresh from Smartsheet.' },
      { icon: ListChecks, tone: 'blue', title: 'Click to Drill Down',
        body: 'Each tile headline and its sub-rows is clickable, opening a drawer of the matching patients; each links to that pellet patient’s detail page.' },
      { icon: Download, tone: 'green', title: 'Download CSV',
        body: 'The drill-down drawer has a "Download CSV" button to export the list.' },
    ],
    tips: [
      'If the Recall Due tile says "Never synced", run Sync Now before trusting the recall counts.',
    ],
  },

  // ── Pellet Recall · /pellets/recall ────────────────────────────
  'pellet-recall': {
    title: 'Pellet Recall',
    steps: ['Sync', 'Pick a Patient', 'Call', 'Log Outcome'],
    sections: [
      { icon: RefreshCw, tone: 'plum', title: 'Refresh from Smartsheet',
        body: 'The list shows patients due or overdue for pellet re-insertion. Use "Refresh" to pull the latest recall data; the header shows when it last synced.' },
      { icon: Filter, tone: 'gray', title: 'Worklist & Search',
        body: 'Search by name, chart # or phone to find a patient. The table shows Last Insertion, Recall Due, Attempts, Last Outcome and Status for each row.' },
      { icon: Phone, tone: 'green', title: 'Open & Call',
        body: 'Click a row to open the recall detail, where you can click-to-dial the patient and review their contact history before calling.' },
      { icon: ClipboardList, tone: 'blue', title: 'Log the Outcome',
        body: 'After each call, record what happened (reached / scheduled / not ready / declined / voicemail / no answer) so the next caller sees the latest attempt.' },
    ],
    tips: [
      'A lock badge on a row means another staff member has already claimed that recall.',
      'This works like the WWE Recalls module — dial, log, and the patient moves out of the overdue bucket once re-inserted.',
    ],
  },

  // ── Recalls (WWE) · /recalls ───────────────────────────────────
  'recalls': {
    title: 'Recalls',
    steps: ['Pick a View', 'Open a Patient', 'Dial', 'Log Outcome'],
    sections: [
      { icon: BarChart3, tone: 'plum', title: 'Queue Metrics',
        body: 'The top strip counts the Active queue, Overdue ≥24mo, Calls today, Calls this week, and Suppressed patients so you can see the day’s workload.' },
      { icon: Filter, tone: 'gray', title: 'Filters & Presets',
        body: 'Filter by Status (Active queue, Completed, Suppressed, All), Recall type and Sort order, with an "Include cooldown" checkbox. Save a filter combination as a preset and star one as your default.' },
      { icon: Phone, tone: 'green', title: 'Click-to-Dial',
        body: 'Click a patient’s phone number to ring your RingCentral extension — pick up your phone and it connects you to the patient.' },
      { icon: ClipboardList, tone: 'blue', title: 'Log Call Outcome',
        body: 'Use "Update" to open the drawer, pick an outcome and add notes. The drawer also shows Well-Woman Exam history and a caller script.' },
      { icon: AlertTriangle, tone: 'red', title: 'Permanent Suppression',
        body: 'Some outcomes permanently suppress a patient (they can’t be re-added). A confirm dialog ("Confirm & Remove") protects against doing that by accident.' },
    ],
    tips: [
      'Managers can pull fresh recalls with "Import ModMed WWE report".',
      'Outcomes labeled "(permanent suppression)" or "(completes recall)" close the patient out — read the warning before confirming.',
    ],
  },

  // ── Active AR · /active-ar ─────────────────────────────────────
  'active-ar': {
    title: 'Active AR',
    steps: ['Pick a Tab', 'Filter', 'Open a Claim', 'Post / Update'],
    sections: [
      { icon: DollarSign, tone: 'plum', title: 'Summary Chips',
        body: 'Six clickable chips frame the work: Open, TF Past, TF Urgent ≤14d, TF Soon 15–30d, Mine, and Unassigned — each showing a count and dollar balance. "TF" is the payer’s timely-filing deadline.' },
      { icon: ListChecks, tone: 'blue', title: 'Workflow Tabs',
        body: 'Tabs quick-filter the worklist by stage: All, New, In Progress, Denials, Appeals, Paid and Rebilled in ModMed, each with a live count.' },
      { icon: Filter, tone: 'gray', title: 'Filters & Presets',
        body: 'Search by claim #, patient, chart # or policy #, then narrow by Assignee and Sort. "More filters" adds priority, age bucket, workflow state, payer, plan and TF status. Save combinations as presets, and switch between Table and "By DOS" views.' },
      { icon: FileText, tone: 'plum', title: 'Open a Claim',
        body: 'Click a claim row to open its detail — the EOB / claim lines, balances and history. Reassign a claim inline from the Assigned column without opening it.' },
      { icon: DollarSign, tone: 'green', title: 'Post & Update',
        body: 'From claim detail you Post Payment, Add Adjustment, Write Off, Reassign, change the workflow state and add notes. "Post Payment" up top also opens the posting screen directly.' },
    ],
    tips: [
      'Use the Actions menu to "Upload Unpaid Claims" (refresh the worklist from a ModMed export) or "Enrich from Charge Analysis".',
      'Watch the TF chips — claims past timely filing are likely uncollectible, so work the urgent ones first.',
    ],
  },

  // ── Surgery Settings · /surgery/settings ───────────────────────
  'surgery-settings': {
    title: 'Surgery Settings',
    steps: ['Tune Alerts', 'Set Steps & Schedules', 'Facilities & Types', 'Templates & Consents'],
    sections: [
      { icon: Settings, tone: 'plum', title: 'Global Configuration',
        body: 'Everything here is practice-wide setup, not a single case — changes affect every surgery. The tabs across the top are Alerts & Windows, Workflow Steps, Post-Op Schedules, Facilities & Capacity, Clearances & Devices, Surgery Types, Templates, Consent Templates, Message Templates and Google Sync.' },
      { icon: AlertTriangle, tone: 'amber', title: 'Alerts & Windows',
        body: 'Set the thresholds that drive the dashboard — overdue hours, labs / pre-op validity, scheduling horizon, office capacity and the cancellation fee. This tab also holds Alert Recipients and the Boarding-Slip Email recipients (MedStar / CRMC) with an "Automatically Email Boarding Slip" option and how many hours after the date is picked to send it.' },
      { icon: ListChecks, tone: 'blue', title: 'Workflow Steps & Post-Op',
        body: 'Workflow Steps sets the named steps and expected days for the Hospital and Office pathways — that "expected days" is what flags a case as behind. Post-Op Schedules builds the visit rules (days after surgery, Office vs Telehealth) matched to a procedure by keyword.' },
      { icon: Calendar, tone: 'plum', title: 'Facilities, Capacity & Surgery Types',
        body: 'Facilities & Capacity manages the facility list and the daily case limits / office slot times. Clearances & Devices edits clearance types, device types, assistant surgeons and the Payer ID → Insurance map. Surgery Types defines each procedure (CPTs, classification, eligible facilities and which consents attach).' },
      { icon: FileSignature, tone: 'green', title: 'Consent Templates',
        body: 'Register a BoldSign consent template and tell the system when to use it — match by CPT codes (most reliable), procedure keywords (fallback), and optionally a specific facility or insurance. Mark a template "supplemental" to add it on top of the primary matched consent.' },
      { icon: Mail, tone: 'gray', title: 'Message & Email Templates',
        body: 'The Templates tab holds procedure, email and SMS templates with editable subject / body and a preview. Message Templates manages the staff-facing message snippets, and Google Sync connects the surgery calendar.' },
    ],
    tips: [
      'Consents only auto-send on a case when a template here matches that case’s procedures — if a consent won’t send, check the CPT / keyword match.',
      'Changing expected days on Workflow Steps re-scores which cases show as behind on the dashboard.',
    ],
  },

  // ── Surgery To-do · /surgery/todo ──────────────────────────────
  'surgery-todo': {
    title: 'Scheduler To-Do',
    steps: ['Scan Action Needed', 'Open a Case', 'Work the Step', 'Clear Activity'],
    sections: [
      { icon: ListChecks, tone: 'plum', title: 'Action Needed',
        body: 'The left column lists the single next open step for every active surgery, with the patient, chart / surgery #, facility and due date. A red border + "Xd behind" means it is past its expected window; an amber border + "Needs review" means data is missing.' },
      { icon: Filter, tone: 'gray', title: 'Behind Only',
        body: 'The header counts how many are open, behind and to review. Tick "Behind only" to hide everything except cases that are past schedule.' },
      { icon: RefreshCw, tone: 'blue', title: 'Recent Activity',
        body: 'The right column is a live feed of what happened on cases — date picked, rescheduled or cancelled, consent signed or declined, document uploaded, labs reported, payment made and step-overdue alerts. A plum dot marks an item you haven’t read.' },
      { icon: ClipboardCheck, tone: 'green', title: 'Open & Mark Read',
        body: 'Click any row — in the worklist or the activity feed — to jump to that case’s Surgery Detail page; opening an activity marks it read. "Mark all read" clears the whole feed at once.' },
    ],
    tips: [
      'This is a cross-case worklist — it pulls the next step from every active surgery so nothing stalls.',
      'Work behind / needs-review rows first; they are the cases most at risk of slipping.',
    ],
  },

  // ── Pellet Settings · /pellets/settings ────────────────────────
  'pellet-settings': {
    title: 'Pellet Settings',
    steps: ['Validity & Windows', 'Portal Requirements', 'Pricing', 'Portal Text'],
    sections: [
      { icon: Settings, tone: 'plum', title: 'Global Configuration',
        body: 'These are program-wide pellet settings, not one patient. The tabs are Thresholds & Windows, Dose Types, Patient Portal, Payments and Portal Info.' },
      { icon: SlidersHorizontal, tone: 'blue', title: 'Thresholds & Windows',
        body: 'Set how long labs and a mammogram stay valid (in days), how many days past schedule a visit goes stale, and how many dose combinations to suggest and the most pellets per combo.' },
      { icon: ClipboardCheck, tone: 'amber', title: 'Patient Portal Requirements',
        body: 'Toggle whether patients must have a current mammogram, current labs and a signed consent before the insertion visit, and set the BoldSign Consent Template ID used for that consent.' },
      { icon: DollarSign, tone: 'green', title: 'Payments & Packages',
        body: 'Set the insertion price and optional monthly subscription amount, choose which payment methods patients can use (single insertion, packages, subscription), and build package discount tiers (count → percent off).' },
      { icon: FileText, tone: 'gray', title: 'Dose Types & Portal Info',
        body: 'Dose Types is the catalog of pellet dose definitions. Portal Info is the markdown text shown to patients on the portal’s Rules & Info page.' },
    ],
    tips: [
      'Mammogram / labs validity here drives the "needs mammo" and "needs labs" flags on the pellet roster.',
      'Leave the subscription amount at 0 if you don’t offer monthly billing.',
    ],
  },

  // ── LARC Settings · /larc/settings ─────────────────────────────
  'larc-settings': {
    title: 'Device Tracking Settings',
    steps: ['Set Windows', 'Edit Device Types', 'Manage Pharmacies'],
    sections: [
      { icon: Settings, tone: 'plum', title: 'Global Configuration',
        body: 'These settings apply to the whole device-tracking program. The tabs are Thresholds & Windows, Device Types and Pharmacies (Super Admins also see Practice Profile).' },
      { icon: SlidersHorizontal, tone: 'blue', title: 'Thresholds & Windows',
        body: 'Set Device Expiry Hold (days before expiry a device is pulled back to unassigned), Assignment Reallocate After (stale assignment age), Pharmacy Order SLA (target turnaround) and the Checkout Ack Window (hours a provider has to acknowledge a checkout).' },
      { icon: Boxes, tone: 'green', title: 'Device Types',
        body: 'Add, edit or retire device types (LARC and Office Procedure), set the NDC, reorder thresholds and the controlled flag. The per-device BoldSign enrollment-form templates (Nexplanon / Paragard / Bayer) are set here too — these are enrollment forms, not consents.' },
      { icon: Phone, tone: 'gray', title: 'Pharmacies',
        body: 'Maintain the pharmacy directory used for enrollment faxes — name, fax, phone, NPI, notes and active flag.' },
    ],
    tips: [
      'Reorder thresholds set here drive the Reorder Alerts on the Device Tracking overview.',
    ],
  },

  // ── Pellet Inventory · /pellets/inventory ──────────────────────
  'pellet-inventory': {
    title: 'Pellet Inventory',
    steps: ['Place Order', 'Receive Shipment', 'Track Lots', 'Transfer / Dispose'],
    sections: [
      { icon: Package, tone: 'plum', title: 'Stock by Lot & Location',
        body: 'The "Lots in Inventory" grid shows doses on hand by dose type and location (White Plains, Brandywine, Arlington), with each Qualgen lot number and its expiration. Expand a dose group to see its lots, or edit a lot inline. Filter by hormone, location or lot #, and export with Excel / Print PDF.' },
      { icon: AlertTriangle, tone: 'amber', title: 'Reorder & Expiry Alerts',
        body: 'Cards surface Reorder Alerts (doses at or below threshold with a suggested order qty) and lots Expiring Within 90 Days, so you reorder and rotate stock before you run short.' },
      { icon: Upload, tone: 'green', title: 'Orders & Receiving',
        body: 'Use "Place order" to log a Qualgen purchase and "Receive shipment" when it arrives — Ordered Pellets shows recent orders with status, ETA and cost. "Receive →" on an order opens the receive flow.' },
      { icon: RefreshCw, tone: 'blue', title: 'Transfers & Disposal',
        body: '"Transfer" moves doses between locations (tracked through chain-of-custody — awaiting pickup vs in transit) and "Dispose" logs destroyed stock. Open Counts shows any reconciliation in progress.' },
      { icon: ListChecks, tone: 'gray', title: 'Dose-Type Catalog',
        body: 'The catalog table lists each dose with on-hand, reorder threshold, order qty and notes. "Edit thresholds →" jumps to the settings that drive the reorder alerts.' },
    ],
    tips: [
      'Testosterone pellets are DEA Schedule III (SCH III badge) — every receipt, transfer and disposal is logged to the Audit tab.',
      'Watch the "Expiring Within 90 Days" card and use the oldest lots first.',
    ],
  },

  // ── Pellet Counts · /pellets/counts ────────────────────────────
  'pellet-counts': {
    title: 'Pellet Counts',
    steps: ['Start a Count', 'Pick Scope', 'Add Witness', 'Reconcile & Finish'],
    sections: [
      { icon: ScanLine, tone: 'plum', title: 'Count Workflow',
        body: 'Counts reconcile the physical pellet stock against the system. "Start count" begins one at a location; "Start all 3 locations" opens one for each. The table lists every count with its location, scope, status, who started / finished it and the witness.' },
      { icon: ShieldCheck, tone: 'amber', title: 'Scope & Witness',
        body: 'Choose "All lots" or "Sch III only" (controlled testosterone). A second-person witness email is required at start whenever any Schedule III lot is in scope — this is a DEA control.' },
      { icon: AlertTriangle, tone: 'red', title: 'Blocking Visits',
        body: 'You can’t start a count while pellet visits are still "proposed" — the drawer lists them so you can mark each "Did not happen", "Edit dose" or "Confirm as planned" first, so the snapshot is accurate.' },
      { icon: FileText, tone: 'green', title: 'Open, Finish & PDF',
        body: 'Open → a count to enter the physical numbers lot by lot and finish it; the system records any variance. Finished counts produce a PDF, and an in-progress count can be Cancelled.' },
    ],
    tips: [
      'Resolve every "proposed" visit before starting — otherwise the on-hand snapshot won’t match the shelf.',
      'The witness must be a different person than whoever starts the count.',
    ],
  },

  // ── Pellet Audit · /pellets/audit ──────────────────────────────
  'pellet-audit': {
    title: 'Pellet Audit Log',
    steps: ['Filter', 'Read an Event', 'Verify Witness'],
    sections: [
      { icon: History, tone: 'plum', title: 'Perpetual Record',
        body: 'This is the write-only history of every inventory change — receipts, transfers, disposals, count adjustments, dose-type edits and opening balances. Nothing here can be edited; it exists for DEA Schedule III (testosterone) compliance.' },
      { icon: Filter, tone: 'gray', title: 'Filters',
        body: 'Narrow the log by Action, Location, Actor (email), Lot UUID and time Window — filters combine with AND. "Clear filters" resets, and the summary shows how many events and how many were witnessed.' },
      { icon: BarChart3, tone: 'blue', title: 'Reading a Row',
        body: 'Each row shows When, Actor, Action, Location, the signed change in doses (Δ doses — green added, red removed) and a plain-language Summary. A detail line adds the witness, reason, transfer destination or count variance (expected vs counted).' },
      { icon: ShieldCheck, tone: 'amber', title: 'In / Out / Witness',
        body: 'A positive Δ means stock came in (receipt / transfer received), a negative Δ means it went out (transfer sent / disposal / count-down). A "wit" shield badge marks events that were witnessed.' },
    ],
    tips: [
      'Filter by Lot UUID to trace a single lot’s entire history end to end.',
      'This log is the source of truth if a count variance ever needs explaining.',
    ],
  },

  // ── Insurance Documents · /billing/insurance-documents ─────────
  'insurance-docs': {
    title: 'Insurance Documents',
    steps: ['Triage New', 'Assign', 'Work the Doc', 'Mark Worked'],
    sections: [
      { icon: Inbox, tone: 'plum', title: 'Document Inbox',
        body: 'This is the shared inbox for faxed and uploaded insurance documents — paper EOBs, patient payments, denials and letters. Each row shows the filename, type, page count, when it arrived, who it’s assigned to and its status.' },
      { icon: Filter, tone: 'gray', title: 'Filters & Search',
        body: 'Filter by Status (New / In progress / Worked — toggles multi-select), by Classification, and with "Assigned to me" or "Unassigned only". Search by filename, and sort by Type or Uploaded date.' },
      { icon: Upload, tone: 'green', title: 'Upload',
        body: '"+ Upload document" adds a PDF or image manually, sets its classification (or "Auto-classify with AI"), and optionally assigns it. The system warns on a possible duplicate.' },
      { icon: FileText, tone: 'blue', title: 'View & Work',
        body: 'Click a row to open the viewer (page through, zoom, rotate) with a side panel to set the classification, assign staff, add notes and change the status. Leaving it unassigned makes it visible to everyone with billing access.' },
      { icon: ListChecks, tone: 'amber', title: 'Status Flow',
        body: 'A document moves New → In progress → Worked. Use "Mark in progress" while you’re on it and "Mark as Worked" when done; the access log records who did what.' },
    ],
    tips: [
      'Assign a document to yourself so two people don’t work the same fax.',
      'Use "Unassigned only" to find documents nobody has picked up yet.',
    ],
  },

  // ── Admin · Permissions · /admin/permissions ───────────────────
  'admin-permissions': {
    title: 'Permissions',
    steps: ['Pick a Module', 'Grant a Tier', 'Use Groups', 'Open a Profile'],
    sections: [
      { icon: KeyRound, tone: 'plum', title: 'Module + Tier Access',
        body: 'Access is granted per module at a tier — View (read), Work (do the day-to-day) or Manage (configure). Pick a module up top to see every group and user who has access to it.' },
      { icon: ListChecks, tone: 'blue', title: 'Granting in the Grid',
        body: 'Each row has a clickable dot under View, Work, Manage and Admin. Click an empty dot to grant that tier, or click the active dot to clear it. Plum dots come from a group or normal override; amber dots are a per-user override.' },
      { icon: Users, tone: 'green', title: 'Groups vs Users',
        body: 'Use the Groups + Users / Groups / Users buttons to filter the list. Groups bundle several grants so you can give a whole role at once; "+ New Group" creates one. A user’s effective access is the highest tier across their groups plus any per-user override.' },
      { icon: ShieldCheck, tone: 'amber', title: 'Profiles & Super Admin',
        body: 'Click a name to open its profile drawer — for a user, manage group membership, per-module overrides and the Super Admin flag; for a group, edit its name, members and grants. Super Admin has Admin on every module and always wins.' },
    ],
    tips: [
      'The Source column tells you where access comes from — "← Group" is inherited, "Override" is per-user (click the active dot to clear it).',
      'Prefer Groups over per-user overrides so access stays consistent across a role.',
      'The system refuses to remove the last Super Admin.',
    ],
  },

  // ── Admin · Checklist Templates · /admin/templates ─────────────
  'admin-templates': {
    title: 'Checklist Templates',
    steps: ['Create a Template', 'Set the Schedule', 'Assign It', 'Add Training'],
    sections: [
      { icon: ClipboardList, tone: 'plum', title: 'Recurring Task Templates',
        body: 'A template generates a checklist task on a schedule for the right people. "+ New Template" creates one; the table lists each by title, category, schedule, due, manager and assignees, with an Active toggle.' },
      { icon: Calendar, tone: 'blue', title: 'Schedule',
        body: 'Pick a recurrence — Daily, specific weekdays or days of month, a yearly anniversary, every N days/months/years, or On demand — plus a weekend rule, due time and priority. This controls when instances are generated.' },
      { icon: Users, tone: 'green', title: 'Who Gets It',
        body: 'Assign by group, by specific users, or to "anyone with this permission". "Preview assignees" shows the computed count before you save. A Manager (escalate to) is required, with an escalate-after-hours setting.' },
      { icon: ShieldCheck, tone: 'amber', title: 'Yes/No & Training',
        body: 'A task can ask a Yes/No question with an optional follow-up ("How many?" / "Why?"). You can also require a training certification before the task is assigned, link the training material, set when the cert expires, and manage authorized trainers and certified trainees.' },
    ],
    tips: [
      'A template with no valid assignees is flagged in red — fix the Who-gets-it section so tasks actually generate.',
      'These are operational checklists; consent forms are configured under Surgery Settings, not here.',
    ],
  },
}

/**
 * Map the current pathname to a HELP_CONTENT key. Returns null when no help
 * is authored for the route (so the Help button hides itself).
 *
 * Order matters: more specific patterns first.
 */
export function helpKeyForPath(pathname) {
  if (!pathname) return null
  // Static surgery sub-routes — listed BEFORE the /surgery/<id> digit guard so
  // they can never false-match (they're non-numeric, but order it defensively).
  if (pathname === '/surgery/settings') return 'surgery-settings'
  if (pathname === '/surgery/todo') return 'surgery-todo'
  if (pathname === '/surgery/reports') return 'surgery-reports'
  // Surgery detail: /surgery/<numeric-id>. Sibling routes (settings, todo,
  // calendar, reports, etc.) are non-numeric, so the digit check below avoids
  // false matches against the static surgery sub-routes handled here.
  if (/^\/surgery\/\d+(?:\/|$)/.test(pathname)) return 'surgery-detail'
  if (pathname === '/surgery' || pathname === '/surgery/') return 'surgery-dashboard'
  // Billing layout sub-routes.
  if (pathname === '/billing/missing-charges') return 'missing-charges'
  if (pathname === '/billing/insurance-documents') return 'insurance-docs'
  // Active AR worklist (claim detail at /active-ar/:id gets its own help later).
  if (pathname === '/active-ar') return 'active-ar'
  // Recalls (WWE) index. Sub-route /recalls/settings is excluded.
  if (pathname === '/recalls' || pathname === '/recalls/') return 'recalls'
  // LARC / Device Tracking — index + reports + settings. Other sub-tabs later.
  if (pathname === '/larc/reports') return 'larc-reports'
  if (pathname === '/larc/settings') return 'larc-settings'
  if (pathname === '/larc' || pathname === '/larc/') return 'larc'
  // Pellet sub-pages (check before the /pellets index).
  if (pathname === '/pellets/reports') return 'pellet-reports'
  if (pathname === '/pellets/recall') return 'pellet-recall'
  if (pathname === '/pellets/settings') return 'pellet-settings'
  if (pathname === '/pellets/inventory') return 'pellet-inventory'
  if (pathname === '/pellets/counts') return 'pellet-counts'
  if (pathname === '/pellets/audit') return 'pellet-audit'
  // Pellets main page is the /pellets index (patient list). Other sub-tabs
  // (/pellets/activity, …) get their own help later.
  if (pathname === '/pellets' || pathname === '/pellets/patients') return 'pellets'
  // Admin console.
  if (pathname === '/admin/permissions') return 'admin-permissions'
  if (pathname === '/admin/templates') return 'admin-templates'
  return null
}
