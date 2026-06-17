import { useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowLeft, Settings } from 'lucide-react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import api from '../utils/api'
import LoadingState from '../components/LoadingState'
import PelletDoseTypes from './PelletDoseTypes'

const TABS = [
  { id: 'thresholds', label: 'Thresholds & Windows' },
  { id: 'types',      label: 'Dose Types' },
  { id: 'portal',     label: 'Patient Portal' },
  { id: 'payments',   label: 'Payments' },
  { id: 'portalinfo', label: 'Portal Info' },
]

export default function PelletSettings() {
  const [tab, setTab] = useState('thresholds')
  return (
    <div>
      <div className="mb-4 flex items-center gap-3">
        <Link to="/pellets" className="text-muted hover:text-plum-700">
          <ArrowLeft size={18} />
        </Link>
        <h1 className="font-serif text-[24px] font-semibold text-ink m-0 flex items-center gap-2">
          <Settings size={22} className="text-plum-700" />
          Pellet Settings
        </h1>
      </div>
      <div className="flex gap-1 border-b border-border-subtle mb-6">
        {TABS.map(t => (
          <button key={t.id} onClick={() => setTab(t.id)}
                  className={`px-3 py-2 text-[13px] border-b-2 -mb-px transition ${
                    tab === t.id
                      ? 'border-plum-700 text-plum-700 font-medium'
                      : 'border-transparent text-muted hover:text-plum-700'}`}>
            {t.label}
          </button>
        ))}
      </div>
      {tab === 'thresholds' && <ThresholdsTab />}
      {tab === 'types'      && <PelletDoseTypes embedded />}
      {tab === 'portal'     && <PatientPortalTab />}
      {tab === 'payments'   && <PaymentsTab />}
      {tab === 'portalinfo' && <PortalInfoTab />}
    </div>
  )
}

// ─── Thresholds & Windows tab ───────────────────────────────────────

const THRESHOLD_FIELDS = [
  { key: 'stale_visit_days', label: 'Stale Visit (Days)',
    hint: 'Pre-insertion visits this many days past their scheduled date are swept stale.' },
  { key: 'dose_suggest_max_pellets', label: 'Max Pellets Per Combo',
    hint: 'Upper bound on pellets in a suggested dose combination.' },
  { key: 'dose_suggest_max_results', label: 'Max Dose Suggestions',
    hint: 'How many dose combinations to offer.' },
  { key: 'labs_valid_days', label: 'Labs Valid (Days)',
    hint: 'Labs must be drawn within this many days of the visit.' },
  { key: 'mammo_valid_days', label: 'Mammogram Valid (Days)',
    hint: 'Mammogram must be within this many days of the visit.' },
]

function saveErrorMessage(error) {
  const detail = error?.response?.data?.detail
  if (Array.isArray(detail)) return detail[0]?.msg || 'Save failed — check values.'
  if (typeof detail === 'string') return detail
  return 'Save failed — check values.'
}

function ThresholdsTab() {
  const qc = useQueryClient()
  const { data } = useQuery({
    queryKey: ['pellet-config'],
    queryFn: () => api.get('/pellets/config').then(r => r.data),
  })
  const [draft, setDraft] = useState({})
  const save = useMutation({
    mutationFn: (body) => api.put('/pellets/config', body).then(r => r.data),
    onSuccess: () => { setDraft({}); qc.invalidateQueries({ queryKey: ['pellet-config'] }) },
  })
  if (!data) return <LoadingState />
  const val = (k) => draft[k] ?? data[k] ?? ''
  return (
    <div className="space-y-6">
      <section className="card p-4">
        <h2 className="font-medium mb-3">Thresholds & Windows</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {THRESHOLD_FIELDS.map(f => (
            <label key={f.key} className="block text-[13px]">
              <span className="font-medium">{f.label}</span>
              <input type="number" className="input mt-1 w-28"
                     value={val(f.key)}
                     onChange={e => setDraft(d => ({ ...d, [f.key]: Number(e.target.value) }))} />
              {f.hint && <p className="text-[11px] text-muted mt-0.5">{f.hint}</p>}
            </label>
          ))}
        </div>
        <button className="btn-primary text-xs mt-4"
                disabled={!Object.keys(draft).length || save.isPending}
                onClick={() => save.mutate(draft)}>
          {save.isPending ? 'Saving…' : 'Save Changes'}
        </button>
        {save.isError && (
          <p className="text-xs text-red-700 mt-2">{saveErrorMessage(save.error)}</p>
        )}
      </section>
    </div>
  )
}

// ─── Portal Info tab ────────────────────────────────────────────────

