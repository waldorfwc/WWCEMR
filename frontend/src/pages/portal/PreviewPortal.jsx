import { useState } from 'react'
import {
  CalendarDays, ChevronRight, ClipboardCheck, CreditCard, FileText,
  Flower2, HeartHandshake, MessageSquare, Phone, Sparkles, Stethoscope,
} from 'lucide-react'

/**
 * /portal/preview — a public, no-auth styling preview of the redesigned
 * patient portal. Mock data only. Once the design is approved, the same
 * components get swapped into PortalShell + Dashboard for real.
 */

const MOCK = {
  patient: { first: 'Tameka', last: 'Simmons', chart: '37475' },
  facility: 'MedStar Southern Maryland Hospital Center',
  procedure: 'Robotic Hysterectomy',
  surgeon: 'Dr. Aryian Cooke, MD',
  date: 'July 18, 2026',
  balance: 1491.20,
  consent: { pending: 1, signed: 0 },
  labs: { window: 'Jul 11 – Jul 14, 2026' },
  unreadMessages: 0,
}

// Subtle film-grain texture (small SVG noise, plum-tinted). Sits over the
// gradient hero to give it a soft, premium feel without using photography.
const GRAIN_SVG = `data:image/svg+xml;utf8,${encodeURIComponent(
  `<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 200 200'>
     <filter id='n'>
       <feTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='2' seed='3'/>
       <feColorMatrix values='0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 .35 0'/>
     </filter>
     <rect width='100%' height='100%' filter='url(%23n)'/>
   </svg>`
)}`

const JOURNEY_STEPS = [
  { k: 'benefits', label: 'Benefits', state: 'done' },
  { k: 'pay',      label: 'Payment',  state: 'current' },
  { k: 'consent',  label: 'Consent',  state: 'pending' },
  { k: 'date',     label: 'Schedule', state: 'pending' },
  { k: 'preop',    label: 'Pre-op',   state: 'upcoming' },
  { k: 'surgery',  label: 'Surgery',  state: 'upcoming' },
  { k: 'postop',   label: 'Recovery', state: 'upcoming' },
]

const NAV = [
  { to: 'dashboard',  label: 'Dashboard', icon: HeartHandshake, active: true },
  { to: 'payments',   label: 'Payments',  icon: CreditCard,    badge: '$1,491' },
  { to: 'schedule',   label: 'Schedule',  icon: CalendarDays },
  { to: 'consent',    label: 'Consent',   icon: ClipboardCheck, badge: '1' },
  { to: 'documents',  label: 'Documents', icon: FileText },
  { to: 'messages',   label: 'Messages',  icon: MessageSquare },
]


export default function PreviewPortal() {
  return (
    <div className="min-h-screen bg-plum-50/40 text-plum-ink">
      <div className="flex">
        <Sidebar />
        <main className="flex-1 min-h-screen">
          <DashboardPreview />
        </main>
      </div>
      <PreviewBadge />
    </div>
  )
}


function Sidebar() {
  return (
    <aside className="w-72 shrink-0 bg-white border-r border-plum-100 min-h-screen
                       flex flex-col">
      <div className="px-6 pt-7 pb-6 border-b border-plum-100">
        <div className="flex items-start gap-3">
          <div className="w-12 h-12 rounded-full bg-plum-100 grid place-items-center text-plum-700 shrink-0">
            <Sparkles size={20} />
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-[0.18em] text-plum-600/70 font-medium">
              Waldorf Women's Care
            </div>
            <div className="font-serif text-[18px] leading-tight text-plum-ink font-semibold tracking-tight mt-0.5">
              Patient Portal
            </div>
            <div className="font-serif italic text-plum-600 text-[11px] -mt-0.5">
              & Aesthetics
            </div>
          </div>
        </div>
      </div>

      <div className="px-6 py-5 border-b border-plum-100">
        <div className="text-[10px] uppercase tracking-[0.16em] text-plum-600/70 mb-1">
          Care plan for
        </div>
        <div className="font-serif text-[18px] text-plum-ink leading-tight font-semibold">
          {MOCK.patient.first} {MOCK.patient.last}
        </div>
        <div className="text-[11px] text-plum-600/80 mt-1 font-mono">
          chart #{MOCK.patient.chart}
        </div>
        <div className="text-[11px] text-plum-700 mt-3">
          {MOCK.procedure}
        </div>
        <div className="text-[11px] text-plum-600/80 mt-0.5">
          {MOCK.facility}
        </div>
      </div>

      <nav className="flex-1 px-3 py-4 space-y-0.5">
        {NAV.map(item => {
          const Icon = item.icon
          return (
            <a key={item.to}
               href={`#${item.to}`}
               className={`flex items-center gap-3 px-3 py-2.5 rounded-lg text-[13px] transition-colors ${
                 item.active
                   ? 'bg-plum-100 text-plum-ink font-medium'
                   : 'text-plum-700/80 hover:bg-plum-50'
               }`}>
              <Icon size={16} className={item.active ? 'text-plum-700' : 'text-plum-400'} />
              <span className="flex-1">{item.label}</span>
              {item.badge && (
                <span className="text-[10px] bg-plum-700 text-white px-1.5 py-0.5 rounded-full font-semibold">
                  {item.badge}
                </span>
              )}
            </a>
          )
        })}
      </nav>

      <div className="px-6 pb-6">
        <div className="rounded-lg bg-plum-50 border border-plum-100 p-3">
          <div className="flex items-center gap-2 text-[11px] text-plum-700">
            <Phone size={12} />
            <span className="font-semibold">Need help?</span>
          </div>
          <div className="text-[11px] text-plum-600/80 mt-1 leading-relaxed">
            Call our office at <strong className="text-plum-ink">240-252-2140</strong> for
            any questions about your care plan.
          </div>
        </div>
        <button className="w-full mt-3 text-[11px] text-plum-600/70 hover:text-plum-700">
          Sign out
        </button>
      </div>
    </aside>
  )
}


