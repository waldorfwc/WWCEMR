import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { ArrowLeft, Pill, Edit3, Save, X, Shield, Plus } from 'lucide-react'
import api from '../utils/api'


export default function PelletDoseTypes({ embedded = false }) {
  const [editing, setEditing] = useState(null)
  const [adding, setAdding] = useState(false)

  const { data: types = [], isLoading } = useQuery({
    queryKey: ['pellet-dose-types'],
    queryFn: () => api.get('/pellets/dose-types').then(r => r.data),
  })

  return (
    <div>
      {!embedded && (
        <>
          <Link to="/pellets/inventory" className="text-[12px] text-muted hover:underline flex items-center gap-1 mb-2">
            <ArrowLeft size={12}/> Pellet inventory
          </Link>
          <h1 className="page-title flex items-center gap-2 mb-1">
            <Pill size={22} className="text-plum-700"/>
            Dose type catalog
          </h1>
          <p className="text-sm text-gray-500 mb-4">
            Configure reorder thresholds + order quantity per dose. The
            <strong> Reorder alert</strong> panel on the inventory dashboard
            watches on-hand vs. these thresholds.
          </p>
        </>
      )}

      <div className="flex justify-end mb-2">
        <button onClick={() => setAdding(true)}
                className="btn-primary text-sm flex items-center gap-1">
          <Plus size={13}/> Add Dose Type
        </button>
      </div>

      <div className="card !p-0 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-plum-50">
            <tr>
              <th className="table-th">Dose</th>
              <th className="table-th">Schedule</th>
              <th className="table-th text-right">Reorder ≤ packs</th>
              <th className="table-th text-right">Order qty packs</th>
              <th className="table-th">Pack sizes</th>
              <th className="table-th">Notes</th>
              <th className="table-th"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {isLoading && (
              <tr><td colSpan={7} className="table-td text-center text-gray-400 py-6">Loading…</td></tr>
            )}
            {types.map(t => (
              <tr key={t.id} className={!t.is_active ? 'opacity-50' : ''}>
                <td className="table-td font-medium">{t.label}</td>
                <td className="table-td">
                  {t.is_controlled ? (
                    <span className="text-[11px] uppercase px-1.5 py-0.5 rounded bg-amber-100 text-amber-700 flex items-center gap-1 w-fit">
                      <Shield size={9}/> Sch III
                    </span>
                  ) : (
                    <span className="text-[10px] text-gray-400">—</span>
                  )}
                </td>
                <td className="table-td text-right font-mono text-[11px]">
                  {t.reorder_thresholds_by_location ? (
                    <div>
                      <div className="text-[10px] text-plum-700">per-location</div>
                      <div className="text-[10px] text-gray-500">
                        {Object.entries(t.reorder_thresholds_by_location)
                          .map(([loc, n]) => {
                            const short = loc === 'white_plains' ? 'WP' :
                                          loc === 'brandywine'   ? 'BR' : 'AR'
                            return `${short}:${n}`
                          }).join(' · ')}
                      </div>
                    </div>
                  ) : (t.reorder_threshold_packs ?? '—')}
                </td>
                <td className="table-td text-right font-mono text-[11px]">{t.reorder_qty_packs ?? '—'}</td>
                <td className="table-td text-[11px]">{(t.pack_sizes || []).join(' · ')}</td>
                <td className="table-td text-[11px] text-gray-500">{t.notes || '—'}</td>
                <td className="table-td text-right">
                  <button onClick={() => setEditing(t)}
                          className="text-plum-700 hover:bg-plum-50 p-1 rounded">
                    <Edit3 size={12}/>
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {editing && (
        <EditDrawer dose={editing} onClose={() => setEditing(null)} />
      )}
      {adding && (
        <AddDrawer onClose={() => setAdding(false)} />
      )}
    </div>
  )
}


function AddDrawer({ onClose }) {
  const qc = useQueryClient()
  const [form, setForm] = useState({
    hormone: 'estradiol',
    dose_mg: '',
    label: '',
    reorder_threshold_packs: '',
    reorder_qty_packs: '',
    typical_cost_per_dose: '',
    pack_sizes: '',
    notes: '',
  })
  const upd = (k, v) => setForm(f => ({ ...f, [k]: v }))
  const dose = form.dose_mg === '' ? null : Number(form.dose_mg)
  const labelPreview = form.label.trim()
    || (dose ? `${form.hormone[0].toUpperCase()}${form.hormone.slice(1)} ${dose}mg` : '')
  const valid = dose !== null && !isNaN(dose) && dose > 0

  const create = useMutation({
    mutationFn: () => api.post('/pellets/dose-types', {
      hormone: form.hormone,
      dose_mg: dose,
      label: form.label.trim() || null,
      reorder_threshold_packs: form.reorder_threshold_packs === '' ? null : Number(form.reorder_threshold_packs),
      reorder_qty_packs: form.reorder_qty_packs === '' ? null : Number(form.reorder_qty_packs),
      typical_cost_per_dose: form.typical_cost_per_dose === '' ? null : Number(form.typical_cost_per_dose),
      pack_sizes: form.pack_sizes
        ? form.pack_sizes.split(',').map(s => Number(s.trim())).filter(n => !isNaN(n) && n > 0)
        : [],
      notes: form.notes || null,
    }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['pellet-dose-types'] })
      qc.invalidateQueries({ queryKey: ['pellet-dashboard'] })
      onClose()
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Could not add dose type'),
  })

  const controlled = form.hormone === 'testosterone'

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/30"/>
      <div className="relative w-full max-w-md bg-white shadow-xl overflow-y-auto"
           onClick={e => e.stopPropagation()}>
        <div className="sticky top-0 bg-white border-b border-border-subtle px-5 py-3 flex items-center justify-between">
          <h2 className="font-serif font-semibold text-ink text-[16px]">Add Dose Type</h2>
          <button onClick={onClose} className="text-muted hover:text-ink"><X size={18}/></button>
        </div>
        <div className="p-5 space-y-3 text-sm">
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="text-[11px] uppercase text-gray-500 block mb-1">Hormone</label>
              <select className="input text-sm w-full" value={form.hormone}
                      onChange={e => upd('hormone', e.target.value)}>
                <option value="estradiol">Estradiol</option>
                <option value="testosterone">Testosterone</option>
              </select>
            </div>
            <div>
              <label className="text-[11px] uppercase text-gray-500 block mb-1">Dose (mg)</label>
              <input type="number" step="0.01" min="0" className="input text-sm w-full"
                     value={form.dose_mg} onChange={e => upd('dose_mg', e.target.value)} />
            </div>
          </div>
          {controlled && (
            <div className="text-[12px] text-amber-800 bg-amber-50 border border-amber-200 rounded p-2 flex items-center gap-1">
              <Shield size={11}/> Testosterone is DEA Schedule III — it will be tracked as controlled.
            </div>
          )}
          <div>
            <label className="text-[11px] uppercase text-gray-500 block mb-1">Label</label>
            <input className="input text-sm w-full" placeholder={labelPreview || 'auto-generated'}
                   value={form.label} onChange={e => upd('label', e.target.value)} />
            {!form.label.trim() && labelPreview && (
              <p className="text-[11px] text-gray-400 mt-0.5">Will be saved as “{labelPreview}”.</p>
            )}
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="text-[11px] uppercase text-gray-500 block mb-1">Reorder ≤ packs</label>
              <input type="number" min="0" className="input text-sm w-full"
                     value={form.reorder_threshold_packs}
                     onChange={e => upd('reorder_threshold_packs', e.target.value)} />
            </div>
            <div>
              <label className="text-[11px] uppercase text-gray-500 block mb-1">Order qty packs</label>
              <input type="number" min="0" className="input text-sm w-full"
                     value={form.reorder_qty_packs}
                     onChange={e => upd('reorder_qty_packs', e.target.value)} />
            </div>
          </div>
          <div>
            <label className="text-[11px] uppercase text-gray-500 block mb-1">Pack sizes (comma-separated)</label>
            <input className="input text-sm w-full" placeholder="6, 12, 30"
                   value={form.pack_sizes} onChange={e => upd('pack_sizes', e.target.value)} />
          </div>
          <div>
            <label className="text-[11px] uppercase text-gray-500 block mb-1">Typical cost per dose ($)</label>
            <input type="number" step="0.01" className="input text-sm w-full font-mono"
                   value={form.typical_cost_per_dose}
                   onChange={e => upd('typical_cost_per_dose', e.target.value)} />
          </div>
          <div>
            <label className="text-[11px] uppercase text-gray-500 block mb-1">Notes</label>
            <input className="input text-sm w-full" value={form.notes}
                   onChange={e => upd('notes', e.target.value)} />
          </div>
        </div>
        <div className="sticky bottom-0 bg-white border-t border-border-subtle px-5 py-3 flex justify-end gap-2">
          <button className="text-sm text-muted hover:underline" onClick={onClose}>Cancel</button>
          <button className="btn-primary text-sm flex items-center gap-1"
                  disabled={!valid || create.isPending}
                  onClick={() => create.mutate()}>
            <Save size={13}/> {create.isPending ? 'Adding…' : 'Add dose type'}
          </button>
        </div>
      </div>
    </div>
  )
}


function EditDrawer({ dose, onClose }) {
  const qc = useQueryClient()
  const perLoc = dose.reorder_thresholds_by_location || {}
  const [form, setForm] = useState({
    reorder_threshold_packs: dose.reorder_threshold_packs ?? '',
    reorder_qty_packs:       dose.reorder_qty_packs ?? '',
    typical_cost_per_dose:   dose.typical_cost_per_dose ?? '',
    pack_sizes:              (dose.pack_sizes || []).join(', '),
    notes:                   dose.notes || '',
    is_active:               dose.is_active,
    use_per_location:        Object.keys(perLoc).length > 0,
    threshold_wp:            perLoc.white_plains ?? '',
    threshold_br:            perLoc.brandywine   ?? '',
    threshold_ar:            perLoc.arlington    ?? '',
  })
  const upd = (k, v) => setForm(f => ({ ...f, [k]: v }))

  const save = useMutation({
    mutationFn: () => {
      let perLocPayload = null
      if (form.use_per_location) {
        perLocPayload = {}
        if (form.threshold_wp !== '') perLocPayload.white_plains = Number(form.threshold_wp)
        if (form.threshold_br !== '') perLocPayload.brandywine   = Number(form.threshold_br)
        if (form.threshold_ar !== '') perLocPayload.arlington    = Number(form.threshold_ar)
        if (Object.keys(perLocPayload).length === 0) perLocPayload = null
      }
      return api.patch(`/pellets/dose-types/${dose.id}`, {
        reorder_threshold_packs: form.reorder_threshold_packs === ''
                                   ? null : Number(form.reorder_threshold_packs),
        reorder_qty_packs:       form.reorder_qty_packs === ''
                                   ? null : Number(form.reorder_qty_packs),
        reorder_thresholds_by_location: perLocPayload,
        typical_cost_per_dose:   form.typical_cost_per_dose === ''
                                   ? null : Number(form.typical_cost_per_dose),
        pack_sizes: form.pack_sizes
          ? form.pack_sizes.split(',').map(s => Number(s.trim())).filter(n => !isNaN(n) && n > 0)
          : [],
        notes:     form.notes || null,
        is_active: !!form.is_active,
      }).then(r => r.data)
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['pellet-dose-types'] })
      qc.invalidateQueries({ queryKey: ['pellet-dashboard'] })
      onClose()
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Save failed'),
  })

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/30"/>
      <div className="relative w-full max-w-md bg-white shadow-xl overflow-y-auto"
           onClick={e => e.stopPropagation()}>
        <div className="sticky top-0 bg-white border-b border-border-subtle px-5 py-3 flex items-center justify-between">
          <h2 className="font-serif font-semibold text-ink text-[16px]">Edit {dose.label}</h2>
          <button onClick={onClose} className="text-muted hover:text-ink"><X size={18}/></button>
        </div>
        <div className="p-5 space-y-3 text-sm">
          {dose.is_controlled && (
            <div className="text-[12px] text-amber-800 bg-amber-50 border border-amber-200 rounded p-2 flex items-center gap-1">
              <Shield size={11}/> DEA Schedule III — every dispense + disposal is witnessed.
            </div>
          )}
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="text-[11px] uppercase text-gray-500 block mb-1">Reorder ≤ packs (global)</label>
              <input type="number" min="0" className="input text-sm w-full"
                     value={form.reorder_threshold_packs}
                     disabled={form.use_per_location}
                     onChange={e => upd('reorder_threshold_packs', e.target.value)}/>
            </div>
            <div>
              <label className="text-[11px] uppercase text-gray-500 block mb-1">Order qty packs</label>
              <input type="number" min="0" className="input text-sm w-full"
                     value={form.reorder_qty_packs}
                     onChange={e => upd('reorder_qty_packs', e.target.value)}/>
            </div>
          </div>

          <div className="border border-border-subtle rounded p-2 space-y-2">
            <label className="flex items-center gap-2 text-[12px]">
              <input type="checkbox" checked={form.use_per_location}
                      onChange={e => upd('use_per_location', e.target.checked)}/>
              <span>Use per-location thresholds (overrides global)</span>
            </label>
            {form.use_per_location && (
              <>
                <div className="text-[11px] text-gray-500">
                  Threshold in packs at each office. Leave blank to skip
                  reorder alerts for that location.
                </div>
                <div className="grid grid-cols-3 gap-2">
                  <div>
                    <label className="text-[11px] uppercase text-gray-500 block mb-1">White Plains</label>
                    <input type="number" min="0" className="input text-sm w-full"
                            value={form.threshold_wp}
                            onChange={e => upd('threshold_wp', e.target.value)}/>
                  </div>
                  <div>
                    <label className="text-[11px] uppercase text-gray-500 block mb-1">Brandywine</label>
                    <input type="number" min="0" className="input text-sm w-full"
                            value={form.threshold_br}
                            onChange={e => upd('threshold_br', e.target.value)}/>
                  </div>
                  <div>
                    <label className="text-[11px] uppercase text-gray-500 block mb-1">Arlington</label>
                    <input type="number" min="0" className="input text-sm w-full"
                            value={form.threshold_ar}
                            onChange={e => upd('threshold_ar', e.target.value)}/>
                  </div>
                </div>
              </>
            )}
          </div>
          <div>
            <label className="text-[11px] uppercase text-gray-500 block mb-1">Typical cost per dose ($)</label>
            <input type="number" step="0.01" className="input text-sm w-full font-mono"
                   value={form.typical_cost_per_dose}
                   onChange={e => upd('typical_cost_per_dose', e.target.value)}/>
          </div>
          <div>
            <label className="text-[11px] uppercase text-gray-500 block mb-1">Pack sizes (comma-separated)</label>
            <input className="input text-sm w-full"
                   placeholder="6, 12, 30"
                   value={form.pack_sizes}
                   onChange={e => upd('pack_sizes', e.target.value)}/>
          </div>
          <div>
            <label className="text-[11px] uppercase text-gray-500 block mb-1">Notes</label>
            <textarea className="input text-[12px] w-full" rows={2}
                      value={form.notes} onChange={e => upd('notes', e.target.value)}/>
          </div>
          <label className="flex items-center gap-2 text-[12px]">
            <input type="checkbox" checked={form.is_active}
                   onChange={e => upd('is_active', e.target.checked)}/>
            Active (uncheck to hide from forms)
          </label>
        </div>
        <div className="sticky bottom-0 bg-white border-t border-border-subtle px-5 py-3 flex justify-end gap-2">
          <button className="text-sm text-muted hover:underline" onClick={onClose}>Cancel</button>
          <button className="btn-primary text-sm flex items-center gap-1"
                  onClick={() => save.mutate()}
                  disabled={save.isPending}>
            <Save size={12}/> {save.isPending ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  )
}
