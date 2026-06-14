import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { Upload, X, FileText } from 'lucide-react'
import api from '../../utils/api'
import { useFacilities } from '../../hooks/useFacilities'


export function UploadDrawer({ onClose }) {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [file, setFile] = useState(null)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)

  const upload = useMutation({
    mutationFn: () => {
      const fd = new FormData()
      fd.append('file', file)
      return api.post('/surgery/orders/upload', fd, {
        headers: { 'Content-Type': 'multipart/form-data' },
      }).then(r => r.data)
    },
    onSuccess: (data) => {
      setResult(data); setError(null)
      qc.invalidateQueries({ queryKey: ['surgery-list'] })
      qc.invalidateQueries({ queryKey: ['surgery-dashboard'] })
    },
    onError: (e) => {
      setError(e?.response?.data?.detail || e.message)
      setResult(null)
    },
  })

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/30" />
      <div className="relative w-full max-w-xl bg-white shadow-xl overflow-y-auto"
           onClick={e => e.stopPropagation()}>
        <div className="sticky top-0 bg-white border-b border-border-subtle px-6 py-4 flex items-center justify-between z-10">
          <h2 className="font-serif font-semibold text-ink text-[18px]">Upload Surgery Order</h2>
          <button onClick={onClose} className="text-muted hover:text-ink"><X size={18} /></button>
        </div>

        <div className="p-6 space-y-4">
          <div className="card !p-3 text-xs text-gray-700 bg-gray-50">
            Upload a ModMed surgery order PDF. The system will use Claude to
            extract patient, procedure, insurance, and facility info. If the
            chart number matches a row already imported via{' '}
            <strong>Upload Surgery Patient Demographics</strong>, the order
            will be mapped onto that existing row; otherwise a new surgery is
            created in <strong>incomplete</strong> status. Review and then
            mark as <strong>new</strong>.
          </div>

          <div className="card !p-3 space-y-2">
            <label className="flex items-center gap-2 text-sm">
              <FileText size={14} className="text-plum-700" />
              <span>Pick the order PDF</span>
            </label>
            <input
              type="file" accept=".pdf"
              className="text-xs"
              onChange={e => {
                setFile(e.target.files?.[0] || null)
                setResult(null); setError(null)
              }}
            />
            <button
              className="btn-primary text-sm flex items-center gap-1 mt-1 disabled:opacity-60"
              disabled={!file || upload.isPending}
              onClick={() => upload.mutate()}>
              <Upload size={13} /> {upload.isPending ? 'Parsing with Claude…' : 'Parse + create'}
            </button>
          </div>

          {error && (
            <div className="card !p-3 bg-red-50 border-red-200 text-xs text-red-800">
              ✗ {error}
            </div>
          )}

          {result?.duplicate && (
            <div className="card !p-3 bg-amber-50 border-amber-200 text-xs text-amber-900">
              <div className="font-semibold">⚠ Possible duplicate</div>
              <p className="mt-1">{result.message}</p>
              <div className="flex gap-2 mt-2">
                <button className="btn-secondary text-xs"
                        onClick={() => { onClose(); navigate(`/surgery/${result.existing_id}`) }}>
                  Open existing surgery
                </button>
              </div>
            </div>
          )}

          {result && !result.duplicate && (
            <div className="card !p-3 bg-green-50 border-green-200 text-xs text-green-900 space-y-1">
              <div className="font-semibold">
                {result.merged ? '✓ Order mapped to existing patient' : '✓ Surgery created'}
              </div>
              <p>{result.message}</p>
              <div className="text-[11px] text-gray-700 mt-2 space-y-0.5">
                <div><strong>Patient:</strong> {result.extracted.patient_name} (chart {result.extracted.chart_number})</div>
                {(result.extracted.procedures || []).map((p, i) => (
                  <div key={i}><strong>Procedure:</strong> {p.description}{p.cpt && ` [${p.cpt}]`}</div>
                ))}
                <div><strong>Facility:</strong> {(result.extracted.eligible_facilities || []).join(' or ') || '—'}</div>
                <div><strong>Insurance:</strong> {result.extracted.primary_insurance || '—'}</div>
                {result.extracted.is_robotic && <div className="text-blue-700">🤖 Robotic — auto-routed to MedStar</div>}
              </div>
              <div className="flex gap-2 mt-3">
                <button className="btn-primary text-xs"
                        onClick={() => { onClose(); navigate(`/surgery/${result.id}`) }}>
                  Open surgery →
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}


export function ManualCreateDrawer({ onClose }) {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const { labelOf } = useFacilities()
  // Picklists drive insurance/surgeon dropdowns
  const { data: picks } = useQuery({
    queryKey: ['surgery-picklists'],
    queryFn: () => api.get('/surgery/picklists').then(r => r.data),
    staleTime: 300_000,
  })
  const insuranceOpts = picks?.insurance_companies || []
  const surgeonOpts   = picks?.surgeons || []
  const procedureOpts = picks?.procedures || []
  const [form, setForm] = useState({
    chart_number: '',
    patient_name: '',
    dob: '',
    phone: '',
    email: '',
    address_street: '',
    address_city: '',
    address_state: '',
    address_zip: '',
    primary_insurance: '',
    primary_member_id: '',
    secondary_insurance: '',
    secondary_member_id: '',
    surgeon_primary: '',
    surgery_name: '',
    procedures: [{ cpt: '', description: '' }],
    diagnoses:  [{ icd: '', description: '' }],
    eligible_facilities: ['medstar'],
    estimated_minutes: 180,
    preop_date: '',
    is_robotic: false,
    is_urgent: false,
    notes: '',
  })
  const [error, setError] = useState(null)

  const requiredMissing =
    !form.chart_number.trim() || !form.patient_name.trim()
    || !form.dob || !form.phone.trim() || !form.email.trim()
    || !form.address_street.trim() || !form.address_city.trim()
    || !form.address_state.trim() || !form.address_zip.trim()
    || !form.primary_insurance || !form.primary_member_id.trim()
    || !form.surgeon_primary || !form.surgery_name
    || !form.preop_date
    || !form.estimated_minutes
    || !form.eligible_facilities.length
    || !form.procedures.some(p => (p.cpt || '').trim() || (p.description || '').trim())
    || !form.diagnoses.some(d => (d.icd || '').trim() || (d.description || '').trim())

  function pickSurgery(label) {
    // The dropdown is keyed by description; auto-fill the first procedure row
    // with the matching CPT + description so coordinators don't double-enter.
    const match = procedureOpts.find(p => p.description === label)
    setForm(f => ({
      ...f,
      surgery_name: label,
      procedures: match
        ? [{ cpt: match.cpt, description: match.description },
           ...f.procedures.slice(1)]
        : f.procedures,
    }))
  }

  const create = useMutation({
    mutationFn: () => api.post('/surgery/manual', {
      chart_number: form.chart_number,
      patient_name: form.patient_name,
      dob: form.dob || null,
      phone: form.phone || null,
      email: form.email || null,
      address_street: form.address_street.trim(),
      address_city:   form.address_city.trim(),
      address_state:  form.address_state.trim(),
      address_zip:    form.address_zip.trim(),
      primary_insurance: form.primary_insurance || null,
      primary_member_id: form.primary_member_id || null,
      secondary_insurance: form.secondary_insurance || null,
      secondary_member_id: form.secondary_member_id || null,
      surgeon_primary: form.surgeon_primary || null,
      surgery_name: form.surgery_name,
      preop_date: form.preop_date,
      procedures: (form.procedures || [])
        .map(p => ({ cpt: (p.cpt || '').trim() || null,
                       description: (p.description || '').trim() || null }))
        .filter(p => p.cpt || p.description),
      diagnoses: (form.diagnoses || [])
        .map(d => ({ icd: (d.icd || '').trim() || null,
                       description: (d.description || '').trim() || null }))
        .filter(d => d.icd || d.description),
      eligible_facilities: form.eligible_facilities,
      estimated_minutes: form.estimated_minutes ? Number(form.estimated_minutes) : null,
      is_robotic: form.is_robotic,
      is_urgent: form.is_urgent,
      notes: form.notes || null,
    }).then(r => r.data),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ['surgery-list'] })
      qc.invalidateQueries({ queryKey: ['surgery-dashboard'] })
      onClose()
      navigate(`/surgery/${data.id}`)
    },
    onError: (e) => {
      const d = e?.response?.data?.detail
      setError(typeof d === 'string' ? d : (e?.message || 'Create failed'))
    },
  })

  function toggleFacility(f) {
    const set = new Set(form.eligible_facilities)
    if (set.has(f)) set.delete(f)
    else set.add(f)
    setForm({ ...form, eligible_facilities: Array.from(set) })
  }

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/30" />
      <div className="relative w-full max-w-2xl bg-white shadow-xl overflow-y-auto"
           onClick={e => e.stopPropagation()}>
        <div className="sticky top-0 bg-white border-b border-border-subtle px-6 py-4 flex items-center justify-between z-10">
          <h2 className="font-serif font-semibold text-ink text-[18px]">+ New surgery (manual)</h2>
          <button onClick={onClose} className="text-muted hover:text-ink"><X size={18} /></button>
        </div>

        <div className="p-6 space-y-3">
          <p className="text-xs text-gray-600">
            Use this when you don't have a PDF order to upload — e.g. patient was scheduled
            directly in ModMed and never had an order generated. Surgery is created in
            <code> incomplete</code> status; review and click <strong>Mark as new</strong> on
            the detail page to spawn milestones.
          </p>

          <div className="grid grid-cols-2 gap-3">
            <Field label="Chart # *">
              <input className="input text-sm font-mono" value={form.chart_number}
                     onChange={e => setForm({ ...form, chart_number: e.target.value })} />
            </Field>
            <Field label="Patient name (Last, First) *">
              <input className="input text-sm" value={form.patient_name}
                     placeholder="Owens, Traci"
                     onChange={e => setForm({ ...form, patient_name: e.target.value })} />
            </Field>
            <Field label="DOB *">
              <input className="input text-sm font-mono" type="date" value={form.dob}
                     onChange={e => setForm({ ...form, dob: e.target.value })} />
            </Field>
            <Field label="Phone *">
              <input className="input text-sm" value={form.phone}
                     onChange={e => setForm({ ...form, phone: e.target.value })} />
            </Field>
            <Field label="Email *">
              <input className="input text-sm" value={form.email}
                     onChange={e => setForm({ ...form, email: e.target.value })} />
            </Field>
            <div className="col-span-2">
              <Field label="Street address *">
                <input className="input text-sm" value={form.address_street}
                       placeholder="123 Main St"
                       onChange={e => setForm({ ...form, address_street: e.target.value })} />
              </Field>
            </div>
            <Field label="City *">
              <input className="input text-sm" value={form.address_city}
                     onChange={e => setForm({ ...form, address_city: e.target.value })} />
            </Field>
            <div className="grid grid-cols-[1fr_1fr] gap-2">
              <Field label="State *">
                <input className="input text-sm" value={form.address_state}
                       maxLength={2} placeholder="MD"
                       onChange={e => setForm({ ...form, address_state: e.target.value.toUpperCase() })} />
              </Field>
              <Field label="ZIP *">
                <input className="input text-sm font-mono" value={form.address_zip}
                       placeholder="20601"
                       onChange={e => setForm({ ...form, address_zip: e.target.value })} />
              </Field>
            </div>
            <Field label="Surgeon *">
              <select className="input text-sm" value={form.surgeon_primary}
                       onChange={e => setForm({ ...form, surgeon_primary: e.target.value })}>
                <option value="">— select —</option>
                {surgeonOpts.map(n => (
                  <option key={n} value={n}>{n}</option>
                ))}
              </select>
            </Field>
            <Field label="Pre-op date *">
              <input className="input text-sm font-mono" type="date" value={form.preop_date}
                     onChange={e => setForm({ ...form, preop_date: e.target.value })} />
            </Field>
            <div className="col-span-2">
              <Field label="Surgery name *">
                <select className="input text-sm" value={form.surgery_name}
                         onChange={e => pickSurgery(e.target.value)}>
                  <option value="">— select a surgery —</option>
                  {procedureOpts.map(p => (
                    <option key={p.cpt} value={p.description}>
                      {p.description} ({p.cpt})
                    </option>
                  ))}
                </select>
              </Field>
            </div>
            <Field label="Primary insurance *">
              <select className="input text-sm" value={form.primary_insurance}
                       onChange={e => setForm({ ...form, primary_insurance: e.target.value })}>
                <option value="">— select —</option>
                {insuranceOpts.map(n => (
                  <option key={`p-${n}`} value={n}>{n}</option>
                ))}
              </select>
            </Field>
            <Field label="Primary member ID *">
              <input className="input text-sm font-mono" value={form.primary_member_id}
                     onChange={e => setForm({ ...form, primary_member_id: e.target.value })} />
            </Field>
            <Field label="Secondary insurance">
              <select className="input text-sm" value={form.secondary_insurance}
                       onChange={e => setForm({ ...form, secondary_insurance: e.target.value })}>
                <option value="">— none —</option>
                {insuranceOpts
                  .filter(n => n !== form.primary_insurance)
                  .map(n => (
                    <option key={`s-${n}`} value={n}>{n}</option>
                  ))}
              </select>
            </Field>
            <Field label="Secondary member ID">
              <input className="input text-sm font-mono" value={form.secondary_member_id}
                     onChange={e => setForm({ ...form, secondary_member_id: e.target.value })} />
            </Field>
            {/* Procedures (multi) */}
            <div className="col-span-2">
              <div className="flex items-baseline justify-between mb-1">
                <label className="text-[11px] uppercase text-gray-500">Procedure CPT codes</label>
                <button type="button"
                        className="text-[11px] text-plum-700 hover:underline"
                        onClick={() => setForm(f => ({
                          ...f, procedures: [...f.procedures, { cpt: '', description: '' }],
                        }))}>
                  + Add CPT
                </button>
              </div>
              <div className="space-y-1.5">
                {form.procedures.map((p, i) => (
                  <div key={i} className="grid grid-cols-[120px_1fr_24px] gap-2 items-center">
                    <input className="input text-sm font-mono"
                            value={p.cpt}
                            placeholder={i === 0 ? '58558' : 'CPT'}
                            onChange={e => setForm(f => ({
                              ...f,
                              procedures: f.procedures.map((row, j) =>
                                j === i ? { ...row, cpt: e.target.value } : row),
                            }))} />
                    <input className="input text-sm"
                            value={p.description}
                            placeholder={i === 0 ? 'Hysteroscopy D&C' : 'description'}
                            onChange={e => setForm(f => ({
                              ...f,
                              procedures: f.procedures.map((row, j) =>
                                j === i ? { ...row, description: e.target.value } : row),
                            }))} />
                    <button type="button"
                            className="text-gray-400 hover:text-danger"
                            title="Remove this CPT"
                            disabled={form.procedures.length === 1}
                            onClick={() => setForm(f => ({
                              ...f, procedures: f.procedures.filter((_, j) => j !== i),
                            }))}>
                      <X size={13}/>
                    </button>
                  </div>
                ))}
              </div>
            </div>

            {/* Diagnoses (multi) */}
            <div className="col-span-2">
              <div className="flex items-baseline justify-between mb-1">
                <label className="text-[11px] uppercase text-gray-500">Diagnosis ICD-10 codes</label>
                <button type="button"
                        className="text-[11px] text-plum-700 hover:underline"
                        onClick={() => setForm(f => ({
                          ...f, diagnoses: [...f.diagnoses, { icd: '', description: '' }],
                        }))}>
                  + Add ICD-10
                </button>
              </div>
              <div className="space-y-1.5">
                {form.diagnoses.map((d, i) => (
                  <div key={i} className="grid grid-cols-[120px_1fr_24px] gap-2 items-center">
                    <input className="input text-sm font-mono"
                            value={d.icd}
                            placeholder={i === 0 ? 'N92.0' : 'ICD-10'}
                            onChange={e => setForm(f => ({
                              ...f,
                              diagnoses: f.diagnoses.map((row, j) =>
                                j === i ? { ...row, icd: e.target.value } : row),
                            }))} />
                    <input className="input text-sm"
                            value={d.description}
                            placeholder={i === 0 ? 'Heavy menstrual bleeding' : 'description'}
                            onChange={e => setForm(f => ({
                              ...f,
                              diagnoses: f.diagnoses.map((row, j) =>
                                j === i ? { ...row, description: e.target.value } : row),
                            }))} />
                    <button type="button"
                            className="text-gray-400 hover:text-danger"
                            title="Remove this ICD-10"
                            disabled={form.diagnoses.length === 1}
                            onClick={() => setForm(f => ({
                              ...f, diagnoses: f.diagnoses.filter((_, j) => j !== i),
                            }))}>
                      <X size={13}/>
                    </button>
                  </div>
                ))}
              </div>
            </div>
            <Field label="Estimated minutes">
              <input className="input text-sm font-mono" type="number" value={form.estimated_minutes}
                     onChange={e => setForm({ ...form, estimated_minutes: e.target.value })} />
            </Field>
            <Field label="Eligible facilities">
              <div className="flex gap-1.5">
                {['medstar', 'crmc', 'office'].map(f => (
                  <button key={f} type="button"
                          onClick={() => toggleFacility(f)}
                          className={`text-xs px-2 py-1 rounded border ${
                            form.eligible_facilities.includes(f)
                              ? 'bg-plum-100 border-plum-300 text-plum-700 font-semibold'
                              : 'bg-white border-border-subtle text-muted'
                          }`}>
                    {labelOf(f)}
                  </button>
                ))}
              </div>
            </Field>
          </div>

          <div className="flex gap-4 text-sm">
            <label className="flex items-center gap-1.5">
              <input type="checkbox" checked={form.is_robotic}
                     onChange={e => setForm({ ...form, is_robotic: e.target.checked })} />
              Robotic case (auto-routes to MedStar)
            </label>
            <label className="flex items-center gap-1.5">
              <input type="checkbox" checked={form.is_urgent}
                     onChange={e => setForm({ ...form, is_urgent: e.target.checked })} />
              🚨 Urgent
            </label>
          </div>

          <Field label="Notes">
            <textarea className="input text-sm" rows={2} value={form.notes}
                      onChange={e => setForm({ ...form, notes: e.target.value })} />
          </Field>

          {create.isError && (
            <div className="text-xs text-red-600">
              {create.error?.response?.data?.detail || create.error.message}
            </div>
          )}

          {requiredMissing && (
            <div className="text-xs text-amber-700">
              All starred fields are required (Secondary insurance and Notes are optional).
            </div>
          )}
          {error && !create.isError && (
            <div className="text-xs text-red-600">{error}</div>
          )}

          <div className="flex justify-end gap-2 pt-2">
            <button className="btn-secondary text-sm" onClick={onClose}>Cancel</button>
            <button className="btn-primary text-sm"
                    onClick={() => create.mutate()}
                    disabled={create.isPending || requiredMissing}>
              {create.isPending ? 'Creating…' : 'Create surgery'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}


function Field({ label, children }) {
  return (
    <div>
      <div className="text-[11px] uppercase text-gray-500 tracking-wide mb-1">{label}</div>
      {children}
    </div>
  )
}
