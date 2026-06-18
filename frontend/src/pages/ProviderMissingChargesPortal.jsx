import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'
import {
  Check, AlertCircle, ExternalLink, FileText, Calendar, Loader2, X,
} from 'lucide-react'
import axios from 'axios'
import logoMark from '../assets/wwc-logo.png'


// Public page — uses axios directly (no auth header injection).
const api = axios.create({ baseURL: '/api', timeout: 30_000 })


function fmtDate(d) {
  if (!d) return '—'
  // MM/DD/YYYY per app convention (was "Jun 12, 2026").
  return new Date(d + 'T00:00:00').toLocaleDateString('en-US', {
    month: '2-digit', day: '2-digit', year: 'numeric',
  })
}


export default function ProviderMissingChargesPortal() {
  const { token } = useParams()
  const [errorFor, setErrorFor] = useState(null)
  const qc = useQueryClient()

  const { data, isLoading, error } = useQuery({
    queryKey: ['provider-portal', token],
    queryFn: () => api.get(`/billing/missing-charges/provider/${encodeURIComponent(token)}`)
                       .then(r => r.data),
    retry: false,
  })

  const actionMut = useMutation({
    mutationFn: ({ chargeId, action, note }) =>
      api.post(`/billing/missing-charges/provider/${encodeURIComponent(token)}/${chargeId}`,
                { action, note }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['provider-portal', token] })
      setErrorFor(null)
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Could not save'),
  })

  if (isLoading) {
    return (
      <Frame>
        <div className="text-gray-500 flex items-center gap-2">
          <Loader2 size={14} className="animate-spin" /> Loading your charges…
        </div>
      </Frame>
    )
  }

  if (error) {
    const msg = error?.response?.data?.detail || error.message
    return (
      <Frame>
        <div className="bg-red-50 border border-red-200 rounded p-4 text-red-800">
          <div className="font-semibold mb-1 flex items-center gap-1">
            <AlertCircle size={14} /> Link not valid
          </div>
          <div className="text-sm">{msg}</div>
          <div className="text-xs text-red-600 mt-2">
            Ask the biller to send a fresh portal link.
          </div>
        </div>
      </Frame>
    )
  }

  const charges = data?.charges || []
  const expiresAt = data?.expires_at
    ? new Date(data.expires_at * 1000).toLocaleDateString('en-US', { month: '2-digit', day: '2-digit', year: 'numeric' })
    : null

  return (
    <Frame>
      <h1 className="text-xl font-semibold text-gray-900 mb-1">
        Welcome, Dr. {data?.provider?.split(',')[0]}
      </h1>
      <p className="text-sm text-gray-600 mb-4">
        Below are appointments that need their <strong>note completed</strong> so
        they can bill. Open each one in ModMed, finish the note, then come back
        and tap <strong>Mark as billed</strong>. If you can't bill one, tap
        <strong>Error</strong> and tell us why — the biller will follow up.
      </p>

      {charges.length === 0 ? (
        <div className="bg-green-50 border border-green-200 rounded p-6 text-center">
          <Check size={28} className="text-green-700 mx-auto mb-2" />
          <div className="text-green-800 font-medium">All caught up — no pending charges.</div>
        </div>
      ) : (
        <div className="space-y-3">
          {charges.map(c => (
            <div key={c.id} className="border border-border-subtle rounded-lg p-3 bg-white">
              <div className="flex items-baseline justify-between gap-2 flex-wrap">
                <div>
                  <div className="font-semibold text-gray-900">{c.patient_name}</div>
                  <div className="text-xs text-gray-500">
                    MRN {c.patient_mrn} · <Calendar size={10} className="inline" />{' '}
                    {fmtDate(c.appointment_date)} · {c.appointment_type || '—'}
                  </div>
                  {c.payer && (
                    <div className="text-xs text-gray-500">Payer: {c.payer}</div>
                  )}
                </div>
                {c.patient_link && (
                  <a href={c.patient_link} target="_blank" rel="noopener noreferrer"
                     className="text-plum-700 hover:underline inline-flex items-center gap-1 text-xs">
                    Open in ModMed <ExternalLink size={10} />
                  </a>
                )}
              </div>

              {errorFor === c.id ? (
                <ErrorForm charge={c}
                            onCancel={() => setErrorFor(null)}
                            onSubmit={note => actionMut.mutate({
                              chargeId: c.id, action: 'error', note,
                            })}
                            pending={actionMut.isPending} />
              ) : (
                <div className="flex gap-2 mt-3">
                  <button
                    className="bg-green-600 hover:bg-green-700 text-white text-sm font-medium px-3 py-1.5 rounded flex items-center gap-1"
                    disabled={actionMut.isPending}
                    onClick={() => {
                      if (window.confirm(`Mark "${c.patient_name}" on ${fmtDate(c.appointment_date)} as billed?`))
                        actionMut.mutate({ chargeId: c.id, action: 'billed' })
                    }}>
                    <Check size={13} /> Mark as Billed
                  </button>
                  <button
                    className="bg-red-100 hover:bg-red-200 text-red-800 text-sm font-medium px-3 py-1.5 rounded flex items-center gap-1"
                    disabled={actionMut.isPending}
                    onClick={() => setErrorFor(c.id)}>
                    <AlertCircle size={13} /> Error
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {expiresAt && (
        <div className="text-[11px] text-gray-400 mt-6 text-center">
          This link expires {expiresAt}. Once expired, the biller will email you a new one.
        </div>
      )}
    </Frame>
  )
}


function ErrorForm({ charge, onCancel, onSubmit, pending }) {
  const [note, setNote] = useState('')
  return (
    <div className="mt-3 bg-red-50 border border-red-200 rounded p-3">
      <label className="text-xs font-medium text-red-800 block mb-1">
        Why can't this be billed? (will be sent to the biller)
      </label>
      <textarea className="w-full border border-red-200 rounded p-2 text-sm"
                rows={3}
                placeholder="e.g. patient didn't actually show; appointment was a phone call only; …"
                value={note}
                onChange={e => setNote(e.target.value)} />
      <div className="flex justify-end gap-2 mt-2">
        <button className="text-xs text-gray-600 hover:underline"
                onClick={onCancel}>Cancel</button>
        <button className="bg-red-700 hover:bg-red-800 text-white text-xs font-medium px-3 py-1.5 rounded"
                disabled={!note.trim() || pending}
                onClick={() => onSubmit(note.trim())}>
          {pending ? 'Saving…' : 'Send to biller'}
        </button>
      </div>
    </div>
  )
}


function Frame({ children }) {
  return (
    <div className="min-h-screen bg-plum-50">
      <header className="bg-white border-b border-border-subtle h-[60px] px-6 flex items-center">
        <img src={logoMark} alt="WWC" className="h-8" />
        <div className="ml-3 text-sm text-gray-600">
          Missing-charges portal
        </div>
      </header>
      <main className="max-w-3xl mx-auto p-6">
        {children}
      </main>
    </div>
  )
}
