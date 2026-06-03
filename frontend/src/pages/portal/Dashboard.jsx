import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import {
  CalendarDays, ChevronRight, ClipboardCheck, CreditCard,
  Flower2, HeartHandshake, Phone, Stethoscope,
} from 'lucide-react'
import { portalApi, isStaffPreview } from '../../lib/portal-api'

// Soft film-grain texture for the hero — plum-tinted noise, sits on top of
// the gradient to give it a tactile feel without any photography.
const GRAIN_SVG = `data:image/svg+xml;utf8,${encodeURIComponent(
  `<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 200 200'>
     <filter id='n'>
       <feTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='2' seed='3'/>
       <feColorMatrix values='0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 .35 0'/>
     </filter>
     <rect width='100%' height='100%' filter='url(%23n)'/>
   </svg>`
)}`

// Display order + labels for the journey timeline. We map the backend's
// milestone keys onto seven readable steps. Keys that aren't returned
// (e.g. fmla when the patient isn't on leave) are skipped automatically.
const JOURNEY = [
  { key: 'benefits',         label: 'Benefits' },
  { key: 'payment',          label: 'Payment' },
  { key: 'consent',          label: 'Consent' },
  { key: 'schedule',         label: 'Schedule' },
  { key: 'labs',             label: 'Pre-op labs' },
  { key: 'hospital_preop',   label: 'Hospital call' },
  { key: 'surgery',          label: 'Surgery' },
]

const DONE_STATES = new Set(['done', 'paid', 'signed', 'completed', 'received'])


