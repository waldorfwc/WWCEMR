import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { ArrowLeft, FileText, Search, X } from 'lucide-react'
import api, { fmt } from '../utils/api'


export default function LarcAudit() {
  const [filters, setFilters] = useState({
    actor: '', device_id: '', chart_number: '', action: '', system_only: false,
  })
  const set = (k, v) => setFilters(f => ({ ...f, [k]: v }))
  const clear = () => setFilters({ actor: '', device_id: '', chart_number: '', action: '', system_only: false })

  const { data, isLoading } = useQuery({
    queryKey: ['larc-audit', filters],
    queryFn: () => api.get('/larc/audit', { params: clean(filters) }).then(r => r.data),
  })

  function clean(f) {
    const out = {}
    for (const [k, v] of Object.entries(f)) {
      if (v === '' || v === false) continue
      out[k] = v
    }
    out.per_page = 200
    return out
  }

  function formatStamp(iso) {
    if (!iso) return ''
    return new Date(iso).toLocaleString('en-US', {
      month: 'short', day: 'numeric', year: 'numeric',
      hour: 'numeric', minute: '2-digit',
    })
  }

  const events = data?.events || []

  return (
    <div>
      <Link to="/larc" className="text-[12px] text-muted hover:underline flex items-center gap-1 mb-2">
        <ArrowLeft size={12} /> LARC dashboard
      </Link>
      <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2 mb-3">
        <FileText size={22} className="text-plum-700" />
        LARC audit log
      </h1>
      <p className="text-sm text-gray-500 mb-4">
        Every device + assignment + checkout state change. Combine filters with AND.
      </p>

      {/* Filter bar */}
      <div className="card mb-3">
        <div className="grid grid-cols-2 md:grid-cols-5 gap-2 text-sm">
          <div>
            <label className="text-[10px] uppercase text-gray-500 block mb-1">User (email contains)</label>
            <input className="input text-sm" value={filters.actor}
                   onChange={e => set('actor', e.target.value)}
                   placeholder="e.g. cooke" />
          </div>
          <div>
            <label className="text-[10px] uppercase text-gray-500 block mb-1">Device ID</label>
            <input className="input text-sm font-mono" value={filters.device_id}
                   onChange={e => set('device_id', e.target.value)}
                   placeholder="GUID" />
          </div>
          <div>
            <label className="text-[10px] uppercase text-gray-500 block mb-1">Patient chart #</label>
            <input className="input text-sm font-mono" value={filters.chart_number}
                   onChange={e => set('chart_number', e.target.value)} />
          </div>
          <div>
            <label className="text-[10px] uppercase text-gray-500 block mb-1">Action</label>
            <select className="input text-sm" value={filters.action}
                    onChange={e => set('action', e.target.value)}>
              <option value="">Any</option>
              <option value="device_added">device_added</option>
              <option value="device_edited">device_edited</option>
              <option value="assignment_created">assignment_created</option>
              <option value="benefits_verified">benefits_verified</option>
              <option value="enrollment_sent">enrollment_sent</option>
              <option value="enrollment_signed">enrollment_signed</option>
              <option value="request_faxed">request_faxed</option>
              <option value="device_received">device_received</option>
              <option value="patient_notified">patient_notified</option>
              <option value="appt_scheduled">appt_scheduled</option>
              <option value="checkout_auto_approved">checkout_auto_approved</option>
              <option value="checkout_flagged_for_manager">checkout_flagged_for_manager</option>
              <option value="checkout_approved">checkout_approved</option>
              <option value="checkout_denied">checkout_denied</option>
              <option value="outcome_recorded">outcome_recorded</option>
              <option value="billed">billed</option>
              <option value="device_reallocated">device_reallocated</option>
              <option value="owed_resolved">owed_resolved</option>
              <option value="pharmacy_sla_breach">pharmacy_sla_breach</option>
            </select>
          </div>
          <div className="flex items-end gap-3">
            <label className="flex items-center gap-1.5 text-xs text-gray-700 mb-1">
              <input type="checkbox" checked={filters.system_only}
                     onChange={e => set('system_only', e.target.checked)} />
              System only
            </label>
            <button type="button" onClick={clear} className="text-[11px] text-muted hover:underline mb-1">
              Clear all
            </button>
          </div>
        </div>
      </div>

      <div className="card !p-0 overflow-hidden">
        {isLoading && <div className="text-gray-400 italic p-4 text-sm">Loading…</div>}
        {!isLoading && events.length === 0 && (
          <div className="text-gray-400 italic p-4 text-sm">No events match these filters.</div>
        )}
        {!isLoading && events.length > 0 && (
          <table className="w-full text-sm">
            <thead className="bg-plum-50">
              <tr>
                <th className="table-th">When</th>
                <th className="table-th">Actor</th>
                <th className="table-th">Action</th>
                <th className="table-th">Patient / Device</th>
                <th className="table-th">Summary</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {events.map(e => (
                <tr key={e.id} className="hover:bg-plum-50/40 align-top">
                  <td className="table-td text-[11px] whitespace-nowrap text-gray-600">
                    {formatStamp(e.occurred_at)}
                  </td>
                  <td className="table-td text-[11px] font-mono">
                    {e.actor?.startsWith('system:') ? (
                      <span className="text-[10px] bg-gray-100 text-gray-700 px-1.5 py-0.5 rounded">{e.actor}</span>
                    ) : (
                      e.actor?.split('@')[0] || '—'
                    )}
                  </td>
                  <td className="table-td text-[11px]">
                    <code className="text-plum-700">{e.action}</code>
                  </td>
                  <td className="table-td text-[11px]">
                    {e.patient_name && <div>{e.patient_name}</div>}
                    {e.chart_number && <div className="text-gray-500 font-mono">{e.chart_number}</div>}
                  </td>
                  <td className="table-td text-[12px] text-gray-700">
                    {e.summary}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      <div className="text-[10px] text-gray-500 mt-2 text-right">
        Showing {events.length} of {data?.total || 0} events
      </div>
    </div>
  )
}
