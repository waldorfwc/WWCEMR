import { useState, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import api, { fmt } from '../utils/api'
import LoadingState from '../components/LoadingState'
import PelletRecallDetail from './PelletRecallDetail'

export default function PelletRecall() {
  const qc = useQueryClient()
  const [search, setSearch] = useState('')
  const [openId, setOpenId] = useState(null)

  const syncMutation = useMutation({
    mutationFn: () => api.post('/pellets/recall/sync'),
    onSettled: () => qc.invalidateQueries({ queryKey: ['pellet-recall-list'] }),
  })

  // Sync once on mount
  useEffect(() => {
    syncMutation.mutate()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const { data, isLoading } = useQuery({
    queryKey: ['pellet-recall-list', search],
    queryFn: () =>
      api.get('/pellets/recall', { params: { search: search || undefined } }).then(r => r.data),
  })

  const items = data?.items || []

  return (
    <div>
      {/* Page header */}
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-ink">Recall Worklist</h1>
          <p className="text-[13px] text-muted">Pellet patients due for re-insertion.</p>
        </div>
        <button
          onClick={() => syncMutation.mutate()}
          disabled={syncMutation.isPending}
          className="inline-flex items-center rounded border border-plum-200 px-3 py-1.5
                     text-[13px] font-medium text-plum-700 hover:bg-plum-50 disabled:opacity-50"
        >
          {syncMutation.isPending ? 'Refreshing…' : 'Refresh'}
        </button>
      </div>

      {/* Search bar */}
      <div className="mb-4">
        <input
          type="search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search by name, chart #, phone…"
          className="w-full max-w-sm rounded border border-border-subtle px-3 py-1.5
                     text-[13px] focus:border-plum-500 focus:outline-none"
        />
      </div>

      {/* Table */}
      {isLoading ? (
        <LoadingState />
      ) : items.length === 0 ? (
        <div className="py-12 text-center text-[13px] text-muted">
          No patients due for recall.
        </div>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-border-subtle">
          <table className="min-w-full text-[13px]">
            <thead className="bg-gray-50 text-left text-[11px] uppercase tracking-wide text-muted">
              <tr>
                <th className="px-3 py-2 font-medium whitespace-nowrap">Patient</th>
                <th className="px-3 py-2 font-medium whitespace-nowrap">Phone</th>
                <th className="px-3 py-2 font-medium whitespace-nowrap">Last Insertion</th>
                <th className="px-3 py-2 font-medium whitespace-nowrap">Recall Due</th>
                <th className="px-3 py-2 font-medium whitespace-nowrap">Attempts</th>
                <th className="px-3 py-2 font-medium whitespace-nowrap">Last Outcome</th>
                <th className="px-3 py-2 font-medium whitespace-nowrap">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border-subtle">
              {items.map((item) => (
                <tr
                  key={item.id}
                  className="cursor-pointer hover:bg-gray-50"
                  onClick={() => setOpenId(item.id)}
                >
                  <td className="px-3 py-2 whitespace-nowrap">
                    <div className="font-medium text-ink">{item.patient_name}</div>
                    <div className="text-[11px] text-muted">#{item.chart_number}</div>
                  </td>
                  <td className="px-3 py-2 whitespace-nowrap text-muted">
                    {item.cell_phone || item.primary_phone || '—'}
                  </td>
                  <td className="px-3 py-2 whitespace-nowrap text-muted">
                    {item.last_visit ? fmt.date(item.last_visit) : '—'}
                  </td>
                  <td className="px-3 py-2 whitespace-nowrap text-muted">
                    {item.recall_due ? fmt.date(item.recall_due) : '—'}
                  </td>
                  <td className="px-3 py-2 whitespace-nowrap text-center text-muted">
                    {item.attempts ?? 0}
                  </td>
                  <td className="px-3 py-2 whitespace-nowrap text-muted">
                    {item.last_outcome || '—'}
                  </td>
                  <td className="px-3 py-2 whitespace-nowrap">
                    {item.claimed_by ? (
                      <span className="inline-flex items-center gap-1 rounded-full bg-amber-50
                                       border border-amber-200 px-2 py-0.5 text-[11px] font-medium text-amber-800">
                        🔒 {item.claimed_by}
                      </span>
                    ) : (
                      <span className="text-muted">{item.status || '—'}</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Recall detail modal */}
      {openId && (
        <PelletRecallDetail
          recallId={openId}
          onClose={() => {
            setOpenId(null)
            qc.invalidateQueries({ queryKey: ['pellet-recall-list'] })
          }}
        />
      )}
    </div>
  )
}
