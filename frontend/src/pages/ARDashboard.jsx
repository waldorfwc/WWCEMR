import { useState, useRef } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer,
  PieChart, Pie, Cell, Legend,
} from 'recharts'
import {
  DollarSign, AlertTriangle, TrendingDown, Upload,
  RefreshCw, CheckCircle, XCircle, Wifi, WifiOff,
} from 'lucide-react'
import api, { fmt } from '../utils/api'
import EmptyState from '../components/EmptyState'

const BUCKET_COLORS = {
  '0–30 Days': '#2E7D32',
  '31–60 Days': '#1976D2',
  '61–90 Days': '#F57C00',
  '91–120 Days': '#E53935',
  '120+ Days': '#6A1B9A',
}

const BUCKET_LABELS = {
  '0_30': '0–30 Days',
  '31_60': '31–60 Days',
  '61_90': '61–90 Days',
  '91_120': '91–120 Days',
  '120_plus': '120+ Days',
}

function AgingBar({ label, amount, total, color }) {
  const pct = total > 0 ? (amount / total) * 100 : 0
  return (
    <div className="mb-3">
      <div className="flex justify-between text-xs mb-1">
        <span className="font-medium text-gray-700">{label}</span>
        <span className="text-gray-500">{fmt.currency(amount)} ({pct.toFixed(1)}%)</span>
      </div>
      <div className="w-full bg-gray-100 rounded-full h-2.5">
        <div
          className="h-2.5 rounded-full transition-all"
          style={{ width: `${pct}%`, backgroundColor: color }}
        />
      </div>
    </div>
  )
}

function StatCard({ label, value, sub, icon: Icon, color = 'text-primary-600', bg = 'bg-blue-50' }) {
  return (
    <div className="stat-card">
      <div className="flex items-start justify-between">
        <div>
          <div className="text-xs text-gray-500 font-medium uppercase tracking-wide">{label}</div>
          <div className={`text-2xl font-bold mt-1 ${color}`}>{value}</div>
          {sub && <div className="text-xs text-gray-400 mt-0.5">{sub}</div>}
        </div>
        <div className={`p-2 rounded-lg ${bg} ${color}`}>
          <Icon size={20} />
        </div>
      </div>
    </div>
  )
}

