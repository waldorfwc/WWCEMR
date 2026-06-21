import { useState, useMemo } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import {
  AlertTriangle, ArrowDown, ArrowUp, ArrowUpDown, Box,
  Check, Clock, PackageCheck, Plus, Search, Truck, X,
} from 'lucide-react'
import api, { fmt } from '../utils/api'
import { OWNERSHIP_TONES, OWNERSHIP_LABELS } from './LarcDevices'
import EmptyState from '../components/EmptyState'


const BUCKET_DEFS = [
  { k: 'outstanding',                    l: 'Outstanding',          tone: 'gray',    descr: 'All active assignments' },
  { k: 'incomplete',                     l: 'Incomplete',           tone: 'amber',   descr: 'Missing required intake info' },
  { k: 'new',                            l: 'New',                  tone: 'blue',    descr: 'Just created, nothing started' },
  { k: 'needs_benefits',                 l: 'Needs Benefits',       tone: 'amber',   descr: 'Benefits not yet verified' },
  { k: 'needs_enrollment',               l: 'Needs Enrollment',     tone: 'amber',   descr: 'Pharmacy-order: enrollment form not signed' },
  { k: 'needs_fax',                      l: 'Needs Fax',            tone: 'amber',   descr: 'Enrollment signed but request not yet faxed to pharmacy' },
  { k: 'awaiting_receipt',               l: 'Awaiting Receipt',     tone: 'blue',    descr: 'Request faxed, waiting for device from pharmacy' },
  { k: 'received_not_notified',          l: 'Received — Notify',    tone: 'amber',   descr: 'Device arrived, patient not yet notified' },
  { k: 'appt_scheduled',                 l: 'Appt Scheduled',       tone: 'blue',    descr: 'Insertion appointment booked' },
  { k: 'checked_out',                    l: 'Checked Out',          tone: 'violet',  descr: 'Device pulled from cabinet, awaiting outcome' },
  { k: 'inserted_not_billed',            l: 'Inserted — To Bill',   tone: 'amber',   descr: 'Inserted successfully, claim # not yet recorded' },
  { k: 'failed_replacement_unrequested', l: 'Failed — Need Replacement', tone: 'red', descr: 'Defective device, replacement not yet requested' },
  { k: 'failed_replacement_pending',     l: 'Failed — Pending',     tone: 'red',     descr: 'Replacement device pending from manufacturer' },
  { k: 'checkout_unacknowledged',        l: 'Unack Checkout',       tone: 'red',     descr: 'Checkout sat >24h with no outcome recorded' },
  { k: 'owed',                           l: 'Owed List',            tone: 'gray',    descr: 'Patient owed a device (reallocated)' },
  // Office-procedure (NovaSure, Bensta) — single-use consumed in a surgery
  { k: 'op_needs_device',                l: 'OP — Pick Device',     tone: 'amber',   descr: 'Office-procedure: surgery scheduled, no device picked yet' },
  { k: 'op_device_assigned',             l: 'OP — Assigned',        tone: 'blue',    descr: 'Office-procedure: device picked, awaiting procedure' },
  { k: 'op_consumed_not_billed',         l: 'OP — To Bill',         tone: 'amber',   descr: 'Office-procedure: device consumed, claim # not yet recorded' },
]


