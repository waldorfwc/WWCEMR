import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { ArrowLeft, Activity, ChevronLeft, ChevronRight } from 'lucide-react'
import api, { fmt } from '../utils/api'


function todayIso() {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`
}

function shiftDay(iso, n) {
  const [y, m, d] = iso.split('-').map(x => parseInt(x))
  const dt = new Date(y, m-1, d)
  dt.setDate(dt.getDate() + n)
  return `${dt.getFullYear()}-${String(dt.getMonth()+1).padStart(2,'0')}-${String(dt.getDate()).padStart(2,'0')}`
}


export default function LarcEodReport() {
  const [date, setDate] = useState(todayIso())
  const { data, isLoading } = useQuery({
    queryKey: ['larc-eod', date],
    queryFn: () => api.get('/larc/reports/eod', { params: { date } }).then(r => r.data),
  })

  return (
    <div>
      <Link to="/larc" className="text-[12px] text-muted hover:underline flex items-center gap-1 mb-2">
        <ArrowLeft size={12} /> LARC dashboard
      </Link>
      <div className="flex items-baseline justify-between flex-wrap gap-2 mb-3">
        <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
          <Activity size={22} className="text-plum-700" />
          End-of-day reconciliation
        </h1>
        <div className="flex items-center gap-1">
          <button onClick={() => setDate(shiftDay(date, -1))}
                  className="btn-secondary text-xs"><ChevronLeft size={12}/></button>
          <input type="date" className="input text-sm" aria-label="Report date" value={date}
                 onChange={e => setDate(e.target.value)} />
          <button onClick={() => setDate(shiftDay(date, 1))}
                  className="btn-secondary text-xs"><ChevronRight size={12}/></button>
          <button onClick={() => setDate(todayIso())}
                  className="btn-secondary text-xs ml-1">Today</button>
        </div>
      </div>
      <p className="text-sm text-gray-500 mb-4">
        Match this report against the physical cabinet at end-of-day. Any device in the report
        but not in the cabinet (or vice versa) needs investigation before staff leaves.
      </p>

      {/* Summary stats */}
      {data && (
        <div className="grid grid-cols-2 md:grid-cols-6 gap-2 mb-4">
          <Stat label="Checkouts" v={data.summary.checkouts_total} tone="violet" />
          <Stat label="Approved" v={data.summary.checkouts_approved} tone="green" />
          <Stat label="Denied" v={data.summary.checkouts_denied} tone="red" />
          <Stat label="Pending" v={data.summary.checkouts_pending} tone="amber" />
          <Stat label="Inserted" v={data.summary.inserted_total} tone="blue" />
          <Stat label={`Lost ($${data.loss_total?.toFixed(2) || '0.00'})`}
                v={data.summary.lost_total} tone="red" />
        </div>
      )}

      {isLoading && <div className="text-gray-400 italic">Loading…</div>}

      {data && (
        <>
          <Section title={`Checkouts (${data.checkouts.length})`}>
            {data.checkouts.length === 0 ? <Empty>No checkouts today.</Empty> : (
              <table className="w-full text-sm">
                <thead className="bg-plum-50">
                  <tr>
                    <th className="table-th">Time</th>
                    <th className="table-th">Patient</th>
                    <th className="table-th">Device</th>
                    <th className="table-th">Requested by</th>
                    <th className="table-th">Status</th>
                    <th className="table-th">Outcome</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {data.checkouts.map(c => (
                    <tr key={c.checkout_id}>
                      <td className="table-td text-[11px]">{fmt.time(c.requested_at)}</td>
                      <td className="table-td">{c.patient_name}</td>
                      <td className="table-td font-mono text-[11px]">
                        {c.device_our_id}
                        <div className="text-[10px] text-gray-500">{c.device_type}</div>
                      </td>
                      <td className="table-td text-[11px]">{c.requested_by?.split('@')[0]}</td>
                      <td className="table-td">
                        <span className={`text-[10px] uppercase px-1.5 py-0.5 rounded ${
                          c.approval_status === 'approved' ? 'bg-green-100 text-green-700' :
                          c.approval_status === 'denied' ? 'bg-red-100 text-red-700' :
                          'bg-amber-100 text-amber-700'
                        }`}>{c.approval_status}</span>
                      </td>
                      <td className="table-td text-[11px]">
                        {c.outcome ? c.outcome.replace(/_/g, ' ') :
                          <span className="text-amber-700">awaiting</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Section>

          <Section title={`Inserted (${data.inserted.length})`}>
            {data.inserted.length === 0 ? <Empty>No insertions today.</Empty> : (
              <ul className="text-sm space-y-1">
                {data.inserted.map(a => (
                  <li key={a.assignment_id} className="flex items-baseline gap-2 px-2 py-1">
                    <span className="text-[11px] text-gray-500 w-12">
                      {fmt.time(a.inserted_at)}
                    </span>
                    <Link to={`/larc/assignments/${a.assignment_id}`}
                          className="font-medium text-plum-700 hover:underline">
                      {a.patient_name}
                    </Link>
                    <span className="text-[11px] text-gray-500">
                      · <span className="font-mono">{a.device_our_id}</span> · {a.device_type}
                    </span>
                    <span className="text-[11px] text-gray-500 ml-auto">
                      by {a.inserted_by?.split('@')[0]}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </Section>

          <Section title={`Outcome / return events (${data.outcome_events.length})`}>
            {data.outcome_events.length === 0 ? <Empty>No outcome / return events today.</Empty> : (
              <ul className="text-sm space-y-1">
                {data.outcome_events.map((e, ix) => (
                  <li key={ix} className="flex items-baseline gap-2 px-2 py-1 text-[12px]">
                    <span className="text-[11px] text-gray-500 w-12">
                      {fmt.time(e.occurred_at)}
                    </span>
                    <code className="text-[10px] text-plum-700 w-44 truncate shrink-0">{e.action}</code>
                    <span className="flex-1">{e.summary}</span>
                    <span className="text-[10px] text-gray-500">{e.actor?.split('@')[0]}</span>
                  </li>
                ))}
              </ul>
            )}
          </Section>

          {data.lost_devices.length > 0 && (
            <Section title={`Lost devices (${data.lost_devices.length}) — $${data.loss_total.toFixed(2)} total`}>
              <ul className="text-sm space-y-1">
                {data.lost_devices.map(d => (
                  <li key={d.device_id} className="flex items-baseline gap-2 px-2 py-1 bg-red-50/40 rounded">
                    <span className="font-mono">{d.our_id}</span>
                    <span className="text-[11px] text-gray-500">· {d.device_type}</span>
                    <span className="font-mono ml-auto text-red-700">
                      ${d.loss_value || '0.00'}
                    </span>
                  </li>
                ))}
              </ul>
            </Section>
          )}
        </>
      )}
    </div>
  )
}


function Section({ title, children }) {
  return (
    <div className="card !p-0 mb-3 overflow-hidden">
      <div className="bg-plum-50 px-3 py-1.5 text-sm font-semibold text-gray-800 border-b border-border-subtle">
        {title}
      </div>
      <div className="p-2">{children}</div>
    </div>
  )
}


function Empty({ children }) {
  return <div className="text-xs text-gray-400 italic px-2 py-1">{children}</div>
}


function Stat({ label, v, tone }) {
  const tones = {
    violet: 'bg-violet-50 border-violet-200 text-violet-800',
    green:  'bg-green-50 border-green-200 text-green-800',
    red:    'bg-red-50 border-red-200 text-red-800',
    amber:  'bg-amber-50 border-amber-200 text-amber-800',
    blue:   'bg-blue-50 border-blue-200 text-blue-800',
  }
  return (
    <div className={`card border ${tones[tone]} !p-2`}>
      <div className="text-[10px] uppercase tracking-wide opacity-80">{label}</div>
      <div className="text-xl font-bold mt-0.5">{v}</div>
    </div>
  )
}
