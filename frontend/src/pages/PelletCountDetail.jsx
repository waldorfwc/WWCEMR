import { useState, useRef, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link, useParams, useNavigate } from 'react-router-dom'
import {
  ArrowLeft, ClipboardList, CheckCircle2, AlertTriangle, Shield, X,
  Save, Clock,
} from 'lucide-react'
import api, { fmt } from '../utils/api'
import LoadingState from '../components/LoadingState'


const LOC_LABEL = {
  white_plains: 'White Plains',
  brandywine:   'Brandywine',
  arlington:    'Arlington',
  all:          'All locations',
}


export default function PelletCountDetail() {
  const { id } = useParams()
  const navigate = useNavigate()
  const qc = useQueryClient()

  const { data: count, isLoading } = useQuery({
    queryKey: ['pellet-count', id],
    queryFn: () => api.get(`/pellets/counts/${id}`).then(r => r.data),
    refetchInterval: 5000,
  })

  const [finishing, setFinishing] = useState(false)
  const [witness, setWitness] = useState('')
  const [finishNotes, setFinishNotes] = useState('')

  const finishMut = useMutation({
    mutationFn: () => api.post(`/pellets/counts/${id}/finish`,
                                { witness_user: witness || null,
                                  notes: finishNotes || null }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['pellet-count', id] })
      qc.invalidateQueries({ queryKey: ['pellet-counts'] })
      qc.invalidateQueries({ queryKey: ['pellet-dashboard'] })
      setFinishing(false)
    },
    onError: (e) => {
      const d = e?.response?.data?.detail
      if (typeof d === 'object' && d.unresolved_lines) {
        alert(`Can't finish: ${d.unresolved_lines.length} line(s) still unresolved.\n\n` +
              d.unresolved_lines.map(l => `• ${l.issue}`).join('\n'))
      } else {
        alert(d || 'Finish failed')
      }
    },
  })

  if (isLoading) return <LoadingState />
  if (!count) return <div className="p-6 text-red-600">Count not found.</div>

  const lines = count.lines || []
  const hasControlled = lines.some(l => l.is_controlled)
  const counted = lines.filter(l => l.counted_doses != null).length
  const remaining = lines.length - counted
  const variances = lines.filter(l => l.counted_doses != null
                                       && l.variance !== 0)
  const unresolvedVariances = variances.filter(l => !(l.notes || '').trim())
  const isDone = count.status !== 'in_progress'

  return (
    <div>
      <Link to="/pellets/counts" className="text-[12px] text-muted hover:underline flex items-center gap-1 mb-2">
        <ArrowLeft size={12} /> All counts
      </Link>
      <div className="flex items-baseline justify-between mb-3 flex-wrap gap-2">
        <div>
          <h1 className="page-title flex items-center gap-2">
            <ClipboardList size={22} className="text-plum-700" />
            Count · {LOC_LABEL[count.location] || count.location}
          </h1>
          <p className="text-sm text-gray-500 mt-0.5">
            Started {fmt.date(count.started_at)} by {count.started_by?.split('@')[0]}
            {isDone && (
              <> · Finished {fmt.date(count.finished_at)} by {count.finished_by?.split('@')[0]}</>
            )}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {!isDone && (
            <button className="btn-primary text-sm flex items-center gap-1"
                    onClick={() => setFinishing(true)}
                    disabled={remaining > 0 || unresolvedVariances.length > 0}
                    title={remaining > 0
                            ? `${remaining} lots not yet counted`
                            : unresolvedVariances.length > 0
                              ? `${unresolvedVariances.length} variances need notes`
                              : 'Finish count'}>
              <CheckCircle2 size={12}/> Finish count
            </button>
          )}
          {isDone && (
            <span className="text-[11px] uppercase px-2 py-0.5 rounded bg-green-100 text-green-700">
              <CheckCircle2 size={11} className="inline mr-0.5"/> finished
            </span>
          )}
        </div>
      </div>

      {/* Progress summary */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mb-3">
        <StatCard label="Lots" value={lines.length} tone="gray" />
        <StatCard label="Counted" value={counted} tone="blue" />
        <StatCard label="Remaining" value={remaining} tone={remaining > 0 ? 'amber' : 'gray'} />
        <StatCard label="Variances" value={variances.length}
                    tone={variances.length > 0 ? 'red' : 'gray'}
                    sub={unresolvedVariances.length > 0
                          ? `${unresolvedVariances.length} need notes`
                          : null} />
      </div>

      {!isDone && hasControlled && (
        <div className="text-[12px] text-amber-800 bg-amber-50 border border-amber-200 rounded p-2 mb-3">
          <Shield size={11} className="inline" /> This count includes
          <strong> Schedule III</strong> testosterone. Finishing requires a
          witness email different from yours.
        </div>
      )}

      {/* Lines */}
      <div className="card !p-0 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-plum-50 sticky top-[60px] z-10">
            <tr>
              <th className="table-th">Dose</th>
              <th className="table-th">Lot #</th>
              <th className="table-th">Expires</th>
              <th className="table-th text-right">Expected</th>
              <th className="table-th text-right">Counted</th>
              <th className="table-th text-right">Variance</th>
              <th className="table-th">Notes</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {lines.length === 0 && (
              <tr><td colSpan={7} className="table-td text-center text-gray-400 py-6 italic">
                No lots had positive stock when this count was started.
              </td></tr>
            )}
            {lines.map(l => (
              <CountLineRow key={l.id} line={l} countId={id} disabled={isDone} />
            ))}
          </tbody>
        </table>
      </div>

      {/* Finish drawer */}
      {finishing && (
        <div className="fixed inset-0 z-50 flex justify-end" onClick={() => setFinishing(false)}>
          <div className="absolute inset-0 bg-black/30" />
          <div className="relative w-full max-w-md bg-white shadow-xl overflow-y-auto"
               onClick={e => e.stopPropagation()}>
            <div className="sticky top-0 bg-white border-b border-border-subtle px-5 py-3 flex items-center justify-between">
              <h2 className="font-serif font-semibold text-ink text-[16px]">Finish Count</h2>
              <button onClick={() => setFinishing(false)} className="text-muted hover:text-ink"><X size={18} /></button>
            </div>
            <div className="p-5 space-y-3 text-sm">
              <div className="text-[12px] text-gray-600 bg-plum-50/50 border border-plum-100 rounded p-2">
                Finishing reconciles inventory to the counted numbers — any
                variances are applied to PelletStock and written to the
                audit log. This action cannot be undone.
              </div>
              {variances.length > 0 && (
                <div className="bg-red-50 border border-red-200 rounded p-2 text-[12px]">
                  <div className="flex items-center gap-1 text-red-800 font-semibold mb-1">
                    <AlertTriangle size={12}/> {variances.length} variance{variances.length === 1 ? '' : 's'} will apply:
                  </div>
                  <ul className="text-[11px] space-y-0.5">
                    {variances.slice(0, 6).map(v => (
                      <li key={v.id}>
                        <strong>{v.variance > 0 ? '+' : ''}{v.variance}</strong>{' '}
                        {v.lot_label} lot {v.qualgen_lot}
                      </li>
                    ))}
                    {variances.length > 6 && (
                      <li className="text-gray-500">+{variances.length - 6} more…</li>
                    )}
                  </ul>
                </div>
              )}
              {hasControlled && (
                <div className="border border-amber-200 bg-amber-50/50 rounded p-2">
                  <div className="text-[11px] text-amber-800 font-semibold flex items-center gap-1 mb-1">
                    <Shield size={11} /> Schedule III witness required
                  </div>
                  <input className="input text-[12px] w-full"
                          placeholder="Witness email (must be a different person)"
                          value={witness} onChange={e => setWitness(e.target.value)} />
                </div>
              )}
              <div>
                <label className="text-[11px] uppercase text-gray-500 block mb-1">Closing notes (optional)</label>
                <textarea className="input text-[12px] w-full" rows={2}
                          value={finishNotes} onChange={e => setFinishNotes(e.target.value)} />
              </div>
            </div>
            <div className="sticky bottom-0 bg-white border-t border-border-subtle px-5 py-3 flex justify-end gap-2">
              <button className="text-sm text-muted hover:underline" onClick={() => setFinishing(false)}>Cancel</button>
              <button className="btn-primary text-sm flex items-center gap-1"
                      onClick={() => finishMut.mutate()}
                      disabled={(hasControlled && !witness.trim()) || finishMut.isPending}>
                <CheckCircle2 size={12}/> {finishMut.isPending ? 'Finishing…' : 'Reconcile & finish'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}


function StatCard({ label, value, tone, sub }) {
  const tones = {
    gray:  'border-gray-200 bg-gray-50',
    blue:  'border-blue-200 bg-blue-50',
    amber: 'border-amber-200 bg-amber-50',
    red:   'border-red-200 bg-red-50',
  }
  return (
    <div className={`card border ${tones[tone] || tones.gray} !p-2.5`}>
      <div className="text-[11px] uppercase tracking-wide text-gray-500">{label}</div>
      <div className="text-2xl font-bold mt-0.5">{value}</div>
      {sub && <div className="text-[10px] text-red-700">{sub}</div>}
    </div>
  )
}


function CountLineRow({ line, countId, disabled }) {
  const qc = useQueryClient()
  const [draft, setDraft] = useState(line.counted_doses != null
                                       ? String(line.counted_doses) : '')
  const [notesDraft, setNotesDraft] = useState(line.notes || '')
  const [editingNotes, setEditingNotes] = useState(false)
  const inputRef = useRef(null)

  useEffect(() => {
    setDraft(line.counted_doses != null ? String(line.counted_doses) : '')
    setNotesDraft(line.notes || '')
  }, [line.counted_doses, line.notes])

  const save = useMutation({
    mutationFn: (body) => api.post(`/pellets/counts/${countId}/scan`,
                                    body).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['pellet-count', countId] })
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Save failed'),
  })

  function commitCount() {
    const n = Number(draft)
    if (draft === '' || Number.isNaN(n) || n < 0) return
    if (n === line.counted_doses) return  // no change
    save.mutate({
      lot_id: line.lot_id,
      counted_doses: n,
      notes: notesDraft || null,
    })
  }

  function commitNotes() {
    if (notesDraft === (line.notes || '')) {
      setEditingNotes(false); return
    }
    if (line.counted_doses == null) {
      // Can't save notes without a count — store locally and require a count first
      setEditingNotes(false)
      return
    }
    save.mutate({
      lot_id: line.lot_id,
      counted_doses: line.counted_doses,
      notes: notesDraft || null,
    })
    setEditingNotes(false)
  }

  const variance = line.counted_doses != null
    ? (line.counted_doses - line.expected_doses) : null
  const isUncounted = line.counted_doses == null
  const isPerfect = variance === 0
  const isMissingNote = variance != null && variance !== 0 && !(line.notes || '').trim()

  return (
    <tr className={isUncounted ? 'bg-amber-50/40'
                  : isMissingNote ? 'bg-red-50/40'
                  : isPerfect ? '' : 'bg-amber-50/30'}>
      <td className="table-td">
        {line.lot_label || '—'}
        {line.is_controlled && (
          <span className="ml-1 text-[11px] bg-amber-100 text-amber-700 px-1 rounded">SCH III</span>
        )}
      </td>
      <td className="table-td font-mono text-[11px]">{line.qualgen_lot || '—'}</td>
      <td className="table-td text-[11px]">
        {line.expiration_date && line.expiration_date !== '2099-12-31'
          ? fmt.date(line.expiration_date)
          : <span className="text-gray-400 italic">unknown</span>}
      </td>
      <td className="table-td text-right font-mono">{line.expected_doses}</td>
      <td className="table-td text-right">
        {disabled ? (
          <span className="font-mono">{line.counted_doses ?? '—'}</span>
        ) : (
          <input ref={inputRef} type="number" min="0"
                 className="input text-[12px] w-20 text-right font-mono"
                 value={draft}
                 onChange={e => setDraft(e.target.value)}
                 onBlur={commitCount}
                 onKeyDown={e => {
                   if (e.key === 'Enter') {
                     e.currentTarget.blur()
                   }
                 }} />
        )}
      </td>
      <td className={`table-td text-right font-mono font-semibold ${
        variance == null ? 'text-gray-400'
          : variance === 0 ? 'text-green-700'
          : 'text-red-700'
      }`}>
        {variance == null ? '—' : (variance > 0 ? '+' : '') + variance}
      </td>
      <td className="table-td text-[11px]">
        {disabled ? (
          <span className="text-gray-600">{line.notes || '—'}</span>
        ) : editingNotes ? (
          <input className="input text-[11px] w-full"
                 autoFocus
                 value={notesDraft}
                 onChange={e => setNotesDraft(e.target.value)}
                 onBlur={commitNotes}
                 onKeyDown={e => {
                   if (e.key === 'Enter') e.currentTarget.blur()
                   if (e.key === 'Escape') {
                     setNotesDraft(line.notes || ''); setEditingNotes(false)
                   }
                 }} />
        ) : (
          <button type="button"
                  className={`text-left w-full ${
                    isMissingNote ? 'text-red-700 italic font-medium' : 'text-gray-600'
                  } hover:text-plum-700`}
                  onClick={() => setEditingNotes(true)}>
            {line.notes || (isMissingNote ? '⚠ explain variance' : 'add note')}
          </button>
        )}
      </td>
    </tr>
  )
}
