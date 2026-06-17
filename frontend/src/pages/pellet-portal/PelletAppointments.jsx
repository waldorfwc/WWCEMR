import { useQuery } from '@tanstack/react-query'
import { CalendarDays, MapPin, User } from 'lucide-react'
import { pelletPortalApi } from '../../lib/pellet-portal-api'

const LOCATION_LABELS = {
  white_plains: 'White Plains',
  brandywine:   'Brandywine',
  arlington:    'Arlington',
}

function locationLabel(loc) {
  if (!loc) return '—'
  return LOCATION_LABELS[loc] || loc
}

// Render a YYYY-MM-DD (or ISO) date as MM/DD/YYYY in local time without the
// negative-UTC-offset day-slip bug.
function fmtDate(val) {
  if (!val) return ''
  const m = String(val).match(/^(\d{4})-(\d{2})-(\d{2})/)
  if (m) {
    const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]))
    return d.toLocaleDateString('en-US', { month: '2-digit', day: '2-digit', year: 'numeric' })
  }
  const d = new Date(val)
  return Number.isNaN(d.getTime()) ? String(val) : d.toLocaleDateString('en-US', { month: '2-digit', day: '2-digit', year: 'numeric' })
}

function statusChipClass(status) {
  const s = String(status || '').toLowerCase()
  if (s.includes('cancel')) return 'bg-rose-50 text-rose-700 border-rose-200'
  if (s.includes('complete') || s.includes('inserted')) return 'bg-emerald-50 text-emerald-700 border-emerald-200'
  return 'bg-plum-50 text-plum-700 border-plum-200'
}

export default function PelletAppointments() {
  const apptsQ = useQuery({
    queryKey: ['pellet-appts'],
    queryFn: () => pelletPortalApi.get('/appointments').then(r => r.data),
    staleTime: 30_000,
  })

  const items = apptsQ.data?.items || []

  return (
    <div>
      <header className="mb-6">
        <div className="text-[11px] uppercase tracking-[0.22em] text-plum-600/70 font-medium mb-2">
          Pellet Portal
        </div>
        <h1 className="font-serif text-[24px] md:text-[30px] text-plum-ink font-semibold tracking-tight leading-tight">
          Appointments
        </h1>
        <p className="text-[13px] md:text-[14px] text-plum-700/80 mt-2 max-w-xl">
          Your pellet insertion visit history.
        </p>
      </header>

      {apptsQ.isLoading ? (
        <div className="py-16 text-center text-plum-600/70 text-sm">Loading appointments…</div>
      ) : apptsQ.error ? (
        <div className="bg-rose-50 border border-rose-200 rounded-lg p-4 text-rose-800 text-sm">
          We couldn't load your appointments right now. Please refresh, or call
          our office at <strong>240-252-2140</strong>.
        </div>
      ) : items.length === 0 ? (
        <div className="bg-white rounded-2xl border border-plum-100 shadow-sm p-8 text-center text-plum-600/70 text-sm">
          No appointments yet.
        </div>
      ) : (
        <div className="space-y-3">
          {items.map(a => {
            const dosage = (a.doses || []).map(d => `${d.label} ×${d.quantity}`).join(', ') || '—'
            return (
              <section key={a.id}
                       className="bg-white rounded-2xl border border-plum-100 shadow-sm p-5">
                <div className="flex items-start justify-between gap-3 flex-wrap">
                  <div className="flex items-center gap-3 min-w-0">
                    <div className="w-9 h-9 rounded-lg bg-plum-50 grid place-items-center text-plum-700 shrink-0">
                      <CalendarDays size={16} />
                    </div>
                    <div className="min-w-0">
                      <div className="text-[15px] text-plum-ink font-semibold leading-tight">
                        {fmtDate(a.scheduled_date) || '—'}
                      </div>
                      {a.visit_kind && (
                        <div className="text-[12px] text-plum-700/70 mt-0.5">{a.visit_kind}</div>
                      )}
                    </div>
                  </div>
                  {a.status && (
                    <span className={`inline-flex items-center text-[11px] font-semibold uppercase tracking-wide
                                      px-2 py-0.5 rounded-full border ${statusChipClass(a.status)}`}>
                      {a.status}
                    </span>
                  )}
                </div>
                <div className="mt-4 grid grid-cols-1 sm:grid-cols-3 gap-3 text-[13px]">
                  <div className="flex items-center gap-1.5 text-plum-700/80">
                    <MapPin size={13} className="shrink-0" /> {locationLabel(a.location)}
                  </div>
                  <div className="flex items-center gap-1.5 text-plum-700/80">
                    <User size={13} className="shrink-0" /> {a.provider || '—'}
                  </div>
                  <div className="text-plum-700/80">
                    <span className="text-plum-600/70">Dosage: </span>{dosage}
                  </div>
                </div>
              </section>
            )
          })}
        </div>
      )}

      <div className="text-[11px] text-plum-600/70 text-center pt-6 mt-6 border-t border-plum-100">
        Need help? Call our office at <strong className="text-plum-700">240-252-2140</strong>.
      </div>
    </div>
  )
}
