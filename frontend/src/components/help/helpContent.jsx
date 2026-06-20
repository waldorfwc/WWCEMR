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
      'The signed provider portal links expire after 60 days.',
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
  // Surgery detail: /surgery/<numeric-id>. Sibling routes (settings, todo,
  // calendar, etc.) are non-numeric, so a digit check avoids false matches.
  if (/^\/surgery\/\d+(?:\/|$)/.test(pathname)) return 'surgery-detail'
  // Missing charges lives under the billing layout.
  if (pathname === '/billing/missing-charges') return 'missing-charges'
  // Pellets main page is the /pellets index (patient list). Sub-tabs
  // (/pellets/inventory, /pellets/recall, …) get their own help later.
  if (pathname === '/pellets' || pathname === '/pellets/patients') return 'pellets'
  return null
}
