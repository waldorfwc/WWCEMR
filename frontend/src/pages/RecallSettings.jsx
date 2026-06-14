import { useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowLeft, Settings } from 'lucide-react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import api from '../utils/api'
import LoadingState from '../components/LoadingState'

const TABS = [
  { id: 'thresholds', label: 'Thresholds & Windows' },
  { id: 'outcomes',   label: 'Outcomes' },
]

export default function RecallSettings() {
  const [tab, setTab] = useState('thresholds')
  return (
    <div>
      <div className="mb-4 flex items-center gap-3">
        <Link to="/recalls" className="text-muted hover:text-plum-700">
          <ArrowLeft size={18} />
        </Link>
        <h1 className="font-serif text-[24px] font-semibold text-ink m-0 flex items-center gap-2">
          <Settings size={22} className="text-plum-700" />
          Recall Settings
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
      {tab === 'outcomes'   && <OutcomesTab />}
    </div>
  )
}

function saveErrorMessage(error) {
  const detail = error?.response?.data?.detail
  if (Array.isArray(detail)) return detail[0]?.msg || 'Save failed — check values.'
  if (typeof detail === 'string') return detail
  return 'Save failed — check values.'
}

// ─── Thresholds & Windows tab ───────────────────────────────────────

const THRESHOLD_FIELDS = [
  { key: 'claim_ttl_minutes', label: 'Soft-Claim Lock (Minutes)',
    hint: 'How long an opened recall stays locked to one caller before others can pick it up.' },
  { key: 'overdue_window_months', label: 'Overdue Window (Months)',
    hint: 'Lookback window for the overdue-recalls metric.' },
]

function ThresholdsTab() {
  const qc = useQueryClient()
  const { data } = useQuery({
    queryKey: ['recall-config'],
    queryFn: () => api.get('/recalls/config').then(r => r.data),
  })
  const [draft, setDraft] = useState({})
  const save = useMutation({
    mutationFn: (body) => api.put('/recalls/config', body).then(r => r.data),
    onSuccess: () => {
      setDraft({})
      qc.invalidateQueries({ queryKey: ['recall-config'] })
      qc.invalidateQueries({ queryKey: ['recall-outcomes'] })
    },
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

// ─── Outcomes tab ───────────────────────────────────────────────────

const CATEGORIES = ['permanent', 'cooldown', 'completed', 'neutral']

function OutcomesTab() {
  const qc = useQueryClient()
  const { data: config } = useQuery({
    queryKey: ['recall-config'],
    queryFn: () => api.get('/recalls/config').then(r => r.data),
  })
  const [rows, setRows] = useState(null)
  const save = useMutation({
    mutationFn: (body) => api.put('/recalls/config', { recall_outcomes: body }).then(r => r.data),
    onSuccess: () => {
      setRows(null)
      qc.invalidateQueries({ queryKey: ['recall-config'] })
      qc.invalidateQueries({ queryKey: ['recall-outcomes'] })
    },
  })
  if (!config) return <LoadingState />
  const effective = rows ?? config.recall_outcomes ?? []

  const upd = (i, fn) => setRows(effective.map((r, j) => j === i ? fn(r) : r))

  return (
    <div className="space-y-4">
      <p className="text-[12px] text-muted">
        These outcomes appear in the call-outcome dropdown when working a recall.
      </p>
      {effective.map((row, i) => (
        <section key={i} className="card p-4">
          <div className="flex flex-wrap items-center gap-2 text-[13px]">
            <input className="input w-56" placeholder="Label"
                   value={row.label || ''}
                   onChange={e => upd(i, r => ({ ...r, label: e.target.value }))} />
            <select className="input w-32" value={row.category || 'cooldown'}
                    onChange={e => upd(i, r => {
                      const category = e.target.value
                      const next = { ...r, category }
                      if (category === 'cooldown' && next.cooldown_days == null) next.cooldown_days = 1
                      return next
                    })}>
              {CATEGORIES.map(c => <option key={c} value={c}>{c}</option>)}
            </select>
            {row.category === 'cooldown' && (
              <label className="flex items-center gap-1 text-[12px]">
                <input type="number" min={1} className="input w-20"
                       value={row.cooldown_days ?? ''}
                       onChange={e => upd(i, r => ({ ...r, cooldown_days: Number(e.target.value) }))} />
                <span className="text-muted">cooldown days</span>
              </label>
            )}
            {row.category === 'permanent' && (
              <input className="input w-40" placeholder="Reason code (optional)"
                     value={row.reason_code || ''}
                     onChange={e => upd(i, r => ({ ...r, reason_code: e.target.value }))} />
            )}
            <button className="text-xs text-red-700 hover:underline ml-auto"
                    onClick={() => setRows(effective.filter((_, j) => j !== i))}>
              Remove
            </button>
          </div>
        </section>
      ))}
      <div className="flex items-center gap-3">
        <button className="text-xs text-plum-700 hover:underline"
                onClick={() => setRows([...effective,
                  { label: '', category: 'cooldown', cooldown_days: 1 }])}>
          + Add Outcome
        </button>
        <button className="btn-primary text-xs" disabled={!rows || save.isPending}
                onClick={() => save.mutate(rows)}>
          {save.isPending ? 'Saving…' : 'Save Changes'}
        </button>
        {save.isError && (
          <span className="text-xs text-red-700">{saveErrorMessage(save.error)}</span>
        )}
      </div>
    </div>
  )
}
