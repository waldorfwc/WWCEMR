import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { ArrowLeft, FileText, X, Shield, Search } from 'lucide-react'
import api, { fmt } from '../utils/api'


const ACTION_LABELS = {
  opening_balance:   'Opening balance (seed)',
  receipt_created:   'Receipt created',
  lot_received:      'Lot received (pre-verification)',
  manifest_verified: 'Manifest verified',
  stock_received:    'Stock added (received)',
  stock_adjusted:    'Stock adjusted (count)',
  transfer_sent:     'Transfer sent',
  transfer_received: 'Transfer received',
  disposal:          'Disposal',
  count_started:     'Count started',
  count_finished:    'Count finished',
  dose_type_edited:  'Dose type edited',
}

const ACTION_TONES = {
  opening_balance:   'bg-gray-100 text-gray-700',
  receipt_created:   'bg-blue-50 text-blue-700',
  lot_received:      'bg-blue-50 text-blue-700',
  manifest_verified: 'bg-green-100 text-green-700',
  stock_received:    'bg-green-50 text-green-700',
  stock_adjusted:    'bg-amber-100 text-amber-700',
  transfer_sent:     'bg-violet-50 text-violet-700',
  transfer_received: 'bg-violet-100 text-violet-700',
  disposal:          'bg-red-100 text-red-700',
  count_started:     'bg-blue-50 text-blue-700',
  count_finished:    'bg-green-100 text-green-700',
  dose_type_edited:  'bg-gray-100 text-gray-700',
}

const LOC_LABEL = {
  white_plains: 'White Plains',
  brandywine:   'Brandywine',
  arlington:    'Arlington',
}


