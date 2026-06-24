import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { Search } from 'lucide-react'
import api from '../utils/api'
import LoadingState from '../components/LoadingState'

export default function StaffInbox() {
  const nav = useNavigate()
  const [view, setView] = useState('unread')   // 'unread' | 'read'
  const [q, setQ] = useState('')

  const { data, isLoading } = useQuery({
    // Keep the unread/no-search key as ['staff-inbox'] so the badge and the
    // mark-read flow invalidate the same cache; read/search get their own keys.
    queryKey: view === 'unread' && !q.trim()
      ? ['staff-inbox']
      : ['staff-inbox', view, q.trim()],
    queryFn: () => api.get('/staff/messages/inbox', {
      params: { view, ...(q.trim() ? { q: q.trim() } : {}) },
    }).then(r => r.data),
    refetchInterval: 60_000,
    staleTime: 30_000,
  })

  const rows = data?.rows || []
  const Tab = ({ k, label }) => (
    <button
      onClick={() => setView(k)}
      className={`px-3 py-1.5 text-sm rounded-md ${
        view === k ? 'bg-plum-700 text-white' : 'text-gray-600 hover:bg-gray-100'
      }`}
    >
      {label}
    </button>
  )

  return (
    <div className="p-4 max-w-4xl">
      <h1 className="page-title mb-4">Messages</h1>
      <div className="flex items-center justify-between gap-3 mb-4 flex-wrap">
        <div className="flex items-center gap-1 bg-gray-50 rounded-lg p-1">
          <Tab k="unread" label="Unread" />
          <Tab k="read" label="Read" />
        </div>
        <div className="relative flex-1 min-w-[200px] max-w-xs">
          <Search size={14}
                  className="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-400" />
          <input
            value={q}
            onChange={e => setQ(e.target.value)}
            placeholder="Search name or chart #…"
            className="w-full text-sm rounded-md border-gray-300 pl-8"
          />
        </div>
      </div>

      {isLoading ? (
        <LoadingState />
      ) : rows.length === 0 ? (
        <div className="text-sm text-muted">
          {q.trim()
            ? 'No threads match your search.'
            : view === 'unread'
              ? 'No unread patient messages.'
              : 'No read threads.'}
        </div>
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
