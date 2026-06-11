import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import api from '../utils/api'
import LoadingState from '../components/LoadingState'

export default function StaffInbox() {
  const nav = useNavigate()
  const { data, isLoading } = useQuery({
    queryKey: ['staff-inbox'],
    queryFn: () => api.get('/staff/messages/inbox').then(r => r.data),
    refetchInterval: 60_000,
    staleTime: 30_000,
  })
  if (isLoading) return <LoadingState />
  const rows = data?.rows || []
  return (
    <div className="p-4 max-w-4xl">
      <h1 className="page-title mb-4">Messages</h1>
      {rows.length === 0 ? (
        <div className="text-sm text-muted">No unread patient messages.</div>
      ) : (
        <div className="bg-white rounded-lg shadow divide-y divide-gray-100">
          {rows.map(r => (
            <button
              key={r.surgery_id}
              onClick={() => nav(`/surgery/${r.surgery_id}#messages`)}
              className="w-full text-left px-4 py-3 hover:bg-gray-50
                          flex items-start justify-between gap-3"
            >
              <div className="min-w-0 flex-1">
                <div className="font-medium text-gray-900">{r.patient_name}</div>
                <div className="text-xs text-muted">Chart #{r.chart_number}</div>
                <div className="text-sm text-gray-700 mt-1 truncate">
                  {r.last_body}
                </div>
              </div>
              <div className="text-xs text-muted shrink-0">
                {r.last_sent_at?.slice(0, 16).replace('T', ' ')}
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
