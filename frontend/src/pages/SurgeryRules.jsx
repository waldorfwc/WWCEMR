import { Link } from 'react-router-dom'
import {
  ArrowLeft, BookOpen, Calendar, Hospital, Building2, Phone,
  AlertTriangle, FileText, Clock, Users, ShieldCheck, MessageSquare,
  CheckCircle2, ChevronRight,
} from 'lucide-react'


/* Reference document for surgery schedulers.
   Audience: a working scheduler looking up a rule, OR a new trainee learning the workflow.
   Source of truth: the rules encoded in the backend matcher / capacity / block schedule
   modules. Update this page when those rules change. */


function Section({ id, icon: Icon, title, children }) {
  return (
    <section id={id} className="card mb-6 scroll-mt-24">
      <h2 className="font-serif text-[18px] font-semibold text-ink mb-3 flex items-center gap-2">
        {Icon && <Icon size={18} className="text-plum-700" />}
        {title}
      </h2>
      <div className="prose-sm max-w-none text-[13px] leading-relaxed text-gray-800 space-y-3">
        {children}
      </div>
    </section>
  )
}


function Rule({ children }) {
  return (
    <div className="border-l-4 border-plum-300 bg-plum-50/40 px-3 py-2 rounded-r">
      {children}
    </div>
  )
}


function Warning({ children }) {
  return (
    <div className="border-l-4 border-amber-400 bg-amber-50 px-3 py-2 rounded-r flex gap-2 items-start">
      <AlertTriangle size={14} className="text-amber-700 mt-0.5 shrink-0" />
      <div>{children}</div>
    </div>
  )
}


