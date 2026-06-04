import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Search } from 'lucide-react'
import api from '../utils/api'
import { useChartFaxSummary } from '../hooks/useChartFaxSummary'

const TODAY_ISO = () => {
  const d = new Date()
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const dd = String(d.getDate()).padStart(2, '0')
  return `${d.getFullYear()}-${mm}-${dd}`
}

function faxChip(summary) {
  if (!summary?.last_sent_at) {
    return <span className="text-[10px] text-muted opacity-45">—</span>
  }
  const sent = new Date(summary.last_sent_at)
  const sentIso = `${sent.getFullYear()}-${String(sent.getMonth() + 1).padStart(2, '0')}-${String(sent.getDate()).padStart(2, '0')}`
  const label = `✓ ${sent.getMonth() + 1}/${sent.getDate()}`
  const isToday = sentIso === TODAY_ISO()
  const cls = isToday
    ? 'bg-green-100 text-green-800'
    : 'bg-plum-100 text-plum-700'
  return (
    <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded whitespace-nowrap ${cls}`}>
      {label}
    </span>
  )
}

export default function ChartPatientList({ activeChartNumber, perPage = 100 }) {
  const navigate = useNavigate()
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)

  const { data: indexStatus } = useQuery({
    queryKey: ['doc-index-status'],
    queryFn: () => api.get('/documents/index/status').then(r => r.data),
  })

  const { data: patients, isLoading } = useQuery({
    queryKey: ['doc-patients', search, page, perPage],
    queryFn: () => api.get('/documents/patients', {
      params: { search: search || undefined, page, per_page: perPage },
    }).then(r => r.data),
    enabled: (indexStatus?.indexed_documents || 0) > 0,
  })

  const { data: faxSummary } = useChartFaxSummary()

  return (
    <div className="bg-white border border-border-subtle rounded-lg overflow-hidden flex flex-col">
      <div className="p-2 border-b border-border-subtle">
        <div className="relative">
          <Search size={12} className="absolute left-2 top-2 text-muted" />
          <input
            className="w-full pl-6 pr-2 py-1.5 border border-border-subtle rounded text-[11px] focus:outline-none focus:ring-1 focus:ring-plum-700"
            placeholder="Search name, chart #, or DOB..."
            value={search}
            onChange={e => { setSearch(e.target.value); setPage(1) }}
          />
        </div>
      </div>
      <div className="flex-1 overflow-y-auto">
        <div className="px-3 py-1.5 text-[11px] text-muted border-b border-border-subtle bg-plum-50">
          {patients?.total?.toLocaleString() || 0} patients
        </div>
        {isLoading ? (
          <div className="text-center text-muted text-[11px] py-8">Loading…</div>
        ) : (
          patients?.patients?.map(p => {
            const isActive = String(p.chart_number) === String(activeChartNumber)
            return (
              <button
                key={p.chart_number}
                onClick={() => navigate(`/chart/${p.chart_number}`)}
                className={`w-full text-left px-3 py-2 text-[11px] border-b border-plum-100 transition-colors flex justify-between items-start gap-2 ${
                  isActive ? 'bg-plum-100' : 'hover:bg-plum-50'
                }`}
              >
                <div className="min-w-0 flex-1">
                  <div className={`truncate ${isActive ? 'font-semibold text-plum-900' : 'font-medium text-ink'}`}>
                    {p.patient_name || `Chart ${p.chart_number}`}
                  </div>
                  <div className="text-muted text-[10px] truncate">
                    #{p.chart_number}
                    {p.dob && <> · DOB {p.dob}</>}
                    {' · '}{p.document_count}d
                  </div>
                </div>
                <div className="shrink-0">{faxChip(faxSummary?.[p.chart_number])}</div>
              </button>
            )
          })
        )}
        {patients && patients.total > perPage && (
          <div className="flex items-center justify-center gap-2 py-3 text-[11px] text-muted">
            <button onClick={() => setPage(p => Math.max(1, p - 1))}
                    disabled={page === 1}
                    className="px-2 py-1 border border-border-subtle rounded disabled:opacity-40">Prev</button>
            <span>{page} / {Math.ceil(patients.total / perPage)}</span>
            <button onClick={() => setPage(p => p + 1)}
                    disabled={page >= Math.ceil(patients.total / perPage)}
                    className="px-2 py-1 border border-border-subtle rounded disabled:opacity-40">Next</button>
          </div>
        )}
      </div>
    </div>
  )
}
