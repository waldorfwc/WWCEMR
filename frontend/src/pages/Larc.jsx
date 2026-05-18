import { useState, useMemo } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'
import {
  Activity, AlertTriangle, ArrowDown, ArrowUp, ArrowUpDown, BookOpen, Box,
  Calendar, Check, ChevronRight, Clock, Plus, Search, Users, Building2,
  Truck, Package, FileText, X,
} from 'lucide-react'
import api, { fmt } from '../utils/api'


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
  const [newRequest, setNewRequest] = useState(false)

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
      <div className="flex items-baseline justify-between mb-4 flex-wrap gap-2">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
            <Box size={22} className="text-plum-700" />
            Device Tracking
          </h1>
          <p className="text-sm text-gray-500 mt-0.5">
            <span className="text-plum-700 font-medium">LARC</span> contraceptive devices ·
            {' '}<span className="text-teal-700 font-medium">Office Procedure Devices</span>
            {' '}(NovaSure, Bensta)
          </p>
        </div>
        <div className="flex gap-2">
          <Link to="/larc/devices" className="btn-secondary text-sm flex items-center gap-1">
            <Package size={13} /> Devices
          </Link>
          <Link to="/larc/checkouts" className="btn-secondary text-sm flex items-center gap-1">
            <AlertTriangle size={13} /> Pending checkouts
          </Link>
          <Link to="/larc/owed" className="btn-secondary text-sm flex items-center gap-1">
            <Users size={13} /> Owed list
          </Link>
          <Link to="/larc/pharmacies" className="btn-secondary text-sm flex items-center gap-1">
            <Building2 size={13} /> Pharmacies
          </Link>
          <Link to="/larc/device-types" className="btn-secondary text-sm flex items-center gap-1">
            <Box size={13} /> Device types
          </Link>
          <Link to="/larc/manual" className="btn-secondary text-sm flex items-center gap-1"
                title="LARC operating procedures — editable reference for staff">
            <BookOpen size={13} /> Manual
          </Link>
          <Link to="/larc/eod" className="btn-secondary text-sm flex items-center gap-1">
            <Activity size={13} /> EOD report
          </Link>
          <Link to="/larc/inventory-count" className="btn-secondary text-sm flex items-center gap-1">
            <Activity size={13} /> Physical count
          </Link>
          <Link to="/larc/audit" className="btn-secondary text-sm flex items-center gap-1">
            <FileText size={13} /> Audit log
          </Link>
          <button className="btn-primary text-sm flex items-center gap-1"
                  onClick={() => setNewRequest(true)}>
            <Plus size={13} /> Order/Assign LARC for Patient
          </button>
        </div>
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
                    <div className={`text-[10px] uppercase tracking-wide ${label}`}>{type}</div>
                    <div className="text-2xl font-bold mt-0.5">{count}</div>
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
          <div className="col-span-full text-xs text-gray-500 italic card !p-3">
            No devices in inventory yet. Click <strong>Devices</strong> to add some.
          </div>
        )}
      </div>

      {/* Reorder + expiring alerts */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mb-4">
        {/* Reorder */}
        <div className="card">
          <div className="flex items-center gap-1.5 mb-2">
            <Truck size={14} className="text-amber-700" />
            <h2 className="text-sm font-semibold text-gray-800">Reorder alerts</h2>
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
                      <span className="ml-1 text-[9px] bg-teal-100 text-teal-700 px-1 rounded">OP</span>
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
            <h2 className="text-sm font-semibold text-gray-800">Expiring within 365 days</h2>
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
            <h2 className="text-sm font-semibold text-gray-800">Overdue pharmacy orders</h2>
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
            <h2 className="text-sm font-semibold text-gray-800">Unacknowledged checkouts</h2>
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
            <tr className="bg-white border-t border-gray-100">
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
                  <div className="font-medium">{a.patient_name}</div>
                  <div className="text-[10px] text-gray-500 font-mono">{a.chart_number}</div>
                </td>
                <td className="table-td text-[12px]">
                  {a.device_our_id || <span className="text-gray-400 italic">none yet</span>}
                  {a.device_type_name && <div className="text-[10px] text-gray-500">{a.device_type_name}</div>}
                </td>
                <td className="table-td text-[11px] capitalize">{a.source_flow.replace('_', ' ')}</td>
                <td className="table-td">
                  <span className="text-[10px] uppercase bg-gray-100 text-gray-700 px-1.5 py-0.5 rounded">
                    {a.status.replace(/_/g, ' ')}
                  </span>
                </td>
                <td className="table-td text-[11px] text-gray-500">
                  {a.created_at ? fmt.date(a.created_at.slice(0, 10)) : '—'}
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

      {newRequest && <NewRequestDrawer onClose={() => setNewRequest(false)}
                                          onCreated={(id) => navigate(`/larc/assignments/${id}`)} />}
    </div>
  )
}


