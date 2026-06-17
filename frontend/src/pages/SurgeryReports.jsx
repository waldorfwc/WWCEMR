import { useState, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Download, X } from 'lucide-react'
import api, { fmt } from '../utils/api'
import LoadingState from '../components/LoadingState'
import { STATUS_LABEL } from './Surgery'

// ─── Date preset helpers ────────────────────────────────────────────────────

function toISO(d) {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

function resolvePreset(preset) {
  const today = new Date()
  const y = today.getFullYear()
  const mo = today.getMonth()

  if (preset === 'this_month') {
    return { from: toISO(new Date(y, mo, 1)), to: toISO(new Date(y, mo + 1, 0)) }
  }
  if (preset === 'last_month') {
    return { from: toISO(new Date(y, mo - 1, 1)), to: toISO(new Date(y, mo, 0)) }
  }
  if (preset === 'last_30') {
    const from = new Date(today); from.setDate(today.getDate() - 29)
    return { from: toISO(from), to: toISO(today) }
  }
  if (preset === 'last_90') {
    const from = new Date(today); from.setDate(today.getDate() - 89)
    return { from: toISO(from), to: toISO(today) }
  }
  return null // custom
}

// ─── Status row order for Status Funnel tile ────────────────────────────────

const STATUS_ORDER = ['incomplete', 'new', 'in_progress', 'confirmed', 'completed', 'hold', 'unresponsive', 'cancelled']

// ─── Facility display labels ─────────────────────────────────────────────────

const FACILITY_LABEL = {
  medstar:                 'MedStar',
  crmc:                    'CRMC',
  office:                  'Office',
  wwc_office_white_plains: 'WWC Office White Plains',
}

const FACILITY_OPTIONS = [
  { value: '',                       label: 'All Facilities' },
  { value: 'medstar',                label: 'MedStar' },
  { value: 'crmc',                   label: 'CRMC' },
  { value: 'office',                 label: 'Office' },
  { value: 'wwc_office_white_plains', label: 'WWC Office White Plains' },
]

const BLOCKER_LABEL = {
  benefits:   'Benefits',
  consents:   'Consents',
  prior_auth: 'Prior Auth',
  clearance:  'Clearance',
  device:     'Device',
  labs:       'Labs',
}

// ─── CSV download helper ─────────────────────────────────────────────────────

async function downloadCSV({ tile, params }) {
  const resp = await api.get(`/surgery/reports/${tile}/rows`, {
    params: { ...params, format: 'csv' },
    responseType: 'blob',
  })
  const url = URL.createObjectURL(resp.data)
  const a = document.createElement('a')
  a.href = url
  a.download = `${tile}.csv`
  a.click()
  URL.revokeObjectURL(url)
}

// ─── Tile wrapper ────────────────────────────────────────────────────────────

function Tile({ title, onClick, children }) {
  return (
    <div
      className={`rounded-lg border border-border-subtle bg-white p-4 ${onClick ? 'cursor-pointer hover:border-plum-200 hover:shadow-sm transition-all' : ''}`}
      onClick={onClick}
    >
      <div className="mb-3 text-[11px] font-semibold uppercase tracking-wide text-muted">
        {title}
      </div>
      {children}
    </div>
  )
}

function BigNumber({ value, label }) {
  return (
    <div>
      <div className="text-3xl font-bold text-ink">{value ?? '—'}</div>
      {label && <div className="mt-0.5 text-[12px] text-muted">{label}</div>}
    </div>
  )
}

// ─── Drill-down panel ────────────────────────────────────────────────────────

function DrillDown({ tile, bucket, params, onClose }) {
  const queryParams = { ...params }
  if (bucket) queryParams.bucket = bucket

  const { data, isLoading } = useQuery({
    queryKey: ['surgery-report-rows', tile, params.from, params.to, params.facility, params.surgeon, bucket],
    queryFn: () =>
      api.get(`/surgery/reports/${tile}/rows`, { params: queryParams }).then(r => r.data),
  })

  const items = data?.items || []
  const columns = items.length > 0 ? Object.keys(items[0]) : []

  const title = bucket
    ? `${tile.replace(/_/g, ' ')} — ${bucket}`
    : tile.replace(/_/g, ' ')

  return (
    <div
      className="fixed inset-0 bg-black/40 flex items-end sm:items-center justify-center z-50 p-4"
      onClick={onClose}
    >
      <div
        className="bg-white rounded-lg border border-border-subtle w-full max-w-4xl max-h-[80vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-border-subtle px-5 py-3 shrink-0">
          <h2 className="text-base font-semibold text-ink capitalize">{title}</h2>
          <div className="flex items-center gap-2">
            <button
              onClick={() => downloadCSV({ tile, params: queryParams })}
              className="inline-flex items-center gap-1.5 rounded border border-plum-200 px-2.5 py-1
                         text-[12px] font-medium text-plum-700 hover:bg-plum-50"
            >
              <Download size={13} /> Download CSV
            </button>
            <button onClick={onClose} className="text-muted hover:text-ink" aria-label="Close">
              <X size={18} />
            </button>
          </div>
        </div>

        {/* Body */}
        <div className="overflow-auto flex-1 p-4">
          {isLoading ? (
            <LoadingState />
          ) : items.length === 0 ? (
            <div className="py-8 text-center text-[13px] text-muted">No rows found.</div>
          ) : (
            <div className="overflow-x-auto rounded-lg border border-border-subtle">
              <table className="min-w-full text-[13px]">
                <thead className="bg-gray-50 text-left text-[11px] uppercase tracking-wide text-muted">
                  <tr>
                    {columns.map(col => (
                      <th key={col} className="px-3 py-2 font-medium whitespace-nowrap">
                        {col.replace(/_/g, ' ')}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-border-subtle">
                  {items.map((row, i) => (
                    <tr key={i} className="hover:bg-gray-50">
                      {columns.map(col => (
                        <td key={col} className="px-3 py-2 whitespace-nowrap">
                          {row[col] == null ? '—' : String(row[col])}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// ─── Main page ───────────────────────────────────────────────────────────────

export default function SurgeryReports() {
  // Filter state
  const [preset, setPreset] = useState('this_month')
  const [customFrom, setCustomFrom] = useState('')
  const [customTo, setCustomTo] = useState('')
  const [facility, setFacility] = useState('')
  const [surgeon, setSurgeon] = useState('')

  // Drill-down state: { tile, bucket }
  const [drillDown, setDrillDown] = useState(null)

  // Resolve date range
  const dateRange = useMemo(() => {
    if (preset === 'custom') return { from: customFrom, to: customTo }
    return resolvePreset(preset) || { from: '', to: '' }
  }, [preset, customFrom, customTo])

  const from = dateRange.from
  const to = dateRange.to

  // Surgeon picklist
  const { data: picklists } = useQuery({
    queryKey: ['surgery-picklists'],
    queryFn: () => api.get('/surgery/picklists').then(r => r.data),
    staleTime: 5 * 60_000,
  })
  const surgeons = picklists?.surgeons || []

  // Summary data
  const { data, isLoading } = useQuery({
    queryKey: ['surgery-report-summary', from, to, facility, surgeon],
    queryFn: () =>
      api.get('/surgery/reports/summary', {
        params: {
          from,
          to,
          facility: facility || undefined,
          surgeon: surgeon || undefined,
        },
      }).then(r => r.data),
    enabled: !!(from && to),
  })

  // Params object passed to drill-down / CSV
  const filterParams = {
    from,
    to,
    ...(facility ? { facility } : {}),
    ...(surgeon ? { surgeon } : {}),
  }

  function openDrill(tile, bucket) {
    setDrillDown({ tile, bucket: bucket || undefined })
  }

  const s = data

  return (
    <div>
      {/* Page header */}
      <div className="mb-4">
        <h1 className="text-lg font-semibold text-ink">Reports</h1>
        <p className="text-[13px] text-muted">Surgery activity summary for the selected period.</p>
      </div>

      {/* Filter bar */}
      <div className="mb-5 flex flex-wrap items-center gap-3">
        {/* Date preset */}
        <select
          value={preset}
          onChange={(e) => setPreset(e.target.value)}
          className="rounded border border-border-subtle px-2.5 py-1.5 text-[13px] focus:border-plum-500 focus:outline-none"
        >
          <option value="this_month">This Month</option>
          <option value="last_month">Last Month</option>
          <option value="last_30">Last 30 Days</option>
          <option value="last_90">Last 90 Days</option>
          <option value="custom">Custom</option>
        </select>

        {/* Custom date inputs */}
        {preset === 'custom' && (
          <>
            <input
              type="date"
              value={customFrom}
              onChange={(e) => setCustomFrom(e.target.value)}
              className="rounded border border-border-subtle px-2.5 py-1.5 text-[13px] focus:border-plum-500 focus:outline-none"
            />
            <span className="text-[13px] text-muted">to</span>
            <input
              type="date"
              value={customTo}
              onChange={(e) => setCustomTo(e.target.value)}
              className="rounded border border-border-subtle px-2.5 py-1.5 text-[13px] focus:border-plum-500 focus:outline-none"
            />
          </>
        )}

        {/* Facility */}
        <select
          value={facility}
          onChange={(e) => setFacility(e.target.value)}
          className="rounded border border-border-subtle px-2.5 py-1.5 text-[13px] focus:border-plum-500 focus:outline-none"
        >
          {FACILITY_OPTIONS.map(o => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>

        {/* Surgeon */}
        <select
          value={surgeon}
          onChange={(e) => setSurgeon(e.target.value)}
          className="rounded border border-border-subtle px-2.5 py-1.5 text-[13px] focus:border-plum-500 focus:outline-none"
        >
          <option value="">All Surgeons</option>
          {surgeons.map(name => (
            <option key={name} value={name}>{name}</option>
          ))}
        </select>
      </div>

      {/* Period label */}
      {from && to && (
        <div className="mb-4 text-[12px] text-muted">
          {fmt.date(from)} – {fmt.date(to)}
        </div>
      )}

      {isLoading ? (
        <LoadingState />
      ) : !s ? (
        <div className="py-12 text-center text-[13px] text-muted">
          Select a date range to load the report.
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">

          {/* ── 1. Status Funnel ─────────────────────────────────────── */}
          <Tile title="Status Funnel" onClick={() => openDrill('status_funnel')}>
            <div className="space-y-1.5">
              {STATUS_ORDER.filter(st => s.status_funnel?.by_status?.[st] != null).map(st => (
                <div
                  key={st}
                  className="flex items-center justify-between cursor-pointer hover:text-plum-700 text-[13px]"
                  onClick={(e) => { e.stopPropagation(); openDrill('status_funnel', st) }}
                >
                  <span className="text-muted">{STATUS_LABEL[st] || st}</span>
                  <span className="font-semibold text-ink">
                    {s.status_funnel.by_status[st]}
                  </span>
                </div>
              ))}
              {(!s.status_funnel?.by_status || Object.keys(s.status_funnel.by_status).length === 0) && (
                <div className="text-[13px] text-muted">No data.</div>
              )}
            </div>
          </Tile>

          {/* ── 2. Not Ready ─────────────────────────────────────────── */}
          <Tile title="Not Ready (≤14 Days)" onClick={() => openDrill('not_ready')}>
            <BigNumber value={s.not_ready?.total} label="surgeries with open blockers" />
            {s.not_ready?.by_blocker && (
              <div className="mt-3 flex flex-wrap gap-1.5">
                {Object.entries(s.not_ready.by_blocker)
                  .filter(([, n]) => n > 0)
                  .map(([key, n]) => (
                    <button
                      key={key}
                      onClick={(e) => { e.stopPropagation(); openDrill('not_ready', key) }}
                      className="inline-flex items-center gap-1 rounded-full bg-amber-50 border border-amber-200
                                 px-2 py-0.5 text-[11px] font-medium text-amber-800 hover:bg-amber-100"
                    >
                      {BLOCKER_LABEL[key] || key}
                      <span className="font-bold">{n}</span>
                    </button>
                  ))}
              </div>
            )}
          </Tile>

          {/* ── 3. Completed ─────────────────────────────────────────── */}
          <Tile title="Completed" onClick={() => openDrill('completed')}>
            <BigNumber value={s.completed?.total} label="this period" />
            {s.completed?.by_classification && (
              <div className="mt-3 space-y-1">
                {Object.entries(s.completed.by_classification).map(([cls, n]) => (
                  <div
                    key={cls}
                    className="flex items-center justify-between cursor-pointer hover:text-plum-700 text-[13px]"
                    onClick={(e) => { e.stopPropagation(); openDrill('completed', cls) }}
                  >
                    <span className="text-muted capitalize">{cls.replace(/_/g, ' ')}</span>
                    <span className="font-semibold text-ink">{n}</span>
                  </div>
                ))}
              </div>
            )}
            {s.completed?.delta != null && (
              <div className={`mt-3 text-[12px] font-medium ${s.completed.delta >= 0 ? 'text-green-700' : 'text-red-600'}`}>
                {s.completed.delta >= 0 ? '▲' : '▼'} {Math.abs(s.completed.delta)} vs prior period
              </div>
            )}
          </Tile>

          {/* ── 4. Cycle Time ────────────────────────────────────────── */}
          <Tile title="Cycle Time" onClick={() => openDrill('cycle_time')}>
            <div className="space-y-3">
              <div>
                <div className="text-3xl font-bold text-ink">
                  {s.cycle_time?.avg_lead_days != null
                    ? `${parseFloat(s.cycle_time.avg_lead_days).toFixed(1)}d`
                    : '—'}
                </div>
                <div className="mt-0.5 text-[12px] text-muted">Avg lead time</div>
              </div>
              <div>
                <div className="text-2xl font-bold text-ink">
                  {s.cycle_time?.reschedule_rate != null
                    ? `${(s.cycle_time.reschedule_rate * 100).toFixed(0)}%`
                    : '—'}
                </div>
                <div className="mt-0.5 text-[12px] text-muted">
                  Reschedule rate
                  {s.cycle_time?.avg_reschedules != null && (
                    <span> · {parseFloat(s.cycle_time.avg_reschedules).toFixed(1)} avg reschedules</span>
                  )}
                </div>
              </div>
              {s.cycle_time?.n != null && (
                <div className="text-[11px] text-muted">n={s.cycle_time.n}</div>
              )}
            </div>
          </Tile>

          {/* ── 5. Payment Posting Backlog ───────────────────────────── */}
          <Tile title="Payment Posting Backlog" onClick={() => openDrill('posting_backlog')}>
            <BigNumber value={s.posting_backlog?.count} label="unposted payments" />
            <div className="mt-3 space-y-1 text-[13px]">
              {s.posting_backlog?.total_amount != null && (
                <div className="flex items-center justify-between">
                  <span className="text-muted">Total amount</span>
                  <span className="font-semibold text-ink">
                    {fmt.currency(s.posting_backlog.total_amount)}
                  </span>
                </div>
              )}
              {s.posting_backlog?.oldest_age_days != null && (
                <div className="flex items-center justify-between">
                  <span className="text-muted">Oldest</span>
                  <span className="font-semibold text-ink">
                    {s.posting_backlog.oldest_age_days}d
                  </span>
                </div>
              )}
            </div>
          </Tile>

          {/* ── 6. Utilization ───────────────────────────────────────── */}
          <Tile title="Utilization" onClick={() => openDrill('utilization')}>
            <BigNumber
              value={s.utilization?.overall_pct != null ? `${Math.round(s.utilization.overall_pct)}%` : '—'}
              label={`${s.utilization?.booked ?? '—'} booked / ${s.utilization?.capacity ?? '—'} capacity`}
            />
            {s.utilization?.by_facility && Object.keys(s.utilization.by_facility).length > 0 && (
              <div className="mt-3 space-y-1.5">
                {Object.entries(s.utilization.by_facility).map(([fac, info]) => (
                  <div
                    key={fac}
                    className="flex items-center justify-between cursor-pointer hover:text-plum-700 text-[13px]"
                    onClick={(e) => { e.stopPropagation(); openDrill('utilization', fac) }}
                  >
                    <span className="text-muted">
                      {FACILITY_LABEL[fac] || fac}
                    </span>
                    <span className="text-[12px] text-ink">
                      <span className="font-semibold">{Math.round(info.pct ?? 0)}%</span>
                      <span className="text-muted ml-1">({info.booked}/{info.capacity})</span>
                    </span>
                  </div>
                ))}
              </div>
            )}
          </Tile>

        </div>
      )}

      {/* Drill-down panel */}
      {drillDown && (
        <DrillDown
          tile={drillDown.tile}
          bucket={drillDown.bucket}
          params={filterParams}
          onClose={() => setDrillDown(null)}
        />
      )}
    </div>
  )
}
