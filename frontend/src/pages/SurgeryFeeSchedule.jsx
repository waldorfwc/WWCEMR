import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  ArrowLeft, DollarSign, Layers, Plus, Save, Trash2, X,
} from 'lucide-react'
import api from '../utils/api'


const CCI_ACTIONS = [
  { v: 'blocked',    label: 'Blocked (cannot bill together)' },
  { v: 'reduce_50',  label: 'Reduced 50% (default MPR)' },
  { v: 'allow_100',  label: 'Override — both pay 100%' },
]


export default function SurgeryFeeSchedule() {
  const [tab, setTab] = useState('fee')

  return (
    <div>
      <Link to="/surgery"
            className="text-[12px] text-muted hover:underline flex items-center gap-1 mb-2">
        <ArrowLeft size={12} /> Surgery dashboard
      </Link>
      <h1 className="text-2xl font-bold text-gray-900 mb-1">Fee Schedule</h1>
      <p className="text-sm text-gray-600 mb-4 max-w-2xl">
        Contracted allowed amounts per payer + CPT, and Correct Coding
        Initiative / Multiple Procedure Reduction overrides. Used by the
        per-surgery allowed-amount calculator on the Surgery Detail page.
      </p>

      <div className="flex gap-1 border-b border-gray-200 mb-4">
        <TabButton active={tab === 'fee'} onClick={() => setTab('fee')}
                    icon={<DollarSign size={13} />}>
          Allowed Amounts
        </TabButton>
        <TabButton active={tab === 'cci'} onClick={() => setTab('cci')}
                    icon={<Layers size={13} />}>
          CCI / MPR Edits
        </TabButton>
      </div>

      {tab === 'fee' ? <FeeTable /> : <CciTable />}
    </div>
  )
}


function TabButton({ active, onClick, icon, children }) {
  return (
    <button onClick={onClick}
            className={`px-3 py-2 text-sm flex items-center gap-1.5 border-b-2 -mb-px ${
              active
                ? 'border-plum-700 text-plum-700 font-semibold'
                : 'border-transparent text-gray-600 hover:text-gray-800'
            }`}>
      {icon}{children}
    </button>
  )
}


// ─── Fee schedule table ───────────────────────────────────────────

