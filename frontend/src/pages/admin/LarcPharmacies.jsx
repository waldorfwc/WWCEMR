import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { ArrowLeft, Plus, Star, X } from 'lucide-react'
import api from '../../utils/api'


// Pharmacy-order LARC devices we expect to ship. Drives the
// device-name chips on each row + the default-for-device pickers.
// Order matters — Nexplanon and Paragard each get their own
// pharmacies; Bayer (Mirena/Skyla/Kyleena) shares.
const PHARMACY_ORDER_DEVICES = [
  'Nexplanon', 'Paragard', 'Mirena', 'Skyla', 'Kyleena',
]


export default function LarcPharmacies() {
  const qc = useQueryClient()
  const { data: pharms, isLoading } = useQuery({
    queryKey: ['admin-larc-pharmacies'],
    queryFn: () => api.get('/larc/pharmacies').then(r => r.data),
  })

  const [creating, setCreating] = useState(false)
  const [editing, setEditing] = useState(null)

  return (
    <div>
      <Link to="/admin"
            className="text-[12px] text-muted hover:underline flex items-center gap-1 mb-1">
        <ArrowLeft size={12} /> Back to Admin
      </Link>
      <div className="flex items-baseline justify-between mb-3">
        <div>
          <h1 className="font-serif font-semibold text-ink text-[22px] m-0">
            LARC Pharmacies
          </h1>
          <p className="text-muted text-[12px] mt-0.5">
            Directory of specialty pharmacies that ship pharmacy-order LARC
            devices. The auto-fax pipeline targets the fax number set here;
            new assignments auto-pick the pharmacy marked default for that
            device family.
          </p>
        </div>
        <button className="btn-primary text-sm flex items-center gap-1"
                onClick={() => setCreating(true)}>
          <Plus size={13} /> New Pharmacy
        </button>
      </div>

      {creating && (
        <PharmacyEditCard
          initial={null}
          onCancel={() => setCreating(false)}
          onSaved={() => { setCreating(false); qc.invalidateQueries({ queryKey: ['admin-larc-pharmacies'] }) }}
        />
      )}

      <div className="card p-0 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-plum-50 border-b border-plum-200 text-left">
            <tr>
              <th className="px-3 py-2 font-medium">Name</th>
              <th className="px-3 py-2 font-medium">Fax</th>
              <th className="px-3 py-2 font-medium">Phone</th>
              <th className="px-3 py-2 font-medium">Ships</th>
              <th className="px-3 py-2 font-medium">Default for</th>
              <th className="px-3 py-2 font-medium"></th>
            </tr>
          </thead>
          <tbody>
            {isLoading && (
              <tr><td colSpan={6} className="px-3 py-6 text-center text-muted">Loading…</td></tr>
            )}
            {!isLoading && (pharms || []).length === 0 && (
              <tr><td colSpan={6} className="px-3 py-6 text-center text-muted">
                No pharmacies yet. Click "New Pharmacy" to add one.
              </td></tr>
            )}
            {(pharms || []).map(p => (
              <tr key={p.id} className="border-t border-plum-100 hover:bg-plum-50">
                <td className="px-3 py-2">
                  <div className="font-medium text-gray-800">{p.name}</div>
                  {p.notes && (
                    <div className="text-[10px] text-gray-500 mt-0.5">{p.notes}</div>
                  )}
                </td>
                <td className="px-3 py-2 font-mono text-[12px]">{p.fax || '—'}</td>
                <td className="px-3 py-2 font-mono text-[12px]">{p.phone || '—'}</td>
                <td className="px-3 py-2">
                  <div className="flex flex-wrap gap-1">
                    {(p.device_names || []).length === 0 && (
                      <span className="text-[10px] text-amber-700 italic">any</span>
                    )}
                    {(p.device_names || []).map(d => (
                      <span key={d}
                            className="text-[10px] bg-plum-100 text-plum-700 px-1.5 py-0.5 rounded">
                        {d}
                      </span>
                    ))}
                  </div>
                </td>
                <td className="px-3 py-2">
                  <div className="flex flex-wrap gap-1">
                    {(p.default_for_devices || []).length === 0 && (
                      <span className="text-[10px] text-gray-400">—</span>
                    )}
                    {(p.default_for_devices || []).map(d => (
                      <span key={d}
                            className="text-[10px] bg-amber-100 text-amber-800 px-1.5 py-0.5 rounded flex items-center gap-0.5">
                        <Star size={9} fill="currentColor" /> {d}
                      </span>
                    ))}
                  </div>
                </td>
                <td className="px-3 py-2 text-right">
                  <button className="text-[11px] text-plum-700 hover:underline"
                          onClick={() => setEditing(p.id)}>
                    Edit
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {editing && (
        <PharmacyEditCard
          initial={(pharms || []).find(p => p.id === editing)}
          onCancel={() => setEditing(null)}
          onSaved={() => { setEditing(null); qc.invalidateQueries({ queryKey: ['admin-larc-pharmacies'] }) }}
        />
      )}
    </div>
  )
}


function PharmacyEditCard({ initial, onCancel, onSaved }) {
  const isNew = !initial
  const [name,    setName]    = useState(initial?.name || '')
  const [fax,     setFax]     = useState(initial?.fax  || '')
  const [phone,   setPhone]   = useState(initial?.phone || '')
  const [address, setAddress] = useState(initial?.address || '')
  const [notes,   setNotes]   = useState(initial?.notes || '')
  const [deviceNames, setDeviceNames] = useState(new Set(initial?.device_names || []))
  const [defaults,    setDefaults]    = useState(new Set(initial?.default_for_devices || []))
  const [error, setError] = useState(null)

  const mutation = useMutation({
    mutationFn: () => {
      const body = {
        name: name.trim(),
        fax: fax.trim() || null,
        phone: phone.trim() || null,
        address: address.trim() || null,
        notes: notes.trim() || null,
        device_names: [...deviceNames],
        default_for_devices: [...defaults],
      }
      return isNew
        ? api.post('/larc/pharmacies', body).then(r => r.data)
        : api.patch(`/larc/pharmacies/${initial.id}`, body).then(r => r.data)
    },
    onSuccess: onSaved,
    onError: (e) => setError(e?.response?.data?.detail || e.message || 'Save failed'),
  })

  function toggleDevice(d, kind /* 'ships' | 'default' */) {
    if (kind === 'ships') {
      const next = new Set(deviceNames)
      if (next.has(d)) { next.delete(d); const nd = new Set(defaults); nd.delete(d); setDefaults(nd) }
      else next.add(d)
      setDeviceNames(next)
    } else {
      const next = new Set(defaults)
      if (next.has(d)) next.delete(d)
      else {
        next.add(d)
        // Ensure the pharmacy also ships the device it's default for
        if (!deviceNames.has(d)) {
          const ships = new Set(deviceNames); ships.add(d); setDeviceNames(ships)
        }
      }
      setDefaults(next)
    }
  }

  const dirty = isNew || (
    name !== (initial.name || '') ||
    fax !== (initial.fax || '') ||
    phone !== (initial.phone || '') ||
    address !== (initial.address || '') ||
    notes !== (initial.notes || '') ||
    JSON.stringify([...deviceNames].sort()) !== JSON.stringify((initial.device_names || []).sort()) ||
    JSON.stringify([...defaults].sort()) !== JSON.stringify((initial.default_for_devices || []).sort())
  )

  return (
    <div className="card mb-3 bg-plum-50/40 border-plum-100">
      <div className="flex items-baseline justify-between mb-2">
        <div className="text-sm font-semibold text-ink">
          {isNew ? 'New Pharmacy' : `Edit: ${initial.name}`}
        </div>
        <button className="text-muted hover:text-ink" onClick={onCancel}>
          <X size={14} />
        </button>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
        <div>
          <label className="text-[11px] uppercase text-gray-500">Name *</label>
          <input className="input text-sm w-full" value={name}
                 onChange={e => setName(e.target.value)} autoFocus />
        </div>
        <div>
          <label className="text-[11px] uppercase text-gray-500">Fax</label>
          <input className="input text-sm w-full font-mono" value={fax}
                 placeholder="866-216-1681"
                 onChange={e => setFax(e.target.value)} />
        </div>
        <div>
          <label className="text-[11px] uppercase text-gray-500">Phone</label>
          <input className="input text-sm w-full font-mono" value={phone}
                 placeholder="844-639-4321"
                 onChange={e => setPhone(e.target.value)} />
        </div>
        <div>
          <label className="text-[11px] uppercase text-gray-500">Address</label>
          <input className="input text-sm w-full" value={address}
                 onChange={e => setAddress(e.target.value)} />
        </div>
        <div className="sm:col-span-2">
          <label className="text-[11px] uppercase text-gray-500">Notes</label>
          <input className="input text-sm w-full" value={notes}
                 onChange={e => setNotes(e.target.value)} />
        </div>
      </div>

      <div className="mt-3">
        <label className="text-[11px] uppercase text-gray-500">Ships these devices</label>
        <div className="flex flex-wrap gap-1.5 mt-1">
          {PHARMACY_ORDER_DEVICES.map(d => {
            const on = deviceNames.has(d)
            return (
              <button key={d}
                      onClick={() => toggleDevice(d, 'ships')}
                      type="button"
                      className={
                        on
                          ? 'text-[11px] bg-plum-100 text-plum-700 px-2 py-0.5 rounded border border-plum-300'
                          : 'text-[11px] bg-white text-gray-500 px-2 py-0.5 rounded border border-gray-200 hover:border-plum-200'
                      }>
                {d}
              </button>
            )
          })}
        </div>
      </div>

      <div className="mt-3">
        <label className="text-[11px] uppercase text-gray-500 flex items-center gap-1">
          <Star size={10} /> Default for (auto-picked on new assignments)
        </label>
        <div className="flex flex-wrap gap-1.5 mt-1">
          {PHARMACY_ORDER_DEVICES.map(d => {
            const on = defaults.has(d)
            return (
              <button key={d}
                      onClick={() => toggleDevice(d, 'default')}
                      type="button"
                      className={
                        on
                          ? 'text-[11px] bg-amber-100 text-amber-800 px-2 py-0.5 rounded border border-amber-300 flex items-center gap-0.5'
                          : 'text-[11px] bg-white text-gray-500 px-2 py-0.5 rounded border border-gray-200 hover:border-amber-200'
                      }>
                {on && <Star size={9} fill="currentColor" />} {d}
              </button>
            )
          })}
        </div>
        <p className="text-[10px] text-gray-500 mt-1">
          Marking a device as default also adds it to "Ships these devices".
          Only one pharmacy should be default per device — the UI doesn't
          enforce uniqueness yet, but the assignment-create endpoint picks
          the first match by name.
        </p>
      </div>

      {error && (
        <div className="text-danger text-[11px] mt-2 bg-red-50 border border-red-200 rounded px-2 py-1">
          {error}
        </div>
      )}
      <div className="flex justify-end gap-2 mt-3">
        <button className="btn-secondary text-sm" onClick={onCancel}>Cancel</button>
        <button className="btn-primary text-sm"
                onClick={() => mutation.mutate()}
                disabled={!name.trim() || !dirty || mutation.isPending}>
          {mutation.isPending ? 'Saving…' : (isNew ? 'Create' : 'Save')}
        </button>
      </div>
    </div>
  )
}