function DashboardPreview() {
  return (
    <div>
      {/* Hero — soft plum gradient with film-grain texture, no photography */}
      <section className="relative h-72 overflow-hidden bg-plum-ink">
        {/* Diagonal plum gradient */}
        <div className="absolute inset-0 bg-gradient-to-br from-plum-900 via-plum-700 to-plum-400" />
        {/* Subtle radial highlight in top-right for soft depth */}
        <div className="absolute inset-0 opacity-60"
             style={{
               background: 'radial-gradient(60% 80% at 80% 20%, rgba(255,255,255,0.18), transparent 60%)',
             }} />
        {/* Film-grain texture overlay */}
        <div className="absolute inset-0 opacity-40 mix-blend-overlay pointer-events-none"
             style={{ backgroundImage: `url("${GRAIN_SVG}")`, backgroundSize: '180px 180px' }} />
        {/* Decorative monogram in the corner */}
        <div className="absolute top-8 right-10 font-serif italic text-white/15 text-[140px] leading-none select-none pointer-events-none">
          W
        </div>
        <div className="relative h-full flex flex-col justify-end px-10 pb-10">
          <div className="text-[11px] uppercase tracking-[0.22em] text-white/80 font-medium mb-2">
            Saturday · June 2
          </div>
          <h1 className="font-serif text-white text-[44px] leading-tight font-semibold tracking-tight">
            Welcome back, {MOCK.patient.first}.
          </h1>
          <p className="text-white/85 text-[15px] mt-1.5 max-w-xl">
            Your surgery is scheduled for <strong>{MOCK.date}</strong>. A few
            steps below to get you ready.
          </p>
        </div>
      </section>

      <div className="px-10 py-10 max-w-5xl">
        {/* Journey */}
        <section className="mb-12">
          <div className="flex items-baseline justify-between mb-5">
            <h2 className="font-serif text-[22px] text-plum-ink font-semibold tracking-tight">
              Your care journey
            </h2>
            <div className="text-[11px] uppercase tracking-[0.16em] text-plum-600/70">
              2 of 7 complete
            </div>
          </div>
          <JourneyTimeline />
        </section>

        {/* Action cards */}
        <section className="mb-14">
          <h2 className="font-serif text-[22px] text-plum-ink font-semibold tracking-tight mb-5">
            What's next
          </h2>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
            <ActionCard
              icon={CreditCard}
              tone="rose"
              title="Settle your balance"
              meta={`$${MOCK.balance.toFixed(2)} due`}
              body="Pay securely with your card, FSA, or HSA. Pre-payment is required before your surgery date can be scheduled."
              cta="Pay now"
              urgent
            />
            <ActionCard
              icon={ClipboardCheck}
              tone="amber"
              title="Sign your consent"
              meta={`${MOCK.consent.pending} envelope awaiting`}
              body="Review and electronically sign the consent forms for your procedure. Takes about three minutes."
              cta="Open envelope"
            />
            <ActionCard
              icon={CalendarDays}
              tone="sky"
              title="Pick your surgery date"
              meta="Available after payment"
              body="Once your balance is settled, you'll see open dates for your procedure with Dr. Cooke at MedStar."
              cta="See dates"
              disabled
            />
            <ActionCard
              icon={Stethoscope}
              tone="emerald"
              title="Pre-op labs window"
              meta={MOCK.labs.window}
              body="Visit any Quest, LabCorp, or hospital lab in this window. We'll receive the results automatically."
              cta="Find a lab"
            />
          </div>
        </section>

        {/* Quick facts row */}
        <section className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-10">
          <FactCard icon={Stethoscope} label="Your surgeon" value={MOCK.surgeon} />
          <FactCard icon={Flower2}     label="Procedure"    value={MOCK.procedure} />
          <FactCard icon={HeartHandshake} label="Coordinator" value="Ivonne Mayo" />
        </section>

        <div className="text-[11px] text-plum-600/70 text-center pt-4 border-t border-plum-100">
          Need to talk to someone? Call <strong className="text-plum-700">240-252-2140</strong>
          {' '}or message us in the portal — we usually reply the same day.
        </div>
      </div>
    </div>
  )
}


