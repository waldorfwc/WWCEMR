import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'
import {
  ArrowLeft, ClipboardList, Plus, X, CheckCircle2, Clock, Shield, AlertTriangle,
  Zap, Trash2, FileText,
} from 'lucide-react'
import api, { fmt } from '../utils/api'


const LOC_LABEL = {
  white_plains: 'White Plains',
  brandywine:   'Brandywine',
  arlington:    'Arlington',
  all:          'All locations',
}

const LOC_ORDER = ['white_plains', 'brandywine', 'arlington']


export default function PelletCounts() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [starting, setStarting] = useState(false)
  const [location, setLocation] = useState('white_plains')
  const [notes, setNotes] = useState('')
  const [scope, setScope] = useState('all')                // 'all' | 'controlled_only'
  const [witnessStart, setWitnessStart] = useState('')
  const [startAllRunning, setStartAllRunning] = useState(false)
  const [startAllResult, setStartAllResult] = useState(null)
  const [showCancelled, setShowCancelled] = useState(false)

  const { data, isLoading } = useQuery({
    queryKey: ['pellet-counts', showCancelled],
    queryFn: () => api.get('/pellets/counts',
                            { params: { include_cancelled: showCancelled } }).then(r => r.data),
  })

  const cancelCountMut = useMutation({
    mutationFn: (countId) => api.post(`/pellets/counts/${countId}/cancel`).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['pellet-counts'] })
      qc.invalidateQueries({ queryKey: ['pellet-dashboard'] })
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Cancel failed'),
  })

  // Pre-check: are there visits today/earlier with proposed (unconfirmed) doses?
  const preCheck = useQuery({
    queryKey: ['pellet-counts-pre-check', location],
    queryFn: () => api.get('/pellets/counts/pre-check',
                            { params: { location } }).then(r => r.data),
    enabled: starting,
  })
  const blockingVisits = preCheck.data?.blocking_visits || []
  const blocked = blockingVisits.length > 0

  const confirmAsPlanned = useMutation({
    mutationFn: (visitId) => api.post(`/pellets/visits/${visitId}/confirm-as-planned`)
                                   .then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['pellet-counts-pre-check'] })
      qc.invalidateQueries({ queryKey: ['pellet-dashboard'] })
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Confirm failed'),
  })

  const cancelVisit = useMutation({
    mutationFn: ({ visitId, reason }) =>
      api.post(`/pellets/visits/${visitId}/cancel`, { reason }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['pellet-counts-pre-check'] })
      qc.invalidateQueries({ queryKey: ['pellet-dashboard'] })
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Cancel failed'),
  })

  const startMut = useMutation({
    mutationFn: () => api.post('/pellets/counts/start', {
      location,
      scope,
      witness_user: witnessStart.trim() || null,
      notes: notes || null,
    }).then(r => r.data),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ['pellet-counts'] })
      navigate(`/pellets/counts/${data.count_id}`)
    },
    onError: (e) => {
      const detail = e?.response?.data?.detail
      // If a count already exists today, offer to open it.
      if (detail && typeof detail === 'object' && detail.existing_count_id) {
        if (confirm(`${detail.message}\n\nOpen the existing count?`)) {
          setStarting(false)
          navigate(`/pellets/counts/${detail.existing_count_id}`)
          return
        }
        return
      }
      const msg = typeof detail === 'string'
        ? detail
        : (detail?.message || 'Failed to start count')
      alert(msg)
      qc.invalidateQueries({ queryKey: ['pellet-counts-pre-check', location] })
    },
  })

  async function startAllThree() {
    let witness = witnessStart.trim()
    if (!witness) {
      const w = window.prompt(
        'Sch III witness email (different from you) — required if any controlled stock is in scope:'
      )
      if (!w || !w.trim()) return
      witness = w.trim()
      setWitnessStart(witness)
    }
    setStartAllRunning(true)
    const results = []
    for (const loc of LOC_ORDER) {
      try {
        const pre = await api.get('/pellets/counts/pre-check',
                                    { params: { location: loc } }).then(r => r.data)
        if (pre.blocking_visits?.length > 0) {
          results.push({ loc, ok: false,
            reason: `${pre.blocking_visits.length} blocker(s) — open Start Count for ${LOC_LABEL[loc]} to resolve` })
          continue
        }
        const r = await api.post('/pellets/counts/start', {
          location: loc, scope, witness_user: witness || null,
          notes: notes || null,
        }).then(r => r.data)
        results.push({ loc, ok: true, count_id: r.count_id, lines: r.lines_snapshot })
      } catch (e) {
        const detail = e?.response?.data?.detail
        if (detail && typeof detail === 'object' && detail.existing_count_id) {
          results.push({ loc, ok: false,
            reason: `already exists today (${detail.existing_status}) — count_id ${detail.existing_count_id}`,
            existing_count_id: detail.existing_count_id })
          continue
        }
        const msg = typeof detail === 'string' ? detail : (detail?.message || 'failed')
        results.push({ loc, ok: false, reason: msg })
      }
    }
    setStartAllRunning(false)
    setStartAllResult(results)
    qc.invalidateQueries({ queryKey: ['pellet-counts'] })
    qc.invalidateQueries({ queryKey: ['pellet-dashboard'] })
  }

  const counts = data?.counts || []

  return (
    <div>
      <Link to="/pellets/inventory" className="text-[12px] text-muted hover:underline flex items-center gap-1 mb-2">
        <ArrowLeft size={12} /> Pellet inventory
      </Link>
      <div className="flex items-baseline justify-between mb-3 flex-wrap gap-2">
        <h1 className="page-title flex items-center gap-2">
          <ClipboardList size={22} className="text-plum-700" />
          Daily counts
        </h1>
        <div className="flex items-center gap-2">
          <button className="btn-secondary text-sm flex items-center gap-1"
                   onClick={startAllThree}
                   disabled={startAllRunning}>
            <Zap size={13} /> {startAllRunning ? 'Starting all 3…' : 'Start all 3 locations'}
          </button>
          <button className="btn-primary text-sm flex items-center gap-1"
                   onClick={() => setStarting(true)}>
            <Plus size={13} /> Start count
          </button>
        </div>
      </div>

      <div className="text-[12px] text-gray-600 bg-amber-50 border border-amber-200 rounded p-2 mb-3">
        <Shield size={11} className="inline text-amber-700" /> Testosterone is DEA
        Schedule III — daily counts of controlled stock are required. The witness
        signs in at <strong>start</strong> (and again at finish) so the count can
        always close even if no one else is around at EOD.
      </div>

      {/* Start All summary */}
      {startAllResult && (
        <div className="card mb-3 border border-plum-100 bg-plum-50/40">
          <div className="flex items-center justify-between gap-2 mb-2">
            <div className="text-sm font-semibold">Start all 3 result</div>
            <button className="text-[11px] text-muted hover:underline"
                    onClick={() => setStartAllResult(null)}>dismiss</button>
          </div>
          <ul className="text-[12px] space-y-1">
            {startAllResult.map(r => (
              <li key={r.loc} className="flex items-center justify-between">
                <span><strong>{LOC_LABEL[r.loc]}</strong> — {r.ok
                  ? <span className="text-green-700">started ({r.lines} lots)</span>
                  : <span className="text-red-700">{r.reason}</span>}</span>
                {r.ok && (
                  <Link to={`/pellets/counts/${r.count_id}`}
                         className="text-plum-700 hover:underline text-[11px]">
                    Open →
                  </Link>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="card !p-0 overflow-hidden">
        <div className="flex items-center justify-end px-3 py-2 bg-plum-50 border-b border-plum-100">
          <label className="text-[11px] flex items-center gap-1 cursor-pointer">
            <input type="checkbox" checked={showCancelled}
                    onChange={e => setShowCancelled(e.target.checked)} />
            <span>Show cancelled</span>
          </label>
        </div>
        <table className="w-full text-sm">
          <thead className="bg-plum-50">
            <tr>
              <th className="table-th">Location</th>
              <th className="table-th">Scope</th>
              <th className="table-th">Status</th>
              <th className="table-th">Started</th>
              <th className="table-th">Finished</th>
              <th className="table-th">Witness</th>
              <th className="table-th text-right">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {isLoading && (
              <tr><td colSpan={7} className="table-td text-center text-gray-400 py-6">Loading…</td></tr>
            )}
            {!isLoading && counts.length === 0 && (
              <tr><td colSpan={7} className="table-td text-center text-gray-400 py-6 italic">
                No counts yet — click <strong>Start count</strong> to begin one.
              </td></tr>
            )}
            {counts.map(c => (
              <tr key={c.id} className="hover:bg-plum-50/40 cursor-pointer"
                  onClick={() => navigate(`/pellets/counts/${c.id}`)}>
                <td className="table-td">{LOC_LABEL[c.location] || c.location}</td>
                <td className="table-td text-[11px]">
                  {c.scope === 'controlled_only'
                    ? <span className="bg-amber-100 text-amber-700 px-1 rounded">Sch III only</span>
                    : <span className="text-gray-500">All lots</span>}
                </td>
                <td className="table-td">
                  {c.status === 'in_progress' && (
                    <span className="text-[11px] uppercase px-1.5 py-0.5 rounded bg-amber-100 text-amber-700">
                      <Clock size={10} className="inline mr-0.5" /> in progress
                    </span>
                  )}
                  {c.status === 'finished' && (
                    <span className="text-[11px] uppercase px-1.5 py-0.5 rounded bg-green-100 text-green-700">
                      <CheckCircle2 size={10} className="inline mr-0.5" /> finished
                    </span>
                  )}
                  {c.status === 'cancelled' && (
                    <span className="text-[11px] uppercase px-1.5 py-0.5 rounded bg-gray-100 text-gray-700">
                      cancelled
                    </span>
                  )}
                </td>
                <td className="table-td text-[11px]">
                  <div>{fmt.date(c.started_at)}</div>
                  <div className="text-[10px] text-gray-500">by {c.started_by?.split('@')[0]}</div>
                </td>
                <td className="table-td text-[11px]">
                  {c.finished_at ? (
                    <>
                      <div>{fmt.date(c.finished_at)}</div>
                      <div className="text-[10px] text-gray-500">by {c.finished_by?.split('@')[0]}</div>
                    </>
                  ) : '—'}
                </td>
                <td className="table-td text-[10px] text-gray-500">
                  {c.witness_user_start && (
                    <div>start: {c.witness_user_start.split('@')[0]}</div>
                  )}
                  {c.witness_user && (
                    <div>finish: {c.witness_user.split('@')[0]}</div>
                  )}
                  {!c.witness_user_start && !c.witness_user && '—'}
                </td>
                <td className="table-td text-right whitespace-nowrap">
                  <div className="flex items-center justify-end gap-2">
                    {c.has_pdf && (
                      <a className="text-plum-700 hover:underline text-[11px] flex items-center gap-1"
                          href={`/api/pellets/counts/${c.id}/pdf`}
                          target="_blank" rel="noreferrer"
                          onClick={e => e.stopPropagation()}>
                        <FileText size={11}/> PDF
                      </a>
                    )}
                    {c.status === 'in_progress' && (
                      <button className="text-red-700 hover:underline text-[11px] flex items-center gap-1"
                              onClick={e => {
                                e.stopPropagation()
                                if (confirm(`Cancel the in-progress count for ${LOC_LABEL[c.location] || c.location}? `
                                  + `The snapshot is discarded; no stock changes have been made.`)) {
                                  cancelCountMut.mutate(c.id)
                                }
                              }}
                              disabled={cancelCountMut.isPending}>
                        <Trash2 size={11}/> Cancel
                      </button>
                    )}
                    <button className="text-plum-700 hover:underline text-[11px]"
                            onClick={() => navigate(`/pellets/counts/${c.id}`)}>
                      Open →
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Start drawer */}
      {starting && (
        <div className="fixed inset-0 z-50 flex justify-end" onClick={() => setStarting(false)}>
          <div className="absolute inset-0 bg-black/30" />
          <div className="relative w-full max-w-md bg-white shadow-xl overflow-y-auto"
               onClick={e => e.stopPropagation()}>
            <div className="sticky top-0 bg-white border-b border-border-subtle px-5 py-3 flex items-center justify-between">
              <h2 className="font-serif font-semibold text-ink text-[16px]">Start Daily Count</h2>
              <button onClick={() => setStarting(false)} className="text-muted hover:text-ink"><X size={18} /></button>
            </div>
            <div className="p-5 space-y-3 text-sm">
              <div className="text-[12px] text-gray-600 bg-plum-50/50 border border-plum-100 rounded p-2">
                Starts a count session. Snapshots every lot with positive on-hand
                in scope. Walk the shelf and enter the actual dose count per lot.
                Variances need notes.
              </div>
              <div>
                <label className="text-[11px] uppercase text-gray-500 block mb-1">Location</label>
                <select className="input text-sm w-full" value={location}
                        onChange={e => setLocation(e.target.value)}>
                  <option value="white_plains">White Plains</option>
                  <option value="brandywine">Brandywine</option>
                  <option value="arlington">Arlington</option>
                  <option value="all">All locations</option>
                </select>
              </div>

              <div>
                <label className="text-[11px] uppercase text-gray-500 block mb-1">Scope</label>
                <div className="flex gap-3 text-[12px]">
                  <label className="flex items-center gap-1 cursor-pointer">
                    <input type="radio" name="scope" checked={scope === 'all'}
                            onChange={() => setScope('all')} />
                    <span>All lots (estradiol + testosterone)</span>
                  </label>
                  <label className="flex items-center gap-1 cursor-pointer">
                    <input type="radio" name="scope" checked={scope === 'controlled_only'}
                            onChange={() => setScope('controlled_only')} />
                    <span>
                      <Shield size={10} className="inline text-amber-700"/> Sch III only
                    </span>
                  </label>
                </div>
                <div className="text-[10px] text-gray-500 mt-0.5">
                  DEA only requires perpetual inventory on testosterone. "Sch III only"
                  skips estradiol lots — faster walk-through.
                </div>
              </div>

              <div className="border border-amber-200 bg-amber-50/40 rounded p-2">
                <label className="text-[11px] uppercase text-amber-800 font-semibold flex items-center gap-1 mb-1">
                  <Shield size={11}/> Witness at start
                </label>
                <input className="input text-[12px] w-full"
                        placeholder="Witness email (different person)"
                        value={witnessStart}
                        onChange={e => setWitnessStart(e.target.value)} />
                <div className="text-[10px] text-amber-800 mt-0.5">
                  Required when any Sch III lot is in scope. Captured now so the count
                  can be finished even if no one else is around at EOD; a finish-time
                  signature is also collected before reconciliation.
                </div>
              </div>

              <div>
                <label className="text-[11px] uppercase text-gray-500 block mb-1">Notes (optional)</label>
                <textarea className="input text-[12px] w-full" rows={2}
                          value={notes} onChange={e => setNotes(e.target.value)} />
              </div>

              {/* Blocking visits — proposed insertions for today or earlier */}
              {preCheck.isLoading && (
                <div className="text-[11px] text-gray-400 italic">Checking for unconfirmed insertions…</div>
              )}
              {!preCheck.isLoading && blocked && (
                <div className="border border-red-200 bg-red-50/60 rounded p-2 space-y-1">
                  <div className="text-[12px] font-semibold text-red-800 flex items-center gap-1">
                    <AlertTriangle size={12}/>
                    Cannot start count — {blockingVisits.length} visit{blockingVisits.length === 1 ? '' : 's'} still proposed
                  </div>
                  <div className="text-[11px] text-red-700">
                    For each visit pick one: <strong>Confirm as planned</strong> if it went exactly as planned,
                    <strong> Edit dose</strong> if the lot/quantity changed, or <strong>Did not happen</strong>
                    if the patient no-showed or cancelled. Stock adjusts automatically.
                  </div>
                  <ul className="text-[11px] divide-y divide-red-100">
                    {blockingVisits.map(v => (
                      <li key={v.visit_id} className="py-1.5 flex items-center justify-between gap-2 flex-wrap">
                        <span className="flex-1 min-w-0">
                          <Link to={`/pellets/patients/${v.patient_id}`}
                                 className="text-plum-700 hover:underline font-medium"
                                 onClick={() => setStarting(false)}>
                            {v.patient_name || '(unknown)'}
                          </Link>
                          {v.chart_number && <span className="text-gray-500"> · #{v.chart_number}</span>}
                          <span className="text-gray-500"> · {fmt.date(v.scheduled_date)}
                          {' · '}{LOC_LABEL[v.location] || v.location || '—'}</span>
                        </span>
                        <div className="flex items-center gap-1 shrink-0">
                          <button className="text-[10px] py-0.5 px-2 rounded border border-red-200 text-red-700 hover:bg-red-50"
                                  onClick={() => {
                                    const reason = prompt(
                                      `Mark this visit as NOT done? Any pre-pulled pellets will return to stock.\n\n${v.patient_name} — ${fmt.date(v.scheduled_date)}\n\nReason (required for audit):`,
                                      'No-show')
                                    if (reason && reason.trim()) {
                                      cancelVisit.mutate({ visitId: v.visit_id,
                                                                reason: reason.trim() })
                                    }
                                  }}
                                  disabled={cancelVisit.isPending || confirmAsPlanned.isPending}>
                            Did not happen
                          </button>
                          <Link to={`/pellets/patients/${v.patient_id}`}
                                 className="text-[10px] py-0.5 px-2 rounded border border-gray-300 text-gray-700 hover:bg-gray-50"
                                 onClick={() => setStarting(false)}>
                            Edit dose
                          </Link>
                          <button className="btn-secondary text-[10px] py-0.5 px-2"
                                  onClick={() => {
                                    if (confirm(`Mark ALL proposed doses on this visit as inserted? "${v.patient_name}" — ${fmt.date(v.scheduled_date)}`)) {
                                      confirmAsPlanned.mutate(v.visit_id)
                                    }
                                  }}
                                  disabled={confirmAsPlanned.isPending || cancelVisit.isPending}>
                            {confirmAsPlanned.isPending ? '…' : 'Confirm as planned'}
                          </button>
                        </div>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {!preCheck.isLoading && !blocked && (
                <div className="text-[11px] text-green-700 flex items-center gap-1">
                  <CheckCircle2 size={11}/> No unconfirmed insertions — ready to start.
                </div>
              )}
            </div>
            <div className="sticky bottom-0 bg-white border-t border-border-subtle px-5 py-3 flex justify-end gap-2">
              <button className="text-sm text-muted hover:underline" onClick={() => setStarting(false)}>Cancel</button>
              <button className="btn-primary text-sm flex items-center gap-1"
                      onClick={() => startMut.mutate()}
                      disabled={startMut.isPending || blocked || preCheck.isLoading}>
                <Plus size={12}/> {startMut.isPending ? 'Starting…' : 'Start count'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
