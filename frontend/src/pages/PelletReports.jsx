import { useState, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Download, X } from 'lucide-react'
import api, { fmt } from '../utils/api'
import LoadingState from '../components/LoadingState'

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

// ─── Pellet status labels + order ──────────────────────────────────────────

const STATUS_LABEL = {
  new:          'New',
  in_progress:  'In Progress',
  inserted:     'Inserted',
  billed:       'Billed',
  cancelled:    'Cancelled',
  rescheduled:  'Rescheduled',
}

const STATUS_ORDER = ['new', 'in_progress', 'inserted', 'billed', 'rescheduled', 'cancelled']

// ─── Location options ───────────────────────────────────────────────────────

const LOCATION_OPTIONS = [
  { value: '',              label: 'All Locations' },
  { value: 'white_plains',  label: 'White Plains' },
  { value: 'brandywine',    label: 'Brandywine' },
  { value: 'arlington',     label: 'Arlington' },
]

// ─── Blocker labels ─────────────────────────────────────────────────────────

const BLOCKER_LABEL = {
  mammo:   'Mammo',
  labs:    'Labs',
  consent: 'Consent',
}

// ─── Kind labels (Title Case) ───────────────────────────────────────────────

function kindLabel(k) {
  if (!k) return k
  return k.charAt(0).toUpperCase() + k.slice(1).toLowerCase()
}

// ─── CSV download helper ─────────────────────────────────────────────────────

