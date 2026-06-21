import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { X } from 'lucide-react'
import api from '../../utils/api'


export default function StartLarcProcessDrawer({ onClose, onCreated }) {
  const qc = useQueryClient()
  const [step, setStep] = useState(1)            // 1 = intake, 2 = suggestion
  const [suggestion, setSuggestion] = useState(null)
  const [chosenFlow, setChosenFlow] = useState(null)
  const [showErrors, setShowErrors] = useState(false)
  const [form, setForm] = useState({
    chart_number: '', patient_first_name: '', patient_last_name: '',
    patient_dob: '', patient_email: '', patient_cell: '',
    device_type_id: '', requested_by_email: '',
    reason_for_request: '', reason_icd10: '',
  })
  const update = (k, v) => setForm(f => ({ ...f, [k]: v }))

  const { data: types } = useQuery({
    queryKey: ['larc-device-types'],
    queryFn: () => api.get('/larc/device-types').then(r => r.data),
    staleTime: 60_000,
  })
  const { data: clinicians } = useQuery({
    queryKey: ['clinicians'],
    queryFn: () => api.get('/admin/users/clinicians').then(r => r.data),
    staleTime: 60_000,
  })
  const { data: config } = useQuery({
    queryKey: ['larc-config'],
    queryFn: () => api.get('/larc/config').then(r => r.data),
    staleTime: 60_000,
  })
  const reasons = config?.reason_for_request_options || []

  const allFilled = form.chart_number.trim() && form.patient_first_name.trim()
    && form.patient_last_name.trim() && form.patient_dob && form.patient_email.trim()
    && form.patient_cell.trim() && form.device_type_id && form.requested_by_email
    && form.reason_for_request

  const missing = {
    chart_number: !form.chart_number.trim(),
    patient_dob: !form.patient_dob,
    patient_first_name: !form.patient_first_name.trim(),
    patient_last_name: !form.patient_last_name.trim(),
    patient_email: !form.patient_email.trim(),
    patient_cell: !form.patient_cell.trim(),
    device_type_id: !form.device_type_id,
    requested_by_email: !form.requested_by_email,
    reason_for_request: !form.reason_for_request,
  }
  const errCls = (k) => (showErrors && missing[k]) ? ' border-red-400 bg-red-50' : ''
  const handleContinue = () => {
    if (!allFilled) { setShowErrors(true); return }
    suggest.mutate()
  }

  const suggest = useMutation({
    mutationFn: () => api.post('/larc/assignments/suggest-flow',
      { device_type_id: form.device_type_id }).then(r => r.data),
    onSuccess: (data) => { setSuggestion(data); setChosenFlow(data.suggested_flow); setStep(2) },
    onError: (e) => alert(e?.response?.data?.detail || 'Could not compute a suggestion'),
  })

  const create = useMutation({
    mutationFn: () => {
      const prov = (clinicians || []).find(c => c.email === form.requested_by_email)
      return api.post('/larc/assignments', {
        chart_number: form.chart_number.trim(),
        patient_name: `${form.patient_last_name.trim()}, ${form.patient_first_name.trim()}`,
        patient_first_name: form.patient_first_name.trim(),
        patient_last_name: form.patient_last_name.trim(),
        patient_dob: form.patient_dob,
        patient_email: form.patient_email.trim(),
        patient_cell: form.patient_cell.trim(),
        device_type_id: form.device_type_id,
        source_flow: chosenFlow,
        reason_for_request: form.reason_for_request,
        reason_icd10: form.reason_icd10,
        requested_by_provider: prov?.display_name || null,
        inserting_provider_email: prov?.email || null,
        inserting_provider_name: prov?.display_name || null,
        inserting_provider_npi: prov?.npi || null,
      }).then(r => r.data)
    },
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ['larc-dashboard'] })
      qc.invalidateQueries({ queryKey: ['larc-assignments'] })
      onCreated(data.id)
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Create failed'),
  })

  const FLOW_LABEL = {
    in_stock: 'Use an in-stock device',
    pharmacy_order: 'Pharmacy enrollment form',
    office_procedure: 'In-office procedure device',
  }

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/30" />
      <div className="relative w-full max-w-lg bg-white shadow-xl overflow-y-auto"
           onClick={e => e.stopPropagation()}>
        <div className="sticky top-0 bg-white border-b px-4 py-3 flex items-center justify-between">
          <h2 className="font-semibold text-plum-700">Start LARC Process</h2>
          <button onClick={onClose}><X size={18} /></button>
        </div>

        {step === 1 && (
          <div className="p-4 grid grid-cols-6 gap-2 text-sm">
            {showErrors && !allFilled && (
              <div className="col-span-6 rounded border border-red-300 bg-red-50 text-red-700 px-3 py-2 text-[12px]">
                Please complete the highlighted fields — every field is required to continue.
              </div>
            )}
            <label className="col-span-3">MRN <span className="text-red-500">*</span>
              <input className={"input w-full" + errCls('chart_number')} value={form.chart_number}
                     onChange={e => update('chart_number', e.target.value)} /></label>
            <label className="col-span-3">DOB <span className="text-red-500">*</span>
              <input type="date" className={"input w-full" + errCls('patient_dob')} value={form.patient_dob}
                     onChange={e => update('patient_dob', e.target.value)} /></label>
            <label className="col-span-3">First Name <span className="text-red-500">*</span>
              <input className={"input w-full" + errCls('patient_first_name')} value={form.patient_first_name}
                     onChange={e => update('patient_first_name', e.target.value)} /></label>
            <label className="col-span-3">Last Name <span className="text-red-500">*</span>
              <input className={"input w-full" + errCls('patient_last_name')} value={form.patient_last_name}
                     onChange={e => update('patient_last_name', e.target.value)} /></label>
            <label className="col-span-3">Email <span className="text-red-500">*</span>
              <input className={"input w-full" + errCls('patient_email')} value={form.patient_email}
                     onChange={e => update('patient_email', e.target.value)} /></label>
            <label className="col-span-3">Cell Phone <span className="text-red-500">*</span>
              <input className={"input w-full" + errCls('patient_cell')} value={form.patient_cell}
                     onChange={e => update('patient_cell', e.target.value)} /></label>
            <label className="col-span-6">Device Type <span className="text-red-500">*</span>
              <select className={"input w-full" + errCls('device_type_id')} value={form.device_type_id}
                      onChange={e => update('device_type_id', e.target.value)}>
                <option value="">— select device —</option>
                {(types || []).filter(t => t.is_active).map(t => (
                  <option key={t.id} value={t.id}>{t.name}</option>))}
              </select></label>
            <label className="col-span-6">Requested By <span className="text-red-500">*</span>
              <select className={"input w-full" + errCls('requested_by_email')} value={form.requested_by_email}
                      onChange={e => update('requested_by_email', e.target.value)}>
                <option value="">— select provider —</option>
                {(clinicians || []).map(c => (
                  <option key={c.email} value={c.email}>
                    {c.display_name}{c.credential ? `, ${c.credential}` : ''}</option>))}
              </select>
              <span className="text-[11px] text-muted">Manage providers in Admin → Users.</span>
            </label>
            <label className="col-span-6">Reason for Request <span className="text-red-500">*</span>
              <select className={"input w-full" + errCls('reason_for_request')} value={form.reason_for_request}
                      onChange={e => {
                        const r = reasons.find(x => x.reason === e.target.value)
                        update('reason_for_request', e.target.value)
                        update('reason_icd10', r?.icd10 || '')
                      }}>
                <option value="">— select reason —</option>
                {reasons.map(r => (
                  <option key={r.reason} value={r.reason}>{r.reason} ({r.icd10})</option>))}
              </select></label>
          </div>
        )}

        {step === 2 && suggestion && (
          <div className="p-4 text-sm space-y-3">
            <div className="rounded border border-plum-200 bg-plum-50 p-3">
              <div className="font-medium text-plum-700">Recommended</div>
              <div>{FLOW_LABEL[suggestion.suggested_flow]}
                {suggestion.suggested_flow === 'in_stock'
                  && ` — ${suggestion.in_stock_count} available`}</div>
            </div>
            <div>
              <div className="text-[11px] text-muted mb-1">Choose how to fulfill:</div>
              {suggestion.allowed_flows.map(f => (
                <label key={f} className="flex items-center gap-2 py-1">
                  <input type="radio" name="flow" checked={chosenFlow === f}
                         onChange={() => setChosenFlow(f)} />
                  {FLOW_LABEL[f]}
                </label>))}
            </div>
          </div>
        )}

        <div className="sticky bottom-0 bg-white border-t px-4 py-3 flex justify-between">
          {step === 2
            ? <button className="btn-ghost" onClick={() => setStep(1)}>Back</button>
            : <span />}
          {step === 1
            ? <button className="btn-primary" disabled={suggest.isPending}
                      onClick={handleContinue}>Continue</button>
            : <button className="btn-primary" disabled={!chosenFlow || create.isPending}
                      onClick={() => create.mutate()}>Confirm &amp; Create</button>}
        </div>
      </div>
    </div>
  )
}