export default function PelletAudit() {
  const [filters, setFilters] = useState({
    actor: '', action: '', location: '', lot_id: '', days: 30,
  })
  const set = (k, v) => setFilters(f => ({ ...f, [k]: v }))
  const clear = () => setFilters({ actor: '', action: '', location: '',
                                     lot_id: '', days: 30 })

  const { data, isLoading } = useQuery({
    queryKey: ['pellet-audit', filters],
    queryFn: () => api.get('/pellets/audit', { params: cleanParams(filters) })
                       .then(r => r.data),
  })

  function cleanParams(f) {
    const out = {}
    for (const [k, v] of Object.entries(f)) {
      if (v === '' || v === false || v == null) continue
      out[k] = v
    }
    out.per_page = 300
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

  // Quick summary: how many events show witness in detail?
  const witnessedCount = events.filter(e => e.detail?.witness).length
  const controlledCount = events.filter(e => e.detail?.controlled).length

  return (
    <div>
      <Link to="/pellets/inventory" className="text-[12px] text-muted hover:underline flex items-center gap-1 mb-2">
        <ArrowLeft size={12} /> Pellet inventory
      </Link>
      <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2 mb-1">
        <FileText size={22} className="text-plum-700" />
        Pellet audit log
      </h1>
      <p className="text-sm text-gray-500 mb-4">
        Perpetual inventory record — every state change. Write-only by
        design (DEA Schedule III compliance for testosterone). Combine
        filters with AND.
      </p>

      {/* Filter bar */}
      <div className="card mb-3">
        <div className="grid grid-cols-2 md:grid-cols-5 gap-2 text-sm">
          <div>
            <label className="text-[11px] uppercase text-gray-500 block mb-1">Action</label>
            <select className="input text-sm w-full" aria-label="Action filter" value={filters.action}
                    onChange={e => set('action', e.target.value)}>
              <option value="">All actions</option>
              {Object.entries(ACTION_LABELS).map(([k, v]) => (
                <option key={k} value={k}>{v}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="text-[11px] uppercase text-gray-500 block mb-1">Location</label>
            <select className="input text-sm w-full" aria-label="Location filter" value={filters.location}
                    onChange={e => set('location', e.target.value)}>
              <option value="">All locations</option>
              {Object.entries(LOC_LABEL).map(([k, v]) => (
                <option key={k} value={k}>{v}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="text-[11px] uppercase text-gray-500 block mb-1">Actor</label>
            <div className="relative">
              <Search size={11} className="absolute left-2 top-2 text-muted" />
              <input className="input text-sm pl-7 w-full" placeholder="email substring"
                     value={filters.actor}
                     onChange={e => set('actor', e.target.value)} />
            </div>
          </div>
          <div>
            <label className="text-[11px] uppercase text-gray-500 block mb-1">Lot UUID</label>
            <input className="input text-sm w-full font-mono text-[11px]"
                   placeholder="full UUID"
                   value={filters.lot_id}
                   onChange={e => set('lot_id', e.target.value)} />
          </div>
          <div>
            <label className="text-[11px] uppercase text-gray-500 block mb-1">Window</label>
            <select className="input text-sm w-full" aria-label="Time window" value={filters.days}
                    onChange={e => set('days', Number(e.target.value))}>
              <option value="7">Last 7 days</option>
              <option value="30">Last 30 days</option>
              <option value="90">Last 90 days</option>
              <option value="365">Last 365 days</option>
              <option value="3650">All time</option>
            </select>
          </div>
        </div>
        <div className="flex items-center gap-3 mt-2 text-[11px] text-gray-500">
          <span>
            {data?.total ?? 0} event{data?.total === 1 ? '' : 's'}
            {witnessedCount > 0 && <> · <Shield size={10} className="inline" /> {witnessedCount} witnessed</>}
          </span>
          <button onClick={clear} className="text-plum-700 hover:underline">Clear filters</button>
        </div>
      </div>

      {/* Table */}
      <div className="card !p-0 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-plum-50">
            <tr>
              <th className="table-th w-[170px]">When</th>
              <th className="table-th w-[160px]">Actor</th>
              <th className="table-th w-[170px]">Action</th>
              <th className="table-th w-[110px]">Location</th>
              <th className="table-th text-right w-[80px]">Δ doses</th>
              <th className="table-th">Summary</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {isLoading && (
              <tr><td colSpan={6} className="table-td text-center text-gray-400 py-6">Loading…</td></tr>
            )}
            {!isLoading && events.length === 0 && (
              <tr><td colSpan={6} className="table-td text-center text-gray-400 py-6 italic">
                No events match these filters.
              </td></tr>
            )}
            {events.map(e => {
              const witnessed = !!e.detail?.witness
              const controlled = !!e.detail?.controlled
              return (
                <tr key={e.id} className="hover:bg-plum-50/40">
                  <td className="table-td text-[11px] whitespace-nowrap">
                    {formatStamp(e.at)}
                  </td>
                  <td className="table-td text-[11px]">
                    {e.actor?.startsWith('system:') ? (
                      <span className="text-gray-500 italic">{e.actor.replace('system:', '')}</span>
                    ) : (
                      e.actor?.split('@')[0]
                    )}
                  </td>
                  <td className="table-td">
                    <span className={`text-[11px] uppercase px-1.5 py-0.5 rounded ${ACTION_TONES[e.action] || 'bg-gray-100 text-gray-700'}`}>
                      {ACTION_LABELS[e.action] || e.action}
                    </span>
                    {witnessed && (
                      <span className="ml-1 text-[11px] bg-amber-100 text-amber-700 px-1 rounded inline-flex items-center gap-0.5"
                            title={`Witnessed by ${e.detail.witness}`}>
                        <Shield size={8} /> wit
                      </span>
                    )}
                  </td>
                  <td className="table-td text-[11px]">
                    {LOC_LABEL[e.location] || e.location || '—'}
                  </td>
                  <td className={`table-td text-right font-mono text-[11px] font-semibold ${
                    e.delta_doses == null ? 'text-gray-400'
                      : e.delta_doses > 0 ? 'text-green-700'
                      : e.delta_doses < 0 ? 'text-red-700'
                      : 'text-gray-500'
                  }`}>
                    {e.delta_doses == null ? '—'
                      : (e.delta_doses > 0 ? '+' : '') + e.delta_doses}
                  </td>
                  <td className="table-td text-[12px] text-gray-700">
                    <div>{e.summary || '—'}</div>
                    {e.detail && Object.keys(e.detail).length > 0 && (
                      <DetailLine detail={e.detail} />
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {data?.total > events.length && (
        <div className="text-[11px] text-gray-500 mt-2 text-center">
          Showing first {events.length} of {data.total}. Narrow the filters
          to see older events.
        </div>
      )}
    </div>
  )
}


function DetailLine({ detail }) {
  // Compact one-line summary of the JSON detail
  const parts = []
  if (detail.witness)   parts.push(`witness ${(detail.witness || '').split('@')[0]}`)
  if (detail.reason)    parts.push(`reason: ${detail.reason}`)
  if (detail.to)        parts.push(`→ ${detail.to}`)
  if (detail.expected != null && detail.counted != null) {
    parts.push(`expected ${detail.expected} · counted ${detail.counted}`)
  }
  if (detail.lots)      parts.push(`${detail.lots} lots`)
  if (detail.lines)     parts.push(`${detail.lines} lines`)
  if (detail.doses)     parts.push(`${detail.doses} doses`)
  if (detail.sheet_id)  parts.push(`source: smartsheet ${detail.sheet_id}`)
  if (detail.changed)   parts.push(`changed: ${detail.changed.join(', ')}`)
  if (parts.length === 0) return null
  return (
    <div className="text-[10px] text-gray-500 mt-0.5">
      {parts.join(' · ')}
    </div>
  )
}
