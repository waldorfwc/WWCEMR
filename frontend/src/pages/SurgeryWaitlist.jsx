import { useState, useMemo } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'
import {
  ArrowLeft, ListPlus, Phone, Trash2, Calendar, AlertCircle, X, Copy, Check,
} from 'lucide-react'
import api, { fmt } from '../utils/api'


const FACILITY_LABEL = {
  medstar: 'MedStar',
  crmc:    'CRMC',
  office:  'Office',
}


export default function SurgeryWaitlist() {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const [facilityFilter, setFacilityFilter] = useState('')
  const [matchingFor, setMatchingFor] = useState(null)   // block_day_id

  const { data, isLoading } = useQuery({
    queryKey: ['surgery-waitlist', facilityFilter],
    queryFn: () => api.get('/surgery/admin/waitlist', {
      params: { facility: facilityFilter || undefined },
    }).then(r => r.data),
  })

  const { data: blockDays } = useQuery({
    queryKey: ['surgery-block-days-for-match'],
    queryFn: () => api.get('/surgery/admin/block-days?days=60').then(r => r.data),
  })

  const removeFromWaitlist = useMutation({
    mutationFn: (surgery_id) => api.delete(`/surgery/${surgery_id}/waitlist`).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['surgery-waitlist'] }),
  })

  const list = data?.waitlist || []

  // Group by facility (or 'multi' for multi-eligible)
  const grouped = useMemo(() => {
    const out = {}
    for (const w of list) {
      const fac = w.eligible_facilities.length === 1
                  ? w.eligible_facilities[0]
                  : 'multi'
      if (!out[fac]) out[fac] = []
      out[fac].push(w)
    }
    return out
  }, [list])

  // Days with open capacity (not full) — used for the "Find matches for this day" picker
  const openDays = (blockDays?.days || []).filter(d => {
    const slots = d.slots || []
    if (d.facility === 'medstar') {
      const r180 = slots.filter(s => s.procedure_kind === 'robotic_180').length
      const r240 = slots.filter(s => s.procedure_kind === 'robotic_240').length
      return r180 < 3 && r240 < 2
    }
    if (d.facility === 'crmc') {
      const minor = slots.filter(s => s.procedure_kind === 'minor').length
      const major = slots.filter(s => s.procedure_kind === 'major').length
      return (minor < 6 && major === 0) || (major < 2 && minor === 0)
    }
    return slots.length < 8   // office capacity rough heuristic
  })

  if (isLoading) return <div className="p-6 text-gray-400">Loading…</div>

  return (
    <div>
      <div className="flex items-baseline justify-between mb-4">
        <div>
          <Link to="/surgery" className="text-[12px] text-muted hover:underline flex items-center gap-1 mb-1">
            <ArrowLeft size={12} /> Surgery dashboard
          </Link>
          <h1 className="font-serif font-semibold text-ink text-[22px] m-0">Waitlist</h1>
          <p className="text-muted text-[12px] mt-0.5">
            {list.length} patient{list.length === 1 ? '' : 's'} waiting for an earlier slot. Pick a day below to find who could fill it.
          </p>
        </div>
        <select className="input text-sm" value={facilityFilter}
                onChange={e => setFacilityFilter(e.target.value)}>
          <option value="">All facilities</option>
          <option value="medstar">MedStar</option>
          <option value="crmc">CRMC</option>
          <option value="office">Office</option>
        </select>
      </div>

      {/* Find-matches strip */}
      <div className="card mb-4 !p-3">
        <div className="text-[10px] uppercase tracking-wide text-gray-500 mb-2">
          Find matches for an open block day
        </div>
        <div className="flex flex-wrap gap-1.5">
          {openDays.slice(0, 12).map(d => (
            <button key={d.id}
                    className="text-[11px] px-2 py-1 rounded border border-plum-200 bg-white hover:bg-plum-50 text-plum-700"
                    onClick={() => setMatchingFor(d.id)}>
              {fmt.date(d.block_date)} · {FACILITY_LABEL[d.facility]}
            </button>
          ))}
          {openDays.length === 0 && (
            <span className="text-xs text-muted italic">No open block days right now.</span>
          )}
        </div>
      </div>

      {Object.keys(grouped).length === 0 ? (
        <div className="card text-sm text-gray-500 italic">
          No one on the waitlist. Use the "Add to waitlist" button on a surgery's detail page.
        </div>
      ) : (
        <div className="space-y-3">
          {Object.entries(grouped).map(([fac, items]) => (
            <div key={fac}>
              <h2 className="text-sm font-semibold text-gray-700 mb-2">
                {fac === 'multi' ? 'Multi-facility eligible' : FACILITY_LABEL[fac]}
                <span className="text-xs text-gray-500 font-normal ml-2">({items.length})</span>
              </h2>
              <div className="card !p-0 overflow-hidden">
                <table className="w-full text-xs">
                  <thead className="bg-plum-50 text-gray-600 text-[10px] uppercase tracking-wide">
                    <tr>
                      <th className="text-left px-3 py-1">Patient</th>
                      <th className="text-left px-2 py-1">Procedure</th>
                      <th className="text-left px-2 py-1">Phone</th>
                      <th className="text-left px-2 py-1">Notice (days)</th>
                      <th className="text-left px-2 py-1">Waiting since</th>
                      <th className="text-right px-3 py-1">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.map(w => (
                      <tr key={w.waitlist_id} className="border-t border-gray-100 hover:bg-plum-50/30">
                        <td className="px-3 py-1.5">
                          <button className="font-medium text-plum-700 hover:underline"
                                  onClick={() => navigate(`/surgery/${w.surgery_id}`)}>
                            {w.patient_name}
                          </button>
                          <div className="text-[10px] text-gray-500 font-mono">{w.chart_number}</div>
                        </td>
                        <td className="px-2 py-1.5 max-w-[260px] truncate">
                          {(w.procedure_descriptions || []).join(', ') || '—'}
                          {w.procedure_classification && (
                            <div className="text-[10px] text-gray-500 capitalize">
                              {w.procedure_classification.replace(/_/g, ' ')}
                            </div>
                          )}
                        </td>
                        <td className="px-2 py-1.5 font-mono">{w.phone || '—'}</td>
                        <td className="px-2 py-1.5">{w.advance_notice_days}</td>
                        <td className="px-2 py-1.5">{fmt.date(w.signed_up_at?.slice(0, 10))}</td>
                        <td className="px-3 py-1.5 text-right">
                          <button className="text-[11px] text-red-700 hover:underline flex items-center gap-1 ml-auto"
                                  onClick={() => {
                                    if (confirm(`Remove ${w.patient_name} from the waitlist?`)) {
                                      removeFromWaitlist.mutate(w.surgery_id)
                                    }
                                  }}>
                            <Trash2 size={11} /> Remove
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ))}
        </div>
      )}

      {matchingFor && (
        <MatchesDrawer blockDayId={matchingFor}
                        onClose={() => setMatchingFor(null)} />
      )}
    </div>
  )
}


export function MatchesDrawer({ blockDayId, onClose }) {
  const qc = useQueryClient()
  const [copied, setCopied] = useState(false)

  const { data, isLoading } = useQuery({
    queryKey: ['waitlist-matches', blockDayId],
    queryFn: () => api.get('/surgery/admin/waitlist-matches', {
      params: { block_day_id: blockDayId },
    }).then(r => r.data),
  })

  const claim = useMutation({
    mutationFn: (waitlist_id) => api.post(`/surgery/admin/waitlist/${waitlist_id}/claim`,
      { block_day_id: blockDayId }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['waitlist-matches'] })
      qc.invalidateQueries({ queryKey: ['surgery-waitlist'] })
      qc.invalidateQueries({ queryKey: ['surgery-list'] })
    },
  })

  function copyBlast() {
    if (!data?.klara_blast) return
    navigator.clipboard.writeText(data.klara_blast)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/30" />
      <div className="relative w-full max-w-2xl bg-white shadow-xl overflow-y-auto"
           onClick={e => e.stopPropagation()}>
        <div className="sticky top-0 bg-white border-b border-border-subtle px-5 py-4 flex items-center justify-between z-10">
          <div>
            <h2 className="font-serif font-semibold text-ink text-[18px]">Waitlist matches</h2>
            {data?.block_day && (
              <div className="text-[11px] text-muted">
                {fmt.date(data.block_day.block_date)} · {FACILITY_LABEL[data.block_day.facility]} · {data.block_day.block_kind.replace(/_/g, ' ')}
              </div>
            )}
          </div>
          <button onClick={onClose} className="text-muted hover:text-ink"><X size={18} /></button>
        </div>

        {isLoading || !data ? (
          <div className="p-6 text-muted">Finding matches…</div>
        ) : (
          <div className="p-5 space-y-4">
            {data.matches.length === 0 ? (
              <div className="bg-amber-50 border border-amber-200 rounded p-3 text-sm">
                No waitlisters match this slot (advance-notice / facility / procedure mismatch).
              </div>
            ) : (
              <>
                <div>
                  <div className="text-[10px] uppercase tracking-wide text-gray-500 mb-1">
                    Klara blast template
                  </div>
                  <pre className="text-xs text-gray-800 whitespace-pre-wrap bg-gray-50 border border-gray-200 rounded p-3 font-sans">
{data.klara_blast}
                  </pre>
                  <button className="btn-secondary text-xs flex items-center gap-1 mt-2"
                          onClick={copyBlast}>
                    <Copy size={11} /> {copied ? 'Copied!' : 'Copy to clipboard'}
                  </button>
                </div>

                <div>
                  <div className="text-[10px] uppercase tracking-wide text-gray-500 mb-1">
                    {data.matches.length} eligible patient{data.matches.length === 1 ? '' : 's'}
                    <span className="text-gray-400 ml-1">— ranked by waiting time</span>
                  </div>
                  <ul className="space-y-2">
                    {data.matches.map((m, i) => (
                      <li key={m.waitlist_id}
                          className="bg-white border border-gray-200 rounded p-3 flex items-baseline justify-between gap-3">
                        <div className="min-w-0">
                          <div className="text-sm font-semibold text-gray-900">
                            {i + 1}. {m.patient_name}
                            {!m.balance_clear && (
                              <span className="ml-2 text-[10px] bg-amber-100 text-amber-800 px-1.5 py-0.5 rounded">
                                balance due
                              </span>
                            )}
                          </div>
                          <div className="text-[11px] text-gray-600">
                            <span className="font-mono">{m.chart_number}</span>
                            {m.phone && <> · <a href={`tel:${m.phone}`} className="text-plum-700">{m.phone}</a></>}
                            <span className="ml-2 text-gray-500">notice {m.advance_notice_days}d</span>
                          </div>
                          {m.procedure_descriptions?.length > 0 && (
                            <div className="text-[10px] text-gray-500 mt-0.5 truncate">
                              {m.procedure_descriptions.join(', ')}
                            </div>
                          )}
                        </div>
                        <button className="btn-primary text-xs flex items-center gap-1 shrink-0"
                                onClick={() => {
                                  if (confirm(`Confirm ${m.patient_name} is taking this slot?`)) {
                                    claim.mutate(m.waitlist_id)
                                  }
                                }}
                                disabled={claim.isPending}>
                          <Check size={11} /> Patient claimed
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
