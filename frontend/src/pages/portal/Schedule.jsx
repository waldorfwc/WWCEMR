import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useParams, Link } from 'react-router-dom'
import { CalendarDays, Lock, MapPin, Clock } from 'lucide-react'
import { portalApi, isStaffPreview } from '../../lib/portal-api'


function GateBanner({ gate, sid }) {
  return (
    <div className="bg-amber-50 border border-amber-200 rounded-2xl p-6 shadow-sm">
      <div className="flex items-center gap-2 text-amber-800 text-[12px] font-semibold uppercase tracking-[0.16em]">
        <Lock size={14} /> Payment required
      </div>
      <p className="text-[14px] text-plum-700/90 mt-3">{gate.reason}</p>
      <Link to={`/portal/s/${sid}/payments`} className="btn-primary mt-5 inline-block">
        Go to Payments
      </Link>
    </div>
  )
}


function BlockDayList({ days, onPick }) {
  if (!days?.length) {
    return (
      <div className="bg-white rounded-2xl border border-plum-100 p-6 shadow-sm text-[13px] text-plum-700/90">
        No open dates within the next 6 months. Please call our office at
        {' '}<a href="tel:2402522140" className="text-plum-700 font-semibold underline">240-252-2140</a>
        {' '}so we can find a date that works for you.
      </div>
    )
  }
  return (
    <ul className="space-y-3">
      {days.map(d => (
        <li key={`${d.block_day_id}-${d.proposed_start_time}`}
            className="bg-white rounded-2xl border border-plum-100 p-5 shadow-sm
                          hover:shadow-md transition flex items-start justify-between gap-4">
          <div className="flex items-start gap-4 min-w-0 flex-1">
            <div className="w-12 h-12 rounded-xl bg-plum-50 grid place-items-center text-plum-700 shrink-0">
              <CalendarDays size={20} />
            </div>
            <div className="min-w-0">
              <div className="font-serif text-[15px] text-plum-ink font-semibold leading-tight">
                {d.weekday}, {d.block_date}
              </div>
              <div className="text-[12px] text-plum-700/80 mt-1 flex flex-wrap items-center gap-x-3 gap-y-1">
                <span className="inline-flex items-center gap-1">
                  <Clock size={11} /> Arrive at {d.proposed_start_time}
                </span>
                <span className="inline-flex items-center gap-1">
                  <MapPin size={11} /> {d.facility}
                </span>
                {d.cases_already_booked > 0 && (
                  <span className="text-plum-600/70">
                    · {d.cases_already_booked} other case{d.cases_already_booked === 1 ? '' : 's'} that day
                  </span>
                )}
              </div>
            </div>
          </div>
          {!isStaffPreview() && (
            <button onClick={() => onPick(d)}
                    className="btn-primary text-sm shrink-0">
              Pick this date
            </button>
          )}
        </li>
      ))}
    </ul>
  )
}


function ConfirmModal({ day, onConfirm, onCancel, busy }) {
  if (!day) return null
  return (
    <div className="fixed inset-0 bg-plum-900/40 flex items-center justify-center p-4 z-50">
      <div className="bg-white rounded-2xl shadow-xl p-6 max-w-sm w-full">
        <div className="text-[10px] uppercase tracking-[0.2em] text-plum-600/70 mb-1">
          Confirm
        </div>
        <h3 className="font-serif text-[14px] text-plum-ink font-semibold tracking-tight">
          Lock in your surgery date
        </h3>
        <div className="bg-plum-50/60 rounded-xl p-4 mt-4 space-y-1">
          <div className="text-[14px] text-plum-ink font-medium">
            {day.weekday}, {day.block_date}
          </div>
          <div className="text-[12px] text-plum-700/80 flex items-center gap-1">
            <Clock size={11} /> Arrive at {day.proposed_start_time}
          </div>
          <div className="text-[12px] text-plum-700/80 flex items-center gap-1">
            <MapPin size={11} /> {day.facility}
          </div>
        </div>
        <p className="text-[12px] text-plum-700/80 mt-4">
          Our coordinator will email you full pre-op instructions once your
          date is confirmed.
        </p>
        <div className="flex justify-end gap-2 mt-5">
          <button onClick={onCancel} className="btn-secondary">Cancel</button>
          {!isStaffPreview() && (
            <button onClick={onConfirm} disabled={busy} className="btn-primary">
              {busy ? 'Booking…' : 'Confirm date'}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}


export default function Schedule() {
  const { sid } = useParams()
  const qc = useQueryClient()
  const [picked, setPicked] = useState(null)
  const [err, setErr] = useState('')

  const { data, isLoading } = useQuery({
    queryKey: ['portal-slots', sid],
    queryFn: () => portalApi.get(`/${sid}/slots`).then(r => r.data),
    staleTime: 30_000,
  })

  const claim = useMutation({
    mutationFn: () => portalApi.post(
      `/${sid}/slots/${picked.block_day_id}/claim`,
      { start_time: picked.proposed_start_time },
    ).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['portal-dashboard', sid] })
      qc.invalidateQueries({ queryKey: ['portal-slots', sid] })
      setPicked(null)
    },
    onError: (e) => setErr(e?.response?.data?.detail || 'Could not book.'),
  })

  if (isLoading) {
    return (
      <div className="px-6 md:px-10 py-16 text-plum-600/70 text-sm">
        Loading open dates…
      </div>
    )
  }

  return (
    <div className="px-6 md:px-10 py-8 md:py-10 max-w-5xl">
      <header className="mb-8">
        <div className="text-[11px] uppercase tracking-[0.22em] text-plum-600/70 font-medium mb-2">
          Patient portal
        </div>
        <h1 className="font-serif text-[24px] md:text-[30px] text-plum-ink font-semibold tracking-tight leading-tight">
          Pick your date
        </h1>
        <p className="text-[13px] md:text-[14px] text-plum-700/80 mt-2 max-w-xl">
          Choose any open date below. Once you confirm, our coordinator will
          send your full pre-op packet.
        </p>
      </header>

      {!data.gate.allowed ? (
        <GateBanner gate={data.gate} sid={sid} />
      ) : (
        <>
          <BlockDayList days={data.block_days} onPick={setPicked} />
          {err && (
            <div className="text-sm text-rose-700 bg-rose-50 border border-rose-200 rounded-xl p-3 mt-4">
              {err}
            </div>
          )}
        </>
      )}
      <ConfirmModal day={picked}
                     onCancel={() => setPicked(null)}
                     onConfirm={() => claim.mutate()}
                     busy={claim.isPending} />
    </div>
  )
}