function FeeTable() {
  const qc = useQueryClient()
  const { data, isLoading } = useQuery({
    queryKey: ['fee-schedule'],
    queryFn: () => api.get('/surgery/fee-schedule').then(r => r.data),
  })
  const { data: picks } = useQuery({
    queryKey: ['surgery-picklists'],
    queryFn: () => api.get('/surgery/picklists').then(r => r.data),
    staleTime: 300_000,
  })

  const [form, setForm] = useState({
    insurance_name: '', cpt_code: '', allowed_amount: '', notes: '',
  })
  const [editingId, setEditingId] = useState(null)
  const [error, setError] = useState(null)

  const upsert = useMutation({
    mutationFn: () => api.post('/surgery/fee-schedule', {
      insurance_name: form.insurance_name,
      cpt_code:       form.cpt_code,
      allowed_amount: Number(form.allowed_amount),
      notes:          form.notes || null,
    }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['fee-schedule'] })
      setForm({ insurance_name: '', cpt_code: '', allowed_amount: '', notes: '' })
      setEditingId(null); setError(null)
    },
    onError: (e) => {
      const d = e?.response?.data?.detail
      setError(typeof d === 'string' ? d : (e?.message || 'Save failed'))
    },
  })

  const del = useMutation({
    mutationFn: (id) => api.delete(`/surgery/fee-schedule/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['fee-schedule'] }),
  })

  function startEdit(row) {
    setEditingId(row.id)
    setForm({
      insurance_name: row.insurance_name,
      cpt_code:       row.cpt_code,
      allowed_amount: row.allowed_amount,
      notes:          row.notes || '',
    })
  }

  return (
    <div className="space-y-4">
      <div className="card !p-3 space-y-2">
        <div className="text-sm font-semibold text-gray-800">
          {editingId ? 'Edit allowed amount' : 'Add allowed amount'}
        </div>
        <div className="grid grid-cols-1 md:grid-cols-[1fr_120px_120px_1fr_auto] gap-2 items-end">
          <select className="input text-sm" aria-label="Insurance" value={form.insurance_name}
                   onChange={e => setForm({ ...form, insurance_name: e.target.value })}>
            <option value="">— select insurance —</option>
            {(picks?.insurance_companies || []).map(n => (
              <option key={n} value={n}>{n}</option>
            ))}
          </select>
          <input type="text" placeholder="CPT" className="input text-sm font-mono"
                  value={form.cpt_code}
                  onChange={e => setForm({ ...form, cpt_code: e.target.value })} />
          <input type="number" step="0.01" placeholder="Allowed $"
                  className="input text-sm font-mono"
                  value={form.allowed_amount}
                  onChange={e => setForm({ ...form, allowed_amount: e.target.value })} />
          <input type="text" placeholder="Notes (optional)"
                  className="input text-sm"
                  value={form.notes}
                  onChange={e => setForm({ ...form, notes: e.target.value })} />
          <div className="flex gap-1">
            <button onClick={() => upsert.mutate()}
                    disabled={upsert.isPending || !form.insurance_name
                                || !form.cpt_code || !form.allowed_amount}
                    className="btn-primary text-sm flex items-center gap-1">
              {editingId ? <><Save size={12} /> Save</> : <><Plus size={12} /> Add</>}
            </button>
            {editingId && (
              <button onClick={() => { setEditingId(null);
                                          setForm({ insurance_name: '', cpt_code: '',
                                                     allowed_amount: '', notes: '' })}}
                      className="btn-secondary text-sm flex items-center gap-1">
                <X size={12} /> Cancel
              </button>
            )}
          </div>
        </div>
        {error && <div className="text-xs text-red-600">{error}</div>}
      </div>

      <div className="card !p-0 overflow-hidden">
        <div className="px-4 py-2 bg-plum-50/40 border-b border-plum-100 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-gray-800">
            Allowed amounts ({data?.rows?.length || 0})
          </h2>
        </div>
        <div className="max-h-[60vh] overflow-y-auto">
          <table className="w-full text-[12px]">
            <thead className="bg-gray-50 sticky top-0">
              <tr className="text-left text-[11px] uppercase text-gray-500">
                <th className="px-3 py-1.5">Insurance</th>
                <th className="px-3 py-1.5">CPT</th>
                <th className="px-3 py-1.5 text-right">Allowed</th>
                <th className="px-3 py-1.5">Notes</th>
                <th className="px-3 py-1.5"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {isLoading && (
                <tr><td colSpan={5} className="px-3 py-3 text-gray-500 italic">Loading…</td></tr>
              )}
              {data?.rows?.length === 0 && (
                <tr><td colSpan={5} className="px-3 py-3 text-gray-500 italic">
                  No entries yet — add the first one above.
                </td></tr>
              )}
              {data?.rows?.map(r => (
                <tr key={r.id}>
                  <td className="px-3 py-1.5">{r.insurance_name}</td>
                  <td className="px-3 py-1.5 font-mono">{r.cpt_code}</td>
                  <td className="px-3 py-1.5 font-mono text-right">${r.allowed_amount.toFixed(2)}</td>
                  <td className="px-3 py-1.5 text-gray-600">{r.notes || ''}</td>
                  <td className="px-3 py-1.5 text-right whitespace-nowrap">
                    <button onClick={() => startEdit(r)}
                            className="text-plum-700 hover:underline text-[11px] mr-2">
                      edit
                    </button>
                    <button onClick={() => { if (confirm('Delete this row?')) del.mutate(r.id) }}
                            className="text-red-700 hover:underline text-[11px]
                                          inline-flex items-center gap-0.5">
                      <Trash2 size={11} /> delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}


// ─── CCI / MPR table ──────────────────────────────────────────────

function CciTable() {
  const qc = useQueryClient()
  const { data, isLoading } = useQuery({
    queryKey: ['cci-edits'],
    queryFn: () => api.get('/surgery/cci-edits').then(r => r.data),
  })

  const [form, setForm] = useState({
    cpt_primary: '', cpt_secondary: '', action: 'blocked', notes: '',
  })
  const [error, setError] = useState(null)

  const upsert = useMutation({
    mutationFn: () => api.post('/surgery/cci-edits', {
      cpt_primary:   form.cpt_primary,
      cpt_secondary: form.cpt_secondary,
      action:        form.action,
      notes:         form.notes || null,
    }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['cci-edits'] })
      setForm({ cpt_primary: '', cpt_secondary: '', action: 'blocked', notes: '' })
      setError(null)
    },
    onError: (e) => {
      const d = e?.response?.data?.detail
      setError(typeof d === 'string' ? d : (e?.message || 'Save failed'))
    },
  })

  const del = useMutation({
    mutationFn: (id) => api.delete(`/surgery/cci-edits/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['cci-edits'] }),
  })

  return (
    <div className="space-y-4">
      <div className="card !p-3">
        <p className="text-[11px] text-gray-600 mb-2">
          By default, when two CPTs are billed together, the calculator
          pays the highest at 100% and every subsequent CPT at 50% (MPR).
          Use this table to override: block a pair, or force both to pay
          100%.
        </p>
        <div className="grid grid-cols-1 md:grid-cols-[120px_120px_220px_1fr_auto] gap-2 items-end">
          <input type="text" placeholder="Primary CPT" className="input text-sm font-mono"
                  value={form.cpt_primary}
                  onChange={e => setForm({ ...form, cpt_primary: e.target.value })} />
          <input type="text" placeholder="Secondary CPT" className="input text-sm font-mono"
                  value={form.cpt_secondary}
                  onChange={e => setForm({ ...form, cpt_secondary: e.target.value })} />
          <select className="input text-sm" value={form.action}
                   onChange={e => setForm({ ...form, action: e.target.value })}>
            {CCI_ACTIONS.map(a => (
              <option key={a.v} value={a.v}>{a.label}</option>
            ))}
          </select>
          <input type="text" placeholder="Notes (optional)" className="input text-sm"
                  value={form.notes}
                  onChange={e => setForm({ ...form, notes: e.target.value })} />
          <button onClick={() => upsert.mutate()}
                  disabled={upsert.isPending || !form.cpt_primary || !form.cpt_secondary}
                  className="btn-primary text-sm flex items-center gap-1">
            <Plus size={12} /> Add
          </button>
        </div>
        {error && <div className="text-xs text-red-600 mt-2">{error}</div>}
      </div>

      <div className="card !p-0 overflow-hidden">
        <div className="px-4 py-2 bg-plum-50/40 border-b border-plum-100">
          <h2 className="text-sm font-semibold text-gray-800">
            CCI / MPR edits ({data?.rows?.length || 0})
          </h2>
        </div>
        <div className="max-h-[60vh] overflow-y-auto">
          <table className="w-full text-[12px]">
            <thead className="bg-gray-50 sticky top-0">
              <tr className="text-left text-[11px] uppercase text-gray-500">
                <th className="px-3 py-1.5">Primary CPT</th>
                <th className="px-3 py-1.5">Secondary CPT</th>
                <th className="px-3 py-1.5">Action</th>
                <th className="px-3 py-1.5">Notes</th>
                <th className="px-3 py-1.5"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {isLoading && (
                <tr><td colSpan={5} className="px-3 py-3 text-gray-500 italic">Loading…</td></tr>
              )}
              {data?.rows?.length === 0 && (
                <tr><td colSpan={5} className="px-3 py-3 text-gray-500 italic">
                  No overrides yet — default MPR applies automatically.
                </td></tr>
              )}
              {data?.rows?.map(r => (
                <tr key={r.id}>
                  <td className="px-3 py-1.5 font-mono">{r.cpt_primary}</td>
                  <td className="px-3 py-1.5 font-mono">{r.cpt_secondary}</td>
                  <td className="px-3 py-1.5">
                    {CCI_ACTIONS.find(a => a.v === r.action)?.label || r.action}
                  </td>
                  <td className="px-3 py-1.5 text-gray-600">{r.notes || ''}</td>
                  <td className="px-3 py-1.5 text-right">
                    <button onClick={() => { if (confirm('Delete this override?')) del.mutate(r.id) }}
                            className="text-red-700 hover:underline text-[11px]
                                          inline-flex items-center gap-0.5">
                      <Trash2 size={11} /> delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
