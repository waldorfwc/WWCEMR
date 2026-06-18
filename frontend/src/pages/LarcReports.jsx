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

// ─── Location options ───────────────────────────────────────────────────────

const LOCATION_OPTIONS = [
  { value: '',              label: 'All Locations' },
  { value: 'white_plains',  label: 'White Plains' },
  { value: 'brandywine',    label: 'Brandywine' },
  { value: 'arlington',     label: 'Arlington' },
]

// ─── Humanize a bucket / snake_case key to Title Case ───────────────────────

function humanize(k) {
  if (k == null) return k
  return String(k)
    .replace(/_/g, ' ')
    .replace(/\b\w/g, c => c.toUpperCase())
}

// ─── Outstanding enrollment stage labels ────────────────────────────────────

const STAGE_LABEL = {
  needs_enrollment:     'Needs Enrollment',
  needs_fax:            'Needs Fax',
  awaiting_receipt:     'Awaiting Receipt',
  received_not_notified:'Received Not Notified',
}

const STAGE_ORDER = ['needs_enrollment', 'needs_fax', 'awaiting_receipt', 'received_not_notified']

// ─── Insertion category labels ──────────────────────────────────────────────

const CATEGORY_LABEL = {
  larc:             'LARC',
  office_procedure: 'Office Procedure',
}

// ─── CSV download helper ─────────────────────────────────────────────────────

