import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { Check, RotateCcw, CreditCard } from 'lucide-react'
import api, { fmt } from '../utils/api'
import { useCurrentUser } from '../hooks/useCurrentUser'
import { MODULE, TIER } from '../routes.jsx'
import EmptyState from '../components/EmptyState'
import LoadingState from '../components/LoadingState'

const FILTERS = [
  { key: 'unposted', label: 'Not Posted' },
  { key: 'posted',   label: 'Posted' },
  { key: 'all',      label: 'All' },
]

const KIND_TONE = {
  patient_balance:  'bg-plum-100 text-plum-700',
  fmla_fee:         'bg-blue-100 text-blue-700',
  cancellation_fee: 'bg-amber-100 text-amber-800',
  no_show_fee:      'bg-amber-100 text-amber-800',
}


function PostCell({ row, canManage, onPost, onUnpost, pending }) {
  const [initials, setInitials] = useState('')

  if (row.posted) {
    return (
      <div className="flex items-center justify-between gap-2">
        <span className="inline-flex items-center gap-1 text-green-700 text-[13px]">
          <Check size={14} />
          <span className="font-medium">{row.posted_initials}</span>
          <span className="text-muted">· {fmt.dateTime(row.posted_at)}</span>
        </span>
        {canManage && (
          <button
            onClick={() => onUnpost(row)}
            disabled={pending}
            className="inline-flex items-center gap-1 text-[12px] text-muted hover:text-red-600 disabled:opacity-50"
            title="Reverse this posting mark"
          >
            <RotateCcw size={12} /> Un-mark
          </button>
        )}
      </div>
    )
  }

  return (
    <form
      className="flex items-center gap-1.5"
      onSubmit={(e) => { e.preventDefault(); if (initials.trim()) onPost(row, initials.trim()) }}
    >
      <input
        value={initials}
        onChange={(e) => setInitials(e.target.value)}
        maxLength={10}
        placeholder="Initials"
        aria-label="Your initials"
        className="w-20 rounded border border-border-subtle px-2 py-1 text-[13px] uppercase
                   focus:border-plum-500 focus:outline-none"
      />
      <button
        type="submit"
        disabled={!initials.trim() || pending}
        className="rounded bg-plum-700 px-2.5 py-1 text-[12px] font-medium text-white
                   hover:bg-plum-800 disabled:opacity-40"
      >
        Mark Posted
      </button>
    </form>
  )
}


export default function SurgeryPaymentPosting() {
  const qc = useQueryClient()
  const { tier } = useCurrentUser()
  const canManage = tier(MODULE.SURGERY, TIER.MANAGE)
  const [filter, setFilter] = useState('unposted')

  const { data, isLoading } = useQuery({
    queryKey: ['surgery-payment-postings', filter],
    queryFn: () => api.get(`/surgery/payment-postings?posted=${filter}`).then(r => r.data),
  })

  const invalidate = () =>
    qc.invalidateQueries({ queryKey: ['surgery-payment-postings'] })

  const postMut = useMutation({
    mutationFn: ({ id, initials }) =>
      api.post(`/surgery/payment-postings/${id}/post`, { initials }).then(r => r.data),
    onSuccess: invalidate,
  })

  const unpostMut = useMutation({
    mutationFn: ({ id }) =>
      api.post(`/surgery/payment-postings/${id}/unpost`).then(r => r.data),
    onSuccess: invalidate,
  })

  const items = data?.items || []
  const pending = postMut.isPending || unpostMut.isPending

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-ink">Payment Posting</h1>
          <p className="text-[13px] text-muted">
            Stripe payments from patients — confirm each has been posted to ModMed.
          </p>
        </div>
        <div className="flex gap-1">
          {FILTERS.map(f => (
            <button
              key={f.key}
              onClick={() => setFilter(f.key)}
              className={`rounded-full px-3 py-1 text-[12px] font-medium transition-colors ${
                filter === f.key
                  ? 'bg-plum-700 text-white'
                  : 'bg-gray-100 text-muted hover:text-plum-700'
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>
      </div>

      {isLoading ? (
        <LoadingState />
      ) : items.length === 0 ? (
        <EmptyState
          icon={CreditCard}
          title="No Payments"
          body={
            filter === 'unposted'
              ? 'Every Stripe payment has been posted to ModMed.'
              : 'No Stripe payments found.'
          }
        />
      ) : (
        <div className="overflow-x-auto rounded-lg border border-border-subtle">
          <table className="min-w-full text-[13px]">
            <thead className="bg-gray-50 text-left text-[12px] uppercase tracking-wide text-muted">
              <tr>
                <th className="px-3 py-2 font-medium">MRN</th>
                <th className="px-3 py-2 font-medium">Patient</th>
                <th className="px-3 py-2 font-medium">Surgery</th>
                <th className="px-3 py-2 font-medium">Type</th>
                <th className="px-3 py-2 font-medium">Date Paid</th>
                <th className="px-3 py-2 font-medium text-right">Amount</th>
                <th className="px-3 py-2 font-medium">Confirmation</th>
                <th className="px-3 py-2 font-medium w-64">Transferred to ModMed</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border-subtle">
              {items.map(row => (
                <tr key={row.id} className={row.posted ? '' : 'bg-amber-50/40'}>
                  <td className="px-3 py-2 font-mono text-[12px]">{row.chart_number || '—'}</td>
                  <td className="px-3 py-2">{row.patient_name || '—'}</td>
                  <td className="px-3 py-2">
                    {row.surgery_id ? (
                      <Link to={`/surgery/${row.surgery_id}`} className="text-plum-700 hover:underline">
                        {row.surgery_number || 'View'}
                      </Link>
                    ) : '—'}
                    {row.procedure_summary && (
                      <div className="text-[11px] text-muted truncate max-w-[14rem]">
                        {row.procedure_summary}
                      </div>
                    )}
                  </td>
                  <td className="px-3 py-2">
                    <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${
                      KIND_TONE[row.kind] || 'bg-gray-100 text-gray-700'}`}>
                      {row.kind_label}
                    </span>
                  </td>
                  <td className="px-3 py-2 whitespace-nowrap">{fmt.date(row.paid_at)}</td>
                  <td className="px-3 py-2 text-right font-medium whitespace-nowrap">
                    {fmt.currency(row.amount_paid)}
                  </td>
                  <td className="px-3 py-2 font-mono text-[11px] text-muted">{row.confirmation || '—'}</td>
                  <td className="px-3 py-2">
                    <PostCell
                      row={row}
                      canManage={canManage}
                      pending={pending}
                      onPost={(r, initials) => postMut.mutate({ id: r.id, initials })}
                      onUnpost={(r) => {
                        if (window.confirm('Reverse the ModMed posting mark on this payment?'))
                          unpostMut.mutate({ id: r.id })
                      }}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
