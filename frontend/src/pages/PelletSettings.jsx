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
