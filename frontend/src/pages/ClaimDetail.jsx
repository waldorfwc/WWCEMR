import { useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { FileDown, AlertTriangle, ArrowLeft, Pencil, Plus } from 'lucide-react'
import api, { fmt, statusColors } from '../utils/api'
import { useState } from 'react'
import EditClaimDrawer from '../components/EditClaimDrawer'
import EditServiceLineDrawer from '../components/EditServiceLineDrawer'
import LoadingState from '../components/LoadingState'

export default function ClaimDetail() {
  const { id } = useParams()
  const [appealDenialId, setAppealDenialId] = useState(null)
  const [appealNotes, setAppealNotes] = useState('')
  const [appealResult, setAppealResult] = useState(null)
  const [generating, setGenerating] = useState(false)
  const [editingClaim, setEditingClaim] = useState(false)
  const [editingLine, setEditingLine] = useState(null)  // null=closed, 'add'=add mode, <line object>=edit

  const { data: claim, isLoading } = useQuery({
    queryKey: ['claim', id],
    queryFn: () => api.get(`/claims/${id}`).then(r => r.data),
  })

  const handleGenerateAppeal = async (denialId) => {
    setGenerating(true)
    try {
      const res = await api.post('/appeals/generate', {
        denial_id: denialId,
        additional_notes: appealNotes,
      })
      setAppealResult(res.data)
    } catch (e) {
      alert('Error generating appeal: ' + (e.response?.data?.detail || e.message))
    }
    setGenerating(false)
  }

  if (isLoading) return <LoadingState />
  if (!claim) return <div className="p-6 text-gray-400">Claim not found</div>

  return (
    <div className="p-6 max-w-5xl">
      <div className="flex items-center gap-3 mb-4">
        <a href="/claims" className="text-gray-400 hover:text-gray-600"><ArrowLeft size={20} /></a>
        <div>
          <h1 className="text-xl font-bold text-gray-900">Claim {claim.claim_number}</h1>
          <p className="text-gray-500 text-sm">{claim.payer_name} · {fmt.date(claim.date_of_service_from)}</p>
        </div>
        <div className="ml-auto flex gap-2">
          <span className={statusColors[claim.status] || 'badge-pending'}>{claim.status?.replace(/_/g, ' ')}</span>
          <button className="btn-primary text-xs" onClick={() => setEditingClaim(true)}>
            <Pencil size={14} className="inline mr-1" />Edit claim
          </button>
          <button
            className="btn-secondary text-xs"
            onClick={() => window.open(`/api/eob/${id}/pdf`, '_blank')}
          >
            <FileDown size={14} className="inline mr-1" />EOB PDF
          </button>
        </div>
      </div>

      {claim.patient && (
        <div className="card mb-6 flex items-center gap-6 py-3">
          <div>
            <div className="text-[11px] uppercase tracking-wide text-gray-400">Patient</div>
            <a
              href={`/patients/${claim.patient.id}`}
              className="font-semibold text-gray-900 hover:text-plum-700"
            >
              {claim.patient.last_name || ''}
              {claim.patient.last_name && claim.patient.first_name ? ', ' : ''}
              {claim.patient.first_name || ''}
            </a>
          </div>
          <div>
            <div className="text-[11px] uppercase tracking-wide text-gray-400">Chart #</div>
            <div className="font-mono text-sm text-gray-800">#{claim.patient.chart_number || '—'}</div>
          </div>
          <div>
            <div className="text-[11px] uppercase tracking-wide text-gray-400">DOB</div>
            <div className="text-sm text-gray-800">{claim.patient.date_of_birth ? fmt.date(claim.patient.date_of_birth) : '—'}</div>
          </div>
        </div>
      )}

      <div className="grid grid-cols-2 gap-4 mb-6">
        {/* Claim Info */}
        <div className="card">
          <h2 className="text-sm font-semibold text-gray-700 mb-3">Claim Information</h2>
          <dl className="text-sm space-y-1.5">
            {[
              ['Claim Number', claim.claim_number],
              ['Payer Claim #', claim.payer_claim_number],
              ['Payer', claim.payer_name],
              ['Member ID', claim.subscriber_id],
              ['Group #', claim.group_number],
              ['DOS', fmt.date(claim.date_of_service_from)],
              ['Insurance Order', claim.insurance_order],
              ['Check #', claim.check_number],
              ['Check Date', fmt.date(claim.check_date)],
              ['Provider', claim.rendering_provider_name],
              ['NPI', claim.rendering_provider_npi],
            ].map(([label, val]) => val && (
              <div key={label} className="flex justify-between">
                <dt className="text-gray-500">{label}:</dt>
                <dd className="font-medium text-gray-800 text-right max-w-[200px] truncate">{val}</dd>
              </div>
            ))}
          </dl>
        </div>

        {/* Financial Summary */}
        <div className="card">
          <h2 className="text-sm font-semibold text-gray-700 mb-3">Financial Summary</h2>
          <dl className="text-sm space-y-1.5">
            {[
              ['Billed Amount', fmt.currency(claim.billed_amount), ''],
              ['Contractual Adj (CO-45)', `(${fmt.currency(claim.contractual_adjustment)})`, 'text-gray-500'],
              ['Allowed Amount', fmt.currency(claim.allowed_amount), ''],
              ['Insurance Paid', fmt.currency(claim.paid_amount), 'text-green-700'],
              ['Other Adjustments', `(${fmt.currency(claim.other_adjustment)})`, 'text-gray-500'],
              ['Patient Responsibility', fmt.currency(claim.patient_responsibility), 'text-orange-600'],
              ['Balance', fmt.currency(claim.balance), claim.balance > 0 ? 'text-red-600 font-bold' : 'text-gray-500'],
            ].map(([label, val, cls]) => (
              <div key={label} className="flex justify-between border-b border-gray-50 pb-1 last:border-0 last:font-semibold">
                <dt className="text-gray-500">{label}:</dt>
                <dd className={`font-mono ${cls}`}>{val}</dd>
              </div>
            ))}
          </dl>
        </div>
      </div>

      {/* Service Lines */}
      <div className="card mb-4">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-semibold text-gray-700">Service Lines</h2>
          <button className="btn-secondary text-xs" onClick={() => setEditingLine('add')}>
            <Plus size={14} className="inline mr-1" />Add line
          </button>
        </div>
        {claim.service_lines?.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border-subtle text-xs text-gray-500 uppercase">
                  <th className="pb-2 text-left">CPT/Code</th>
                  <th className="pb-2 text-left">Modifiers</th>
                  <th className="pb-2 text-left">DOS</th>
                  <th className="pb-2 text-right">Units</th>
                  <th className="pb-2 text-right">Billed</th>
                  <th className="pb-2 text-right">Paid</th>
                  <th className="pb-2 text-right">Pt. Resp</th>
                  <th className="pb-2 text-right"></th>
                </tr>
              </thead>
              <tbody>
                {claim.service_lines.map(svc => (
                  <tr key={svc.id} className="border-b border-gray-50">
                    <td className="py-2 font-mono font-medium">{svc.procedure_code}</td>
                    <td className="py-2 text-gray-500 text-xs">
                      {[svc.modifier_1, svc.modifier_2, svc.modifier_3, svc.modifier_4].filter(Boolean).join(' ')}
                    </td>
                    <td className="py-2 text-xs">{fmt.date(svc.date_of_service_from)}</td>
                    <td className="py-2 text-right">{svc.units}</td>
                    <td className="py-2 text-right font-mono">{fmt.currency(svc.billed_amount)}</td>
                    <td className="py-2 text-right font-mono text-green-700">{fmt.currency(svc.paid_amount)}</td>
                    <td className="py-2 text-right font-mono text-orange-600">{fmt.currency(svc.patient_responsibility)}</td>
                    <td className="py-2 text-right">
                      <button className="text-xs text-plum-600 underline" onClick={() => setEditingLine(svc)}>
                        ✎ Edit
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="text-xs text-gray-500 italic">No service lines yet.</div>
        )}
      </div>

      {/* Denials */}
      {claim.denials?.length > 0 && (
        <div className="card mb-4">
          <h2 className="text-sm font-semibold text-gray-700 mb-3 flex items-center gap-2">
            <AlertTriangle size={16} className="text-red-500" />
            Denials ({claim.denials.length})
          </h2>
          {claim.denials.map(denial => (
            <div key={denial.id} className="border border-red-100 rounded-lg p-4 mb-3 bg-red-50">
              <div className="flex items-start justify-between mb-2">
                <div>
                  <span className="font-mono font-bold text-red-700 text-sm">CARC {denial.carc_code}</span>
                  <span className="text-red-600 text-sm ml-2">{denial.carc_description}</span>
                </div>
                <span className="font-mono font-bold text-red-700">{fmt.currency(denial.denied_amount)}</span>
              </div>
              <div className="text-xs text-gray-600 space-y-1">
                <div><span className="font-medium">Category:</span> {denial.category?.replace(/_/g, ' ')}</div>
                <div><span className="font-medium">Recommended:</span> {denial.recommended_action?.replace(/_/g, ' ')}</div>
                {denial.appeal_deadline && (
                  <div className={`font-medium ${new Date(denial.appeal_deadline) < new Date(Date.now() + 30*86400000) ? 'text-red-600' : ''}`}>
                    Appeal Deadline: {fmt.date(denial.appeal_deadline)}
                  </div>
                )}
                {denial.write_off_recommended && (
                  <div className="text-purple-700 font-medium">⚠ Write-off recommended</div>
                )}
              </div>
              {denial.appealable && !denial.write_off_recommended && (
                <div className="mt-3">
                  {appealDenialId === denial.id ? (
                    <div className="space-y-2">
                      <textarea
                        className="input text-xs h-16 resize-none"
                        placeholder="Optional notes for the AI (e.g. 'claim was originally submitted 01/15/2025 via Waystar, tracking #12345')"
                        value={appealNotes}
                        onChange={e => setAppealNotes(e.target.value)}
                      />
                      <div className="flex gap-2">
                        <button
                          className="btn-primary text-xs"
                          onClick={() => handleGenerateAppeal(denial.id)}
                          disabled={generating}
                        >
                          {generating ? 'Generating…' : 'Generate AI Appeal Letter'}
                        </button>
                        <button className="btn-secondary text-xs" onClick={() => setAppealDenialId(null)}>Cancel</button>
                      </div>
                    </div>
                  ) : (
                    <button className="btn-primary text-xs mt-1" onClick={() => { setAppealDenialId(denial.id); setAppealResult(null) }}>
                      Generate Appeal Letter
                    </button>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Appeal Result */}
      {appealResult && (
        <div className="card border border-blue-200 bg-blue-50">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-semibold text-blue-800">Appeal Letter Generated</h2>
            <button
              className="btn-secondary text-xs"
              onClick={() => window.open(`/api/appeals/${appealResult.appeal_id}/download`)}
            >
              Download .txt
            </button>
          </div>
          <div className="text-xs font-medium text-blue-700 mb-2">{appealResult.subject}</div>
          <pre className="text-xs text-gray-700 whitespace-pre-wrap bg-white p-4 rounded border border-blue-100 max-h-96 overflow-y-auto font-sans leading-relaxed">
            {appealResult.body}
          </pre>
          <p className="text-xs text-blue-600 mt-2">
            Appeal saved · ID: {appealResult.appeal_id} · Generated by {appealResult.model_used}
          </p>
        </div>
      )}

      {editingClaim && (
        <EditClaimDrawer claim={claim} onClose={() => setEditingClaim(false)} />
      )}
      {editingLine && (
        <EditServiceLineDrawer
          claimId={claim.id}
          line={editingLine === 'add' ? null : editingLine}
          onClose={() => setEditingLine(null)}
        />
      )}
    </div>
  )
}
