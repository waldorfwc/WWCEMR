import { useQuery } from '@tanstack/react-query'
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell } from 'recharts'
import { AlertTriangle, CheckCircle, Clock, DollarSign, FileText, TrendingUp } from 'lucide-react'
import api, { fmt } from '../utils/api'

function StatCard({ label, value, sub, icon: Icon, color = 'text-primary-500' }) {
  return (
    <div className="stat-card">
      <div className="flex items-start justify-between">
        <div>
          <div className="text-xs text-gray-500 font-medium uppercase tracking-wide">{label}</div>
          <div className={`text-2xl font-bold mt-1 ${color}`}>{value}</div>
          {sub && <div className="text-xs text-gray-400 mt-0.5">{sub}</div>}
        </div>
        <div className={`p-2 rounded-lg bg-gray-50 ${color}`}>
          <Icon size={20} />
        </div>
      </div>
    </div>
  )
}

const STATUS_COLORS = {
  paid: '#2E7D32',
  denied: '#C62828',
  partial: '#F57C00',
  pending: '#9E9E9E',
  adjusted: '#1976D2',
  written_off: '#6A1B9A',
}

export default function Dashboard() {
  const { data: claimSummary } = useQuery({
    queryKey: ['claim-summary'],
    queryFn: () => api.get('/claims/summary').then(r => r.data),
  })

  const { data: denialSummary } = useQuery({
    queryKey: ['denial-summary'],
    queryFn: () => api.get('/denials/summary').then(r => r.data),
  })

  const statusData = claimSummary?.by_status
    ? Object.entries(claimSummary.by_status).map(([status, d]) => ({
        status: status.charAt(0).toUpperCase() + status.slice(1),
        count: d.count,
        amount: d.billed,
      }))
    : []

  const denialByCat = denialSummary?.by_category
    ? Object.entries(denialSummary.by_category).map(([cat, d]) => ({
        category: cat.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()),
        count: d.count,
        amount: d.amount,
      }))
    : []

  return (
    <div className="p-6">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-900">Dashboard</h1>
        <p className="text-gray-500 text-sm mt-1">ERA 835 Payment Posting — Maryland</p>
      </div>

      {/* Stat Row */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        <StatCard
          label="Total Claims"
          value={claimSummary?.total_claims?.toLocaleString() || '—'}
          sub="All time"
          icon={FileText}
        />
        <StatCard
          label="Total Billed"
          value={claimSummary ? fmt.currency(claimSummary.total_billed) : '—'}
          sub={`Paid: ${claimSummary ? fmt.currency(claimSummary.total_paid) : '—'}`}
          icon={DollarSign}
          color="text-success"
        />
        <StatCard
          label="Outstanding Balance"
          value={claimSummary ? fmt.currency(claimSummary.total_balance) : '—'}
          sub="Patient + Insurance"
          icon={TrendingUp}
          color="text-warning"
        />
        <StatCard
          label="Open Denials"
          value={denialSummary?.open?.toLocaleString() || '—'}
          sub={denialSummary ? fmt.currency(denialSummary.total_denied_amount) + ' at risk' : ''}
          icon={AlertTriangle}
          color="text-danger"
        />
      </div>

      {/* Urgent Denials Banner */}
      {denialSummary?.urgent > 0 && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-4 mb-6 flex items-center gap-3">
          <AlertTriangle size={20} className="text-red-600 shrink-0" />
          <div>
            <span className="font-semibold text-red-700">{denialSummary.urgent} appeal deadline(s) within 30 days!</span>
            <span className="text-red-600 text-sm ml-2">
              {denialSummary.overdue > 0 && `— ${denialSummary.overdue} already past deadline`}
            </span>
          </div>
          <a href="/denials?urgent=1" className="ml-auto btn-danger text-xs">Review Now</a>
        </div>
      )}

      {/* Charts */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
        <div className="card">
          <h2 className="text-sm font-semibold text-gray-700 mb-4">Claims by Status</h2>
          {statusData.length > 0 ? (
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={statusData}>
                <XAxis dataKey="status" tick={{ fontSize: 11 }} />
                <YAxis tick={{ fontSize: 11 }} />
                <Tooltip formatter={(v) => v.toLocaleString()} />
                <Bar dataKey="count" radius={[4, 4, 0, 0]}>
                  {statusData.map((entry) => (
                    <Cell
                      key={entry.status}
                      fill={STATUS_COLORS[entry.status.toLowerCase()] || '#9E9E9E'}
                    />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <div className="text-center text-gray-400 py-12 text-sm">No claim data yet</div>
          )}
        </div>

        <div className="card">
          <h2 className="text-sm font-semibold text-gray-700 mb-4">Open Denials by Category</h2>
          {denialByCat.length > 0 ? (
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={denialByCat} layout="vertical">
                <XAxis type="number" tick={{ fontSize: 10 }} />
                <YAxis dataKey="category" type="category" tick={{ fontSize: 10 }} width={110} />
                <Tooltip formatter={(v, n) => n === 'count' ? v : fmt.currency(v)} />
                <Bar dataKey="count" fill="#1B4F8A" radius={[0, 4, 4, 0]} />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <div className="text-center text-gray-400 py-12 text-sm">No denial data yet</div>
          )}
        </div>
      </div>

      {/* Quick Actions */}
      <div className="card">
        <h2 className="text-sm font-semibold text-gray-700 mb-3">Quick Actions</h2>
        <div className="flex flex-wrap gap-3">
          <a href="/import" className="btn-primary">Import ERA 835 File</a>
          <a href="/denials" className="btn-secondary">Review Denials</a>
          <a href="/patients" className="btn-secondary">Patient Ledgers</a>
          <a href="/appeals" className="btn-secondary">Manage Appeals</a>
        </div>
      </div>
    </div>
  )
}