async function downloadCSV({ tile, params }) {
  const resp = await api.get(`/pellets/reports/${tile}/rows`, {
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
    queryKey: ['pellet-report-rows', tile, params.from, params.to, params.location, params.provider, bucket],
    queryFn: () =>
      api.get(`/pellets/reports/${tile}/rows`, { params: queryParams }).then(r => r.data),
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

export default function PelletReports() {
  // Filter state
  const [preset, setPreset] = useState('this_month')
  const [customFrom, setCustomFrom] = useState('')
  const [customTo, setCustomTo] = useState('')
  const [location, setLocation] = useState('')
  const [provider, setProvider] = useState('')

  // Drill-down state: { tile, bucket }
  const [drillDown, setDrillDown] = useState(null)

  // Resolve date range
  const dateRange = useMemo(() => {
    if (preset === 'custom') return { from: customFrom, to: customTo }
    return resolvePreset(preset) || { from: '', to: '' }
  }, [preset, customFrom, customTo])

  const from = dateRange.from
  const to = dateRange.to

  // Summary data
  const { data, isLoading } = useQuery({
    queryKey: ['pellet-report-summary', from, to, location, provider],
    queryFn: () =>
      api.get('/pellets/reports/summary', {
        params: {
          from,
          to,
          location: location || undefined,
          provider: provider || undefined,
        },
      }).then(r => r.data),
    enabled: !!(from && to),
  })

  // Provider list from loaded summary
  const providers = data?.providers || []

  // Params object passed to drill-down / CSV
  const filterParams = {
    from,
    to,
    ...(location ? { location } : {}),
    ...(provider ? { provider } : {}),
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
        <p className="text-[13px] text-muted">Pellet activity summary for the selected period.</p>
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

        {/* Location */}
        <select
          value={location}
          onChange={(e) => setLocation(e.target.value)}
          className="rounded border border-border-subtle px-2.5 py-1.5 text-[13px] focus:border-plum-500 focus:outline-none"
        >
          {LOCATION_OPTIONS.map(o => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>

        {/* Provider */}
        <select
          value={provider}
          onChange={(e) => setProvider(e.target.value)}
          className="rounded border border-border-subtle px-2.5 py-1.5 text-[13px] focus:border-plum-500 focus:outline-none"
        >
          <option value="">All Providers</option>
          {providers.map(name => (
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

          {/* ── 1. Visit Status Funnel ───────────────────────────────── */}
          <Tile title="Visit Status Funnel" onClick={() => openDrill('status_funnel')}>
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
              {/* Statuses not in the canonical order but present in data */}
              {s.status_funnel?.by_status && Object.entries(s.status_funnel.by_status)
                .filter(([st]) => !STATUS_ORDER.includes(st))
                .map(([st, n]) => (
                  <div
                    key={st}
                    className="flex items-center justify-between cursor-pointer hover:text-plum-700 text-[13px]"
                    onClick={(e) => { e.stopPropagation(); openDrill('status_funnel', st) }}
                  >
                    <span className="text-muted">{STATUS_LABEL[st] || st}</span>
                    <span className="font-semibold text-ink">{n}</span>
                  </div>
                ))}
              {(!s.status_funnel?.by_status || Object.keys(s.status_funnel.by_status).length === 0) && (
                <div className="text-[13px] text-muted">No data.</div>
              )}
            </div>
          </Tile>

          {/* ── 2. Insertions ─────────────────────────────────────────── */}
          <Tile title="Insertions" onClick={() => openDrill('insertions')}>
            <BigNumber value={s.insertions?.total} label="this period" />
            {s.insertions?.by_kind && Object.keys(s.insertions.by_kind).length > 0 && (
              <div className="mt-3 space-y-1">
                {Object.entries(s.insertions.by_kind).map(([kind, n]) => (
                  <div
                    key={kind}
                    className="flex items-center justify-between cursor-pointer hover:text-plum-700 text-[13px]"
                    onClick={(e) => { e.stopPropagation(); openDrill('insertions', kind) }}
                  >
                    <span className="text-muted">{kindLabel(kind)}</span>
                    <span className="font-semibold text-ink">{n}</span>
                  </div>
                ))}
              </div>
            )}
            {s.insertions?.delta != null && (
              <div className={`mt-3 text-[12px] font-medium ${s.insertions.delta >= 0 ? 'text-green-700' : 'text-red-600'}`}>
                {s.insertions.delta >= 0 ? '▲' : '▼'} {Math.abs(s.insertions.delta)} vs prior period
              </div>
            )}
          </Tile>

          {/* ── 3. Recall Due ─────────────────────────────────────────── */}
          <Tile title="Recall Due" onClick={() => openDrill('recall_due')}>
            <BigNumber value={s.recall_due?.total} label="patients due for recall" />
            <div className="mt-3 space-y-1.5 text-[13px]">
              {s.recall_due?.overdue != null && (
                <div
                  className="flex items-center justify-between cursor-pointer hover:text-plum-700"
                  onClick={(e) => { e.stopPropagation(); openDrill('recall_due', 'overdue') }}
                >
                  <span className="text-red-600">Overdue</span>
                  <span className="font-semibold text-ink">{s.recall_due.overdue}</span>
                </div>
              )}
              {s.recall_due?.due_soon != null && (
                <div
                  className="flex items-center justify-between cursor-pointer hover:text-plum-700"
                  onClick={(e) => { e.stopPropagation(); openDrill('recall_due', 'due_soon') }}
                >
                  <span className="text-amber-600">Due Soon</span>
                  <span className="font-semibold text-ink">{s.recall_due.due_soon}</span>
                </div>
              )}
            </div>
          </Tile>

          {/* ── 4. Prerequisites Not Ready ────────────────────────────── */}
          <Tile title="Prerequisites Not Ready" onClick={() => openDrill('prerequisites')}>
            <BigNumber value={s.prerequisites?.total} label="patients with open blockers" />
            {s.prerequisites?.by_blocker && (
              <div className="mt-3 flex flex-wrap gap-1.5">
                {Object.entries(s.prerequisites.by_blocker)
                  .filter(([, n]) => n > 0)
                  .map(([key, n]) => (
                    <button
                      key={key}
                      onClick={(e) => { e.stopPropagation(); openDrill('prerequisites', key) }}
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

          {/* ── 5. Billing Backlog ────────────────────────────────────── */}
          <Tile title="Billing Backlog" onClick={() => openDrill('billing_backlog')}>
            <BigNumber value={s.billing_backlog?.count} label="unbilled insertions" />
            <div className="mt-3 space-y-1 text-[13px]">
              {s.billing_backlog?.total_amount != null && (
                <div className="flex items-center justify-between">
                  <span className="text-muted">Total amount</span>
                  <span className="font-semibold text-ink">
                    {fmt.currency(s.billing_backlog.total_amount)}
                  </span>
                </div>
              )}
            </div>
          </Tile>

          {/* ── 6. Inventory Health ───────────────────────────────────── */}
          <Tile title="Inventory Health" onClick={() => openDrill('inventory_health')}>
            <BigNumber value={s.inventory_health?.total_on_hand} label="pellets on hand" />
            {s.inventory_health?.by_location && Object.keys(s.inventory_health.by_location).length > 0 && (
              <div className="mt-3 space-y-1">
                {Object.entries(s.inventory_health.by_location).map(([loc, n]) => (
                  <div
                    key={loc}
                    className="flex items-center justify-between cursor-pointer hover:text-plum-700 text-[13px]"
                    onClick={(e) => { e.stopPropagation(); openDrill('inventory_health', loc) }}
                  >
                    <span className="text-muted capitalize">{loc.replace(/_/g, ' ')}</span>
                    <span className="font-semibold text-ink">{n}</span>
                  </div>
                ))}
              </div>
            )}
            {(s.inventory_health?.expiring_lots > 0 || s.inventory_health?.below_reorder > 0) && (
              <div className="mt-3 flex flex-wrap gap-1.5">
                {s.inventory_health?.expiring_lots > 0 && (
                  <span className="inline-flex items-center rounded-full bg-amber-50 border border-amber-200
                                   px-2 py-0.5 text-[11px] font-medium text-amber-800">
                    {s.inventory_health.expiring_lots} expiring
                  </span>
                )}
                {s.inventory_health?.below_reorder > 0 && (
                  <span className="inline-flex items-center rounded-full bg-red-50 border border-red-200
                                   px-2 py-0.5 text-[11px] font-medium text-red-700">
                    {s.inventory_health.below_reorder} below reorder
                  </span>
                )}
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
