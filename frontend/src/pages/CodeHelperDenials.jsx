import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Trash2, ChevronLeft } from 'lucide-react'
import { Link } from 'react-router-dom'
import api, { fmt } from '../utils/api'


export default function CodeHelperDenials() {
  const qc = useQueryClient()
  const [showInactive, setShowInactive] = useState(false)
  const [adding, setAdding] = useState(false)
  const [form, setForm] = useState({
    code: '', code_type: 'cpt', payer_name: '', reason: '',
  })

  const { data } = useQuery({
    queryKey: ['code-helper-denials', showInactive],
    queryFn: () => api.get('/billing/code-helper/denials',
                            { params: { active: showInactive ? 'false' : 'true' } })
                       .then(r => r.data),
  })

  const create = useMutation({
    mutationFn: () => api.post('/billing/code-helper/denials', {
      code: form.code.trim(),
      code_type: form.code_type,
      payer_name: form.payer_name.trim() || null,
      reason: form.reason.trim() || null,
    }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['code-helper-denials'] })
      setAdding(false)
      setForm({ code: '', code_type: 'cpt', payer_name: '', reason: '' })
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Add failed'),
  })

  const toggleActive = useMutation({
    mutationFn: (d) => api.patch(`/billing/code-helper/denials/${d.id}`,
                                    { is_active: !d.is_active }).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['code-helper-denials'] }),
  })

  const del = useMutation({
    mutationFn: (id) => api.delete(`/billing/code-helper/denials/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['code-helper-denials'] }),
  })

  return (
    <div>
      <Link to="/billing/code-helper"
            className="text-sm text-plum-700 hover:underline inline-flex items-center gap-1 mb-3">
        <ChevronLeft size={13} /> Back to Code Helper
      </Link>
      <div className="flex items-baseline justify-between mb-4">
        <h1 className="text-2xl font-bold text-gray-900">Denial List</h1>
        <div className="flex items-center gap-2">
          <label className="text-[11px] text-gray-500">
            <input type="checkbox" checked={showInactive}
                    onChange={e => setShowInactive(e.target.checked)} />
            {' '}Show inactive
          </label>
          <button className="btn-primary text-sm flex items-center gap-1"
                  onClick={() => setAdding(true)}>
            <Plus size={12} /> Add denial
          </button>
        </div>
      </div>

      {adding && (
        <div className="card mb-3">
          <h2 className="text-sm font-semibold mb-2">New Denial Entry</h2>
          <div className="grid grid-cols-4 gap-2 text-sm">
            <input className="input" placeholder="Code (e.g. 97110)"
                    value={form.code} onChange={e => setForm({...form, code: e.target.value})} />
            <select className="input" value={form.code_type}
                     onChange={e => setForm({...form, code_type: e.target.value})}>
              <option value="cpt">CPT</option>
              <option value="icd10">ICD-10</option>
            </select>
            <input className="input" placeholder="Payer (blank = all)"
                    value={form.payer_name}
                    onChange={e => setForm({...form, payer_name: e.target.value})} />
            <input className="input" placeholder="Reason (optional)"
                    value={form.reason}
                    onChange={e => setForm({...form, reason: e.target.value})} />
          </div>
          <div className="flex gap-2 mt-3 justify-end">
            <button className="text-sm text-muted" onClick={() => setAdding(false)}>Cancel</button>
            <button className="btn-primary text-sm"
                    disabled={!form.code || create.isPending}
                    onClick={() => create.mutate()}>
              {create.isPending ? 'Adding…' : 'Add'}
            </button>
          </div>
        </div>
      )}

      <div className="card !p-0 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-plum-50 text-[11px] uppercase">
            <tr>
              <th className="table-th">Code</th>
              <th className="table-th">Type</th>
              <th className="table-th">Payer</th>
              <th className="table-th">Reason</th>
              <th className="table-th">Added</th>
              <th className="table-th">Active</th>
              <th className="table-th"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {(data?.denials || []).map(d => (
              <tr key={d.id} className={!d.is_active ? 'opacity-50' : ''}>
                <td className="table-td"><code>{d.code}</code></td>
                <td className="table-td text-[11px] uppercase">{d.code_type}</td>
                <td className="table-td text-[11px]">{d.payer_name || <em>all</em>}</td>
                <td className="table-td text-[11px]">{d.reason || '—'}</td>
                <td className="table-td text-[11px]">
                  {fmt.date(d.added_at.slice(0, 10))} · {d.added_by?.split('@')[0]}
                </td>
                <td className="table-td">
                  <input type="checkbox" checked={d.is_active}
                          onChange={() => toggleActive.mutate(d)} />
                </td>
                <td className="table-td">
                  <button className="text-red-600 hover:bg-red-50 p-1 rounded"
                          title="Delete"
                          onClick={() => window.confirm('Delete this entry?') && del.mutate(d.id)}>
                    <Trash2 size={12} />
                  </button>
                </td>
              </tr>
            ))}
            {!(data?.denials || []).length && (
              <tr><td colSpan={7} className="table-td text-center text-gray-400 italic py-6">
                No denial entries yet.
              </td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
