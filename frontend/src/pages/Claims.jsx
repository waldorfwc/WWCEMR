import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { Search, Filter, SearchX } from 'lucide-react'
import api, { fmt, statusColors } from '../utils/api'
import EmptyState from '../components/EmptyState'

function followUpClass(dateStr, state) {
  if (!dateStr) return 'text-gray-400'
  if (state === 'Closed') return 'text-gray-400'
  const d = new Date(dateStr + 'T00:00:00')
  const today = new Date(); today.setHours(0, 0, 0, 0)
  const diff = (d - today) / (1000 * 60 * 60 * 24)
  if (diff < 0) return 'text-red-600 font-semibold'
  if (diff <= 7) return 'text-amber-600'
  return 'text-gray-600'
}

function daysBetween(dateStr) {
  if (!dateStr) return null
  const d = new Date(dateStr + 'T00:00:00')
  const today = new Date(); today.setHours(0, 0, 0, 0)
  return Math.floor((today - d) / (1000 * 60 * 60 * 24))
}

const STATUSES = ['', 'paid', 'denied', 'partial', 'pending', 'adjusted', 'written_off', 'appealed']
const AGE_BUCKETS = ['', '0-30', '31-60', '61-90', '90+']
const PRIORITY_BADGE = {
  primary: { label: 'P', cls: 'bg-emerald-100 text-emerald-700' },
  secondary: { label: 'S', cls: 'bg-amber-100 text-amber-700' },
  tertiary: { label: 'T', cls: 'bg-gray-100 text-gray-600' },
}

