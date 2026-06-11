import { useEffect, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { ArrowLeft, Plus, Search, Package, X, Printer, Layers, SearchX } from 'lucide-react'
import api, { fmt } from '../utils/api'
import EmptyState from '../components/EmptyState'


const STATUS_TONES = {
  unassigned:  'bg-gray-100 text-gray-700',
  received:    'bg-blue-100 text-blue-700',
  assigned:    'bg-amber-100 text-amber-700',
  checked_out: 'bg-violet-100 text-violet-700',
  inserted:    'bg-green-100 text-green-700',
  billed:      'bg-green-200 text-green-800',
  defective:   'bg-red-100 text-red-700',
  returned:    'bg-red-50 text-red-700',
  lost:        'bg-red-200 text-red-800',
  expired:     'bg-gray-200 text-gray-700',
}

export const OWNERSHIP_TONES = {
  patient_owned: 'bg-sky-100 text-sky-800',
  wwc_owned:     'bg-plum-100 text-plum-700',
  wwc_claimed:   'bg-emerald-100 text-emerald-800',
}

export const OWNERSHIP_LABELS = {
  patient_owned: 'Patient',
  wwc_owned:     'WWC',
  wwc_claimed:   'WWC Claimed',
}


export default function LarcDevices() {
  const navigate = useNavigate()
  const [filters, setFilters] = useState({
    device_type_id: '', category: '', status: '', location: '', ownership: '',
    search: '',
    active_only: true,
  })
  const [adding, setAdding] = useState(false)
  const [bulkAdding, setBulkAdding] = useState(false)
  // ?add=1 auto-opens the bulk-add form (linked from /larc's
  // "Receive Devices into Inventory" button). Consume the param once
  // so reopening the form later doesn't require a fresh URL.
  const [searchParams, setSearchParams] = useSearchParams()
  useEffect(() => {
    if (searchParams.get('add') === '1' && !bulkAdding) {
      setBulkAdding(true)
      const next = new URLSearchParams(searchParams)
      next.delete('add')
      setSearchParams(next, { replace: true })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  const [selected, setSelected] = useState(new Set())

  const { data: types } = useQuery({
    queryKey: ['larc-device-types'],
    queryFn: () => api.get('/larc/device-types').then(r => r.data),
    staleTime: 60_000,
  })
  const { data, isLoading } = useQuery({
    queryKey: ['larc-devices', filters],
    queryFn: () => api.get('/larc/devices', {
      params: Object.fromEntries(
        Object.entries(filters).filter(([k, v]) => v !== '' && v !== null)
      ),
    }).then(r => r.data),
  })

  const devices = data?.devices || []

  return (
    <div>
      <Link to="/larc" className="text-[12px] text-muted hover:underline flex items-center gap-1 mb-2">
        <ArrowLeft size={12} /> LARC dashboard
      </Link>
      <div className="flex items-baseline justify-between mb-3 flex-wrap gap-2">
        <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
          <Package size={22} className="text-plum-700" />
          Device Tracking · Inventory
        </h1>
        <div className="flex items-center gap-2">
          {selected.size > 0 && (
            <a href={`/api/larc/devices/labels.pdf?ids=${[...selected].join(',')}`}
               target="_blank" rel="noopener noreferrer"
               className="btn-secondary text-sm flex items-center gap-1">
              <Printer size={13} /> Print {selected.size} label{selected.size === 1 ? '' : 's'}
            </a>
          )}
          <button className="btn-secondary text-sm flex items-center gap-1"
                  onClick={() => setBulkAdding(true)}>
            <Layers size={13} /> Bulk add
          </button>
          <button className="btn-primary text-sm flex items-center gap-1"
                  onClick={() => setAdding(true)}>
            <Plus size={13} /> Add Device
          </button>
        </div>
      </div>

      {/* Filters */}
      <div className="card mb-3">
        <div className="grid grid-cols-2 md:grid-cols-5 gap-2 text-sm">
          <div>
            <label className="text-[10px] uppercase text-gray-500 block mb-1">Category</label>
            <select className="input text-sm w-full" aria-label="Category"
                    value={filters.category}
                    onChange={e => setFilters({ ...filters, category: e.target.value, device_type_id: '' })}>
              <option value="">All categories</option>
              <option value="larc">LARC</option>
              <option value="office_procedure">Office Procedure Devices</option>
            </select>
          </div>
          <div>
            <label className="text-[10px] uppercase text-gray-500 block mb-1">Device type</label>
            <select className="input text-sm w-full" aria-label="Device type"
                    value={filters.device_type_id}
                    onChange={e => setFilters({ ...filters, device_type_id: e.target.value })}>
              <option value="">All types</option>
              {(types || []).filter(t => !filters.category || t.category === filters.category)
                .map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
          </div>
          <div>
            <label className="text-[10px] uppercase text-gray-500 block mb-1">Status</label>
            <select className="input text-sm w-full" aria-label="Status"
                    value={filters.status}
                    onChange={e => setFilters({ ...filters, status: e.target.value })}>
              <option value="">
                {filters.active_only ? 'Active (default)' : 'All'}
              </option>
              <option value="unassigned">Unassigned</option>
              <option value="assigned">Assigned</option>
              <option value="checked_out">Checked out</option>
              <option value="inserted">Inserted</option>
              <option value="billed">Billed</option>
              <option value="defective">Defective</option>
              <option value="returned">Returned</option>
              <option value="lost">Lost</option>
              <option value="expired">Expired</option>
            </select>
            <label className="flex items-center gap-1 text-[10px] text-gray-500 mt-1 cursor-pointer">
              <input type="checkbox"
                     checked={!filters.active_only}
                     onChange={e => setFilters({ ...filters, active_only: !e.target.checked })} />
              Include history (terminal statuses)
            </label>
          </div>
          <div>
            <label className="text-[10px] uppercase text-gray-500 block mb-1">Location</label>
            <select className="input text-sm w-full" aria-label="Location"
                    value={filters.location}
                    onChange={e => setFilters({ ...filters, location: e.target.value })}>
              <option value="">All</option>
              <option value="white_plains">White Plains</option>
              <option value="arlington">Arlington</option>
              <option value="brandywine">Brandywine</option>
            </select>
          </div>
          <div>
            <label className="text-[10px] uppercase text-gray-500 block mb-1">Ownership</label>
            <select className="input text-sm w-full" aria-label="Ownership"
                    value={filters.ownership}
                    onChange={e => setFilters({ ...filters, ownership: e.target.value })}>
              <option value="">All</option>
              <option value="wwc_owned">WWC Owned</option>
              <option value="wwc_claimed">WWC Claimed</option>
              <option value="patient_owned">Patient Owned</option>
            </select>
          </div>
          <div>
            <label className="text-[10px] uppercase text-gray-500 block mb-1">Search</label>
            <div className="relative">
              <Search size={12} className="absolute left-2 top-2.5 text-muted" />
              <input className="input text-sm pl-7 w-full"
                     placeholder="our_id / lot / serial"
                     value={filters.search}
                     onChange={e => setFilters({ ...filters, search: e.target.value })} />
            </div>
          </div>
        </div>
      </div>

      <div className="card !p-0 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-plum-50">
            <tr>
              <th className="table-th w-8">
                <input type="checkbox"
                       aria-label="Select all devices on this page"
                       checked={devices.length > 0 && selected.size === devices.length}
                       onChange={e => {
                         if (e.target.checked) setSelected(new Set(devices.map(d => d.id)))
                         else setSelected(new Set())
                       }} />
              </th>
              <th className="table-th">Our ID</th>
              <th className="table-th">Type</th>
              <th className="table-th">Lot #</th>
              <th className="table-th">Ownership</th>
              <th className="table-th">Location</th>
              <th className="table-th">Expires</th>
              <th className="table-th">Status</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {isLoading && (
              <tr><td colSpan={8} className="table-td text-center py-6 text-gray-400">Loading…</td></tr>
            )}
            {!isLoading && devices.length === 0 && (
              <tr>
                <td colSpan={8} className="table-td">
                  <EmptyState
                    icon={SearchX}
                    title="No devices match"
                    body="Try clearing the search or status filter."
                    compact
                  />
                </td>
              </tr>
            )}
            {devices.map(d => {
              const checked = selected.has(d.id)
              return (
                <tr key={d.id} className="hover:bg-plum-50/40">
                  <td className="table-td">
                    <input type="checkbox" checked={checked}
                           aria-label={`Select device ${d.our_id || d.id}`}
                           onClick={e => e.stopPropagation()}
                           onChange={() => {
                             const next = new Set(selected)
                             if (next.has(d.id)) next.delete(d.id); else next.add(d.id)
                             setSelected(next)
                           }} />
                  </td>
                  <td className="table-td font-mono cursor-pointer"
                      onClick={() => navigate(`/larc/devices/${d.id}`)}>{d.our_id}</td>
                  <td className="table-td cursor-pointer"
                      onClick={() => navigate(`/larc/devices/${d.id}`)}>
                    {d.device_type_name}
                    {d.category === 'office_procedure' && (
                      <span className="ml-1 text-[11px] bg-teal-100 text-teal-700 px-1 rounded">OP</span>
                    )}
                  </td>
                  <td className="table-td font-mono text-[11px]">{d.manufacturer_lot || '—'}</td>
                  <td className="table-td">
                    <span className={`text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded ${OWNERSHIP_TONES[d.ownership] || 'bg-gray-100 text-gray-700'}`}>
                      {OWNERSHIP_LABELS[d.ownership] || d.ownership_label || d.ownership}
                    </span>
                  </td>
                  <td className="table-td text-[11px]">{d.location_label}</td>
                  <td className="table-td text-[11px]">
                    {d.expiration_date ? fmt.date(d.expiration_date) : '—'}
                  </td>
                  <td className="table-td">
                    <span className={`text-[10px] uppercase px-1.5 py-0.5 rounded ${STATUS_TONES[d.status] || 'bg-gray-100 text-gray-700'}`}>
                      {d.status.replace(/_/g, ' ')}
                    </span>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <div className="text-[10px] text-gray-500 mt-2 text-right">
        Showing {devices.length} of {data?.total || 0}
      </div>

      {adding && <AddDeviceForm types={types || []} onClose={() => setAdding(false)} />}
      {bulkAdding && <BulkAddForm types={types || []} onClose={() => setBulkAdding(false)} />}
    </div>
  )
}


function BulkAddForm({ types, onClose }) {
  const qc = useQueryClient()
  // Shared across all rows since typical use is "shipment of N of same type at one location"
  const [shared, setShared] = useState({
    device_type_id: types[0]?.id || '',
    location: 'white_plains',
    purchase_date: new Date().toISOString().slice(0, 10),
  })
  // Per-row fields: our_id (required), manufacturer_lot, expiration_date, purchase_price
  const blankRow = () => ({ our_id: '', manufacturer_lot: '', expiration_date: '', purchase_price: '' })
  const [rows, setRows] = useState([blankRow(), blankRow(), blankRow(), blankRow(), blankRow()])
  const [result, setResult] = useState(null)

  const updateRow = (i, k, v) => {
    setRows(prev => prev.map((r, ix) => ix === i ? { ...r, [k]: v } : r))
  }
  const addRow = () => setRows(prev => [...prev, blankRow()])
  const removeRow = (i) => setRows(prev => prev.filter((_, ix) => ix !== i))

  const create = useMutation({
    mutationFn: () => {
      const filled = rows.filter(r => r.our_id.trim())
      const devices = filled.map(r => ({
        our_id: r.our_id.trim(),
        device_type_id: shared.device_type_id,
        location: shared.location,
        purchase_date: shared.purchase_date || null,
        manufacturer_lot: r.manufacturer_lot || null,
        expiration_date: r.expiration_date || null,
        purchase_price: r.purchase_price === '' ? null : Number(r.purchase_price),
      }))
      return api.post('/larc/devices/bulk', { devices }).then(r => r.data)
    },
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ['larc-devices'] })
      qc.invalidateQueries({ queryKey: ['larc-dashboard'] })
      setResult(data)
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Bulk add failed'),
  })

  if (result) {
    return (
      <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
        <div className="absolute inset-0 bg-black/30" />
        <div className="relative w-full max-w-2xl bg-white shadow-xl overflow-y-auto"
             onClick={e => e.stopPropagation()}>
          <div className="px-5 py-3 border-b border-border-subtle flex items-center justify-between">
            <h2 className="font-serif font-semibold text-ink text-[16px]">✓ Added {result.created} devices</h2>
            <button onClick={onClose} className="text-muted hover:text-ink"><X size={18} /></button>
          </div>
          <div className="p-5 space-y-3 text-sm">
            <p>Print labels for the new devices in one shot:</p>
            <a href={`/api/larc/devices/labels.pdf?ids=${result.device_ids.join(',')}`}
               target="_blank" rel="noopener noreferrer"
               className="btn-primary text-sm inline-flex items-center gap-1">
              <Printer size={13} /> Open {result.created}-page label PDF
            </a>
          </div>
          <div className="px-5 py-3 border-t border-border-subtle flex justify-end">
            <button className="btn-secondary text-sm" onClick={onClose}>Done</button>
          </div>
        </div>
      </div>
    )
  }

  const filledCount = rows.filter(r => r.our_id.trim()).length

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/30" />
      <div className="relative w-full max-w-3xl bg-white shadow-xl overflow-y-auto"
           onClick={e => e.stopPropagation()}>
        <div className="sticky top-0 bg-white border-b border-border-subtle px-5 py-3 flex items-center justify-between">
          <h2 className="font-serif font-semibold text-ink text-[16px]">
            Bulk add devices
          </h2>
          <button onClick={onClose} className="text-muted hover:text-ink"><X size={18} /></button>
        </div>

        <div className="p-5 space-y-3 text-sm">
          {/* Shared fields */}
          <div className="bg-plum-50/30 border border-plum-100 rounded p-3">
            <div className="text-[10px] uppercase tracking-wide text-plum-700 mb-2">
              Shared (applies to all rows)
            </div>
            <div className="grid grid-cols-3 gap-2">
              <div>
                <label className="text-[10px] uppercase text-gray-500 block mb-1">Device type</label>
                <select className="input text-sm w-full"
                        value={shared.device_type_id}
                        onChange={e => setShared({ ...shared, device_type_id: e.target.value })}>
                  {types.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
                </select>
              </div>
              <div>
                <label className="text-[10px] uppercase text-gray-500 block mb-1">Location</label>
                <select className="input text-sm w-full"
                        value={shared.location}
                        onChange={e => setShared({ ...shared, location: e.target.value })}>
                  <option value="white_plains">White Plains</option>
                  <option value="arlington">Arlington</option>
                  <option value="brandywine">Brandywine</option>
                </select>
              </div>
              <div>
                <label className="text-[10px] uppercase text-gray-500 block mb-1">Purchase date</label>
                <input type="date" className="input text-sm w-full"
                       value={shared.purchase_date}
                       onChange={e => setShared({ ...shared, purchase_date: e.target.value })} />
              </div>
            </div>
          </div>

          {/* Per-row fields */}
          <div>
            <div className="text-[10px] uppercase tracking-wide text-gray-500 mb-1">
              Devices ({filledCount} of {rows.length} filled)
            </div>
            <table className="w-full text-sm">
              <thead>
                <tr className="text-[10px] uppercase text-gray-500 text-left">
                  <th className="px-1">#</th>
                  <th className="px-1">Our ID *</th>
                  <th className="px-1">Lot #</th>
                  <th className="px-1">Expires</th>
                  <th className="px-1">Price ($)</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={i}>
                    <td className="px-1 text-[10px] text-gray-400">{i + 1}</td>
                    <td className="px-1">
                      <input className="input text-[12px] w-full font-mono"
                             placeholder={i === 0 ? "WWC0700" : ""}
                             value={r.our_id}
                             onChange={e => updateRow(i, 'our_id', e.target.value)} />
                    </td>
                    <td className="px-1">
                      <input className="input text-[12px] w-full font-mono"
                             value={r.manufacturer_lot}
                             onChange={e => updateRow(i, 'manufacturer_lot', e.target.value)} />
                    </td>
                    <td className="px-1">
                      <input type="date" className="input text-[12px] w-full"
                             value={r.expiration_date}
                             onChange={e => updateRow(i, 'expiration_date', e.target.value)} />
                    </td>
                    <td className="px-1">
                      <input type="number" step="0.01" className="input text-[12px] w-full font-mono"
                             value={r.purchase_price}
                             onChange={e => updateRow(i, 'purchase_price', e.target.value)} />
                    </td>
                    <td className="px-1">
                      {rows.length > 1 && (
                        <button onClick={() => removeRow(i)}
                                className="text-red-600 hover:bg-red-50 rounded p-1">
                          <X size={11} />
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <button className="text-[11px] text-plum-700 hover:underline mt-2"
                    onClick={addRow}>
              + Add row
            </button>
          </div>
        </div>

        <div className="sticky bottom-0 bg-white border-t border-border-subtle px-5 py-3 flex justify-end gap-2">
          <button className="text-sm text-muted hover:underline" onClick={onClose}>Cancel</button>
          <button className="btn-primary text-sm"
                  onClick={() => create.mutate()}
                  disabled={filledCount === 0 || create.isPending}>
            {create.isPending ? 'Adding…' : `Add ${filledCount} device${filledCount === 1 ? '' : 's'}`}
          </button>
        </div>
      </div>
    </div>
  )
}


function AddDeviceForm({ types, onClose }) {
  const qc = useQueryClient()
  const [form, setForm] = useState({
    our_id: '', device_type_id: types[0]?.id || '', manufacturer_lot: '',
    manufacturer_serial: '', expiration_date: '', purchase_date: '',
    purchase_price: '', location: 'white_plains', notes: '',
  })
  const update = (k, v) => setForm(f => ({ ...f, [k]: v }))

  const create = useMutation({
    mutationFn: () => api.post('/larc/devices', {
      ...form,
      purchase_price: form.purchase_price === '' ? null : Number(form.purchase_price),
    }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['larc-devices'] })
      qc.invalidateQueries({ queryKey: ['larc-dashboard'] })
      onClose()
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Add failed'),
  })

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/30" />
      <div className="relative w-full max-w-lg bg-white shadow-xl overflow-y-auto"
           onClick={e => e.stopPropagation()}>
        <div className="sticky top-0 bg-white border-b border-border-subtle px-5 py-3 flex items-center justify-between">
          <h2 className="font-serif font-semibold text-ink text-[16px]">Add Device to Inventory</h2>
          <button onClick={onClose} className="text-muted hover:text-ink"><X size={18} /></button>
        </div>
        <div className="p-5 space-y-3 text-sm">
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="text-[10px] uppercase text-gray-500 block mb-1">Our ID *</label>
              <input className="input text-sm w-full font-mono" required
                     placeholder="WWC0700"
                     value={form.our_id} onChange={e => update('our_id', e.target.value)} />
            </div>
            <div>
              <label className="text-[10px] uppercase text-gray-500 block mb-1">Device type *</label>
              <select className="input text-sm w-full"
                      value={form.device_type_id}
                      onChange={e => update('device_type_id', e.target.value)}>
                {types.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
              </select>
            </div>
            <div>
              <label className="text-[10px] uppercase text-gray-500 block mb-1">Manufacturer lot</label>
              <input className="input text-sm w-full font-mono"
                     value={form.manufacturer_lot}
                     onChange={e => update('manufacturer_lot', e.target.value)} />
            </div>
            <div>
              <label className="text-[10px] uppercase text-gray-500 block mb-1">Manufacturer serial</label>
              <input className="input text-sm w-full font-mono"
                     value={form.manufacturer_serial}
                     onChange={e => update('manufacturer_serial', e.target.value)} />
            </div>
            <div>
              <label className="text-[10px] uppercase text-gray-500 block mb-1">Expiration date</label>
              <input type="date" className="input text-sm w-full"
                     value={form.expiration_date}
                     onChange={e => update('expiration_date', e.target.value)} />
            </div>
            <div>
              <label className="text-[10px] uppercase text-gray-500 block mb-1">Purchase date</label>
              <input type="date" className="input text-sm w-full"
                     value={form.purchase_date}
                     onChange={e => update('purchase_date', e.target.value)} />
            </div>
            <div>
              <label className="text-[10px] uppercase text-gray-500 block mb-1">Purchase price ($)</label>
              <input type="number" step="0.01" className="input text-sm w-full font-mono"
                     value={form.purchase_price}
                     onChange={e => update('purchase_price', e.target.value)} />
            </div>
            <div>
              <label className="text-[10px] uppercase text-gray-500 block mb-1">Location</label>
              <select className="input text-sm w-full"
                      value={form.location}
                      onChange={e => update('location', e.target.value)}>
                <option value="white_plains">White Plains</option>
                <option value="arlington">Arlington</option>
                <option value="brandywine">Brandywine</option>
              </select>
            </div>
          </div>
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
                  disabled={!form.our_id.trim() || !form.device_type_id || create.isPending}>
            {create.isPending ? 'Adding…' : 'Add device'}
          </button>
        </div>
      </div>
    </div>
  )
}