function JourneyTimeline() {
  return (
    <div className="bg-white rounded-2xl border border-plum-100 p-6 shadow-sm">
      <div className="flex items-center justify-between gap-2">
        {JOURNEY_STEPS.map((step, i) => (
          <div key={step.k} className="flex-1 flex items-center">
            <div className="flex flex-col items-center flex-1">
              <div className={`w-10 h-10 rounded-full grid place-items-center text-[12px] font-semibold transition ${
                step.state === 'done'
                  ? 'bg-plum-700 text-white shadow-lg shadow-plum-300/50'
                  : step.state === 'current'
                    ? 'bg-white border-2 border-plum-700 text-plum-700 ring-4 ring-plum-100'
                    : 'bg-plum-50 border border-plum-100 text-plum-400'
              }`}>
                {step.state === 'done' ? '✓' : (i + 1)}
              </div>
              <div className={`text-[11px] mt-2 ${
                step.state === 'upcoming' ? 'text-plum-400' : 'text-plum-700 font-medium'
              }`}>
                {step.label}
              </div>
            </div>
            {i < JOURNEY_STEPS.length - 1 && (
              <div className={`h-px flex-1 mt-[-22px] ${
                JOURNEY_STEPS[i + 1].state === 'upcoming'
                  ? 'bg-plum-100'
                  : 'bg-plum-300'
              }`} />
            )}
          </div>
        ))}
      </div>
    </div>
  )
}


const TONES = {
  rose:    { ring: 'border-rose-200',    icon: 'bg-rose-100 text-rose-700',         dot: 'bg-rose-500' },
  amber:   { ring: 'border-amber-200',   icon: 'bg-amber-100 text-amber-800',        dot: 'bg-amber-500' },
  sky:     { ring: 'border-sky-200',     icon: 'bg-sky-100 text-sky-700',            dot: 'bg-sky-500' },
  emerald: { ring: 'border-emerald-200', icon: 'bg-emerald-100 text-emerald-700',    dot: 'bg-emerald-500' },
}

function ActionCard({ icon: Icon, tone, title, meta, body, cta, urgent, disabled }) {
  const t = TONES[tone]
  return (
    <div className={`relative bg-white rounded-2xl border ${t.ring} p-6 shadow-sm
                      hover:shadow-md transition group overflow-hidden`}>
      {urgent && (
        <div className="absolute top-4 right-4 text-[9px] uppercase tracking-[0.18em]
                          text-rose-700 bg-rose-50 px-2 py-0.5 rounded-full font-semibold border border-rose-200">
          due now
        </div>
      )}
      <div className={`w-12 h-12 rounded-xl ${t.icon} grid place-items-center mb-4`}>
        <Icon size={20} />
      </div>
      <h3 className="font-serif text-[19px] text-plum-ink font-semibold tracking-tight">
        {title}
      </h3>
      <div className="text-[12px] text-plum-700 mt-0.5 font-medium tracking-wide">
        {meta}
      </div>
      <p className="text-[13px] text-plum-700/80 mt-3 leading-relaxed">
        {body}
      </p>
      <button
        disabled={disabled}
        className={`mt-5 inline-flex items-center gap-1.5 text-[12px] font-semibold tracking-wide
                    transition ${
          disabled
            ? 'text-plum-400 cursor-not-allowed'
            : 'text-plum-700 hover:text-plum-900'
        }`}>
        {cta} <ChevronRight size={14} />
      </button>
    </div>
  )
}


function FactCard({ icon: Icon, label, value }) {
  return (
    <div className="bg-white rounded-xl border border-plum-100 p-4 flex items-center gap-3">
      <div className="w-10 h-10 rounded-lg bg-plum-50 grid place-items-center text-plum-700">
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


function PreviewBadge() {
  const [open, setOpen] = useState(true)
  if (!open) return null
  return (
    <div className="fixed bottom-4 right-4 z-50">
      <div className="bg-plum-ink text-white text-[11px] px-3 py-2 rounded-lg
                       shadow-xl shadow-plum-900/40 flex items-center gap-3 max-w-sm">
        <Sparkles size={13} className="text-plum-300" />
        <span className="leading-snug">
          <strong>Patient portal redesign — preview</strong>
          <span className="text-white/70"> · all data is mock</span>
        </span>
        <button onClick={() => setOpen(false)}
                className="text-white/60 hover:text-white text-[14px] leading-none">
          ×
        </button>
      </div>
    </div>
  )
}
