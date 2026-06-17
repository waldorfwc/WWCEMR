import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import {
  CheckCircle2, ChevronRight, Clock, Image, ClipboardCheck,
  FlaskConical, CreditCard, CalendarDays, Lock,
} from 'lucide-react'
import { pelletPortalApi } from '../../lib/pellet-portal-api'

// Requirement key → presentation metadata (icon + sub-page route).
const REQ_META = {
  mammo:   { icon: Image,          to: 'mammo',   title: 'Mammogram' },
  labs:    { icon: FlaskConical,   to: 'labs',    title: 'Labs' },
  consent: { icon: ClipboardCheck, to: 'consent', title: 'Insertion Consent' },
}

function StatusChip({ status }) {
  if (status === 'done') {
    return (
      <span className="inline-flex items-center gap-1 text-[11px] font-semibold uppercase tracking-wide
                         px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-200">
        <CheckCircle2 size={12} /> Done
      </span>
    )
  }
  if (status === 'pending') {
    return (
      <span className="inline-flex items-center gap-1 text-[11px] font-semibold uppercase tracking-wide
                         px-2 py-0.5 rounded-full bg-amber-50 text-amber-800 border border-amber-200">
        <Clock size={12} /> Submitted · Awaiting Review
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-1 text-[11px] font-semibold uppercase tracking-wide
                       px-2 py-0.5 rounded-full bg-plum-50 text-plum-600 border border-plum-100">
      To Do
    </span>
  )
}

function RequirementRow({ req }) {
  const meta = REQ_META[req.key] || { icon: ClipboardCheck, to: '', title: req.label }
  const Icon = meta.icon
  const actionable = req.status !== 'done'

  const inner = (
    <div className={`bg-white rounded-2xl border border-plum-100 p-5 shadow-sm flex items-center gap-4
                      ${actionable ? 'hover:shadow-md transition group' : ''}`}>
      <div className="w-11 h-11 rounded-xl bg-plum-50 grid place-items-center text-plum-700 shrink-0">
        <Icon size={18} />
      </div>
      <div className="min-w-0 flex-1">
        <div className="font-serif text-[15px] text-plum-ink font-semibold leading-tight">
          {req.label || meta.title}
        </div>
        <div className="mt-1.5">
          <StatusChip status={req.status} />
        </div>
      </div>
      {actionable && (
        <div className="inline-flex items-center gap-1 text-[12px] font-semibold text-plum-700
                          group-hover:text-plum-900 shrink-0">
          {req.status === 'pending' ? 'View' : 'Start'} <ChevronRight size={14} />
        </div>
      )}
    </div>
  )

  if (!actionable) return inner
  return <Link to={meta.to} className="block">{inner}</Link>
}

function LockedRow({ icon: Icon, title }) {
  return (
    <div className="bg-white/60 rounded-2xl border border-plum-100 p-5 shadow-sm flex items-center gap-4 opacity-70">
      <div className="w-11 h-11 rounded-xl bg-plum-50 grid place-items-center text-plum-400 shrink-0">
        <Icon size={18} />
      </div>
      <div className="min-w-0 flex-1">
        <div className="font-serif text-[15px] text-plum-500 font-semibold leading-tight">
          {title}
        </div>
        <div className="mt-1.5">
          <span className="inline-flex items-center gap-1 text-[11px] font-semibold uppercase tracking-wide
                             px-2 py-0.5 rounded-full bg-plum-50 text-plum-400 border border-plum-100">
            <Lock size={11} /> Coming Soon
          </span>
        </div>
      </div>
    </div>
  )
}

export default function PelletDashboard() {
  const { data, isLoading, error } = useQuery({
    queryKey: ['pellet-dashboard'],
    queryFn: () => pelletPortalApi.get('/dashboard').then(r => r.data),
    staleTime: 30_000,
  })

  if (isLoading) {
    return <div className="py-16 text-center text-plum-600/70 text-sm">Loading your checklist…</div>
  }
  if (error) {
    return (
      <div className="bg-rose-50 border border-rose-200 rounded-lg p-4 text-rose-800 text-sm">
        We couldn't load your checklist right now. Please refresh, or call
        our office at <strong>240-252-2140</strong>.
      </div>
    )
  }

  const { patient, requirements = [] } = data
  const name = patient?.patient_name?.split(',').reverse().join(' ').trim() || 'there'

  return (
    <div>
      <header className="mb-6">
        <div className="text-[11px] uppercase tracking-[0.22em] text-plum-600/70 font-medium mb-2">
          Pellet Portal
        </div>
        <h1 className="font-serif text-[24px] md:text-[30px] text-plum-ink font-semibold tracking-tight leading-tight">
          Welcome, {name}.
        </h1>
        {patient?.chart_number && (
          <div className="text-[11px] text-plum-600/80 mt-1 font-mono">
            chart #{patient.chart_number}
          </div>
        )}
        <p className="text-[13px] md:text-[14px] text-plum-700/80 mt-3 max-w-xl">
          Complete the steps below so we can get you ready for your pellet
          insertion. We'll review each item as you submit it.
        </p>
      </header>

      <section className="space-y-3">
        <h2 className="font-serif text-[14px] md:text-[15px] text-plum-ink font-semibold tracking-tight mb-1">
          Your Requirements
        </h2>
        {requirements.map(req => (
          <RequirementRow key={req.key} req={req} />
        ))}

        <LockedRow icon={CreditCard} title="Payment" />
        <LockedRow icon={CalendarDays} title="Scheduling" />
      </section>

      <div className="text-[11px] text-plum-600/70 text-center pt-6 mt-6 border-t border-plum-100">
        Need help? Call our office at <strong className="text-plum-700">240-252-2140</strong>.
      </div>
    </div>
  )
}