export default function Dashboard() {
  const { sid } = useParams()
  const qc = useQueryClient()
  const { data, isLoading, error } = useQuery({
    queryKey: ['portal-dashboard', sid],
    queryFn: () => portalApi.get(`/${sid}/dashboard`).then(r => r.data),
    staleTime: 30_000,
  })

  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="text-plum-600/70 text-sm">Loading your care plan…</div>
      </div>
    )
  }
  if (error) {
    return (
      <div className="px-10 py-16">
        <div className="bg-rose-50 border border-rose-200 rounded-lg p-4 text-rose-800 text-sm">
          We couldn't load your dashboard right now. Please refresh, or call
          our office at <strong>240-252-2140</strong>.
        </div>
      </div>
    )
  }

  const { surgery, milestones, next_action } = data
  const firstName = (surgery.patient_name?.split(',')[1] || surgery.patient_name || '').trim().split(' ')[0] || 'there'
  const today = new Date().toLocaleDateString('en-US', {
    weekday: 'long', month: 'long', day: 'numeric',
  })

  // Map milestone keys → milestone object for the journey ribbon
  const mByKey = Object.fromEntries((milestones || []).map(m => [m.key, m]))

  // Count done for the journey progress label
  const doneCount = JOURNEY.filter(s => {
    const m = mByKey[s.key]
    return m && DONE_STATES.has(m.status)
  }).length

  // Surgery date status — drives the welcome subtitle copy
  const surgeryDate = surgery.surgery_date
    ? new Date(surgery.surgery_date + 'T00:00:00').toLocaleDateString('en-US', {
        weekday: 'long', month: 'long', day: 'numeric', year: 'numeric',
      })
    : null

  // Derived state for the action cards
  const balance = parseFloat(surgery.outstanding_balance ?? surgery.patient_responsibility ?? 0)
  const consentMs = mByKey['consent']
  const consentPending = consentMs && !DONE_STATES.has(consentMs.status)
  const scheduleMs = mByKey['schedule']
  const labsMs = mByKey['labs']
  const labsDone = labsMs && DONE_STATES.has(labsMs.status)

  return (
    <div>
      {/* Hero — compact plum gradient, no photography */}
      <section className="relative overflow-hidden bg-plum-ink">
        <div className="absolute inset-0 bg-gradient-to-br from-plum-900 via-plum-700 to-plum-400" />
        <div className="absolute inset-0 opacity-60"
             style={{ background: 'radial-gradient(60% 80% at 80% 20%, rgba(255,255,255,0.18), transparent 60%)' }} />
        <div className="absolute inset-0 opacity-40 mix-blend-overlay pointer-events-none"
             style={{ backgroundImage: `url("${GRAIN_SVG}")`, backgroundSize: '180px 180px' }} />
        <div className="absolute top-2 right-6 font-serif italic text-white/15 text-[72px] leading-none select-none pointer-events-none">
          W
        </div>
        <div className="relative flex flex-col justify-center px-6 md:px-10 py-4">
          <div className="text-[10px] uppercase tracking-[0.22em] text-white/80 font-medium">
            {today}
          </div>
          <h1 className="font-serif text-white text-[18px] md:text-[22px] leading-tight font-semibold tracking-tight mt-0.5">
            Welcome back, {firstName}.
          </h1>
          <p className="text-white/85 text-[12px] md:text-[13px] mt-1 max-w-xl">
            {surgeryDate
              ? <>Your surgery is scheduled for <strong>{surgeryDate}</strong>. A few steps below to get you ready.</>
              : <>Your care plan is below. Once you've completed the steps, we'll help you pick a surgery date.</>}
          </p>
        </div>
      </section>

      <div className="px-6 md:px-10 py-4 md:py-5 max-w-5xl">
        {/* Journey */}
        <section className="mb-5">
          <div className="flex items-baseline justify-between mb-3 gap-3">
            <h2 className="font-serif text-[14px] md:text-[15px] text-plum-ink font-semibold tracking-tight">
              Your care journey
            </h2>
            <div className="text-[11px] uppercase tracking-[0.16em] text-plum-600/70 shrink-0">
              {doneCount} of {JOURNEY.length} complete
            </div>
          </div>
          <JourneyTimeline milestones={mByKey} />
        </section>

        {/* Action cards */}
        <section className="mb-5">
          <h2 className="font-serif text-[14px] md:text-[15px] text-plum-ink font-semibold tracking-tight mb-5">
            What's next
          </h2>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
            {balance > 0 && (
              <ActionCard
                icon={CreditCard}
                tone="rose"
                urgent
                title="Settle your balance"
                meta={`$${balance.toFixed(2)} due`}
                body="Pay securely with your card, FSA, or HSA. Pre-payment is required before your surgery date is locked in."
                cta="Pay now"
                to={`/portal/s/${sid}/payments`}
              />
            )}
            {consentPending && (
              <ActionCard
                icon={ClipboardCheck}
                tone="amber"
                title="Sign your consent"
                meta="Awaiting your signature"
                body="Review and electronically sign the consent forms for your procedure. Takes about three minutes."
                cta="Open envelope"
                to={`/portal/s/${sid}/consent`}
              />
            )}
            {scheduleMs && !DONE_STATES.has(scheduleMs.status) && (
              <ActionCard
                icon={CalendarDays}
                tone="sky"
                title="Pick your surgery date"
                meta={balance > 0 ? 'Available after payment' : 'Open dates available'}
                body={surgery.facility
                  ? `Pick an open date for your procedure with ${surgery.surgeon || 'your surgeon'} at ${surgery.facility}.`
                  : 'Pick an open date once your facility is set. Our coordinator will reach out if anything needs your attention.'}
                cta="See dates"
                to={`/portal/s/${sid}/schedule`}
                disabled={balance > 0}
              />
            )}
            {labsMs && !labsDone && surgeryDate && (
              <ActionCard
                icon={Stethoscope}
                tone="emerald"
                title="Pre-op labs window"
                meta="4–7 days before your surgery"
                body="Visit any Quest, LabCorp, or hospital lab in this window. Results come back to us automatically."
                cta="View instructions"
                to={`/portal/s/${sid}/documents`}
              />
            )}
            {next_action && balance === 0 && !consentPending && !(scheduleMs && !DONE_STATES.has(scheduleMs.status)) && (
              <ActionCard
                icon={HeartHandshake}
                tone="emerald"
                title="You're all set"
                meta="Next step"
                body={next_action.label}
                cta="Open"
                to={`/portal/s/${sid}/documents`}
              />
            )}
            {balance === 0 && !consentPending && !scheduleMs && !labsMs && (
              <ActionCard
                icon={HeartHandshake}
                tone="emerald"
                title="Everything is up to date"
                meta="Nothing to do right now"
                body="We'll reach out by text or email as soon as something needs your attention."
                cta="Open documents"
                to={`/portal/s/${sid}/documents`}
              />
            )}
          </div>
        </section>

        {/* Quick facts */}
        <section className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-10">
          {surgery.surgeon && (
            <FactCard icon={Stethoscope} label="Your surgeon" value={surgery.surgeon} />
          )}
          {surgery.procedure && (
            <FactCard icon={Flower2} label="Procedure" value={surgery.procedure} />
          )}
          {surgery.facility && (
            <FactCard icon={CalendarDays} label="Facility" value={surgery.facility} />
          )}
        </section>

        {/* Self-report row — kept here for visibility, but on labs/hospital
            milestones only. Hidden in staff-preview mode. */}
        {!isStaffPreview() && (
          <SelfReportRow milestones={mByKey} sid={sid}
                          onDone={() => qc.invalidateQueries({ queryKey: ['portal-dashboard', sid] })} />
        )}

        <div className="text-[11px] text-plum-600/70 text-center pt-6 mt-6 border-t border-plum-100">
          Need to talk to someone? Call <strong className="text-plum-700">240-252-2140</strong>
          {' '}or send a message — we usually reply the same day.
        </div>
      </div>
    </div>
  )
}


