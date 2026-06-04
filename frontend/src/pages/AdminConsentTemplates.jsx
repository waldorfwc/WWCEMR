import { useEffect, useMemo, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import {
  ArrowLeft, Plus, Trash2, Edit3, AlertTriangle, Check, X, RefreshCw,
  ShieldCheck, FileSignature,
} from 'lucide-react'
import api from '../utils/api'


const FACILITIES = [
  { v: '',        label: 'Any facility' },
  { v: 'medstar', label: 'MedStar (hospital)' },
  { v: 'crmc',    label: 'CRMC (hospital)' },
  { v: 'office',  label: 'Office' },
]

// Multi-select options for the template form. "Any facility" is the implicit
// default when nothing is checked, so it's not a checkbox itself.
const FACILITY_OPTIONS = FACILITIES.filter(f => f.v)


function facilityListFromInitial(raw) {
  if (!raw) return []
  if (Array.isArray(raw)) return raw.map(s => String(s).toLowerCase()).filter(Boolean)
  return [String(raw).toLowerCase()]
}


function facilityLabel(raw) {
  const list = facilityListFromInitial(raw)
  if (!list.length) return null  // means "any"
  return list.map(code =>
    FACILITY_OPTIONS.find(f => f.v === code)?.label.replace(/ \(.*\)/, '') || code
  ).join(', ')
}

const MEDICAID_MCO_PRESETS = [
  'priority partners',
  'maryland physicians care',
  'united healthcare community',
  'wellpoint',
  'blue cross family',
  'medstar family',
]


function listFromCommaString(s) {
  return (s || '').split(',').map(x => x.trim()).filter(Boolean)
}


function ChipList({ items }) {
  if (!items || items.length === 0) return <span className="text-gray-400 italic text-[11px]">—</span>
  return (
    <div className="flex flex-wrap gap-1">
      {items.map((x, i) => (
        <span key={i} className="text-[10px] bg-gray-100 text-gray-700 px-1.5 py-0.5 rounded">
          {x}
        </span>
      ))}
    </div>
  )
}


function TemplateForm({ initial, onClose, onSave }) {
  const [form, setForm] = useState(() => ({
    name: initial?.name || '',
    boldsign_template_id: initial?.boldsign_template_id || '',
    cpt_codes_text: (initial?.cpt_codes || []).join(', '),
    procedure_match_text: (initial?.procedure_match || []).join(', '),
    facility_match: facilityListFromInitial(initial?.facility_match),
    insurance_match_text: (initial?.insurance_match || []).join(', '),
    is_supplemental: !!initial?.is_supplemental,
    min_days_before_surgery: initial?.min_days_before_surgery ?? '',
    notes: initial?.notes || '',
    is_active: initial?.is_active ?? true,
  }))
  const [testInput, setTestInput] = useState({
    procedure: '',
    cpt: '',
    facility: '',
    primary_insurance: '',
  })
  const [testResult, setTestResult] = useState(null)

  // List of available BoldSign templates (so admin can pick instead of typing)
  const bsTemplates = useQuery({
    queryKey: ['boldsign-templates'],
    queryFn: () => api.get('/consent-templates/boldsign-templates').then(r => r.data),
    staleTime: 60_000,
    retry: false,
  })

  function applyMedicaidPreset() {
    const existing = listFromCommaString(form.insurance_match_text)
    const merged = Array.from(new Set([...existing, ...MEDICAID_MCO_PRESETS]))
    setForm({ ...form, insurance_match_text: merged.join(', ') })
  }

  const testMatch = useMutation({
    mutationFn: () => api.post('/consent-templates/test-match', testInput).then(r => r.data),
    onSuccess: setTestResult,
  })

  function submit(e) {
    e.preventDefault()
    onSave({
      name: form.name,
      boldsign_template_id: form.boldsign_template_id,
      cpt_codes: listFromCommaString(form.cpt_codes_text),
      procedure_match: listFromCommaString(form.procedure_match_text),
      // Send a list of facility codes — backend normalises empty as "any facility".
      facility_match: form.facility_match,
      insurance_match: listFromCommaString(form.insurance_match_text),
      is_supplemental: form.is_supplemental,
      min_days_before_surgery: form.min_days_before_surgery === ''
        ? null
        : Number(form.min_days_before_surgery),
      notes: form.notes,
      is_active: form.is_active,
    })
  }

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/30" />
      <form className="relative w-full max-w-xl bg-white shadow-xl overflow-y-auto"
            onClick={e => e.stopPropagation()}
            onSubmit={submit}>
        <div className="sticky top-0 bg-white border-b border-border-subtle px-6 py-4 flex items-center justify-between">
          <div>
            <h2 className="font-serif font-semibold text-ink text-[18px]">
              {initial ? 'Edit consent template' : 'New consent template'}
            </h2>
            <div className="text-muted text-[11px]">
              Maps a procedure (and optional facility / insurance) to a BoldSign template ID
            </div>
          </div>
          <button type="button" onClick={onClose} className="text-muted hover:text-ink">
            <X size={18} />
          </button>
        </div>

        <div className="p-6 space-y-4">
          <div>
            <label className="block text-[11px] font-medium text-gray-700 mb-1">
              Name <span className="text-red-500">*</span>
            </label>
            <input className="input w-full text-[13px]" required
                   value={form.name}
                   onChange={e => setForm({ ...form, name: e.target.value })}
                   placeholder="e.g. D&C — MedStar Hospital" />
          </div>

          <div>
            <label className="block text-[11px] font-medium text-gray-700 mb-1">
              BoldSign template <span className="text-red-500">*</span>
            </label>
            {bsTemplates.data && bsTemplates.data.length > 0 && (
              <select className="input w-full text-[12px] mb-1"
                      value={form.boldsign_template_id}
                      onChange={e => setForm({ ...form, boldsign_template_id: e.target.value })}>
                <option value="">— pick a BoldSign template —</option>
                {bsTemplates.data.map(t => (
                  <option key={t.template_id} value={t.template_id}>
                    {t.name} ({(t.template_id || '').slice(0, 8)}…)
                  </option>
                ))}
              </select>
            )}
            <input className="input w-full text-[12px] font-mono"
                   required
                   value={form.boldsign_template_id}
                   onChange={e => setForm({ ...form, boldsign_template_id: e.target.value })}
                   placeholder="BoldSign template ID" />
            {bsTemplates.isLoading && (
              <div className="text-[11px] text-gray-500 mt-1">
                Loading BoldSign templates…
              </div>
            )}
            {bsTemplates.error && (
              <div className="text-[11px] text-amber-700 mt-1">
                Couldn't list BoldSign templates ({bsTemplates.error?.response?.status || 'network'}) — paste the template ID manually below.
              </div>
            )}
          </div>

          <div>
            <label className="block text-[11px] font-medium text-gray-700 mb-1">
              CPT codes <span className="text-gray-500 font-normal">(primary match)</span>
            </label>
            <input className="input w-full text-[12px] font-mono"
                   value={form.cpt_codes_text}
                   onChange={e => setForm({ ...form, cpt_codes_text: e.target.value })}
                   placeholder="comma-separated, e.g. 58300, 58301" />
            <div className="text-[10px] text-gray-500 mt-0.5">
              Most reliable. One template can cover multiple CPTs (e.g. IUD insert/remove).
              For same-CPT-different-form (D&amp;C office vs hospital), set Facility below.
            </div>
          </div>

          <div>
            <label className="block text-[11px] font-medium text-gray-700 mb-1">
              Procedure keywords <span className="text-gray-500 font-normal">(fallback)</span>
            </label>
            <input className="input w-full text-[12px]"
                   value={form.procedure_match_text}
                   onChange={e => setForm({ ...form, procedure_match_text: e.target.value })}
                   placeholder="comma-separated, e.g. d&c, dilation, dilatation" />
            <div className="text-[10px] text-gray-500 mt-0.5">
              Used only when CPT codes is empty. Substring match (case-insensitive) on the procedure name.
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-[11px] font-medium text-gray-700 mb-1">
                Facility (optional)
              </label>
              <div className="space-y-1">
                {FACILITY_OPTIONS.map(f => {
                  const checked = form.facility_match.includes(f.v)
                  return (
                    <label key={f.v} className="flex items-center gap-2 text-[12px]">
                      <input type="checkbox"
                             checked={checked}
                             onChange={() => {
                               const next = checked
                                 ? form.facility_match.filter(v => v !== f.v)
                                 : [...form.facility_match, f.v]
                               setForm({ ...form, facility_match: next })
                             }} />
                      {f.label}
                    </label>
                  )
                })}
              </div>
              <div className="text-[10px] text-gray-500 mt-1">
                {form.facility_match.length === 0
                  ? 'Matches any facility.'
                  : 'Matches only the checked facility/facilities.'}
              </div>
            </div>
            <div>
              <label className="block text-[11px] font-medium text-gray-700 mb-1">
                Min days before surgery
              </label>
              <input type="number" min="0" className="input w-full text-[12px]"
                     value={form.min_days_before_surgery}
                     onChange={e => setForm({ ...form, min_days_before_surgery: e.target.value })}
                     placeholder="e.g. 30 for Medicaid sterilization" />
            </div>
          </div>

          <div>
            <label className="block text-[11px] font-medium text-gray-700 mb-1">
              Insurance keywords (optional)
            </label>
            <input className="input w-full text-[12px]"
                   value={form.insurance_match_text}
                   onChange={e => setForm({ ...form, insurance_match_text: e.target.value })}
                   placeholder="comma-separated; leave empty to match any insurance" />
            <div className="flex items-center gap-3 mt-1">
              <button type="button"
                      onClick={applyMedicaidPreset}
                      className="text-[11px] text-plum-700 hover:underline">
                + Apply Medicaid MCO preset
              </button>
              <span className="text-[10px] text-gray-500">
                (Priority Partners, MD Physicians Care, UHC Community, Wellpoint, BCBS Family, MedStar Family)
              </span>
            </div>
          </div>

          <div className="flex items-center gap-4">
            <label className="flex items-center gap-2 text-[12px]">
              <input type="checkbox" checked={form.is_supplemental}
                     onChange={e => setForm({ ...form, is_supplemental: e.target.checked })} />
              Supplemental (attaches in addition to primary)
            </label>
            <label className="flex items-center gap-2 text-[12px]">
              <input type="checkbox" checked={form.is_active}
                     onChange={e => setForm({ ...form, is_active: e.target.checked })} />
              Active
            </label>
          </div>

          <div>
            <label className="block text-[11px] font-medium text-gray-700 mb-1">Notes</label>
            <textarea className="input w-full text-[12px]" rows={2}
                      value={form.notes}
                      onChange={e => setForm({ ...form, notes: e.target.value })}
                      placeholder="Optional internal notes about this template" />
          </div>

          <div className="border-t border-gray-100 pt-4">
            <h3 className="text-[12px] font-semibold text-gray-700 mb-2 flex items-center gap-1.5">
              <ShieldCheck size={13} className="text-plum-600" />
              Test match
            </h3>
            <div className="grid grid-cols-4 gap-2">
              <input className="input text-[11px] font-mono" placeholder="CPT"
                     value={testInput.cpt}
                     onChange={e => setTestInput({ ...testInput, cpt: e.target.value })} />
              <input className="input text-[11px]" placeholder="Procedure name"
                     value={testInput.procedure}
                     onChange={e => setTestInput({ ...testInput, procedure: e.target.value })} />
              <select className="input text-[11px]"
                      value={testInput.facility}
                      onChange={e => setTestInput({ ...testInput, facility: e.target.value })}>
                <option value="">(any facility)</option>
                {FACILITIES.filter(f => f.v).map(f => (
                  <option key={f.v} value={f.v}>{f.label}</option>
                ))}
              </select>
              <input className="input text-[11px]" placeholder="Primary insurance"
                     value={testInput.primary_insurance}
                     onChange={e => setTestInput({ ...testInput, primary_insurance: e.target.value })} />
            </div>
            <button type="button"
                    className="btn-secondary text-[11px] mt-2 flex items-center gap-1"
                    disabled={(!testInput.cpt && !testInput.procedure) || testMatch.isPending}
                    onClick={() => testMatch.mutate()}>
              <RefreshCw size={11} className={testMatch.isPending ? 'animate-spin' : ''} />
              Run match
            </button>
            {testResult && (
              <div className="mt-2 text-[11px] space-y-0.5">
                {testResult.map(r => (
                  <div key={r.template_id} className={`flex items-center gap-2 ${r.matches ? 'text-green-700' : 'text-gray-500'}`}>
                    {r.matches ? <Check size={11} /> : <X size={11} />}
                    <span className="font-medium">{r.name}</span>
                    {r.is_supplemental && <span className="text-[9px] bg-amber-100 text-amber-700 px-1 rounded">SUPPL</span>}
                    <span className="text-[10px] text-gray-400">
                      proc:{r.procedure_match_ok ? '✓' : '✗'} fac:{r.facility_match_ok ? '✓' : '✗'} ins:{r.insurance_match_ok ? '✓' : '✗'}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        <div className="sticky bottom-0 bg-white border-t border-border-subtle px-6 py-3 flex justify-end gap-2">
          <button type="button" className="text-sm text-muted hover:underline" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn-primary text-sm">
            {initial ? 'Save changes' : 'Create template'}
          </button>
        </div>
      </form>
    </div>
  )
}


export default function AdminConsentTemplates() {
  const qc = useQueryClient()
  const { data: templates, isLoading } = useQuery({
    queryKey: ['consent-templates'],
    queryFn: () => api.get('/consent-templates').then(r => r.data),
  })
  const [editing, setEditing] = useState(null)   // null | 'new' | template object
  const [filter, setFilter] = useState('')

  // FastAPI 422s come back as { detail: [{loc, msg, type}, ...] } — passing
  // that array to alert() yields '[object Object]'. Normalize to a readable
  // string for any shape.
  function fmtErr(err, fallback) {
    const d = err?.response?.data?.detail
    if (typeof d === 'string' && d) return d
    if (Array.isArray(d)) {
      return d.map(item =>
        typeof item === 'string' ? item
          : `${(item?.loc || []).slice(1).join('.')}: ${item?.msg || JSON.stringify(item)}`
      ).join('\n') || fallback
    }
    if (d && typeof d === 'object') return d.message || JSON.stringify(d)
    return err?.message || fallback
  }

  const createMut = useMutation({
    mutationFn: (body) => api.post('/consent-templates', body).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['consent-templates'] })
      setEditing(null)
    },
    onError: (err) => alert(fmtErr(err, 'Create failed')),
  })
  const updateMut = useMutation({
    mutationFn: ({ id, body }) => api.put(`/consent-templates/${id}`, body).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['consent-templates'] })
      setEditing(null)
    },
    onError: (err) => alert(fmtErr(err, 'Save failed')),
  })
  const deleteMut = useMutation({
    mutationFn: (id) => api.delete(`/consent-templates/${id}`).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['consent-templates'] }),
    onError: (err) => alert(fmtErr(err, 'Delete failed')),
  })

  const filtered = useMemo(() => {
    const f = filter.trim().toLowerCase()
    if (!f) return templates || []
    return (templates || []).filter(t =>
      t.name.toLowerCase().includes(f)
      || (t.cpt_codes || []).some(c => c.toLowerCase().includes(f))
      || (t.procedure_match || []).some(p => p.includes(f))
      || (t.insurance_match || []).some(i => i.includes(f))
    )
  }, [templates, filter])

  return (
    <div>
      <div className="flex items-baseline justify-between mb-4">
        <div className="flex items-center gap-3">
          <Link to="/admin" className="text-muted hover:text-plum-700">
            <ArrowLeft size={16} />
          </Link>
          <div>
            <h1 className="font-serif font-semibold text-ink text-[22px] m-0 flex items-center gap-2">
              <FileSignature size={22} className="text-plum-700" />
              Consent Templates
            </h1>
            <div className="text-muted text-[12px] mt-0.5">
              Map each procedure to a BoldSign template. Supplemental forms (Medicaid sterilization, etc.)
              attach in addition to the primary.
            </div>
          </div>
        </div>
        <button className="btn-primary text-sm flex items-center gap-1"
                onClick={() => setEditing('new')}>
          <Plus size={13} /> New template
        </button>
      </div>

      <div className="mb-3">
        <input className="input w-full max-w-md text-[12px]"
               placeholder="Filter by name, CPT, procedure keyword, or insurance"
               value={filter}
               onChange={e => setFilter(e.target.value)} />
      </div>

      <div className="card p-0 overflow-hidden">
        <table className="w-full">
          <thead className="bg-plum-50">
            <tr>
              <th className="table-th">Name</th>
              <th className="table-th">CPT codes</th>
              <th className="table-th">Procedure keywords</th>
              <th className="table-th">Facility</th>
              <th className="table-th">Insurance filter</th>
              <th className="table-th text-center">Suppl</th>
              <th className="table-th text-center">Min days</th>
              <th className="table-th text-center">In use</th>
              <th className="table-th"></th>
            </tr>
          </thead>
          <tbody>
            {isLoading && (
              <tr><td colSpan={9} className="table-td text-center text-muted py-8">Loading…</td></tr>
            )}
            {!isLoading && filtered.length === 0 && (
              <tr><td colSpan={9} className="table-td text-center text-muted py-8">
                No templates yet. Click "New template" to register one.
              </td></tr>
            )}
            {filtered.map(t => (
              <tr key={t.id} className={`table-row ${!t.is_active ? 'opacity-50' : ''}`}>
                <td className="table-td">
                  <div className="font-medium text-[13px]">{t.name}</div>
                  <div className="text-[10px] text-gray-500 font-mono">
                    {(t.boldsign_template_id || '').slice(0, 12)}…
                  </div>
                </td>
                <td className="table-td"><ChipList items={t.cpt_codes} /></td>
                <td className="table-td"><ChipList items={t.procedure_match} /></td>
                <td className="table-td text-[11px]">
                  {facilityLabel(t.facility_match) || <span className="text-gray-400 italic">any</span>}
                </td>
                <td className="table-td"><ChipList items={t.insurance_match} /></td>
                <td className="table-td text-center">
                  {t.is_supplemental
                    ? <span className="text-[10px] bg-amber-100 text-amber-700 px-1.5 py-0.5 rounded">YES</span>
                    : <span className="text-gray-300">—</span>}
                </td>
                <td className="table-td text-center text-[11px]">
                  {t.min_days_before_surgery ?? <span className="text-gray-300">—</span>}
                </td>
                <td className="table-td text-center text-[11px] text-muted">
                  {t.in_use_count > 0 ? t.in_use_count : <span className="text-gray-300">0</span>}
                </td>
                <td className="table-td">
                  <div className="flex gap-1">
                    <button className="text-plum-700 hover:bg-plum-50 p-1 rounded"
                            onClick={() => setEditing(t)} title="Edit">
                      <Edit3 size={13} />
                    </button>
                    <button className="text-red-600 hover:bg-red-50 p-1 rounded disabled:opacity-30"
                            onClick={() => {
                              if (confirm(`Delete "${t.name}"?`)) deleteMut.mutate(t.id)
                            }}
                            disabled={t.in_use_count > 0}
                            title={t.in_use_count > 0 ? "Can't delete — in use" : 'Delete'}>
                      <Trash2 size={13} />
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {editing && (
        <TemplateForm
          initial={editing === 'new' ? null : editing}
          onClose={() => setEditing(null)}
          onSave={(body) => {
            if (editing === 'new') createMut.mutate(body)
            else updateMut.mutate({ id: editing.id, body })
          }}
        />
      )}
    </div>
  )
}
