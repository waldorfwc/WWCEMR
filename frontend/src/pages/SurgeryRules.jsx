import { useState, useMemo } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import api from '../utils/api'
import {
  ArrowLeft, BookOpen, Calendar, Hospital, Building2, Phone,
  AlertTriangle, FileText, Clock, Users, ShieldCheck, MessageSquare,
  CheckCircle2, ChevronRight,
  Sliders, Mail, Stethoscope, ListChecks,
  Plus, Trash2, Save, X, Edit3, Search,
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


function MilestoneRulesTab() {
  return (
    <div className="max-w-4xl mx-auto">
      <div className="mb-4 flex items-baseline justify-between">
        <div className="flex items-center gap-3">
          <div>
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


function ThresholdsTab() {
  const qc = useQueryClient()
  const { data } = useQuery({
    queryKey: ['surgery-config'],
    queryFn: () => api.get('/surgery/config').then(r => r.data),
  })
  const [draft, setDraft] = useState(null)
  const live = draft || data || {}

  const save = useMutation({
    mutationFn: (body) => api.put('/surgery/config', body).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['surgery-config'] })
      setDraft(null)
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Save failed'),
  })

  function field(key, label, hint) {
    return (
      <div className="flex items-center gap-3">
        <label className="text-[12px] text-gray-600 w-56">{label}</label>
        <input type="number" min="1" className="input text-sm w-24"
               value={live[key] ?? ''}
               onChange={e => setDraft({ ...live, [key]: Number(e.target.value) })} />
        {hint && <span className="text-[11px] text-gray-400">{hint}</span>}
      </div>
    )
  }

  return (
    <div className="bg-white rounded-lg border border-border-subtle p-5 max-w-2xl">
      <h2 className="text-base font-semibold mb-3">Release-alert thresholds</h2>
      <div className="space-y-3">
        {field('office_full_threshold',   'Office full threshold', '(<this = release the rest)')}
        {field('office_lookahead_days',   'Office lookahead days', '(fire alert this many days ahead)')}
        {field('hospital_lookahead_days', 'Hospital lookahead days', '(scan empty hospital days within this window)')}
      </div>
      <div className="mt-4 flex items-center gap-2">
        <button className="btn-primary text-sm" disabled={!draft || save.isPending}
                onClick={() => save.mutate(draft)}>
          {save.isPending ? 'Saving…' : 'Save'}
        </button>
        {draft && (
          <button className="btn-secondary text-sm" onClick={() => setDraft(null)}>Cancel</button>
        )}
      </div>
    </div>
  )
}
function RecipientsTab() {
  const qc = useQueryClient()
  const { data } = useQuery({
    queryKey: ['surgery-recipients'],
    queryFn: () => api.get('/surgery/admin/alert-recipients').then(r => r.data),
  })
  const add = useMutation({
    mutationFn: ({ alert_kind, email }) =>
      api.post('/surgery/admin/alert-recipients', { alert_kind, email }).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['surgery-recipients'] }),
    onError: (e) => alert(e?.response?.data?.detail || 'Add failed'),
  })
  const remove = useMutation({
    mutationFn: ({ alert_kind, email }) =>
      api.delete('/surgery/admin/alert-recipients', { params: { alert_kind, email } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['surgery-recipients'] }),
  })

  function ListEditor({ title, kind, hint }) {
    const [draft, setDraft] = useState('')
    const list = data?.[kind] || []
    return (
      <div className="bg-white rounded-lg border border-border-subtle p-5 max-w-xl mb-3">
        <h3 className="text-sm font-semibold mb-1">{title}</h3>
        <p className="text-[11px] text-gray-500 mb-3">{hint}</p>
        <div className="flex items-center gap-2 mb-3">
          <input className="input text-sm flex-1"
                 placeholder="someone@waldorfwomenscare.com"
                 value={draft} onChange={e => setDraft(e.target.value)} />
          <button className="btn-primary text-sm" disabled={!draft.trim()}
                  onClick={() => { add.mutate({ alert_kind: kind, email: draft.trim() }); setDraft('') }}>
            Add
          </button>
        </div>
        {list.length === 0 ? (
          <div className="text-[11px] text-gray-400 italic">
            No configured recipients — falling back to role-based query.
          </div>
        ) : (
          <ul className="space-y-1">
            {list.map(e => (
              <li key={e} className="flex items-center justify-between text-[12px]">
                <span>{e}</span>
                <button onClick={() => remove.mutate({ alert_kind: kind, email: e })}
                        className="text-red-600 text-[11px] hover:underline">Remove</button>
              </li>
            ))}
          </ul>
        )}
      </div>
    )
  }

  return (
    <div>
      <ListEditor title="Office release alert"   kind="office_release"
                   hint="Notified when an office procedure day is short on bookings." />
      <ListEditor title="Hospital release alert" kind="hospital_release"
                   hint="Notified when a hospital block day is fully empty." />
    </div>
  )
}
// ─── FacilitiesTab ───────────────────────────────────────────────