export default function Claims() {
  const navigate = useNavigate()
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState('')
  const [page, setPage] = useState(1)
  const [workflowFilter, setWorkflowFilter] = useState('all')
  const [ageBucket, setAgeBucket] = useState('')
  const [payerFilter, setPayerFilter] = useState('')

  // Default sort: F/U asc when working a queue, else DOS desc
  const orderBy =
    workflowFilter === 'followup' || workflowFilter === 'overdue' ? 'fu_asc' : 'dos_desc'

  const { data, isLoading } = useQuery({
    queryKey: ['claims', search, status, workflowFilter, ageBucket, payerFilter, page, orderBy],
    queryFn: () => {
      const params = { search, status, page, per_page: 50, order_by: orderBy }
      if (workflowFilter === 'open') params.state = 'open'
      if (workflowFilter === 'followup' || workflowFilter === 'overdue') {
        params.has_followup = true
      }
      if (ageBucket) params.age_bucket = ageBucket
      if (payerFilter) params.payer = payerFilter
      return api.get('/claims', { params }).then(r => r.data)
    },
  })

  const { data: queueSummary } = useQuery({
    queryKey: ['claims-work-queue-summary'],
    queryFn: () => api.get('/claims/work-queue/summary').then(r => r.data),
  })

  function applyTodayPreset() {
    setWorkflowFilter('overdue')
    setAgeBucket('')
    setPayerFilter('')
    setStatus('')
    setSearch('')
    setPage(1)
  }
  function clearAll() {
    setWorkflowFilter('all'); setAgeBucket(''); setPayerFilter('')
    setStatus(''); setSearch(''); setPage(1)
  }

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Claims</h1>
          <p className="text-gray-500 text-sm mt-1">{data?.total?.toLocaleString() || 0} claims in current view</p>
        </div>
        <div className="flex gap-2">
          <button className="btn-secondary" onClick={applyTodayPreset}>📋 Today's Queue</button>
          <a href="/import" className="btn-primary">+ Import ERA 835</a>
        </div>
      </div>

      {/* Work-queue summary chips */}
      {queueSummary && (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-4">
          <SummaryChip label="Open" value={queueSummary.open_total.toLocaleString()} sub={fmt.currency(queueSummary.open_balance)} tone="blue" />
          <SummaryChip label="Overdue" value={queueSummary.overdue.toLocaleString()} sub="F/U date past" tone="red"
                       onClick={() => { setWorkflowFilter('overdue'); setAgeBucket(''); setPayerFilter(''); setPage(1) }} />
          <SummaryChip label="Due today" value={queueSummary.due_today.toLocaleString()} sub="F/U today" tone="amber" />
          <SummaryChip label="No F/U set" value={queueSummary.no_fu.toLocaleString()} sub="needs review" tone="gray" />
          <SummaryChip label="90+ days old" value={queueSummary.age_buckets.find(b => b.bucket === '90+')?.count.toLocaleString() || '0'}
                       sub={fmt.currency(queueSummary.age_buckets.find(b => b.bucket === '90+')?.balance || 0)} tone="amber"
                       onClick={() => { setWorkflowFilter('open'); setAgeBucket('90+'); setPayerFilter(''); setPage(1) }} />
        </div>
      )}

      {/* Filters */}
      <div className="card mb-4 flex gap-3 items-center flex-wrap">
        <div className="relative flex-1 min-w-[200px]">
          <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
          <input
            className="input pl-9"
            placeholder="Search claim #, member ID, patient name, chart #…"
            value={search}
            onChange={e => { setSearch(e.target.value); setPage(1) }}
          />
        </div>
        <div className="flex items-center gap-2">
          <Filter size={15} className="text-gray-400" />
          <select className="input w-36" value={status} onChange={e => { setStatus(e.target.value); setPage(1) }}>
            {STATUSES.map(s => (
              <option key={s} value={s}>{s ? s.charAt(0).toUpperCase() + s.slice(1) : 'All Statuses'}</option>
            ))}
          </select>
          <select className="input w-32" value={ageBucket} onChange={e => { setAgeBucket(e.target.value); setPage(1) }}>
            {AGE_BUCKETS.map(b => (
              <option key={b} value={b}>{b ? `${b} days` : 'All ages'}</option>
            ))}
          </select>
          <select className="input w-52" value={payerFilter} onChange={e => { setPayerFilter(e.target.value); setPage(1) }}>
            <option value="">All payers</option>
            {queueSummary?.top_payers?.map(p => (
              <option key={p.payer} value={p.payer}>{p.payer.length > 38 ? p.payer.slice(0, 38) + '…' : p.payer} ({p.count})</option>
            ))}
          </select>
        </div>
        <div className="flex gap-1 items-center">
          {[
            { key: 'all', label: 'All' },
            { key: 'open', label: 'Open' },
            { key: 'followup', label: 'F/U queue' },
            { key: 'overdue', label: 'Overdue' },
          ].map(f => (
            <button
              key={f.key}
              onClick={() => { setWorkflowFilter(f.key); setPage(1) }}
              className={`px-2 py-1 text-xs rounded ${
                workflowFilter === f.key
                  ? 'bg-plum-700 text-white'
                  : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
              }`}
            >
              {f.label}
            </button>
          ))}
          {(search || status || workflowFilter !== 'all' || ageBucket || payerFilter) && (
            <button className="ml-2 text-xs text-gray-500 hover:text-gray-800 underline" onClick={clearAll}>Clear</button>
          )}
        </div>
      </div>

      {/* Table */}
      <div className="card p-0 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead className="bg-gray-50 border-b border-gray-200">
              <tr>
                <th className="table-th">Claim #</th>
                <th className="table-th">Pri</th>
                <th className="table-th">Patient</th>
                <th className="table-th">DOS</th>
                <th className="table-th text-right">Age</th>
                <th className="table-th">Payer</th>
                <th className="table-th text-right">Billed</th>
                <th className="table-th text-right">Paid</th>
                <th className="table-th text-right">Balance</th>
                <th className="table-th">Status</th>
                <th className="table-th">Last Sub</th>
                <th className="table-th">Follow-up</th>
              </tr>
            </thead>
            <tbody>
              {isLoading && (
                <tr><td colSpan={12} className="table-td text-center text-gray-400 py-8">Loading…</td></tr>
              )}
              {!isLoading && data?.claims?.length === 0 && (
                <tr>
                  <td colSpan={12} className="table-td">
                    <EmptyState
                      icon={SearchX}
                      title="No claims match these filters"
                      body="Try clearing a filter or widening the date range."
                      compact
                    />
                  </td>
                </tr>
              )}
              {data?.claims?.map(claim => {
                const age = daysBetween(claim.date_of_service_from)
                const pri = PRIORITY_BADGE[claim.insurance_order] || PRIORITY_BADGE.primary
                return (
                  <tr key={claim.id} className="table-row cursor-pointer" onClick={() => navigate(`/claims/${claim.id}`)}>
                    <td className="table-td font-mono text-xs font-medium text-plum-700">{claim.claim_number}</td>
                    <td className="table-td">
                      <span className={`px-1.5 py-0.5 text-[10px] font-bold rounded ${pri.cls}`}>{pri.label}</span>
                    </td>
                    <td className="table-td text-xs">
                      {claim.patient ? (
                        <div>
                          <div className="font-medium text-gray-900">
                            {claim.patient.last_name || ''}{claim.patient.last_name && claim.patient.first_name ? ', ' : ''}{claim.patient.first_name || ''}
                          </div>
                          <div className="text-[10px] text-gray-500 font-mono">
                            #{claim.patient.chart_number || '—'}
                            {claim.patient.date_of_birth && <> · DOB {fmt.date(claim.patient.date_of_birth)}</>}
                          </div>
                        </div>
                      ) : <span className="text-gray-400">—</span>}
                    </td>
                    <td className="table-td text-xs">{fmt.date(claim.date_of_service_from)}</td>
                    <td className={`table-td text-right text-xs ${age > 90 ? 'text-red-600 font-semibold' : age > 60 ? 'text-amber-600' : 'text-gray-600'}`}>
                      {age != null ? `${age}d` : '—'}
                    </td>
                    <td className="table-td text-xs max-w-[160px] truncate" title={claim.payer_name}>{claim.payer_name || '—'}</td>
                    <td className="table-td text-right font-mono text-xs">{fmt.currency(claim.billed_amount)}</td>
                    <td className="table-td text-right font-mono text-xs text-green-700">{fmt.currency(claim.paid_amount)}</td>
                    <td className={`table-td text-right font-mono text-xs font-semibold ${claim.balance > 0 ? 'text-red-600' : 'text-gray-500'}`}>
                      {fmt.currency(claim.balance)}
                    </td>
                    <td className="table-td">
                      <span className={statusColors[claim.status] || 'badge-pending'}>
                        {claim.status?.replace(/_/g, ' ')}
                      </span>
                      {claim.claim_state && (
                        <div className="text-[10px] text-gray-400 mt-0.5">{claim.claim_state}</div>
                      )}
                    </td>
                    <td className="table-td text-xs text-gray-600">
                      {claim.last_submission_date ? fmt.date(claim.last_submission_date) : '—'}
                    </td>
                    <td className="table-td">
                      {claim.follow_up_date ? (
                        <div className={`text-xs ${followUpClass(claim.follow_up_date, claim.claim_state)}`}>
                          {fmt.date(claim.follow_up_date)}
                          {claim.follow_up_reason && (
                            <div className="text-[10px] text-gray-400 truncate max-w-[140px]" title={claim.follow_up_reason}>
                              {claim.follow_up_reason}
                            </div>
                          )}
                        </div>
                      ) : <span className="text-gray-400 text-xs">—</span>}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>

        {data && data.total > 50 && (
          <div className="border-t border-gray-100 px-4 py-3 flex items-center justify-between text-sm text-gray-500">
            <span>Page {page} of {Math.ceil(data.total / 50)}</span>
            <div className="flex gap-2">
              <button className="btn-secondary py-1 px-3 text-xs" onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page === 1}>Prev</button>
              <button className="btn-secondary py-1 px-3 text-xs" onClick={() => setPage(p => p + 1)} disabled={page >= Math.ceil(data.total / 50)}>Next</button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

function SummaryChip({ label, value, sub, tone, onClick }) {
  const tones = {
    blue:  'bg-blue-50 border-blue-200 text-blue-700',
    red:   'bg-red-50 border-red-200 text-red-700',
    amber: 'bg-amber-50 border-amber-200 text-amber-700',
    gray:  'bg-gray-50 border-gray-200 text-gray-700',
  }
  return (
    <button
      onClick={onClick}
      disabled={!onClick}
      className={`text-left rounded-lg border p-3 ${tones[tone] || tones.gray} ${onClick ? 'hover:shadow cursor-pointer' : 'cursor-default'}`}
    >
      <div className="text-[11px] uppercase tracking-wide opacity-80">{label}</div>
      <div className="text-2xl font-bold leading-tight">{value}</div>
      <div className="text-[11px] opacity-70 mt-0.5">{sub}</div>
    </button>
  )
}
