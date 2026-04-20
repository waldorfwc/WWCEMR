import { useEffect, useMemo, useState } from 'react'
import MoneyInput from './MoneyInput'
import PatientPicker from './PatientPicker'
import AdjustmentList from './AdjustmentList'
import { useClaimEdit } from '../hooks/useClaimEdit'

const CLAIM_STATUSES = [
  'pending', 'paid', 'partial', 'denied', 'adjusted', 'reversed', 'appealed', 'written_off'
]
const INSURANCE_ORDERS = ['primary', 'secondary', 'tertiary', 'patient']

const EDITABLE_CLAIM_FIELDS = [
  'claim_number', 'payer_claim_number', 'payer_name', 'payer_id',
  'subscriber_id', 'group_number', 'insurance_order',
  'date_of_service_from', 'date_of_service_to',
  'check_number', 'check_date',
  'rendering_provider_name', 'rendering_provider_npi',
  'patient_id',
  'status', 'notes',
  'billed_amount', 'allowed_amount', 'paid_amount',
  'patient_responsibility', 'contractual_adjustment', 'other_adjustment',
]


export default function EditClaimDrawer({ claim, onClose }) {
  const initialFields = useMemo(() => {
    const o = {}
    for (const k of EDITABLE_CLAIM_FIELDS) o[k] = claim[k] ?? null
    return o
  }, [claim])

  const [fields, setFields] = useState(initialFields)
  const [adjustments, setAdjustments] = useState(
    (claim.adjustments || []).map(a => ({ ...a, op: 'none' }))
  )
  const { save, saving, error, step, reset } = useClaimEdit()

  useEffect(() => { document.body.style.overflow = 'hidden'; return () => { document.body.style.overflow = '' } }, [])

  function set(k, v) {
    setFields(prev => ({ ...prev, [k]: v }))
  }

  const computedBalance = useMemo(() => {
    const n = (v) => parseFloat(v || 0) || 0
    return n(fields.billed_amount) - n(fields.contractual_adjustment) - n(fields.other_adjustment)
         - n(fields.paid_amount) - n(fields.patient_responsibility)
  }, [fields])

  function diffFields() {
    const out = {}
    for (const k of EDITABLE_CLAIM_FIELDS) {
      if ((fields[k] ?? null) !== (initialFields[k] ?? null)) out[k] = fields[k]
    }
    return out
  }

  async function onSave() {
    const result = await save({
      claimId: claim.id,
      fieldsDiff: diffFields(),
      adjustments,
    })
    if (result.ok) onClose()
  }

  return (
    <div className="fixed inset-0 z-40 flex justify-end">
      <div className="absolute inset-0 bg-ink/20" onClick={saving ? undefined : onClose} />
      <aside className="relative w-[520px] max-w-full bg-white h-full shadow-xl overflow-y-auto flex flex-col">
        <header className="px-5 py-4 border-b flex items-center justify-between sticky top-0 bg-white">
          <h2 className="font-serif font-semibold text-ink text-[18px]">Edit claim {claim.claim_number || ''}</h2>
          <button className="text-muted text-[13px]" onClick={onClose} disabled={saving}>✕ Close</button>
        </header>

        <div className="flex-1 px-5 py-4 space-y-5 text-[12px]">
          <Section title="Identifiers">
            <Field label="Claim #"><Text value={fields.claim_number} onChange={v => set('claim_number', v)} /></Field>
            <Field label="Payer claim #"><Text value={fields.payer_claim_number} onChange={v => set('payer_claim_number', v)} /></Field>
          </Section>

          <Section title="Routing">
            <Field label="Payer name"><Text value={fields.payer_name} onChange={v => set('payer_name', v)} /></Field>
            <Field label="Payer ID"><Text value={fields.payer_id} onChange={v => set('payer_id', v)} /></Field>
            <Field label="Subscriber ID"><Text value={fields.subscriber_id} onChange={v => set('subscriber_id', v)} /></Field>
            <Field label="Group #"><Text value={fields.group_number} onChange={v => set('group_number', v)} /></Field>
            <Field label="Insurance order">
              <select className="input w-full py-1 text-[12px]"
                      value={fields.insurance_order || 'primary'}
                      onChange={(e) => set('insurance_order', e.target.value)}>
                {INSURANCE_ORDERS.map(o => <option key={o} value={o}>{o}</option>)}
              </select>
            </Field>
          </Section>

          <Section title="Dates">
            <Field label="DOS from"><Date value={fields.date_of_service_from} onChange={v => set('date_of_service_from', v)} /></Field>
            <Field label="DOS to"><Date value={fields.date_of_service_to} onChange={v => set('date_of_service_to', v)} /></Field>
            <Field label="Check #"><Text value={fields.check_number} onChange={v => set('check_number', v)} /></Field>
            <Field label="Check date"><Date value={fields.check_date} onChange={v => set('check_date', v)} /></Field>
          </Section>

          <Section title="Provider">
            <Field label="Rendering name"><Text value={fields.rendering_provider_name} onChange={v => set('rendering_provider_name', v)} /></Field>
            <Field label="Rendering NPI"><Text value={fields.rendering_provider_npi} onChange={v => set('rendering_provider_npi', v)} /></Field>
          </Section>

          <Section title="Patient">
            <PatientPicker value={fields.patient_id} onChange={(v) => set('patient_id', v)} />
          </Section>

          <Section title="Status & Notes">
            <Field label="Status">
              <select className="input w-full py-1 text-[12px]"
                      value={fields.status || 'pending'}
                      onChange={(e) => set('status', e.target.value)}>
                {CLAIM_STATUSES.map(s => <option key={s} value={s}>{s}</option>)}
              </select>
            </Field>
            <Field label="Notes">
              <textarea
                className="input w-full py-1 text-[12px]"
                rows={3}
                value={fields.notes || ''}
                onChange={(e) => set('notes', e.target.value)}
              />
            </Field>
          </Section>

          <Section title="Money">
            <Field label="Billed"><MoneyInput value={fields.billed_amount} onChange={v => set('billed_amount', v)} /></Field>
            <Field label="Allowed"><MoneyInput value={fields.allowed_amount} onChange={v => set('allowed_amount', v)} /></Field>
            <Field label="Paid"><MoneyInput value={fields.paid_amount} onChange={v => set('paid_amount', v)} /></Field>
            <Field label="Patient resp"><MoneyInput value={fields.patient_responsibility} onChange={v => set('patient_responsibility', v)} /></Field>
            <Field label="Contractual adj"><MoneyInput value={fields.contractual_adjustment} onChange={v => set('contractual_adjustment', v)} /></Field>
            <Field label="Other adj"><MoneyInput value={fields.other_adjustment} onChange={v => set('other_adjustment', v)} /></Field>
            <div className="flex items-center justify-between pt-1">
              <span className="text-muted">Balance (computed) 🔒</span>
              <span className="font-mono">${computedBalance.toFixed(2)}</span>
            </div>
          </Section>

          <Section title="Claim adjustments">
            <AdjustmentList value={adjustments} onChange={setAdjustments} disabled={saving} />
          </Section>

          {error && (
            <div className="card bg-red-50 border border-red-200 p-3 text-[12px] text-danger">
              <div className="font-semibold">Save failed at step {error.completed + 1} of {error.total}</div>
              <div>{error.message}</div>
              <div className="mt-1">{error.completed} of {error.total} changes applied.</div>
              <div className="mt-2 flex gap-2">
                <button className="btn-secondary py-1 px-2 text-[11px]"
                        onClick={() => { reset(); onSave() }}>Retry</button>
                <button className="text-[11px] underline" onClick={reset}>Dismiss</button>
              </div>
            </div>
          )}
        </div>

        <footer className="px-5 py-3 border-t flex justify-end gap-2 sticky bottom-0 bg-white">
          <button className="btn-secondary text-[12px]" onClick={onClose} disabled={saving}>Cancel</button>
          <button className="btn-primary text-[12px]" onClick={onSave} disabled={saving}>
            {saving ? (step ? `Saving ${step}…` : 'Saving…') : 'Save'}
          </button>
        </footer>
      </aside>
    </div>
  )
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