const NEW_FACILITY = {
  id:         '__new',
  code:       '',
  label:      '',
  address:    '',
  is_active:  true,
  sort_order: 100,
}

function FacilitiesTab() {
  const qc = useQueryClient()
  const [editingId, setEditingId] = useState(null)
  const [draft, setDraft]         = useState(null)
  const [filter, setFilter]       = useState('')

  const { data, isLoading } = useQuery({
    queryKey: ['surgery-facilities'],
    queryFn:  () => api.get('/surgery/admin/facilities').then(r => r.data.facilities),
  })

  const facilities = useMemo(() => {
    const rows = data || []
    const q = filter.trim().toLowerCase()
    if (!q) return rows
    return rows.filter(f =>
      (f.code    || '').toLowerCase().includes(q) ||
      (f.label   || '').toLowerCase().includes(q) ||
      (f.address || '').toLowerCase().includes(q)
    )
  }, [data, filter])

  const createMut = useMutation({
    mutationFn: (body) => api.post('/surgery/admin/facilities', body).then(r => r.data),
    onSuccess:  () => { qc.invalidateQueries({ queryKey: ['surgery-facilities'] }); setEditingId(null); setDraft(null) },
    onError:    (e) => alert(e?.response?.data?.detail || 'Create failed'),
  })

  const patchMut = useMutation({
    mutationFn: ({ id, body }) => api.patch(`/surgery/admin/facilities/${id}`, body).then(r => r.data),
    onSuccess:  () => { qc.invalidateQueries({ queryKey: ['surgery-facilities'] }); setEditingId(null); setDraft(null) },
    onError:    (e) => alert(e?.response?.data?.detail || 'Update failed'),
  })

  const deleteMut = useMutation({
    mutationFn: (id) => api.delete(`/surgery/admin/facilities/${id}`),
    onSuccess:  () => qc.invalidateQueries({ queryKey: ['surgery-facilities'] }),
    onError:    (e) => alert(e?.response?.data?.detail || 'Delete failed'),
  })

  function startEdit(row) {
    setEditingId(row.id)
    setDraft({
      code:       row.code       || '',
      label:      row.label      || '',
      address:    row.address    || '',
      is_active:  row.is_active  ?? true,
      sort_order: row.sort_order ?? 100,
    })
  }

  function cancelEdit() { setEditingId(null); setDraft(null) }

  function startNewRow() {
    setEditingId('__new')
    setDraft({ code: '', label: '', address: '', is_active: true, sort_order: 100 })
  }

  function save() {
    if (!draft?.code?.trim())  { alert('Code is required.');  return }
    if (!draft?.label?.trim()) { alert('Label is required.'); return }
    const body = {
      code:       draft.code.trim(),
      label:      draft.label.trim(),
      address:    draft.address.trim() || null,
      is_active:  draft.is_active,
      sort_order: Number(draft.sort_order) || 100,
    }
    if (editingId === '__new') createMut.mutate(body)
    else                       patchMut.mutate({ id: editingId, body })
  }

  function confirmDelete(row) {
    if (!window.confirm(`Delete "${row.label}"?`)) return
    deleteMut.mutate(row.id)
  }

  const showNewRow = editingId === '__new'
  const rows = showNewRow ? [NEW_FACILITY, ...facilities] : facilities
  const isSaving = createMut.isPending || patchMut.isPending

  return (
    <div>
      <div className="bg-white rounded-lg border border-border-subtle">
        {/* Card header */}
        <div className="px-5 py-4 border-b border-border-subtle flex items-center gap-3">
          <div className="flex-1 min-w-0">
            <h2 className="text-lg font-semibold text-gray-900">Facilities</h2>
            <p className="text-[12px] text-gray-500 mt-0.5">
              Surgical facilities available for scheduling. Inactive facilities are hidden from the scheduler.
            </p>
          </div>
          <div className="relative">
            <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-gray-400" />
            <input className="input text-sm pl-7 pr-2 py-1 w-48"
                   placeholder="Filter…"
                   value={filter}
                   onChange={e => setFilter(e.target.value)} />
          </div>
          <button className="btn-primary text-sm flex items-center gap-1"
                  onClick={startNewRow}
                  disabled={!!editingId}>
            <Plus size={12} /> Add row
          </button>
        </div>

        {/* Table */}
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-[11px] uppercase text-gray-500">
              <tr>
                <th className="text-left px-5 py-2 w-[10%]">Code</th>
                <th className="text-left px-3 py-2 w-[28%]">Label</th>
                <th className="text-left px-3 py-2 w-[28%]">Address</th>
                <th className="text-center px-3 py-2 w-[8%]">Active</th>
                <th className="text-center px-3 py-2 w-[8%]">Sort</th>
                <th className="text-right px-5 py-2 w-[120px]">Actions</th>
              </tr>
            </thead>
            <tbody>
              {isLoading && (
                <tr><td colSpan={6} className="px-5 py-6 text-gray-400 text-[12px]">Loading…</td></tr>
              )}
              {!isLoading && rows.length === 0 && (
                <tr><td colSpan={6} className="px-5 py-6 text-gray-400 text-[12px] italic">
                  No facilities yet — click <strong>Add row</strong> to start.
                </td></tr>
              )}
              {rows.map(row => {
                const isEditing = editingId === row.id
                const dimmed    = !isEditing && !row.is_active
                return (
                  <tr key={row.id}
                      className={`border-t border-border-subtle ${
                        isEditing ? 'bg-plum-50/40'
                        : dimmed  ? 'opacity-60 hover:bg-gray-50'
                        :           'hover:bg-gray-50'
                      }`}>
                    {isEditing ? (
                      <FacilityEditRow
                        draft={draft}
                        setDraft={setDraft}
                        save={save}
                        cancel={cancelEdit}
                        isSaving={isSaving}
                      />
                    ) : (
                      <FacilityDisplayRow
                        row={row}
                        startEdit={() => startEdit(row)}
                        onDelete={() => confirmDelete(row)}
                        disabled={!!editingId}
                      />
                    )}
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

function FacilityDisplayRow({ row, startEdit, onDelete, disabled }) {
  return (
    <>
      <td className="px-5 py-3 align-middle">
        <code className="text-[12px] bg-gray-100 px-1 py-0.5 rounded">{row.code}</code>
      </td>
      <td className="px-3 py-3 align-middle font-medium text-gray-900">{row.label}</td>
      <td className="px-3 py-3 align-middle text-[12px] text-gray-600">{row.address || <span className="italic text-gray-400">—</span>}</td>
      <td className="px-3 py-3 align-middle text-center">
        <span className={`inline-block w-2 h-2 rounded-full ${row.is_active ? 'bg-green-500' : 'bg-gray-300'}`} title={row.is_active ? 'Active' : 'Inactive'} />
      </td>
      <td className="px-3 py-3 align-middle text-center text-[12px] text-gray-500">{row.sort_order}</td>
      <td className="px-5 py-3 align-middle text-right">
        <div className="inline-flex items-center gap-1">
          <button className="text-[11px] px-2 py-1 rounded border border-gray-200 hover:bg-plum-50 flex items-center gap-1 disabled:opacity-30"
                  onClick={startEdit}
                  disabled={disabled}
                  title="Edit row">
            <Edit3 size={11} /> Edit
          </button>
          <button className="text-[11px] px-2 py-1 rounded border border-red-200 text-red-600 hover:bg-red-50 flex items-center gap-1 disabled:opacity-30"
                  onClick={onDelete}
                  disabled={disabled}
                  title="Delete">
            <Trash2 size={11} />
          </button>
        </div>
      </td>
    </>
  )
}

function FacilityEditRow({ draft, setDraft, save, cancel, isSaving }) {
  return (
    <>
      <td className="px-5 py-3 align-top">
        <input className="input text-sm w-24"
               placeholder="office"
               value={draft.code}
               onChange={e => setDraft({ ...draft, code: e.target.value })}
               autoFocus />
      </td>
      <td className="px-3 py-3 align-top">
        <input className="input text-sm w-full"
               placeholder="Facility label"
               value={draft.label}
               onChange={e => setDraft({ ...draft, label: e.target.value })} />
      </td>
      <td className="px-3 py-3 align-top">
        <input className="input text-sm w-full"
               placeholder="City, ST  or full address"
               value={draft.address}
               onChange={e => setDraft({ ...draft, address: e.target.value })} />
      </td>
      <td className="px-3 py-3 align-top text-center">
        <input type="checkbox"
               className="h-4 w-4 rounded border-gray-300 text-plum-600 focus:ring-plum-500"
               checked={draft.is_active}
               onChange={e => setDraft({ ...draft, is_active: e.target.checked })} />
      </td>
      <td className="px-3 py-3 align-top">
        <input type="number" min="1"
               className="input text-sm w-16 text-center"
               value={draft.sort_order}
               onChange={e => setDraft({ ...draft, sort_order: e.target.value })} />
      </td>
      <td className="px-5 py-3 align-top text-right">
        <div className="inline-flex items-center gap-1">
          <button className="text-[11px] px-2 py-1 rounded bg-plum-600 text-white hover:bg-plum-700 flex items-center gap-1 disabled:opacity-50"
                  onClick={save}
                  disabled={isSaving}>
            <Save size={11} /> {isSaving ? 'Saving…' : 'Save'}
          </button>
          <button className="text-[11px] px-2 py-1 rounded border border-gray-200 hover:bg-gray-50"
                  onClick={cancel}
                  disabled={isSaving}>
            Cancel
          </button>
        </div>
      </td>
    </>
  )
}
// ─── TemplatesTab ────────────────────────────────────────────────

const PROCEDURE_KINDS = ['minor', 'major', 'office', 'robotic_180', 'robotic_240']

const NEW_TEMPLATE = {
  id:                       '__new',
  code:                     '',
  name:                     '',
  procedure_kind:           'minor',
  default_duration_minutes: 60,
  default_cpt_code:         '',
  is_active:                true,
}

function TemplatesTab() {
  const qc = useQueryClient()
  const [editingId, setEditingId] = useState(null)
  const [draft, setDraft]         = useState(null)
  const [filter, setFilter]       = useState('')

  const { data, isLoading } = useQuery({
    queryKey: ['surgery-templates-admin'],
    queryFn:  () => api.get('/surgery/admin/procedure-templates').then(r => r.data.templates),
  })

  const templates = useMemo(() => {
    const rows = data || []
    const q = filter.trim().toLowerCase()
    if (!q) return rows
    return rows.filter(t =>
      (t.code           || '').toLowerCase().includes(q) ||
      (t.name           || '').toLowerCase().includes(q) ||
      (t.procedure_kind || '').toLowerCase().includes(q)
    )
  }, [data, filter])

  const createMut = useMutation({
    mutationFn: (body) => api.post('/surgery/admin/procedure-templates', body).then(r => r.data),
    onSuccess:  () => { qc.invalidateQueries({ queryKey: ['surgery-templates-admin'] }); setEditingId(null); setDraft(null) },
    onError:    (e) => alert(e?.response?.data?.detail || 'Create failed'),
  })

  const patchMut = useMutation({
    mutationFn: ({ id, body }) => api.patch(`/surgery/admin/procedure-templates/${id}`, body).then(r => r.data),
    onSuccess:  () => { qc.invalidateQueries({ queryKey: ['surgery-templates-admin'] }); setEditingId(null); setDraft(null) },
    onError:    (e) => alert(e?.response?.data?.detail || 'Update failed'),
  })

  const deleteMut = useMutation({
    mutationFn: (id) => api.delete(`/surgery/admin/procedure-templates/${id}`),
    onSuccess:  () => qc.invalidateQueries({ queryKey: ['surgery-templates-admin'] }),
    onError:    (e) => alert(e?.response?.data?.detail || 'Delete failed'),
  })

  function startEdit(row) {
    setEditingId(row.id)
    setDraft({
      code:                     row.code                     || '',
      name:                     row.name                     || '',
      procedure_kind:           row.procedure_kind           || 'minor',
      default_duration_minutes: row.default_duration_minutes ?? 60,
      default_cpt_code:         row.default_cpt_code         || '',
      is_active:                row.is_active                ?? true,
    })
  }

  function cancelEdit() { setEditingId(null); setDraft(null) }

  function startNewRow() {
    setEditingId('__new')
    setDraft({ code: '', name: '', procedure_kind: 'minor', default_duration_minutes: 60, default_cpt_code: '', is_active: true })
  }

  function save() {
    if (!draft?.code?.trim()) { alert('Code is required.');  return }
    if (!draft?.name?.trim()) { alert('Name is required.'); return }
    const body = {
      code:                     draft.code.trim(),
      name:                     draft.name.trim(),
      procedure_kind:           draft.procedure_kind,
      default_duration_minutes: Number(draft.default_duration_minutes) || 60,
      default_cpt_code:         draft.default_cpt_code.trim() || null,
      is_active:                draft.is_active,
    }
    if (editingId === '__new') createMut.mutate(body)
    else                       patchMut.mutate({ id: editingId, body })
  }

  function confirmDelete(row) {
    if (!window.confirm(`Delete "${row.name}"?`)) return
    deleteMut.mutate(row.id)
  }

  const showNewRow = editingId === '__new'
  const rows = showNewRow ? [NEW_TEMPLATE, ...templates] : templates
  const isSaving = createMut.isPending || patchMut.isPending

  return (
    <div>
      <div className="bg-white rounded-lg border border-border-subtle">
        {/* Card header */}
        <div className="px-5 py-4 border-b border-border-subtle flex items-center gap-3">
          <div className="flex-1 min-w-0">
            <h2 className="text-lg font-semibold text-gray-900">Procedure Templates</h2>
            <p className="text-[12px] text-gray-500 mt-0.5">
              Default procedure templates used when scheduling a surgery. Inactive templates are hidden from the scheduler.
            </p>
          </div>
          <div className="relative">
            <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-gray-400" />
            <input className="input text-sm pl-7 pr-2 py-1 w-48"
                   placeholder="Filter…"
                   value={filter}
                   onChange={e => setFilter(e.target.value)} />
          </div>
          <button className="btn-primary text-sm flex items-center gap-1"
                  onClick={startNewRow}
                  disabled={!!editingId}>
            <Plus size={12} /> Add row
          </button>
        </div>

        {/* Table */}
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-[11px] uppercase text-gray-500">
              <tr>
                <th className="text-left px-5 py-2 w-[10%]">Code</th>
                <th className="text-left px-3 py-2 w-[24%]">Name</th>
                <th className="text-left px-3 py-2 w-[16%]">Procedure kind</th>
                <th className="text-center px-3 py-2 w-[10%]">Default min</th>
                <th className="text-left px-3 py-2 w-[10%]">Default CPT</th>
                <th className="text-center px-3 py-2 w-[8%]">Active</th>
                <th className="text-right px-5 py-2 w-[120px]">Actions</th>
              </tr>
            </thead>
            <tbody>
              {isLoading && (
                <tr><td colSpan={7} className="px-5 py-6 text-gray-400 text-[12px]">Loading…</td></tr>
              )}
              {!isLoading && rows.length === 0 && (
                <tr><td colSpan={7} className="px-5 py-6 text-gray-400 text-[12px] italic">
                  No templates yet — click <strong>Add row</strong> to start.
                </td></tr>
              )}
              {rows.map(row => {
                const isEditing = editingId === row.id
                const dimmed    = !isEditing && !row.is_active
                return (
                  <tr key={row.id}
                      className={`border-t border-border-subtle ${
                        isEditing ? 'bg-plum-50/40'
                        : dimmed  ? 'opacity-60 hover:bg-gray-50'
                        :           'hover:bg-gray-50'
                      }`}>
                    {isEditing ? (
                      <TemplateEditRow
                        draft={draft}
                        setDraft={setDraft}
                        save={save}
                        cancel={cancelEdit}
                        isSaving={isSaving}
                      />
                    ) : (
                      <TemplateDisplayRow
                        row={row}
                        startEdit={() => startEdit(row)}
                        onDelete={() => confirmDelete(row)}
                        disabled={!!editingId}
                      />
                    )}
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

function TemplateDisplayRow({ row, startEdit, onDelete, disabled }) {
  return (
    <>
      <td className="px-5 py-3 align-middle">
        <code className="text-[12px] bg-gray-100 px-1 py-0.5 rounded">{row.code}</code>
      </td>
      <td className="px-3 py-3 align-middle font-medium text-gray-900">{row.name}</td>
      <td className="px-3 py-3 align-middle text-[12px] text-gray-600">{row.procedure_kind || <span className="italic text-gray-400">—</span>}</td>
      <td className="px-3 py-3 align-middle text-center text-[12px] text-gray-600">{row.default_duration_minutes ?? <span className="italic text-gray-400">—</span>}</td>
      <td className="px-3 py-3 align-middle text-[12px] text-gray-600">
        {row.default_cpt_code
          ? <code className="bg-gray-100 px-1 py-0.5 rounded">{row.default_cpt_code}</code>
          : <span className="italic text-gray-400">—</span>}
      </td>
      <td className="px-3 py-3 align-middle text-center">
        <span className={`inline-block w-2 h-2 rounded-full ${row.is_active ? 'bg-green-500' : 'bg-gray-300'}`} title={row.is_active ? 'Active' : 'Inactive'} />
      </td>
      <td className="px-5 py-3 align-middle text-right">
        <div className="inline-flex items-center gap-1">
          <button className="text-[11px] px-2 py-1 rounded border border-gray-200 hover:bg-plum-50 flex items-center gap-1 disabled:opacity-30"
                  onClick={startEdit}
                  disabled={disabled}
                  title="Edit row">
            <Edit3 size={11} /> Edit
          </button>
          <button className="text-[11px] px-2 py-1 rounded border border-red-200 text-red-600 hover:bg-red-50 flex items-center gap-1 disabled:opacity-30"
                  onClick={onDelete}
                  disabled={disabled}
                  title="Delete">
            <Trash2 size={11} />
          </button>
        </div>
      </td>
    </>
  )
}

function TemplateEditRow({ draft, setDraft, save, cancel, isSaving }) {
  return (
    <>
      <td className="px-5 py-3 align-top">
        <input className="input text-sm w-24"
               placeholder="office_30"
               value={draft.code}
               onChange={e => setDraft({ ...draft, code: e.target.value })}
               autoFocus />
      </td>
      <td className="px-3 py-3 align-top">
        <input className="input text-sm w-full"
               placeholder="Template name"
               value={draft.name}
               onChange={e => setDraft({ ...draft, name: e.target.value })} />
      </td>
      <td className="px-3 py-3 align-top">
        <select className="input text-sm w-full"
                value={draft.procedure_kind}
                onChange={e => setDraft({ ...draft, procedure_kind: e.target.value })}>
          {PROCEDURE_KINDS.map(k => <option key={k} value={k}>{k}</option>)}
        </select>
      </td>
      <td className="px-3 py-3 align-top">
        <input type="number" min="1"
               className="input text-sm w-20 text-center"
               value={draft.default_duration_minutes}
               onChange={e => setDraft({ ...draft, default_duration_minutes: e.target.value })} />
      </td>
      <td className="px-3 py-3 align-top">
        <input className="input text-sm w-24"
               placeholder="58571"
               value={draft.default_cpt_code}
               onChange={e => setDraft({ ...draft, default_cpt_code: e.target.value })} />
      </td>
      <td className="px-3 py-3 align-top text-center">
        <input type="checkbox"
               className="h-4 w-4 rounded border-gray-300 text-plum-600 focus:ring-plum-500"
               checked={draft.is_active}
               onChange={e => setDraft({ ...draft, is_active: e.target.checked })} />
      </td>
      <td className="px-5 py-3 align-top text-right">
        <div className="inline-flex items-center gap-1">
          <button className="text-[11px] px-2 py-1 rounded bg-plum-600 text-white hover:bg-plum-700 flex items-center gap-1 disabled:opacity-50"
                  onClick={save}
                  disabled={isSaving}>
            <Save size={11} /> {isSaving ? 'Saving…' : 'Save'}
          </button>
          <button className="text-[11px] px-2 py-1 rounded border border-gray-200 hover:bg-gray-50"
                  onClick={cancel}
                  disabled={isSaving}>
            Cancel
          </button>
        </div>
      </td>
    </>
  )
}


function EmailTemplatesTab() {
  const qc = useQueryClient()
  const { data } = useQuery({
    queryKey: ['email-templates'],
    queryFn: () => api.get('/surgery/admin/email-templates').then(r => r.data),
  })

  const [editingId, setEditingId] = useState(null)
  const [draft, setDraft] = useState(null)
  const [previewVars, setPreviewVars] = useState('{\n  "patient_name": "Pat",\n  "surgery_date": "2026-06-15"\n}')
  const [preview, setPreview] = useState(null)

  const patch = useMutation({
    mutationFn: ({ id, body }) =>
      api.patch(`/surgery/admin/email-templates/${id}`, body).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['email-templates'] })
      setEditingId(null); setDraft(null); setPreview(null)
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Save failed'),
  })

  const previewMut = useMutation({
    mutationFn: (body) =>
      api.post('/surgery/admin/email-templates/preview', body).then(r => r.data),
    onSuccess: (data) => setPreview(data),
    onError: (e) => alert(e?.response?.data?.detail || 'Preview failed'),
  })

  function startEdit(t) {
    setEditingId(t.id)
    setDraft({
      label:     t.label,
      subject:   t.subject,
      html_body: t.html_body,
      is_active: t.is_active,
    })
    setPreview(null)
  }

  function runPreview() {
    let ctx
    try { ctx = JSON.parse(previewVars) }
    catch { return alert('Preview vars JSON is invalid') }
    previewMut.mutate({
      subject:   draft?.subject || '',
      html_body: draft?.html_body || '',
      context:   ctx,
    })
  }

  const list = data?.templates || []

  return (
    <div className="space-y-3">
      {list.map(t => (
        <div key={t.id}
             className={`bg-white border rounded-lg p-4 ${
               editingId === t.id ? 'border-plum-400' : 'border-border-subtle'
             }`}>
          <div className="flex items-center justify-between mb-1">
            <div>
              <div className="text-sm font-semibold">{t.label}</div>
              <div className="text-[11px] text-gray-500 font-mono">{t.kind}</div>
            </div>
            <div className="flex items-center gap-2">
              <span className={`text-[11px] px-2 py-0.5 rounded ${
                t.is_active ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-500'
              }`}>{t.is_active ? 'active' : 'inactive'}</span>
              {editingId !== t.id && (
                <button className="btn-secondary text-[11px]" onClick={() => startEdit(t)}>
                  Edit
                </button>
              )}
            </div>
          </div>

          {editingId === t.id ? (
            <div className="mt-2 space-y-2">
              <div>
                <label className="text-[11px] uppercase text-gray-500 block mb-0.5">Subject</label>
                <input className="input text-sm w-full"
                       value={draft.subject}
                       onChange={e => setDraft({ ...draft, subject: e.target.value })} />
              </div>
              <div>
                <label className="text-[11px] uppercase text-gray-500 block mb-0.5">HTML body</label>
                <textarea className="input text-sm w-full font-mono" rows={8}
                          value={draft.html_body}
                          onChange={e => setDraft({ ...draft, html_body: e.target.value })} />
              </div>
              <div>
                <label className="text-[11px] uppercase text-gray-500 block mb-0.5">
                  Preview vars (JSON)
                </label>
                <textarea className="input text-[11px] w-full font-mono" rows={4}
                          value={previewVars}
                          onChange={e => setPreviewVars(e.target.value)} />
              </div>
              {preview && (
                <div className="bg-gray-50 border border-border-subtle rounded p-2">
                  <div className="text-[10px] uppercase text-gray-500 mb-1">Preview</div>
                  <div className="text-[12px] font-semibold">{preview.subject}</div>
                  <div className="text-[12px] mt-1" dangerouslySetInnerHTML={{ __html: preview.html_body }} />
                </div>
              )}
              <div className="flex items-center gap-2 pt-1">
                <button className="btn-primary text-sm"
                        onClick={() => patch.mutate({ id: t.id, body: draft })}
                        disabled={patch.isPending}>
                  {patch.isPending ? 'Saving…' : 'Save'}
                </button>
                <button className="btn-secondary text-sm" onClick={runPreview}
                        disabled={previewMut.isPending}>
                  {previewMut.isPending ? 'Rendering…' : 'Preview'}
                </button>
                <label className="text-[11px] flex items-center gap-1 ml-2">
                  <input type="checkbox"
                         checked={draft.is_active}
                         onChange={e => setDraft({ ...draft, is_active: e.target.checked })} />
                  Active
                </label>
                <button className="btn-secondary text-sm ml-auto"
                        onClick={() => { setEditingId(null); setDraft(null); setPreview(null) }}>
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <div className="text-[12px] text-gray-700 mt-2 font-mono whitespace-pre-wrap line-clamp-3">
              {t.subject}
            </div>
          )}
        </div>
      ))}
    </div>
  )
}


const TABS = [
  { k: 'milestones', label: 'Milestone Rules',     icon: ListChecks },
  { k: 'thresholds', label: 'Thresholds',          icon: Sliders },
  { k: 'recipients', label: 'Alert Recipients',    icon: Mail },
  { k: 'facilities', label: 'Facilities',          icon: Building2 },
  { k: 'templates',  label: 'Procedure Templates', icon: Stethoscope },
  { k: 'emails',     label: 'Email Templates',     icon: Mail },
  { k: 'sms',        label: 'SMS Templates',       icon: MessageSquare },
]

export default function SurgeryRules() {
  const [tab, setTab] = useState('milestones')
  return (
    <div>
      <div className="mb-4 flex items-center gap-3">
        <Link to="/surgery" className="text-muted hover:text-plum-700">
          <ArrowLeft size={18} />
        </Link>
        <h1 className="font-serif text-[24px] font-semibold text-ink m-0 flex items-center gap-2">
          <BookOpen size={22} className="text-plum-700" />
          Surgery rules
        </h1>
      </div>
      <div className="flex gap-1 border-b border-border-subtle mb-4">
        {TABS.map(t => {
          const Icon = t.icon
          return (
            <button key={t.k}
                    onClick={() => setTab(t.k)}
                    className={`flex items-center gap-1.5 px-4 py-2 text-sm font-medium border-b-2 transition ${
                      tab === t.k
                        ? 'border-plum-600 text-plum-700'
                        : 'border-transparent text-gray-500 hover:text-plum-700 hover:border-plum-200'
                    }`}>
              <Icon size={14} /> {t.label}
            </button>
          )
        })}
      </div>
      {tab === 'milestones' && <MilestoneRulesTab />}
      {tab === 'thresholds' && <ThresholdsTab />}
      {tab === 'recipients' && <RecipientsTab />}
      {tab === 'facilities' && <FacilitiesTab />}
      {tab === 'templates'  && <TemplatesTab />}
      {tab === 'emails'     && <EmailTemplatesTab />}
      {tab === 'sms'        && <SmsTemplatesTab />}
    </div>
  )
}


function SmsTemplatesTab() {
  const qc = useQueryClient()
  const { data } = useQuery({
    queryKey: ['sms-templates'],
    queryFn: () => api.get('/surgery/admin/sms-templates').then(r => r.data),
  })

  const [editingId, setEditingId] = useState(null)
  const [draft, setDraft] = useState(null)
  const [previewVars, setPreviewVars] = useState('{\n  "patient_name": "Pat",\n  "surgery_date": "2026-06-15",\n  "start_time": "07:30",\n  "facility": "MedStar",\n  "days_until": "3"\n}')
  const [preview, setPreview] = useState(null)

  const patch = useMutation({
    mutationFn: ({ id, body }) =>
      api.patch(`/surgery/admin/sms-templates/${id}`, body).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['sms-templates'] })
      setEditingId(null); setDraft(null); setPreview(null)
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Save failed'),
  })

  const previewMut = useMutation({
    mutationFn: (body) =>
      api.post('/surgery/admin/sms-templates/preview', body).then(r => r.data),
    onSuccess: (data) => setPreview(data),
    onError: (e) => alert(e?.response?.data?.detail || 'Preview failed'),
  })

  function startEdit(t) {
    setEditingId(t.id)
    setDraft({ label: t.label, body: t.body, is_active: t.is_active })
    setPreview(null)
  }

  function runPreview() {
    let ctx
    try { ctx = JSON.parse(previewVars) }
    catch { return alert('Preview vars JSON is invalid') }
    previewMut.mutate({ body: draft?.body || '', context: ctx })
  }

  const list = data?.templates || []

  return (
    <div className="space-y-3">
      {list.map(t => (
        <div key={t.id}
             className={`bg-white border rounded-lg p-4 ${
               editingId === t.id ? 'border-plum-400' : 'border-border-subtle'
             }`}>
          <div className="flex items-center justify-between mb-1">
            <div>
              <div className="text-sm font-semibold">{t.label}</div>
              <div className="text-[11px] text-gray-500 font-mono">{t.kind}</div>
            </div>
            <div className="flex items-center gap-2">
              <span className={`text-[11px] px-2 py-0.5 rounded ${
                t.is_active ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-500'
              }`}>{t.is_active ? 'active' : 'inactive'}</span>
              {editingId !== t.id && (
                <button className="btn-secondary text-[11px]" onClick={() => startEdit(t)}>
                  Edit
                </button>
              )}
            </div>
          </div>

          {editingId === t.id ? (
            <div className="mt-2 space-y-2">
              <div>
                <label className="text-[11px] uppercase text-gray-500 block mb-0.5">
                  {'Body (plain text — {{var}} for substitution)'}
                </label>
                <textarea className="input text-sm w-full font-mono" rows={4}
                          value={draft.body}
                          onChange={e => setDraft({ ...draft, body: e.target.value })} />
                <div className="text-[10px] text-gray-400 mt-0.5">
                  {draft.body.length} chars
                  {draft.body.length > 160 && (
                    <span className="text-amber-700 ml-2">
                      (will send as multiple segments)
                    </span>
                  )}
                </div>
              </div>
              <div>
                <label className="text-[11px] uppercase text-gray-500 block mb-0.5">
                  Preview vars (JSON)
                </label>
                <textarea className="input text-[11px] w-full font-mono" rows={4}
                          value={previewVars}
                          onChange={e => setPreviewVars(e.target.value)} />
              </div>
              {preview && (
                <div className="bg-gray-50 border border-border-subtle rounded p-2">
                  <div className="text-[10px] uppercase text-gray-500 mb-1">
                    Preview ({preview.length} chars · {preview.segments} segment{preview.segments === 1 ? '' : 's'})
                  </div>
                  <div className="text-[12px] font-mono whitespace-pre-wrap">{preview.body}</div>
                </div>
              )}
              <div className="flex items-center gap-2 pt-1">
                <button className="btn-primary text-sm"
                        onClick={() => patch.mutate({ id: t.id, body: draft })}
                        disabled={patch.isPending}>
                  {patch.isPending ? 'Saving…' : 'Save'}
                </button>
                <button className="btn-secondary text-sm" onClick={runPreview}
                        disabled={previewMut.isPending}>
                  {previewMut.isPending ? 'Rendering…' : 'Preview'}
                </button>
                <label className="text-[11px] flex items-center gap-1 ml-2">
                  <input type="checkbox"
                         checked={draft.is_active}
                         onChange={e => setDraft({ ...draft, is_active: e.target.checked })} />
                  Active
                </label>
                <button className="btn-secondary text-sm ml-auto"
                        onClick={() => { setEditingId(null); setDraft(null); setPreview(null) }}>
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <div className="text-[12px] text-gray-700 mt-2 font-mono whitespace-pre-wrap line-clamp-3">
              {t.body}
            </div>
          )}
        </div>
      ))}
    </div>
  )
}
