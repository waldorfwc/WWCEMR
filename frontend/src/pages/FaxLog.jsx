import { useState } from 'react'
import { useQuery, keepPreviousData } from '@tanstack/react-query'
import { format } from 'date-fns'
import api, { fmt } from '../utils/api'
import FaxStatusChip from '../components/FaxStatusChip'

const STATUSES = [
  { value: '',          label: 'All' },
  { value: 'queued',    label: 'Queued' },
  { value: 'sent',      label: 'Sent' },
  { value: 'delivered', label: 'Delivered' },
  { value: 'failed',    label: 'Failed' },
]

export default function FaxLog() {
  const [status, setStatus] = useState('')
  const [chart, setChart] = useState('')
  const [page, setPage] = useState(1)

  const q = useQuery({
    queryKey: ['fax-log', status, chart, page],
    queryFn: () => api.get('/fax-log', {
      params: { status: status || undefined, chart: chart || undefined, page, page_size: 50 },
    }).then(r => r.data),
    placeholderData: keepPreviousData,
  })

  const data = q.data

  async function retry(id) {
    await api.post(`/fax/retry/${id}`)
    q.refetch()
  }

  return (
    <div>
      <div className="flex items-baseline justify-between mb-4">
        <h1 className="font-serif font-semibold text-ink text-[24px] m-0">Fax log</h1>
        {data && <div className="text-muted text-[13px]">{data.total} total</div>}
      </div>

      <div className="flex gap-3 mb-3">
        <select className="input w-40" value={status}
                onChange={(e) => { setStatus(e.target.value); setPage(1) }}>
          {STATUSES.map(s => <option key={s.value} value={s.value}>{s.label}</option>)}
        </select>
        <input className="input w-48" placeholder="Chart #"
               value={chart} onChange={(e) => { setChart(e.target.value); setPage(1) }} />
      </div>

      <div className="card p-0 overflow-hidden">
        <table className="w-full">
          <thead className="bg-plum-50">
            <tr>
              <th className="table-th">Sent</th>
              <th className="table-th">Chart</th>
              <th className="table-th">Patient</th>
              <th className="table-th">Docs</th>
              <th className="table-th">Grouping</th>
              <th className="table-th">Dest</th>
              <th className="table-th">Status</th>
              <th className="table-th"></th>
            </tr>
          </thead>
          <tbody>
            {data?.rows?.map(r => (
              <tr key={r.id} className="table-row">
                <td className="table-td whitespace-nowrap">
                  {r.sent_at ? format(new Date(r.sent_at), 'MM/dd h:mm a') : '—'}
                </td>
                <td className="table-td">{r.chart_number}</td>
                <td className="table-td">{r.patient_name}</td>
                <td className="table-td">{r.doc_count}</td>
                <td className="table-td">{r.grouping_mode}</td>
                <td className="table-td">{r.dest_fax}</td>
                <td className="table-td"><FaxStatusChip row={r} onRetry={() => retry(r.id)} /></td>
                <td className="table-td text-[12px]">{r.error ? <span className="text-danger" title={r.error}>error</span> : ''}</td>
              </tr>
            ))}
            {data?.rows?.length === 0 && (
              <tr><td colSpan={8} className="table-td text-center text-muted py-8">
                No faxes match these filters.
              </td></tr>
            )}
          </tbody>
        </table>
      </div>

      {data && data.total > 50 && (
        <div className="flex items-center justify-end gap-2 mt-3 text-[13px]">
          <button className="btn-secondary" disabled={page <= 1}
                  onClick={() => setPage(p => Math.max(1, p - 1))}>Prev</button>
          <span className="text-muted">Page {data.page}</span>
          <button className="btn-secondary" disabled={page * 50 >= data.total}
                  onClick={() => setPage(p => p + 1)}>Next</button>
        </div>
      )}
    </div>
  )
}