export default function ARDashboard() {
  const [uploadResult, setUploadResult] = useState(null)
  const [uploading, setUploading] = useState(false)
  const fileRef = useRef()
  const qc = useQueryClient()

  const { data: arData, isLoading: arLoading, refetch: refetchAR } = useQuery({
    queryKey: ['ar-summary'],
    queryFn: () => api.get('/ar/summary').then(r => r.data),
    refetchInterval: 60000,
  })

  const { data: waystarStatus } = useQuery({
    queryKey: ['waystar-status'],
    queryFn: () => api.get('/waystar/status').then(r => r.data),
  })

  const { data: payerPerf, isLoading: payerLoading } = useQuery({
    queryKey: ['payer-performance'],
    queryFn: () => api.get('/ar/payer-performance').then(r => r.data),
  })

  const testConnection = useMutation({
    mutationFn: () => api.post('/waystar/test-connection').then(r => r.data),
  })

  const syncEras = useMutation({
    mutationFn: () => api.post('/waystar/sync-eras').then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries(['ar-summary'])
      qc.invalidateQueries(['claim-summary'])
    },
  })

  async function handleUpload(e) {
    const file = e.target.files?.[0]
    if (!file) return
    setUploading(true)
    setUploadResult(null)
    try {
      const fd = new FormData()
      fd.append('file', file)
      const res = await api.post('/ar/upload-aging', fd, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      setUploadResult({ success: true, data: res.data })
    } catch (err) {
      setUploadResult({ success: false, error: err.response?.data?.detail || err.message })
    } finally {
      setUploading(false)
      e.target.value = ''
    }
  }

  const buckets = arData?.aging_buckets || {}
  const total = arData?.total_outstanding || 0

  const agingChartData = Object.entries(BUCKET_LABELS).map(([key, label]) => ({
    label,
    amount: buckets[key] || 0,
    color: BUCKET_COLORS[label],
  }))

  const payerChartData = (arData?.payer_breakdown || []).slice(0, 8).map(p => ({
    name: p.payer.length > 20 ? p.payer.slice(0, 18) + '…' : p.payer,
    balance: p.balance,
  }))

  const payerPieData = (arData?.payer_breakdown || []).slice(0, 6).map((p, i) => ({
    name: p.payer.length > 22 ? p.payer.slice(0, 20) + '…' : p.payer,
    value: p.balance,
  }))
  const PIE_COLORS = ['#1B4F8A', '#2E7D32', '#F57C00', '#C62828', '#6A1B9A', '#00838F']

  const psAging = uploadResult?.success && uploadResult.data?.detected_format === 'ar_aging'
    ? uploadResult.data.summary
    : null
  const psBuckets = psAging?.buckets || {}
  const psTotal = psBuckets.total || 0

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">A/R Dashboard</h1>
          <p className="text-gray-500 text-sm mt-1">
            Accounts Receivable — ERA Database + Waystar + PrimeSuite
          </p>
        </div>
        <button
          onClick={() => refetchAR()}
          className="btn-secondary flex items-center gap-2 text-xs"
        >
          <RefreshCw size={14} />
          Refresh
        </button>
      </div>

      {/* Waystar Status Bar */}
      <div className="card mb-5 flex flex-wrap items-center gap-4">
        <div className="flex items-center gap-2 text-sm">
          {waystarStatus?.configured ? (
            <Wifi size={16} className="text-green-600" />
          ) : (
            <WifiOff size={16} className="text-gray-400" />
          )}
          <span className="font-medium text-gray-700">Waystar:</span>
          {waystarStatus?.configured ? (
            <span className="text-green-700">Credentials configured</span>
          ) : (
            <span className="text-gray-400">Not configured</span>
          )}
          {waystarStatus?.configured && (
            <span className="text-gray-400 text-xs ml-1">
              (key: {waystarStatus.api_key_hint})
            </span>
          )}
        </div>

        {waystarStatus?.configured && (
          <>
            <button
              onClick={() => testConnection.mutate()}
              disabled={testConnection.isPending}
              className="btn-secondary text-xs flex items-center gap-1"
            >
              {testConnection.isPending ? (
                <RefreshCw size={12} className="animate-spin" />
              ) : (
                <CheckCircle size={12} />
              )}
              Test Connection
            </button>

            {testConnection.data && (
              <span className={`text-xs px-2 py-1 rounded-full font-medium ${
                testConnection.data.status === 'connected'
                  ? 'bg-green-100 text-green-700'
                  : 'bg-red-100 text-red-700'
              }`}>
                {testConnection.data.status === 'connected'
                  ? `Connected via ${testConnection.data.mode}`
                  : 'Connection failed — see details below'}
              </span>
            )}

            {waystarStatus?.has_sftp && (
              <button
                onClick={() => syncEras.mutate()}
                disabled={syncEras.isPending}
                className="btn-primary text-xs flex items-center gap-1 ml-auto"
              >
                {syncEras.isPending ? (
                  <RefreshCw size={12} className="animate-spin" />
                ) : (
                  <Upload size={12} />
                )}
                Sync ERAs via SFTP
              </button>
            )}
          </>
        )}

        {testConnection.data?.status !== 'connected' && testConnection.data?.help && (
          <div className="w-full mt-2 text-xs text-amber-700 bg-amber-50 rounded p-2">
            {testConnection.data.help}
          </div>
        )}
      </div>

      {/* Stat Row */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        <StatCard
          label="Total Outstanding"
          value={arLoading ? '—' : fmt.currency(total)}
          sub={`${arData?.open_claim_count || 0} open claims`}
          icon={DollarSign}
          color="text-orange-600"
          bg="bg-orange-50"
        />
        <StatCard
          label="Collection Rate"
          value={arLoading ? '—' : `${arData?.collection_rate_pct || 0}%`}
          sub={`Paid ${fmt.currency(arData?.total_paid || 0)} of ${fmt.currency(arData?.total_billed || 0)}`}
          icon={TrendingDown}
          color="text-blue-600"
          bg="bg-blue-50"
        />
        <StatCard
          label="120+ Days"
          value={arLoading ? '—' : fmt.currency(buckets['120_plus'] || 0)}
          sub="Oldest A/R — hardest to collect"
          icon={AlertTriangle}
          color="text-red-600"
          bg="bg-red-50"
        />
        <StatCard
          label="Open Denials"
          value={arLoading ? '—' : (arData?.denial_metrics?.open_denials || 0).toLocaleString()}
          sub={`${fmt.currency(arData?.denial_metrics?.denied_amount || 0)} at risk`}
          icon={XCircle}
          color="text-purple-600"
          bg="bg-purple-50"
        />
      </div>

      {/* Alert banners */}
      {(arData?.denial_metrics?.urgent_deadlines > 0 || arData?.denial_metrics?.overdue > 0) && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-3 mb-5 flex items-center gap-3 text-sm">
          <AlertTriangle size={18} className="text-red-600 shrink-0" />
          <div>
            {arData.denial_metrics.urgent_deadlines > 0 && (
              <span className="font-semibold text-red-700 mr-3">
                {arData.denial_metrics.urgent_deadlines} appeal deadline(s) within 30 days
              </span>
            )}
            {arData.denial_metrics.overdue > 0 && (
              <span className="text-red-600">
                {arData.denial_metrics.overdue} deadline(s) already passed
              </span>
            )}
          </div>
          <a href="/denials?urgent=1" className="ml-auto btn-danger text-xs">Review Denials</a>
        </div>
      )}

      {arData?.days_oldest > 365 && (
        <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 mb-5 flex items-center gap-3 text-sm">
          <AlertTriangle size={16} className="text-amber-600 shrink-0" />
          <span className="text-amber-700">
            Oldest open DOS is {arData.days_oldest} days ago ({arData.oldest_open_dos}).
            Claims this old may be past timely filing limits — review for write-off.
          </span>
        </div>
      )}

      {/* A/R Aging from ERA DB */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
        <div className="card">
          <h2 className="text-sm font-semibold text-gray-700 mb-4">
            A/R Aging — ERA Database
          </h2>
          {Object.keys(buckets).length > 0 ? (
            <>
              {agingChartData.map(({ label, amount, color }) => (
                <AgingBar key={label} label={label} amount={amount} total={total} color={color} />
              ))}
              <div className="mt-3 pt-3 border-t flex justify-between text-sm font-semibold text-gray-700">
                <span>Total Outstanding</span>
                <span>{fmt.currency(total)}</span>
              </div>
            </>
          ) : (
            <EmptyState
              icon={Upload}
              title="No open claim data yet"
              body="Import ERA 835 files to start seeing aging."
            />
          )}
        </div>

        <div className="card">
          <h2 className="text-sm font-semibold text-gray-700 mb-4">Outstanding by Payer</h2>
          {payerChartData.length > 0 ? (
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={payerChartData} layout="vertical">
                <XAxis type="number" tick={{ fontSize: 10 }} tickFormatter={v => `$${(v/1000).toFixed(0)}k`} />
                <YAxis dataKey="name" type="category" tick={{ fontSize: 10 }} width={120} />
                <Tooltip formatter={v => fmt.currency(v)} />
                <Bar dataKey="balance" fill="#1B4F8A" radius={[0, 4, 4, 0]} />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <div className="text-center text-gray-400 py-10 text-sm">No payer data yet</div>
          )}
        </div>
      </div>

      {/* Payer Performance Table */}
      <div className="card mb-6">
        <h2 className="text-sm font-semibold text-gray-700 mb-4">Payer Performance</h2>
        {!payerLoading && payerPerf?.payers?.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b text-gray-500">
                  <th className="text-left py-2 pr-4 font-medium">Payer</th>
                  <th className="text-right py-2 pr-4 font-medium">Claims</th>
                  <th className="text-right py-2 pr-4 font-medium">Billed</th>
                  <th className="text-right py-2 pr-4 font-medium">Paid</th>
                  <th className="text-right py-2 pr-4 font-medium">Balance</th>
                  <th className="text-right py-2 font-medium">Collection %</th>
                </tr>
              </thead>
              <tbody>
                {payerPerf.payers.map((p, i) => (
                  <tr key={i} className="border-b last:border-0 hover:bg-gray-50">
                    <td className="py-2 pr-4 font-medium text-gray-800">{p.payer}</td>
                    <td className="py-2 pr-4 text-right text-gray-600">{p.claim_count.toLocaleString()}</td>
                    <td className="py-2 pr-4 text-right text-gray-600">{fmt.currency(p.total_billed)}</td>
                    <td className="py-2 pr-4 text-right text-gray-600">{fmt.currency(p.total_paid)}</td>
                    <td className={`py-2 pr-4 text-right font-medium ${p.total_balance > 0 ? 'text-red-600' : 'text-gray-400'}`}>
                      {fmt.currency(p.total_balance)}
                    </td>
                    <td className="py-2 text-right">
                      <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                        p.collection_rate_pct >= 90 ? 'bg-green-100 text-green-700' :
                        p.collection_rate_pct >= 70 ? 'bg-yellow-100 text-yellow-700' :
                        'bg-red-100 text-red-700'
                      }`}>
                        {p.collection_rate_pct}%
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="text-center text-gray-400 py-8 text-sm">No payer data yet</div>
        )}
      </div>

      {/* PrimeSuite Upload Section */}
      <div className="card">
        <h2 className="text-sm font-semibold text-gray-700 mb-1">
          PrimeSuite Export — Upload AR Aging Report
        </h2>
        <p className="text-xs text-gray-400 mb-4">
          Upload a PrimeSuite AR Aging, Charge Capture, Payment, or Claim Status export (CSV or Excel).
          The system will auto-detect the format and normalize the columns.
        </p>

        <div className="flex items-center gap-3">
          <input
            ref={fileRef}
            type="file"
            accept=".csv,.xlsx,.xls"
            onChange={handleUpload}
            className="hidden"
          />
          <button
            onClick={() => fileRef.current?.click()}
            disabled={uploading}
            className="btn-primary flex items-center gap-2 text-xs"
          >
            {uploading ? (
              <RefreshCw size={14} className="animate-spin" />
            ) : (
              <Upload size={14} />
            )}
            {uploading ? 'Processing…' : 'Upload PrimeSuite Report'}
          </button>
          <span className="text-xs text-gray-400">CSV or Excel (.xlsx/.xls)</span>
        </div>

        {uploadResult && (
          <div className={`mt-4 rounded-lg p-4 ${uploadResult.success ? 'bg-green-50 border border-green-200' : 'bg-red-50 border border-red-200'}`}>
            {uploadResult.success ? (
              <>
                <div className="flex items-center gap-2 text-sm font-semibold text-green-700 mb-2">
                  <CheckCircle size={16} />
                  {uploadResult.data.filename} — {uploadResult.data.row_count} rows detected as&nbsp;
                  <span className="capitalize">{(uploadResult.data.detected_format || 'unknown').replace(/_/g, ' ')}</span>
                </div>

                {psAging && (
                  <div className="mt-3">
                    <div className="text-xs font-semibold text-gray-700 mb-2">
                      PrimeSuite A/R Aging Summary
                    </div>
                    <div className="grid grid-cols-3 gap-3 text-xs mb-3">
                      {Object.entries(BUCKET_LABELS).map(([key, label]) => (
                        <div key={key} className="bg-white rounded p-2 border">
                          <div className="text-gray-500">{label}</div>
                          <div className="font-bold text-gray-800 mt-0.5">
                            {fmt.currency(psBuckets[key] || 0)}
                          </div>
                        </div>
                      ))}
                      <div className="bg-gray-50 rounded p-2 border col-span-3">
                        <div className="text-gray-500">Total A/R from PrimeSuite</div>
                        <div className="font-bold text-gray-900 text-sm mt-0.5">{fmt.currency(psTotal)}</div>
                      </div>
                    </div>

                    {psAging.payer_totals && Object.keys(psAging.payer_totals).length > 0 && (
                      <>
                        <div className="text-xs font-semibold text-gray-700 mb-1">By Insurance</div>
                        <div className="space-y-1">
                          {Object.entries(psAging.payer_totals).slice(0, 8).map(([payer, bal]) => (
                            <div key={payer} className="flex justify-between text-xs text-gray-600">
                              <span>{payer}</span>
                              <span className="font-medium">{fmt.currency(bal)}</span>
                            </div>
                          ))}
                        </div>
                      </>
                    )}
                  </div>
                )}

                {uploadResult.data.note && (
                  <div className="text-xs text-amber-700 mt-2">{uploadResult.data.note}</div>
                )}

                {uploadResult.data.rows?.length > 0 && !psAging && (
                  <div className="mt-3 overflow-x-auto">
                    <div className="text-xs font-semibold text-gray-600 mb-1">Preview (first 5 rows)</div>
                    <table className="text-xs w-full border-collapse">
                      <thead>
                        <tr className="bg-gray-100">
                          {Object.keys(uploadResult.data.rows[0]).filter(k => !k.startsWith('_')).slice(0, 8).map(h => (
                            <th key={h} className="text-left p-1 border border-gray-200 font-medium text-gray-600">
                              {h.replace(/_/g, ' ')}
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {uploadResult.data.rows.slice(0, 5).map((row, i) => (
                          <tr key={i}>
                            {Object.entries(row).filter(([k]) => !k.startsWith('_')).slice(0, 8).map(([k, v]) => (
                              <td key={k} className="p-1 border border-gray-200 text-gray-700">
                                {v ?? '—'}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </>
            ) : (
              <div className="flex items-center gap-2 text-sm text-red-700">
                <XCircle size={16} />
                {uploadResult.error}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
