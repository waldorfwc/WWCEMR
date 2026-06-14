import { useState } from 'react'
import { useQuery, useMutation } from '@tanstack/react-query'
import { X, FileText } from 'lucide-react'
import api from '../../utils/api'
import { useFacilities } from '../../hooks/useFacilities'


// Default shape for a brand-new (create-mode) form.
const EMPTY_FORM = {
  chart_number: '',
  first_name: '',
  last_name: '',
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
  surgeon_primary: 'Aryian Cooke, MD',
  assistant_surgeon_name: 'None',
  clearance_types: ['None'],
  device_types: ['None'],
  surgery_name: '',
  procedures: [{ cpt: '', description: '' }],
  diagnoses:  [{ icd: '', description: '' }],
  eligible_facilities: ['medstar'],
  estimated_minutes: 180,
  preop_date: '',
  is_robotic: false,
  is_urgent: false,
  notes: '',
}


/**
 * Shared surgery intake form, used by both the create drawer (ManualCreateDrawer)
 * and the Update Surgery drawer. Owns the full form state, picklists/config
 * fetch, the optional order-PDF extract-prefill, the toggle-chip helpers, the
 * field JSX, and the required-field validation.
 *
 * Props:
 *   - mode: 'create' | 'update'
 *   - initialValues: partial form values to seed state (merged over EMPTY_FORM)
 *   - onSubmit: ({ fields, orderFile }) => void — called with the full edited
 *       field set (incl. composed patient_name) and the uploaded order PDF (or null)
 *   - submitLabel: string for the submit button
 *   - submitting: boolean, disables the button while the parent mutation runs
 *   - error: string | null, surfaced inline above the buttons
 *   - onCancel: () => void
 */
