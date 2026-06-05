import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { format } from 'date-fns'
import api from '../../utils/api'
import FaxStatusChip from '../../components/FaxStatusChip'

const STATUSES = [
  { value: '',          label: 'All status' },
  { value: 'queued',    label: 'Queued' },
  { value: 'sent',      label: 'Sent' },
  { value: 'delivered', label: 'Delivered' },
  { value: 'failed',    label: 'Failed' },
]
const WINDOWS = [
  { value: 7,  label: 'Last 7 days' },
  { value: 30, label: 'Last 30 days' },
  { value: 90, label: 'Last 90 days' },
]

export default function FaxLogPane() {
  const [status, setStatus] = useState('')
  const [window, setWindow] = useState(7)

  const q = useQuery({
    queryKey: ['fax-recent-pane', status, window],
    queryFn: () => api.get('/fax/recent', {
      params: { limit: 50, window, status: status || undefined },
    }).then(r => r.data),
    refetchInterval: (query) => {
      const data = query.state?.data
      return Array.isArray(data) && data.some(r => r.status === 'queued' || r.status === 'sent')
        ? 30_000
        : false
    },
  })

  async function retry(id) {
    await api.post(`/fax/retry/${id}`)
    q.refetch()
  }

  const rows = q.data || []

  return (
    <div className="bg-white border border-border-subtle rounded-lg overflow-hidden flex flex-col">
      <div className="px-4 py-2.5 border-b border-border-subtle flex justify-between items-center">
        <div className="font-serif text-ink text-[15px] font-semibold">Recent Faxes</div>
        <div className="flex gap-2">
          <select className="input text-[11px] py-1 px-2 w-[130px]"
                  aria-label="Fax status filter"
                  value={status} onChange={e => setStatus(e.target.value)}>
            {STATUSES.map(s => <option key={s.value} value={s.value}>{s.label}</option>)}
          </select>
          <select className="input text-[11px] py-1 px-2 w-[130px]"
                  aria-label="Fax time window"
                  value={window} onChange={e => setWindow(Number(e.target.value))}>
            {WINDOWS.map(w => <option key={w.value} value={w.value}>{w.label}</option>)}
          </select>
        </div>
      </div>
      <div className="overflow-auto flex-1">
        <table className="w-full text-[12px]">
          <thead className="bg-plum-50 sticky top-0">
            <tr>
              <th className="table-th">Sent</th>
              <th className="table-th">Patient</th>
              <th className="table-th">DOB</th>
              <th className="table-th">Chart</th>
              <th className="table-th">Docs</th>
              <th className="table-th">Doc types</th>
              <th className="table-th">Dest</th>
              <th className="table-th">Status</th>
              <th className="table-th">Sent by</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(r => (
              <tr key={r.id} className="table-row">
                <td className="table-td whitespace-nowrap">{r.sent_at ? format(new Date(r.sent_at), 'M/d h:mma') : '—'}</td>
                <td className="table-td">{r.patient_name}</td>
                <td className="table-td">{r.dob || '—'}</td>
                <td className="table-td">#{r.chart_number}</td>
                <td className="table-td">{r.doc_count}</td>
                <td className="table-td text-muted">{(r.doc_types || []).join(', ') || '—'}</td>
                <td className="table-td">{r.dest_fax}</td>
                <td className="table-td"><FaxStatusChip row={r} onRetry={() => retry(r.id)} /></td>
                <td className="table-td text-muted">{r.sent_by || '—'}</td>
              </tr>
            ))}
            {rows.length === 0 && !q.isLoading && (
              <tr><td colSpan={9} className="table-td text-center text-muted py-8">No faxes in this window.</td></tr>
            )}
            {q.isLoading && (
              <tr><td colSpan={9} className="table-td text-center text-muted py-8">Loading…</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