function NewRequestDrawer({ onClose, onCreated }) {
  const qc = useQueryClient()
  const [form, setForm] = useState({
    chart_number: '', patient_name: '', patient_dob: '',
    patient_email: '', patient_phone: '', primary_insurance: '',
    source_flow: 'in_stock', device_id: '', device_type_id: '',
    notes: '',
  })
  const update = (k, v) => setForm(f => ({ ...f, [k]: v }))

  const { data: types } = useQuery({
    queryKey: ['larc-device-types'],
    queryFn: () => api.get('/larc/device-types').then(r => r.data),
    staleTime: 60_000,
  })
  const { data: picklists } = useQuery({
    queryKey: ['larc-picklists'],
    queryFn: () => api.get('/larc/picklists').then(r => r.data),
    staleTime: 60_000,
  })
  // For in_stock flow, list unassigned devices the MA can pick
  const { data: stockDevices } = useQuery({
    queryKey: ['larc-devices-unassigned'],
    queryFn: () => api.get('/larc/devices', { params: { status: 'unassigned' } }).then(r => r.data),
    enabled: form.source_flow === 'in_stock',
  })

  const create = useMutation({
    mutationFn: () => api.post('/larc/assignments', {
      chart_number: form.chart_number.trim(),
      patient_name: form.patient_name.trim(),
      patient_dob: form.patient_dob || null,
      patient_email: form.patient_email || null,
      patient_phone: form.patient_phone || null,
      primary_insurance: form.primary_insurance || null,
      source_flow: form.source_flow,
      device_id: form.source_flow === 'in_stock' ? form.device_id : null,
      device_type_id: form.source_flow === 'pharmacy_order' ? form.device_type_id : null,
      notes: form.notes || null,
    }).then(r => r.data),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ['larc-dashboard'] })
      qc.invalidateQueries({ queryKey: ['larc-assignments'] })
      qc.invalidateQueries({ queryKey: ['larc-devices-unassigned'] })
      qc.invalidateQueries({ queryKey: ['larc-ready-to-checkout'] })
      onCreated(data.id)
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Create failed'),
  })

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/30" />
      <div className="relative w-full max-w-lg bg-white shadow-xl overflow-y-auto"
           onClick={e => e.stopPropagation()}>
        <div className="sticky top-0 bg-white border-b border-border-subtle px-5 py-3 flex items-center justify-between">
          <h2 className="font-serif font-semibold text-ink text-[16px]">Order / Assign LARC for Patient</h2>
          <button onClick={onClose} className="text-muted hover:text-ink"><X size={18} /></button>
        </div>
        <div className="p-5 space-y-3 text-sm">
          <div>
            <label className="text-[10px] uppercase text-gray-500 block mb-1">Source</label>
            <div className="flex gap-2">
              <label className={`text-[12px] px-2 py-1 rounded border cursor-pointer ${
                form.source_flow === 'in_stock'
                  ? 'border-plum-500 bg-plum-50 text-plum-800'
                  : 'border-gray-200 text-gray-700'}`}>
                <input type="radio" className="mr-1" checked={form.source_flow === 'in_stock'}
                       onChange={() => update('source_flow', 'in_stock')} />
                In-stock (Liletta)
              </label>
              <label className={`text-[12px] px-2 py-1 rounded border cursor-pointer ${
                form.source_flow === 'pharmacy_order'
                  ? 'border-plum-500 bg-plum-50 text-plum-800'
                  : 'border-gray-200 text-gray-700'}`}>
                <input type="radio" className="mr-1" checked={form.source_flow === 'pharmacy_order'}
                       onChange={() => update('source_flow', 'pharmacy_order')} />
                Pharmacy order (Mirena/Skyla/Kyleena/Paragard/Nexplanon)
              </label>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="text-[10px] uppercase text-gray-500 block mb-1">Chart # *</label>
              <input className="input text-sm w-full font-mono" required
                     value={form.chart_number}
                     onChange={e => update('chart_number', e.target.value)} />
            </div>
            <div>
              <label className="text-[10px] uppercase text-gray-500 block mb-1">Patient name *</label>
              <input className="input text-sm w-full" required
                     placeholder="Last, First"
                     value={form.patient_name}
                     onChange={e => update('patient_name', e.target.value)} />
            </div>
            <div>
              <label className="text-[10px] uppercase text-gray-500 block mb-1">DOB</label>
              <input type="date" className="input text-sm w-full"
                     value={form.patient_dob}
                     onChange={e => update('patient_dob', e.target.value)} />
            </div>
            <div>
              <label className="text-[10px] uppercase text-gray-500 block mb-1">Phone</label>
              <input className="input text-sm w-full font-mono"
                     value={form.patient_phone}
                     onChange={e => update('patient_phone', e.target.value)} />
            </div>
            <div className="col-span-2">
              <label className="text-[10px] uppercase text-gray-500 block mb-1">Email</label>
              <input className="input text-sm w-full"
                     value={form.patient_email}
                     onChange={e => update('patient_email', e.target.value)} />
            </div>
            <div className="col-span-2">
              <label className="text-[10px] uppercase text-gray-500 block mb-1">Primary insurance</label>
              <select className="input text-sm w-full"
                      value={form.primary_insurance}
                      onChange={e => update('primary_insurance', e.target.value)}>
                <option value="">— select insurance —</option>
                {(picklists?.insurance_companies || []).map(name => (
                  <option key={name} value={name}>{name}</option>
                ))}
              </select>
            </div>
          </div>

          {form.source_flow === 'in_stock' && (
            <div>
              <label className="text-[10px] uppercase text-gray-500 block mb-1">Pick device from stock *</label>
              <select className="input text-sm w-full"
                      value={form.device_id}
                      onChange={e => update('device_id', e.target.value)}>
                <option value="">— pick a device —</option>
                {(stockDevices?.devices || [])
                  .filter(d => d.status === 'unassigned')
                  .map(d => (
                  <option key={d.id} value={d.id}>
                    {d.our_id} · {d.device_type_name} · expires {d.expiration_date || 'unknown'} · {d.location_label}
                  </option>
                ))}
              </select>
              {(stockDevices?.devices || []).filter(d => d.status === 'unassigned').length === 0 && (
                <div className="text-[10px] text-amber-700 mt-1">
                  No unassigned devices available — order from pharmacy or add new devices in inventory.
                </div>
              )}
            </div>
          )}

          {form.source_flow === 'pharmacy_order' && (
            <div>
              <label className="text-[10px] uppercase text-gray-500 block mb-1">Device type to order *</label>
              <select className="input text-sm w-full"
                      value={form.device_type_id}
                      onChange={e => update('device_type_id', e.target.value)}>
                <option value="">— pick device type —</option>
                {(types || []).filter(t => t.default_flow === 'pharmacy_order' || form.source_flow === 'pharmacy_order')
                  .map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
              </select>
              <div className="text-[10px] text-gray-500 mt-1">
                Device row will be created when it arrives from the pharmacy.
              </div>
            </div>
          )}

          <div>
            <label className="text-[10px] uppercase text-gray-500 block mb-1">Notes</label>
            <textarea className="input text-sm w-full" rows={2}
                      value={form.notes}
                      onChange={e => update('notes', e.target.value)} />
          </div>
        </div>
        <div className="sticky bottom-0 bg-white border-t border-border-subtle px-5 py-3 flex justify-end gap-2">
          <button className="text-sm text-muted hover:underline" onClick={onClose}>Cancel</button>
          <button className="btn-primary text-sm"
                  onClick={() => create.mutate()}
                  disabled={
                    !form.chart_number.trim() || !form.patient_name.trim()
                    || (form.source_flow === 'in_stock' && !form.device_id)
                    || (form.source_flow === 'pharmacy_order' && !form.device_type_id)
                    || create.isPending
                  }>
            {create.isPending ? 'Creating…' : 'Create request'}
          </button>
        </div>
      </div>
    </div>
  )
}
