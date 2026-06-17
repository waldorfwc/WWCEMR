import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { CalendarDays, Clock, MapPin, CheckCircle2, AlertCircle, X } from 'lucide-react'
import { pelletPortalApi } from '../../lib/pellet-portal-api'

const LOCATION_LABELS = {
  white_plains: 'White Plains',
  brandywine:   'Brandywine',
  arlington:    'Arlington',
}

function locationLabel(loc) {
  return LOCATION_LABELS[loc] || loc
}

function mutationErr(e, fallback) {
  const detail = e?.response?.data?.detail
  if (typeof detail === 'string') return detail
  return fallback
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

// Render a HH:MM[:SS] time string as HH:MM (24h, zero-padded).
function fmtTime(val) {
  if (!val) return ''
  const m = String(val).match(/^(\d{1,2}):(\d{2})/)
  if (m) return `${m[1].padStart(2, '0')}:${m[2]}`
  return String(val)
}

export default function PelletSchedule() {
  const qc = useQueryClient()
  const [location, setLocation] = useState(null)
  const [err, setErr] = useState('')

  const locationsQ = useQuery({
    queryKey: ['pellet-sched-locations'],
    queryFn: () => pelletPortalApi.get('/schedule/locations').then(r => r.data),
    staleTime: 60_000,
  })
  const myQ = useQuery({
    queryKey: ['pellet-sched-my'],
    queryFn: () => pelletPortalApi.get('/schedule/my').then(r => r.data),
    staleTime: 30_000,
  })
  const slotsQ = useQuery({
    queryKey: ['pellet-sched-slots', location],
    queryFn: () => pelletPortalApi.get('/schedule/slots', { params: { location } }).then(r => r.data),
    enabled: !!location,
    staleTime: 15_000,
  })

  const invalidateAll = () => {
    qc.invalidateQueries({ queryKey: ['pellet-sched-my'] })
    qc.invalidateQueries({ queryKey: ['pellet-sched-slots'] })
    qc.invalidateQueries({ queryKey: ['pellet-dashboard'] })
  }

  const book = useMutation({
    mutationFn: (id) => pelletPortalApi.post(`/schedule/slots/${id}/book`).then(r => r.data),
    onSuccess: invalidateAll,
    onError: (e) => setErr(mutationErr(e, 'We couldn’t book that time. It may have just been taken. Please try another, or call our office at 240-252-2140.')),
  })
  const cancel = useMutation({
    mutationFn: (id) => pelletPortalApi.post(`/schedule/slots/${id}/cancel`).then(r => r.data),
    onSuccess: invalidateAll,
    onError: (e) => setErr(mutationErr(e, 'We couldn’t cancel that booking. Please call our office at 240-252-2140.')),
  })

  if (locationsQ.isLoading) {
    return <div className="py-16 text-center text-plum-600/70 text-sm">Loading scheduling…</div>
  }
  if (locationsQ.error) {
    return (
      <div className="bg-rose-50 border border-rose-200 rounded-lg p-4 text-rose-800 text-sm">
        We couldn't load scheduling right now. Please refresh, or call
        our office at <strong>240-252-2140</strong>.
      </div>
    )
  }

  const locations = locationsQ.data?.locations || []
  const myBookings = myQ.data?.items || []
  const slots = slotsQ.data?.items || []
  const canSchedule = slotsQ.data?.can_schedule
  const reason = slotsQ.data?.reason

  // Group open slots by date, preserving date order.
  const grouped = []
  const byDate = new Map()
  for (const s of [...slots].sort((a, b) => {
    if (a.slot_date !== b.slot_date) return a.slot_date < b.slot_date ? -1 : 1
    return (a.start_time || '') < (b.start_time || '') ? -1 : 1
  })) {
    if (!byDate.has(s.slot_date)) {
      const bucket = { date: s.slot_date, items: [] }
      byDate.set(s.slot_date, bucket)
      grouped.push(bucket)
    }
    byDate.get(s.slot_date).items.push(s)
  }

  return (
    <div>
      <header className="mb-6">
        <div className="text-[11px] uppercase tracking-[0.22em] text-plum-600/70 font-medium mb-2">
          Pellet Portal
        </div>
        <h1 className="font-serif text-[24px] md:text-[30px] text-plum-ink font-semibold tracking-tight leading-tight">
          Schedule Your Insertion
        </h1>
        <p className="text-[13px] md:text-[14px] text-plum-700/80 mt-2 max-w-xl">
          Choose a location and pick an open time for your pellet insertion.
        </p>
      </header>

      {/* My upcoming bookings */}
      {myBookings.length > 0 && (
        <section className="bg-white rounded-2xl border border-plum-100 shadow-sm p-5 mb-4">
          <h2 className="font-serif text-[16px] text-plum-ink font-semibold leading-tight mb-3">
            My Upcoming
          </h2>
          <div className="space-y-2">
            {myBookings.map(b => (
              <div key={b.slot_id}
                   className="flex items-center gap-3 rounded-xl border border-plum-100 bg-plum-50/40 px-4 py-3">
                <div className="w-9 h-9 rounded-lg bg-plum-50 grid place-items-center text-plum-700 shrink-0">
                  <CalendarDays size={16} />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="text-[14px] text-plum-ink font-semibold leading-tight">
                    {fmtDate(b.slot_date)} · {fmtTime(b.start_time)}
                  </div>
                  <div className="text-[12px] text-plum-700/80 mt-0.5 flex items-center gap-1">
                    <MapPin size={12} /> {locationLabel(b.location)}
                  </div>
                </div>
                <button onClick={() => { setErr(''); cancel.mutate(b.slot_id) }}
                        disabled={cancel.isPending}
                        className="inline-flex items-center gap-1 text-[12px] font-semibold text-rose-700
                                   hover:text-rose-900 disabled:opacity-50 shrink-0">
                  <X size={14} /> Cancel
                </button>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Location picker */}
      <section className="bg-white rounded-2xl border border-plum-100 shadow-sm p-5 mb-4">
        <h2 className="font-serif text-[16px] text-plum-ink font-semibold leading-tight mb-3">
          Choose A Location
        </h2>
        <div className="flex flex-wrap gap-2">
          {locations.map(loc => (
            <button key={loc} onClick={() => { setErr(''); setLocation(loc) }}
                    className={`px-4 py-2 rounded-lg text-sm border transition ${
                      location === loc
                        ? 'bg-plum-700 text-white border-plum-700'
                        : 'bg-white text-plum-700 border-plum-200 hover:border-plum-400'}`}>
              {locationLabel(loc)}
            </button>
          ))}
        </div>
      </section>

      {/* Slots for the selected location */}
      {location && (
        <section className="bg-white rounded-2xl border border-plum-100 shadow-sm p-5 mb-4">
          <h2 className="font-serif text-[16px] text-plum-ink font-semibold leading-tight mb-3">
            Open Times · {locationLabel(location)}
          </h2>

          {/* Gate banner */}
          {slotsQ.data && !canSchedule && (
            <div className="mb-4 flex items-start gap-2 text-[13px] text-amber-900 bg-amber-50
                            border border-amber-200 rounded-lg px-4 py-3">
              <AlertCircle size={16} className="shrink-0 mt-0.5" />
              <div>
                <div>{reason || 'You can’t schedule yet. Please finish the remaining steps first.'}</div>
                <Link to="/pellet-portal/home"
                      className="inline-block mt-1 font-semibold underline text-amber-900 hover:text-amber-950">
                  Go To Checklist
                </Link>
              </div>
            </div>
          )}

          {slotsQ.isLoading ? (
            <div className="py-8 text-center text-plum-600/70 text-sm">Loading open times…</div>
          ) : grouped.length === 0 ? (
            <div className="py-8 text-center text-plum-600/70 text-sm">
              No open times at this location right now. Please check back, or call
              our office at <strong className="text-plum-700">240-252-2140</strong>.
            </div>
          ) : (
            <div className="space-y-5">
              {grouped.map(group => (
                <div key={group.date}>
                  <div className="text-[12px] uppercase tracking-wide text-plum-600/70 font-semibold mb-2">
                    {fmtDate(group.date)}
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {group.items.map(slot => (
                      <button key={slot.id}
                              onClick={() => { setErr(''); book.mutate(slot.id) }}
                              disabled={!canSchedule || book.isPending}
                              className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm border
                                         border-plum-200 text-plum-700 hover:border-plum-400 hover:bg-plum-50
                                         disabled:opacity-50 disabled:cursor-not-allowed transition">
                        <Clock size={14} />
                        {fmtTime(slot.start_time)}
                      </button>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>
      )}

      {book.isSuccess && (
        <div className="mb-4 flex items-center gap-2 text-[14px] text-emerald-800 bg-emerald-50
                        border border-emerald-200 rounded-lg px-4 py-3">
          <CheckCircle2 size={16} /> Your insertion is booked. See "My Upcoming" above.
        </div>
      )}
      {err && <div className="text-sm text-rose-700 mb-4">{err}</div>}

      <div className="mt-2">
        <Link to="/pellet-portal/home"
              className="text-[12px] text-plum-700 hover:text-plum-900 underline">
          Back to Checklist
        </Link>
      </div>

      <div className="text-[11px] text-plum-600/70 text-center pt-6 mt-6 border-t border-plum-100">
        Need help? Call our office at <strong className="text-plum-700">240-252-2140</strong>.
      </div>
    </div>
  )
}
