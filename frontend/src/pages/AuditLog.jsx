import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Shield } from 'lucide-react'
import api, { fmt } from '../utils/api'

const ACTION_COLORS = {
  VIEW: 'bg-blue-50 text-blue-700',
  CREATE: 'bg-green-50 text-green-700',
  UPDATE: 'bg-yellow-50 text-yellow-700',
  DELETE: 'bg-red-50 text-red-700',
  EXPORT: 'bg-purple-50 text-purple-700',
  IMPORT: 'bg-teal-50 text-teal-700',
  GENERATE_EOB: 'bg-orange-50 text-orange-700',
  GENERATE_APPEAL: 'bg-indigo-50 text-indigo-700',
}

export default function AuditLog() {
  const [action, setAction] = useState('')
  const [resource, setResource] = useState('')
  const [page, setPage] = useState(1)

  const { data, isLoading } = useQuery({
    queryKey: ['audit', action, resource, page],
    queryFn: () => api.get('/audit', { params: { action, resource_type: resource, page, per_page: 100 } }).then(r => r.data),
  })

  return (
    <div className="p-6">
      <div className="flex items-center gap-3 mb-6">
        <Shield size={22} className="text-primary-500" />
        <div>
          <h1 className="text-2xl font-bold text-gray-900">HIPAA Audit Log</h1>
          <p className="text-gray-500 text-sm">{data?.total?.toLocaleString() || 0} events · All PHI access and modifications recorded</p>
        </div>
      </div>

      <div className="card mb-4 flex gap-3 flex-wrap">
        <select className="input w-44" aria-label="Action filter" value={action} onChange={e => setAction(e.target.value)}>
          <option value="">All Actions</option>
          {['VIEW','CREATE','UPDATE','DELETE','EXPORT','IMPORT','GENERATE_EOB','GENERATE_APPEAL'].map(a => (
            <option key={a} value={a}>{a}</option>
          ))}
        </select>
        <select className="input w-36" aria-label="Resource filter" value={resource} onChange={e => setResource(e.target.value)}>
          <option value="">All Resources</option>
          {['patient','claim','denial','appeal','era_file','ledger','file'].map(r => (
            <option key={r} value={r}>{r}</option>
          ))}
        </select>
      </div>

      <div className="card p-0 overflow-hidden">
        <table className="w-full">
          <thead className="bg-gray-50 border-b border-gray-200">
            <tr>
              <th className="table-th">Timestamp</th>
              <th className="table-th">Action</th>
              <th className="table-th">Resource</th>
              <th className="table-th">Resource ID</th>
              <th className="table-th">Patient ID</th>
              <th className="table-th">User</th>
              <th className="table-th">Description</th>
              <th className="table-th">Status</th>
            </tr>
          </thead>
          <tbody>
            {isLoading && <tr><td colSpan={8} className="table-td text-center py-8 text-gray-400">Loading…</td></tr>}
            {data?.logs?.map(log => (
              <tr key={log.id} className="table-row text-xs">
                <td className="table-td font-mono text-gray-500 whitespace-nowrap">{fmt.dateTime(log.timestamp)}</td>
                <td className="table-td">
                  <span className={`badge text-xs ${ACTION_COLORS[log.action] || 'badge-pending'}`}>{log.action}</span>
                </td>
                <td className="table-td text-gray-600">{log.resource_type}</td>
                <td className="table-td font-mono text-gray-400 text-xs max-w-[100px] truncate">{log.resource_id?.substring(0, 8) || '—'}</td>
                <td className="table-td font-mono text-gray-400 text-xs">{log.patient_id?.substring(0, 8) || '—'}</td>
                <td className="table-td">{log.user_name || log.user_id || 'system'}</td>
                <td className="table-td text-gray-500 whitespace-normal break-words max-w-md">{log.description || '—'}</td>
                <td className="table-td">
                  <span className={log.status === 'success' ? 'text-green-600' : 'text-red-600'}>
                    {log.status}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="mt-4 text-xs text-gray-400">
        This audit log satisfies HIPAA Security Rule §164.312(b) — Audit Controls.
        All access to Protected Health Information (PHI) is logged with user, timestamp, and action.
      </div>
    </div>
  )
}
