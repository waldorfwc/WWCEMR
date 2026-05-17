import { useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { ArrowLeft, AlertTriangle, FileDown } from 'lucide-react'
import api, { fmt, statusColors } from '../utils/api'

export default function PatientDetail() {
  const { id } = useParams()

  const { data: patient } = useQuery({
    queryKey: ['patient', id],
    queryFn: () => api.get(`/patients/${id}`).then(r => r.data),
  })

  const { data: ledger, isLoading } = useQuery({
    queryKey: ['ledger', id],
    queryFn: () => api.get(`/patients/${id}/ledger`).then(r => r.data),
  })

  if (isLoading) return <div className="p-6 text-gray-400">Loading ledger…</div>
  if (!ledger) return <div className="p-6 text-gray-400">Patient not found</div>

  const { summary, dos_entries, payment_history, open_denials } = ledger

  return (
    <div className="p-6 max-w-6xl">
      <div className="flex items-center gap-3 mb-6">
        <a href="/patients" className="text-gray-400 hover:text-gray-600"><ArrowLeft size={20} /></a>
        <div>
          <h1 className="text-xl font-bold text-gray-900">{ledger.patient?.full_name}</h1>
          <p className="text-gray-500 text-sm">MRN: {ledger.patient?.patient_id} · DOB: {fmt.date(ledger.patient?.date_of_birth)}</p>
        </div>
        <div className="ml-auto flex gap-2">
          <button
            className="btn-primary text-xs flex items-center gap-1"
            onClick={() => window.open(`/api/patients/${id}/ledger/pdf`, '_blank')}
          >
            <FileDown size={14} /> Download full ledger PDF
          </button>
        </div>
      </div>

      {/* Insurance */}
      <div className="card mb-4">
        <h2 className="text-sm font-semibold text-gray-700 mb-2">Insurance Coverage</h2>
        <div className="grid grid-cols-3 gap-4 text-sm">
          {[
            ['Primary', ledger.patient?.primary_insurance],
            ['Secondary', ledger.patient?.secondary_insurance],
            ['Tertiary', ledger.patient?.tertiary_insurance],
          ].map(([label, ins]) => (
            <div key={label}>
              <div className="text-xs text-gray-400 uppercase">{label}</div>
              <div className="font-medium">{ins || '—'}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Financial Summary */}
      <div className="grid grid-cols-4 gap-4 mb-6">
        {[
          ['Total Billed', fmt.currency(summary.total_billed), 'text-gray-700'],
          ['Allowed', fmt.currency(summary.total_allowed), 'text-blue-700'],
          ['Insurance Paid', fmt.currency(summary.total_insurance_paid), 'text-green-700'],
          ['Contractual Adj', fmt.currency(summary.total_contractual_adjustment), 'text-gray-500'],
          ['Patient Responsibility', fmt.currency(summary.total_patient_responsibility), 'text-orange-600'],
          ['Patient Paid', fmt.currency(summary.total_patient_paid), 'text-green-700'],
          ['Outstanding Balance', fmt.currency(summary.outstanding_balance), summary.outstanding_balance > 0 ? 'text-red-600 font-bold text-lg' : 'text-gray-500'],
        ].map(([label, val, cls]) => (
          <div key={label} className="stat-card">
            <div className="text-xs text-gray-400 uppercase tracking-wide">{label}</div>
            <div className={`text-xl font-bold mt-1 ${cls}`}>{val}</div>
          </div>
        ))}
      </div>

      {/* Open Denials Banner */}
      {open_denials?.length > 0 && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-4 mb-6">
          <div className="flex items-center gap-2 mb-2">
            <AlertTriangle size={16} className="text-red-600" />
            <span className="font-semibold text-red-700 text-sm">{open_denials.length} Open Denial(s) — {fmt.currency(summary.open_denial_amount)} at risk</span>
          </div>
          <div className="space-y-1">
            {open_denials.map(d => (
              <div key={d.denial_id} className="text-xs text-red-600 flex items-center gap-2">
                <span className="font-mono">CARC {d.carc_code}</span>
                <span>{d.carc_description}</span>
                <span className="font-medium">{fmt.currency(d.denied_amount)}</span>
                <span className="text-gray-500">· DOS {fmt.date(d.dos)} · {d.payer}</span>
                {d.appeal_deadline && <span className="font-medium">· Deadline: {fmt.date(d.appeal_deadline)}</span>}
                <a href={`/claims/${d.claim_id}`} className="text-primary-500 hover:underline ml-auto">View Claim →</a>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* DOS Ledger */}
      <div className="card mb-6">
        <h2 className="text-sm font-semibold text-gray-700 mb-4">Financial Ledger by Date of Service</h2>
        {dos_entries?.length === 0 && (
          <p className="text-gray-400 text-sm">No claims on record for this patient.</p>
        )}
        {dos_entries?.map(entry => (
          <div key={entry.date_of_service} className="mb-6 last:mb-0">
            <div className="flex items-center gap-2 mb-2 pb-1 border-b border-gray-100">
              <span className="font-semibold text-gray-800 text-sm">DOS: {fmt.date(entry.date_of_service)}</span>
              <span className="text-xs text-gray-400">
                Billed {fmt.currency(entry.total_billed)} ·
                Ins. Paid {fmt.currency(entry.total_insurance_paid)} ·
                Pt. Balance <span className={entry.balance > 0 ? 'text-red-600 font-semibold' : 'text-green-700'}>{fmt.currency(entry.balance)}</span>
              </span>
            </div>

            {entry.claims.map(claim => (
              <div key={claim.claim_id} className="ml-4 mb-3 p-3 rounded-lg border border-gray-100 bg-gray-50">
                <div className="flex items-center gap-2 mb-2 text-sm">
                  <span className="font-mono text-xs text-primary-500">{claim.claim_number}</span>
                  <span className="text-gray-500 text-xs">{claim.payer_name}</span>
                  <span className={`${statusColors[claim.status] || 'badge-pending'} text-xs`}>{claim.status?.replace(/_/g, ' ')}</span>
                  <span className="text-xs text-gray-400 uppercase">{claim.insurance_order}</span>
                  <button
                    className="ml-auto text-xs text-primary-500 hover:underline"
                    onClick={() => window.open(`/api/patients/${id}/ledger/pdf?visit_id=${encodeURIComponent(claim.claim_number)}`, '_blank')}
                  >
                    Statement
                  </button>
                  <button
                    className="text-xs text-primary-500 hover:underline"
                    onClick={() => window.open(`/api/eob/${claim.claim_id}/pdf`, '_blank')}
                  >
                    EOB
                  </button>
                </div>
                <div className="grid grid-cols-6 gap-2 text-xs text-center">
                  {[
                    ['Billed', fmt.currency(claim.billed_amount), ''],
                    ['Allowed', fmt.currency(claim.allowed_amount), 'text-blue-700'],
                    ['Insurance Paid', fmt.currency(claim.paid_amount), 'text-green-700'],
                    ['Contractual', fmt.currency(claim.contractual_adjustment), 'text-gray-400'],
                    ['Pt. Resp.', fmt.currency(claim.patient_responsibility), 'text-orange-600'],
                    ['Pt. Paid', fmt.currency(claim.patient_paid), 'text-green-700'],
                  ].map(([label, val, cls]) => (
                    <div key={label} className="bg-white rounded p-2 border border-gray-100">
                      <div className="text-gray-400">{label}</div>
                      <div className={`font-mono font-semibold ${cls}`}>{val}</div>
                    </div>
                  ))}
                </div>
                {claim.denials?.length > 0 && (
                  <div className="mt-2 text-xs text-red-600 flex items-center gap-1">
                    <AlertTriangle size={11} />
                    {claim.denials.map(d => `CARC ${d.carc_code}: ${d.carc_description?.substring(0, 40)}`).join(' | ')}
                  </div>
                )}
              </div>
            ))}
          </div>
        ))}
      </div>

      {/* Payment History */}
      {payment_history?.length > 0 && (
        <div className="card">
          <h2 className="text-sm font-semibold text-gray-700 mb-3">Payment History</h2>
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b text-xs text-gray-500 uppercase">
                <th className="pb-2 text-left">Date</th>
                <th className="pb-2 text-left">Type</th>
                <th className="pb-2 text-left">Payer / Method</th>
                <th className="pb-2 text-left">Check #</th>
                <th className="pb-2 text-right">Amount</th>
              </tr>
            </thead>
            <tbody>
              {payment_history.map((p, i) => (
                <tr key={i} className="border-b border-gray-50">
                  <td className="py-2">{fmt.date(p.date)}</td>
                  <td className="py-2 text-gray-500">{p.type?.replace(/_/g, ' ')}</td>
                  <td className="py-2">{p.payer || p.method || '—'}</td>
                  <td className="py-2 font-mono text-xs">{p.check_number || '—'}</td>
                  <td className="py-2 text-right font-mono font-medium text-green-700">{fmt.currency(p.amount)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
