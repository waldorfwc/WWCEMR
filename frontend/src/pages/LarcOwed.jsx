import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { ArrowLeft, Users, Clock, CheckCircle2 } from 'lucide-react'
import api, { fmt } from '../utils/api'
import EmptyState from '../components/EmptyState'


export default function LarcOwed() {
  const qc = useQueryClient()
  const [includeResolved, setIncludeResolved] = useState(false)

  const { data: rows = [] } = useQuery({
    queryKey: ['larc-owed', includeResolved],
    queryFn: () => api.get('/larc/owed',
                            { params: { include_resolved: includeResolved } }).then(r => r.data),
  })

  return (
    <div>
      <Link to="/larc" className="text-[12px] text-muted hover:underline flex items-center gap-1 mb-2">
        <ArrowLeft size={12} /> LARC dashboard
      </Link>
      <div className="flex items-baseline justify-between mb-3">
        <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
          <Users size={22} className="text-plum-700" />
          Owed list
        </h1>
        <label className="flex items-center gap-1.5 text-xs text-gray-700">
          <input type="checkbox" checked={includeResolved}
                 onChange={e => setIncludeResolved(e.target.checked)} />
          Include resolved
        </label>
      </div>
      <p className="text-sm text-gray-500 mb-4">
        Patients whose device was reallocated (after 6 months unused or within 365 days of expiry).
        They have until the original device's expiration date to claim a fresh one.
      </p>

      {rows.length === 0 ? (
        <div className="card">
          <EmptyState
            icon={CheckCircle2}
            title={includeResolved ? 'No owed patients on file' : 'No active owed patients'}
            body={includeResolved
              ? 'Nothing in the system yet.'
              : 'Everyone has been resolved — toggle "Include resolved" to see history.'}
          />
        </div>
      ) : (
        <div className="card !p-0 overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-plum-50">
              <tr>
                <th className="table-th">Patient</th>
                <th className="table-th">Owed since</th>
                <th className="table-th">Expires</th>
                <th className="table-th">Status</th>
                <th className="table-th text-right">Resolve</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {rows.map(o => <OwedRow key={o.id} o={o} qc={qc} />)}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}


function OwedRow({ o, qc }) {
  const [resolution, setResolution] = useState('reallocated')
  const [notes, setNotes] = useState('')
  const [open, setOpen] = useState(false)

  const resolve = useMutation({
    mutationFn: () => api.post(`/larc/owed/${o.id}/resolve`, {
      resolution, notes: notes || null,
    }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['larc-owed'] })
      setOpen(false)
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Resolve failed'),
  })

  const isResolved = !!o.resolved_at
  const daysLeft = o.days_until_expiry
  const expired = daysLeft != null && daysLeft < 0
  const stale = daysLeft != null && daysLeft >= 0 && daysLeft < 30

  return (
    <>
      <tr className={isResolved ? 'opacity-50' : ''}>
        <td className="table-td">
          <div className="font-medium">{o.patient_name}</div>
          <div className="text-[10px] text-gray-500 font-mono">{o.chart_number}</div>
        </td>
        <td className="table-td text-[11px]">
          {fmt.date(o.owed_since)}
          <div className="text-[10px] text-gray-500">
            {Math.floor((Date.now() - new Date(o.owed_since).getTime()) / 86400000)} days ago
          </div>
        </td>
        <td className="table-td text-[11px]">
          {o.expires_at ? (
            <>
              {fmt.date(o.expires_at)}
              <div className={`text-[10px] ${
                expired ? 'text-red-700 font-semibold' :
                stale ? 'text-amber-700' : 'text-gray-500'
              }`}>
                {expired ? `${-daysLeft}d past` :
                 stale ? `${daysLeft}d left` :
                 `${daysLeft}d left`}
              </div>
            </>
          ) : <span className="text-gray-400">—</span>}
        </td>
        <td className="table-td">
          {isResolved ? (
            <span className="text-[11px] uppercase bg-gray-100 text-gray-700 px-1.5 py-0.5 rounded">
              {o.resolution}
            </span>
          ) : (
            <span className="text-[11px] uppercase bg-amber-100 text-amber-700 px-1.5 py-0.5 rounded">
              awaiting
            </span>
          )}
        </td>
        <td className="table-td text-right">
          {!isResolved && (
            <button className="text-[11px] text-plum-700 hover:underline"
                    onClick={() => setOpen(o => !o)}>
              {open ? 'cancel' : 'resolve'}
            </button>
          )}
        </td>
      </tr>
      {open && (
        <tr>
          <td colSpan={5} className="bg-plum-50/30 p-3">
            <div className="space-y-2">
              <div>
                <label className="text-[11px] uppercase text-gray-500 block mb-1">Resolution</label>
                <select className="input text-sm" value={resolution}
                        onChange={e => setResolution(e.target.value)}>
                  <option value="reallocated">Reallocated — patient is being given a fresh device</option>
                  <option value="declined">Declined — patient no longer wants the device</option>
                  <option value="expired">Expired — too long, original device past expiry</option>
                </select>
              </div>
              <div>
                <label className="text-[11px] uppercase text-gray-500 block mb-1">Notes</label>
                <input className="input text-sm w-full" value={notes}
                       onChange={e => setNotes(e.target.value)}
                       placeholder="Optional context for the audit log" />
              </div>
              {resolution === 'reallocated' && (
                <div className="text-[11px] text-plum-700 bg-plum-50 border border-plum-200 rounded px-2 py-1">
                  Before clicking Save, create a new LARC request for this patient on the dashboard
                  (so a fresh device gets bound). Then resolve here.
                </div>
              )}
              <button className="btn-primary text-[11px]"
                      onClick={() => resolve.mutate()}
                      disabled={resolve.isPending}>
                {resolve.isPending ? 'Saving…' : 'Resolve'}
              </button>
            </div>
          </td>
        </tr>
      )}
    </>
  )
}