function PortalInfoTab() {
  const qc = useQueryClient()
  const { data } = useQuery({
    queryKey: ['pellet-config'],
    queryFn: () => api.get('/pellets/config').then(r => r.data),
  })
  const [draft, setDraft] = useState(null)
  const save = useMutation({
    mutationFn: (body) => api.put('/pellets/config', body).then(r => r.data),
    onSuccess: () => { setDraft(null); qc.invalidateQueries({ queryKey: ['pellet-config'] }) },
  })
  if (!data) return <LoadingState />
  const value = draft ?? data.portal_info_text ?? ''
  const dirty = draft != null && draft !== (data.portal_info_text ?? '')
  return (
    <div className="space-y-6">
      <section className="card p-4">
        <h2 className="font-medium mb-1">Portal Info</h2>
        <p className="text-[11px] text-muted mb-3">
          Shown to patients on the portal's Rules &amp; Info page (markdown).
        </p>
        <textarea className="input text-[12px] w-full font-mono" rows={16}
                  value={value}
                  onChange={e => setDraft(e.target.value)}
                  placeholder="Markdown supported: # headings · **bold** · *italic* · - lists · | tables | · `code` · > quotes" />
        <button className="btn-primary text-xs mt-4"
                disabled={!dirty || save.isPending}
                onClick={() => save.mutate({ portal_info_text: value })}>
          {save.isPending ? 'Saving…' : 'Save Changes'}
        </button>
        {save.isError && (
          <p className="text-xs text-red-700 mt-2">{saveErrorMessage(save.error)}</p>
        )}
      </section>
    </div>
  )
}

// ─── Payments tab ───────────────────────────────────────────────────

const PAY_TOGGLES = [
  { key: 'enable_single', label: 'Enable Single Insertion',
    hint: 'Patients can pay for one insertion at the standard price.' },
  { key: 'enable_package', label: 'Enable Packages',
    hint: 'Patients can buy multiple insertions up front at a discount.' },
  { key: 'enable_subscription', label: 'Enable Subscription',
    hint: 'Patients can pay a recurring monthly amount toward an insertion.' },
]