export default function SurgeryRules() {
  return (
    <div className="max-w-4xl mx-auto">
      <div className="mb-4 flex items-baseline justify-between">
        <div className="flex items-center gap-3">
          <Link to="/surgery" className="text-muted hover:text-plum-700">
            <ArrowLeft size={18} />
          </Link>
          <div>
            <h1 className="font-serif text-[24px] font-semibold text-ink m-0 flex items-center gap-2">
              <BookOpen size={22} className="text-plum-700" />
              Surgery Scheduling Reference
            </h1>
            <div className="text-muted text-[12px] mt-0.5">
              Working rules for the WWC surgery scheduling workflow. Use as a lookup —
              every rule below is enforced (or surfaced) by the system.
            </div>
          </div>
        </div>
      </div>

      {/* Table of contents */}
      <div className="card mb-6 !p-3 bg-plum-50/30">
        <div className="text-[10px] uppercase tracking-wide text-gray-500 mb-2">Jump to</div>
        <div className="grid grid-cols-2 md:grid-cols-3 gap-x-4 gap-y-1 text-[12px]">
          {[
            ['#workflow',  'Workflow overview'],
            ['#facilities','Facilities & rooms'],
            ['#blocks',    'Block schedule'],
            ['#capacity',  'Capacity rules'],
            ['#milestones','Milestones'],
            ['#buckets',   'Workload buckets'],
            ['#preop',     'Pre-op (180-day rule)'],
            ['#consent',   'Consents & DocuSign'],
            ['#sterilization','Medicaid sterilization'],
            ['#klara',     'Klara messaging'],
            ['#release',   'Hospital release'],
            ['#waitlist',  'Waitlist'],
            ['#cancellation','Cancellation'],
            ['#patient_picker','Patient date picker'],
            ['#dates',     'Date / holiday rules'],
            ['#contacts',  'Key contacts'],
          ].map(([href, label]) => (
            <a key={href} href={href} className="text-plum-700 hover:underline flex items-center gap-1">
              <ChevronRight size={11} /> {label}
            </a>
          ))}
        </div>
      </div>

      {/* WORKFLOW */}
      <Section id="workflow" icon={CheckCircle2} title="Workflow overview">
        <p>Each surgery moves through a fixed sequence of <strong>milestones</strong>. The
          dashboard surfaces each surgery into one or more <strong>workload buckets</strong>
          based on which milestones are pending. Work the buckets, not the list.</p>
        <ol className="list-decimal pl-5 space-y-1">
          <li><strong>Order received</strong> — PDF order uploaded, patient/procedure parsed.</li>
          <li><strong>Benefits determined</strong> — verify insurance, run estimate.</li>
          <li><strong>Prior auth</strong> — submit if required by payer.</li>
          <li><strong>Scheduling message sent</strong> — Klara to patient with date options.</li>
          <li><strong>Patient picks date</strong> — via the patient-facing picker (DOB + last 4 of phone).</li>
          <li><strong>Consent</strong> — DocuSign envelope(s) sent and signed.</li>
          <li><strong>Clearance / labs</strong> — only if procedure or comorbidity requires.</li>
          <li><strong>Posted to hospital</strong> — boarding slip generated and faxed.</li>
          <li><strong>Surgery day</strong> — case completed.</li>
          <li><strong>Post-op</strong> — F/U appt scheduled, post-op call, op note + path uploaded, billing done.</li>
        </ol>
      </Section>

      {/* FACILITIES */}
      <Section id="facilities" icon={Hospital} title="Facilities & rooms">
        <p>Three facilities. The patient picks among the ones their surgery is eligible for
          (set on the Surgery's <code>eligible_facilities</code> list).</p>
        <ul className="list-disc pl-5 space-y-1.5">
          <li><strong>MedStar Southern Maryland</strong> (hospital) — robotic + major minimally-invasive cases.
            Block hours typically 7:30 AM – 3:30 PM.</li>
          <li><strong>CRMC (UM Charles Regional)</strong> (hospital) — minor outpatient OR major open cases.
            Block hours typically 7:30 AM – 12:30 PM (short day) or longer when scheduled.</li>
          <li><strong>WWC Office Procedure Suite</strong> — every Thursday. In-office procedures only.</li>
        </ul>
      </Section>

      {/* BLOCKS */}
      <Section id="blocks" icon={Calendar} title="Block schedule (5-week pattern)">
        <p>Block days repeat on a 5-week pattern, materialized 180 days into the future.</p>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mt-2">
          <div className="border border-plum-100 rounded p-3 bg-plum-50/30">
            <div className="font-semibold text-plum-800">MedStar Robotic</div>
            <div className="text-[12px] mt-1">
              <div>2nd, 4th, 5th <strong>Mondays</strong></div>
              <div>1st, 3rd <strong>Wednesdays</strong></div>
            </div>
          </div>
          <div className="border border-plum-100 rounded p-3 bg-plum-50/30">
            <div className="font-semibold text-plum-800">CRMC</div>
            <div className="text-[12px] mt-1">
              <div>1st <strong>Monday</strong> (short day)</div>
              <div>2nd, 4th <strong>Tuesdays</strong></div>
            </div>
          </div>
          <div className="border border-plum-100 rounded p-3 bg-plum-50/30">
            <div className="font-semibold text-plum-800">Office</div>
            <div className="text-[12px] mt-1">
              Every <strong>Thursday</strong>
            </div>
          </div>
        </div>

        <p className="mt-3 text-[12px] text-gray-600">
          Holidays roll the affected block off, then the 5-week cycle continues
          on the next eligible weekday.
        </p>
      </Section>

      {/* CAPACITY */}
      <Section id="capacity" icon={Clock} title="Capacity rules per block">
        <Rule>
          <div className="font-semibold mb-1">MedStar (robotic block)</div>
          <ul className="list-disc pl-5 text-[12px]">
            <li><strong>3 × 180 min robotic</strong> OR <strong>2 × 240 min robotic</strong> per day (mutually exclusive).</li>
            <li>After 2 robotic cases, minor add-ons may be appended if time remains.</li>
            <li>180-min and 240-min robotic cases <strong>cannot be mixed</strong> in the same day.</li>
          </ul>
        </Rule>
        <Rule>
          <div className="font-semibold mb-1">CRMC</div>
          <ul className="list-disc pl-5 text-[12px]">
            <li><strong>6 minor</strong> OR <strong>2 major</strong> per day (mutually exclusive).</li>
            <li>Minor durations: 90 min default. Major: 180 min default.</li>
            <li>Short days (1st Monday) cap at the room's actual hours — system checks before slotting.</li>
          </ul>
        </Rule>
        <Rule>
          <div className="font-semibold mb-1">Office (Thursday)</div>
          <ul className="list-disc pl-5 text-[12px]">
            <li>Up to <strong>6 in-office cases</strong> per Thursday.</li>
            <li>Default 60 min per case (varies — confirm with provider).</li>
          </ul>
        </Rule>
        <Warning>
          The system blocks any slot creation that would violate these rules. If you
          believe a case <em>should</em> fit but the system refuses, double-check
          the procedure duration and whether mixed types are at play before forcing.
        </Warning>
      </Section>

      {/* MILESTONES */}
      <Section id="milestones" icon={CheckCircle2} title="Milestones (state machine)">
        <p>Every Surgery has a fixed list of milestones. Each has expected duration days; if
          a milestone goes beyond its expected window the surgery shows as
          <strong> behind schedule</strong> on the dashboard.</p>
        <ul className="list-disc pl-5 space-y-0.5 text-[12px]">
          <li><code>order_received</code></li>
          <li><code>benefits_determined</code></li>
          <li><code>prior_auth</code> (skipped if not required by payer)</li>
          <li><code>klara_scheduling</code></li>
          <li><code>patient_picks_date</code></li>
          <li><code>device_assigned</code> (robotic only)</li>
          <li><code>consent</code></li>
          <li><code>clearance</code> (only when <code>clearance_required</code>)</li>
          <li><code>labs</code></li>
          <li><code>preop</code> (the H&P / pre-op visit)</li>
          <li><code>posted_to_hospital</code></li>
          <li><code>surgery_day</code></li>
          <li><code>followup_appt</code> · <code>post_op_call</code> · <code>op_notes</code> · <code>path</code></li>
          <li><code>billed</code></li>
        </ul>
        <p className="text-[12px] text-gray-600">
          Milestones auto-advance from triggers (e.g. Klara confirmation closes
          <code> klara_scheduling</code>; DocuSign webhook closes <code>consent</code>).
          Manual override is always available on the surgery detail page.
        </p>
      </Section>

      {/* BUCKETS */}
      <Section id="buckets" icon={Users} title="Workload buckets (the dashboard tiles)">
        <p>A surgery can sit in several buckets at once — clear the work in any of them
          to advance the case.</p>
        <table className="w-full text-[12px] mt-2">
          <thead>
            <tr className="text-left text-gray-500 text-[11px]">
              <th className="pb-1 pr-2">Bucket</th>
              <th className="pb-1">When a surgery falls in</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            <tr><td className="py-1 pr-2 font-medium">Outstanding</td><td className="py-1">Surgery is in flight (not completed) — total active count.</td></tr>
            <tr><td className="py-1 pr-2 font-medium">Incomplete</td><td className="py-1">Required fields missing on the order.</td></tr>
            <tr><td className="py-1 pr-2 font-medium">Needs Benefits</td><td className="py-1">Benefits not yet determined.</td></tr>
            <tr><td className="py-1 pr-2 font-medium">Needs Prior Auth</td><td className="py-1">PA required and not yet submitted/approved.</td></tr>
            <tr><td className="py-1 pr-2 font-medium">Needs Sched Msg</td><td className="py-1">Patient has not received the Klara scheduling message yet.</td></tr>
            <tr><td className="py-1 pr-2 font-medium">Unresponsive</td><td className="py-1">Klara sent ≥7 days ago and patient still has not picked a date.</td></tr>
            <tr><td className="py-1 pr-2 font-medium">Date Picked</td><td className="py-1">Patient picked a date but downstream items remain.</td></tr>
            <tr><td className="py-1 pr-2 font-medium">Needs Consent</td><td className="py-1">Consent envelope not all signed.</td></tr>
            <tr><td className="py-1 pr-2 font-medium">Needs Clearance</td><td className="py-1">Cardiac/medical clearance required and not received.</td></tr>
            <tr><td className="py-1 pr-2 font-medium">Needs Labs</td><td className="py-1">Pre-op labs not received/sent to hospital.</td></tr>
            <tr><td className="py-1 pr-2 font-medium">Needs Repeat Pre-op</td><td className="py-1">Pre-op exam date is &gt;180 days before surgery → must be re-done.</td></tr>
            <tr><td className="py-1 pr-2 font-medium">Needs F/U Appt</td><td className="py-1">Post-op follow-up not yet scheduled.</td></tr>
            <tr><td className="py-1 pr-2 font-medium">Needs Post-Op Call</td><td className="py-1">Post-op call not made.</td></tr>
            <tr><td className="py-1 pr-2 font-medium">Needs Post-Op Docs</td><td className="py-1">Op note or path report not uploaded.</td></tr>
            <tr><td className="py-1 pr-2 font-medium">Needs Billed</td><td className="py-1">Surgery completed but charges not yet submitted.</td></tr>
          </tbody>
        </table>
      </Section>

      {/* PREOP 180 */}
      <Section id="preop" icon={AlertTriangle} title="Pre-op visit — 180-day rule">
        <Rule>
          <strong>The pre-op H&P must be dated within 180 days of the surgery date.</strong>
          If it's older, the patient must repeat the pre-op visit before surgery.
        </Rule>
        <p>The system flags this automatically: a red <strong>"needs repeat (&gt;180d)"</strong>
          badge appears next to the pre-op date on the surgery detail page, and the case
          shows up in the <strong>Needs Repeat Pre-op</strong> bucket.</p>
      </Section>

      {/* CONSENT */}
      <Section id="consent" icon={FileText} title="Consents & DocuSign">
        <p>Each surgery may need <strong>one or more</strong> consent forms — one per
          procedure, plus supplemental forms (e.g. Medicaid sterilization).</p>
        <Rule>
          <div className="font-semibold mb-1">How template matching works</div>
          <ol className="list-decimal pl-5 text-[12px] space-y-0.5">
            <li>The system iterates each procedure on the surgery.</li>
            <li>For each procedure, it finds the <strong>one primary template</strong>
              whose procedure keywords match (substring, case-insensitive).</li>
            <li>Then it looks at supplemental templates (e.g. sterilization) and attaches
              any whose <em>procedure + insurance + facility</em> match.</li>
            <li>One DocuSign envelope is sent per matched template — signed sequentially:
              Patient → Provider → Witness.</li>
          </ol>
        </Rule>
        <p>Combined cases (e.g. D&C + Hysteroscopy) get one envelope per procedure.
          The patient receives all envelopes in separate emails.</p>
        <p>
          Templates are managed in&nbsp;
          <Link to="/admin/consent-templates" className="text-plum-700 underline">Settings → Consent Templates</Link>.
          Every procedure must have a primary template registered or the system
          will refuse to send.
        </p>
      </Section>

      {/* MEDICAID STERILIZATION */}
      <Section id="sterilization" icon={ShieldCheck} title="Medicaid sterilization (HHS-687)">
        <Rule>
          <strong>Medicaid sterilization consent must be signed at least 30 days, and
          no more than 180 days, before the procedure</strong> (Title XIX requirement).
          Tubal patients with Medicaid MCO coverage get a supplemental envelope
          automatically.
        </Rule>
        <p>The supplemental form attaches when <em>both</em> conditions are true:</p>
        <ul className="list-disc pl-5 text-[12px] space-y-0.5">
          <li>Procedure keyword matches (tubal, sterilization, salpingectomy, ligation, BTL, Essure).</li>
          <li>Patient's primary insurance contains one of: <em>Priority Partners, Maryland
            Physicians Care, United Healthcare Community Plan, Wellpoint, Blue Cross
            Family Plan, MedStar Family Plan</em>.</li>
        </ul>
        <Warning>
          If the surgery is closer than 30 days when you click "Send via DocuSign", the
          system blocks the send and shows the warning. Either reschedule or override
          (and document why on the surgery).
        </Warning>
      </Section>

      {/* KLARA */}
      <Section id="klara" icon={MessageSquare} title="Klara messaging">
        <p>Klara is the patient-messaging channel. The system drafts messages for you;
          you review and send.</p>
        <ul className="list-disc pl-5 space-y-1 text-[12px]">
          <li><strong>Scheduling message</strong> — sent after benefits determined. Includes
            the patient-facing date-picker link.</li>
          <li><strong>Cardiology asks</strong> — when clearance required, Klara automatically
            adds the request for a clearance letter and contact info for the cardiologist.</li>
          <li><strong>Pre-op reminders</strong> — fired automatically as the surgery date
            approaches.</li>
        </ul>
      </Section>

      {/* RELEASE */}
      <Section id="release" icon={Hospital} title="Releasing unbooked hospital days">
        <p>Hospitals expect us to either fill or release block days. The system flags
          unbooked hospital days inside 14 days as candidates to release.</p>
        <Rule>
          <strong>Workflow:</strong> When a hospital block day is &lt;14 days out and has
          zero booked cases, a release-alert task appears on the dashboard. Notify the
          hospital and check off the to-do once they've been told.
        </Rule>
        <p className="text-[12px] text-gray-600">
          Releasing happens out-of-system (phone or email to OR scheduling). The
          checkbox just records that you did it.
        </p>
      </Section>

      {/* WAITLIST */}
      <Section id="waitlist" icon={Users} title="Waitlist">
        <p>Patients who want an earlier date than what's offered can opt onto the waitlist.
          When a slot opens (cancellation, hold, etc.) the system surfaces a Klara
          blast draft with all matching waitlist patients.</p>
        <ul className="list-disc pl-5 text-[12px] space-y-0.5">
          <li>Patients specify advance-notice days — how much warning they need to come in.</li>
          <li>Matches are limited to candidates whose advance-notice window fits the
            cancellation timeframe.</li>
          <li>Removing a waitlist row is automatic when the patient claims a slot or cancels.</li>
        </ul>
      </Section>

      {/* CANCELLATION */}
      <Section id="cancellation" icon={AlertTriangle} title="Cancellation reasons">
        <table className="w-full text-[12px]">
          <tbody className="divide-y divide-gray-100">
            <tr><td className="py-1 pr-2 font-medium">patient</td><td className="py-1">Patient initiated. May incur cancellation fee per policy.</td></tr>
            <tr><td className="py-1 pr-2 font-medium">anesthesia</td><td className="py-1">Anesthesia clearance failed.</td></tr>
            <tr><td className="py-1 pr-2 font-medium">hospital</td><td className="py-1">OR / facility issue. Patient gets first claim on the next available date.</td></tr>
            <tr><td className="py-1 pr-2 font-medium">medical</td><td className="py-1">New medical issue prevents surgery.</td></tr>
            <tr><td className="py-1 pr-2 font-medium">unresponsive</td><td className="py-1">Patient never confirmed after multiple Klara attempts.</td></tr>
            <tr><td className="py-1 pr-2 font-medium">hold</td><td className="py-1">Not cancelling, just delaying.</td></tr>
          </tbody>
        </table>
        <p className="text-[12px] text-gray-600">
          Cancelling frees the slot and chains into the waitlist matcher automatically.
        </p>
      </Section>

      {/* PATIENT PICKER */}
      <Section id="patient_picker" icon={Phone} title="Patient-facing date picker">
        <Rule>
          Soft-auth: the patient enters their <strong>DOB</strong> + <strong>last 4 digits
          of their phone</strong>. After 3 failed attempts the system locks the picker
          for 15 minutes (per surgery).
        </Rule>
        <ul className="list-disc pl-5 text-[12px] space-y-0.5">
          <li>Slots are materialized 180 days ahead.</li>
          <li>Patient sees only days where their procedure fits (capacity rules applied).</li>
          <li>Patient can update cardiologist info if clearance required.</li>
          <li>Picking a date stamps <code>scheduled_date</code> + <code>scheduled_start_time</code>
            and auto-advances the patient_picks_date milestone.</li>
        </ul>
      </Section>

      {/* DATE / HOLIDAY RULES */}
      <Section id="dates" icon={Calendar} title="Holiday / weekend handling">
        <ul className="list-disc pl-5 text-[12px] space-y-0.5">
          <li>Federal holidays are seeded as blackout days on system boot.</li>
          <li>Holidays that fall on a weekend roll to the nearest weekday (Saturday → Friday,
            Sunday → Monday).</li>
          <li>The 5-week block pattern continues from the next eligible weekday after a
            holiday — it does <em>not</em> skip a slot.</li>
        </ul>
      </Section>

      {/* CONTACTS */}
      <Section id="contacts" icon={Phone} title="Key contacts (placeholder)">
        <p className="text-[12px] text-gray-600">
          Fill in your hospital scheduling, anesthesia, and on-call surgeon contacts here
          so they're one click away from the dashboard.
        </p>
        <ul className="list-disc pl-5 text-[12px] space-y-0.5">
          <li>MedStar OR scheduling — <em>(add)</em></li>
          <li>CRMC OR scheduling — <em>(add)</em></li>
          <li>Anesthesia — <em>(add)</em></li>
          <li>Pathology — <em>(add)</em></li>
          <li>WWC on-call — <em>(add)</em></li>
        </ul>
      </Section>

      <div className="text-[11px] text-gray-500 text-center mt-8 mb-12">
        Last updated by the system on every code change. If something on this page
        contradicts what you observe in the app, the app is correct — please flag it
        so this doc can be brought back into sync.
      </div>
    </div>
  )
}
