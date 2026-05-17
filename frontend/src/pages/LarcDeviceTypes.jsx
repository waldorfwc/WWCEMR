import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { ArrowLeft, Box, Edit3, Plus, X, Save } from 'lucide-react'
import api from '../utils/api'


export default function LarcDeviceTypes() {
  const qc = useQueryClient()
  const [editing, setEditing] = useState(null)   // null | 'new' | type id

  const { data: types = [] } = useQuery({
    queryKey: ['larc-device-types'],
    queryFn: () => api.get('/larc/device-types').then(r => r.data),
  })
  const { data: dsTemplates } = useQuery({
    queryKey: ['larc-docusign-templates'],
    queryFn: () => api.get('/larc/docusign-templates').then(r => r.data),
    retry: false,
    staleTime: 60_000,
  })

  return (
    <div>
      <Link to="/larc" className="text-[12px] text-muted hover:underline flex items-center gap-1 mb-2">
        <ArrowLeft size={12} /> LARC dashboard
      </Link>
      <div className="flex items-baseline justify-between mb-3">
        <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
          <Box size={22} className="text-plum-700" />
          Device type catalog
        </h1>
        <button className="btn-primary text-sm flex items-center gap-1"
                onClick={() => setEditing('new')}>
          <Plus size={13} /> Add type
        </button>
      </div>
      <p className="text-sm text-gray-500 mb-4">
        Configure LARC device types — typical cost, reorder thresholds, and the DocuSign
        enrollment form template per device. Bayer devices (Mirena/Skyla/Kyleena) can share
        a template ID; the patient picks the specific device inside the form.
      </p>

      <div className="card !p-0 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-plum-50">
            <tr>
              <th className="table-th">Name</th>
              <th className="table-th">Manufacturer</th>
              <th className="table-th">Category</th>
              <th className="table-th">Flow</th>
              <th className="table-th">Cost</th>
              <th className="table-th">Reorder ≤ / qty</th>
              <th className="table-th">Enrollment template</th>
              <th className="table-th"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {types.map(t => (
              <tr key={t.id} className={t.is_active ? '' : 'opacity-50'}>
                <td className="table-td font-medium">{t.name}</td>
                <td className="table-td text-[12px]">{t.manufacturer || '—'}</td>
                <td className="table-td text-[11px]">
                  <span className={`px-1.5 py-0.5 rounded text-[10px] uppercase ${
                    t.category === 'office_procedure'
                      ? 'bg-teal-100 text-teal-700'
                      : 'bg-plum-100 text-plum-700'
                  }`}>
                    {t.category === 'office_procedure' ? 'office proc' : 'LARC'}
                  </span>
                </td>
                <td className="table-td text-[11px]">
                  <span className={`px-1.5 py-0.5 rounded text-[10px] uppercase ${
                    t.default_flow === 'in_stock' ? 'bg-green-100 text-green-700' :
                    t.default_flow === 'office_procedure' ? 'bg-teal-100 text-teal-700' :
                    'bg-blue-100 text-blue-700'
                  }`}>
                    {t.default_flow.replace('_', ' ')}
                  </span>
                </td>
                <td className="table-td font-mono text-[11px]">
                  {t.typical_cost ? `$${t.typical_cost}` : '—'}
                </td>
                <td className="table-td text-[11px]">
                  {t.reorder_threshold ?? '—'}
                  {t.reorder_quantity ? ` / ${t.reorder_quantity}` : ''}
                </td>
                <td className="table-td text-[10px] font-mono text-gray-600">
                  {t.enrollment_form_template
                    ? <span title={t.enrollment_form_template}>
                        {t.enrollment_form_template.slice(0, 8)}…
                      </span>
                    : <span className="text-amber-700 italic">not set</span>}
                </td>
                <td className="table-td text-right">
                  <button className="text-plum-700 hover:bg-plum-50 p-1 rounded"
                          onClick={() => setEditing(t)}>
                    <Edit3 size={12} />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {editing && (
        <DeviceTypeForm
          initial={editing === 'new' ? null : editing}
          dsTemplates={dsTemplates}
          onClose={() => setEditing(null)}
          qc={qc} />
      )}
    </div>
  )
}


function DeviceTypeForm({ initial, dsTemplates, onClose, qc }) {
  const [form, setForm] = useState({
    name: initial?.name || '',
    manufacturer: initial?.manufacturer || '',
    category: initial?.category || 'larc',
    default_flow: initial?.default_flow || 'pharmacy_order',
    typical_cost: initial?.typical_cost || '',
    reorder_threshold: initial?.reorder_threshold ?? '',
    reorder_quantity: initial?.reorder_quantity ?? '',
    enrollment_form_template: initial?.enrollment_form_template || '',
    notes: initial?.notes || '',
    is_active: initial?.is_active ?? true,
  })
  const update = (k, v) => {
    setForm(f => {
      const next = { ...f, [k]: v }
      // Keep flow consistent with category
      if (k === 'category' && v === 'office_procedure') next.default_flow = 'office_procedure'
      if (k === 'category' && v === 'larc' && next.default_flow === 'office_procedure') {
        next.default_flow = 'pharmacy_order'
      }
      return next
    })
  }

  const mut = useMutation({
    mutationFn: () => {
      const body = {
        ...form,
        typical_cost: form.typical_cost === '' ? null : Number(form.typical_cost),
        reorder_threshold: form.reorder_threshold === '' ? null : Number(form.reorder_threshold),
        reorder_quantity: form.reorder_quantity === '' ? null : Number(form.reorder_quantity),
        enrollment_form_template: form.enrollment_form_template || null,
        notes: form.notes || null,
      }
      return initial
        ? api.patch(`/larc/device-types/${initial.id}`, body).then(r => r.data)
        : api.post('/larc/device-types', body).then(r => r.data)
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['larc-device-types'] })
      qc.invalidateQueries({ queryKey: ['larc-dashboard'] })
      onClose()
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Save failed'),
  })

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/30" />
      <div className="relative w-full max-w-md bg-white shadow-xl overflow-y-auto"
           onClick={e => e.stopPropagation()}>
        <div className="sticky top-0 bg-white border-b border-border-subtle px-5 py-3 flex items-center justify-between">
          <h2 className="font-serif font-semibold text-ink text-[16px]">
            {initial ? `Edit ${initial.name}` : 'New device type'}
          </h2>
          <button onClick={onClose} className="text-muted hover:text-ink"><X size={18} /></button>
        </div>
        <div className="p-5 space-y-3 text-sm">
          <div>
            <label className="text-[10px] uppercase text-gray-500 block mb-1">Name *</label>
            <input className="input text-sm w-full" required
                   value={form.name} onChange={e => update('name', e.target.value)} />
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="text-[10px] uppercase text-gray-500 block mb-1">Manufacturer</label>
              <input className="input text-sm w-full"
                     value={form.manufacturer}
                     onChange={e => update('manufacturer', e.target.value)} />
            </div>
            <div>
              <label className="text-[10px] uppercase text-gray-500 block mb-1">Category</label>
              <select className="input text-sm w-full"
                      value={form.category}
                      onChange={e => update('category', e.target.value)}>
                <option value="larc">LARC (contraceptive)</option>
                <option value="office_procedure">Office procedure (single-use)</option>
              </select>
            </div>
            <div>
              <label className="text-[10px] uppercase text-gray-500 block mb-1">Default flow</label>
              <select className="input text-sm w-full"
                      value={form.default_flow}
                      onChange={e => update('default_flow', e.target.value)}>
                <option value="in_stock">In-stock (kept on shelf)</option>
                <option value="pharmacy_order">Pharmacy order (patient-specific)</option>
                <option value="office_procedure">Office procedure (assigned at surgery)</option>
              </select>
            </div>
            <div>
              <label className="text-[10px] uppercase text-gray-500 block mb-1">Typical cost ($)</label>
              <input type="number" step="0.01" className="input text-sm w-full font-mono"
                     value={form.typical_cost}
                     onChange={e => update('typical_cost', e.target.value)} />
            </div>
            <div>
              <label className="text-[10px] uppercase text-gray-500 block mb-1">
                Reorder ≤ <span className="text-[9px] text-gray-400">(in-stock only)</span>
              </label>
              <input type="number" min="0" className="input text-sm w-full"
                     value={form.reorder_threshold}
                     onChange={e => update('reorder_threshold', e.target.value)}
                     disabled={form.default_flow === 'pharmacy_order'} />
            </div>
            <div>
              <label className="text-[10px] uppercase text-gray-500 block mb-1">
                Reorder qty <span className="text-[9px] text-gray-400">(how many to order)</span>
              </label>
              <input type="number" min="0" className="input text-sm w-full"
                     value={form.reorder_quantity}
                     onChange={e => update('reorder_quantity', e.target.value)}
                     disabled={form.default_flow === 'pharmacy_order'} />
            </div>
          </div>
          <div>
            <label className="text-[10px] uppercase text-gray-500 block mb-1">DocuSign enrollment template</label>
            {dsTemplates && Array.isArray(dsTemplates) ? (
              <select className="input text-sm w-full mb-1"
                      value={form.enrollment_form_template}
                      onChange={e => update('enrollment_form_template', e.target.value)}>
                <option value="">— none —</option>
                {dsTemplates.map(t => (
                  <option key={t.template_id} value={t.template_id}>
                    {t.name} ({t.template_id?.slice(0, 8)}…)
                  </option>
                ))}
              </select>
            ) : (
              <div className="text-[10px] text-amber-700 mb-1">
                Couldn't list DocuSign templates — type the GUID manually below.
              </div>
            )}
            <input className="input text-[11px] w-full font-mono"
                   value={form.enrollment_form_template}
                   onChange={e => update('enrollment_form_template', e.target.value)}
                   placeholder="DocuSign template ID (GUID)" />
            <div className="text-[10px] text-gray-500 mt-0.5">
              Bayer-shared form? Use the same template GUID on Mirena, Skyla, and Kyleena.
            </div>
          </div>
          <div>
            <label className="text-[10px] uppercase text-gray-500 block mb-1">Notes</label>
            <textarea className="input text-sm w-full" rows={2}
                      value={form.notes}
                      onChange={e => update('notes', e.target.value)} />
          </div>
          <label className="flex items-center gap-2 text-[12px]">
            <input type="checkbox" checked={form.is_active}
                   onChange={e => update('is_active', e.target.checked)} />
            Active
          </label>
        </div>
        <div className="sticky bottom-0 bg-white border-t border-border-subtle px-5 py-3 flex justify-end gap-2">
          <button className="text-sm text-muted hover:underline" onClick={onClose}>Cancel</button>
          <button className="btn-primary text-sm flex items-center gap-1"
                  onClick={() => mut.mutate()}
                  disabled={!form.name.trim() || mut.isPending}>
            <Save size={12} /> {mut.isPending ? 'Saving…' : (initial ? 'Save changes' : 'Create type')}
          </button>
        </div>
      </div>
    </div>
  )
}