function PaymentsTab() {
  const qc = useQueryClient()
  const { data } = useQuery({
    queryKey: ['pellet-config'],
    queryFn: () => api.get('/pellets/config').then(r => r.data),
  })
  const [draft, setDraft] = useState({})
  const save = useMutation({
    mutationFn: (body) => api.put('/pellets/config', body).then(r => r.data),
    onSuccess: () => { setDraft({}); qc.invalidateQueries({ queryKey: ['pellet-config'] }) },
  })
  if (!data) return <LoadingState />

  const num = (k) => draft[k] ?? data[k] ?? ''
  const bool = (k) => draft[k] ?? data[k] ?? false
  const tiers = draft.package_discount_tiers ?? data.package_discount_tiers ?? []

  const setTiers = (next) =>
    setDraft(d => ({ ...d, package_discount_tiers: next }))
  const updateTier = (i, key, value) =>
    setTiers(tiers.map((t, idx) => idx === i ? { ...t, [key]: Number(value) } : t))
  const addTier = () =>
    setTiers([...tiers, { count: 2, percent_off: 0 }])
  const removeTier = (i) =>
    setTiers(tiers.filter((_, idx) => idx !== i))

  return (
    <div className="space-y-6">
      <section className="card p-4">
        <h2 className="font-medium mb-3">Pricing</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <label className="block text-[13px]">
            <span className="font-medium">Insertion Price ($)</span>
            <input type="number" className="input mt-1 w-32"
                   value={num('insertion_price')}
                   onChange={e => setDraft(d => ({ ...d, insertion_price: Number(e.target.value) }))} />
            <p className="text-[11px] text-muted mt-0.5">Standard price for a single insertion.</p>
          </label>
          <label className="block text-[13px]">
            <span className="font-medium">Subscription Monthly Amount ($)</span>
            <input type="number" className="input mt-1 w-32"
                   value={num('subscription_monthly_amount')}
                   onChange={e => setDraft(d => ({ ...d, subscription_monthly_amount: Number(e.target.value) }))} />
            <p className="text-[11px] text-muted mt-0.5">Recurring monthly charge. Leave at 0 if unused.</p>
          </label>
        </div>
      </section>

      <section className="card p-4">
        <h2 className="font-medium mb-3">Payment Methods</h2>
        <div className="space-y-3">
          {PAY_TOGGLES.map(f => (
            <label key={f.key} className="flex items-start gap-2 text-[13px]">
              <input type="checkbox" className="mt-0.5"
                     checked={bool(f.key)}
                     onChange={e => setDraft(d => ({ ...d, [f.key]: e.target.checked }))} />
              <span>
                <span className="font-medium">{f.label}</span>
                {f.hint && <p className="text-[11px] text-muted mt-0.5">{f.hint}</p>}
              </span>
            </label>
          ))}
        </div>
      </section>

      <section className="card p-4">
        <h2 className="font-medium mb-3">Package Discount Tiers</h2>
        <p className="text-[11px] text-muted mb-3">
          Each tier applies its percent discount when the package count is at or above the tier count.
        </p>
        <table className="text-[13px] mb-3">
          <thead>
            <tr className="text-left text-muted">
              <th className="pr-4 pb-1 font-medium">Count</th>
              <th className="pr-4 pb-1 font-medium">Percent Off</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {tiers.map((t, i) => (
              <tr key={i}>
                <td className="pr-4 py-1">
                  <input type="number" className="input w-24"
                         value={t.count ?? ''}
                         onChange={e => updateTier(i, 'count', e.target.value)} />
                </td>
                <td className="pr-4 py-1">
                  <input type="number" className="input w-24"
                         value={t.percent_off ?? ''}
                         onChange={e => updateTier(i, 'percent_off', e.target.value)} />
                </td>
                <td className="py-1">
                  <button type="button"
                          className="text-xs text-red-700 hover:underline"
                          onClick={() => removeTier(i)}>
                    Remove
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <button type="button" className="btn-secondary text-xs" onClick={addTier}>
          Add Tier
        </button>
      </section>

      <button className="btn-primary text-xs"
              disabled={!Object.keys(draft).length || save.isPending}
              onClick={() => save.mutate(draft)}>
        {save.isPending ? 'Saving…' : 'Save Changes'}
      </button>
      {save.isError && (
        <p className="text-xs text-red-700 mt-2">{saveErrorMessage(save.error)}</p>
      )}
    </div>
  )
}

// ─── Patient Portal tab ─────────────────────────────────────────────

const PORTAL_TOGGLES = [
  { key: 'require_mammo', label: 'Require Mammogram',
    hint: 'Patients must upload a current mammogram before the insertion visit.' },
  { key: 'require_labs', label: 'Require Labs',
    hint: 'Patients must self-report (or have on file) current labs.' },
  { key: 'require_consent', label: 'Require Consent',
    hint: 'Patients must sign the insertion consent before the visit.' },
]

function PatientPortalTab() {
  const qc = useQueryClient()
  const { data } = useQuery({
    queryKey: ['pellet-config'],
    queryFn: () => api.get('/pellets/config').then(r => r.data),
  })
  const [draft, setDraft] = useState({})
  const save = useMutation({
    mutationFn: (body) => api.put('/pellets/config', body).then(r => r.data),
    onSuccess: () => { setDraft({}); qc.invalidateQueries({ queryKey: ['pellet-config'] }) },
  })
  if (!data) return <LoadingState />
  const bool = (k) => draft[k] ?? data[k] ?? true
  const str = (k) => draft[k] ?? data[k] ?? ''
  return (
    <div className="space-y-6">
      <section className="card p-4">
        <h2 className="font-medium mb-3">Patient Portal Requirements</h2>
        <div className="space-y-3">
          {PORTAL_TOGGLES.map(f => (
            <label key={f.key} className="flex items-start gap-2 text-[13px]">
              <input type="checkbox" className="mt-0.5"
                     checked={bool(f.key)}
                     onChange={e => setDraft(d => ({ ...d, [f.key]: e.target.checked }))} />
              <span>
                <span className="font-medium">{f.label}</span>
                {f.hint && <p className="text-[11px] text-muted mt-0.5">{f.hint}</p>}
              </span>
            </label>
          ))}
        </div>
        <div className="mt-4">
          <label className="block text-[13px]">
            <span className="font-medium">Consent Template ID</span>
            <input type="text" className="input mt-1 w-full max-w-md"
                   value={str('consent_template_id')}
                   onChange={e => setDraft(d => ({ ...d, consent_template_id: e.target.value }))} />
            <p className="text-[11px] text-muted mt-0.5">
              The BoldSign template id used for the insertion consent. Leave blank if none.
            </p>
          </label>
        </div>
        <button className="btn-primary text-xs mt-4"
                disabled={!Object.keys(draft).length || save.isPending}
                onClick={() => save.mutate(draft)}>
          {save.isPending ? 'Saving…' : 'Save Changes'}
        </button>
        {save.isError && (
          <p className="text-xs text-red-700 mt-2">{saveErrorMessage(save.error)}</p>
        )}
      </section>
    </div>
  )
}
