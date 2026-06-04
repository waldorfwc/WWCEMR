import { useState, useMemo } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'
import {
  ArrowLeft, ListPlus, Phone, Trash2, Calendar, AlertCircle, X, Copy, Check,
} from 'lucide-react'
import api, { fmt } from '../utils/api'
import { useFacilities } from '../hooks/useFacilities'

const URGENCY_TONE = {
  routine:   'bg-gray-100 text-gray-700',
  expedited: 'bg-amber-100 text-amber-800',
  urgent:    'bg-red-100 text-red-700',
}
const URGENCY_LABEL = {
  routine: 'Routine', expedited: 'Expedited', urgent: 'Urgent',
}
const URGENCY_RANK = { urgent: 0, expedited: 1, routine: 2 }


export default function SurgeryWaitlist() {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const { labelOf } = useFacilities()
  const [facilityFilter, setFacilityFilter] = useState('')
  const [matchingFor, setMatchingFor] = useState(null)   // block_day_id
  const [sortKey, setSortKey] = useState('urgency')   // 'urgency' | 'notice' | 'facility'
  const [sortDir, setSortDir] = useState('asc')

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

  const list = useMemo(() => {
    const rows = [...(data?.waitlist || [])]
    rows.sort((a, b) => {
      let av, bv
      if (sortKey === 'urgency')  { av = URGENCY_RANK[a.urgency] ?? 99; bv = URGENCY_RANK[b.urgency] ?? 99 }
      else if (sortKey === 'notice') { av = a.advance_notice_days ?? 0; bv = b.advance_notice_days ?? 0 }
      else                        { av = (a.facility || ''); bv = (b.facility || '') }
      if (av < bv) return sortDir === 'asc' ? -1 : 1
      if (av > bv) return sortDir === 'asc' ?  1 : -1
      return 0
    })
    return rows
  }, [data, sortKey, sortDir])

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

  function clickSort(key) {
    if (sortKey === key) {
      setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    } else {
      setSortKey(key)
      setSortDir('asc')
    }
  }

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
              {fmt.date(d.block_date)} · {labelOf(d.facility)}
            </button>
          ))}
          {openDays.length === 0 && (
            <span className="text-xs text-muted italic">No open block days right now.</span>
          )}
        </div>
      </div>

      {list.length === 0 ? (
        <div className="card text-sm text-gray-500 italic">
          No one on the waitlist. Use the "Add to waitlist" button on a surgery's detail page.
        </div>
      ) : (
        <div className="card !p-0 overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-[11px] uppercase text-gray-500">
              <tr>
                <th className="text-left px-4 py-2">Patient</th>
                <th className="text-left px-3 py-2">
                  <button onClick={() => clickSort('notice')}>
                    Notice {sortKey === 'notice' && (sortDir === 'asc' ? '↑' : '↓')}
                  </button>
                </th>
                <th className="text-left px-3 py-2">Type</th>
                <th className="text-left px-3 py-2">
                  <button onClick={() => clickSort('facility')}>
                    Location {sortKey === 'facility' && (sortDir === 'asc' ? '↑' : '↓')}
                  </button>
                </th>
                <th className="text-left px-3 py-2">
                  <button onClick={() => clickSort('urgency')}>
                    Urgency {sortKey === 'urgency' && (sortDir === 'asc' ? '↑' : '↓')}
                  </button>
                </th>
                <th className="px-4 py-2 w-[120px] text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {list.map(w => (
                <tr key={w.id} className="border-t border-border-subtle hover:bg-gray-50">
                  <td className="px-4 py-3">
                    <Link to={`/surgery/${w.surgery_id}`} className="text-plum-700 hover:underline">
                      {w.patient_name}
                    </Link>
                  </td>
                  <td className="px-3 py-3 text-[12px]">{w.advance_notice_days}d</td>
                  <td className="px-3 py-3 text-[12px]">{w.procedure_name || '—'}</td>
                  <td className="px-3 py-3 text-[12px]">{labelOf(w.facility) || '—'}</td>
                  <td className="px-3 py-3">
                    <span className={`text-[11px] px-2 py-0.5 rounded ${URGENCY_TONE[w.urgency] || URGENCY_TONE.routine}`}>
                      {URGENCY_LABEL[w.urgency] || 'Routine'}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-right">
                    <button className="text-[11px] px-2 py-1 rounded border border-gray-200 hover:bg-plum-50 inline-flex items-center gap-1"
                            onClick={() => {
                              if (confirm(`Remove ${w.patient_name} from the waitlist?`)) {
                                removeFromWaitlist.mutate(w.surgery_id)
                              }
                            }}
                            title="Remove from waitlist">
                      <Trash2 size={11} /> Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
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
            <h2 className="font-serif font-semibold text-ink text-[18px]">Waitlist Matches</h2>
            {data?.block_day && (
              <div className="text-[11px] text-muted">
                {fmt.date(data.block_day.block_date)} · {labelOf(data.block_day.facility)} · {data.block_day.block_kind.replace(/_/g, ' ')}
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
