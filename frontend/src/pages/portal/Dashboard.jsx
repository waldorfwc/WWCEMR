import { useQuery } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'
import { portalApi } from '../../lib/portal-api'

const STATUS_BADGE = {
  done:         'bg-green-100 text-green-700',
  in_progress:  'bg-amber-100 text-amber-700',
  todo:         'bg-gray-200 text-gray-700',
  not_required: 'bg-gray-100 text-gray-500',
}

const STATUS_LABEL = {
  done: '✓ Done',
  in_progress: '… In progress',
  todo: 'Not started',
  not_required: 'Not required',
}

export default function Dashboard() {
  const { sid } = useParams()
  const { data, isLoading, error } = useQuery({
    queryKey: ['portal-dashboard', sid],
    queryFn: () => portalApi.get(`/${sid}/dashboard`).then(r => r.data),
    staleTime: 30_000,
  })
  if (isLoading) return <div className="text-sm text-gray-500">Loading…</div>
  if (error) return <div className="text-sm text-red-600">Couldn't load your dashboard.</div>
  const { surgery, milestones, next_action } = data
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold text-gray-900">Your surgery</h1>

      {next_action && (
        <div className="bg-plum-50 border border-plum-200 rounded-lg p-4">
          <div className="text-xs uppercase tracking-wide text-plum-700">Next step</div>
          <div className="text-base font-medium text-gray-900 mt-1">
            {next_action.label}
          </div>
        </div>
      )}

      <section className="bg-white rounded-lg shadow p-4">
        <h2 className="text-sm font-semibold text-gray-700 mb-2">Surgery details</h2>
        <dl className="grid grid-cols-2 gap-y-2 text-sm">
          <dt className="text-gray-500">Procedure</dt>
          <dd className="text-gray-900">{surgery.procedure || '—'}</dd>
          <dt className="text-gray-500">Surgeon</dt>
          <dd className="text-gray-900">{surgery.surgeon || '—'}</dd>
          <dt className="text-gray-500">Date</dt>
          <dd className="text-gray-900">{surgery.surgery_date || 'not scheduled yet'}</dd>
          <dt className="text-gray-500">Arrival time</dt>
          <dd className="text-gray-900">{surgery.surgery_time || 'TBD'}</dd>
          <dt className="text-gray-500">Location</dt>
          <dd className="text-gray-900">{surgery.facility || 'TBD'}</dd>
          <dt className="text-gray-500">Patient responsibility</dt>
          <dd className="text-gray-900">
            {surgery.patient_responsibility != null
              ? `$${surgery.patient_responsibility.toFixed(2)}`
              : 'calculating'}
          </dd>
        </dl>
      </section>

      <section className="bg-white rounded-lg shadow p-4">
        <h2 className="text-sm font-semibold text-gray-700 mb-3">Your progress</h2>
        <ul className="divide-y divide-gray-100">
          {milestones.map(m => (
            <li key={m.key} className="flex items-center justify-between py-2">
              <span className="text-sm text-gray-800">{m.label}</span>
              <span className={`text-xs px-2 py-1 rounded ${STATUS_BADGE[m.status] || STATUS_BADGE.todo}`}>
                {STATUS_LABEL[m.status] || m.status}
              </span>
            </li>
          ))}
        </ul>
      </section>
    </div>
  )
}
