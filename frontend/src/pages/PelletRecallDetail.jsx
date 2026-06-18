import { useEffect, useRef, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Phone, X, Clock, Lock } from 'lucide-react'
import api, { fmt } from '../utils/api'

// ─── Small local helpers ─────────────────────────────────────────────────────

function Card({ children, className = '' }) {
  return (
    <div className={`card ${className}`}>
      {children}
    </div>
  )
}

// Insertion-history dates arrive already formatted as MM/DD/YYYY per the API
// contract, but tolerate a raw ISO (YYYY-MM-DD…) value too — fmt.date only
// parses ISO heads and would blank an already-formatted string, so pass those
// straight through.
function displayDate(val) {
  if (val == null || val === '') return '—'
  return /^\d{4}-\d{2}-\d{2}/.test(String(val)) ? fmt.date(val) : String(val)
}

function Field({ label, val }) {
  return (
    <div>
      <div className="text-[11px] text-gray-400 uppercase tracking-wide">{label}</div>
      <div className="text-gray-800 font-medium truncate">{val ?? '—'}</div>
    </div>
  )
}

// ─── Main modal ──────────────────────────────────────────────────────────────

export default function PelletRecallDetail({ recallId, onClose }) {
  const qc = useQueryClient()
  const [claimError, setClaimError] = useState(null)
  const [outcome, setOutcome] = useState('')
  const [notes, setNotes] = useState('')
  const [dialState, setDialState] = useState(null)   // null | 'ringing' | 'connected' | 'error'
  const [dialMsg, setDialMsg] = useState(null)
  const dialResetTimer = useRef(null)

  // ── Data fetch ──────────────────────────────────────────────────────────────
  const { data, isLoading } = useQuery({
    queryKey: ['pellet-recall', recallId],
    queryFn: () => api.get(`/pellets/recall/${recallId}`).then(r => r.data),
  })

  const recall = data?.recall
  const insertionHistory = data?.insertion_history || []
  const callerScript = data?.caller_script || ''
  const outcomes = data?.outcomes || []
  const history = data?.history || []
  const phone = recall?.cell_phone || recall?.primary_phone

  // ── Claim on mount / release on close ─────────────────────────────────────
  useEffect(() => {
    let cancelled = false
    let timer = null

    const claim = async () => {
      try {
        await api.post(`/pellets/recall/${recallId}/claim`)
        if (cancelled) return
        setClaimError(null)
        // Refresh claim every 4 minutes so a long call doesn't expire the lock
        timer = setTimeout(claim, 4 * 60 * 1000)
      } catch (err) {
        if (cancelled) return
        if (err?.response?.status === 409) {
          setClaimError(err.response?.data?.detail || 'Another user is working this recall.')
        }
        // Non-409: don't block the user
      }
    }
    claim()

    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
      if (dialResetTimer.current) clearTimeout(dialResetTimer.current)
      // Best-effort release on unmount — this is the single source of the
      // DELETE so handleClose (which only unmounts via the parent) doesn't
      // double-fire it.
      api.delete(`/pellets/recall/${recallId}/claim`).catch(() => {})
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recallId])

  // ── Close wrapper — unmounts the modal via the parent. The claim release
  // happens in the effect cleanup that the unmount triggers. ────────────────
  function handleClose() {
    onClose()
  }

  // ── Dial ───────────────────────────────────────────────────────────────────
  const dial = useMutation({
    mutationFn: () => api.post(`/pellets/recall/${recallId}/dial`).then(r => r.data),
    onMutate: () => { setDialState('ringing'); setDialMsg('Calling your RC extension…') },
    onSuccess: (d) => {
      setDialState('connected')
      setDialMsg(d.message || 'Pick up your phone — RC is connecting you.')
      dialResetTimer.current = setTimeout(() => { setDialState(null); setDialMsg(null) }, 8000)
    },
    onError: (err) => {
      setDialState('error')
      setDialMsg(err?.response?.data?.detail || 'Dial failed')
      dialResetTimer.current = setTimeout(() => { setDialState(null); setDialMsg(null) }, 6000)
    },
  })

  // ── Log outcome ────────────────────────────────────────────────────────────
  const logOutcome = useMutation({
    mutationFn: () => api.post(`/pellets/recall/${recallId}/outcome`, { outcome, notes }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['pellet-recall', recallId] })
      setOutcome('')
      setNotes('')
    },
    onError: (err) => {
      alert(err?.response?.data?.detail || 'Failed to log outcome.')
    },
  })

  const dialStateColor =
    dialState === 'ringing'   ? 'text-amber-700 bg-amber-50 border-amber-200' :
    dialState === 'connected' ? 'text-green-700 bg-green-50 border-green-200' :
    dialState === 'error'     ? 'text-red-700 bg-red-50 border-red-200' : ''

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40"
      onClick={handleClose}
    >
      <div
        className="relative bg-white rounded-lg shadow-xl w-full max-w-2xl max-h-[90vh] flex flex-col"
        onClick={e => e.stopPropagation()}
      >
        {/* ── Header ──────────────────────────────────────────────────────── */}
        <div className="sticky top-0 bg-white border-b border-border-subtle px-6 py-4 flex items-center justify-between z-10 rounded-t-lg">
          <h2 className="font-serif font-semibold text-ink text-[18px]">Update Recall</h2>
          <button onClick={handleClose} className="text-muted hover:text-ink" aria-label="Close">
            <X size={18} />
          </button>
        </div>

        {/* ── Scrollable body ─────────────────────────────────────────────── */}
        <div className="overflow-y-auto flex-1 p-6 space-y-4">

          {/* Claim conflict banner */}
          {claimError && (
            <div className="px-3 py-2 rounded border border-amber-300 bg-amber-50 text-[12px] text-amber-900 flex items-start gap-2">
              <Lock size={14} className="shrink-0 mt-0.5" />
              <div>
                <strong>Locked.</strong> {claimError} You can view this recall but dial + outcome may be blocked until they finish.
              </div>
            </div>
          )}

          {isLoading && (
            <div className="py-8 text-center text-muted text-sm">Loading…</div>
          )}

          {recall && (
            <>
              {/* ── Patient card ─────────────────────────────────────────── */}
              <Card className="!p-3">
                <div className="text-sm font-semibold text-gray-900">{recall.patient_name}</div>
                <div className="text-[12px] text-gray-600 mt-0.5">
                  Chart #{recall.chart_number}
                  {recall.dob && <> · DOB {fmt.date(recall.dob)}</>}
                </div>

                {phone && (
                  <div className="mt-2">
                    <button
                      type="button"
                      onClick={() => dial.mutate()}
                      disabled={dial.isPending}
                      className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-plum-100 hover:bg-plum-200 text-plum-700 rounded text-sm font-mono disabled:opacity-60"
                      title="Dial via RingCentral"
                    >
                      <Phone size={13} /> {phone}
                    </button>
                    {dialMsg && (
                      <div className={`text-[11px] mt-1 px-2 py-1 rounded border ${dialStateColor}`}>
                        {dialMsg}
                      </div>
                    )}
                  </div>
                )}

                <div className="grid grid-cols-2 gap-2 mt-3 text-[11px]">
                  <Field label="Last Visit" val={recall.last_visit ? fmt.date(recall.last_visit) : 'None on file'} />
                  <Field label="Recall Type" val={recall.recall_type || '—'} />
                  <Field label="Recall Due" val={recall.recall_due ? fmt.date(recall.recall_due) : '—'} />
                  <Field label="Attempts" val={recall.attempts ?? 0} />
                  <Field label="Insurance" val={recall.primary_insurance || '—'} />
                  <Field label="Email" val={recall.email || '—'} />
                </div>
              </Card>

              {/* ── Insertion History card ───────────────────────────────── */}
              <Card className="!p-3">
                <h3 className="text-sm font-semibold text-ink mb-2">Insertion History</h3>
                {insertionHistory.length === 0 ? (
                  <div className="text-xs text-gray-400 italic">No insertion history on file.</div>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full text-[11px]">
                      <thead>
                        <tr className="text-left text-gray-400 uppercase tracking-wide">
                          <th className="pb-1.5 pr-3 font-medium">Date</th>
                          <th className="pb-1.5 pr-3 font-medium">Location</th>
                          <th className="pb-1.5 pr-3 font-medium">Provider</th>
                          <th className="pb-1.5 pr-3 font-medium">Dosage</th>
                          <th className="pb-1.5 font-medium">Status</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-border-subtle">
                        {insertionHistory.map((row, i) => (
                          <tr key={i} className="text-gray-700">
                            <td className="py-1.5 pr-3 font-mono whitespace-nowrap">
                              {displayDate(row.date)}
                            </td>
                            <td className="py-1.5 pr-3">{row.location || '—'}</td>
                            <td className="py-1.5 pr-3">{row.provider || '—'}</td>
                            <td className="py-1.5 pr-3">{row.doses || '—'}</td>
                            <td className="py-1.5">{row.status || '—'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </Card>

              {/* ── Caller Script card ───────────────────────────────────── */}
              <Card className="!p-3 bg-plum-50/30 border-plum-100">
                <h3 className="text-sm font-semibold text-plum-700 mb-2">Caller Script</h3>
                {callerScript ? (
                  <div className="text-[12px] text-gray-800 leading-relaxed whitespace-pre-line">
                    {callerScript}
                  </div>
                ) : (
                  <div className="text-xs text-gray-400 italic">No script configured.</div>
                )}
              </Card>

              {/* ── Log Call Outcome card ────────────────────────────────── */}
              <Card>
                <h3 className="text-sm font-semibold text-ink mb-2">Log Call Outcome</h3>
                <div className="space-y-2">
                  <select
                    className="input text-sm w-full"
                    value={outcome}
                    onChange={e => setOutcome(e.target.value)}
                  >
                    <option value="">— Pick an outcome —</option>
                    {outcomes.map(o => (
                      <option key={o} value={o}>{o}</option>
                    ))}
                  </select>
                  <textarea
                    className="input text-sm w-full"
                    rows={3}
                    placeholder="Notes / next steps…"
                    value={notes}
                    onChange={e => setNotes(e.target.value)}
                  />
                  <div className="flex justify-end">
                    <button
                      className="btn-primary text-sm"
                      disabled={!outcome || logOutcome.isPending}
                      onClick={() => logOutcome.mutate()}
                    >
                      {logOutcome.isPending ? 'Saving…' : 'Log Outcome'}
                    </button>
                  </div>
                </div>
              </Card>

              {/* ── History card ─────────────────────────────────────────── */}
              <Card>
                <h3 className="text-sm font-semibold text-ink mb-2 flex items-center gap-1.5">
                  <Clock size={13} /> History ({history.length})
                </h3>
                {history.length === 0 ? (
                  <div className="text-xs text-gray-400 italic">No activity yet.</div>
                ) : (
                  <ul className="space-y-2">
                    {history.map(h => (
                      <li key={h.id} className="border-l-2 border-plum-200 pl-3 py-0.5 text-[11px]">
                        <div className="flex items-baseline gap-2 flex-wrap">
                          <span className="font-medium">
                            {(h.user_email || 'system').split('@')[0]}
                          </span>
                          <span className="text-gray-400">·</span>
                          <span className="text-gray-600">
                            {h.outcome || (h.event_type || '').replace(/_/g, ' ')}
                          </span>
                          <span className="text-gray-400 ml-auto whitespace-nowrap">
                            {fmt.dateTime(h.occurred_at)}
                          </span>
                        </div>
                        {h.notes && (
                          <div className="text-gray-700 mt-0.5 whitespace-pre-wrap">{h.notes}</div>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </Card>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