async function downloadCSV({ tile, params }) {
  const resp = await api.get(`/larc/reports/${tile}/rows`, {
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
    queryKey: ['larc-report-rows', tile, params.from, params.to, params.location, params.device_type_id, bucket],
    queryFn: () =>
      api.get(`/larc/reports/${tile}/rows`, { params: queryParams }).then(r => r.data),
  })

  const items = data?.items || []
  const columns = items.length > 0 ? Object.keys(items[0]) : []

  const title = bucket
    ? `${humanize(tile)} — ${humanize(bucket)}`
    : humanize(tile)

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
          <h2 className="text-base font-semibold text-ink">{title}</h2>
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

export default function LarcReports() {
  // Filter state
  const [preset, setPreset] = useState('this_month')
  const [customFrom, setCustomFrom] = useState('')
  const [customTo, setCustomTo] = useState('')
  const [location, setLocation] = useState('')
  const [deviceTypeId, setDeviceTypeId] = useState('')

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
    queryKey: ['larc-report-summary', from, to, location, deviceTypeId],
    queryFn: () =>
      api.get('/larc/reports/summary', {
        params: {
          from,
          to,
          location: location || undefined,
          device_type_id: deviceTypeId || undefined,
        },
      }).then(r => r.data),
    enabled: !!(from && to),
  })

  // Device types from loaded summary
  const deviceTypes = data?.device_types || []

  // Params object passed to drill-down / CSV
  const filterParams = {
    from,
    to,
    ...(location ? { location } : {}),
    ...(deviceTypeId ? { device_type_id: deviceTypeId } : {}),
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
        <p className="text-[13px] text-muted">Device tracking summary for the selected period.</p>
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

        {/* Device Type */}
        <select
          value={deviceTypeId}
          onChange={(e) => setDeviceTypeId(e.target.value)}
          className="rounded border border-border-subtle px-2.5 py-1.5 text-[13px] focus:border-plum-500 focus:outline-none"
        >
          <option value="">All Types</option>
          {deviceTypes.map(dt => (
            <option key={dt.id} value={dt.id}>{dt.name}</option>
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

          {/* ── 1. Workflow Funnel ────────────────────────────────────── */}
          <Tile title="Workflow Funnel" onClick={() => openDrill('workflow_funnel')}>
            <div className="space-y-1.5">
              {s.workflow_funnel?.by_bucket && Object.keys(s.workflow_funnel.by_bucket).length > 0 ? (
                Object.entries(s.workflow_funnel.by_bucket)
                  .sort((a, b) => b[1] - a[1])
                  .map(([key, n]) => (
                    <div
                      key={key}
                      className="flex items-center justify-between cursor-pointer hover:text-plum-700 text-[13px]"
                      onClick={(e) => { e.stopPropagation(); openDrill('workflow_funnel', key) }}
                    >
                      <span className="text-muted">{humanize(key)}</span>
                      <span className="font-semibold text-ink">{n}</span>
                    </div>
                  ))
              ) : (
                <div className="text-[13px] text-muted">No data.</div>
              )}
            </div>
          </Tile>

          {/* ── 2. Outstanding Enrollment ─────────────────────────────── */}
          <Tile title="Outstanding Enrollment" onClick={() => openDrill('outstanding_enrollment')}>
            <BigNumber value={s.outstanding_enrollment?.total} label="patients outstanding" />
            {s.outstanding_enrollment?.by_stage && (
              <div className="mt-3 flex flex-wrap gap-1.5">
                {STAGE_ORDER
                  .filter(st => (s.outstanding_enrollment.by_stage[st] || 0) > 0)
                  .map(st => (
                    <button
                      key={st}
                      onClick={(e) => { e.stopPropagation(); openDrill('outstanding_enrollment', st) }}
                      className="inline-flex items-center gap-1 rounded-full bg-amber-50 border border-amber-200
                                 px-2 py-0.5 text-[11px] font-medium text-amber-800 hover:bg-amber-100"
                    >
                      {STAGE_LABEL[st] || humanize(st)}
                      <span className="font-bold">{s.outstanding_enrollment.by_stage[st]}</span>
                    </button>
                  ))}
              </div>
            )}
          </Tile>

          {/* ── 3. Insertions ─────────────────────────────────────────── */}
          <Tile title="Insertions" onClick={() => openDrill('insertions')}>
            <BigNumber value={s.insertions?.total} label="this period" />
            {s.insertions?.by_category && Object.keys(s.insertions.by_category).length > 0 && (
              <div className="mt-3 space-y-1">
                {Object.entries(s.insertions.by_category).map(([cat, n]) => (
                  <div
                    key={cat}
                    className="flex items-center justify-between cursor-pointer hover:text-plum-700 text-[13px]"
                    onClick={(e) => { e.stopPropagation(); openDrill('insertions', cat) }}
                  >
                    <span className="text-muted">{CATEGORY_LABEL[cat] || humanize(cat)}</span>
                    <span className="font-semibold text-ink">{n}</span>
                  </div>
                ))}
              </div>
            )}
            {s.insertions?.delta != null && (
              <div className={`mt-3 text-[12px] font-medium ${s.insertions.delta >= 0 ? 'text-green-700' : 'text-red-600'}`}>
                {s.insertions.delta >= 0 ? '▲' : '▼'} {Math.abs(s.insertions.delta)} vs prior
                {s.insertions.prior_total != null && ` (${s.insertions.prior_total})`}
              </div>
            )}
          </Tile>

          {/* ── 4. Billing Backlog ────────────────────────────────────── */}
          <Tile title="Billing Backlog" onClick={() => openDrill('billing_backlog')}>
            <BigNumber value={s.billing_backlog?.count} label="unbilled insertions" />
          </Tile>

          {/* ── 5. Owed Patients ──────────────────────────────────────── */}
          <Tile title="Owed Patients" onClick={() => openDrill('owed_patients')}>
            <BigNumber value={s.owed_patients?.total} label="total owed" />
            <div className="mt-3 space-y-1.5 text-[13px]">
              {s.owed_patients?.owed_count != null && (
                <div
                  className="flex items-center justify-between cursor-pointer hover:text-plum-700"
                  onClick={(e) => { e.stopPropagation(); openDrill('owed_patients') }}
                >
                  <span className="text-muted">Owed</span>
                  <span className="font-semibold text-ink">{s.owed_patients.owed_count}</span>
                </div>
              )}
              {s.owed_patients?.awaiting_replacement != null && (
                <div
                  className="flex items-center justify-between cursor-pointer hover:text-plum-700"
                  onClick={(e) => { e.stopPropagation(); openDrill('owed_patients', 'awaiting_replacement') }}
                >
                  <span className="text-muted">Awaiting Replacement</span>
                  <span className="font-semibold text-ink">{s.owed_patients.awaiting_replacement}</span>
                </div>
              )}
            </div>
          </Tile>

          {/* ── 6. Inventory Health ───────────────────────────────────── */}
          <Tile title="Inventory Health" onClick={() => openDrill('inventory_health')}>
            <BigNumber value={s.inventory_health?.total_on_hand} label="devices on hand" />
            {s.inventory_health?.by_type && Object.keys(s.inventory_health.by_type).length > 0 && (
              <div className="mt-3 space-y-1">
                {Object.entries(s.inventory_health.by_type).map(([name, n]) => (
                  <div
                    key={name}
                    className="flex items-center justify-between text-[13px]"
                  >
                    <span className="text-muted">{name}</span>
                    <span className="font-semibold text-ink">{n}</span>
                  </div>
                ))}
              </div>
            )}
            {(s.inventory_health?.expiring > 0 || s.inventory_health?.below_reorder > 0) && (
              <div className="mt-3 flex flex-wrap gap-1.5">
                {s.inventory_health?.expiring > 0 && (
                  <button
                    onClick={(e) => { e.stopPropagation(); openDrill('inventory_health', 'expiring') }}
                    className="inline-flex items-center rounded-full bg-amber-50 border border-amber-200
                               px-2 py-0.5 text-[11px] font-medium text-amber-800 hover:bg-amber-100"
                  >
                    {s.inventory_health.expiring} expiring
                  </button>
                )}
                {s.inventory_health?.below_reorder > 0 && (
                  <button
                    onClick={(e) => { e.stopPropagation(); openDrill('inventory_health', 'below_reorder') }}
                    className="inline-flex items-center rounded-full bg-red-50 border border-red-200
                               px-2 py-0.5 text-[11px] font-medium text-red-700 hover:bg-red-100"
                  >
                    {s.inventory_health.below_reorder} below reorder
                  </button>
                )}
              </div>
            )}
          </Tile>

          {/* ── 7. Insertion Outcomes ─────────────────────────────────── */}
          <Tile title="Insertion Outcomes" onClick={() => openDrill('insertion_outcomes')}>
            <div className="space-y-1.5 text-[13px]">
              {s.insertion_outcomes?.success != null && (
                <div
                  className="flex items-center justify-between cursor-pointer hover:text-plum-700"
                  onClick={(e) => { e.stopPropagation(); openDrill('insertion_outcomes', 'success') }}
                >
                  <span className="text-green-700">Success</span>
                  <span className="font-semibold text-ink">{s.insertion_outcomes.success}</span>
                </div>
              )}
              {s.insertion_outcomes?.failed_unused != null && (
                <div
                  className="flex items-center justify-between cursor-pointer hover:text-plum-700"
                  onClick={(e) => { e.stopPropagation(); openDrill('insertion_outcomes', 'failed_unused') }}
                >
                  <span className="text-amber-600">Failed (Unused)</span>
                  <span className="font-semibold text-ink">{s.insertion_outcomes.failed_unused}</span>
                </div>
              )}
              {s.insertion_outcomes?.failed_used != null && (
                <div
                  className="flex items-center justify-between cursor-pointer hover:text-plum-700"
                  onClick={(e) => { e.stopPropagation(); openDrill('insertion_outcomes', 'failed_used') }}
                >
                  <span className="text-red-600">Failed (Used)</span>
                  <span className="font-semibold text-ink">{s.insertion_outcomes.failed_used}</span>
                </div>
              )}
            </div>
            {s.insertion_outcomes?.failure_rate != null && (
              <div className="mt-3 text-[12px] text-muted">
                Failure Rate{' '}
                <span className="font-semibold text-ink">
                  {(s.insertion_outcomes.failure_rate * 100).toFixed(0)}%
                </span>
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