export default function SurgeryIntakeForm({
  mode = 'create',
  initialValues,
  onSubmit,
  submitLabel,
  submitting = false,
  error = null,
  onCancel,
}) {
  const { labelOf } = useFacilities()
  // Picklists drive insurance/surgeon dropdowns
  const { data: picks } = useQuery({
    queryKey: ['surgery-picklists'],
    queryFn: () => api.get('/surgery/picklists').then(r => r.data),
    staleTime: 300_000,
  })
  // Config drives the assistant-surgeon / clearance / device option lists
  const { data: config } = useQuery({
    queryKey: ['surgery-config'],
    queryFn: () => api.get('/surgery/config').then(r => r.data),
    staleTime: 300_000,
  })
  const insuranceOpts = picks?.insurance_companies || []
  const surgeonOpts   = picks?.surgeons || []
  const procedureOpts = picks?.procedures || []
  const assistantOpts = config?.assistant_surgeons || ['None']
  const clearanceOpts = config?.clearance_types || ['None']
  const deviceOpts    = config?.surgery_device_types || ['None']

  const [form, setForm] = useState(() => ({ ...EMPTY_FORM, ...(initialValues || {}) }))
  // Order upload / extract state
  const [orderFile, setOrderFile] = useState(null)
  const [warnings, setWarnings] = useState([])
  const [extractError, setExtractError] = useState(null)

  const requiredMissing =
    !form.chart_number.trim() || !form.first_name.trim() || !form.last_name.trim()
    || !form.dob || !form.phone.trim() || !form.email.trim()
    || !form.address_street.trim() || !form.address_city.trim()
    || !form.address_state.trim() || !form.address_zip.trim()
    || !form.primary_insurance || !form.primary_member_id.trim()
    || !form.surgeon_primary || !form.surgery_name
    || !form.assistant_surgeon_name
    || !form.clearance_types.length
    || !form.device_types.length
    || !form.preop_date
    || !form.estimated_minutes
    || !form.eligible_facilities.length
    || !form.procedures.some(p => (p.cpt || '').trim() || (p.description || '').trim())
    || !form.diagnoses.some(d => (d.icd || '').trim() || (d.description || '').trim())

  // Optional: upload a PDF order and prefill the form from extracted fields.
  const extract = useMutation({
    mutationFn: (file) => {
      const fd = new FormData()
      fd.append('file', file)
      return api.post('/surgery/orders/extract', fd, {
        headers: { 'Content-Type': 'multipart/form-data' },
      }).then(r => r.data)
    },
    onSuccess: (data) => {
      setExtractError(null)
      setWarnings(Array.isArray(data?.warnings) ? data.warnings : [])
      const f = data?.fields || {}
      setForm(prev => {
        const next = { ...prev }
        // Merge only keys the extractor actually returned.
        for (const k of [
          'chart_number', 'dob', 'primary_insurance', 'primary_member_id',
          'surgeon_primary', 'estimated_minutes', 'is_robotic', 'is_urgent',
        ]) {
          if (f[k] !== undefined && f[k] !== null) next[k] = f[k]
        }
        // Name: prefer split fields; otherwise split "Last, First" patient_name.
        if (f.first_name) next.first_name = f.first_name
        if (f.last_name)  next.last_name  = f.last_name
        if (!f.first_name && !f.last_name && f.patient_name) {
          const pn = String(f.patient_name)
          if (pn.includes(',')) {
            const [last, first] = pn.split(',')
            next.last_name = (last || '').trim()
            next.first_name = (first || '').trim()
          } else {
            const parts = pn.trim().split(/\s+/)
            next.first_name = parts.shift() || ''
            next.last_name = parts.join(' ')
          }
        }
        if (Array.isArray(f.procedures) && f.procedures.length) {
          next.procedures = f.procedures.map(p => ({
            cpt: p.cpt || '', description: p.description || '',
          }))
          next.surgery_name = f.procedures[0]?.description || next.surgery_name
        }
        if (Array.isArray(f.diagnoses) && f.diagnoses.length) {
          next.diagnoses = f.diagnoses.map(d => ({
            icd: d.icd || '', description: d.description || '',
          }))
        }
        if (Array.isArray(f.eligible_facilities) && f.eligible_facilities.length) {
          next.eligible_facilities = f.eligible_facilities
        }
        return next
      })
    },
    onError: (e) => {
      const d = e?.response?.data?.detail
      setExtractError(typeof d === 'string' ? d : (e?.message || 'Could not read that PDF.'))
      setWarnings([])
    },
  })

  function onPickOrder(file) {
    setOrderFile(file)
    setExtractError(null)
    setWarnings([])
    if (file) extract.mutate(file)
  }

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

  function toggleArray(key, value) {
    setForm(f => {
      const set = new Set(f[key])
      if (set.has(value)) set.delete(value)
      else set.add(value)
      return { ...f, [key]: Array.from(set) }
    })
  }

  function toggleFacility(f) {
    const set = new Set(form.eligible_facilities)
    if (set.has(f)) set.delete(f)
    else set.add(f)
    setForm({ ...form, eligible_facilities: Array.from(set) })
  }

  // Build the full edited field set the parent will POST/PATCH.
  function buildFields() {
    return {
      chart_number: form.chart_number,
      first_name: form.first_name.trim(),
      last_name: form.last_name.trim(),
      patient_name: `${form.last_name.trim()}, ${form.first_name.trim()}`,
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
      assistant_surgeon_name: form.assistant_surgeon_name,
      clearance_types: form.clearance_types,
      device_types: form.device_types,
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
    }
  }

  function handleSubmit() {
    if (requiredMissing) return
    onSubmit({ fields: buildFields(), orderFile })
  }

  return (
    <div className="p-6 space-y-3">
      {mode === 'create' && (
        <p className="text-xs text-gray-600">
          Use this when you don't have a PDF order to upload — e.g. patient was scheduled
          directly in ModMed and never had an order generated. Surgery is created in
          <code> incomplete</code> status; review and click <strong>Mark as new</strong> on
          the detail page to spawn milestones.
        </p>
      )}

      {/* Optional: upload a surgery order PDF to prefill the form. */}
      <div className="card !p-3 space-y-2 bg-plum-50/40 border-plum-200">
        <label className="flex items-center gap-2 text-sm font-medium">
          <FileText size={14} className="text-plum-700" />
          <span>Order PDF (optional) — prefill from order</span>
        </label>
        <p className="text-[11px] text-muted">
          {mode === 'update'
            ? 'Pick a ModMed surgery order to re-extract and overwrite the fields below. The PDF is attached to the surgery on save.'
            : 'Pick a ModMed surgery order to auto-fill the fields below. You can still enter everything manually. The PDF is attached to the surgery on save.'}
        </p>
        <input
          type="file" accept=".pdf"
          className="text-xs"
          onChange={e => onPickOrder(e.target.files?.[0] || null)}
        />
        {extract.isPending && (
          <div className="text-[11px] text-plum-700">Extracting from order…</div>
        )}
        {orderFile && !extract.isPending && (
          <button type="button"
                  className="text-[11px] text-plum-700 hover:underline"
                  onClick={() => extract.mutate(orderFile)}>
            Re-run extract from “{orderFile.name}”
          </button>
        )}
        {extractError && (
          <div className="text-[11px] text-red-700">✗ {extractError}</div>
        )}
        {warnings.length > 0 && (
          <ul className="text-[11px] text-amber-800 list-disc pl-4 space-y-0.5">
            {warnings.map((w, i) => <li key={i}>{w}</li>)}
          </ul>
        )}
      </div>

      <div className="grid grid-cols-2 gap-3">
        <Field label="Chart # *">
          <input className="input text-sm font-mono" value={form.chart_number}
                 onChange={e => setForm({ ...form, chart_number: e.target.value })} />
        </Field>
        <div />
        <Field label="First Name *">
          <input className="input text-sm" value={form.first_name}
                 placeholder="Traci"
                 onChange={e => setForm({ ...form, first_name: e.target.value })} />
        </Field>
        <Field label="Last Name *">
          <input className="input text-sm" value={form.last_name}
                 placeholder="Owens"
                 onChange={e => setForm({ ...form, last_name: e.target.value })} />
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
        <Field label="Assistant Surgeon *">
          <select className="input text-sm" value={form.assistant_surgeon_name}
                   onChange={e => setForm({ ...form, assistant_surgeon_name: e.target.value })}>
            {assistantOpts.map(n => (
              <option key={n} value={n}>{n}</option>
            ))}
          </select>
        </Field>
        <div className="col-span-2">
          <Field label="Clearance Type * (select all that apply)">
            <div className="flex flex-wrap gap-1.5">
              {clearanceOpts.map(c => (
                <button key={c} type="button"
                        onClick={() => toggleArray('clearance_types', c)}
                        className={`text-xs px-2 py-1 rounded border ${
                          form.clearance_types.includes(c)
                            ? 'bg-plum-100 border-plum-300 text-plum-700 font-semibold'
                            : 'bg-white border-border-subtle text-muted'
                        }`}>
                  {c}
                </button>
              ))}
            </div>
          </Field>
        </div>
        <div className="col-span-2">
          <Field label="Device Required * (select all that apply)">
            <div className="flex flex-wrap gap-1.5">
              {deviceOpts.map(d => (
                <button key={d} type="button"
                        onClick={() => toggleArray('device_types', d)}
                        className={`text-xs px-2 py-1 rounded border ${
                          form.device_types.includes(d)
                            ? 'bg-plum-100 border-plum-300 text-plum-700 font-semibold'
                            : 'bg-white border-border-subtle text-muted'
                        }`}>
                  {d}
                </button>
              ))}
            </div>
          </Field>
        </div>
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
        <Field label="Estimated minutes *">
          <input className="input text-sm font-mono" type="number" value={form.estimated_minutes}
                 onChange={e => setForm({ ...form, estimated_minutes: e.target.value })} />
        </Field>
        <Field label="Eligible facilities *">
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

      {requiredMissing && (
        <div className="text-xs text-amber-700">
          All starred fields are required, including Assistant Surgeon and at
          least one Clearance Type and Device (use “None” if not applicable).
          Secondary insurance, Notes, and the order PDF are optional.
        </div>
      )}
      {error && (
        <div className="text-xs text-red-600">{error}</div>
      )}

      <div className="flex justify-end gap-2 pt-2">
        <button className="btn-secondary text-sm" onClick={onCancel}>Cancel</button>
        <button className="btn-primary text-sm"
                onClick={handleSubmit}
                disabled={submitting || requiredMissing}>
          {submitting ? 'Saving…' : (submitLabel || 'Save')}
        </button>
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
