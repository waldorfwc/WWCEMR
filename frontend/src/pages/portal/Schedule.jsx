import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useParams, Link } from 'react-router-dom'
import { portalApi } from '../../lib/portal-api'

function GateBanner({ gate, sid }) {
  return (
    <div className="bg-amber-50 border border-amber-200 rounded-lg p-4">
      <div className="text-sm text-amber-700 font-medium">
        Payment required before scheduling
      </div>
      <p className="text-sm text-gray-700 mt-1">{gate.reason}</p>
      <Link to={`/portal/s/${sid}/payments`}
            className="btn-primary mt-3 inline-block">
        Go to Payments
      </Link>
    </div>
  )
}

function BlockDayList({ days, onPick }) {
  if (!days?.length) {
    return (
      <div className="bg-white rounded-lg shadow p-4 text-sm text-gray-600">
        No open dates within the next 6 months. Please call our office at
        <a className="text-plum-700 underline ml-1" href="tel:2402522140">240-252-2140</a>.
      </div>
    )
  }
  return (
    <section className="bg-white rounded-lg shadow p-4">
      <h2 className="text-sm font-semibold text-gray-700 mb-3">
        Open dates
      </h2>
      <ul className="divide-y divide-gray-100">
        {days.map(d => (
          <li key={`${d.block_day_id}-${d.proposed_start_time}`}
              className="py-3 flex items-center justify-between">
            <div>
              <div className="text-sm font-medium text-gray-900">
                {d.weekday}, {d.block_date}
              </div>
              <div className="text-xs text-gray-500 mt-0.5">
                Arrive at {d.proposed_start_time} · {d.facility}
                {d.cases_already_booked > 0 ? ` · ${d.cases_already_booked} other case(s) that day` : ''}
              </div>
            </div>
            <button onClick={() => onPick(d)} className="btn-primary text-sm">
              Pick this date
            </button>
          </li>
        ))}
      </ul>
    </section>
  )
}

function ConfirmModal({ day, onConfirm, onCancel, busy }) {
  if (!day) return null
  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center p-4 z-50">
      <div className="bg-white rounded-lg shadow-lg p-5 max-w-sm w-full space-y-3">
        <h3 className="font-semibold text-gray-900">Confirm your surgery date</h3>
        <p className="text-sm text-gray-600">
          {day.weekday}, {day.block_date} at {day.proposed_start_time}<br />
          {day.facility}
        </p>
        <div className="flex justify-end gap-2 pt-2">
          <button onClick={onCancel} className="btn-secondary">Cancel</button>
          <button onClick={onConfirm} disabled={busy} className="btn-primary">
            {busy ? 'Booking…' : 'Confirm'}
          </button>
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

  if (isLoading) return <div className="text-sm text-gray-500">Loading…</div>
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold text-gray-900">Schedule</h1>
      {!data.gate.allowed ? (
        <GateBanner gate={data.gate} sid={sid} />
      ) : (
        <>
          <BlockDayList days={data.block_days} onPick={setPicked} />
          {err && <div className="text-sm text-red-600">{err}</div>}
        </>
      )}
      <ConfirmModal day={picked}
                       onCancel={() => setPicked(null)}
                       onConfirm={() => claim.mutate()}
                       busy={claim.isPending} />
    </div>
  )
}
