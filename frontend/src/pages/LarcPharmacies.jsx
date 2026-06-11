import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { ArrowLeft, Building2, Plus, X } from 'lucide-react'
import api from '../utils/api'
import EmptyState from '../components/EmptyState'


export default function LarcPharmacies() {
  const qc = useQueryClient()
  const [adding, setAdding] = useState(false)

  const { data: rows = [] } = useQuery({
    queryKey: ['larc-pharmacies'],
    queryFn: () => api.get('/larc/pharmacies').then(r => r.data),
  })

  return (
    <div>
      <Link to="/larc" className="text-[12px] text-muted hover:underline flex items-center gap-1 mb-2">
        <ArrowLeft size={12} /> LARC dashboard
      </Link>
      <div className="flex items-baseline justify-between mb-3">
        <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
          <Building2 size={22} className="text-plum-700" />
          Pharmacy directory
        </h1>
        <button className="btn-primary text-sm flex items-center gap-1"
                onClick={() => setAdding(true)}>
          <Plus size={13} /> Add Pharmacy
        </button>
      </div>
      <p className="text-sm text-gray-500 mb-4">
        Pharmacies that supply patient-specific LARC orders (Mirena/Skyla/Kyleena/Paragard/Nexplanon).
        Pick a pharmacy when faxing a request — fax number auto-fills.
      </p>

      <div className="card !p-0 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-plum-50">
            <tr>
              <th className="table-th">Name</th>
              <th className="table-th">Fax</th>
              <th className="table-th">Phone</th>
              <th className="table-th">Accepts insurance</th>
              <th className="table-th">Notes</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {rows.length === 0 && (
              <tr>
                <td colSpan={5} className="table-td">
                  <EmptyState
                    icon={Building2}
                    title="No pharmacies configured"
                    body={<>Click <strong>+ Add Pharmacy</strong> above to set up where LARC orders fax to.</>}
                    compact
                  />
                </td>
              </tr>
            )}
            {rows.map(p => (
              <tr key={p.id}>
                <td className="table-td">
                  <div className="font-medium">{p.name}</div>
                  {p.address && <div className="text-[10px] text-gray-500">{p.address}</div>}
                </td>
                <td className="table-td font-mono text-[11px]">{p.fax || '—'}</td>
                <td className="table-td font-mono text-[11px]">{p.phone || '—'}</td>
                <td className="table-td text-[11px]">
                  {(p.accepts_insurance || []).length === 0 ? (
                    <span className="text-gray-400 italic">any</span>
                  ) : (
                    <div className="flex flex-wrap gap-1">
                      {p.accepts_insurance.map((i, ix) => (
                        <span key={ix} className="text-[11px] bg-gray-100 px-1 py-0.5 rounded">{i}</span>
                      ))}
                    </div>
                  )}
                </td>
                <td className="table-td text-[11px] text-gray-600">{p.notes || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {adding && <AddPharmacyForm onClose={() => setAdding(false)}
                                     qc={qc} />}
    </div>
  )
}


function AddPharmacyForm({ onClose, qc }) {
  const [form, setForm] = useState({
    name: '', fax: '', phone: '', address: '',
    accepts_insurance_text: '', notes: '',
  })
  const update = (k, v) => setForm(f => ({ ...f, [k]: v }))

  const create = useMutation({
    mutationFn: () => api.post('/larc/pharmacies', {
      name: form.name.trim(),
      fax: form.fax || null,
      phone: form.phone || null,
      address: form.address || null,
      accepts_insurance: form.accepts_insurance_text
        .split(',').map(s => s.trim().toLowerCase()).filter(Boolean),
      notes: form.notes || null,
    }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['larc-pharmacies'] })
      onClose()
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Add failed'),
  })

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/30" />
      <div className="relative w-full max-w-md bg-white shadow-xl overflow-y-auto"
           onClick={e => e.stopPropagation()}>
        <div className="sticky top-0 bg-white border-b border-border-subtle px-5 py-3 flex items-center justify-between">
          <h2 className="font-serif font-semibold text-ink text-[16px]">Add Pharmacy</h2>
          <button onClick={onClose} className="text-muted hover:text-ink"><X size={18} /></button>
        </div>
        <div className="p-5 space-y-3 text-sm">
          <div>
            <label className="text-[11px] uppercase text-gray-500 block mb-1">Name *</label>
            <input className="input text-sm w-full" required
                   value={form.name} onChange={e => update('name', e.target.value)} />
          </div>
          <div>
            <label className="text-[11px] uppercase text-gray-500 block mb-1">Fax</label>
            <input className="input text-sm w-full font-mono"
                   placeholder="240-555-1234"
                   value={form.fax} onChange={e => update('fax', e.target.value)} />
          </div>
          <div>
            <label className="text-[11px] uppercase text-gray-500 block mb-1">Phone</label>
            <input className="input text-sm w-full font-mono"
                   value={form.phone} onChange={e => update('phone', e.target.value)} />
          </div>
          <div>
            <label className="text-[11px] uppercase text-gray-500 block mb-1">Address</label>
            <input className="input text-sm w-full"
                   value={form.address} onChange={e => update('address', e.target.value)} />
          </div>
          <div>
            <label className="text-[11px] uppercase text-gray-500 block mb-1">
              Accepts insurance (comma-separated keywords)
            </label>
            <input className="input text-sm w-full"
                   placeholder="e.g. priority partners, medicaid"
                   value={form.accepts_insurance_text}
                   onChange={e => update('accepts_insurance_text', e.target.value)} />
          </div>
          <div>
            <label className="text-[11px] uppercase text-gray-500 block mb-1">Notes</label>
            <textarea className="input text-sm w-full" rows={2}
                      value={form.notes} onChange={e => update('notes', e.target.value)} />
          </div>
        </div>
        <div className="sticky bottom-0 bg-white border-t border-border-subtle px-5 py-3 flex justify-end gap-2">
          <button className="text-sm text-muted hover:underline" onClick={onClose}>Cancel</button>
          <button className="btn-primary text-sm"
                  onClick={() => create.mutate()}
                  disabled={!form.name.trim() || create.isPending}>
            {create.isPending ? 'Adding…' : 'Add pharmacy'}
          </button>
        </div>
      </div>
    </div>
  )
}