export default function Larc() {
  const navigate = useNavigate()
  const [filterBucket, setFilterBucket] = useState('')
  const [search, setSearch] = useState('')
  const [startOpen, setStartOpen] = useState(false)

  const { data: dash } = useQuery({
    queryKey: ['larc-dashboard'],
    queryFn: () => api.get('/larc/dashboard').then(r => r.data),
  })

  const { data: list } = useQuery({
    queryKey: ['larc-assignments', filterBucket, search],
    queryFn: () => api.get('/larc/assignments', {
      params: { bucket: filterBucket || undefined, search: search || undefined },
    }).then(r => r.data),
  })

  const qc = useQueryClient()
  const ackCheckout = useMutation({
    mutationFn: (checkoutId) =>
      api.post(`/larc/checkouts/${checkoutId}/acknowledge`).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['larc-dashboard'] }),
    onError: (e) => alert(e?.response?.data?.detail || 'Acknowledge failed'),
  })

  // Per-column sort + filter on the assignment list (client-side).
  // sortBy=null means default order (whatever the API returns).
  const [sortBy, setSortBy] = useState(null)
  const [sortDir, setSortDir] = useState('asc')
  const [colFilters, setColFilters] = useState({
    patient: '', device: '', flow: '', status: '', created: '',
  })

  const toggleSort = (key) => {
    if (sortBy !== key) { setSortBy(key); setSortDir('asc'); return }
    if (sortDir === 'asc') { setSortDir('desc'); return }
    setSortBy(null); setSortDir('asc')  // 3rd click clears
  }

  const SortArrow = ({ k }) => {
    if (sortBy !== k) return <ArrowUpDown size={11} className="inline opacity-40 ml-1" />
    return sortDir === 'asc'
      ? <ArrowUp size={11} className="inline ml-1 text-plum-700" />
      : <ArrowDown size={11} className="inline ml-1 text-plum-700" />
  }

  const ACCESSORS = {
    patient: (a) => `${a.patient_name || ''} ${a.chart_number || ''}`,
    device:  (a) => `${a.device_our_id || ''} ${a.device_type_name || ''}`,
    flow:    (a) => a.source_flow || '',
    status:  (a) => a.status || '',
    created: (a) => a.created_at || '',
  }

  const visibleAssignments = useMemo(() => {
    const rows = list?.assignments || []
    // filter
    const filters = Object.entries(colFilters)
      .filter(([_, v]) => v && v.trim())
      .map(([k, v]) => [k, v.trim().toLowerCase()])
    let out = rows.filter(a =>
      filters.every(([k, needle]) => ACCESSORS[k](a).toLowerCase().includes(needle))
    )
    // sort
    if (sortBy) {
      const acc = ACCESSORS[sortBy]
      out = [...out].sort((x, y) => {
        const xv = acc(x).toLowerCase(); const yv = acc(y).toLowerCase()
        if (xv < yv) return sortDir === 'asc' ? -1 : 1
        if (xv > yv) return sortDir === 'asc' ?  1 : -1
        return 0
      })
    }
    return out
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [list, sortBy, sortDir, colFilters])

  return (
    <div>
      <div className="flex items-baseline justify-between mb-4 flex-wrap gap-y-3 gap-x-2">
        <div className="min-w-0">
          <h1 className="page-title flex items-center gap-2">
            <Box size={22} className="text-plum-700" />
            Device Tracking
          </h1>
          <p className="text-sm text-gray-500 mt-0.5">
            <span className="text-plum-700 font-medium">LARC</span> contraceptive devices ·
            {' '}<span className="text-teal-700 font-medium">Office Procedure Devices</span>
            {' '}(NovaSure, Bensta)
          </p>
        </div>
        <div className="flex flex-wrap justify-end gap-2">
          <button className="btn-primary" onClick={() => setStartOpen(true)}>
            <Plus size={13} /> Start LARC Process
          </button>
        </div>
      </div>

      {/* On-hand by ownership */}
      <div className="grid grid-cols-3 gap-2 mb-4">
        {[
          { label: 'Practice Owned', count: dash?.on_hand_by_ownership?.wwc_owned ?? 0 },
          { label: 'Patient Owned', count: dash?.on_hand_by_ownership?.patient_owned ?? 0 },
          { label: 'Practice Claimed', count: dash?.on_hand_by_ownership?.wwc_claimed ?? 0 },
        ].map(({ label, count }) => (
          <div key={label} className="card border border-plum-100 bg-plum-50/30 !p-2.5">
            <div className="text-[11px] uppercase tracking-wide text-plum-700">{label}</div>
            <div className="text-2xl display-number mt-0.5">{count}</div>
            <div className="text-[10px] text-gray-500">on hand</div>
          </div>
        ))}
      </div>

      {/* On-hand by device type — split by category */}
      {(() => {
        const cats = dash?.device_categories || {}
        const onHand = dash?.on_hand_by_type || {}
        const larcEntries = Object.entries(onHand).filter(([t]) => cats[t] !== 'office_procedure')
        const opEntries = Object.entries(onHand).filter(([t]) => cats[t] === 'office_procedure')
        const Section = ({ title, color, entries }) => entries.length === 0 ? null : (
          <div className="mb-4">
            <div className={`text-[11px] font-semibold uppercase tracking-wide ${color} mb-1.5`}>
              {title} <span className="text-gray-400 font-normal">({entries.reduce((s, [, c]) => s + c, 0)} on hand)</span>
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-6 gap-2">
              {entries.map(([type, count]) => {
                const isOP = cats[type] === 'office_procedure'
                const cls = isOP ? 'border-teal-100 bg-teal-50/40' : 'border-plum-100 bg-plum-50/30'
                const label = isOP ? 'text-teal-700' : 'text-plum-700'
                return (
                  <div key={type} className={`card border ${cls} !p-2.5`}>
                    <div className={`text-[11px] uppercase tracking-wide ${label}`}>{type}</div>
                    <div className="text-2xl display-number mt-0.5">{count}</div>
                    <div className="text-[10px] text-gray-500">on hand</div>
                  </div>
                )
              })}
            </div>
          </div>
        )
        return (
          <>
            <Section title="LARC" color="text-plum-700" entries={larcEntries} />
            <Section title="Office Procedure Devices" color="text-teal-700" entries={opEntries} />
          </>
        )
      })()}
      <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-6 gap-2 mb-4">
        {Object.keys(dash?.on_hand_by_type || {}).length === 0 && (
          <div className="col-span-full card">
            <EmptyState
              icon={Box}
              title="No devices in inventory yet"
              body={<>Click <strong>Devices</strong> to add your first LARC.</>}
              compact
            />
          </div>
        )}
      </div>

      {/* Reorder + expiring alerts */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mb-4">
        {/* Reorder */}
        <div className="card">
          <div className="flex items-center gap-1.5 mb-2">
            <Truck size={14} className="text-amber-700" />
            <h2 className="text-sm font-semibold text-gray-800">Reorder Alerts</h2>
            <span className="text-[11px] text-muted">(in-stock below threshold)</span>
          </div>
          {(dash?.reorder_alerts || []).length === 0 ? (
            <div className="text-xs text-gray-400 italic">All stocked devices are above threshold.</div>
          ) : (
            <ul className="text-xs space-y-1">
              {dash.reorder_alerts.map(r => (
                <li key={r.device_type} className="flex items-baseline justify-between bg-amber-50 px-2 py-1 rounded">
                  <span>
                    <strong>{r.device_type}</strong>
                    {r.category === 'office_procedure' && (
                      <span className="ml-1 text-[11px] bg-teal-100 text-teal-700 px-1 rounded">OP</span>
                    )}
                  </span>
                  <span className="text-amber-700">
                    {r.on_hand} on hand · threshold {r.threshold}
                    {r.suggested_quantity ? ` · order ${r.suggested_quantity}` : ''}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* Expiring soon (within 365 days) */}
        <div className="card">
          <div className="flex items-center gap-1.5 mb-2">
            <Clock size={14} className="text-red-700" />
            <h2 className="text-sm font-semibold text-gray-800">Expiring Within 365 Days</h2>
            <span className="text-[11px] text-muted">(move to unallocated)</span>
          </div>
          {(dash?.expiring_soon || []).length === 0 ? (
            <div className="text-xs text-gray-400 italic">No devices expiring within a year.</div>
          ) : (
            <ul className="text-xs space-y-1">
              {dash.expiring_soon.slice(0, 8).map(d => (
                <li key={d.device_id}
                    className="flex items-baseline justify-between cursor-pointer hover:bg-red-50 px-1 py-0.5 rounded"
                    onClick={() => navigate(`/larc/devices/${d.device_id}`)}>
                  <span><strong>{d.our_id}</strong> <span className="text-gray-500">— {d.device_type_name}</span></span>
                  <span className={`shrink-0 ${d.days_to_expiry < 90 ? 'text-red-700 font-semibold' : 'text-amber-700'}`}>
                    {d.days_to_expiry}d
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      {/* Pharmacy + checkout alerts */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mb-4">
        <div className="card">
          <div className="flex items-center gap-1.5 mb-2">
            <Truck size={14} className="text-red-700" />
            <h2 className="text-sm font-semibold text-gray-800">Overdue Pharmacy Orders</h2>
            <span className="text-[11px] text-muted">(faxed &gt;14d ago, not received)</span>
          </div>
          {(dash?.overdue_pharmacy_orders || []).length === 0 ? (
            <div className="text-xs text-gray-400 italic">No overdue pharmacy orders.</div>
          ) : (
            <ul className="text-xs space-y-1">
              {dash.overdue_pharmacy_orders.map(o => (
                <li key={o.assignment_id}
                    className="flex items-baseline justify-between cursor-pointer hover:bg-red-50 px-1 py-0.5 rounded"
                    onClick={() => navigate(`/larc/assignments/${o.assignment_id}`)}>
                  <span><strong>{o.patient_name}</strong> <span className="text-gray-500">— {o.device_type_name}</span></span>
                  <span className="text-red-700 shrink-0">{o.days_overdue}d past SLA</span>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="card">
          <div className="flex items-center gap-1.5 mb-2">
            <AlertTriangle size={14} className="text-red-700" />
            <h2 className="text-sm font-semibold text-gray-800">Unacknowledged Checkouts</h2>
            <span className="text-[11px] text-muted">(&gt;24h after request)</span>
          </div>
          {(dash?.unacknowledged_checkouts || []).length === 0 ? (
            <div className="text-xs text-gray-400 italic">All recent checkouts are acknowledged.</div>
          ) : (
            <ul className="text-xs space-y-1">
              {dash.unacknowledged_checkouts.map(c => (
                <li key={c.checkout_id} className="flex items-center justify-between gap-2 px-1 py-0.5 rounded bg-red-50">
                  <span className="min-w-0 truncate">
                    <strong>{c.patient_name}</strong>{' '}
                    <span className="text-gray-500">— pulled by {c.requested_by?.split('@')[0]}</span>
                  </span>
                  <span className="flex items-center gap-2 shrink-0">
                    <span className="text-red-700">{c.hours_outstanding}h</span>
                    <button
                      type="button"
                      onClick={() => ackCheckout.mutate(c.checkout_id)}
                      disabled={ackCheckout.isPending}
                      title="Acknowledge — I've seen this checkout"
                      className="text-[11px] px-2 py-0.5 rounded border border-red-300 bg-white text-red-800 hover:bg-red-100 disabled:opacity-50 inline-flex items-center gap-1"
                    >
                      <Check size={11} /> Ack
                    </button>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      {/* Devices ready to check out (MA action) */}
      <div className="mb-4">
        <LarcCheckoutCard />
      </div>

      {/* Bucket chip bar */}
      <div className="flex flex-wrap gap-1.5 mb-4">
        {BUCKET_DEFS.map(b => {
          const count = dash?.buckets?.[b.k] ?? 0
          const active = filterBucket === b.k
          const tones = {
            amber:  active ? 'bg-amber-500 text-white border-amber-600'  : 'bg-amber-50 text-amber-800 border-amber-200 hover:bg-amber-100',
            red:    active ? 'bg-red-600 text-white border-red-700'      : 'bg-red-50 text-red-800 border-red-200 hover:bg-red-100',
            blue:   active ? 'bg-blue-600 text-white border-blue-700'    : 'bg-blue-50 text-blue-800 border-blue-200 hover:bg-blue-100',
            violet: active ? 'bg-violet-600 text-white border-violet-700': 'bg-violet-50 text-violet-800 border-violet-200 hover:bg-violet-100',
            gray:   active ? 'bg-gray-700 text-white border-gray-800'    : 'bg-gray-50 text-gray-700 border-gray-200 hover:bg-gray-100',
          }
          return (
            <button key={b.k}
                    type="button"
                    title={b.descr}
                    onClick={() => setFilterBucket(active ? '' : b.k)}
                    className={`text-[11px] px-2 py-1 rounded-full border inline-flex items-center gap-1.5 transition ${tones[b.tone] || tones.gray}`}>
              <span>{b.l}</span>
              <span className="font-semibold opacity-80">{count}</span>
            </button>
          )
        })}
      </div>

      {/* Search */}
      <div className="card mb-3">
        <div className="relative max-w-md">
          <Search size={12} className="absolute left-2 top-2.5 text-muted" />
          <input className="input text-sm pl-7 w-full"
                 placeholder="Patient name or chart #…"
                 value={search}
                 onChange={e => setSearch(e.target.value)} />
        </div>
      </div>

      {/* Assignment list */}
      <div className="card !p-0 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-plum-50">
            <tr>
              {[
                { k: 'patient', l: 'Patient' },
                { k: 'device',  l: 'Device'  },
                { k: 'flow',    l: 'Flow'    },
                { k: 'status',  l: 'Status'  },
                { k: 'created', l: 'Created' },
              ].map(col => (
                <th key={col.k}
                    onClick={() => toggleSort(col.k)}
                    className="table-th cursor-pointer select-none hover:bg-plum-100">
                  {col.l}<SortArrow k={col.k} />
                </th>
              ))}
            </tr>
            <tr className="bg-white border-t border-border-subtle">
              {['patient', 'device', 'flow', 'status', 'created'].map(k => (
                <th key={k} className="px-2 py-1 align-top">
                  <div className="relative">
                    <input
                      className="input text-[11px] py-1 w-full"
                      placeholder="filter…"
                      value={colFilters[k]}
                      onChange={e => setColFilters(f => ({ ...f, [k]: e.target.value }))}
                    />
                    {colFilters[k] && (
                      <button
                        type="button"
                        onClick={() => setColFilters(f => ({ ...f, [k]: '' }))}
                        className="absolute right-1 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-700"
                        title="Clear filter">
                        <X size={11} />
                      </button>
                    )}
                  </div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {visibleAssignments.map(a => (
              <tr key={a.id}
                  className="hover:bg-plum-50 cursor-pointer"
                  onClick={() => navigate(`/larc/assignments/${a.id}`)}>
                <td className="table-td">
                  <div className="flex items-center gap-1.5 flex-wrap">
                    <span className="font-medium">{a.patient_name}</span>
                    {a.from_surgery && (
                      <span className="text-[10px] uppercase tracking-wide bg-indigo-100 text-indigo-700 px-1.5 py-0.5 rounded"
                            title="Auto-created from a scheduled surgery">
                        From surgery
                      </span>
                    )}
                  </div>
                  <div className="text-[10px] text-gray-500 font-mono">{a.chart_number}</div>
                </td>
                <td className="table-td text-[12px]">
                  <div className="flex items-center gap-1.5 flex-wrap">
                    <span>
                      {a.device_our_id || <span className="text-gray-400 italic">none yet</span>}
                    </span>
                    {a.device_ownership && (
                      <span className={`text-[11px] uppercase tracking-wide px-1 py-0.5 rounded ${OWNERSHIP_TONES[a.device_ownership] || 'bg-gray-100 text-gray-700'}`}
                            title={a.device_ownership === 'patient_owned'
                              ? 'Patient Owned — WWC does NOT bill insurance.'
                              : a.device_ownership === 'wwc_claimed'
                                ? 'WWC Claimed (originally patient-owned).'
                                : 'WWC Owned — billable to insurance.'}>
                        {OWNERSHIP_LABELS[a.device_ownership] || a.device_ownership}
                      </span>
                    )}
                  </div>
                  {a.device_type_name && <div className="text-[10px] text-gray-500">{a.device_type_name}</div>}
                </td>
                <td className="table-td text-[11px] capitalize">{a.source_flow.replace('_', ' ')}</td>
                <td className="table-td">
                  <span className="text-[11px] uppercase bg-gray-100 text-gray-700 px-1.5 py-0.5 rounded">
                    {a.status.replace(/_/g, ' ')}
                  </span>
                </td>
                <td className="table-td text-[11px] text-gray-500">
                  {a.created_at ? fmt.date(a.created_at) : '—'}
                </td>
              </tr>
            ))}
            {visibleAssignments.length === 0 && (
              <tr><td colSpan={5} className="table-td text-center text-gray-400 italic py-6">
                {(list?.assignments || []).length === 0
                  ? 'No assignments yet.'
                  : 'No rows match the current filters.'}
              </td></tr>
            )}
          </tbody>
        </table>
      </div>

      {startOpen && <StartLarcProcessDrawer
        onClose={() => setStartOpen(false)}
        onCreated={(id) => { setStartOpen(false); navigate('/larc/assignments/' + id) }}
      />}
    </div>
  )
}



// Devices ready to check out — the MA pulls the device from the cabinet and
// records the device ID. Mirrors the LarcCheckoutCard on My Checklist; the
// per-assignment checkout action used to live on the assignment detail page.
function LarcCheckoutCard() {
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const { data, isLoading, error } = useQuery({
    queryKey: ['larc-checkouts'],
    queryFn: () => api.get('/larc/checkouts/ready').then(r => r.data),
  })

  const rows = data || []
  const count = rows.length

  return (
    <div className="card border-plum-100 bg-plum-50/30">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <PackageCheck size={18} className="text-plum-700" />
          <div>
            <div className="text-sm font-semibold text-gray-800">Devices Ready to Check Out</div>
            <div className="text-xs text-gray-600">
              {isLoading
                ? 'Loading…'
                : error
                  ? <span className="text-red-600">Couldn't load — {error?.response?.data?.detail || error.message}</span>
                  : count === 0
                    ? 'No devices waiting to be checked out.'
                    : `${count} ${count === 1 ? 'device' : 'devices'} ready to check out.`}
            </div>
          </div>
        </div>
        <button
          className="btn-primary text-xs"
          onClick={() => setOpen(o => !o)}
          disabled={count === 0}
        >
          {open ? 'Close' : 'Check out a device'}
        </button>
      </div>

      {open && (
        <div className="mt-3 space-y-2">
          {rows.map(r => (
            <LarcCheckoutRow key={r.assignment_id} row={r} qc={qc} />
          ))}
        </div>
      )}
    </div>
  )
}


function LarcCheckoutRow({ row, qc }) {
  const [deviceId, setDeviceId] = useState('')
  const [givenTo, setGivenTo] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const [done, setDone] = useState(false)

  async function submit() {
    if (!deviceId.trim()) {
      setErr('Enter the device ID from the label')
      return
    }
    setBusy(true); setErr(null)
    try {
      await api.post(`/larc/assignments/${row.assignment_id}/checkout-direct`, {
        device_our_id: deviceId.trim(),
        given_to: givenTo.trim() || null,
      })
      setDone(true)
      qc.invalidateQueries({ queryKey: ['larc-checkouts'] })
      qc.invalidateQueries({ queryKey: ['larc-dashboard'] })
    } catch (e) {
      setErr(e?.response?.data?.detail || e.message)
    } finally { setBusy(false) }
  }

  if (done) {
    return (
      <div className="bg-green-50 border border-green-200 rounded p-2 text-xs text-green-800">
        ✓ Checked out {row.device_type_name} for {row.patient_name}.
      </div>
    )
  }

  return (
    <div className="bg-white border border-border-subtle rounded p-2.5">
      <div className="flex items-baseline justify-between gap-3">
        <div className="min-w-0">
          <div className="text-sm font-medium text-gray-900 truncate">{row.patient_name}</div>
          <div className="text-xs text-gray-600">
            {row.device_type_name || 'Device'}
            {row.appt_date && <> · appt {fmt.date(row.appt_date)}</>}
            {row.chart_number && <> · chart {row.chart_number}</>}
          </div>
        </div>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <input
          className="input text-xs font-mono w-40"
          placeholder="Device ID from label"
          value={deviceId}
          onChange={e => setDeviceId(e.target.value)}
          autoComplete="off"
        />
        <input
          className="input text-xs w-48"
          placeholder="Given to (optional)"
          value={givenTo}
          onChange={e => setGivenTo(e.target.value)}
        />
        <button
          className="btn-primary text-xs"
          onClick={submit}
          disabled={busy || !deviceId.trim()}
        >
          {busy ? 'Checking out…' : 'Check out'}
        </button>
      </div>
      {err && <div className="text-xs text-red-600 mt-1">{err}</div>}
    </div>
  )
}


function StartLarcProcessDrawer({ onClose, onCreated }) {
  const qc = useQueryClient()
  const [step, setStep] = useState(1)            // 1 = intake, 2 = suggestion
  const [suggestion, setSuggestion] = useState(null)
  const [chosenFlow, setChosenFlow] = useState(null)
  const [showErrors, setShowErrors] = useState(false)
  const [form, setForm] = useState({
    chart_number: '', patient_first_name: '', patient_last_name: '',
    patient_dob: '', patient_email: '', patient_cell: '',
    device_type_id: '', requested_by_email: '',
    reason_for_request: '', reason_icd10: '',
  })
  const update = (k, v) => setForm(f => ({ ...f, [k]: v }))

  const { data: types } = useQuery({
    queryKey: ['larc-device-types'],
    queryFn: () => api.get('/larc/device-types').then(r => r.data),
    staleTime: 60_000,
  })
  const { data: clinicians } = useQuery({
    queryKey: ['clinicians'],
    queryFn: () => api.get('/admin/users/clinicians').then(r => r.data),
    staleTime: 60_000,
  })
  const { data: config } = useQuery({
    queryKey: ['larc-config'],
    queryFn: () => api.get('/larc/config').then(r => r.data),
    staleTime: 60_000,
  })
  const reasons = config?.reason_for_request_options || []

  const allFilled = form.chart_number.trim() && form.patient_first_name.trim()
    && form.patient_last_name.trim() && form.patient_dob && form.patient_email.trim()
    && form.patient_cell.trim() && form.device_type_id && form.requested_by_email
    && form.reason_for_request

  const missing = {
    chart_number: !form.chart_number.trim(),
    patient_dob: !form.patient_dob,
    patient_first_name: !form.patient_first_name.trim(),
    patient_last_name: !form.patient_last_name.trim(),
    patient_email: !form.patient_email.trim(),
    patient_cell: !form.patient_cell.trim(),
    device_type_id: !form.device_type_id,
    requested_by_email: !form.requested_by_email,
    reason_for_request: !form.reason_for_request,
  }
  const errCls = (k) => (showErrors && missing[k]) ? ' border-red-400 bg-red-50' : ''
  const handleContinue = () => {
    if (!allFilled) { setShowErrors(true); return }
    suggest.mutate()
  }

  const suggest = useMutation({
    mutationFn: () => api.post('/larc/assignments/suggest-flow',
      { device_type_id: form.device_type_id }).then(r => r.data),
    onSuccess: (data) => { setSuggestion(data); setChosenFlow(data.suggested_flow); setStep(2) },
    onError: (e) => alert(e?.response?.data?.detail || 'Could not compute a suggestion'),
  })

  const create = useMutation({
    mutationFn: () => {
      const prov = (clinicians || []).find(c => c.email === form.requested_by_email)
      return api.post('/larc/assignments', {
        chart_number: form.chart_number.trim(),
        patient_name: `${form.patient_last_name.trim()}, ${form.patient_first_name.trim()}`,
        patient_first_name: form.patient_first_name.trim(),
        patient_last_name: form.patient_last_name.trim(),
        patient_dob: form.patient_dob,
        patient_email: form.patient_email.trim(),
        patient_cell: form.patient_cell.trim(),
        device_type_id: form.device_type_id,
        source_flow: chosenFlow,
        reason_for_request: form.reason_for_request,
        reason_icd10: form.reason_icd10,
        requested_by_provider: prov?.display_name || null,
        inserting_provider_email: prov?.email || null,
        inserting_provider_name: prov?.display_name || null,
        inserting_provider_npi: prov?.npi || null,
      }).then(r => r.data)
    },
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ['larc-dashboard'] })
      qc.invalidateQueries({ queryKey: ['larc-assignments'] })
      onCreated(data.id)
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Create failed'),
  })

  const FLOW_LABEL = {
    in_stock: 'Use an in-stock device',
    pharmacy_order: 'Pharmacy enrollment form',
    office_procedure: 'In-office procedure device',
  }

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/30" />
      <div className="relative w-full max-w-lg bg-white shadow-xl overflow-y-auto"
           onClick={e => e.stopPropagation()}>
        <div className="sticky top-0 bg-white border-b px-4 py-3 flex items-center justify-between">
          <h2 className="font-semibold text-plum-700">Start LARC Process</h2>
          <button onClick={onClose}><X size={18} /></button>
        </div>

        {step === 1 && (
          <div className="p-4 grid grid-cols-6 gap-2 text-sm">
            {showErrors && !allFilled && (
              <div className="col-span-6 rounded border border-red-300 bg-red-50 text-red-700 px-3 py-2 text-[12px]">
                Please complete the highlighted fields — every field is required to continue.
              </div>
            )}
            <label className="col-span-3">MRN <span className="text-red-500">*</span>
              <input className={"input w-full" + errCls('chart_number')} value={form.chart_number}
                     onChange={e => update('chart_number', e.target.value)} /></label>
            <label className="col-span-3">DOB <span className="text-red-500">*</span>
              <input type="date" className={"input w-full" + errCls('patient_dob')} value={form.patient_dob}
                     onChange={e => update('patient_dob', e.target.value)} /></label>
            <label className="col-span-3">First Name <span className="text-red-500">*</span>
              <input className={"input w-full" + errCls('patient_first_name')} value={form.patient_first_name}
                     onChange={e => update('patient_first_name', e.target.value)} /></label>
            <label className="col-span-3">Last Name <span className="text-red-500">*</span>
              <input className={"input w-full" + errCls('patient_last_name')} value={form.patient_last_name}
                     onChange={e => update('patient_last_name', e.target.value)} /></label>
            <label className="col-span-3">Email <span className="text-red-500">*</span>
              <input className={"input w-full" + errCls('patient_email')} value={form.patient_email}
                     onChange={e => update('patient_email', e.target.value)} /></label>
            <label className="col-span-3">Cell Phone <span className="text-red-500">*</span>
              <input className={"input w-full" + errCls('patient_cell')} value={form.patient_cell}
                     onChange={e => update('patient_cell', e.target.value)} /></label>
            <label className="col-span-6">Device Type <span className="text-red-500">*</span>
              <select className={"input w-full" + errCls('device_type_id')} value={form.device_type_id}
                      onChange={e => update('device_type_id', e.target.value)}>
                <option value="">— select device —</option>
                {(types || []).filter(t => t.is_active).map(t => (
                  <option key={t.id} value={t.id}>{t.name}</option>))}
              </select></label>
            <label className="col-span-6">Requested By <span className="text-red-500">*</span>
              <select className={"input w-full" + errCls('requested_by_email')} value={form.requested_by_email}
                      onChange={e => update('requested_by_email', e.target.value)}>
                <option value="">— select provider —</option>
                {(clinicians || []).map(c => (
                  <option key={c.email} value={c.email}>
                    {c.display_name}{c.credential ? `, ${c.credential}` : ''}</option>))}
              </select>
              <span className="text-[11px] text-muted">Manage providers in Admin → Users.</span>
            </label>
            <label className="col-span-6">Reason for Request <span className="text-red-500">*</span>
              <select className={"input w-full" + errCls('reason_for_request')} value={form.reason_for_request}
                      onChange={e => {
                        const r = reasons.find(x => x.reason === e.target.value)
                        update('reason_for_request', e.target.value)
                        update('reason_icd10', r?.icd10 || '')
                      }}>
                <option value="">— select reason —</option>
                {reasons.map(r => (
                  <option key={r.reason} value={r.reason}>{r.reason} ({r.icd10})</option>))}
              </select></label>
          </div>
        )}

        {step === 2 && suggestion && (
          <div className="p-4 text-sm space-y-3">
            <div className="rounded border border-plum-200 bg-plum-50 p-3">
              <div className="font-medium text-plum-700">Recommended</div>
              <div>{FLOW_LABEL[suggestion.suggested_flow]}
                {suggestion.suggested_flow === 'in_stock'
                  && ` — ${suggestion.in_stock_count} available`}</div>
            </div>
            <div>
              <div className="text-[11px] text-muted mb-1">Choose how to fulfill:</div>
              {suggestion.allowed_flows.map(f => (
                <label key={f} className="flex items-center gap-2 py-1">
                  <input type="radio" name="flow" checked={chosenFlow === f}
                         onChange={() => setChosenFlow(f)} />
                  {FLOW_LABEL[f]}
                </label>))}
            </div>
          </div>
        )}

        <div className="sticky bottom-0 bg-white border-t px-4 py-3 flex justify-between">
          {step === 2
            ? <button className="btn-ghost" onClick={() => setStep(1)}>Back</button>
            : <span />}
          {step === 1
            ? <button className="btn-primary" disabled={suggest.isPending}
                      onClick={handleContinue}>Continue</button>
            : <button className="btn-primary" disabled={!chosenFlow || create.isPending}
                      onClick={() => create.mutate()}>Confirm &amp; Create</button>}
        </div>
      </div>
    </div>
  )
}
