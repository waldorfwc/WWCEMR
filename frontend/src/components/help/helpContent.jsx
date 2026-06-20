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
  ListPlus, Copy, FileSpreadsheet, Layers, CheckCircle2, BookOpen,
  CreditCard, Search, RotateCcw, Printer, Clock, Building2, Box,
  Activity, Camera, Check, X, CalendarDays, Pill, Plus, GraduationCap,
  Star, Trophy, QrCode, MessageSquareWarning, Sparkles, Zap, Database,
  Banknote, Stethoscope, Wand2, Trash2,
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
    steps: ['Start LARC Process', 'Pick the Path', 'Benefits & Enroll', 'Fax / Receive', 'Insert & Bill'],
    sections: [
      { icon: ListChecks, tone: 'plum', title: 'Workflow Buckets',
        body: 'The bucket cards group every assignment by what it needs next — Needs Benefits, Needs Enrollment, Needs Fax, Awaiting Receipt, Received — Notify, Appt Scheduled, Inserted — To Bill, plus the Office-Procedure (OP) lanes. Click a bucket to load just those patients.' },
      { icon: UserPlus, tone: 'green', title: 'Start LARC Process',
        body: 'Click "Start LARC Process" and enter the patient (MRN, DOB, name, email, cell), device type, requesting provider, and reason for request. The system then recommends using an in-stock device or sending a pharmacy enrollment form — you can accept the recommendation or pick the other path before confirming.' },
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

  // ── A/R Dashboard · /ar ────────────────────────────────────────
  'ar-dashboard': {
    title: 'A/R Dashboard',
    steps: ['Read the Stats', 'Check Aging', 'Review Payers', 'Upload PrimeSuite Report'],
    sections: [
      { icon: DollarSign, tone: 'plum', title: 'Top Stat Cards',
        body: 'Four cards summarize the practice at a glance — Total Outstanding (with open-claim count), Collection Rate, 120+ Days (oldest A/R) and Open Denials with the dollars at risk.' },
      { icon: BarChart3, tone: 'blue', title: 'Aging & By Payer',
        body: 'The A/R Aging bars break the balance into 0–30 / 31–60 / 61–90 / 91–120 / 120+ Days from the ERA database, and the Outstanding by Payer chart shows which carriers hold the most money.' },
      { icon: ListChecks, tone: 'gray', title: 'Payer Performance',
        body: 'A table of each payer’s claims, billed, paid, balance and collection % — a green / yellow / red badge flags carriers collecting below 90% so you know who to chase.' },
      { icon: AlertTriangle, tone: 'red', title: 'Alert Banners',
        body: 'Red and amber banners warn when appeal deadlines are within 30 days or already passed, or when the oldest open date of service is over a year old and may be past timely filing — "Review Denials" jumps you there.' },
      { icon: Upload, tone: 'green', title: 'Upload PrimeSuite Report',
        body: 'Use "Upload PrimeSuite Report" to drop in an A/R Aging, Charge Capture, Payment or Claim Status export (CSV or Excel). The system auto-detects the format and shows a normalized summary or a 5-row preview.' },
      { icon: RefreshCw, tone: 'gray', title: 'Refresh',
        body: 'The dashboard auto-refreshes every minute; "Refresh" up top pulls the latest A/R numbers on demand.' },
    ],
    tips: [
      'These numbers come from the ERA database plus PrimeSuite — import ERA 835 files first or the aging bars stay empty.',
      'Uploading a PrimeSuite report here only shows a summary; it does not post payments or create claims.',
    ],
  },

  // ── Claims · /claims ───────────────────────────────────────────
  'claims': {
    title: 'Claims',
    steps: ['Work Today’s Queue', 'Filter', 'Open a Claim', 'Set Follow-up'],
    sections: [
      { icon: BarChart3, tone: 'plum', title: 'Work-Queue Chips',
        body: 'Five chips frame the day’s work — Open, Overdue (follow-up date past), Due today, No F/U set, and 90+ days old — each with a count and balance. Click Overdue or 90+ to load just those claims.' },
      { icon: ListChecks, tone: 'blue', title: 'Today’s Queue',
        body: '"Today’s Queue" applies the Overdue preset so you start on the claims whose follow-up date has already passed. The header shows how many claims are in the current view.' },
      { icon: Filter, tone: 'gray', title: 'Search & Filters',
        body: 'Search by claim #, member ID, patient name or chart #, then narrow by Status, age bucket and payer. The All / Open / F/U queue / Overdue buttons switch the workflow slice; "Clear" resets everything.' },
      { icon: FileText, tone: 'plum', title: 'The Claims Table',
        body: 'Each row shows the claim #, priority (P / S / T for primary / secondary / tertiary), patient, DOS, age, payer, billed / paid / balance, status, last submission and follow-up. Click a row to open that claim’s detail.' },
      { icon: Upload, tone: 'green', title: 'Import ERA 835',
        body: '"+ Import ERA 835" jumps to the Import page to load new charges and post payments — claims are created there, not added by hand here.' },
    ],
    tips: [
      'Age and follow-up dates are color-coded — red means over 90 days old or a past-due follow-up, amber means it’s coming up soon.',
      'The list pages 50 claims at a time; use the filters to narrow before paging.',
    ],
  },

  // ── Denials · /denials ─────────────────────────────────────────
  'denials': {
    title: 'Denial Management',
    steps: ['Watch Deadlines', 'Filter by Category', 'Explain a Denial', 'Generate Appeal or Write Off'],
    sections: [
      { icon: AlertTriangle, tone: 'red', title: 'Deadline Urgency',
        body: 'The header counts open denials, dollars at risk, and how many are urgent or overdue. Each row’s Deadline shows OVERDUE, a red ⚡ badge (≤14 days) or yellow (15–30 days) so the soonest appeals rise to the top.' },
      { icon: Filter, tone: 'plum', title: 'Category Cards',
        body: 'Clickable cards tally denials by reason — timely filing, authorization, medical necessity, eligibility, coding, COB and more — with a count and dollar amount. Click one to filter to that category.' },
      { icon: SlidersHorizontal, tone: 'gray', title: 'Status & Checkbox Filters',
        body: 'Filter by Status (Open, Appealing, Overturned, Upheld, Written Off) and tick "Urgent only (≤30 days)" or "Write-off recommended" to focus the table.' },
      { icon: Sparkles, tone: 'blue', title: 'Explain This Denial',
        body: 'Each row shows its denial codes (group + CARC / RARC); click a code or "Explain this denial" to open a drawer that decodes what the payer’s reason means and what to do next.' },
      { icon: Zap, tone: 'green', title: 'Generate Appeal / Write Off',
        body: 'For an appealable open denial, "Generate Appeal" drafts a letter and opens the claim. "Write Off" marks an uncollectible denial off after a confirm, and "View Claim" opens the full claim.' },
    ],
    tips: [
      'Code prefixes mean: CO = Contractual, PR = Patient Responsibility, OA = Other, PI = Payer Initiated.',
      'Maryland appeals reference MD Insurance Article §15-1005; the MIA help line is 800-492-6116 (shown in the page legend).',
    ],
  },

  // ── Appeals · /appeals ─────────────────────────────────────────
  'appeals': {
    title: 'Appeal Letters',
    steps: ['Pick a Letter', 'Review the Draft', 'Download & Mail', 'Mark Submitted'],
    sections: [
      { icon: Mail, tone: 'plum', title: 'Letter List',
        body: 'The left column lists every appeal letter with its status, appeal level, deadline and when it was created. Generate new ones from the Denials page; click any to read it.' },
      { icon: FileText, tone: 'blue', title: 'Letter Detail',
        body: 'The right panel shows the full letter body, its subject, an "AI Generated" tag when applicable, and the appeal deadline. Read it over before sending — these are drafts.' },
      { icon: Download, tone: 'green', title: 'Download',
        body: '"Download" saves the letter as a file to print and mail or fax to the payer.' },
      { icon: ClipboardCheck, tone: 'amber', title: 'Mark Submitted',
        body: 'Once a draft or ready letter is sent, "Mark Submitted" records it; the footer then shows the submitted date and any decision notes.' },
    ],
    tips: [
      'No letters here yet? Go to Denials and use "Generate Appeal" on an open, appealable denial first.',
      'Marking a letter Submitted only logs that you sent it — actually mail or fax the downloaded copy to the payer.',
    ],
  },

  // ── Import · /import ───────────────────────────────────────────
  'import-files': {
    title: 'Import',
    steps: ['Charge Analysis', 'Claims Analysis', 'ERA 835', 'Preview → Commit'],
    sections: [
      { icon: Database, tone: 'plum', title: 'Charge Analysis Import',
        body: 'Upload the PrimeSuite Charge Analysis .xls to create claims and patients from charges. Voided rows and claims already on file (by VisitID) are skipped automatically.' },
      { icon: Link2, tone: 'blue', title: 'Claims Analysis Import',
        body: 'Upload the Claims Analysis .xls to link PrimeSuite Claim IDs and set claim status, follow-up dates and filing info. Secondary / tertiary records are created as needed — re-upload any time, Claims Analysis always wins.' },
      { icon: FileText, tone: 'green', title: 'ERA 835 Payment Posting',
        body: 'Drop one or more .835 files to post payments to existing claims, matched strictly on the linked Claim ID. Reversals, unmatched claims and already-posted checks are flagged before you commit.' },
      { icon: ClipboardCheck, tone: 'amber', title: 'Preview Before Commit',
        body: 'Every upload first shows a preview — what will be created, linked, posted or skipped, plus any errors and warnings. Nothing is saved until you click Commit / Post payments, and the preview session expires after a few minutes.' },
      { icon: History, tone: 'gray', title: 'ERA File Import History',
        body: 'The bottom card lists previously imported ERA files with payer, check #, amount, claim count and whether each processed cleanly.' },
    ],
    tips: [
      'Order matters: Charge Analysis creates the claims, Claims Analysis links the Claim IDs, then ERA 835 posts payments — ERAs only match claims that already have a linked Claim ID.',
      'If the preview says the session expired, re-upload the file; the import is intentionally blocked once the timer runs out.',
    ],
  },

  // ── Billing · /billing ─────────────────────────────────────────
  'billing': {
    title: 'Billing',
    steps: ['Pick a Tab', 'Do the Work'],
    sections: [
      { icon: LayoutGrid, tone: 'plum', title: 'Billing Tabs',
        body: 'Billing is a set of tabs across the top — Bank Recon, Missing Charges, Insurance Documents, Insurance Contacts and Code Helper. Each tab has its own Help once you’re on it.' },
      { icon: Banknote, tone: 'blue', title: 'Bank Recon & Missing Charges',
        body: 'Bank Recon turns a bank CSV into a BAI2 file for ModMed. Missing Charges tracks encounters with no charge on file until a provider bills them.' },
      { icon: Inbox, tone: 'green', title: 'Documents & Contacts',
        body: 'Insurance Documents is the shared inbox for faxed EOBs and letters. Insurance Contacts keeps payer phone numbers, claims portals and notes.' },
      { icon: Stethoscope, tone: 'gray', title: 'Code Helper',
        body: 'Code Helper drafts CPT and ICD-10 codes from a clinical note with AI to speed up coding.' },
    ],
    tips: [
      'Opening Billing lands you on the Bank Recon tab by default.',
      'Each tab has its own Help button — open the tab first, then click Help for step-by-step guidance.',
    ],
  },

  // ── Bank Reconciliation · /billing/bank-recon ──────────────────
  'bank-recon': {
    title: 'Bank Reconciliation',
    steps: ['Set Bank & Filters', 'Upload CSV', 'Review Rows', 'Generate BAI2'],
    sections: [
      { icon: SlidersHorizontal, tone: 'plum', title: 'Bank Label & Skip Toggles',
        body: 'Set the Bank / Account Label (it becomes the BAI2 filename prefix), then choose what to drop — Skip withdrawals, Skip ModMed, Skip Stripe and Skip zero-amount. MERCHANT BNKCD rows are always dropped.' },
      { icon: Upload, tone: 'blue', title: 'Upload Bank CSV',
        body: '"Upload Bank CSV" reads a bank export (CSV or TXT) and opens the review screen. The label must be set first.' },
      { icon: ListChecks, tone: 'gray', title: 'Review Transactions',
        body: 'Each row has a checkbox, the reformatted BAI2 text, amount, method and a status of new / already imported / previously excluded. Use Select All, Select None or "Select Only New (default)" — already-imported and previously-excluded rows are unchecked for you.' },
      { icon: FileText, tone: 'green', title: 'Generate BAI2',
        body: 'The footer shows how many transactions and what dollar total will be included; "Generate BAI2" builds the file (auto-downloading it) for import into ModMed.' },
      { icon: History, tone: 'amber', title: 'History & Excluded',
        body: 'Generated BAI2 Files lists past runs (download or delete each, expand for skip counts). Excluded Transactions holds the sticky exclusions; a manager can "Reinstate" one so it imports again next time.' },
    ],
    tips: [
      'A row marked "already imported" was in a prior BAI file (same date, amount and last-4) — re-worded duplicates are caught too, so leave it unchecked.',
      'Unchecking a brand-new row makes it a sticky exclusion: it stays out of future files until a manager reinstates it.',
    ],
  },

  // ── Insurance Contacts · /billing/insurance-contacts ───────────
  'insurance-contacts': {
    title: 'Insurance Contacts',
    steps: ['Search', 'Add a Row', 'Fill Links & Phones', 'Save'],
    sections: [
      { icon: ListChecks, tone: 'plum', title: 'Contacts Table',
        body: 'A directory of the carriers you bill — each row holds the company, claims portal links, phone numbers and free-text notes. Claims links open in a new tab; phone numbers show inline.' },
      { icon: Search, tone: 'gray', title: 'Filter',
        body: 'The "Filter…" box narrows the list by company, notes, or any link or phone label / value as you type.' },
      { icon: UserPlus, tone: 'green', title: 'Add & Edit Rows',
        body: '"Add row" creates a new carrier inline; "Edit" opens any row for changes. Inside, "Add link" and "Add phone" stack as many portals and numbers as you need, each with its own label.' },
      { icon: Pencil, tone: 'blue', title: 'Save & Notes',
        body: 'Company name is required to save. Use the Notes box for payer ID, escalation contacts and gotchas. Only admins see Delete — and deleted rows are still kept in the history table.' },
    ],
    tips: [
      'Put the payer ID and any escalation contact in Notes so the whole team has it in one place.',
      'Deleting is admin-only and can’t be undone from the screen, so prefer editing a row over removing it.',
    ],
  },

  // ── Code Helper · /billing/code-helper ─────────────────────────
  'code-helper': {
    title: 'Code Helper',
    steps: ['Paste or Upload Note', 'Set Payer', 'Generate Codes', 'Review & Save'],
    sections: [
      { icon: FileText, tone: 'plum', title: 'Note Input',
        body: 'Choose "Paste note" to type / paste a clinical note, or "Upload PDF" to attach one. Add the Payer (Cigna, Aetna, …) so the suggestions account for that carrier’s rules.' },
      { icon: Wand2, tone: 'blue', title: 'Generate Codes',
        body: '"Generate Codes" sends the note to Claude and returns suggested CPT codes (with modifiers and position) and ICD-10 diagnoses. This is an AI assist — a coder still reviews it.' },
      { icon: ClipboardCheck, tone: 'green', title: 'CPT Justifications & Save Patient',
        body: 'Each CPT card expands to show its justification, and flags codes a payer is likely to deny with a suggested alternative. The patient strip auto-matches a chart; edit the name / DOB and "Save Patient" to link it.' },
      { icon: AlertTriangle, tone: 'amber', title: 'Denial List',
        body: '"Manage denial list" opens the payer-specific rules that drive the denial warnings on CPT cards, so you can keep the known problem codes current.' },
      { icon: History, tone: 'gray', title: 'History',
        body: 'The History table logs past requests with patient, DOB, date, payer and the CPT / ICD-10 codes produced; click a row to reopen that suggestion.' },
    ],
    tips: [
      'AI suggestions are a starting point — always have a coder verify CPTs, modifiers and ICD-10 before billing.',
      'A red "Likely denied" flag means that payer often rejects the code; check the suggested alternative before submitting.',
    ],
  },

  // ── Surgery Block Schedule · /surgery/block-schedule ───────────
  'surgery-block-schedule': {
    title: 'Block Schedule',
    steps: ['Add Recurring Schedule', 'Re-materialize', 'Add Blackouts / PTO', 'Add Surgery Day'],
    sections: [
      { icon: Calendar, tone: 'plum', title: 'The Three Tabs',
        body: 'Upcoming Days lists the actual bookable block days for the next 60 days; Recurring Schedules holds the rules that generate them; Blackouts holds the holiday / PTO dates that block scheduling.' },
      { icon: RefreshCw, tone: 'blue', title: 'Recurring Schedules',
        body: 'A recurring schedule sets which weekdays a facility (MedStar / CRMC / Office) has surgical blocks — every week, specific weeks of the month, or specific dates. "Add Recurring Schedule" creates one; deleting it stops new block days from being made.' },
      { icon: AlertTriangle, tone: 'amber', title: 'Blackouts & PTO',
        body: 'Blackouts are days surgeries can\'t be booked. Holidays auto-seed through 2031; add PTO, equipment-down or facility-closed dates yourself, office-wide or for one provider, whole-day or a partial-day window.' },
      { icon: Plus, tone: 'green', title: 'Add Surgery Day',
        body: 'On the Blackouts tab, "Add Surgery Day" marks a one-off (ad-hoc) date as bookable — use it for vacation make-ups or extra hospital days. The opposite of a blackout.' },
      { icon: RefreshCw, tone: 'gray', title: 'Re-materialize',
        body: 'Re-materialize rebuilds upcoming block days from the recurring schedules, skipping any that now fall on a blackout. Run it after changing schedules or blackouts so the calendar stays in sync.' },
    ],
    tips: [
      'Adding a blackout on a date that already has block days will remove those days when you re-materialize — confirm nothing is booked there first.',
      'Capacity is per facility — MedStar allows 3×180min or 2×240min robotic, CRMC allows 6 minors OR 2 majors per day.',
    ],
  },

  // ── Surgery Waitlist · /surgery/waitlist ───────────────────────
  'surgery-waitlist': {
    title: 'Waitlist',
    steps: ['Pick an Open Day', 'Review Matches', 'Copy Klara Blast', 'Confirm Patient Claimed'],
    sections: [
      { icon: ListPlus, tone: 'plum', title: 'Who\'s Waiting',
        body: 'This is the list of patients hoping for an earlier surgery slot. Patients are added from the "Add to waitlist" button on their Surgery Detail page, not here.' },
      { icon: Filter, tone: 'gray', title: 'Filter & Sort',
        body: 'Filter by facility (MedStar / CRMC / Office) up top, and click the Notice, Location or Urgency column headers to sort. Urgency shows as Routine, Expedited or Urgent.' },
      { icon: Calendar, tone: 'blue', title: 'Find Matches For An Open Day',
        body: 'The strip of date chips lists block days that still have open capacity. Click one to open the Waitlist Matches drawer of patients eligible for that exact slot, ranked by waiting time.' },
      { icon: Copy, tone: 'green', title: 'Klara Blast & Claim',
        body: 'The matches drawer gives a ready-to-send Klara blast you "Copy to clipboard" and paste into Klara. When a patient says yes, click "Patient claimed" to book them into that slot.' },
    ],
    tips: [
      'A "balance due" badge on a match means the patient still owes money — clear it before confirming the slot.',
      'Matches are filtered by advance notice, facility and procedure, so a day may show no eligible waitlisters even when it has open capacity.',
    ],
  },

  // ── Surgery Calendar · /surgery/calendar ───────────────────────
  'surgery-calendar': {
    title: 'Surgery Calendar',
    steps: ['Pick Month / Week', 'Open a Day', 'Book an Open Slot', 'Pick the Patient'],
    sections: [
      { icon: LayoutGrid, tone: 'plum', title: 'Month / Week Views',
        body: 'Toggle Month or Week in the top right. Month shows the whole grid with Prev / Today / Next; Week shows a 7-day strip with Prev / Next, This week and a "Jump to" date picker that snaps to Monday.' },
      { icon: Calendar, tone: 'blue', title: 'Day Designation Chips',
        body: 'Each working day wears a colored chip for its designation — MedStar, CRMC, Procedures, Office or Blocked (PTO / Holiday). Weekends and non-surgery days are dimmed; a * marks a partial block.' },
      { icon: ClipboardList, tone: 'gray', title: 'Surgery Cards',
        body: 'Each booked case shows as a card colored by facility, with a status dot (green ready, yellow open tasks, red critically behind) and 🚨 urgent / 🤖 robotic / ⚠ incomplete flags. Click a card to open its Surgery Detail.' },
      { icon: Plus, tone: 'green', title: 'Open A Day & Book',
        body: 'Click any day to open its drawer with a per-facility time grid. Click an "available" green slot to pick from eligible unscheduled surgeries and Book one straight into that time.' },
    ],
    tips: [
      'If a day says "Not allocated as a surgery day", make it bookable first via Block Schedule → Blackouts → Add Surgery Day.',
      'You can only delete a surgery day that has zero booked cases — cancel or reschedule the bookings first.',
    ],
  },

  // ── Surgery Bulk Import · /surgery/bulk-import ─────────────────
  'surgery-bulk-import': {
    title: 'Bulk Import Surgery Candidates',
    steps: ['Choose File', 'Preview (Dry-Run)', 'Review Counts', 'Import All Rows'],
    sections: [
      { icon: FileSpreadsheet, tone: 'plum', title: 'Expected Columns',
        body: 'Upload a ModMed-style patient roster .xlsx. Patient MRN is required; the rest (name, DOB, phone, address, payers, PCP) are optional. Column order, case and spacing don\'t matter, and "None" / "-" cells count as empty.' },
      { icon: Upload, tone: 'blue', title: 'Choose File',
        body: 'Click the drop zone or drag an .xlsx onto it. Each row becomes a Surgery in "incomplete" status for coordinators to triage; active surgeries for the same chart number are skipped automatically.' },
      { icon: ListChecks, tone: 'amber', title: 'Preview First',
        body: 'Always run "Preview (dry-run)" before importing — it parses the file and shows how many rows Would create, Skipped (dup) and Errored without saving anything.' },
      { icon: CheckCircle2, tone: 'green', title: 'Import All Rows',
        body: 'Once the preview looks right, "Import all rows" commits them. The result panel lists exactly which rows were created, skipped (with reason) and errored.' },
    ],
    tips: [
      'Imported cases land in "incomplete" status — open each from the dashboard to fill in chart #, DOB and procedure before working it.',
      'The "Import all rows" button stays disabled until you\'ve run a preview.',
    ],
  },

  // ── Surgery Fee Schedule · /surgery/fee-schedule ───────────────
  'surgery-fee-schedule': {
    title: 'Fee Schedule',
    steps: ['Pick a Tab', 'Add Allowed Amount', 'Add CCI / MPR Edit', 'Save'],
    sections: [
      { icon: DollarSign, tone: 'plum', title: 'Allowed Amounts',
        body: 'The Allowed Amounts tab holds the contracted allowed dollar amount per insurance + CPT. Pick an insurance, enter the CPT and amount, and Add — this feeds the allowed-amount calculator on each Surgery Detail page.' },
      { icon: Layers, tone: 'blue', title: 'CCI / MPR Edits',
        body: 'The CCI / MPR Edits tab overrides how two CPTs pay when billed together. By default the highest pays 100% and each later one 50% (MPR); add an edit to Block a pair or force both to 100%.' },
      { icon: Pencil, tone: 'gray', title: 'Edit & Delete',
        body: 'On the Allowed Amounts table use "edit" to load a row back into the form, or "delete" to remove it. CCI rows can only be deleted; default MPR applies whenever no override exists.' },
    ],
    tips: [
      'The calculator pulls the allowed amount straight from this table, so a missing insurance + CPT row means the estimate can\'t be computed.',
      'Insurance names come from the shared picklist — pick from the dropdown rather than typing, so they match exactly.',
    ],
  },

  // ── Surgery Payment Posting · /surgery/payment-posting ─────────
  'surgery-payment-posting': {
    title: 'Payment Posting',
    steps: ['Open How-To Guide', 'Copy MRN / Amount / Confirmation', 'Post in ModMed', 'Mark Posted'],
    sections: [
      { icon: CreditCard, tone: 'plum', title: 'Stripe Payments To Post',
        body: 'This lists patient Stripe payments (balance, FMLA, cancellation or no-show fees) that still need posting to ModMed. The "Not Posted" filter shows only the outstanding ones; "Posted" and "All" show the rest.' },
      { icon: BookOpen, tone: 'blue', title: 'How To Post In ModMed',
        body: 'This button opens a step-by-step guide and a Collect Payment field cheat sheet. Highlighted cheat-sheet rows (Amount, Confirmation) are values you copy straight from this tab\'s row.' },
      { icon: ClipboardCheck, tone: 'green', title: 'Mark Posted',
        body: 'After you\'ve entered the payment in ModMed, type your initials in that row and click "Mark Posted" — it stamps your initials and the time so everyone knows it\'s done.' },
      { icon: RefreshCw, tone: 'amber', title: 'Un-mark',
        body: 'Posted by mistake? Managers can "Un-mark" a row to reverse the posting stamp and send it back to the unposted list.' },
    ],
    tips: [
      'Copy the MRN and Confirmation from the row rather than retyping — it avoids posting a payment to the wrong patient.',
      'This screen only tracks that a payment was posted; the actual money moves in ModMed, not here.',
    ],
  },

  // ── Surgery Messages · /surgery/messages ───────────────────────
  'surgery-messages': {
    title: 'Messages',
    steps: ['Scan Unread', 'Open the Case', 'Reply in Messages'],
    sections: [
      { icon: Inbox, tone: 'plum', title: 'Unread Patient Messages',
        body: 'This inbox lists surgery cases with unread patient replies, newest activity first. Each row shows the patient, chart number, a preview of their last message and when it arrived.' },
      { icon: Mail, tone: 'blue', title: 'Open & Reply',
        body: 'Click a row to jump straight to that case\'s Surgery Detail Messages thread, where you read the full conversation and reply. The list auto-refreshes about once a minute.' },
    ],
    tips: [
      'A case leaves this list once its messages have been read on the Surgery Detail page.',
      'This is the surgery patient-message thread (Twilio SMS) — Klara messages are drafted and sent separately.',
    ],
  },

  // ── Surgery Deleted · /surgery/deleted ─────────────────────────
  'surgery-deleted': {
    title: 'Deleted Surgeries',
    steps: ['Search', 'Find the Case', 'Restore'],
    sections: [
      { icon: Trash2, tone: 'plum', title: 'Soft-Deleted Cases',
        body: 'Deleted surgeries are hidden from the system but not gone — they\'re kept here and recoverable. The table shows the patient, chart #, DOB, status, when it was deleted and by whom.' },
      { icon: Search, tone: 'gray', title: 'Search',
        body: 'Search by patient name, chart # or surgery # to find the case you need to bring back.' },
      { icon: RotateCcw, tone: 'green', title: 'Restore',
        body: '"Restore" returns a case to the active surgery list after a confirmation. It immediately reappears on the dashboard and worklists.' },
    ],
    tips: [
      'This page is reached from the Surgery "Add ▾ → Restore Deleted" menu and is limited to Manage-level access.',
    ],
  },

  // ── LARC Device Inventory · /larc/devices ──────────────────────
  'larc-devices': {
    title: 'Device Inventory',
    steps: ['Filter the List', 'Open a Device', 'Add / Bulk Add', 'Print Labels'],
    sections: [
      { icon: Package, tone: 'plum', title: 'Inventory Table',
        body: 'Every physical device on file, one row each — Our ID, Type, Lot #, Ownership, Location, Expires and Status. Office-Procedure devices carry an "OP" tag. Click a row to open that device’s detail.' },
      { icon: Filter, tone: 'gray', title: 'Filters',
        body: 'Narrow by Category (LARC / Office Procedure Devices), Device type, Status, Location and Ownership, plus a search box matching our_id / lot / serial. Tick "Include history (terminal statuses)" to also see returned, lost and expired devices.' },
      { icon: Plus, tone: 'green', title: 'Add Device',
        body: '"Add Device" logs one device with full detail (Our ID, type, lot, serial, expiry, purchase info, location). Use "Bulk add" to enter a whole shipment of the same type at one location, one row per Our ID.' },
      { icon: Printer, tone: 'blue', title: 'Print Labels',
        body: 'Tick the row checkboxes to select devices, then "Print N labels" opens a QR-label PDF. Bulk add offers the same label PDF for every device you just created.' },
    ],
    tips: [
      'Our ID is the WWC barcode (e.g. WWC0700) printed on the QR label — it’s what gets scanned during an inventory count.',
      'Ownership shows who paid: WWC, WWC Claimed or Patient.',
    ],
  },

  // ── LARC Pending Checkouts · /larc/checkouts ───────────────────
  'larc-checkouts': {
    title: 'Pending Checkout Approvals',
    steps: ['Review the Request', 'Approve', 'Or Enter a Reason & Deny'],
    sections: [
      { icon: ClipboardCheck, tone: 'plum', title: 'Approval Queue',
        body: 'Only checkout requests the auto-approval gates flagged land here — each card shows the patient, chart #, device Our ID / type, who requested it and when. The list refreshes on its own.' },
      { icon: Check, tone: 'green', title: 'Approve',
        body: '"Approve" releases the device to the requesting provider and clears the card from the queue.' },
      { icon: X, tone: 'red', title: 'Deny',
        body: 'To deny, type a denial reason (required) in the box, then "Deny". The reason is recorded for the provider and the audit log.' },
    ],
    tips: [
      'This is only the exceptions queue — routine checkouts that pass the gates never appear here.',
      'A denial reason is mandatory; the Deny button stays disabled until you enter one.',
    ],
  },

  // ── LARC Owed List · /larc/owed ────────────────────────────────
  'larc-owed': {
    title: 'Owed List',
    steps: ['Review Owed Patients', 'Reallocate a Fresh Device', 'Pick a Resolution', 'Resolve'],
    sections: [
      { icon: Users, tone: 'plum', title: 'Owed Patients',
        body: 'Patients whose device was pulled back (unused for 6 months, or within 365 days of expiry) and who are owed a replacement. Columns show Owed since, Expires (with days left) and Status.' },
      { icon: Clock, tone: 'amber', title: 'Expiry Countdown',
        body: 'The Expires column counts down to the original device’s expiration — amber when under 30 days, red once past. A patient has until that date to claim a fresh device.' },
      { icon: CheckCircle2, tone: 'green', title: 'Resolve',
        body: 'Click "resolve" to pick an outcome — Reallocated (giving a fresh device), Declined (no longer wants it) or Expired (too late) — add optional notes, then save.' },
      { icon: Filter, tone: 'gray', title: 'Include Resolved',
        body: 'By default only active owed patients show. Tick "Include resolved" to see the full history.' },
    ],
    tips: [
      'For a Reallocated resolution, create the new LARC request on the dashboard first (so a fresh device binds), then resolve here.',
      'Notes you add are written to the LARC audit log.',
    ],
  },

  // ── LARC Audit Log · /larc/audit ───────────────────────────────
  'larc-audit': {
    title: 'Device Audit Log',
    steps: ['Set Filters', 'Read an Event', 'Clear & Refine'],
    sections: [
      { icon: History, tone: 'plum', title: 'Change History',
        body: 'Every device, assignment and checkout state change in one feed — When, Actor, Action, the patient / device it touched and a plain-language Summary.' },
      { icon: Filter, tone: 'gray', title: 'Filters',
        body: 'Narrow by User (email contains), Device ID (GUID), Patient chart # and Action, with a "System only" toggle for automated events. Filters combine with AND; "Clear all" resets them.' },
      { icon: ListChecks, tone: 'blue', title: 'Action Types',
        body: 'The Action dropdown covers the whole lifecycle — device_added / edited, assignment_created, benefits_verified, enrollment_sent / signed, request_faxed, device_received, checkout_approved / denied, billed, device_reallocated, owed_resolved and more.' },
    ],
    tips: [
      'A "system:" actor badge marks an automated event (e.g. an SLA breach), not a staff action.',
      'Filter by Device ID to trace one device’s entire history end to end.',
    ],
  },

  // ── LARC Pharmacy Directory · /larc/pharmacies ─────────────────
  'larc-pharmacies': {
    title: 'Pharmacy Directory',
    steps: ['Review Pharmacies', 'Add Pharmacy', 'Set Fax & Insurance'],
    sections: [
      { icon: Building2, tone: 'plum', title: 'Pharmacy List',
        body: 'The pharmacies that supply patient-specific LARC orders (Mirena / Skyla / Kyleena / Paragard / Nexplanon). Each row shows Name, Fax, Phone, Accepts insurance and Notes.' },
      { icon: Plus, tone: 'green', title: 'Add Pharmacy',
        body: '"Add Pharmacy" records the name, fax, phone, address and notes. The fax number is what auto-fills when you pick this pharmacy to fax a request.' },
      { icon: ShieldCheck, tone: 'gray', title: 'Accepts Insurance',
        body: 'Enter comma-separated insurance keywords (e.g. priority partners, medicaid) so the system can suggest the right pharmacy by plan. Leave it blank and the pharmacy is treated as accepting any.' },
    ],
    tips: [
      'The fax number set here is what auto-fills on the enrollment fax — keep it current.',
    ],
  },

  // ── LARC Device Type Catalog · /larc/device-types ──────────────
  'larc-device-types': {
    title: 'Device Type Catalog',
    steps: ['Review Types', 'Add / Edit a Type', 'Set Flow & Reorder', 'Link Enrollment Form'],
    sections: [
      { icon: Box, tone: 'plum', title: 'Type Catalog',
        body: 'Every device type the practice stocks — Name, Manufacturer, Category, Flow, Cost, Reorder threshold / qty and the linked enrollment template. Retired (inactive) types show dimmed.' },
      { icon: Pencil, tone: 'green', title: 'Add / Edit Type',
        body: '"Add type" or the edit pencil opens a form for typical cost, Category (LARC vs Office procedure) and Default flow (In-stock, Pharmacy order, or Office procedure). Untick Active to retire a type.' },
      { icon: AlertTriangle, tone: 'amber', title: 'Reorder Thresholds',
        body: 'Set "Reorder ≤" and "Reorder qty" so the dashboard flags low stock — these apply to in-stock types only and are disabled for pharmacy-order types (those are ordered per patient).' },
      { icon: FileSignature, tone: 'blue', title: 'BoldSign Enrollment Template',
        body: 'Link the BoldSign enrollment-form template for this device (pick from the list or paste the template ID). These are enrollment forms, not consents — Bayer devices (Mirena / Skyla / Kyleena) can share one template GUID.' },
    ],
    tips: [
      'A device showing "not set" for its enrollment template can’t generate an enrollment form — set the BoldSign ID.',
      'Reorder thresholds set here drive the Reorder Alerts on the Device Tracking overview.',
    ],
  },

  // ── LARC End-of-Day · /larc/eod ────────────────────────────────
  'larc-eod': {
    title: 'End-Of-Day Reconciliation',
    steps: ['Pick the Date', 'Review the Stats', 'Match Against the Cabinet', 'Investigate Gaps'],
    sections: [
      { icon: Activity, tone: 'plum', title: 'Day Summary',
        body: 'Stat cards total the day’s activity — Checkouts, Approved, Denied, Pending, Inserted and Lost (with the dollar loss). Use the date arrows or "Today" to change the day.' },
      { icon: ClipboardList, tone: 'blue', title: 'Checkouts & Inserted',
        body: 'The Checkouts table lists every request with its time, patient, device, requester, approval status and outcome. The Inserted section lists devices placed today; click a patient to open the assignment.' },
      { icon: History, tone: 'gray', title: 'Outcome / Return Events',
        body: 'A timeline of outcome and return events for the day (with the action and who did it), so anything that came back or changed state is accounted for.' },
      { icon: AlertTriangle, tone: 'red', title: 'Lost Devices',
        body: 'If any device was marked lost, it appears here with its Our ID, type and loss value — reconcile these before staff leave.' },
    ],
    tips: [
      'Match this report against the physical cabinet at end of day — any device in one but not the other needs investigating before staff leave.',
      'Devices show as "awaiting" outcome until an insertion or return is recorded.',
    ],
  },

  // ── LARC Physical Inventory Count · /larc/inventory-count ──────
  'larc-inventory-count': {
    title: 'Physical Inventory Count',
    steps: ['Start a Count', 'Scan Every Device', 'Resolve Unexpected', 'Finish'],
    sections: [
      { icon: ScanLine, tone: 'plum', title: 'Start & Scan',
        body: '"Start count" snapshots the cabinet (all locations or one). Then scan or type each device’s Our ID — the live tallies show scanned, expected and missing-so-far as you go. A beep confirms each scan.' },
      { icon: Camera, tone: 'blue', title: 'Camera Scanner',
        body: '"Camera" opens the phone or laptop camera to read the QR labels hands-free; scans queue automatically. The page must be on HTTPS (or localhost) for camera access.' },
      { icon: AlertTriangle, tone: 'amber', title: 'Unexpected & Not Yet Scanned',
        body: '"Unexpected scans" flags a device that turned up but wasn’t expected at this location. "Not Yet Scanned" lists what the system still expects to find — work it down to zero.' },
      { icon: Check, tone: 'red', title: 'Finish',
        body: 'Finishing reconciles the count. Any device still in "Not Yet Scanned" gets marked LOST for the loss-tracking report — a confirm dialog shows how many before you commit.' },
    ],
    tips: [
      'Anything expected but never scanned is marked lost on finish — sweep the cabinet before finishing.',
      'An in-progress count is picked back up automatically when you return to this page.',
    ],
  },

  // ── Pellet Activity · /pellets/activity ────────────────────────
  'pellet-activity': {
    title: 'Patient Activity',
    steps: ['Watch the Feed', 'Open an Item', 'Verify Mammo / Labs', 'Mark All Read'],
    sections: [
      { icon: Activity, tone: 'plum', title: 'Live Activity Feed',
        body: 'A running list of what pellet patients did on the portal — mammogram uploaded, labs self-reported, consent sent / signed, payment made and booked. The feed refreshes on its own about once a minute.' },
      { icon: CheckCircle2, tone: 'green', title: 'Verify Uploads',
        body: 'Mammogram and lab uploads show a "Verify" button — click it once you’ve confirmed the document is real and current, and the row flips to "Verified". Only staff with Work access can verify.' },
      { icon: ListChecks, tone: 'amber', title: 'Unread Markers',
        body: 'New rows have an amber left border and a dot until they’re seen. "Mark All Read" clears the unread markers across the whole feed at once.' },
    ],
    tips: [
      'Verifying a mammo or labs item here is what clears that patient’s "needs mammo / needs labs" flag on the roster.',
      'Each row shows the patient name and chart number so you know whose action it was.',
    ],
  },

  // ── Pellet Dose Types · /pellets/dose-types ────────────────────
  'pellet-dose-types': {
    title: 'Dose Type Catalog',
    steps: ['Add a Dose Type', 'Set Reorder Thresholds', 'Set Order Qty & Pack Sizes', 'Save'],
    sections: [
      { icon: Pill, tone: 'plum', title: 'Dose Catalog',
        body: 'This is the master list of pellet dose definitions, each with its hormone and strength (e.g. Estradiol 12.5mg). The table shows Dose, Schedule, Reorder ≤ packs, Order qty packs, Pack sizes and Notes.' },
      { icon: UserPlus, tone: 'green', title: 'Add Dose Type',
        body: '"Add Dose Type" opens a drawer to pick the hormone (Estradiol or Testosterone) and dose in mg; the label auto-generates if you leave it blank. Set the reorder threshold, order quantity, pack sizes and typical cost.' },
      { icon: SlidersHorizontal, tone: 'blue', title: 'Reorder Thresholds',
        body: 'Edit a row to set the global "Reorder ≤ packs" and order quantity, or tick "Use per-location thresholds" to set separate White Plains, Brandywine and Arlington levels. These drive the Reorder Alert panel on the inventory dashboard.' },
      { icon: ShieldCheck, tone: 'amber', title: 'Controlled (Sch III)',
        body: 'Any Testosterone dose is automatically flagged DEA Schedule III with a "Sch III" badge — every dispense and disposal of it is witnessed. Uncheck "Active" on a dose to hide it from forms without deleting it.' },
    ],
    tips: [
      'Pack sizes are entered comma-separated (e.g. 6, 12, 30) — they’re the bundle sizes Qualgen ships.',
      'Leave the per-location boxes blank for any office you don’t want reorder alerts for.',
    ],
  },

  // ── Pellet Scheduling · /pellets/schedule ──────────────────────
  'pellet-schedule': {
    title: 'Scheduling',
    steps: ['Add Availability', 'Set Recurrence & Times', 'Re-materialize Slots', 'Add One-Off Slots'],
    sections: [
      { icon: CalendarDays, tone: 'plum', title: 'Availability Templates',
        body: 'Recurring windows that generate bookable insertion slots for patients. The table lists each by Location, Recurrence, Time Window, Slot Length and Provider; "Delete" removes an active one.' },
      { icon: Plus, tone: 'green', title: 'New Availability',
        body: 'Pick a Location, a Recurrence (Daily, Weekly, Weekly Nth-in-month, Monthly day-of-month, or Specific Dates), the start / end times, slot length in minutes and an optional provider, then "Add Availability".' },
      { icon: RefreshCw, tone: 'blue', title: 'Re-materialize Slots',
        body: '"Re-materialize Slots" regenerates the actual bookable slots from the templates across the booking horizon, and reports how many new slots it created.' },
      { icon: CalendarDays, tone: 'gray', title: 'One-Off Slots',
        body: '"Add One-Off Slot" creates a single ad-hoc slot for one date that isn’t part of any recurring template — useful for a one-time opening.' },
    ],
    tips: [
      'Editing or adding a template doesn’t change existing slots until you "Re-materialize Slots".',
      'Locations are White Plains, Brandywine and Arlington — set a separate template per office.',
    ],
  },

  // ── Training Matrix · /training ────────────────────────────────
  'training': {
    title: 'Training Matrix',
    steps: ['Find a Task', 'Read the Grid', 'Click a Cell', 'Certify or Authorize'],
    sections: [
      { icon: GraduationCap, tone: 'plum', title: 'Task × Employee Grid',
        body: 'Each row is a training-gated task (grouped by category) and each column is an employee. The colored cell shows their certification status, and the header tallies how many tasks each person is certified on.' },
      { icon: ListChecks, tone: 'blue', title: 'Reading the Cells',
        body: 'The legend explains the colors — Active cert, Expiring soon (≤30 days), Trainer signed / awaiting trainee, Disputed, Expired / revoked and No cert. A shield marks an authorized trainer for that task.' },
      { icon: ClipboardCheck, tone: 'green', title: 'Click a Cell to Act',
        body: 'Click any cell to open a drawer where a manager can authorize that person as a trainer, mark them as trained (certify), or revoke a cert. After a trainer signs, the trainee must acknowledge before the cert goes active.' },
      { icon: GraduationCap, tone: 'gray', title: 'Card View & Templates',
        body: '"Card view" switches to one card per task — better for authorizing trainers and bulk-certifying whole groups. "Checklist Templates" opens where the underlying tasks (and their required training) are configured.' },
    ],
    tips: [
      'Tasks only generate for employees who hold an active certification — gaps in this grid mean those tasks won’t be assigned.',
      'Use the "Filter tasks…" box to jump to a task by title or category.',
    ],
  },

  // ── Training — Per-Task View · /training/cards ─────────────────
  'training-cards': {
    title: 'Training — Per-Task View',
    steps: ['Scan Coverage', 'Open a Task', 'Add Trainers', 'Certify Employees'],
    sections: [
      { icon: BarChart3, tone: 'plum', title: 'Coverage Banner',
        body: 'The top strip counts Tasks total, Fully covered, Has gaps and Expiring ≤30d. Click "Has gaps" or "Expiring ≤30d" to filter the cards to just those, and "Filter tasks…" searches by title.' },
      { icon: ShieldCheck, tone: 'blue', title: 'Trainers & Certified',
        body: 'Each task card lists its authorized Trainers and Certified employees as chips, plus any Pending (awaiting trainee confirm or trainer signoff). The "X missing" count expands to show everyone not yet certified.' },
      { icon: UserPlus, tone: 'green', title: 'Add to a Task',
        body: 'At the bottom of each card, pick "one employee" to "+ Trainer" or "+ Certify" them, or "whole group" to "+ Certify whole group" at once (already-certified members are skipped). "Revoke group" un-certifies everyone, for when an SOP changes.' },
      { icon: ListChecks, tone: 'amber', title: 'Missing Drill-In',
        body: 'Click the "X missing" link on a card to list everyone not certified — click any of those emails to certify that person on the spot.' },
    ],
    tips: [
      'Use "+ Certify whole group" after an all-staff training session, then "Revoke group" + re-certify when the procedure changes.',
      '"Edit Task" and "Checklist Templates" links open the underlying template in a new tab.',
    ],
  },

  // ── Reviews · /marketing ───────────────────────────────────────
  'marketing': {
    title: 'Reviews',
    steps: ['Read New Reviews', 'Check Consent', 'Approve for Website'],
    sections: [
      { icon: Star, tone: 'plum', title: 'Patient Reviews',
        body: 'Reviews patients leave through the portal land here for moderation. Each card shows the star rating, who it’s for, the comment, and (privately) the patient’s name, chart # and phone.' },
      { icon: ClipboardCheck, tone: 'green', title: 'Show on Website',
        body: 'When a patient consented to display, tick "Show on website" to approve that review for the public site embed. Reviews without display consent can’t be shown.' },
      { icon: LayoutGrid, tone: 'gray', title: 'Marketing Tabs',
        body: 'The top tabs are Reviews (here), Leaderboard (staff scoreboard) and Profiles (per-employee QR codes). Together they run the online-reputation program.' },
    ],
    tips: [
      'Chart # and phone are shown to you for follow-up only — they are never published with the review.',
      'A "→ Google share clicked" note means the patient was sent on to leave a Google review too.',
    ],
  },

  // ── Leaderboard · /marketing/leaderboard ───────────────────────
  'marketing-leaderboard': {
    title: 'Leaderboard',
    steps: ['Open the Leaderboard', 'Read the Ranking', 'Compare Columns'],
    sections: [
      { icon: Trophy, tone: 'plum', title: 'Staff Ranking',
        body: 'Employees are ranked by total Points earned from the reputation program. The top spot gets a trophy; the rank number runs down from there. The list refreshes on its own about once a minute.' },
      { icon: BarChart3, tone: 'blue', title: 'Score Columns',
        body: 'Each row breaks the score into Scans (QR scans), Reviews (count left), 5-star reviews and Google shares, with the combined Points on the right.' },
    ],
    tips: [
      'Points come from patients scanning an employee’s QR code and leaving a review — generate codes on the Profiles tab.',
      'Deactivated employees still show but are dimmed.',
    ],
  },

  // ── Reputation Profiles · /marketing/profiles ──────────────────
  'marketing-profiles': {
    title: 'Reputation Profiles',
    steps: ['Add an Employee', 'Set Location', 'Show / Print QR', 'Rotate if Leaked'],
    sections: [
      { icon: Users, tone: 'plum', title: 'Employee Profiles',
        body: 'One profile per staff member drives their personal review QR code. "+ New employee" creates one with a Display name, Role and Location; each row shows its current QR token.' },
      { icon: QrCode, tone: 'green', title: 'QR Code',
        body: '"QR code" opens that employee’s code to Download or Print for a badge or card. Patients scan it to leave a review tied to that person.' },
      { icon: RefreshCw, tone: 'amber', title: 'Rotate Token',
        body: '"Rotate token" issues a fresh QR and immediately invalidates the old one — use it if a printed code is lost or compromised. "Deactivate" / "Reactivate" turns a profile on or off.' },
    ],
    tips: [
      'The Location you set decides which office’s Google review URL a 5-star reviewer is sent to.',
      'Rotating a token breaks every already-printed QR code for that employee — only do it when you mean to.',
    ],
  },

  // ── Patient Charts / Documents · /documents ────────────────────
  'documents': {
    title: 'Patient Charts',
    steps: ['Find a Patient', 'Open the Chart', 'Send to Provider', 'Confirm the Fax'],
    sections: [
      { icon: Users, tone: 'plum', title: 'Patient List',
        body: 'The left pane lists every patient with documents on file — search by name, chart # or DOB. Each row shows the chart number, DOB and document count; a green or plum "✓" chip means a chart was faxed (green = today).' },
      { icon: FileText, tone: 'blue', title: 'Open a Chart',
        body: 'Click a patient to open their chart at /chart/<number>, where you page through the indexed documents and send selected pages out by fax.' },
      { icon: Inbox, tone: 'gray', title: 'Recent Faxes',
        body: 'The right pane is the Recent Faxes log — each row shows when it sent, the patient, DOB, chart, how many docs, the doc types, destination fax, status and who sent it.' },
      { icon: Filter, tone: 'amber', title: 'Status & Window Filters',
        body: 'Filter the fax log by status (All status, Queued, Sent, Delivered, Failed) and by time window (Last 7 / 30 / 90 days). The list auto-refreshes while any fax is still Queued or Sent.' },
      { icon: RefreshCw, tone: 'green', title: 'Retry a Fax',
        body: 'A failed fax shows a retry action on its status chip — click it to resend without rebuilding the document set.' },
    ],
    tips: [
      'The patient list only shows charts that already have indexed documents — the header counts total documents and patients on file.',
      'A "Failed" fax is the one to act on; use the retry on its status chip rather than starting over from the chart.',
    ],
  },

  // ── Patients (directory) · /patients ───────────────────────────
  'patients': {
    title: 'Patients',
    steps: ['Search', 'Scan the Row', 'Open the Patient', 'View Ledger'],
    sections: [
      { icon: Filter, tone: 'plum', title: 'Search',
        body: 'Use the search box to find a patient by name, MRN or insurance ID. Results page 50 at a time and the count up top reflects the whole directory.' },
      { icon: Users, tone: 'blue', title: 'Directory Columns',
        body: 'Each row shows Patient, MRN, DOB, Primary Insurance, Member ID and Secondary — a quick read of who the patient is and how they’re covered.' },
      { icon: FileText, tone: 'green', title: 'Open a Patient',
        body: 'Click any row — or the "View Ledger" action — to open that patient’s detail and billing ledger.' },
    ],
    tips: [
      'Patient records are created automatically when an ERA file is imported — there’s no "add patient" button here.',
      'Dates show as MM/DD/YYYY; search matches name, MRN or insurance ID.',
    ],
  },

  // ── My Checklist · /checklist ──────────────────────────────────
  'checklist': {
    title: 'My Checklist',
    steps: ['Review Today’s Tasks', 'Answer Yes / No', 'Log a Pain Point', 'Track My Tasks'],
    sections: [
      { icon: ClipboardCheck, tone: 'plum', title: 'Today’s Tasks',
        body: 'Your daily checklist is generated from your practice role — answer each task Yes (done) or No, or Skip it with a reason. A No may ask a quick follow-up ("How many?" / "Why?"). The progress bar tracks done / skipped / remaining.' },
      { icon: ClipboardList, tone: 'blue', title: 'My Tasks',
        body: 'The My Tasks card is your personal to-do list — "New task" adds one with a priority, due date, assignees and collaborators, and you can break a task into subtasks. Click the status dot to move a task New → In Progress → Closed.' },
      { icon: MessageSquareWarning, tone: 'amber', title: 'Pain Points',
        body: 'At the end of the day flag anything that got in your way for your manager. Choose "Yes — there’s something" and write a brief note; your manager responds and you acknowledge it back.' },
      { icon: Boxes, tone: 'green', title: 'LARC Checkout & Scheduler Alerts',
        body: 'If you have LARC or Surgery access, extra cards appear up top — "LARC checkout" lets you check out a ready device by its label ID, and an under-booked office-day alert links to the block schedule.' },
      { icon: BookOpen, tone: 'gray', title: 'Responsibilities & Training',
        body: '"My Job Responsibilities" opens your assigned tasks and training status (with a Print PDF). "Documentation & Training" opens the training site in a new tab.' },
    ],
    tips: [
      'No practice role means no checklist — ask your administrator to assign one if today’s list is empty.',
      'You can Reopen a task you answered by mistake — the Reopen link replaces the action buttons once a task is final.',
    ],
  },

  // ── Manager Dashboard · /manager-dashboard ─────────────────────
  'manager-dashboard': {
    title: 'Manager Dashboard',
    steps: ['Set the Window', 'Read the Tiles', 'Work Each Section', 'Run Escalations'],
    sections: [
      { icon: LayoutGrid, tone: 'plum', title: 'Accountability Tiles',
        body: 'Four tiles total the work across your direct reports — No-answers, Overdue / unanswered, Open pain points and Unassigned templates. Use the time-window selector (last 24h / 7 / 14 / 30 days) to set the lookback.' },
      { icon: ListChecks, tone: 'blue', title: 'No-Answers & Overdue',
        body: 'The No-answers section lists tasks someone answered No, with their follow-up count or reason. The Overdue / unanswered section shows tasks past the escalation window, how many hours late, and whether a notification went out.' },
      { icon: MessageSquareWarning, tone: 'amber', title: 'Open Pain Points',
        body: 'Issues your reports flagged at the end of their checklist. Add an optional response, then Acknowledge or Resolve each one.' },
      { icon: AlertTriangle, tone: 'red', title: 'Unassigned Templates',
        body: 'Templates with zero matching users today generate no tasks at all — each row links to "Fix" on the Templates admin so you can add an assignee.' },
      { icon: RefreshCw, tone: 'green', title: 'Run Escalations',
        body: '"Run escalations" sweeps now instead of waiting for the scheduled run — it reports how many managers were notified and how many tasks were flagged.' },
    ],
    tips: [
      'If "No direct reports yet" shows, this user isn’t set as the escalate-to owner on any checklist template.',
      'Unassigned templates in red are the most urgent fix — until they have an assignee, those tasks never appear for anyone.',
    ],
  },

  // ── HIPAA Audit Log · /audit ───────────────────────────────────
  'audit-log': {
    title: 'HIPAA Audit Log',
    steps: ['Filter', 'Read an Event', 'Check the Status'],
    sections: [
      { icon: ShieldCheck, tone: 'plum', title: 'PHI Access Record',
        body: 'This is the write-only log of every access to and change of Protected Health Information — it satisfies the HIPAA Security Rule audit-controls requirement. The header counts total events on file.' },
      { icon: Filter, tone: 'gray', title: 'Filters',
        body: 'Narrow by Action (View, Create, Update, Delete, Export, Import, Generate EOB, Generate Appeal) and by Resource (patient, claim, denial, appeal, era file, ledger, file). Results page 100 at a time.' },
      { icon: History, tone: 'blue', title: 'Reading a Row',
        body: 'Each row shows the Timestamp, a colored Action badge, the Resource and its ID, the Patient ID, the User who acted, a plain-language Description and the Status.' },
      { icon: AlertTriangle, tone: 'amber', title: 'Success vs Failure',
        body: 'The Status column is green for "success" and red for a failed attempt — failed entries are still logged so denied or errored access leaves a trail.' },
    ],
    tips: [
      'Nothing in this log can be edited or deleted — it’s the system of record for compliance.',
      'Resource and Patient IDs are shown truncated to the first 8 characters; the User column falls back to "system" for automated actions.',
    ],
  },

  // ── Admin Console (Users) · /admin ─────────────────────────────
  'admin': {
    title: 'Admin Console',
    steps: ['Add a User', 'Set Groups', 'Map RingCentral / Clinician', 'Generate Tasks'],
    sections: [
      { icon: Settings, tone: 'plum', title: 'Console Tabs',
        body: 'The Admin console is super-admin only and opens on User Management. The tabs across the top are Users (here), Permissions and Templates — reached from the username menu, not the sidebar.' },
      { icon: Users, tone: 'blue', title: 'User Management',
        body: '"Add User" creates a user by email; each row edits the display name inline and the Groups cell assigns role groups. "Permissions" on a row jumps to that user’s access grid. The Trash icon hard-deletes a user (type the email to confirm).' },
      { icon: Phone, tone: 'gray', title: 'RingCentral & Clinician',
        body: 'The RingCentral column shows each user’s extension and editable callback number; "Sync RC" pulls them from RingCentral and "manual override" exempts a user from the email auto-match. The Clinician / NPI column sets provider / APP, NPI and credential.' },
      { icon: ClipboardCheck, tone: 'green', title: 'Checklist Tools',
        body: 'Daily task instances spawn at 12:05 AM automatically; "Generate today’s tasks" creates them immediately for every user with active templates and reports how many were made.' },
    ],
    tips: [
      'Deleting a user is permanent — the audit log and past task instances keep the email as a string reference only.',
      'Use the Groups cell to grant a whole role at once; fine-grained access lives on the Permissions tab.',
    ],
  },

  // ── Recall Settings (WWE) · /recalls/settings ──────────────────
  'recall-settings': {
    title: 'Recall Settings',
    steps: ['Set Thresholds', 'Edit Outcomes', 'Save Changes'],
    sections: [
      { icon: Settings, tone: 'plum', title: 'Configuration Tabs',
        body: 'These are program-wide WWE recall settings, not one patient. The two tabs are Thresholds & Windows and Outcomes; "Save Changes" persists each tab.' },
      { icon: SlidersHorizontal, tone: 'blue', title: 'Thresholds & Windows',
        body: 'Set the Soft-Claim Lock (Minutes) — how long an opened recall stays locked to one caller before others can pick it up — and the Overdue Window (Months) used for the overdue-recalls metric.' },
      { icon: ListChecks, tone: 'amber', title: 'Outcomes',
        body: 'Build the call-outcome dropdown callers see. Each outcome has a Label and a category — permanent (suppresses the patient), cooldown (with a cooldown-days value), completed, or neutral. "+ Add Outcome" adds a row; Remove deletes one.' },
    ],
    tips: [
      'A "permanent" outcome permanently suppresses the patient from recalls — use it sparingly and add a reason code.',
      'Outcomes edited here immediately change the dropdown on the Recalls worklist after you Save Changes.',
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
  // ── Batch 4: remaining pages (all exact-match) ─────────────────
  // Billing & A/R
  if (pathname === '/ar') return 'ar-dashboard'
  if (pathname === '/claims') return 'claims'
  if (pathname === '/denials') return 'denials'
  if (pathname === '/appeals') return 'appeals'
  if (pathname === '/import') return 'import-files'
  if (pathname === '/billing/bank-recon') return 'bank-recon'
  if (pathname === '/billing/insurance-contacts') return 'insurance-contacts'
  if (pathname === '/billing/code-helper') return 'code-helper'
  if (pathname === '/billing' || pathname === '/billing/') return 'billing'
  // Surgery sub-pages
  if (pathname === '/surgery/block-schedule') return 'surgery-block-schedule'
  if (pathname === '/surgery/waitlist') return 'surgery-waitlist'
  if (pathname === '/surgery/calendar') return 'surgery-calendar'
  if (pathname === '/surgery/bulk-import') return 'surgery-bulk-import'
  if (pathname === '/surgery/fee-schedule') return 'surgery-fee-schedule'
  if (pathname === '/surgery/payment-posting') return 'surgery-payment-posting'
  if (pathname === '/surgery/messages') return 'surgery-messages'
  if (pathname === '/surgery/deleted') return 'surgery-deleted'
  // LARC sub-pages
  if (pathname === '/larc/devices') return 'larc-devices'
  if (pathname === '/larc/checkouts') return 'larc-checkouts'
  if (pathname === '/larc/owed') return 'larc-owed'
  if (pathname === '/larc/audit') return 'larc-audit'
  if (pathname === '/larc/pharmacies') return 'larc-pharmacies'
  if (pathname === '/larc/device-types') return 'larc-device-types'
  if (pathname === '/larc/eod') return 'larc-eod'
  if (pathname === '/larc/inventory-count') return 'larc-inventory-count'
  // Pellet sub-pages
  if (pathname === '/pellets/activity') return 'pellet-activity'
  if (pathname === '/pellets/dose-types') return 'pellet-dose-types'
  if (pathname === '/pellets/schedule') return 'pellet-schedule'
  // Training / Marketing
  if (pathname === '/training' || pathname === '/training/') return 'training'
  if (pathname === '/training/cards') return 'training-cards'
  if (pathname === '/marketing' || pathname === '/marketing/') return 'marketing'
  if (pathname === '/marketing/leaderboard') return 'marketing-leaderboard'
  if (pathname === '/marketing/profiles') return 'marketing-profiles'
  // Charts / Checklist / Admin / misc
  if (pathname === '/documents') return 'documents'
  if (pathname === '/patients') return 'patients'
  if (pathname === '/checklist') return 'checklist'
  if (pathname === '/manager-dashboard') return 'manager-dashboard'
  if (pathname === '/audit') return 'audit-log'
  if (pathname === '/recalls/settings') return 'recall-settings'
  if (pathname === '/admin' || pathname === '/admin/') return 'admin'
  return null
}
