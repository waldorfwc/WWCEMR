import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { ArrowLeft, AlertTriangle, Check, X } from 'lucide-react'
import api, { fmt } from '../utils/api'


export default function LarcCheckouts() {
  const qc = useQueryClient()
  const { data: rows = [] } = useQuery({
    queryKey: ['larc-pending-checkouts'],
    queryFn: () => api.get('/larc/checkouts/pending').then(r => r.data),
    refetchInterval: 30_000,
  })

  return (
    <div>
      <Link to="/larc" className="text-[12px] text-muted hover:underline flex items-center gap-1 mb-2">
        <ArrowLeft size={12} /> LARC dashboard
      </Link>
      <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2 mb-3">
        <AlertTriangle size={22} className="text-amber-700" />
        Pending checkout approvals
      </h1>
      <p className="text-sm text-gray-500 mb-4">
        Requests flagged by the auto-approval gates. Approve or deny each one.
      </p>
      {rows.length === 0 ? (
        <div className="card text-xs text-gray-400 italic">No pending requests.</div>
      ) : (
        <div className="space-y-2">
          {rows.map(c => <PendingRow key={c.id} c={c} qc={qc} />)}
        </div>
      )}
    </div>
  )
}


function PendingRow({ c, qc }) {
  const [denialReason, setDenialReason] = useState('')
  const decide = useMutation({
    mutationFn: ({ approve, reason }) => api.post(`/larc/checkouts/${c.id}/decide`,
                                                     { approve, denial_reason: reason || null }).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['larc-pending-checkouts'] }),
    onError: (e) => alert(e?.response?.data?.detail || 'Failed'),
  })
  return (
    <div className="card !p-3">
      <div className="flex items-baseline justify-between gap-3">
        <div>
          <div className="font-medium text-sm">{c.patient_name}</div>
          <div className="text-[11px] text-gray-500">
            Chart {c.chart_number} · device <span className="font-mono">{c.device_our_id}</span> ({c.device_type})
          </div>
          <div className="text-[11px] text-gray-500 mt-0.5">
            Requested by {c.requested_by?.split('@')[0]} at {fmt.date(c.requested_at.slice(0,10))}{' '}
            {c.requested_at.slice(11, 16)}
            {c.given_to && <> · giving to {c.given_to}</>}
          </div>
        </div>
        <div className="flex items-center gap-1 shrink-0">
          <button className="btn-primary text-[11px] flex items-center gap-1"
                  onClick={() => decide.mutate({ approve: true })}
                  disabled={decide.isPending}>
            <Check size={11} /> Approve
          </button>
        </div>
      </div>
      <div className="mt-2 flex items-center gap-2">
        <input className="input text-[11px] flex-1"
               placeholder="Denial reason (required to deny)"
               value={denialReason}
               onChange={e => setDenialReason(e.target.value)} />
        <button className="text-[11px] border border-red-300 text-red-700 hover:bg-red-50 px-2 py-1 rounded flex items-center gap-1"
                onClick={() => decide.mutate({ approve: false, reason: denialReason })}
                disabled={!denialReason.trim() || decide.isPending}>
          <X size={11} /> Deny
        </button>
      </div>
    </div>
  )
}