function JourneyTimeline({ milestones }) {
  return (
    <div className="bg-white rounded-2xl border border-plum-100 p-4 md:p-6 shadow-sm
                     overflow-x-auto">
      <div className="flex items-center justify-between gap-2 min-w-[640px]">
        {JOURNEY.map((step, i) => {
          const m = milestones[step.key]
          const status = m?.status || 'todo'
          const isDone = DONE_STATES.has(status)
          const isCurrent = !isDone && (status === 'in_progress' || (i > 0 && DONE_STATES.has(milestones[JOURNEY[i-1].key]?.status)))
          const isUpcoming = !isDone && !isCurrent
          return (
            <div key={step.key} className="flex-1 flex items-center">
              <div className="flex flex-col items-center flex-1">
                <div className={`w-10 h-10 rounded-full grid place-items-center text-[12px] font-semibold transition ${
                  isDone
                    ? 'bg-plum-700 text-white shadow-lg shadow-plum-300/50'
                    : isCurrent
                      ? 'bg-white border-2 border-plum-700 text-plum-700 ring-4 ring-plum-100'
                      : 'bg-plum-50 border border-plum-100 text-plum-400'
                }`}>
                  {isDone ? '✓' : (i + 1)}
                </div>
                <div className={`text-[11px] mt-2 text-center ${
                  isUpcoming ? 'text-plum-400' : 'text-plum-700 font-medium'
                }`}>
                  {step.label}
                </div>
              </div>
              {i < JOURNEY.length - 1 && (
                <div className={`h-px flex-1 mt-[-22px] ${
                  DONE_STATES.has(milestones[JOURNEY[i+1].key]?.status) || isDone
                    ? 'bg-plum-300'
                    : 'bg-plum-100'
                }`} />
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}


const TONES = {
  rose:    { ring: 'border-rose-200',    icon: 'bg-rose-100 text-rose-700' },
  amber:   { ring: 'border-amber-200',   icon: 'bg-amber-100 text-amber-800' },
  sky:     { ring: 'border-sky-200',     icon: 'bg-sky-100 text-sky-700' },
  emerald: { ring: 'border-emerald-200', icon: 'bg-emerald-100 text-emerald-700' },
}

function ActionCard({ icon: Icon, tone, title, meta, body, cta, urgent, disabled, to }) {
  const t = TONES[tone] || TONES.sky
  const inner = (
    <div className={`relative bg-white rounded-2xl border ${t.ring} p-6 shadow-sm
                      hover:shadow-md transition group overflow-hidden h-full`}>
      {urgent && (
        <div className="absolute top-4 right-4 text-[9px] uppercase tracking-[0.18em]
                          text-rose-700 bg-rose-50 px-2 py-0.5 rounded-full font-semibold border border-rose-200">
          due now
        </div>
      )}
      <div className={`w-12 h-12 rounded-xl ${t.icon} grid place-items-center mb-4`}>
        <Icon size={20} />
      </div>
      <h3 className="font-serif text-[15px] md:text-[16px] text-plum-ink font-semibold tracking-tight">
        {title}
      </h3>
      <div className="text-[12px] text-plum-700 mt-0.5 font-medium tracking-wide">
        {meta}
      </div>
      <p className="text-[13px] text-plum-700/80 mt-3 leading-relaxed">
        {body}
      </p>
      <div className={`mt-5 inline-flex items-center gap-1.5 text-[12px] font-semibold tracking-wide ${
        disabled ? 'text-plum-400' : 'text-plum-700 group-hover:text-plum-900'
      }`}>
        {cta} <ChevronRight size={14} />
      </div>
    </div>
  )
  if (disabled || !to) return <div className={disabled ? 'opacity-60 cursor-not-allowed' : ''}>{inner}</div>
  return <Link to={to} className="block">{inner}</Link>
}


function FactCard({ icon: Icon, label, value }) {
  return (
    <div className="bg-white rounded-xl border border-plum-100 p-4 flex items-center gap-3">
      <div className="w-10 h-10 rounded-lg bg-plum-50 grid place-items-center text-plum-700 shrink-0">
        <Icon size={16} />
      </div>
      <div className="min-w-0">
        <div className="text-[10px] uppercase tracking-[0.16em] text-plum-600/70">
          {label}
        </div>
        <div className="text-[13px] text-plum-ink font-medium truncate">
          {value}
        </div>
      </div>
    </div>
  )
}


function SelfReportRow({ milestones, sid, onDone }) {
  const labs = milestones['labs']
  const hosp = milestones['hospital_preop']
  const todo = []
  if (labs && labs.status === 'todo') todo.push({ key: 'labs', label: 'Mark labs as done' })
  if (hosp && hosp.status === 'todo') todo.push({ key: 'hospital_preop', label: 'Mark hospital call as done' })
  if (todo.length === 0) return null
  return (
    <div className="bg-plum-50/80 border border-plum-100 rounded-xl p-4 flex flex-wrap items-center gap-3">
      <div className="flex items-center gap-2 text-[12px] text-plum-700">
        <HeartHandshake size={14} />
        <span className="font-medium">Update us</span>
      </div>
      {todo.map(t => (
        <SelfReportButton key={t.key} sid={sid} kind={t.key}
                            label={t.label} onDone={onDone} />
      ))}
    </div>
  )
}

function SelfReportButton({ sid, kind, label, onDone }) {
  const [busy, setBusy] = useState(false)
  async function click() {
    setBusy(true)
    try {
      const path = kind === 'labs'
        ? `/${sid}/self-report/labs`
        : `/${sid}/self-report/hospital-preop`
      await portalApi.post(path)
      onDone?.()
    } finally { setBusy(false) }
  }
  return (
    <button onClick={click} disabled={busy}
             className="text-[12px] text-plum-700 hover:text-plum-900 underline">
      {busy ? 'Saving…' : label}
    </button>
  )
}
