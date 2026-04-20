import { useEffect, useMemo, useState } from 'react'
import MoneyInput from './MoneyInput'
import AdjustmentList from './AdjustmentList'
import { useServiceLineEdit } from '../hooks/useServiceLineEdit'

const EDITABLE_SL_FIELDS = [
  'procedure_code', 'modifier_1', 'modifier_2', 'modifier_3', 'modifier_4',
  'revenue_code', 'description', 'units',
  'date_of_service_from', 'date_of_service_to',
  'billed_amount', 'allowed_amount', 'paid_amount',
  'patient_responsibility', 'contractual_adjustment', 'other_adjustment',
  'diagnosis_codes',
]


export default function EditServiceLineDrawer({ claimId, line, onClose }) {
  // line === null → add mode; otherwise edit mode
  const isAdd = line == null
  const initialFields = useMemo(() => {
    const o = {}
    for (const k of EDITABLE_SL_FIELDS) o[k] = line?.[k] ?? (k === 'diagnosis_codes' ? [] : null)
    return o
  }, [line])

  const [fields, setFields] = useState(initialFields)
  const [adjustments, setAdjustments] = useState(
    (line?.adjustments || []).map(a => ({ ...a, op: 'none' }))
  )
  const [dxInput, setDxInput] = useState((initialFields.diagnosis_codes || []).join(', '))
  const [confirmDelete, setConfirmDelete] = useState(false)
  const { save, del, saving, error, step, reset } = useServiceLineEdit()

  useEffect(() => { document.body.style.overflow = 'hidden'; return () => { document.body.style.overflow = '' } }, [])

  function set(k, v) { setFields(prev => ({ ...prev, [k]: v })) }

  function diffFields() {
    const out = {}
    for (const k of EDITABLE_SL_FIELDS) {
      const cur = fields[k]
      const orig = initialFields[k]
      if (k === 'diagnosis_codes') {
        if (JSON.stringify(cur || []) !== JSON.stringify(orig || [])) out[k] = cur || []
      } else if ((cur ?? null) !== (orig ?? null)) {
        out[k] = cur
      }
    }
    return out
  }

  function commitDxInput() {
    const codes = dxInput.split(',').map(s => s.trim()).filter(Boolean)
    set('diagnosis_codes', codes)
  }

  async function onSave() {
    commitDxInput()
    // Re-read fields with dx committed (setState is async — read via diffFields next tick)
    // Safer: compute the outgoing body inline
    const codes = dxInput.split(',').map(s => s.trim()).filter(Boolean)
    const outFields = { ...fields, diagnosis_codes: codes }
    const diffWithDx = { ...diffFields() }
    if (JSON.stringify(codes) !== JSON.stringify(initialFields.diagnosis_codes || [])) {
      diffWithDx.diagnosis_codes = codes
    }

    const result = await save({
      claimId,
      lineId: isAdd ? null : line.id,
      fields: isAdd ? _sanitizeForPost(outFields) : undefined,
      fieldsDiff: isAdd ? undefined : diffWithDx,
      adjustments,
    })
    if (result.ok) onClose()
  }

  async function onDelete() {
    const result = await del({ claimId, lineId: line.id })
    if (result.ok) onClose()
  }

  return (
    <div className="fixed inset-0 z-40 flex justify-end">
      <div className="absolute inset-0 bg-ink/20" onClick={saving ? undefined : onClose} />
      <aside className="relative w-[520px] max-w-full bg-white h-full shadow-xl overflow-y-auto flex flex-col">
        <header className="px-5 py-4 border-b flex items-center justify-between sticky top-0 bg-white">
          <h2 className="font-serif font-semibold text-ink text-[18px]">
            {isAdd ? 'Add service line' : `Edit line ${line.procedure_code || ''}`}
          </h2>
          <button className="text-muted text-[13px]" onClick={onClose} disabled={saving}>✕ Close</button>
        </header>

        <div className="flex-1 px-5 py-4 space-y-5 text-[12px]">
          <Section title="Code">
            <Field label="Procedure code"><Text value={fields.procedure_code} onChange={v => set('procedure_code', v)} /></Field>
            <Field label="Revenue code"><Text value={fields.revenue_code} onChange={v => set('revenue_code', v)} /></Field>
            <Field label="Description"><Text value={fields.description} onChange={v => set('description', v)} /></Field>
          </Section>

          <Section title="Modifiers">
            <div className="grid grid-cols-4 gap-2">
              {['modifier_1','modifier_2','modifier_3','modifier_4'].map(k => (
                <input key={k} className="input py-1 text-[12px] font-mono"
                       placeholder={k.replace('modifier_','M')}
                       value={fields[k] || ''}
                       onChange={e => set(k, e.target.value)} />
              ))}
            </div>
          </Section>

          <Section title="Dates">
            <Field label="DOS from"><Date value={fields.date_of_service_from} onChange={v => set('date_of_service_from', v)} /></Field>
            <Field label="DOS to"><Date value={fields.date_of_service_to} onChange={v => set('date_of_service_to', v)} /></Field>
          </Section>

          <Section title="Quantity">
            <Field label="Units">
              <input type="number" step="0.01" className="input w-28 py-1 text-[12px]"
                     value={fields.units ?? ''}
                     onChange={e => set('units', e.target.value)} />
            </Field>
          </Section>

          <Section title="Diagnosis codes">
            <input className="input w-full py-1 text-[12px] font-mono"
                   placeholder="Z00.00, E11.9, ..."
                   value={dxInput}
                   onChange={e => setDxInput(e.target.value)}
                   onBlur={commitDxInput} />
            <div className="text-[11px] text-muted mt-1">Comma-separated ICD-10 codes.</div>
          </Section>

          <Section title="Money">
            <Field label="Billed"><MoneyInput value={fields.billed_amount} onChange={v => set('billed_amount', v)} /></Field>
            <Field label="Allowed"><MoneyInput value={fields.allowed_amount} onChange={v => set('allowed_amount', v)} /></Field>
            <Field label="Paid"><MoneyInput value={fields.paid_amount} onChange={v => set('paid_amount', v)} /></Field>
            <Field label="Patient resp"><MoneyInput value={fields.patient_responsibility} onChange={v => set('patient_responsibility', v)} /></Field>
            <Field label="Contractual adj"><MoneyInput value={fields.contractual_adjustment} onChange={v => set('contractual_adjustment', v)} /></Field>
            <Field label="Other adj"><MoneyInput value={fields.other_adjustment} onChange={v => set('other_adjustment', v)} /></Field>
          </Section>

          <Section title="Line adjustments">
            <AdjustmentList value={adjustments} onChange={setAdjustments} disabled={saving} />
          </Section>

          {error && (
            <div className="card bg-red-50 border border-red-200 p-3 text-[12px] text-danger">
              <div className="font-semibold">Save failed</div>
              <div>{error.message}</div>
              <div className="mt-2 flex gap-2">
                <button className="btn-secondary py-1 px-2 text-[11px]"
                        onClick={() => { reset(); onSave() }}>Retry</button>
                <button className="text-[11px] underline" onClick={reset}>Dismiss</button>
              </div>
            </div>
          )}
        </div>

        <footer className="px-5 py-3 border-t flex justify-between items-center sticky bottom-0 bg-white">
          <div>
            {!isAdd && (
              confirmDelete ? (
                <div className="flex items-center gap-2 text-[11px]">
                  <span className="text-danger">Delete this line?</span>
                  <button className="btn-secondary py-1 px-2 text-[11px] text-danger"
                          onClick={onDelete} disabled={saving}>Yes, delete</button>
                  <button className="text-[11px] underline"
                          onClick={() => setConfirmDelete(false)} disabled={saving}>cancel</button>
                </div>
              ) : (
                <button className="text-[11px] text-danger underline"
                        onClick={() => setConfirmDelete(true)} disabled={saving}>Delete line</button>
              )
            )}
          </div>
          <div className="flex gap-2">
            <button className="btn-secondary text-[12px]" onClick={onClose} disabled={saving}>Cancel</button>
            <button className="btn-primary text-[12px]" onClick={onSave} disabled={saving}>
              {saving ? (step ? `Saving ${step}…` : 'Saving…') : 'Save'}
            </button>
          </div>
        </footer>
      </aside>
    </div>
  )
}

function _sanitizeForPost(fields) {
  // Drop null/empty fields from the POST body to avoid sending "" to numeric columns
  const out = {}
  for (const [k, v] of Object.entries(fields)) {
    if (v === null || v === undefined || v === '') continue
    if (Array.isArray(v) && v.length === 0) continue
    out[k] = v
  }
  return out
}

function Section({ title, children }) {
  return (
    <div>
      <h3 className="text-[11px] uppercase tracking-wide text-muted mb-1">{title}</h3>
      <div className="space-y-2">{children}</div>
    </div>
  )
}
function Field({ label, children }) {
  return (
    <label className="block">
      <div className="text-[11px] text-muted mb-0.5">{label}</div>
      {children}
    </label>
  )
}
function Text({ value, onChange }) {
  return (
    <input className="input w-full py-1 text-[12px]" value={value ?? ''}
           onChange={(e) => onChange(e.target.value)} />
  )
}
function Date({ value, onChange }) {
  return (
    <input type="date" className="input w-full py-1 text-[12px]" value={value ?? ''}
           onChange={(e) => onChange(e.target.value || null)} />
  )
}
