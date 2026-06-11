import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { AlertTriangle, Clock, CheckCircle, XCircle, Zap, Sparkles } from 'lucide-react'
import api, { fmt, statusColors } from '../utils/api'
import DenialCodeAutocomplete from '../components/DenialCodeAutocomplete'
import DenialCodeDrawer, { GroupBadge, CodeChip } from '../components/DenialCodeDrawer'

const CATEGORIES = [
  '', 'timely_filing', 'authorization', 'medical_necessity', 'eligibility',
  'duplicate', 'coding', 'cob', 'provider_credentialing', 'missing_information',
  'benefit_limit', 'non_covered', 'other',
]

function urgencyBadge(deadline) {
  if (!deadline) return null
  const days = Math.ceil((new Date(deadline) - new Date()) / 86400000)
  if (days < 0) return <span className="badge bg-red-200 text-red-900">OVERDUE</span>
  if (days <= 14) return <span className="badge bg-red-100 text-red-700">⚡ {days}d</span>
  if (days <= 30) return <span className="badge bg-yellow-100 text-yellow-700">{days}d</span>
  return <span className="text-xs text-gray-400">{days}d</span>
}

export default function Denials() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [category, setCategory] = useState('')
  const [status, setStatus] = useState('open')
  const [urgentOnly, setUrgentOnly] = useState(false)
  const [writeOffOnly, setWriteOffOnly] = useState(false)
  const [generating, setGenerating] = useState(null)
  const [drawer, setDrawer] = useState({ open: false, request: null })

  function openSingle(code_type, code) {
    setDrawer({ open: true, request: { mode: 'single', code_type, code } })
  }
  function openCombo(d) {
    setDrawer({
      open: true,
      request: {
        mode: 'combo',
        group_code: d.group_code || 'CO',
        carc: d.carc_code,
        rarcs: d.rarc_code ? [d.rarc_code] : [],
      },
    })
  }
  function openGroup(group_code) {
    setDrawer({ open: true, request: { mode: 'group', group_code } })
  }

  const { data, isLoading } = useQuery({
    queryKey: ['denials', category, status, urgentOnly, writeOffOnly],
    queryFn: () => api.get('/denials', {
      params: { category, status, urgent_only: urgentOnly, write_off_only: writeOffOnly, per_page: 100 }
    }).then(r => r.data),
  })

  const { data: summary } = useQuery({
    queryKey: ['denial-summary'],
    queryFn: () => api.get('/denials/summary').then(r => r.data),
  })

  const handleGenerateAppeal = async (denialId) => {
    setGenerating(denialId)
    try {
      const res = await api.post('/appeals/generate', { denial_id: denialId })
      qc.invalidateQueries(['denials'])
      navigate(`/claims/${res.data.claim_id || ''}`)
      alert('Appeal letter generated! Check the claim detail page.')
    } catch (e) {
      alert('Error: ' + (e.response?.data?.detail || e.message))
    }
    setGenerating(null)
  }

  const handleWriteOff = async (denialId) => {
    if (!confirm('Mark this denial as written off?')) return
    await api.patch(`/denials/${denialId}`, { status: 'written_off' })
    qc.invalidateQueries(['denials'])
  }

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-4 gap-4">
        <div>
          <h1 className="page-title">Denial Management</h1>
          <p className="text-gray-500 text-sm mt-1">
            {summary?.open || 0} open · {fmt.currency(summary?.total_denied_amount || 0)} at risk ·
            <span className="text-red-600 font-medium ml-1">{summary?.urgent || 0} urgent · {summary?.overdue || 0} overdue</span>
          </p>
        </div>
        <DenialCodeAutocomplete
          onPick={req => setDrawer({ open: true, request: req })}
        />
      </div>

      {/* Summary cards */}
      {summary?.by_category && (
        <div className="flex gap-2 flex-wrap mb-4">
          {Object.entries(summary.by_category).map(([cat, d]) => (
            <button
              key={cat}
              onClick={() => setCategory(cat === category ? '' : cat)}
              className={`px-3 py-1.5 rounded-lg text-xs border transition-colors ${
                category === cat ? 'bg-plum-700 text-white border-plum-700' : 'bg-white border-border-subtle text-gray-700 hover:border-plum-300'
              }`}
            >
              {cat.replace(/_/g, ' ')} <span className="font-bold">({d.count})</span>
              <span className="ml-1 text-gray-400">{fmt.currency(d.amount)}</span>
            </button>
          ))}
        </div>
      )}

      {/* Filters */}
      <div className="card mb-4 flex flex-wrap gap-3 items-center">
        <select className="input w-36" aria-label="Status filter" value={status} onChange={e => setStatus(e.target.value)}>
          <option value="">All Statuses</option>
          <option value="open">Open</option>
          <option value="appealing">Appealing</option>
          <option value="overturned">Overturned</option>
          <option value="upheld">Upheld</option>
          <option value="written_off">Written Off</option>
        </select>
        <label className="flex items-center gap-2 text-sm cursor-pointer">
          <input type="checkbox" checked={urgentOnly} onChange={e => setUrgentOnly(e.target.checked)} />
          Urgent only (≤30 days)
        </label>
        <label className="flex items-center gap-2 text-sm cursor-pointer">
          <input type="checkbox" checked={writeOffOnly} onChange={e => setWriteOffOnly(e.target.checked)} />
          Write-off recommended
        </label>
      </div>

      {/* Table */}
      <div className="card p-0 overflow-hidden">
        <table className="w-full">
          <thead className="bg-gray-50 border-b border-gray-200">
            <tr>
              <th className="table-th">Codes</th>
              <th className="table-th">Category</th>
              <th className="table-th">Claim / DOS</th>
              <th className="table-th">Payer</th>
              <th className="table-th text-right">Amount</th>
              <th className="table-th">Deadline</th>
              <th className="table-th">Status</th>
              <th className="table-th">Actions</th>
            </tr>
          </thead>
          <tbody>
            {isLoading && <tr><td colSpan={8} className="table-td text-center py-8 text-gray-400">Loading…</td></tr>}
            {!isLoading && data?.denials?.length === 0 && (
              <tr><td colSpan={8} className="table-td text-center py-8 text-gray-400">No denials match filters</td></tr>
            )}
            {data?.denials?.map(d => (
              <tr key={d.id} className="table-row">
                <td className="table-td">
                  <div className="flex items-center gap-1 flex-wrap">
                    {d.group_code && <GroupBadge code={d.group_code} onClick={() => openGroup(d.group_code)} />}
                    {d.carc_code && (
                      <CodeChip type="CARC" code={d.carc_code} onClick={() => openSingle('CARC', d.carc_code)} />
                    )}
                    {d.rarc_code && (
                      <CodeChip type="RARC" code={d.rarc_code} onClick={() => openSingle('RARC', d.rarc_code)} />
                    )}
                  </div>
                  <button
                    onClick={() => openCombo(d)}
                    className="mt-1 text-[10px] text-plum-600 hover:underline flex items-center gap-1"
                  >
                    <Sparkles size={10} /> Explain this denial
                  </button>
                  {d.carc_description && (
                    <div className="text-[10px] text-gray-400 mt-0.5 max-w-[200px] leading-tight truncate" title={d.carc_description}>
                      {d.carc_description}
                    </div>
                  )}
                </td>
                <td className="table-td text-xs">
                  <span className="badge bg-gray-100 text-gray-600">{d.category?.replace(/_/g, ' ')}</span>
                  {d.write_off_recommended && (
                    <div className="text-xs text-purple-600 mt-1">⚠ Write-off rec.</div>
                  )}
                </td>
                <td className="table-td text-xs">
                  {d.claim && (
                    <>
                      <a href={`/claims/${d.claim_id}`} className="font-mono text-plum-700 hover:underline">{d.claim.claim_number}</a>
                      <div className="text-gray-400">{fmt.date(d.claim.date_of_service_from)}</div>
                    </>
                  )}
                </td>
                <td className="table-td text-xs">{d.claim?.payer_name || '—'}</td>
                <td className="table-td text-right font-mono font-bold text-red-600">{fmt.currency(d.denied_amount)}</td>
                <td className="table-td">{urgencyBadge(d.appeal_deadline)}</td>
                <td className="table-td">
                  <span className={statusColors[d.status] || 'badge-pending'}>{d.status?.replace(/_/g, ' ')}</span>
                </td>
                <td className="table-td">
                  <div className="flex flex-col gap-1">
                    {d.appealable && d.status === 'open' && (
                      <button
                        className="text-xs text-blue-600 hover:underline flex items-center gap-1"
                        onClick={() => navigate(`/claims/${d.claim_id}`)}
                        disabled={generating === d.id}
                      >
                        <Zap size={11} />
                        {generating === d.id ? 'Generating…' : 'Generate Appeal'}
                      </button>
                    )}
                    {d.write_off_recommended && d.status === 'open' && (
                      <button
                        className="text-xs text-purple-600 hover:underline"
                        onClick={() => handleWriteOff(d.id)}
                      >
                        Write Off
                      </button>
                    )}
                    {d.claim_id && (
                      <a href={`/claims/${d.claim_id}`} className="text-xs text-gray-500 hover:underline">View Claim</a>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Legend */}
      <div className="mt-4 text-xs text-gray-400 flex gap-4 flex-wrap">
        <span>⚡ = deadline ≤14 days</span>
        <span>Yellow = 15-30 days</span>
        <span>CO = Contractual · PR = Patient Resp · OA = Other · PI = Payer Initiated</span>
        <span>Maryland: MD Insurance Article §15-1005 | MIA: 800-492-6116</span>
      </div>

      <DenialCodeDrawer
        open={drawer.open}
        onClose={() => setDrawer(d => ({ ...d, open: false }))}
        initialRequest={drawer.request}
      />
    </div>
  )
}
