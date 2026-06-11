import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Mail, Download, Check } from 'lucide-react'
import api, { fmt, statusColors } from '../utils/api'
import EmptyState from '../components/EmptyState'

export default function Appeals() {
  const qc = useQueryClient()
  const [selected, setSelected] = useState(null)

  const { data: appeals, isLoading } = useQuery({
    queryKey: ['appeals'],
    queryFn: () => api.get('/appeals').then(r => r.data),
  })

  const { data: detail } = useQuery({
    queryKey: ['appeal', selected],
    queryFn: () => selected ? api.get(`/appeals/${selected}`).then(r => r.data) : null,
    enabled: !!selected,
  })

  const markSubmitted = async (id) => {
    await api.patch(`/appeals/${id}`, { status: 'submitted' })
    qc.invalidateQueries(['appeals', 'appeal'])
  }

  return (
    <div className="p-6">
      <h1 className="text-2xl font-bold text-gray-900 mb-2">Appeal Letters</h1>
      <p className="text-gray-500 text-sm mb-6">AI-generated appeal letters for denied claims</p>

      <div className="flex gap-4 h-[calc(100vh-180px)]">
        {/* List */}
        <div className="w-80 card p-0 overflow-y-auto">
          {isLoading && <div className="p-4 text-gray-400 text-sm">Loading…</div>}
          {appeals?.length === 0 && (
            <EmptyState
              icon={Mail}
              title="No appeal letters yet"
              body={<a href="/denials" className="text-plum-700 hover:underline">Go to Denials to generate one.</a>}
            />
          )}
          {appeals?.map(a => (
            <div
              key={a.id}
              className={`p-3 border-b border-gray-100 cursor-pointer hover:bg-gray-50 transition-colors ${selected === a.id ? 'bg-blue-50 border-l-2 border-l-blue-500' : ''}`}
              onClick={() => setSelected(a.id)}
            >
              <div className="flex items-center justify-between mb-1">
                <span className={statusColors[a.status] || 'badge-pending'}>{a.status?.replace(/_/g, ' ')}</span>
                {a.deadline && (
                  <span className="text-xs text-gray-400">Due {fmt.date(a.deadline)}</span>
                )}
              </div>
              <div className="text-xs text-gray-700 font-medium leading-tight line-clamp-2">{a.letter_subject}</div>
              <div className="text-xs text-gray-400 mt-1">Level {a.level} · {fmt.dateTime(a.created_at)}</div>
            </div>
          ))}
        </div>

        {/* Detail */}
        <div className="flex-1 card overflow-y-auto">
          {!selected && (
            <div className="h-full flex items-center justify-center text-gray-400">
              <div className="text-center">
                <Mail size={40} className="mx-auto mb-2 text-gray-300" />
                <p>Select an appeal to view</p>
              </div>
            </div>
          )}
          {selected && detail && (
            <div>
              <div className="flex items-start justify-between mb-4">
                <div>
                  <h2 className="font-semibold text-gray-900 text-sm">{detail.letter_subject}</h2>
                  <div className="flex items-center gap-2 mt-1">
                    <span className={statusColors[detail.status] || 'badge-pending'}>{detail.status?.replace(/_/g, ' ')}</span>
                    {detail.generated_by_ai && (
                      <span className="badge bg-blue-50 text-blue-600">AI Generated</span>
                    )}
                    {detail.deadline && (
                      <span className="text-xs text-gray-500">Appeal deadline: {fmt.date(detail.deadline)}</span>
                    )}
                  </div>
                </div>
                <div className="flex gap-2">
                  {detail.status === 'draft' || detail.status === 'ready' ? (
                    <button
                      className="btn-primary text-xs flex items-center gap-1"
                      onClick={() => markSubmitted(detail.id)}
                    >
                      <Check size={13} />
                      Mark Submitted
                    </button>
                  ) : null}
                  <button
                    className="btn-secondary text-xs flex items-center gap-1"
                    onClick={() => window.open(`/api/appeals/${detail.id}/download`)}
                  >
                    <Download size={13} />
                    Download
                  </button>
                </div>
              </div>

              <div className="bg-gray-50 border border-gray-200 rounded-lg p-6">
                <pre className="whitespace-pre-wrap text-sm text-gray-700 font-sans leading-relaxed">
                  {detail.letter_body}
                </pre>
              </div>

              {detail.submitted_date && (
                <div className="mt-3 text-xs text-green-700 flex items-center gap-1">
                  <Check size={12} /> Submitted {fmt.date(detail.submitted_date)}
                  {detail.decision_notes && ` · Decision: ${detail.decision_notes}`}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
