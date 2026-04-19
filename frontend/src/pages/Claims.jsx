import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { Search, Filter } from 'lucide-react'
import api, { fmt, statusColors } from '../utils/api'

const STATUSES = ['', 'paid', 'denied', 'partial', 'pending', 'adjusted', 'written_off', 'appealed']

export default function Claims() {
  const navigate = useNavigate()
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState('')
  const [page, setPage] = useState(1)

  const { data, isLoading } = useQuery({
    queryKey: ['claims', search, status, page],
    queryFn: () => api.get('/claims', { params: { search, status, page, per_page: 50 } }).then(r => r.data),
  })

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Claims</h1>
          <p className="text-gray-500 text-sm mt-1">{data?.total?.toLocaleString() || 0} total claims</p>
        </div>
        <a href="/import" className="btn-primary">+ Import ERA 835</a>
      </div>

      {/* Filters */}
      <div className="card mb-4 flex gap-3 items-center flex-wrap">
        <div className="relative flex-1 min-w-[200px]">
          <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
          <input
            className="input pl-9"
            placeholder="Search claim #, payer claim #, member ID…"
            value={search}
            onChange={e => { setSearch(e.target.value); setPage(1) }}
          />
        </div>
        <div className="flex items-center gap-2">
          <Filter size={15} className="text-gray-400" />
          <select className="input w-40" value={status} onChange={e => { setStatus(e.target.value); setPage(1) }}>
            {STATUSES.map(s => (
              <option key={s} value={s}>{s ? s.charAt(0).toUpperCase() + s.slice(1) : 'All Statuses'}</option>
            ))}
          </select>
        </div>
      </div>

      {/* Table */}
      <div className="card p-0 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead className="bg-gray-50 border-b border-gray-200">
              <tr>
                <th className="table-th">Claim #</th>
                <th className="table-th">DOS</th>
                <th className="table-th">Payer</th>
                <th className="table-th">Member ID</th>
                <th className="table-th text-right">Billed</th>
                <th className="table-th text-right">Paid</th>
                <th className="table-th text-right">Balance</th>
                <th className="table-th">Status</th>
                <th className="table-th">Actions</th>
              </tr>
            </thead>
            <tbody>
              {isLoading && (
                <tr><td colSpan={9} className="table-td text-center text-gray-400 py-8">Loading…</td></tr>
              )}
              {!isLoading && data?.claims?.length === 0 && (
                <tr><td colSpan={9} className="table-td text-center text-gray-400 py-8">No claims found</td></tr>
              )}
              {data?.claims?.map(claim => (
                <tr key={claim.id} className="table-row cursor-pointer" onClick={() => navigate(`/claims/${claim.id}`)}>
                  <td className="table-td font-mono text-xs font-medium text-primary-500">{claim.claim_number}</td>
                  <td className="table-td text-xs">{fmt.date(claim.date_of_service_from)}</td>
                  <td className="table-td text-xs max-w-[140px] truncate">{claim.payer_name || '—'}</td>
                  <td className="table-td font-mono text-xs">{claim.subscriber_id || '—'}</td>
                  <td className="table-td text-right font-mono text-xs">{fmt.currency(claim.billed_amount)}</td>
                  <td className="table-td text-right font-mono text-xs text-green-700">{fmt.currency(claim.paid_amount)}</td>
                  <td className={`table-td text-right font-mono text-xs font-semibold ${claim.balance > 0 ? 'text-red-600' : 'text-gray-500'}`}>
                    {fmt.currency(claim.balance)}
                  </td>
                  <td className="table-td">
                    <span className={statusColors[claim.status] || 'badge-pending'}>
                      {claim.status?.replace(/_/g, ' ')}
                    </span>
                  </td>
                  <td className="table-td">
                    <button
                      className="text-xs text-primary-500 hover:underline"
                      onClick={e => { e.stopPropagation(); window.open(`/api/eob/${claim.id}/pdf`, '_blank') }}
                    >
                      EOB PDF
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Pagination */}
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
